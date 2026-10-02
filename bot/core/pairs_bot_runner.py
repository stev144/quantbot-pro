# ============================================================
# bot/core/pairs_bot_runner.py
# claude code changed: new file — live pairs-trading pilot, Step 4 (final).
#
# Master loop for the pairs pilot: 10 pairs monitored (real, live Kalman
# beta tracking + NORMAL/DEGRADED/INVALIDATED health for all 10), but
# only the pairs currently passing the real cointegration gate are
# entry-eligible and can actually trade (today: DODO/FIDA, MINA/ONG —
# see bot/pairs/config.py's is_entry_eligible/ENTRY_ELIGIBLE_PAIR_NAMES
# and bot/pairs/monitor_engine.py for why the other pairs are
# structurally incapable of trading rather than just gated off).
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
import io
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
from bot.pairs.monitor_engine import PairMonitorEngine
from bot.pairs.execution import PairsExecutionEngine
from bot.pairs.health import evaluate_pair_health, write_health_snapshot, PairHealthState
from bot.pairs.config import COINTEGRATION_CSV   # claude code changed: Stage 2D Rev B step 6 — the seed registry lives next to the research outputs
from bot.quant_core.seed import load_seed, validate_seed, verify_theta_reproduction   # claude code changed: Stage 2D Rev B step 6 (D9); C6 causal seed + data-version check
from bot.quant_core.protocol import CANONICAL, METHODOLOGY_VERSION   # claude code changed: Stage 2D Rev B C6; D-2 provenance on rejections
from bot.quant_core.lifecycle import ENTRY_SIGN_MISMATCH   # claude code changed: Stage 2D Rev B D-2
from bot.pairs.canonical_eligibility import ELIGIBILITY_DIR, check_entry_eligibility   # claude code changed: Stage 2D Rev B D-3

PROTOCOL = CANONICAL   # claude code changed: Stage 2D Rev B — the protocol live seeds and trades under (its hash must match the seed record)

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
        encoding="utf-8",  # claude code changed: explicit — without this, RotatingFileHandler falls back to locale.getpreferredencoding() (cp1252 on this machine), same failure mode as the console handler below, even though it happened not to crash on this file yet
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    # claude code changed: real bug found 2026-09-24 — plain
    # logging.StreamHandler() defaults to sys.stderr, and on this machine,
    # when launched as a background/piped process, sys.stderr sometimes
    # ends up without a working .reconfigure(encoding="utf-8") (the
    # module-level reconfigure block above silently no-ops via its
    # hasattr guard), leaving Python's default Windows console codepage
    # (cp1252) in effect. ccxt's DEBUG-level logging of full exchange
    # responses occasionally contains a character cp1252 can't encode,
    # which crashed console_handler.emit() with UnicodeEncodeError twice
    # per startup (confirmed via two separate restarts' captured output).
    # Python's logging module swallows the exception itself (doesn't
    # crash the bot), so this never stopped trading — but it silently
    # dropped whatever that console line was meant to show. Explicitly
    # wrapping the raw buffer in a UTF-8 TextIOWrapper with
    # errors="backslashreplace" fixes it unconditionally, without
    # depending on whether the earlier reconfigure() call happened to work.
    console_stream = sys.stdout
    if hasattr(console_stream, "buffer"):
        console_stream = io.TextIOWrapper(console_stream.buffer, encoding="utf-8", errors="backslashreplace")
    console_handler = logging.StreamHandler(console_stream)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


