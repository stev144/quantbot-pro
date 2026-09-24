# ============================================================
# bot/core/pairs_bot_runner.py
# claude code changed: new file — live pairs-trading pilot, Step 4 (final).
#
# Master loop for the 3-pair pilot (DODO/FIDA, MINA/ONG, AVA/PHA).
# Mirrors bot/core/bot_runner.py's proven shape (a per-unit Session
# dataclass, a standalone per-unit process_*_candle() function each
# session's errors are caught inside, worst-status-wins retry timing,
# startup component/reconciliation gates before the loop starts) but
# is NOT a modification of that file — the data flow is fundamentally
# different (two symbols per pair, a spread/z-score decision instead of
# a single-symbol BUY/SELL signal, margin borrow/repay instead of plain
# spot orders), so this is its own script per the approved plan
# (C:\Users\HP\.claude\plans\dazzling-zooming-origami.md).
#
# HOW TO RUN:
#   python bot/core/pairs_bot_runner.py
#
# BEFORE RUNNING LIVE:
#   1. python bot/core/dry_run_test.py   <- run this first (once its
#      pairs sections exist — see the approved plan's rollout sequence)
#   2. Set DRY_RUN = True below (default) and let it run for a real
#      soak period — see the plan's rollout step 5 for the full go-live
#      checklist. DRY_RUN mode needs ZERO real Binance API credentials:
#      every write (transfer/borrow/order/repay) is simulated locally
#      using real fetched prices; only public market-data endpoints are
#      ever called.
#   3. Only set DRY_RUN = False, AND set PAIRS_PILOT_LIVE_CONFIRMED=true,
#      once the go-live checklist is complete. Both are required —
#      mirrors bot/engines/kraken_adapter.py's build_kraken_adapter()
#      pattern of refusing to construct a live path without an explicit,
#      deliberate second signal, not just one flag flip.
# ============================================================

import sys
import time
import logging
import logging.handlers
import os
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import ccxt

# ---- Windows console UTF-8 fix — see bot_runner.py's identical block for why ----
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# ---- Project root on sys.path — see bot_runner.py's identical block for why ----
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ---- Django app registry — see bot_runner.py's identical block for why ----
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from bot.config.logging_config import LOG_MAX_BYTES, LOG_BACKUP_COUNT
from bot.journal.models import TradeRecord
from bot.pairs.config import load_pair_configs, PairConfig
from bot.pairs.signal_engine import PairSignalEngine
from bot.pairs.execution import PairsExecutionEngine

# ── SAFETY ───────────────────────────────────────────────────
# DRY_RUN = True  -> every leg fill/borrow/repay/transfer is simulated
#                    in-process using real fetched prices/rates; NOTHING
#                    is ever sent to Binance as a write request.
# DRY_RUN = False -> live trading with real capital, real borrowed
#                    coins, real liquidation risk. Also requires
#                    PAIRS_PILOT_LIVE_CONFIRMED=true (checked below) —
#                    two independent, deliberate signals, not one flag.
DRY_RUN = True

CANDLE_TIMEFRAME = "1h"        # matches the validated strategy's design
CANDLE_INTERVAL_SECONDS = 3600  # poll once per hour, matching the candle width
HEARTBEAT_INTERVAL_SECONDS = 300

API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")

LOG_FILE_PATH = os.path.join(PROJECT_ROOT, "logs", "pairs_bot.log")


