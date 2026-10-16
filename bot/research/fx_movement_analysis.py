"""
bot/research/fx_movement_analysis.py
Pair-specific FX price-movement analysis for DCA ladder calibration.

Computes DESCRIPTIVE STATISTICS ONLY on calibration data.
No edge claims. No ladder threshold selection here — thresholds are derived
from these statistics in a subsequent step after owner review.

Protocol: FX-DCA-PROTOCOL-1-A2 (FX_DCA_PROTOCOL_2026-10-16_v3.md)
Pre-specified horizons: 6, 12, 24, 48, 96, 168 trading candles
Pre-specified statistics: mean, median, p40, p60, p75, p85, p92, p97, p99, n
"""
# claude code changed: new file — 2026-10-16; Phase 3 pair-specific movement analysis, calibration data only

from __future__ import annotations

import numpy as np
import pandas as pd

# Pre-specified per FX-DCA-PROTOCOL-1-A2 — do not change after examining results
HORIZONS = [6, 12, 24, 48, 96, 168]  # claude code changed: fixed before any results seen
QUANTILES = [0.40, 0.60, 0.75, 0.85, 0.92, 0.97, 0.99]  # claude code changed: fixed before any results seen
QUANTILE_LABELS = ["p40", "p60", "p75", "p85", "p92", "p97", "p99"]  # claude code changed: matches QUANTILES
ATR_WINDOW = 24  # claude code changed: per protocol Part 3 Step 1
MAE_HORIZON = 24  # claude code changed: per protocol Part 3 Step 2
MAX_PURGE = max(HORIZONS)  # claude code changed: 168 — exclude last MAX_PURGE rows from label estimation


def _quantile_stats(arr: np.ndarray) -> dict:
    """Compute pre-specified summary statistics for a 1-D array."""  # claude code changed: new helper
    if len(arr) == 0:
        return {k: float("nan") for k in ["mean", "median"] + QUANTILE_LABELS + ["n"]}
    qs = np.quantile(arr, QUANTILES)
    return {
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        **{label: float(q) for label, q in zip(QUANTILE_LABELS, qs)},
        "n": int(len(arr)),
    }


def compute_atr_ref(df: pd.DataFrame, window: int = ATR_WINDOW) -> float:
    """
    Training-period median ATR (24-candle rolling) excluding the last MAX_PURGE rows.

    ATR per candle = max(H-L, |H-prev_C|, |L-prev_C|)
    Rolling ATR = mean of the previous `window` ATR values (causal).
    ATR_ref = median of rolling ATR over all training rows except the last MAX_PURGE.

    Returns NaN if insufficient rows.
    """  # claude code changed: new — per FX-DCA-PROTOCOL-1-A2 Part 3 Step 1
    if len(df) < window + MAX_PURGE + 2:
        return float("nan")
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    atr_raw = np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low - prev_close),
    ])
    # Rolling mean of atr_raw using a causal window
    atr_series = pd.Series(atr_raw).rolling(window, min_periods=window).mean().values
    # Exclude last MAX_PURGE rows and NaN head
    valid = atr_series[window - 1: len(atr_series) - MAX_PURGE]
    if len(valid) == 0:
        return float("nan")
    return float(np.median(valid))  # claude code changed: median per protocol


def _compute_mae_series(
    df: pd.DataFrame,
    direction: str,
    horizon: int,
    pip_size: float,
) -> dict:
    """
    Compute MAE for all eligible candles at a given horizon and direction.

    Eligible candles: all rows except the last `max(horizon, MAX_PURGE)` rows
    (avoids partial forward windows AND avoids contamination at fold boundary).

    Returns dict with pct stats and pip stats.
    """  # claude code changed: new — FX-DCA-PROTOCOL-1-A2 Part 3 Step 2
    purge = max(horizon, MAX_PURGE)
    n_usable = len(df) - purge
    if n_usable <= 0:
        empty: dict = _quantile_stats(np.array([]))
        return {"pct": empty, "pips": empty}

    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)

    mae_pct = np.empty(n_usable)
    for i in range(n_usable):
        entry = close[i]
        if entry <= 0:
            mae_pct[i] = float("nan")
            continue
        if direction == "LONG":
            # Worst (lowest) low in [i+1 .. i+horizon]
            worst = float(np.min(low[i + 1: i + 1 + horizon]))
            mae_pct[i] = max(0.0, (entry - worst) / entry)
        else:  # SHORT
            # Worst (highest) high in [i+1 .. i+horizon]
            worst = float(np.max(high[i + 1: i + 1 + horizon]))
            mae_pct[i] = max(0.0, (worst - entry) / entry)

    # Drop NaN (bad prices)
    valid_pct = mae_pct[~np.isnan(mae_pct)]
    # Convert to pips: mae_pct * entry_close / pip_size
    entry_closes = close[:n_usable]
    valid_entries = entry_closes[~np.isnan(mae_pct)]
    valid_pips = valid_pct * valid_entries / pip_size

    return {
        "pct": _quantile_stats(valid_pct),
        "pips": _quantile_stats(valid_pips),
    }


