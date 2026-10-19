"""
bot/fetch_fx_intraday.py
Download 5-minute and 15-minute OHLCV data for the four v7 FX protocol pairs
from the locally running MetaTrader 5 terminal.

Pairs: EUR/USD, GBP/USD, USD/CHF, AUD/USD  (USD/JPY dropped per v7/A6-2)
Timeframes: 5m, 15m
Output: data/forex/{SYMBOL}_5m.csv, data/forex/{SYMBOL}_15m.csv

Rows after the v7 holdout boundary (2026-10-16 08:00 UTC) are stripped by
seal_before_write() — same D-8 protection used by the 1h fetcher.

Requires:
  MT5_ENABLED=true
  MT5_LOGIN / MT5_PASSWORD / MT5_SERVER  (or MT5 terminal already running
  and logged in — pass --attach to use the open terminal without credentials)

Usage:
  python bot/fetch_fx_intraday.py            # use env-var credentials
  python bot/fetch_fx_intraday.py --attach   # attach to running terminal
  python bot/fetch_fx_intraday.py --dry-run  # print what would be fetched, no writes

Note on gap detection: detect_gaps() is borrowed from forex_data_fetcher.py and
is calibrated for 1h bars (it checks Fri-23:00 → Mon-00:00 as a weekend signal).
For 5m/15m the weekend-gap heuristic will be less precise in its reporting, but
the data itself is saved and timestamped correctly — this affects only the printed
summary, not the saved file.
"""
# claude code changed: new file — v8 intraday FX research protocol; fetch 5m/15m for EUR/USD, GBP/USD, USD/CHF, AUD/USD

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QUANTBOT_SECRET_KEY", "dummy-key-for-offline-script")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django; django.setup()

from bot.mt5_data_fetcher import (
    MT5_CLOCK_LABEL,
    MT5ConnectionError,
    _attach_running_terminal,
    _import_mt5,
    _mt5_timeframe,
    _mt5_credentials,
    _to_mt5_symbol,
    connect,
    mt5_server_time_to_utc,
)
from bot.forex_data_fetcher import detect_gaps
from bot.research.fx_holdout_guard import V7_HOLDOUT_START  # claude code changed: D-8 fix — use v7 FX boundary, not crypto zone F
from bot.instruments import write_provenance_marker

logger = logging.getLogger(__name__)

# ── Protocol pairs (v7: USD/JPY dropped per A6-2) ──────────────────────────────

PROTOCOL_PAIRS = ["EUR/USD", "GBP/USD", "USD/CHF", "AUD/USD"]
TIMEFRAMES = ["5m", "15m"]
OUTPUT_DIR = Path("data/forex")

# claude code changed: v8 intraday — broker history limits confirmed empirically 2026-10-18:
# 5m: max ~180 days before copy_rates_range returns Invalid params
# 15m: max ~730 days (2 years, ~49k bars per pair) — sufficient for walk-forward folds
HISTORY_DAYS_BY_INTERVAL = {
    "5m":  180,   # broker limit: ~180d; sufficient for feasibility, not full walk-forward
    "15m": 730,   # broker limit: ~730d; ~2 years, usable for multi-fold walk-forward
}


def _intraday_filename(symbol: str, interval: str) -> str:
    """EUR/USD + 5m  ->  EUR_USD_5m.csv"""  # claude code changed: new — interval-parameterised filename
    return symbol.replace("/", "_") + f"_{interval}.csv"