def setup_logging():
    """Separate log file from bot_runner.py's (trades.log) — these are
    two independent bots; interleaving their logs would make either
    harder to audit."""
    os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.handlers.RotatingFileHandler(
        filename=LOG_FILE_PATH, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


@dataclass
class PairSession:
    """One per pilot pair. Bundles the pair's own signal engine (its own
    Kalman state, its own EntryExitEngine instance) with tracking for
    the loop — mirrors bot_runner.py's VenueSession, but per-pair
    instead of per-venue since all 3 pairs share ONE
    PairsExecutionEngine/Binance account."""
    pair_config: PairConfig
    signal_engine: PairSignalEngine
    last_candle_ts: Optional[int] = None  # ms epoch of the last CLOSED candle processed, dedupes re-polls
    last_action: str = "NONE"


def _fetch_last_closed_candle(exchange, symbol: str, timeframe: str):
    """Returns (timestamp_ms, close_price) for the last FULLY CLOSED
    candle — candles[-1] from ccxt is typically the still-forming
    current candle, which would give a repainting close price if acted
    on directly. limit=3 is enough to guarantee at least one closed
    candle even right at an hour boundary."""
    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=3)
    if not candles or len(candles) < 2:
        return None, None
    ts_ms, _o, _h, _l, close, _v = candles[-2]
    return ts_ms, float(close)


BOOTSTRAP_CANDLES = 1000  # comfortably above ZSCORE_WINDOW(504)+WARMUP_CANDLES(168) — Binance's own klines limit ceiling

# claude code changed: new — live beta-drift guard. cointegration_engine.py's
# passes_filters gate (bot/pairs/config.py) only checks the STATIC,
# full-history relationship at config-load time; it says nothing about
# whether the online Kalman filter's CURRENT tracked hedge ratio still
# resembles that seed. Real, confirmed finding 2026-09-24: MINA/ONG's
# recent-data beta flipped sign (+1.625 seed -> -0.307 replicated-live)
# while still passing the static test — this pair's "spread" is no
# longer the relationship that was validated, and nothing was watching
# for it. BETA_DRIFT_MAX_RATIO=0.5 means more than a 50% change in
# magnitude from the seed blocks new entries; a sign flip always blocks
# regardless of magnitude (categorically worse than a large same-sign
# move). Existing open positions are never force-closed by this guard —
# only NEW entries are refused — matching this file's existing
# conservative-on-ambiguity pattern (see reconcile_pairs()).
BETA_DRIFT_MAX_RATIO = 0.5


def _check_beta_drift(seed_beta: float, current_beta: float) -> tuple[bool, str]:
    """Returns (drifted, reason). A sign flip is always flagged; otherwise
    flagged when |current - seed| / |seed| exceeds BETA_DRIFT_MAX_RATIO."""
    if seed_beta == 0:
        return False, ""  # can't compute a ratio; nothing sane to compare against
    if (seed_beta > 0) != (current_beta > 0):
        return True, f"sign flip (seed={seed_beta:.4f}, current={current_beta:.4f})"
    drift_ratio = abs(current_beta - seed_beta) / abs(seed_beta)
    if drift_ratio > BETA_DRIFT_MAX_RATIO:
        return True, f"{drift_ratio:.0%} drift from seed (seed={seed_beta:.4f}, current={current_beta:.4f})"
    return False, ""


def bootstrap_session(session: PairSession, exchange) -> None:
    """Replays the most recent real closed candles (fetched fresh from
    Binance right now, not from potentially-stale data/*.csv files) through
    the signal engine's state-only update path, so it can generate a real
    signal on its first LIVE candle instead of needing ~1 week of cold-start
    accumulation. Never triggers a trade decision — see
    PairSignalEngine.bootstrap_candle()'s docstring for why that's
    structurally impossible here (it never touches self.engine.current_trade)."""
    logger = logging.getLogger(__name__)
    cfg = session.pair_config

    candles_a = exchange.fetch_ohlcv(cfg.binance_symbol_a, timeframe=CANDLE_TIMEFRAME, limit=BOOTSTRAP_CANDLES)
    candles_b = exchange.fetch_ohlcv(cfg.binance_symbol_b, timeframe=CANDLE_TIMEFRAME, limit=BOOTSTRAP_CANDLES)

    # drop the still-forming current candle from each (same reasoning as _fetch_last_closed_candle)
    closed_a = {c[0]: c[4] for c in candles_a[:-1]}
    closed_b = {c[0]: c[4] for c in candles_b[:-1]}
    common_ts = sorted(set(closed_a.keys()) & set(closed_b.keys()))

    if not common_ts:
        logger.warning(f"[PairsBotRunner:{cfg.pair_name}] Bootstrap found no overlapping candle timestamps between legs — starting cold instead.")
        return

    price_pairs = [(closed_a[ts], closed_b[ts]) for ts in common_ts]
    session.signal_engine.bootstrap_from_history(price_pairs)
    session.last_candle_ts = common_ts[-1]

    zscore_ready = session.signal_engine._zscore_roller._values and len(session.signal_engine._zscore_roller._values) >= session.signal_engine._zscore_roller.min_periods
    logger.info(
        f"[PairsBotRunner:{cfg.pair_name}] Bootstrapped {len(price_pairs)} historical candles "
        f"({pd.Timestamp(common_ts[0], unit='ms', tz='UTC')} -> {pd.Timestamp(common_ts[-1], unit='ms', tz='UTC')}) | "
        f"z-score ready: {bool(zscore_ready)}"
    )


