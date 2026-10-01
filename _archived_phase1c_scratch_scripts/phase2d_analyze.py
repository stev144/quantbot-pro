"""
Phase 2D — analysis step.

# claude code changed: new file — per user request, stop waiting on
# ETH/USDT's remaining raw-aggTrades day downloads (network to
# data.binance.vision has been intermittently flaky) and run the
# re-test now on whatever is actually available: BTC/USDT's complete
# 180-day window (already fully cached, incl. combined flow_features/
# ohlcv/funding/oi from phase2d_acquire.py) plus ETH/USDT's partial
# window (113 of 180 days of raw trades cached — enough for a real,
# honestly-smaller-N test, not a simulated one).

Reuses, UNCHANGED, the exact three engines + methodology Phase 2C
already established (see git commit 374246f's message and
bot/research/feature_validator.py's apply_family_wide_correction()):
  - bot/research/feature_calculator.py  (FeatureCalculator)      -> realized_vol/adx/close + forward_return_1h/4h/24h
  - bot/research/derivatives_engine.py  (DerivativesEngine)      -> funding_*/oi_* candidates + forward_return_1h/2h/4h/12h
  - bot/research/trade_flow_engine.py   (TradeFlowEngine)        -> delta/cvd_slope_24h/buy_sell_ratio/trade_intensity/avg_trade_size
  - bot/research/feature_validator.py   (FeatureValidator)       -> per-symbol raw IC + block-permutation, then
                                                                     apply_family_wide_correction() pools BTC+ETH and
                                                                     applies ONE FDR correction across the real 10-feature family.

No new statistical methodology invented here — same discipline Phase 2C
used, just re-run at Phase 2D's (much larger, honestly-reported-partial-
for-ETH) sample size.
"""
import sys
import json
import warnings
from pathlib import Path

sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

import django
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

import numpy as np
import pandas as pd
import ccxt

from bot.engines.derivatives_data import fetch_funding_rate_history, fetch_open_interest_history
from bot.research.trade_flow_engine import TradeFlowEngine
from bot.research.feature_calculator import FeatureCalculator
from bot.research.derivatives_engine import DerivativesEngine
from bot.research.feature_validator import FeatureValidator, apply_family_wide_correction

CACHE_DIR = Path("phase2d_cache")
TRADE_FLOW_COLS = ["buy_volume", "sell_volume", "delta", "trade_intensity", "avg_trade_size", "buy_sell_ratio"]
HORIZONS = ["forward_return_1h", "forward_return_4h", "forward_return_12h"]


def _with_retry(fn, *args, max_attempts=4, **kwargs):
    # claude code changed: new — local retry wrapper for the ccxt/derivatives
    # calls in THIS script only. Network to Binance's endpoints has been
    # intermittently slow/flaky in this environment (repeated RequestTimeout
    # on both spot klines and dapi exchangeInfo during phase2d_acquire.py's
    # last several runs) — matches the existing bounded-retry convention
    # used elsewhere in this codebase (trade_data.py's _with_retry), just
    # applied here since this script's own calls aren't already wrapped.
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < max_attempts:
                print(f"  transient error (attempt {attempt}/{max_attempts}): {e}", flush=True)
                import time
                time.sleep(2.0 * attempt)
    raise last_err


def _acquire_ohlcv(symbol: str, since_ms: int, n_candles: int) -> pd.DataFrame:
    ex = ccxt.binance({"timeout": 30000, "enableRateLimit": True})
    all_rows = []
    cursor = since_ms
    while len(all_rows) < n_candles:
        batch = _with_retry(ex.fetch_ohlcv, symbol, timeframe="1h", since=cursor, limit=1000)
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


def _load_or_fetch_btc(manifest: dict):
    safe = "BTC_USDT"
    ohlcv = pd.read_pickle(CACHE_DIR / f"{safe}_ohlcv.pkl")
    funding_df = pd.read_pickle(CACHE_DIR / f"{safe}_funding.pkl")
    oi_df = pd.read_pickle(CACHE_DIR / f"{safe}_oi.pkl")
    flow = pd.read_pickle(CACHE_DIR / f"{safe}_flow_features.pkl")
    print(f"BTC/USDT: loaded from cache — {len(ohlcv)} OHLCV candles, "
          f"{flow['delta'].notna().sum()}/{len(flow)} candles with real trade-flow data.", flush=True)
    return ohlcv, funding_df, oi_df, flow


