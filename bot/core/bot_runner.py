# ============================================================
# bot/core/bot_runner.py
# Bot Runner — master loop for the live trading system
#
# UPGRADED FROM PREVIOUS VERSION:
# 1. RegimeDetector + StrategyRouter replace direct strategy call
# 2. TradeNarrativeGenerator documents every live trade
# 3. Heartbeat log every 5 minutes shows system health
# 4. API latency measured and reported in heartbeat
# 5. Last detected regime shown in heartbeat
# 6. Component health checks on startup before trading begins
# 7. All bot. import prefixes correct for your project structure
# 8. claude code changed: new — optional, independent Kraken venue
#    (KRAKEN_ENABLED=true; see bot/engines/kraken_adapter.py). Disabled by
#    default — Binance-only behavior is completely unchanged when it's
#    off. When on, Kraken gets its OWN candle fetch, RegimeDetector,
#    StrategyRouter, and ExecutionEngine — it never sees or uses
#    Binance's regime/signal. See VenueSession/process_venue_candle below.
#
# HOW TO RUN:
#   python bot/core/bot_runner.py
#
# BEFORE RUNNING LIVE:]
#   1. python bot/core/dry_run_test.py   ← run this first
#   2. Set DRY_RUN = True below
#   3. Watch logs for 30 minutes
#   4. Only set DRY_RUN = False when confident
#
# Kraken: set KRAKEN_ENABLED=true to also trade Kraken (same symbol,
# fully independent pipeline). KRAKEN_DRY_RUN defaults true regardless of
# DRY_RUN above — each venue's dry-run/live state is controlled
# independently (see build_kraken_adapter() in bot/engines/kraken_adapter.py).
# ============================================================

import sys                   # claude code changed: new — needed for the sys.path fix below
import time                # Sleep between candles + latency measurement
import logging               # Structured logging
import logging.handlers      # Rotating file handler
import os                    # Environment variable access for API keys
from dataclasses import dataclass   # claude code changed: new — VenueSession
import pandas as pd          # OHLCV DataFrame building
import ccxt                  # Exchange connection

# -----------------------------------------------------------------
# claude code changed: new — force UTF-8 on stdout/stderr. This file
# uses ✓/✗/⚠/— throughout its log messages (71 occurrences), and
# Windows' default console codepage (cp1252) can't encode most of them.
# Without this, those characters were silently mangled to "�" in
# terminal/log output instead of printing correctly (verified: the
# startup banner's "DRY RUN — no real orders" line was coming out as
# "DRY RUN � no real orders") — not a crash, but log output the operator
# can't trust to read correctly is its own risk for a live trading bot.
# Placed before setup_logging() runs so the StreamHandler it creates
# (which defaults to sys.stderr at creation time) inherits this fix.
# -----------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# -----------------------------------------------------------------
# claude code changed: new — make sure the project root is on sys.path
# when running directly via `python bot/core/bot_runner.py` (exactly
# what this file's own header above instructs). Without this, Python
# puts this script's OWN directory (bot/core) on sys.path instead of
# the project root, so every "from bot.engines..." import below fails
# with "ModuleNotFoundError: No module named 'bot'" before the bot
# ever starts. dry_run_test.py already uses this same pattern —
# mirrored here so both files behave consistently.
# -----------------------------------------------------------------
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# -----------------------------------------------------------------
# claude code changed: new — configure Django before importing anything
# that touches a Django model. ExecutionEngine (imported below) pulls in
# TradeLogger -> bot.journal.models.TradeRecord, and TradeRecord's class
# body cannot execute until Django's app registry is ready. Without this,
# import fails with "ImproperlyConfigured: ... settings are not
# configured" (or "Apps aren't loaded yet") the moment ExecutionEngine
# is imported — this bot was never actually runnable via its own
# documented `python bot/core/bot_runner.py` command until now.
# -----------------------------------------------------------------
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

# claude code changed: new — single-sourced logging constants, see
# bot/config/logging_config.py header for why this stays separate from
# Django's own LOGGING setting
from bot.config.logging_config import LOG_FILE_PATH, LOG_MAX_BYTES, LOG_BACKUP_COUNT

# ── SYSTEM COMPONENTS ─────────────────────────────────────────

# Regime detection — classifies market every candle
from bot.engines.regime_detector import RegimeDetector

# Strategy routing — selects correct strategy per regime
from bot.engines.strategy_router import StrategyRouter

# Trade narrative — documents every trade in plain English
from bot.engines.trade_narrative import TradeNarrativeGenerator

# Execution engine — handles entry, exit, position management
from bot.engines.execution_engine import ExecutionEngine

# claude code changed: new import — fixes architecture audit issue 6.
# Cross-checks Binance's price against a second exchange before trading on it.
from bot.engines.price_validator import PriceCrossChecker