@dataclass
class PairSession:
    """One per pilot pair. Bundles the pair's own engine (its own Kalman
    state) with tracking for the loop — mirrors bot_runner.py's
    VenueSession, but per-pair instead of per-venue since all pairs share
    ONE PairsExecutionEngine/Binance account.

    claude code changed: `engine` is now PairSignalEngine (full
    entry/exit + Kalman, for the pair_config.is_entry_eligible pairs) OR
    PairMonitorEngine (Kalman beta tracking only, for the
    monitoring-only pairs — see bot/pairs/monitor_engine.py). Both
    expose the same `.current_beta` property, which is all the
    heartbeat/health code below needs; process_pair_candle() branches on
    pair_config.is_entry_eligible for everything else (entry/exit
    decisions only ever run for the entry-eligible engine type)."""
    pair_config: PairConfig
    engine: "PairSignalEngine | PairMonitorEngine"
    last_candle_ts: Optional[int] = None  # ms epoch of the last CLOSED candle processed, dedupes re-polls
    last_action: str = "NONE"
    seed_ok: bool = False                 # claude code changed: Stage 2D Rev B step 6 (D9) — True only after a full replay from the validated canonical seed candle; entries are refused otherwise
    seed_refusal: str = "not bootstrapped"   # claude code changed: Stage 2D Rev B step 6 (D9)
    seed: Optional[object] = None             # claude code changed: Stage 2D Rev B D-2/D-3 — the validated KalmanSeed (lineage for rejections and eligibility)


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


BOOTSTRAP_CANDLES = 1000  # comfortably above ZSCORE_WINDOW(504)+WARMUP_CANDLES(168) — Binance's own klines limit ceiling. claude code changed: Stage 2D Rev B — now used for MONITOR-ONLY pairs only; trading pairs replay from the canonical seed (D9)
SEED_REGISTRY_DIR = os.path.dirname(COINTEGRATION_CSV)   # claude code changed: Stage 2D Rev B step 6 — research_data/, where KalmanFilterEngine writes kalman_seeds.json
HOUR_MS = 3_600_000   # claude code changed: Stage 2D Rev B step 6


def _fetch_closed_history(exchange, symbol: str, since_ms: int, now_ms: int, page_limit: int = 1000):   # claude code changed: Stage 2D Rev B step 6 (D9) — new
    """{open_ms: close} for every CLOSED 1h candle from since_ms to now,
    paginated (a canonical replay can span years). Hard page ceiling so a
    misbehaving endpoint can never loop forever; ceiling reached = error."""
    out, cursor = {}, since_ms
    max_pages = (now_ms - since_ms) // (HOUR_MS * page_limit) + 3
    for _ in range(max_pages):
        batch = exchange.fetch_ohlcv(symbol, timeframe=CANDLE_TIMEFRAME, since=cursor, limit=page_limit)
        closed = [c for c in batch if c[0] + HOUR_MS <= now_ms]   # a candle is closed once open + 1h has passed
        for c in closed:
            out[int(c[0])] = float(c[4])
        if not batch or len(closed) < len(batch) or batch[-1][0] + HOUR_MS > now_ms:
            return out
        nxt = int(batch[-1][0]) + HOUR_MS
        if nxt <= cursor:
            raise RuntimeError(f"{symbol}: kline pagination did not advance at {cursor}")
        cursor = nxt
    raise RuntimeError(f"{symbol}: kline pagination exceeded {max_pages} pages from {since_ms}")

# claude code changed: 2026-09-24 — the ad-hoc BETA_DRIFT_MAX_RATIO=0.5
# guard that was here was superseded by bot/pairs/health.py after a real
# historical-evidence audit showed 50% drift does NOT predict worse trade
# outcomes (see that module's docstring for the full investigation).
# PairHealthState/evaluate_pair_health() is now the single authoritative
# health source, used identically by the entry gate below, the heartbeat
# log, and the dashboard.
# claude code changed: DEFAULT_ENTRY_POLICY — which health states still
# permit a new entry. NORMAL always does; DEGRADED is blocked by default
# per the governance requirement that degraded pairs never silently keep
# trading, but is a named, single place to override later if evidence
# ever justifies trading through a specific DEGRADED condition; INVALIDATED
# is never enterable, full stop — deliberately not made configurable.
ENTRY_ALLOWED_STATES = {PairHealthState.NORMAL}