def _load_partial_eth(manifest: dict):
    """
    claude code changed: new — ETH/USDT equivalent of process_symbol() in
    phase2d_acquire.py, EXCEPT: never downloads a new raw-aggTrades day
    file. Uses only the per-day trade caches that already exist on disk
    (113/180 days as of this run) — every day beyond that becomes an
    honest NaN row via the same reindex-to-full-candle-index step
    phase2d_acquire.py already used for missing/failed days, not a
    silently shortened window.
    """
    symbol = "ETH/USDT"
    safe = "ETH_USDT"
    start_date, end_date = manifest["start_date"], manifest["end_date"]

    since_ms = int(pd.Timestamp(start_date, tz="UTC").timestamp() * 1000)
    n_candles_target = manifest["n_days_target"] * 24 + 24
    ohlcv = _with_retry(_acquire_ohlcv, symbol, since_ms, n_candles_target)
    print(f"ETH/USDT: OHLCV {len(ohlcv)} candles, {ohlcv['timestamp'].min()} to {ohlcv['timestamp'].max()}", flush=True)
    candle_index = pd.DatetimeIndex(ohlcv["timestamp"])

    funding_df = _with_retry(fetch_funding_rate_history, symbol, since_ms=since_ms)
    oi_df = _with_retry(fetch_open_interest_history, symbol, days=30)
    print(f"ETH/USDT: funding {len(funding_df)} obs, OI {len(oi_df)} obs", flush=True)

    tfe = TradeFlowEngine(timeframe="1h")
    dates = pd.date_range(start_date, end_date, freq="D").strftime("%Y-%m-%d").tolist()

    daily_chunks = []
    n_days_available = 0
    for date in dates:
        cache_path = CACHE_DIR / f"{safe}_{date}.pkl"
        if not cache_path.exists():
            continue   # claude code changed: stop point — no new downloads, just skip uncached days
        trades_df = pd.read_pickle(cache_path)
        if len(trades_df) == 0:
            continue
        day_start = pd.Timestamp(date, tz="UTC")
        day_end = day_start + pd.Timedelta(days=1)
        day_candle_index = candle_index[(candle_index >= day_start) & (candle_index < day_end)]
        if len(day_candle_index) == 0:
            continue
        day_features = tfe.compute_features(trades_df, day_candle_index)
        daily_chunks.append(day_features[TRADE_FLOW_COLS])
        n_days_available += 1
        del trades_df

    print(f"ETH/USDT: trade-flow computed from {n_days_available}/{len(dates)} cached days "
          f"({n_days_available/len(dates)*100:.0f}% coverage).", flush=True)

    combined = pd.concat(daily_chunks).sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]
    combined = combined.reindex(candle_index)

    # claude code changed: same fix proven necessary in
    # ChunkedProcessingSafetyTest (bot/tests/test_trade_flow_engine.py) —
    # cvd/cvd_slope_24h recomputed on the full continuous delta series,
    # never taken from a per-day chunk. NaN gaps (uncached days) simply
    # propagate through cumsum/diff as NaN for the candles that depend on
    # them, which is the honest behavior here.
    combined["cvd"] = combined["delta"].cumsum()
    combined["cvd_slope_24h"] = combined["cvd"].diff(24)
    combined["symbol"] = symbol
    combined["timestamp"] = candle_index

    return ohlcv, funding_df, oi_df, combined