# claude code changed: new import — optional, independent Kraken venue.
# build_kraken_adapter() already gates on KRAKEN_ENABLED/credentials and
# refuses to construct a live adapter without real API keys (Step 4) —
# reused as-is, no new credential/dry-run logic invented here.
from bot.engines.kraken_adapter import build_kraken_adapter

# Position tracker — live memory of open positions
# Accessed via engine.position_tracker


# ============================================================
# LOGGING SETUP
# Configured once here — all modules inherit this automatically
# Rotating file: max 5MB per file, keeps last 3 files
# ============================================================
def setup_logging():
    """Configures logging for the entire bot session."""

    # Timestamp + severity + module + message on every line
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Rotating file handler — prevents log files growing indefinitely
    # claude code changed: was hardcoded "trades.log" / 5MB / 3 backups
    # inline here — now a single named source in bot/config/logging_config.py
    file_handler = logging.handlers.RotatingFileHandler(
        filename    = LOG_FILE_PATH,         # Log file location
        maxBytes    = LOG_MAX_BYTES,         # Rotate after this many bytes
        backupCount = LOG_BACKUP_COUNT,      # Keep this many rotated files
    )
    file_handler.setFormatter(formatter)    # Apply timestamp format
    file_handler.setLevel(logging.DEBUG)    # Write everything to file

    # Console handler — shows logs in terminal while running
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)  # Show INFO+ in terminal

    # Apply to root logger — affects all modules
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)            # Capture all levels
    root.addHandler(file_handler)           # Write to file
    root.addHandler(console_handler)        # Write to terminal


# ============================================================
# BOT CONFIGURATION
# All settings in one place — no need to dig into other files
# ============================================================

# ── SAFETY ───────────────────────────────────────────────────
# DRY_RUN = True  → logs signals but places NO real orders
# DRY_RUN = False → live trading with real money
# Always start with True — only flip when confident
# Applies to the Binance session only — Kraken's dry-run state is
# controlled independently via KRAKEN_DRY_RUN (see build_kraken_adapter()).
DRY_RUN = True

# ── TRADING PAIR ─────────────────────────────────────────────
SYMBOL = "XRP/USDT"    # Must match ccxt format exactly
# claude code changed: Kraken (when enabled) trades this SAME symbol,
# fully independently — confirmed directly listed on Kraken under this
# exact quote currency (Step 5's real-data audit), so no symbol
# normalization/fallback is needed for this specific pair.

# ── TIMEFRAME ────────────────────────────────────────────────
TIMEFRAME = "1h"         # "1h", "4h", "1d" — must match candle interval

# ── CANDLE HISTORY ───────────────────────────────────────────
CANDLE_LIMIT = 200       # Candles fetched per loop — 200 gives EMA50 warmup

# ── LOOP INTERVAL ────────────────────────────────────────────
# How long to sleep between each candle check
# 3600 = 1 hour (matches 1h candles)
# Use 60 for testing — checks every minute
CANDLE_INTERVAL_SECONDS = 3600

# ── HEARTBEAT INTERVAL ───────────────────────────────────────
# How often to print the system health line (seconds)
# 300 = every 5 minutes — enough to confirm bot is alive
HEARTBEAT_INTERVAL_SECONDS =  300

# ── SYMBOLS TO RECONCILE ─────────────────────────────────────
# All pairs the bot might have open positions on
# Add more here as you expand to multiple pairs
# claude code changed: was [] — meant reconcile_with_exchange()'s loop body
# never ran for ANY symbol, including the one this bot actually trades
# (SYMBOL below), so a crash/restart never actually reconciled against the
# exchange despite that being reconcile's whole purpose. Found while fixing
# the TradeLogger.MultipleObjectsReturned fragility — this was one of the
# contributing gaps (see bot/journal/models.py's new unique constraint and
# execution_engine.py's new DB-backed duplicate guard for the rest).
TRACKED_SYMBOLS = [SYMBOL]

# ── SECONDARY PRICE SOURCE ───────────────────────────────────
# claude code changed: new — fixes architecture audit issue 6. Independent
# exchange used to sanity-check Binance's price every candle before trading
# on it. Does not replace Binance as the primary data source.
#
# claude code changed: NOTE — this is a price SANITY CHECK on Binance,
# unrelated to Kraken potentially being traded as its own independent
# venue below (VenueSession). Kraken here is only ever a reference price;
# when KRAKEN_ENABLED also makes it a traded venue, that trading path
# never reads from or writes to this checker.
SECONDARY_EXCHANGE = "kraken"

# ── API CREDENTIALS ──────────────────────────────────────────
# Read from environment variables — NEVER hardcode in source code
# Set them in terminal before running:
#   export BINANCE_API_KEY="your_key_here"
#   export BINANCE_API_SECRET="your_secret_here"
API_KEY    = os.getenv("BINANCE_API_KEY",    "YOUR_API_KEY_HERE")
API_SECRET = os.getenv("BINANCE_API_SECRET", "YOUR_API_SECRET_HERE")


