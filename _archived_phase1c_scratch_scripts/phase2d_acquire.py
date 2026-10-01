"""
Phase 2D — chunked, resumable acquisition for a properly-powered
incremental-information re-test of Phase 2C's finding.

DECIDED BEFORE ANY DOWNLOAD (Phase 2D Step 2 — do not extend after
seeing results):
  Symbols:    BTC/USDT, ETH/USDT (primary). A third symbol only added
              after these two are fully acquired and if time permits.
  Timeframe:  1h
  Period:     180 days (6 months) ending yesterday (most recent
              published archive day) — Phase 2D's stated primary target
              (the 12-month figure is an explicit stretch goal, not the
              committed target).
  Target:     forward_return_4h (primary, unchanged from Phase 2C) plus
              forward_return_1h / forward_return_12h for the Step 11
              horizon check (feature_calculator.py already computes all
              three unchanged).
  Candidates: EXACTLY Phase 2C's family, no additions —
              trade flow: delta, cvd_slope_24h, buy_sell_ratio,
                          trade_intensity, avg_trade_size
              derivatives: funding_rate, funding_rate_zscore,
                          oi_change_1h, oi_change_24h, oi_price_interaction

ENGINEERING DESIGN (why this isn't Phase 2C's approach just scaled up):
Phase 2C held the full raw-trade history in memory as one DataFrame.
At 180 days x ~650k-900k trades/day x 2 symbols, that's on the order of
2-300 million rows — infeasible to hold simultaneously. This script
processes ONE UTC DAY at a time: downloads (or loads from a resumable
per-day disk cache) that day's raw trades, computes that day's
per-candle trade-flow columns via the EXISTING, unchanged
TradeFlowEngine.compute_features() (buy_volume/sell_volume/delta/
trade_intensity/avg_trade_size/buy_sell_ratio — each depends ONLY on
trades within that single candle's own window, so per-day chunking
cannot change these values; proven against the real engine in
bot/tests/test_trade_flow_engine.py's new ChunkedProcessingSafetyTest),
then DISCARDS that day's raw trades from memory (kept on disk for
resumability/coverage auditing). cvd / cvd_slope_24h are NOT taken from
any single day's chunk (each chunk's own "cvd" restarts at 0 — see the
same test's negative proof) — they are recomputed ONCE on the full
concatenated, continuous delta series after every day has been
processed, which is mathematically identical to a single non-chunked
call (also proven in that test).
"""
import sys
import time
import json
import warnings
from pathlib import Path

sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import ccxt

from bot.engines.trade_data import fetch_agg_trades_from_archive
from bot.engines.derivatives_data import fetch_funding_rate_history, fetch_open_interest_history
from bot.research.trade_flow_engine import TradeFlowEngine

CACHE_DIR = Path("phase2d_cache")
CACHE_DIR.mkdir(exist_ok=True)

N_DAYS = 180   # claude code changed: decided before any download — Phase 2D Step 2
SYMBOLS = ["BTC/USDT", "ETH/USDT"]