def _build_observations(symbol: str, ohlcv: pd.DataFrame, funding_df, oi_df, flow: pd.DataFrame) -> pd.DataFrame:
    """
    claude code changed: new — assembles one symbol's full observation
    frame by reusing FeatureCalculator (realized_vol/adx/forward_return_
    1h/4h/24h) and DerivativesEngine (forward_return_2h/12h + funding_*/
    oi_* candidates) UNCHANGED, then merges in the trade-flow candidate
    columns (delta/cvd_slope_24h/buy_sell_ratio/trade_intensity/
    avg_trade_size) by timestamp. Exactly Phase 2C's Model D ("all
    combined"), just built once per symbol instead of as separate
    Model A/B/C/D variants — feature_validator.py tests every candidate
    column independently regardless, so there is only one family here,
    not four separate ones to keep in sync.
    """
    fc = FeatureCalculator(min_data_required=100)
    base = fc.calculate_all_features(ohlcv.copy(), symbol=symbol, timeframe="1h")

    de = DerivativesEngine()
    deriv = de.calculate_all(ohlcv.copy(), funding_df, oi_df)
    deriv_extra_cols = [
        "timestamp", "forward_return_2h", "forward_return_12h",
        "funding_rate", "funding_rate_change", "funding_rate_cumulative_24h", "funding_rate_zscore",
        "open_interest_amount", "oi_change_1h", "oi_change_24h", "oi_price_interaction",
    ]
    base["timestamp"] = pd.to_datetime(base["timestamp"], utc=True)
    deriv["timestamp"] = pd.to_datetime(deriv["timestamp"], utc=True)
    merged = base.merge(deriv[deriv_extra_cols], on="timestamp", how="left")

    # claude code changed: flow's own index is the same DatetimeIndex as
    # its 'timestamp' column (a side effect of phase2d_acquire.py's
    # combined.reindex(candle_index) step, whose index inherits the
    # 'timestamp' name from ohlcv['timestamp']) — merge() raises on that
    # index/column name collision, so drop the redundant index first.
    flow = flow.reset_index(drop=True).copy()
    flow["timestamp"] = pd.to_datetime(flow["timestamp"], utc=True)
    merged = merged.merge(flow[["timestamp"] + TRADE_FLOW_COLS + ["cvd", "cvd_slope_24h"]],
                           on="timestamp", how="left")

    merged["symbol"] = symbol
    return merged


def main():
    manifest = json.loads((CACHE_DIR / "manifest.json").read_text())
    print(f"MANIFEST: {manifest}", flush=True)

    btc_ohlcv, btc_funding, btc_oi, btc_flow = _load_or_fetch_btc(manifest)
    eth_ohlcv, eth_funding, eth_oi, eth_flow = _load_partial_eth(manifest)

    obs_btc = _build_observations("BTC/USDT", btc_ohlcv, btc_funding, btc_oi, btc_flow)
    obs_eth = _build_observations("ETH/USDT", eth_ohlcv, eth_funding, eth_oi, eth_flow)

    obs_btc.to_pickle(CACHE_DIR / "BTC_USDT_observations.pkl")
    obs_eth.to_pickle(CACHE_DIR / "ETH_USDT_observations.pkl")

    print(f"\nBTC/USDT observations: {len(obs_btc)} rows", flush=True)
    print(f"ETH/USDT observations: {len(obs_eth)} rows "
          f"({obs_eth['delta'].notna().sum()} with real trade-flow data)", flush=True)

    # ── Per-horizon: raw per-symbol validation, then ONE family-wide
    # FDR correction pooling BTC+ETH — identical scope discipline to
    # apply_family_wide_correction()'s own docstring (Phase B remediation):
    # one hypothesis family = every (symbol, feature) pair tested this run,
    # corrected together, not per-symbol.
    all_pooled = {}
    for horizon in HORIZONS:
        print(f"\n{'='*100}\nHORIZON: {horizon}\n{'='*100}", flush=True)
        validator_btc = FeatureValidator(min_observations=30, alpha=0.05)
        raw_btc = validator_btc.validate_all_features_raw(obs_btc.copy(), horizon)

        validator_eth = FeatureValidator(min_observations=30, alpha=0.05)
        raw_eth = validator_eth.validate_all_features_raw(obs_eth.copy(), horizon)

        per_symbol, pooled = apply_family_wide_correction(
            {"BTC/USDT": raw_btc, "ETH/USDT": raw_eth}, alpha=0.05
        )
        pooled["horizon"] = horizon
        all_pooled[horizon] = pooled

        trade_flow_rows = pooled[pooled["feature"].isin(
            ["delta", "cvd_slope_24h", "buy_sell_ratio", "trade_intensity", "avg_trade_size"]
        )]
        print(f"\n--- TRADE-FLOW FEATURES @ {horizon} (FDR-corrected across full 10-feature/2-symbol family) ---")
        print(trade_flow_rows[["symbol", "feature", "ic_overall", "ic_rank",
                                "p_value_block_permutation", "p_fdr", "passes_fdr", "recommendation"]]
              .to_string(index=False))

    full_pooled = pd.concat(all_pooled.values(), ignore_index=True)
    full_pooled.to_csv(CACHE_DIR / "phase2d_validation_results.csv", index=False)
    print(f"\nSaved full results to {CACHE_DIR / 'phase2d_validation_results.csv'}", flush=True)


if __name__ == "__main__":
    main()