# ============================================================
# VENUE SESSION
# claude code changed: new — bundles everything one venue's independent
# candle-fetch -> regime -> signal -> execute pipeline needs, so run_bot()
# can iterate a list of these instead of hardcoding a single Binance-only
# pipeline. Each venue gets its OWN RegimeDetector/StrategyRouter instance
# — same config/strategy logic every venue (never duplicated/forked
# per venue), just independent STATE — so one venue's price action can
# never influence another venue's signal.
# ============================================================
@dataclass
class VenueSession:
    venue_id: str
    exchange: object            # ccxt exchange instance for this venue
    symbol: str
    engine: object               # ExecutionEngine
    detector: object              # RegimeDetector
    router: object                # StrategyRouter
    narrator: object               # TradeNarrativeGenerator
    last_regime: str = "UNKNOWN"
    last_signal: str = "NONE"


# ============================================================
# OHLCV TO DATAFRAME CONVERTER
# Converts raw ccxt candle list into pandas DataFrame
# ============================================================
def build_dataframe(candles: list) -> pd.DataFrame:
    """
    Converts ccxt OHLCV list into a labelled pandas DataFrame.

    ccxt returns: [[timestamp, open, high, low, close, volume], ...]
    Strategies expect: DataFrame with named columns + DatetimeIndex

    Returns DataFrame or None on failure.
    """

    if not candles or len(candles) == 0:
        return None    # Cannot build from empty data

    try:
        # Convert list of lists to named columns
        df = pd.DataFrame(
            candles,
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )

        # Convert millisecond timestamp to readable datetime
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

        # Set DatetimeIndex — required by RegimeDetector
        df.set_index("timestamp", inplace=True)

        # Force all price columns to float — removes any string artifacts
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Drop rows with NaN price data
        df.dropna(subset=["open", "high", "low", "close"], inplace=True)

        # Reset index after dropping rows — keeps index sequential
        df.reset_index(inplace=True)

        # Set DatetimeIndex again after reset
        df.set_index("timestamp", inplace=True)

        return df    # Clean DataFrame ready for strategy and detector

    except Exception as e:
        logging.getLogger(__name__).error(
            f"[BotRunner] DataFrame build failed: {e}"
        )
        return None    # Caller handles None gracefully


# ============================================================
# API LATENCY MEASUREMENT
# Measures how long a simple ticker fetch takes
# Used in the heartbeat to monitor connection health
# ============================================================
def measure_latency(exchange) -> int:
    """
    Measures API round-trip latency in milliseconds.
    Returns int milliseconds. Returns -1 if measurement fails.

    Uses the module-level SYMBOL — safe for any venue's exchange instance
    since every venue trades this same symbol (see VenueSession above).
    """

    try:
        start = time.time()                        # Record start time
        exchange.fetch_ticker(SYMBOL)              # Simple price fetch
        latency_ms = int((time.time() - start) * 1000)   # Calculate ms
        return latency_ms    # Return latency in milliseconds

    except Exception:
        return -1    # Return -1 to signal measurement failed