def _compute_mfe_series(
    df: pd.DataFrame,
    direction: str,
    horizon: int,
    pip_size: float,
) -> dict:
    """
    Compute MFE (Maximum Favourable Excursion) for all eligible candles.

    LONG MFE = max(0, max(high[i+1..i+H]) - entry) / entry
    SHORT MFE = max(0, entry - min(low[i+1..i+H])) / entry
    """  # claude code changed: new — paired with _compute_mae_series
    purge = max(horizon, MAX_PURGE)
    n_usable = len(df) - purge
    if n_usable <= 0:
        empty = _quantile_stats(np.array([]))
        return {"pct": empty, "pips": empty}

    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)

    mfe_pct = np.empty(n_usable)
    for i in range(n_usable):
        entry = close[i]
        if entry <= 0:
            mfe_pct[i] = float("nan")
            continue
        if direction == "LONG":
            best = float(np.max(high[i + 1: i + 1 + horizon]))
            mfe_pct[i] = max(0.0, (best - entry) / entry)
        else:
            best = float(np.min(low[i + 1: i + 1 + horizon]))
            mfe_pct[i] = max(0.0, (entry - best) / entry)

    valid_pct = mfe_pct[~np.isnan(mfe_pct)]
    entry_closes = close[:n_usable]
    valid_entries = entry_closes[~np.isnan(mfe_pct)]
    valid_pips = valid_pct * valid_entries / pip_size

    return {
        "pct": _quantile_stats(valid_pct),
        "pips": _quantile_stats(valid_pips),
    }


def analyse_returns(df: pd.DataFrame, pip_size: float) -> dict:
    """
    1h log-return statistics (positive and negative separately).

    Skips pairs where a gap between timestamps > 1 hour exists (returns NaN for
    that gap's return — weekend/holiday closures produce no return for that slot).
    """  # claude code changed: new — candle-level return stats, both pct and pips
    close = df["close"].values.astype(float)
    if len(close) < 2:
        return {}
    log_ret = np.diff(np.log(close))
    # Exclude weekend/session-gap returns: gaps > 1.5h
    ts = pd.to_datetime(df["timestamp"].values)
    gaps_h = np.diff(ts).astype("timedelta64[s]").astype(float) / 3600
    valid_mask = gaps_h <= 1.5
    log_ret = log_ret[valid_mask]
    pct_ret = (np.exp(log_ret) - 1.0)

    pos = pct_ret[pct_ret > 0]
    neg = np.abs(pct_ret[pct_ret < 0])  # absolute value for consistent interpretation

    # Convert to pips using approximate mid price (mean of two adjacent closes)
    mid = (close[:-1] + close[1:]) / 2
    mid_valid = mid[valid_mask]
    pos_pips = pos * mid_valid[pct_ret > 0] / pip_size
    neg_pips = neg * mid_valid[pct_ret < 0] / pip_size

    return {
        "positive": {"pct": _quantile_stats(pos), "pips": _quantile_stats(pos_pips)},
        "negative": {"pct": _quantile_stats(neg), "pips": _quantile_stats(neg_pips)},
        "all": {"pct": _quantile_stats(np.abs(pct_ret)), "pips": _quantile_stats(np.abs(pct_ret) * mid_valid / pip_size)},
    }


def analyse_pair(
    df: pd.DataFrame,
    pip_size: float,
    pair: str = "",
) -> dict:
    """
    Full movement analysis for one pair on CALIBRATION DATA ONLY.

    Returns nested dict:
      {
        "atr_ref": float,
        "returns": {...},
        "LONG": {horizon: {"mae": {...}, "mfe": {...}}},
        "SHORT": {horizon: {"mae": {...}, "mfe": {...}}},
      }

    The caller is responsible for passing only calibration rows.
    """  # claude code changed: new — top-level per-pair analysis
    atr_ref = compute_atr_ref(df, window=ATR_WINDOW)
    returns = analyse_returns(df, pip_size)

    long_results: dict = {}
    short_results: dict = {}

    for h in HORIZONS:
        long_results[h] = {
            "mae": _compute_mae_series(df, "LONG", h, pip_size),
            "mfe": _compute_mfe_series(df, "LONG", h, pip_size),
        }
        short_results[h] = {
            "mae": _compute_mae_series(df, "SHORT", h, pip_size),
            "mfe": _compute_mfe_series(df, "SHORT", h, pip_size),
        }

    return {
        "pair": pair,
        "n_calibration_rows": len(df),
        "atr_ref": atr_ref,
        "returns": returns,
        "LONG": long_results,
        "SHORT": short_results,
    }