def reconcile_pairs(pair_configs: list[PairConfig], exec_engine: PairsExecutionEngine) -> None:
    """Hard safety gate, run once before the loop starts — mirrors
    bot_runner.py's PositionTracker.reconcile_with_exchange() in spirit,
    but with an honest, different resolution: this strategy's exit-rule
    state (stop_zscore/target_zscore/min_hold_hours, entry_timestamp —
    see bot/research/entry_exit_engine.py's TradeRecord) lives only in
    PairSignalEngine's in-memory EntryExitEngine instance and is NOT
    persisted anywhere (bot/journal/models.py's TradeRecord has no
    columns for it). A crash mid-trade therefore cannot be safely
    resumed with correct exit parameters — guessing them would be worse
    than refusing to trade. So: any pair with OPEN legs already in the
    database at startup is disabled for this run rather than resumed,
    and requires a human to manually reconcile the real isolated-margin
    account state and clear those records before it trades again."""
    logger = logging.getLogger(__name__)
    for cfg in pair_configs:
        strategy_tag = f"PairsTrading_{cfg.pair_name}"
        open_legs = TradeRecord.objects.filter(strategy=strategy_tag, status="OPEN")
        if open_legs.exists():
            exec_engine.disable_pair(
                cfg.pair_name,
                f"{open_legs.count()} OPEN leg(s) found in TradeRecord from a previous run — "
                f"z-score exit-rule state is not persisted and cannot be safely reconstructed. "
                f"Manually reconcile the real isolated-margin account state for "
                f"{cfg.binance_symbol_a}/{cfg.binance_symbol_b} before clearing these records.",
            )
        else:
            logger.info(f"[PairsBotRunner] {cfg.pair_name}: no OPEN legs found at startup — clear to trade.")