# ============================================================
# HEARTBEAT LOG
# Prints one line every 5 minutes showing system health
# Format: [HEARTBEAT] Engines: OK | Risk: OK | API: 45ms | Regime: RANGING
# ============================================================
def print_heartbeat(
    engine,           # ExecutionEngine instance
    detector,         # RegimeDetector instance
    router,           # StrategyRouter instance
    exchange,         # ccxt exchange instance
    last_regime: str, # Last detected regime string
    last_signal: str, # Last signal produced
    loop_count: int,  # How many candle loops have completed
):
    """
    Prints a single heartbeat line confirming all systems are alive.
    Called every HEARTBEAT_INTERVAL_SECONDS from the main loop, once per
    venue session.
    """

    logger = logging.getLogger(__name__)

    # ── CHECK EACH COMPONENT ─────────────────────────────────

    # Check execution engine is alive
    try:
        positions = engine.position_tracker.get_all_positions()
        engines_status = "OK"
        open_count = len(positions)    # Number of open positions
    except Exception:
        engines_status = "ERROR"
        open_count = -1

    # Check position sizer is alive
    try:
        _ = engine.position_sizer.risk_pct    # Access a property
        risk_status = "OK"
    except Exception:
        risk_status = "ERROR"

    # Check regime detector is alive
    try:
        _ = detector.adx_trend_threshold    # Access a property
        regime_status = "OK"
    except Exception:
        regime_status = "ERROR"

    # Measure live API latency
    latency_ms = measure_latency(exchange)
    if latency_ms == -1:
        latency_str = "TIMEOUT"
    elif latency_ms > 500:
        latency_str = f"{latency_ms}ms ⚠"    # High latency warning
    else:
        latency_str = f"{latency_ms}ms"       # Normal latency

    # claude code changed: new — surface DrawdownGuard's state in the
    # heartbeat. A kill switch nobody can see the status of isn't a
    # usable safety feature — this is the only place an operator
    # watching the console/log would otherwise notice it tripped.
    # Reports peak balance and tripped/OK only — deliberately does not
    # fetch a fresh balance here just to compute a live drawdown number,
    # to avoid spending an extra API call every heartbeat on top of the
    # one execute_signal() already makes before every entry decision.
    try:
        guard = engine.drawdown_guard
        drawdown_status = (
            f"TRIPPED ⚠ (peak: ${guard.peak_balance:.2f})"
            if guard.tripped
            else f"OK (peak: ${guard.peak_balance:.2f})"
        )
    except Exception:
        drawdown_status = "ERROR"

    # ── BUILD HEARTBEAT LINE ──────────────────────────────────
    heartbeat = (
        f"[HEARTBEAT] "
        f"Venue: {getattr(engine, 'venue_id', 'unknown')} | "   # claude code changed: new — Step 15
        f"Engines: {engines_status} | "
        f"Risk: {risk_status} | "
        f"Regime detector: {regime_status} | "
        f"Drawdown guard: {drawdown_status} | "
        f"API latency: {latency_str} | "
        f"Last regime: {last_regime} | "
        f"Last signal: {last_signal} | "
        f"Open positions: {open_count} | "
        f"Loops completed: {loop_count}"
    )

    # Log at INFO level — appears in both terminal and log file
    logger.info(heartbeat)

    # If any component shows ERROR — log additional warning
    if "ERROR" in (engines_status, risk_status, regime_status):
        logger.warning(
            "[HEARTBEAT] ⚠ One or more components reporting ERROR. "
            "Check logs above for details."
        )

    # Warn if latency is very high — may cause missed candle closes
    if latency_ms > 1000:
        logger.warning(
            f"[HEARTBEAT] ⚠ API latency is HIGH ({latency_ms}ms). "
            f"May cause delayed order execution."
        )


# ============================================================
# STARTUP COMPONENT CHECK
# Verifies all components initialise before the loop starts
# ============================================================
def check_components(engine, detector, router, narrator, price_checker=None) -> bool:
    """
    Verifies all key components are available and functional.
    Called once on startup per venue session — returns True if all OK,
    False if not.

    price_checker: claude code changed: now optional (was required) —
    only the Binance session has one (it's a price sanity check using
    Kraken as a reference; a venue never checks itself against itself).
    None skips that one check for any other venue's session.
    """

    logger = logging.getLogger(__name__)

    logger.info("[BotRunner] Running startup component check...")

    all_ok = True    # Assume OK until a check fails

    # Check execution engine
    try:
        _ = engine.position_tracker.open_positions
        logger.info("[BotRunner] ✓ ExecutionEngine — OK")
    except Exception as e:
        logger.error(f"[BotRunner] ✗ ExecutionEngine — FAILED: {e}")
        all_ok = False

    # Check regime detector
    try:
        _ = detector.adx_trend_threshold
        logger.info("[BotRunner] ✓ RegimeDetector — OK")
    except Exception as e:
        logger.error(f"[BotRunner] ✗ RegimeDetector — FAILED: {e}")
        all_ok = False

    # Check strategy router
    try:
        _ = router.trend_strategy
        _ = router.reversion_strategy
        logger.info("[BotRunner] ✓ StrategyRouter — OK (both strategies loaded)")
    except Exception as e:
        logger.error(f"[BotRunner] ✗ StrategyRouter — FAILED: {e}")
        all_ok = False

    # Check narrative generator
    try:
        _ = narrator.rsi_zones
        logger.info("[BotRunner] ✓ TradeNarrativeGenerator — OK")
    except Exception as e:
        logger.error(f"[BotRunner] ✗ TradeNarrativeGenerator — FAILED: {e}")
        all_ok = False

    # Check position sizer
    try:
        _ = engine.position_sizer.risk_pct
        logger.info(
            f"[BotRunner] ✓ PositionSizer — OK "
            f"(risk: {engine.position_sizer.risk_pct * 100:.1f}%)"
        )
    except Exception as e:
        logger.error(f"[BotRunner] ✗ PositionSizer — FAILED: {e}")
        all_ok = False

    # claude code changed: new — check the cross-exchange price validator,
    # consistent with every other component check here (fixes architecture
    # audit issue 6). Only checks the object is constructed, same as the
    # other checks — a live secondary-exchange fetch failure at runtime is
    # already handled per-candle inside PriceCrossChecker.check() itself.
    # claude code changed: now conditional — see docstring above.
    if price_checker is not None:
        try:
            _ = price_checker.secondary_exchange_id
            logger.info(
                f"[BotRunner] ✓ PriceCrossChecker — OK "
                f"(secondary: {price_checker.secondary_exchange_id})"
            )
        except Exception as e:
            logger.error(f"[BotRunner] ✗ PriceCrossChecker — FAILED: {e}")
            all_ok = False

    if all_ok:
        logger.info("[BotRunner] All component checks passed")
    else:
        logger.error(
            "[BotRunner] Component checks FAILED — fix errors before trading"
        )

    return all_ok    # True = safe to proceed, False = halt