def fetch_intraday(attach_only: bool = False, dry_run: bool = False) -> dict:  # claude code changed: new
    """
    Fetch 5m and 15m data for all PROTOCOL_PAIRS.
    Returns a summary dict: {symbol: {interval: {success, candles, file, error}}}.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=timezone.utc)

    print("\n" + "=" * 72)
    print("INTRADAY FX DATA FETCH — v8 protocol (5m and 15m)")
    print(f"Pairs     : {', '.join(PROTOCOL_PAIRS)}")
    print(f"Timeframes: {', '.join(TIMEFRAMES)}")
    for tf, days in HISTORY_DAYS_BY_INTERVAL.items():
        print(f"History   : {tf} → {days} days (broker limit)")
    print(f"Mode      : {'DRY RUN — no files written' if dry_run else 'LIVE — writing to data/forex/'}")
    print("=" * 72)

    if dry_run:
        for symbol in PROTOCOL_PAIRS:
            for interval in TIMEFRAMES:
                fname = _intraday_filename(symbol, interval)
                days = HISTORY_DAYS_BY_INTERVAL[interval]
                est_bars = int(days * 24 * (60 // int(interval.rstrip("m"))))
                print(f"  [DRY RUN] {symbol} {interval} → data/forex/{fname}  (~{est_bars:,} bars)")
        return {}

    mt5 = _attach_running_terminal() if attach_only else connect()
    summary: dict = {}

    try:
        for symbol in PROTOCOL_PAIRS:
            summary[symbol] = {}
            mt5_symbol = _to_mt5_symbol(symbol)

            if not mt5.symbol_select(mt5_symbol, True):
                err = f"mt5.symbol_select({mt5_symbol!r}) failed: {mt5.last_error()}"
                print(f"\n[{symbol}] SKIP — {err}")
                for interval in TIMEFRAMES:
                    summary[symbol][interval] = {"success": False, "candles": 0, "file": None, "error": err}
                continue

            for interval in TIMEFRAMES:
                print(f"\n[{symbol} {interval}]")
                try:
                    timeframe = _mt5_timeframe(mt5, interval)
                    days = HISTORY_DAYS_BY_INTERVAL[interval]  # claude code changed: per-interval broker limit
                    date_from = now - timedelta(days=days)
                    rates = mt5.copy_rates_range(mt5_symbol, timeframe, date_from, now)

                    if rates is None or len(rates) == 0:
                        raise MT5ConnectionError(f"no data returned: {mt5.last_error()}")

                    df = pd.DataFrame(rates)
                    df["timestamp"] = mt5_server_time_to_utc(df["time"]).to_numpy()
                    df = df.rename(columns={"tick_volume": "volume"})[
                        ["timestamp", "open", "high", "low", "close", "volume"]
                    ]
                    for col in ["open", "high", "low", "close", "volume"]:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                    df.dropna(subset=["open", "high", "low", "close"], inplace=True)
                    df.drop_duplicates(subset=["timestamp"], inplace=True)
                    df.sort_values("timestamp", inplace=True)
                    df.reset_index(drop=True, inplace=True)

                    if df.empty:
                        raise MT5ConnectionError("no usable rows after cleaning")

                    gaps = detect_gaps(df)
                    other_gaps = [g for g in gaps if not g["likely_weekend"]]

                    filepath = OUTPUT_DIR / _intraday_filename(symbol, interval)

                    # claude code changed: D-8 fix — FX intraday uses v7 FX holdout boundary
                    # (2026-10-16 08:00 UTC), NOT the crypto zone F boundary (2026-10-04).
                    # seal_before_write() was stripping 12 extra days of valid research data.
                    ts_col = pd.to_datetime(df["timestamp"], utc=True)
                    n_sealed = int((ts_col >= V7_HOLDOUT_START).sum())
                    if n_sealed:
                        logger.warning(f"v7 FX holdout seal: {n_sealed} row(s) at/after "
                                       f"{V7_HOLDOUT_START.isoformat()} stripped from {filepath.name}")
                    df = df[ts_col < V7_HOLDOUT_START].reset_index(drop=True)

                    df.to_csv(filepath, index=False)

                    write_provenance_marker(
                        filepath, venue="mt5", data_source="fetch_fx_intraday",
                        source="mt5_terminal_ipc", symbol=symbol, timeframe=interval,
                        start_date=str(df["timestamp"].iloc[0].date()),
                        end_date=str(df["timestamp"].iloc[-1].date()),
                        row_count=len(df), clock=MT5_CLOCK_LABEL,
                    )

                    last_ts = df["timestamp"].iloc[-1]
                    staleness = (now - last_ts).total_seconds() / 86400
                    print(f"  Candles   : {len(df):,}")
                    print(f"  Range     : {df['timestamp'].iloc[0]} → {last_ts}")
                    print(f"  Staleness : {staleness:.1f} days")
                    if other_gaps:
                        print(f"  ! Non-weekend gaps: {len(other_gaps)}")
                    print(f"  Saved     : {filepath}")

                    summary[symbol][interval] = {
                        "success": True, "candles": len(df), "file": str(filepath),
                        "error": None, "staleness_days": round(staleness, 2),
                    }

                except (MT5ConnectionError, Exception) as e:
                    print(f"  ERROR: {e}")
                    summary[symbol][interval] = {"success": False, "candles": 0, "file": None, "error": str(e)}

    finally:
        mt5.shutdown()

    # ── Summary ─────────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("FETCH SUMMARY")
    total_ok = 0
    for symbol in PROTOCOL_PAIRS:
        for interval in TIMEFRAMES:
            r = summary.get(symbol, {}).get(interval, {})
            status = "OK" if r.get("success") else "FAIL"
            candles = f"{r.get('candles', 0):,}" if r.get("success") else r.get("error", "")
            print(f"  {symbol:8s} {interval:4s}  {status:4s}  {candles}")
            if r.get("success"):
                total_ok += 1
    print(f"\n  {total_ok}/{len(PROTOCOL_PAIRS) * len(TIMEFRAMES)} fetches succeeded")
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(name)s | %(message)s")

    parser = argparse.ArgumentParser(description="Fetch 5m/15m FX data from MT5 for v8 intraday protocol")
    parser.add_argument("--attach", action="store_true",
                        help="Attach to already-running MT5 terminal (no credential env vars needed)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be fetched without writing any files")
    args = parser.parse_args()

    fetch_intraday(attach_only=args.attach, dry_run=args.dry_run)