def process_pair_candle(session: PairSession, exchange, exec_engine: PairsExecutionEngine) -> str:
    """One full pass for a SINGLE pair: fetch both legs' latest closed
    candle -> feed the signal engine -> act on ENTER/EXIT. Returns
    "ok"/"network_error"/"rate_limited"/"error" — run_bot() aggregates
    across all pairs (worst-status-wins) exactly like bot_runner.py's
    process_venue_candle(), so one pair's transient failure never stops
    another pair's candle from being checked this pass."""
    logger = logging.getLogger(__name__)
    cfg = session.pair_config

    try:
        ts_a, price_a = _fetch_last_closed_candle(exchange, cfg.binance_symbol_a, CANDLE_TIMEFRAME)
        ts_b, price_b = _fetch_last_closed_candle(exchange, cfg.binance_symbol_b, CANDLE_TIMEFRAME)

        if ts_a is None or ts_b is None:
            logger.warning(f"[PairsBotRunner:{cfg.pair_name}] Empty candle response — skipping this pass")
            return "error"

        if ts_a != ts_b:
            logger.warning(
                f"[PairsBotRunner:{cfg.pair_name}] Leg candle timestamps disagree "
                f"({cfg.binance_symbol_a}={ts_a} vs {cfg.binance_symbol_b}={ts_b}) — skipping this pass"
            )
            return "error"

        if session.last_candle_ts is not None and ts_a <= session.last_candle_ts:
            logger.debug(f"[PairsBotRunner:{cfg.pair_name}] No new closed candle yet")
            return "ok"

        session.last_candle_ts = ts_a
        timestamp = pd.Timestamp(ts_a, unit="ms", tz="UTC")

        decision = session.signal_engine.process_candle(timestamp, price_a, price_b)
        session.last_action = decision.action

        logger.info(
            f"[PairsBotRunner:{cfg.pair_name}] {timestamp} | action={decision.action} | "
            f"{decision.direction or decision.reason or ''}"
        )

        if decision.action == "ENTER":
            drifted, drift_reason = _check_beta_drift(cfg.hedge_ratio, session.signal_engine.current_beta)
            if not exec_engine.is_pair_tradeable(cfg.pair_name):
                logger.warning(f"[PairsBotRunner:{cfg.pair_name}] Entry signal fired but pair is disabled — not opening.")
            elif exec_engine.has_open_position(cfg.pair_name):
                logger.error(f"[PairsBotRunner:{cfg.pair_name}] Entry signal fired but a position is already tracked open — not opening a second.")
            elif drifted:
                # claude code changed: new — see BETA_DRIFT_MAX_RATIO's comment above.
                logger.warning(
                    f"[PairsBotRunner:{cfg.pair_name}] Entry signal fired but REFUSED — "
                    f"online beta has drifted from the validated seed: {drift_reason}. "
                    f"This pair's current spread may no longer represent the validated relationship."
                )
            else:
                exec_engine.open_pair_trade(cfg, decision)

        elif decision.action == "EXIT":
            if exec_engine.has_open_position(cfg.pair_name):
                exec_engine.close_pair_trade(cfg, exit_reason=decision.reason)
            else:
                logger.error(f"[PairsBotRunner:{cfg.pair_name}] Exit signal fired but no position is tracked open — nothing to close.")

        return "ok"

    except ccxt.NetworkError as e:
        logger.warning(f"[PairsBotRunner:{cfg.pair_name}] Network error: {e}")
        return "network_error"
    except ccxt.RateLimitExceeded as e:
        logger.warning(f"[PairsBotRunner:{cfg.pair_name}] Rate limit hit: {e}")
        return "rate_limited"
    except Exception as e:
        logger.error(f"[PairsBotRunner:{cfg.pair_name}] Unexpected error: {type(e).__name__}: {e}")
        return "error"