# ============================================================
# PROCESS ONE VENUE'S CANDLE
# claude code changed: new — extracted from run_bot()'s main loop body so
# it's a standalone, testable function and so each venue's errors are
# caught HERE rather than aborting a whole multi-venue loop iteration —
# one venue's network hiccup must never stop another venue's candle from
# being checked this pass.
# ============================================================
def process_venue_candle(session: VenueSession, price_checker=None) -> str:
    """
    Runs one full candle-processing pass for a SINGLE venue:
    fetch -> build_dataframe -> [Binance-only price sanity check] ->
    detect regime -> route -> execute_signal -> manage_positions ->
    dry-run logging.

    price_checker: only meaningful for the Binance session (sanity-checks
    Binance's own price against a secondary reference) — None for any
    other venue. This is unrelated to that other venue being traded in
    its own right; nothing here ever compares one traded venue's price
    against another's.

    Returns "ok", "network_error", "rate_limited", or "error" — run_bot()
    uses the worst status across all sessions processed this pass to
    decide how long to sleep before the next iteration, reproducing this
    file's original single-venue retry timing exactly (NetworkError -> 60s,
    RateLimitExceeded -> 120s, any other failure -> 60s, otherwise the
    normal candle interval) rather than a naive per-venue rewrite letting
    one venue's transient error fall through to the full hour-long wait.
    """
    logger = logging.getLogger(__name__)

    try:
        logger.info(
            f"[BotRunner:{session.venue_id}] ── {session.symbol} ──────────────"
        )

        # ── FETCH OHLCV CANDLES ───────────────────────────
        candles = session.exchange.fetch_ohlcv(
            symbol    = session.symbol,
            timeframe = TIMEFRAME,
            limit     = CANDLE_LIMIT,
        )

        # Guard: empty candle response
        if not candles:
            logger.warning(
                f"[BotRunner:{session.venue_id}] Empty candle response — skipping this venue this loop"
            )
            return "error"

        # ── BUILD DATAFRAME ───────────────────────────────
        df = build_dataframe(candles)

        if df is None:
            logger.warning(
                f"[BotRunner:{session.venue_id}] DataFrame build failed — skipping this venue this loop"
            )
            return "error"

        logger.info(f"[BotRunner:{session.venue_id}] DataFrame ready — {len(df)} candles")

        # ── CROSS-EXCHANGE PRICE SANITY CHECK (Binance only) ──
        # claude code changed: unchanged logic, now conditional on a
        # price_checker being given — see docstring above.
        if price_checker is not None:
            latest_close = float(df["close"].iloc[-1])
            price_check = price_checker.check(session.symbol, latest_close)

            if price_check["suspicious"]:
                logger.warning(
                    f"[BotRunner:{session.venue_id}] Skipping — suspicious price for {session.symbol} | "
                    f"Primary: {price_check['primary_price']} | "
                    f"{price_check['secondary_exchange']}: {price_check['secondary_price']} | "
                    f"Deviation: {price_check['deviation_pct']:.3f}%"
                )
                return "error"

        # ── DETECT MARKET REGIME ──────────────────────────
        regime_result = session.detector.detect(df)
        session.last_regime = regime_result.regime    # Update heartbeat state

        logger.info(
            f"[BotRunner:{session.venue_id}] Regime: {regime_result.regime} "
            f"({regime_result.confidence}) | "
            f"ADX: {regime_result.adx:.1f} | "
            f"ATR ratio: {regime_result.atr_ratio:.2f}"
        )

        # ── ROUTE TO CORRECT STRATEGY ─────────────────────
        signal = session.router.route(df, regime_result)
        session.last_signal = signal["signal"]    # Update heartbeat state

        logger.info(
            f"[BotRunner:{session.venue_id}] Signal: {signal['signal']} | "
            f"Strategy: {signal.get('strategy', 'none')} | "
            f"Reason: {signal.get('reason', 'none')}"
        )

        # ── PROCESS VALID SIGNAL ──────────────────────────
        if signal["signal"] in ("BUY", "SELL"):

            # Inject symbol — strategies don't know which pair
            signal["symbol"] = session.symbol

            if session.engine.dry_run:
                # Dry run — log signal before executing against paper money
                logger.info(
                    f"[BotRunner:{session.venue_id}] DRY RUN | "
                    f"{signal['signal']} {session.symbol} | "
                    f"Entry: {signal['entry']} | "
                    f"SL: {signal['sl']} | "
                    f"TP: {signal['tp']} | "
                    f"RSI: {signal['rsi']:.1f} | "
                    f"Regime: {signal['regime']} ({signal['regime_confidence']})"
                )

                # Generate narrative even in dry run — useful for review
                narrative = session.narrator.generate_entry(signal, session.symbol)
                logger.info(
                    f"[BotRunner:{session.venue_id}] DRY RUN NARRATIVE: "
                    f"{narrative.entry_narrative[:150]}..."
                )

            else:
                # Live mode — about to execute the signal
                logger.info(
                    f"[BotRunner:{session.venue_id}] Executing {signal['signal']} on {session.symbol}"
                )

            # claude code changed: uses session.engine.dry_run rather than
            # the module-level DRY_RUN — correct for whichever venue this
            # is (Kraken's dry-run state is independent, see KRAKEN_DRY_RUN).
            session.engine.execute_signal(signal)

        # ── MANAGE OPEN POSITIONS ─────────────────────────
        session.engine.manage_positions()    # Executes exits when SL/TP hit (real or simulated)

        if session.engine.dry_run:
            # Dry run — log current paper positions after management ran
            positions = session.engine.position_tracker.get_all_positions()

            if positions:
                for sym, pos in positions.items():
                    logger.info(
                        f"[BotRunner:{session.venue_id}] DRY RUN position | "
                        f"{sym} | {pos['side']} | "
                        f"Entry: {pos['entry_price']} | "
                        f"SL: {pos['sl']} | "
                        f"TP: {pos['tp']}"
                    )
            else:
                logger.info(f"[BotRunner:{session.venue_id}] No open positions")

        return "ok"

    except ccxt.NetworkError as e:
        # Network dropped — recoverable, run_bot() decides the retry wait
        logger.warning(f"[BotRunner:{session.venue_id}] Network error: {e}")
        return "network_error"

    except ccxt.RateLimitExceeded as e:
        # Rate limited — run_bot() backs off hard
        logger.warning(f"[BotRunner:{session.venue_id}] Rate limit hit: {e}")
        return "rate_limited"

    except Exception as e:
        # Unexpected error for THIS venue only — never crash the process,
        # never stop another venue's candle from being checked this pass
        logger.error(
            f"[BotRunner:{session.venue_id}] Unexpected error: "
            f"{type(e).__name__}: {e}"
        )
        return "error"


