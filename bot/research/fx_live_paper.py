# bot/research/fx_live_paper.py  # claude code changed: new — 2026-10-18; QC-1 live paper trading harness for GBP/JPY + AUD/CAD LONG
#
# Live paper trading runner: QC-1 DCA strategy, GBP/JPY + AUD/CAD LONG only.
#
# Data source  : MT5 (live terminal, must be running)
# Strategy     : FX-QC-PROTOCOL-1 — 0.5% profit activation, 0.2% trailing,
#                7-level ladder, max 72h per cycle
# Spec source  : derived from each pair's fold-5 training window (the most
#                recent historical fold), recomputed on startup
# Loop         : polls hourly (just after each hour close), re-simulates
#                from LIVE_START_DATE, detects new cycle events, logs to
#                data/live_dca/<pair>_log.csv
# Run          : python bot/research/fx_live_paper.py
#                python bot/research/fx_live_paper.py --once   (single pass, no loop)
# claude code changed: new — 2026-10-18; live paper trading harness for QC-1

import os, sys, time, json, logging, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("QUANTBOT_SECRET_KEY", "dummy-key-for-research")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import MetaTrader5 as mt5

from bot.research.fx_fold_runner import (
    _simulate_data_derived_next_open,
    run_fx_fold,
)
from bot.research.fx_fold_splitter import make_fx_splits
from bot.research.fx_multipair_runner import (
    QC1_MAX_CYCLE_CANDLES, QC1_PROFIT_ACTIVATION, QC1_TRAILING_PCT,
    QC1_MAE_HORIZON, QC1_INITIAL_USDT, QC1_PROTOCOL_ID,
)
from bot.research.forex_instrument_spec import QC_PORTFOLIO_SPECS, QC_PORTFOLIO_CSV_FILES

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

_ROOT       = Path(__file__).resolve().parents[2]
_LOG_DIR    = _ROOT / "data" / "live_dca"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Configuration ────────────────────────────────────────────────────────────
PAIRS = ["GBP/JPY", "AUD/CAD"]

# MT5 symbol map (broker uses no slash)
MT5_SYMBOLS = {"GBP/JPY": "GBPJPY", "AUD/CAD": "AUDCAD"}

# How far back to start the live window (overridden by --days CLI arg)
LIVE_WINDOW_DAYS = 14

# How many historical bars to load as warmup before the live window
# (needed so any cycle that was already open at LIVE_START_DATE is tracked)
WARMUP_BARS = 200


# ── MT5 helpers ──────────────────────────────────────────────────────────────