def run_bot():
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("[PairsBotRunner] Steph Quant Technologies — Pairs Pilot starting up")
    logger.info(f"[PairsBotRunner] Timeframe:  {CANDLE_TIMEFRAME}")
    logger.info(f"[PairsBotRunner] Interval:   {CANDLE_INTERVAL_SECONDS}s")
    logger.info(f"[PairsBotRunner] Heartbeat:  every {HEARTBEAT_INTERVAL_SECONDS}s")
    logger.info(f"[PairsBotRunner] Mode:       {'DRY RUN — no real orders/borrows/transfers' if DRY_RUN else '*** LIVE TRADING — REAL MARGIN CAPITAL ***'}")
    logger.info("=" * 60)

    if not DRY_RUN and os.getenv("PAIRS_PILOT_LIVE_CONFIRMED", "").lower() != "true":
        logger.critical(
            "[PairsBotRunner] DRY_RUN=False but PAIRS_PILOT_LIVE_CONFIRMED is not set to 'true'. "
            "Refusing to start live — this requires two independent, deliberate confirmations, "
            "not one flag flip (real capital, real borrowed coins, real liquidation risk). "
            "Complete the go-live checklist in the approved plan first, then set both."
        )
        return

    try:
        pair_configs = load_pair_configs()
    except Exception as e:
        logger.critical(f"[PairsBotRunner] Failed to load pair configs: {e}")
        return
    logger.info(f"[PairsBotRunner] Loaded {len(pair_configs)} pilot pairs: {[c.pair_name for c in pair_configs]}")

    exchange = ccxt.binance({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "options": {"defaultType": "spot"},  # isolated-margin orders are placed via explicit per-call params — see bot/pairs/margin_adapter.py
    })

    try:
        start = time.time()
        exchange.load_markets()  # public endpoint — succeeds with zero credentials, which is what makes DRY_RUN need none
        latency_ms = int((time.time() - start) * 1000)
        logger.info(f"[PairsBotRunner] Connected to Binance (latency: {latency_ms}ms)")
    except Exception as e:
        logger.critical(f"[PairsBotRunner] Exchange connection FAILED: {e}")
        return

    exec_engine = PairsExecutionEngine(exchange, dry_run=DRY_RUN)

    reconcile_pairs(pair_configs, exec_engine)

    sessions = [PairSession(pair_config=cfg, signal_engine=PairSignalEngine(cfg)) for cfg in pair_configs]

    logger.info("[PairsBotRunner] Bootstrapping signal engines from recent real history...")
    for session in sessions:
        try:
            bootstrap_session(session, exchange)
        except Exception as e:
            logger.error(f"[PairsBotRunner:{session.pair_config.pair_name}] Bootstrap failed ({e}) — this pair will start cold and need ~1 week of live candles before its first possible signal.")

    logger.info("[PairsBotRunner] Startup complete — starting main loop")

    last_heartbeat_time = time.time()
    loop_count = 0

    while True:
        try:
            loop_count += 1
            logger.info(f"[PairsBotRunner] == Loop {loop_count} == ({len(sessions)} pairs) ==")

            statuses = [process_pair_candle(session, exchange, exec_engine) for session in sessions]

            if "rate_limited" in statuses:
                logger.info("[PairsBotRunner] Backing off 120s for rate limit...")
                time.sleep(120)
                continue

            if "network_error" in statuses or "error" in statuses:
                logger.info("[PairsBotRunner] Waiting 60s before retrying...")
                time.sleep(60)
                continue

            now = time.time()
            if now - last_heartbeat_time >= HEARTBEAT_INTERVAL_SECONDS:
                try:
                    equity = exec_engine.compute_mark_to_market_equity(pair_configs)
                    tripped = exec_engine.drawdown_guard.update(equity)
                    logger.info(
                        f"[PairsBotRunner] HEARTBEAT | loop={loop_count} | "
                        f"mark_to_market_equity=${equity:.2f} | drawdown_guard_tripped={tripped}"
                    )
                    for session in sessions:
                        seed_beta = session.pair_config.hedge_ratio
                        current_beta = session.signal_engine.current_beta
                        drifted, drift_reason = _check_beta_drift(seed_beta, current_beta)
                        logger.info(
                            f"[PairsBotRunner]   {session.pair_config.pair_name}: "
                            f"last_action={session.last_action} | "
                            f"open={exec_engine.has_open_position(session.pair_config.pair_name)} | "
                            f"tradeable={exec_engine.is_pair_tradeable(session.pair_config.pair_name)} | "
                            f"seed_beta={seed_beta:.4f} | current_beta={current_beta:.4f}"
                            f"{' | BETA DRIFT: ' + drift_reason if drifted else ''}"
                        )
                except Exception as e:
                    logger.error(f"[PairsBotRunner] Heartbeat computation failed: {e}")
                last_heartbeat_time = now

            logger.info(f"[PairsBotRunner] Sleeping {CANDLE_INTERVAL_SECONDS}s until next candle...")
            time.sleep(CANDLE_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            logger.info("[PairsBotRunner] Keyboard interrupt — shutting down cleanly")
            break

        except ccxt.NetworkError as e:
            logger.warning(f"[PairsBotRunner] Network error: {e}")
            time.sleep(60)

        except ccxt.RateLimitExceeded as e:
            logger.warning(f"[PairsBotRunner] Rate limit: {e}")
            time.sleep(120)

        except Exception as e:
            logger.error(f"[PairsBotRunner] Unexpected top-level error: {type(e).__name__}: {e}")
            time.sleep(60)

    logger.info("[PairsBotRunner] Stopped cleanly")


if __name__ == "__main__":
    run_bot()