def acquire_ohlcv(symbol: str, since_ms: int, n_candles: int) -> pd.DataFrame:
    """claude code changed: paginated — a single ccxt call caps at 1000
    candles; 180 days = 4320 candles needs 5 calls. Small/fast, direct
    REST (no archive needed for OHLCV — the volume here is trivial)."""
    ex = ccxt.binance({"timeout": 20000, "enableRateLimit": True})
    all_rows = []
    cursor = since_ms
    while len(all_rows) < n_candles:
        batch = ex.fetch_ohlcv(symbol, timeframe="1h", since=cursor, limit=1000)
        if not batch:
            break
        all_rows.extend(batch)
        last_ts = batch[-1][0]
        if last_ts <= cursor:
            break
        cursor = last_ts + 3600_000
        if len(batch) < 1000:
            break
    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.drop_duplicates(subset=["timestamp"], inplace=True)
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def process_symbol(symbol: str, start_date: str, end_date: str) -> tuple:
    print(f"\n{'='*70}\nACQUIRING (CHUNKED, RESUMABLE): {symbol}\n{'='*70}", flush=True)
    safe = symbol.replace("/", "_")

    since_ms = int(pd.Timestamp(start_date, tz="UTC").timestamp() * 1000)
    n_candles_target = N_DAYS * 24 + 24
    ohlcv = acquire_ohlcv(symbol, since_ms, n_candles_target)
    print(f"  OHLCV: {len(ohlcv)} candles, {ohlcv['timestamp'].min()} to {ohlcv['timestamp'].max()}", flush=True)
    candle_index = pd.DatetimeIndex(ohlcv["timestamp"])

    funding_df = fetch_funding_rate_history(symbol, since_ms=since_ms)
    oi_df = fetch_open_interest_history(symbol, days=30)   # claude code changed: Binance's real, documented 30-day hard cap — honestly reported as a coverage gap in the final report, not silently worked around
    print(f"  Funding: {len(funding_df)} obs (full window) | OI: {len(oi_df)} obs (Binance's 30-day cap, NOT the full {N_DAYS}-day window)", flush=True)

    tfe = TradeFlowEngine(timeframe="1h")
    dates = pd.date_range(start_date, end_date, freq="D").strftime("%Y-%m-%d").tolist()

    daily_chunks = []
    coverage_rows = []
    t_start = time.time()

    for i, date in enumerate(dates, 1):
        cache_path = CACHE_DIR / f"{safe}_{date}.pkl"
        if cache_path.exists():
            trades_df = pd.read_pickle(cache_path)
            cached = True
        else:
            trades_df = fetch_agg_trades_from_archive(symbol, date)
            trades_df.to_pickle(cache_path)   # claude code changed: resumable — a re-run skips every already-downloaded day
            cached = False

        n_trades = len(trades_df)
        coverage_rows.append({"date": date, "n_trades": n_trades, "cached": cached, "available": n_trades > 0})

        if n_trades == 0:
            continue

        day_start = pd.Timestamp(date, tz="UTC")
        day_end = day_start + pd.Timedelta(days=1)
        day_candle_index = candle_index[(candle_index >= day_start) & (candle_index < day_end)]
        if len(day_candle_index) == 0:
            del trades_df
            continue

        day_features = tfe.compute_features(trades_df, day_candle_index)
        daily_chunks.append(day_features[["buy_volume", "sell_volume", "delta", "trade_intensity", "avg_trade_size", "buy_sell_ratio"]])

        del trades_df   # claude code changed: THE memory-bound step — never accumulate raw trades across days

        if i % 20 == 0 or i == len(dates):
            elapsed = time.time() - t_start
            print(f"  [{i}/{len(dates)}] {date} — {n_trades:,} trades ({'cached' if cached else 'downloaded'}) — {elapsed:.0f}s elapsed", flush=True)

    coverage_df = pd.DataFrame(coverage_rows)
    total_trades = int(coverage_df["n_trades"].sum())
    n_missing_days = int((~coverage_df["available"]).sum())
    print(f"  TOTAL: {total_trades:,} real trades across {len(dates)} days ({n_missing_days} missing/empty days)", flush=True)

    if not daily_chunks:
        raise RuntimeError(f"{symbol}: zero days produced any trade data — acquisition failed")

    combined = pd.concat(daily_chunks).sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]
    combined = combined.reindex(candle_index)   # claude code changed: any candle with zero trades (or a missing/failed day) becomes an honest NaN row, not silently dropped

    # ── Recompute cvd/cvd_slope_24h on the FULL continuous series ──────
    # claude code changed: THE fix proven necessary in
    # ChunkedProcessingSafetyTest — never take "cvd" from a per-day chunk.
    combined["cvd"] = combined["delta"].cumsum()
    combined["cvd_slope_24h"] = combined["cvd"].diff(24)

    combined["symbol"] = symbol
    combined["timestamp"] = candle_index

    coverage_df.to_pickle(CACHE_DIR / f"{safe}_coverage.pkl")
    ohlcv.to_pickle(CACHE_DIR / f"{safe}_ohlcv.pkl")
    funding_df.to_pickle(CACHE_DIR / f"{safe}_funding.pkl")
    oi_df.to_pickle(CACHE_DIR / f"{safe}_oi.pkl")
    combined.to_pickle(CACHE_DIR / f"{safe}_flow_features.pkl")

    return ohlcv, funding_df, oi_df, combined, coverage_df


if __name__ == "__main__":
    manifest_path = CACHE_DIR / "manifest.json"
    if manifest_path.exists():
        # claude code changed: load the already-decided window instead of
        # recomputing it from "now" on every resume — recomputing shifted
        # the window forward by however many days had passed since the
        # first run, discarding the "decided before any download" window
        # and chasing a new, not-yet-stable last day (a today-minus-1
        # archive can still be slow/incomplete to serve) each time the
        # script was re-run after a transient failure.
        with open(manifest_path) as f:
            manifest = json.load(f)
        start_date, end_date = manifest["start_date"], manifest["end_date"]
    else:
        now = pd.Timestamp.utcnow().floor("h")
        end_date = (now - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = (now - pd.Timedelta(days=N_DAYS)).strftime("%Y-%m-%d")

        manifest = {
            "symbols": SYMBOLS, "timeframe": "1h", "start_date": start_date, "end_date": end_date,
            "n_days_target": N_DAYS, "decided_before_download": True,
        }
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
    print(f"MANIFEST: {manifest}", flush=True)

    for symbol in SYMBOLS:
        process_symbol(symbol, start_date, end_date)

    print("\nACQUISITION COMPLETE FOR ALL SYMBOLS.", flush=True)