def _mt5_fetch(symbol_mt5: str, n_bars: int) -> pd.DataFrame:
    """Fetch last n_bars 1h bars from MT5, return as DataFrame matching CSV format."""
    rates = mt5.copy_rates_from(symbol_mt5, mt5.TIMEFRAME_H1,
                                 datetime.now(timezone.utc), n_bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"MT5 returned no data for {symbol_mt5}")
    df = pd.DataFrame(rates)
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.rename(columns={"tick_volume": "volume"})
    return df[["timestamp", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def _load_historical(pair: str) -> pd.DataFrame:
    """Load the historical CSV for a pair (used to derive the fold-5 spec)."""
    csv_file = QC_PORTFOLIO_CSV_FILES[pair]
    return pd.read_csv(_ROOT / "data" / "forex" / csv_file, parse_dates=["timestamp"])


# ── Spec derivation ──────────────────────────────────────────────────────────

def _derive_spec(pair: str):
    """Run fold-5 train+test window to get the ladder spec for live use."""
    df        = _load_historical(pair)
    splits    = make_fx_splits(df)
    fold5     = splits.folds[-1]       # most recent fold
    pair_spec = QC_PORTFOLIO_SPECS[pair]
    result    = run_fx_fold(
        train_df=fold5.train,
        test_df=fold5.test,
        pair=pair,
        direction="LONG",
        pip_size=pair_spec.pip_size,
        pair_spec=pair_spec,
        fold_num=5,
        initial_usdt=QC1_INITIAL_USDT,
        profit_activation_pct=QC1_PROFIT_ACTIVATION,
        trailing_pct=QC1_TRAILING_PCT,
        max_cycle_candles=QC1_MAX_CYCLE_CANDLES,
        mae_horizon=QC1_MAE_HORIZON,
    )
    return result["spec"]


# ── Simulation on live bars ───────────────────────────────────────────────────

def _run_live_sim(pair: str, spec, live_df: pd.DataFrame) -> tuple[list, list]:
    """
    Simulate DCA on live_df using the pre-computed spec.
    Returns (closed_trades, open_trades).
    """
    pair_spec   = QC_PORTFOLIO_SPECS[pair]
    spread_pips = pair_spec.spread_typical_pips or 2.0   # default 2 pips if unknown
    commission  = pair_spec.commission_per_lot  or 0.0
    swap_long   = pair_spec.swap_long           or 0.0
    swap_short  = pair_spec.swap_short          or 0.0

    # simulation expects DatetimeIndex
    sim_df = live_df.copy()
    if "timestamp" in sim_df.columns:
        sim_df["timestamp"] = pd.to_datetime(sim_df["timestamp"], utc=True)
        sim_df = sim_df.set_index("timestamp")

    trades, _ = _simulate_data_derived_next_open(
        sim_df, spec,
        initial_usdt=QC1_INITIAL_USDT,
        profit_activation_pct=QC1_PROFIT_ACTIVATION,
        trailing_pct=QC1_TRAILING_PCT,
        spread_pips=spread_pips,
        commission_per_lot=commission,
        swap_long_pips=swap_long,
        swap_short_pips=swap_short,
        max_cycle_candles=QC1_MAX_CYCLE_CANDLES,
    )

    closed = [t for t in trades if t.get("exit_reason") != "DATA_END_STILL_OPEN"]
    open_  = [t for t in trades if t.get("exit_reason") == "DATA_END_STILL_OPEN"]
    return closed, open_


# ── Logging ──────────────────────────────────────────────────────────────────

def _append_log(pair: str, trade: dict, status: str):
    log_file = _LOG_DIR / f"{pair.replace('/', '')}_log.csv"
    row = {
        "logged_at":   datetime.now(timezone.utc).isoformat(),
        "status":      status,
        "entry_time":  trade.get("entry_time", ""),
        "exit_time":   trade.get("exit_time", ""),
        "entry_price": trade.get("entry_price", ""),
        "exit_price":  trade.get("exit_price", ""),
        "fills":       trade.get("fills", ""),
        "total_cost":  trade.get("total_cost_usdt", ""),
        "profit_usdt": trade.get("profit_usdt", ""),
        "exit_reason": trade.get("exit_reason", ""),
        "candles_held":trade.get("candles_held", ""),
    }
    df_row = pd.DataFrame([row])
    if log_file.exists():
        df_row.to_csv(log_file, mode="a", header=False, index=False)
    else:
        df_row.to_csv(log_file, index=False)


# ── One pass ─────────────────────────────────────────────────────────────────

def _state_file(pair: str) -> Path:
    return _LOG_DIR / f"{pair.replace('/', '')}_state.json"


def _detect_latest_bar() -> datetime:
    """Return the timestamp of the most recent bar MT5 has for GBPJPY."""
    rates = mt5.copy_rates_from(MT5_SYMBOLS["GBP/JPY"], mt5.TIMEFRAME_H1,
                                 datetime.now(timezone.utc), 1)
    if rates is None or len(rates) == 0:
        raise RuntimeError("MT5 returned no bars — is the terminal running?")
    ts = pd.to_datetime(rates[-1]["time"], unit="s", utc=True).to_pydatetime()
    return ts


def run_one_pass(specs: dict, live_start: datetime, verbose: bool = True) -> dict:
    """
    Fetch latest bars, simulate, detect new events, log them.
    Returns summary dict {pair: {"closed": N, "open": bool, ...}}.
    """
    summary = {}

    for pair in PAIRS:
        sym = MT5_SYMBOLS[pair]

        # Fetch: warmup bars + enough to cover from live_start
        latest_bar = _detect_latest_bar()
        hours_since_start = max(WARMUP_BARS, int((latest_bar - live_start).total_seconds() / 3600) + WARMUP_BARS)
        raw_df = _mt5_fetch(sym, hours_since_start)

        # Trim to warmup window before live start + all bars after
        cutoff = live_start - timedelta(hours=WARMUP_BARS)
        live_df = raw_df[raw_df["timestamp"] >= cutoff].reset_index(drop=True)

        if live_df.empty:
            summary[pair] = {"error": "no bars fetched"}
            continue

        spec = specs[pair]
        closed, open_ = _run_live_sim(pair, spec, live_df)

        # Only count cycles that entered on or after live_start
        live_closed = [t for t in closed
                       if pd.Timestamp(t["entry_time"]) >= live_start]
        live_open   = [t for t in open_
                       if pd.Timestamp(t["entry_time"]) >= live_start]

        # Load previous state
        sf = _state_file(pair)
        prev_state = json.loads(sf.read_text()) if sf.exists() else {"known_entries": []}
        known = set(prev_state.get("known_entries", []))

        # Detect new closed trades
        for t in live_closed:
            key = str(t["entry_time"])
            if key not in known:
                _append_log(pair, t, "CLOSED")
                known.add(key)

        # Detect newly opened cycle
        for t in live_open:
            key = str(t["entry_time"])
            if key not in known:
                _append_log(pair, t, "OPENED")
                known.add(key)

        # Save updated state
        sf.write_text(json.dumps({"known_entries": list(known)}))

        total_pnl = sum(t["profit_usdt"] for t in live_closed)
        wins      = sum(1 for t in live_closed if t["profit_usdt"] > 0)
        win_pct   = wins / len(live_closed) * 100 if live_closed else 0.0

        current_cycle = live_open[0] if live_open else None
        summary[pair] = {
            "closed":       len(live_closed),
            "wins":         wins,
            "win_pct":      win_pct,
            "total_pnl":    total_pnl,
            "open_cycle":   current_cycle,
            "latest_bar":   str(live_df["timestamp"].iloc[-1]),
        }

    return summary


def _print_dashboard(summary: dict, specs: dict, live_start: datetime, latest_bar: datetime):
    now = datetime.now(timezone.utc)
    stale_hours = (now - latest_bar).total_seconds() / 3600
    stale_warn  = f"  *** DATA STALE: latest bar {latest_bar.strftime('%Y-%m-%d %H:%M')} UTC ({stale_hours:.0f}h ago) ***" if stale_hours > 4 else ""
    print(f"\n{'='*65}")
    print(f"  QC-1 LIVE PAPER TRADING  |  {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Protocol: {QC1_PROTOCOL_ID}  |  LONG only")
    print(f"  Live since: {live_start.strftime('%Y-%m-%d')}  |  Latest bar: {latest_bar.strftime('%Y-%m-%d %H:%M')} UTC")
    if stale_warn:
        print(stale_warn)
    print(f"{'='*65}")

    for pair, s in summary.items():
        print(f"\n  {pair}")
        if "error" in s:
            print(f"    ERROR: {s['error']}")
            continue
        print(f"    Latest bar : {s['latest_bar']}")
        print(f"    Closed     : {s['closed']} trades  |  win%={s['win_pct']:.0f}%  |  P&L={s['total_pnl']:+.4f} USD")
        cyc = s["open_cycle"]
        if cyc:
            entry_t   = pd.Timestamp(cyc["entry_time"]).strftime("%Y-%m-%d %H:%M")
            cost      = cyc.get("total_cost_usdt", QC1_INITIAL_USDT)
            fills     = cyc.get("fills", 1)
            entry_p   = cyc.get("entry_price", 0)
            print(f"    OPEN CYCLE : entered {entry_t}  |  fills={fills}  |  deployed=${cost:.0f}  |  entry={entry_p:.4f}")
        else:
            print(f"    No open cycle — waiting for next entry")

    print(f"\n  Logs: {_LOG_DIR}")
    print(f"{'='*65}\n")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="QC-1 live paper trading runner")
    parser.add_argument("--once", action="store_true", help="Run one pass then exit (no loop)")
    parser.add_argument("--days", type=int, default=LIVE_WINDOW_DAYS,
                        help=f"How many days back to start the live window (default {LIVE_WINDOW_DAYS})")
    args = parser.parse_args()

    print(f"\n{'='*65}")
    print(f"  QC-1 LIVE PAPER RUNNER starting up...")
    print(f"  Pairs: {PAIRS}  |  Direction: LONG")
    print(f"  Data: MT5 terminal  |  Logs: {_LOG_DIR}")
    print(f"{'='*65}")

    # Connect MT5
    if not mt5.initialize():
        print("ERROR: MT5 terminal not running or not connected. Start MT5 first.")
        sys.exit(1)
    print("  MT5 connected OK")

    # Derive specs from historical fold-5 training data
    print("  Computing DCA specs from fold-5 training data...")
    specs = {}
    for pair in PAIRS:
        specs[pair] = _derive_spec(pair)
        print(f"    {pair}: spec computed (atr_ref={specs[pair].atr_ref:.5f}, {len(specs[pair].base_pips)} DCA levels)")

    # Determine live start: latest MT5 bar minus --days
    latest_bar = _detect_latest_bar()
    live_start = latest_bar - timedelta(days=args.days)
    stale_h = (datetime.now(timezone.utc) - latest_bar).total_seconds() / 3600
    print(f"  Latest MT5 bar : {latest_bar.strftime('%Y-%m-%d %H:%M')} UTC")
    if stale_h > 4:
        print(f"  *** WARNING: data is {stale_h:.0f}h stale — MT5 feed may be offline ***")
        print(f"      Reconnect the broker in MT5 terminal to get live prices.")
    print(f"  Live window    : {live_start.strftime('%Y-%m-%d')} → now ({args.days} days)")
    print(f"  Ready.\n")

    try:
        while True:
            latest_bar = _detect_latest_bar()
            summary    = run_one_pass(specs, live_start=live_start, verbose=True)
            _print_dashboard(summary, specs, live_start, latest_bar)

            if args.once:
                break

            # Sleep until 2 minutes after the next hour close
            now     = datetime.now(timezone.utc)
            next_h  = now.replace(minute=2, second=0, microsecond=0) + timedelta(hours=1)
            wait_s  = (next_h - now).total_seconds()
            print(f"  Next check at {next_h.strftime('%H:%M UTC')} (in {wait_s/60:.0f} min)")
            time.sleep(wait_s)

    finally:
        mt5.shutdown()
        print("  MT5 disconnected.")


if __name__ == "__main__":
    main()