def _entry_refusal_reason(health) -> Optional[str]:
    """None if the entry is allowed to proceed on health grounds; else the
    exact log message to emit instead of opening the trade."""
    if health.state in ENTRY_ALLOWED_STATES:
        return None
    return f"ENTRY REFUSED — PAIR {health.state.value} — {health.summary()}"


def bootstrap_session(session: PairSession, exchange) -> None:
    """Replays the most recent real closed candles (fetched fresh from
    Binance right now, not from potentially-stale data/*.csv files) through
    the engine's state-only update path, so it can generate a real signal
    (entry-eligible pairs) or a real tracked beta (monitoring-only pairs)
    on its first LIVE candle instead of needing ~1 week of cold-start
    accumulation. Never triggers a trade decision — see
    PairSignalEngine.bootstrap_candle()'s docstring for why that's
    structurally impossible here (it never touches self.engine.current_trade);
    PairMonitorEngine.update() has no trade-decision path to touch at all."""
    logger = logging.getLogger(__name__)
    cfg = session.pair_config

    if cfg.is_entry_eligible:   # claude code changed: Stage 2D Rev B step 6 (D9) — trading pairs: canonical replay from the seed, or no trading
        _bootstrap_from_canonical_seed(session, exchange)
        return

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
    session.last_candle_ts = common_ts[-1]

    # claude code changed: Stage 2D Rev B step 6 — monitor-only pairs only now (the entry-eligible branch moved to _bootstrap_from_canonical_seed)
    for price_a, price_b in price_pairs:
        session.engine.update(price_a, price_b)
    logger.info(
        f"[PairsBotRunner:{cfg.pair_name}] Bootstrapped {len(price_pairs)} historical candles "
        f"({pd.Timestamp(common_ts[0], unit='ms', tz='UTC')} -> {pd.Timestamp(common_ts[-1], unit='ms', tz='UTC')}) | "
        f"MONITOR_ONLY | current_beta={session.engine.current_beta:.4f}"
    )