def run_all_pairs(
    calibration_dfs: dict,
    pip_sizes: dict,
) -> dict:
    """
    Run analysis for all pairs in calibration_dfs.

    Parameters
    ----------
    calibration_dfs : dict  {pair_name: pd.DataFrame}  — calibration rows only
    pip_sizes       : dict  {pair_name: float}

    Returns
    -------
    dict  {pair_name: analysis_result}
    """  # claude code changed: new — multi-pair entry point
    results = {}
    for pair, df in calibration_dfs.items():
        ps = pip_sizes.get(pair, 0.0001)
        results[pair] = analyse_pair(df, ps, pair=pair)
    return results


def print_movement_report(results: dict) -> None:  # claude code changed: new — human-readable output
    """Print the full pair-specific movement report."""
    print("\n" + "=" * 78)  # claude code changed: ASCII only — Windows cp1252 compat
    print("FX PAIR-SPECIFIC MOVEMENT REPORT - FX-DCA-PROTOCOL-1-A2")  # claude code changed: ASCII
    print("DESCRIPTIVE STATISTICS ONLY - calibration data - no edge claims")  # claude code changed: ASCII
    print("=" * 78)

    for pair, res in results.items():
        print(f"\n{'-'*78}")
        print(f"  {pair}   (n={res['n_calibration_rows']:,} calibration candles,  "
              f"ATR_ref={res['atr_ref']:.6f})")
        print(f"{'-'*78}")

        # 1h return stats
        ret = res.get("returns", {})
        if ret:
            print("\n  -- 1h log-return statistics (gap-filtered) --")  # claude code changed: ASCII
            print(f"  {'Direction':<14} {'Unit':<6} {'mean':>10} {'median':>10} "
                  f"{'p75':>10} {'p92':>10} {'p97':>10} {'p99':>10} {'n':>8}")
            for lbl, key in [("Positive", "positive"), ("Negative(abs)", "negative")]:
                for unit, ukey in [("%", "pct"), ("pips", "pips")]:
                    s = ret.get(key, {}).get(ukey, {})
                    if not s or s.get("n", 0) == 0:
                        continue
                    mult = 100.0 if unit == "%" else 1.0
                    print(f"  {lbl:<14} {unit:<6} "
                          f"{s['mean']*mult:>10.4f} {s['median']*mult:>10.4f} "
                          f"{s['p75']*mult:>10.4f} "
                          f"{s['p92']*mult:>10.4f} "
                          f"{s['p97']*mult:>10.4f} "
                          f"{s['p99']*mult:>10.4f} "
                          f"{s['n']:>8,}")

        # MAE/MFE by horizon
        for direction in ["LONG", "SHORT"]:
            dir_res = res.get(direction, {})
            print(f"\n  -- {direction} MAE / MFE by horizon --")  # claude code changed: ASCII
            print(f"  {'H':>4} {'Type':<4} {'Unit':<6} {'mean':>10} {'median':>10} "
                  f"{'p40':>8} {'p60':>8} {'p75':>8} {'p85':>8} {'p92':>8} {'p97':>8} "
                  f"{'p99':>8} {'n':>7}")
            for h in HORIZONS:
                h_res = dir_res.get(h, {})
                for exc_type in ["mae", "mfe"]:
                    for unit, ukey in [("%", "pct"), ("pips", "pips")]:
                        s = h_res.get(exc_type, {}).get(ukey, {})
                        if not s or s.get("n", 0) == 0:
                            continue
                        mult = 100.0 if unit == "%" else 1.0
                        print(
                            f"  {h:>4} {exc_type.upper():<4} {unit:<6} "
                            f"{s['mean']*mult:>10.4f} {s['median']*mult:>10.4f} "
                            f"{s['p40']*mult:>8.4f} {s['p60']*mult:>8.4f} "
                            f"{s['p75']*mult:>8.4f} {s['p85']*mult:>8.4f} "
                            f"{s['p92']*mult:>8.4f} {s['p97']*mult:>8.4f} "
                            f"{s['p99']*mult:>8.4f} {s['n']:>7,}"
                        )

    print("\n" + "=" * 78)
    print("END OF REPORT - no ladder thresholds derived here")  # claude code changed: ASCII
    print("=" * 78 + "\n")