# ============================================================
# MAIN BOT FUNCTION
# ============================================================
def run_bot():
    """
    Main entry point — initialises all components and runs the loop.

    Flow every candle, per venue session (Binance always; Kraken too if
    KRAKEN_ENABLED=true — see VenueSession/process_venue_candle):
    1. Fetch OHLCV candles from that venue
    2. Build pandas DataFrame
    3. RegimeDetector classifies the market (that venue's own instance)
    4. StrategyRouter selects correct strategy for that regime (that
       venue's own instance)
    5. Signal returned with regime context attached
    6. If BUY/SELL → ExecutionEngine.execute_signal() (that venue's own
       engine)
    7. ExecutionEngine.manage_positions() checks all open trades
    8. Sleep until next candle (shared cadence across every venue)
    9. Every 5 minutes → print one heartbeat line per venue
    """

    # Set up logging before anything else
    setup_logging()
    logger = logging.getLogger(__name__)

    # ── STARTUP BANNER ────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("[BotRunner] Quant Bot Pro starting up")
    logger.info(f"[BotRunner] Symbol:     {SYMBOL}")
    logger.info(f"[BotRunner] Timeframe:  {TIMEFRAME}")
    logger.info(f"[BotRunner] Candles:    {CANDLE_LIMIT}")
    logger.info(f"[BotRunner] Interval:   {CANDLE_INTERVAL_SECONDS}s")
    logger.info(f"[BotRunner] Heartbeat:  every {HEARTBEAT_INTERVAL_SECONDS}s")
    logger.info(
        f"[BotRunner] Mode:       "
        f"{'DRY RUN — no real orders' if DRY_RUN else '⚠ LIVE TRADING'}"
    )
    logger.info("=" * 60)

    # ── CONNECT TO BINANCE (primary venue) ────────────────────
    exchange = ccxt.binance({
        "apiKey":  API_KEY,
        "secret":  API_SECRET,
        "options": {"defaultType": "spot"},    # Spot trading only
    })

    # Test connection before initialising anything else
    try:
        start = time.time()
        exchange.load_markets()    # Fetch all trading pairs — confirms auth
        latency_ms = int((time.time() - start) * 1000)
        logger.info(
            f"[BotRunner] Connected to Binance "
            f"(latency: {latency_ms}ms)"
        )
    except ccxt.AuthenticationError:
        logger.critical(
            "[BotRunner] Authentication FAILED — check API keys. "
            "Set BINANCE_API_KEY and BINANCE_API_SECRET environment variables."
        )
        return    # Cannot proceed without valid credentials
    except Exception as e:
        logger.critical(f"[BotRunner] Exchange connection FAILED: {e}")
        return    # Cannot proceed without exchange access

    # ── INITIALISE ALL COMPONENTS ─────────────────────────────
    logger.info("[BotRunner] Initialising system components...")

    # ExecutionEngine — central coordinator (contains all sub-components)
    # claude code changed: pass DRY_RUN through — fixes architecture audit
    # finding C-2 (issue 2). ExecutionEngine's OrderManager/MarketData now
    # simulate fills and paper balance internally when dry_run=True, so the
    # signal-handling block below can call execute_signal()/manage_positions()
    # unconditionally instead of skipping them in dry run.
    engine = ExecutionEngine(exchange, dry_run=DRY_RUN)

    # RegimeDetector — classifies market regime every candle
    detector = RegimeDetector()

    # StrategyRouter — routes to correct strategy per regime
    router = StrategyRouter(
        allow_longs             = True,    # Long trades allowed
        allow_shorts            = True,    # Short trades allowed
        require_high_confidence = False,   # Accept MEDIUM confidence too
        min_adx_for_trend       = 20,      # Minimum ADX for trend trades
    )

    # TradeNarrativeGenerator — documents every trade in plain English
    narrator = TradeNarrativeGenerator()

    # claude code changed: new — fixes architecture audit issue 6.
    # PriceCrossChecker only calls the secondary exchange's public
    # fetch_ticker(), so no API keys are needed here.
    price_checker = PriceCrossChecker(secondary_exchange_id=SECONDARY_EXCHANGE)

    sessions = [
        VenueSession(
            venue_id = "binance",
            exchange = exchange,
            symbol   = SYMBOL,
            engine   = engine,
            detector = detector,
            router   = router,
            narrator = narrator,
        )
    ]

    # ── OPTIONALLY CONNECT TO KRAKEN (second, independent venue) ──
    # claude code changed: new. Kraken trades the SAME symbol as Binance
    # (SYMBOL, confirmed directly listed on Kraken — Step 5), fully
    # independently: its own candle fetch, its own RegimeDetector/
    # StrategyRouter instance, its own ExecutionEngine (with its own
    # PositionSizer/DrawdownGuard, confirmed independent in Step 12) —
    # never Binance's regime or signal. Disabled by default
    # (KRAKEN_ENABLED unset) — when disabled, `sessions` above is exactly
    # today's single-item Binance-only list, so Binance-only operation is
    # completely unaffected by this block existing.
    kraken_adapter = build_kraken_adapter()

    if kraken_adapter is not None:
        try:
            start = time.time()
            kraken_adapter.exchange.load_markets()
            latency_ms = int((time.time() - start) * 1000)
            logger.info(
                f"[BotRunner] Connected to Kraken "
                f"(latency: {latency_ms}ms, dry_run={kraken_adapter.dry_run})"
            )

            kraken_engine = ExecutionEngine(
                kraken_adapter.exchange,
                dry_run = kraken_adapter.dry_run,
                adapter = kraken_adapter,
            )
            kraken_detector = RegimeDetector()
            kraken_router = StrategyRouter(
                allow_longs             = True,
                allow_shorts            = True,
                require_high_confidence = False,
                min_adx_for_trend       = 20,
            )
            kraken_narrator = TradeNarrativeGenerator()

            sessions.append(VenueSession(
                venue_id = "kraken",
                exchange = kraken_adapter.exchange,
                symbol   = SYMBOL,
                engine   = kraken_engine,
                detector = kraken_detector,
                router   = kraken_router,
                narrator = kraken_narrator,
            ))
            logger.info(
                f"[BotRunner] Kraken session added — trading {SYMBOL} "
                f"independently of Binance"
            )

        except Exception as e:
            # A broken Kraken connection must never stop Binance from
            # trading — log and continue with Binance only.
            logger.error(
                f"[BotRunner] Kraken connection FAILED — continuing with "
                f"Binance only: {type(e).__name__}: {e}"
            )
    else:
        logger.info(
            "[BotRunner] Kraken not enabled (set KRAKEN_ENABLED=true to "
            "also trade it) — Binance only"
        )

    # ── STARTUP COMPONENT CHECK (per session) ─────────────────
    all_ok = True
    for session in sessions:
        session_price_checker = price_checker if session.venue_id == "binance" else None
        ok = check_components(
            session.engine, session.detector, session.router,
            session.narrator, session_price_checker,
        )
        all_ok = all_ok and ok

    if not all_ok:
        logger.critical(
            "[BotRunner] Startup component check FAILED. "
            "Fix errors and run dry_run_test.py before retrying."
        )
        return    # Do not start the loop with broken components

    # ── POSITION RECONCILIATION (per session) ─────────────────
    # Syncs with exchange to catch any positions open from before crash
    # Must complete before loop starts — never during it
    logger.info("[BotRunner] Reconciling positions with exchange...")

    for session in sessions:
        session.engine.position_tracker.reconcile_with_exchange(
            exchange        = session.exchange,
            tracked_symbols = [session.symbol],
        )

    logger.info("[BotRunner] Reconciliation complete — starting main loop")

    # ── HEARTBEAT STATE ───────────────────────────────────────
    last_heartbeat_time = time.time()    # When we last printed heartbeat
    loop_count          = 0              # How many loops completed

    # ── MAIN LOOP ─────────────────────────────────────────────
    while True:

        try:
            loop_count += 1    # Increment loop counter

            logger.info(
                f"[BotRunner] ══ Loop {loop_count} ══ "
                f"({len(sessions)} venue{'s' if len(sessions) != 1 else ''}) ══"
            )

            # claude code changed: new — processes every venue session in
            # turn, each fully independent (own fetch/regime/signal/
            # execute), each wrapped in its own error handling inside
            # process_venue_candle() so one venue's failure this pass
            # never stops another venue's candle from being checked.
            statuses = [
                process_venue_candle(
                    session,
                    price_checker if session.venue_id == "binance" else None,
                )
                for session in sessions
            ]

            # claude code changed: new — aggregates retry timing across
            # every venue processed this pass, reproducing this file's
            # original single-venue retry semantics exactly (worst status
            # wins) rather than letting one venue's transient error fall
            # through to the full CANDLE_INTERVAL_SECONDS wait.
            if "rate_limited" in statuses:
                logger.info("[BotRunner] Backing off 120s for rate limit...")
                time.sleep(120)
                continue

            if "network_error" in statuses or "error" in statuses:
                logger.info("[BotRunner] Waiting 60s before retrying...")
                time.sleep(60)
                continue

            # ── HEARTBEAT CHECK (per session) ─────────────────
            # Print heartbeat line if interval has elapsed
            now = time.time()
            if now - last_heartbeat_time >= HEARTBEAT_INTERVAL_SECONDS:

                for session in sessions:
                    print_heartbeat(
                        engine      = session.engine,
                        detector    = session.detector,
                        router      = session.router,
                        exchange    = session.exchange,
                        last_regime = session.last_regime,
                        last_signal = session.last_signal,
                        loop_count  = loop_count,
                    )

                last_heartbeat_time = now    # Reset heartbeat timer

            # ── SLEEP UNTIL NEXT CANDLE ───────────────────────
            logger.info(
                f"[BotRunner] Sleeping {CANDLE_INTERVAL_SECONDS}s "
                f"until next candle..."
            )
            time.sleep(CANDLE_INTERVAL_SECONDS)


        except KeyboardInterrupt:
            # Ctrl+C — clean graceful shutdown
            logger.info(
                "[BotRunner] Keyboard interrupt — shutting down cleanly"
            )

            # Print final route stats before exiting — per session
            for session in sessions:
                stats = session.router.get_route_stats()
                logger.info(
                    f"[BotRunner:{session.venue_id}] Session route stats: "
                    f"{stats['route_breakdown']}"
                )

            break    # Exit the while loop


        except ccxt.NetworkError as e:
            # claude code changed: kept as a top-level safety net — every
            # venue's OWN NetworkError is already caught inside
            # process_venue_candle() above, so this now only fires for
            # something genuinely unexpected outside that function (e.g.
            # the heartbeat/aggregation logic itself).
            logger.warning(f"[BotRunner] Network error: {e}")
            logger.info("[BotRunner] Waiting 60s before retrying...")
            time.sleep(60)


        except ccxt.RateLimitExceeded as e:
            # Rate limited — back off hard
            logger.warning(f"[BotRunner] Rate limit hit: {e}")
            logger.info("[BotRunner] Backing off 120s for rate limit...")
            time.sleep(120)


        except Exception as e:
            # Unexpected error — log full detail but keep bot alive
            # Never crash while positions are open
            logger.error(
                f"[BotRunner] Unexpected error in main loop: "
                f"{type(e).__name__}: {e}"
            )
            logger.info("[BotRunner] Waiting 60s before retrying...")
            time.sleep(60)

    # Loop exited cleanly
    logger.info("[BotRunner] Bot stopped cleanly")
    logger.info("=" * 60)


# ============================================================
# ENTRY POINT
# Only runs when executed directly:
#   python bot/core/bot_runner.py
# Does NOT run when imported by another module
# ============================================================
if __name__ == "__main__":
    run_bot()