def _bootstrap_from_canonical_seed(session: PairSession, exchange) -> None:   # claude code changed: Stage 2D Rev B step 6 (D9) — new
    """Replay EVERY closed candle from the research seed candle through the
    state-only path, so the live filter state equals research's (same seed
    candle, θ₀, P₀, Q, R, update order, warmup, gap policy). Fails closed —
    session.seed_ok stays False and entries are refused — when the seed
    record is missing/stale/incompatible or the exchange history does not
    start exactly at the seed candle."""
    logger = logging.getLogger(__name__)
    cfg = session.pair_config
    # claude code changed: Stage 2D Rev B C6 — the seed must be CAUSAL (θ₀ from an
    # init window ending one candle before the seed); it is no longer compared
    # with cointegration_pairs.csv's hedge_ratio/intercept (a look-ahead fit).
    check = validate_seed(load_seed(cfg.pair_name, SEED_REGISTRY_DIR), pair_name=cfg.pair_name, protocol=PROTOCOL)
    if not check.ok:
        session.seed_ok, session.seed_refusal = False, check.reason
        logger.critical(f"[PairsBotRunner:{cfg.pair_name}] ENTRIES REFUSED — {check.reason}")
        return
    seed = check.value
    init_ms = int(pd.Timestamp(seed.init_window_start).timestamp() * 1000)   # claude code changed: Stage 2D Rev B C6 — history from the init window start
    since_ms = int(seed.seed_ts.timestamp() * 1000)
    now_ms = int(exchange.milliseconds())
    hist_a = _fetch_closed_history(exchange, cfg.binance_symbol_a, init_ms, now_ms)
    hist_b = _fetch_closed_history(exchange, cfg.binance_symbol_b, init_ms, now_ms)
    reproduced = verify_theta_reproduction(seed, hist_a, hist_b)   # claude code changed: Stage 2D Rev B C6 — data-version check: exchange closes must reproduce θ₀
    if not reproduced.ok:
        session.seed_ok, session.seed_refusal = False, reproduced.reason
        logger.critical(f"[PairsBotRunner:{cfg.pair_name}] ENTRIES REFUSED — {reproduced.reason}")
        return
    common = sorted(t for t in set(hist_a) & set(hist_b) if t >= since_ms)   # claude code changed: Stage 2D Rev B C6 — the filter starts at the seed candle
    if not common or common[0] != since_ms:
        session.seed_ok = False
        session.seed_refusal = (f"exchange history does not start at the seed candle {seed.seed_timestamp} "
                                f"(first common candle: {pd.Timestamp(common[0], unit='ms', tz='UTC') if common else None})")
        logger.critical(f"[PairsBotRunner:{cfg.pair_name}] ENTRIES REFUSED — {session.seed_refusal}")
        return
    # candles present on only one leg are left out here and stepped by the
    # engine as missing grid candles — exactly research's NaN grid rows
    session.engine = PairSignalEngine(cfg, theta0=(seed.beta0, seed.alpha0), protocol=PROTOCOL)   # claude code changed: Stage 2D Rev B C6 — θ₀ from the causal seed record
    session.engine.bootstrap_from_history(
        [(pd.Timestamp(ts, unit="ms", tz="UTC"), hist_a[ts], hist_b[ts]) for ts in common])
    session.last_candle_ts = common[-1]
    session.seed_ok, session.seed_refusal = True, ""
    session.seed = seed   # claude code changed: Stage 2D Rev B D-2/D-3
    logger.info(
        f"[PairsBotRunner:{cfg.pair_name}] Canonical replay of {len(common)} candles from seed "
        f"{seed.seed_timestamp} -> {pd.Timestamp(common[-1], unit='ms', tz='UTC')} | "
        f"missing grid candles stepped: {session.engine.missing_candles_stepped} | "
        f"z-score ready: {session.engine._zscore_roller.n_valid >= session.engine._zscore_roller.min_periods}"
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
    # claude code changed: Post-audit Phase 1 A1 — an unresolved
    # MANUAL_REVIEW_REQUIRED execution incident (failed mid-open unwind, failed
    # or exchange-blocked close) keeps its pair disabled across restarts.
    for name in exec_engine.trade_logger.unresolved_manual_review_pairs():
        exec_engine.disable_pair(name, "unresolved MANUAL_REVIEW_REQUIRED execution incident in the journal "
                                       "(bot.journal.models.ExecutionIncident) — resolve it before this pair trades again.")
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

        if not cfg.is_entry_eligible:
            # claude code changed: monitoring-only pair (see bot/pairs/monitor_engine.py)
            # — advance Kalman beta tracking only. No signal, no sizing, no
            # entry/exit: this engine type structurally has no trade-decision
            # path to call (PairMonitorEngine never constructs an
            # EntryExitEngine at all), so there is nothing else to do here.
            session.engine.update(price_a, price_b)
            session.last_action = "MONITOR_ONLY"
            logger.info(
                f"[PairsBotRunner:{cfg.pair_name}] {timestamp} | MONITOR_ONLY | "
                f"current_beta={session.engine.current_beta:.4f}"
            )
            return "ok"

        decision = session.engine.process_candle(timestamp, price_a, price_b)
        session.last_action = decision.action

        # claude code changed: Stage 2D Rev B D-2 — was "action=… | <direction or reason>",
        # which logged a C1 rejection as "action=NONE | LONG_SPREAD" (direction,
        # not reason). NONE/HOLD/EXIT now always name the reason; a direction is
        # shown only as what was INTENDED.
        detail = f"direction={decision.direction}" if decision.action == "ENTER" else (
            f"reason={decision.reason or '-'}" + (f" intended={decision.direction}" if decision.direction else ""))
        logger.info(f"[PairsBotRunner:{cfg.pair_name}] {timestamp} | action={decision.action} | {detail}")

        if decision.action == "NONE" and decision.reason == ENTRY_SIGN_MISMATCH and decision.rejection is not None:   # claude code changed: Stage 2D Rev B D-2 — durable journal record
            exec_engine.trade_logger.log_signal_rejection(
                cfg.pair_name, decision.rejection, methodology_version=METHODOLOGY_VERSION,
                protocol_hash=PROTOCOL.config_hash(), data_version=getattr(session.seed, "data_version", "") or "")

        if decision.action == "ENTER":
            health = evaluate_pair_health(
                cfg.pair_name, cfg.hedge_ratio, session.engine.current_beta,
                cfg.passes_filters, cfg.reject_reason,
            )
            refusal = _entry_refusal_reason(health)
            eligibility = check_entry_eligibility(cfg.pair_name, seed=session.seed, protocol=PROTOCOL, registry_dir=ELIGIBILITY_DIR)   # claude code changed: Stage 2D Rev B D-3
            opened = None   # claude code changed: Stage 2D Rev B D-3 — any path below that does not open aborts the engine-side entry
            if not session.seed_ok:   # claude code changed: Stage 2D Rev B step 6 (D9) — state not provably canonical
                logger.warning(f"[PairsBotRunner:{cfg.pair_name}] ENTRY REFUSED — Kalman state not canonical: {session.seed_refusal}")
            elif session.engine.requires_reconciliation:   # claude code changed: Stage 2D Rev B C5 — defensive; the engine already refuses
                logger.critical(f"[PairsBotRunner:{cfg.pair_name}] ENTRY REFUSED — live candle gap requires reconciliation: {session.engine.reconciliation_gaps}")
            elif not exec_engine.is_pair_tradeable(cfg.pair_name):
                logger.warning(f"[PairsBotRunner:{cfg.pair_name}] Entry signal fired but pair is disabled — not opening.")
            elif exec_engine.has_open_position(cfg.pair_name):
                logger.error(f"[PairsBotRunner:{cfg.pair_name}] Entry signal fired but a position is already tracked open — not opening a second.")
            elif refusal is not None:
                # claude code changed: new — see bot/pairs/health.py. Replaces the earlier
                # ad-hoc BETA_DRIFT_MAX_RATIO check with the full NORMAL/DEGRADED/INVALIDATED
                # policy (cointegration gate + sign reversal + drift magnitude).
                logger.warning(f"[PairsBotRunner:{cfg.pair_name}] {refusal}")
            elif not eligibility.ok:   # claude code changed: Stage 2D Rev B D-3 — canonical evidence only; legacy can never enable
                logger.warning(f"[PairsBotRunner:{cfg.pair_name}] ENTRY REFUSED — not canonically eligible: {eligibility.reason}")
            else:
                opened = exec_engine.open_pair_trade(cfg, decision)
            if opened is None:   # claude code changed: Stage 2D Rev B D-3 — no position exists, so the engine must not hold a phantom trade
                session.engine.abort_entry(decision.trade)

        elif decision.action == "EXIT":
            if exec_engine.has_open_position(cfg.pair_name):
                exec_engine.close_pair_trade(cfg, exit_reason=decision.reason,   # claude code changed: Stage 2D Rev B D-1 — durable reconciliation provenance
                                             reconciliation_affected=bool(getattr(decision.trade, "reconciliation_affected", False)),
                                             reconciliation_gaps=getattr(decision.trade, "reconciliation_gaps", "") or "")
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


def pending_migrations() -> list:   # claude code changed: Post-audit Phase 1 A5 — new
    """Unapplied Django migrations ([] when the schema is current)."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    return [f"{m.app_label}.{m.name}" for m, _ in executor.migration_plan(executor.loader.graph.leaf_nodes())]


def run_bot():
    setup_logging()
    logger = logging.getLogger(__name__)

    # claude code changed: Post-audit Phase 1 A5 — refuse to start on an
    # out-of-date schema. The journal now writes reconciliation, borrow-status,
    # fill-model, rejection and incident fields (migrations 0012/0013); a missing
    # column would make every journal write fail (logged CRITICAL, but the
    # position record would be lost). Migrations are NEVER applied automatically.
    try:
        missing = pending_migrations()
    except Exception as e:
        logger.critical(f"[PairsBotRunner] Cannot verify the database schema ({e}) — refusing to start.")
        return
    if missing:
        logger.critical(f"[PairsBotRunner] Unapplied migrations {missing} — run `python manage.py migrate` first. Refusing to start.")
        return

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
    # claude code changed: Post-audit Phase 1 provenance — every journal row records
    # the methodology and protocol it was traded under (same values SignalRejection
    # records). data_version is per-session (each pair's seed) and is not set here.
    exec_engine.trade_logger.provenance = {"methodology_version": METHODOLOGY_VERSION,
                                           "protocol_hash": PROTOCOL.config_hash()}

    reconcile_pairs(pair_configs, exec_engine)

    # claude code changed: entry-eligible pairs get a full PairSignalEngine
    # (Kalman + EntryExitEngine + KalmanPositionSizer — can trade); the
    # other, monitoring-only pairs get the lighter PairMonitorEngine
    # (Kalman beta tracking only — structurally cannot trade, see
    # bot/pairs/monitor_engine.py). PairSignalEngine's constructor would
    # raise (KalmanPositionSizer requires a real validated_win_rate) if
    # built for a pair whose config.py resolved validated_win_rate=None,
    # so this branch is required, not just tidier.
    sessions = [
        PairSession(
            pair_config=cfg,
            engine=PairSignalEngine(cfg) if cfg.is_entry_eligible else PairMonitorEngine(cfg),
        )
        for cfg in pair_configs
    ]

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
                    # claude code changed: Post-audit Phase 1 A2 — the SAME strategy-level
                    # equity and guard check that open_pair_trade enforces (was the
                    # isolated-account sum, which reads ≈ 0 once collateral is refunded).
                    capital = max((c.capital_usdt for c in pair_configs if c.is_entry_eligible), default=0.0)
                    allowed, why = exec_engine.drawdown_entry_check(capital)
                    logger.info(
                        f"[PairsBotRunner] HEARTBEAT | loop={loop_count} | "
                        f"entries_allowed_by_drawdown_guard={allowed} | {why}"
                    )
                    health_results = {}
                    for session in sessions:
                        cfg = session.pair_config
                        health = evaluate_pair_health(
                            cfg.pair_name, cfg.hedge_ratio, session.engine.current_beta,
                            cfg.passes_filters, cfg.reject_reason,
                        )
                        health_results[cfg.pair_name] = health
                        is_open = exec_engine.has_open_position(cfg.pair_name)
                        logger.info(
                            f"[PairsBotRunner]   {cfg.pair_name}: "
                            f"last_action={session.last_action} | "
                            f"open={is_open} | "
                            f"tradeable={exec_engine.is_pair_tradeable(cfg.pair_name)} | "
                            f"health={health.state.value} | "
                            f"seed_beta={health.seed_beta:.4f} | current_beta={health.current_beta:.4f} | "
                            f"drift={health.beta_drift_ratio:.0%} | sign_reversed={health.beta_sign_reversed}"
                            f"{' | ' + ', '.join(health.reason_codes) if health.reason_codes else ''}"
                        )
                        # claude code changed: new — item 11's requirement: an open
                        # position whose pair health has degraded/invalidated since
                        # entry must be clearly visible, without ever auto-closing it
                        # (that's a separate, unresearched decision — see the audit
                        # report's "remaining risks").
                        if is_open and health.state != PairHealthState.NORMAL:
                            icon = "\U0001F534" if health.state == PairHealthState.INVALIDATED else "⚠"
                            logger.warning(
                                f"[PairsBotRunner]   {icon} {cfg.pair_name} — OPEN POSITION — "
                                f"PAIR {health.state.value} — {health.summary()}"
                            )
                    write_health_snapshot(health_results)  # claude code changed: new — see bot/pairs/health.py's module docstring; the dashboard (a separate process) reads this
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
