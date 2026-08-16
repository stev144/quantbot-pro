# claude code changed: new file
# ============================================================
# bot/backtesting/regime_precomputer.py
#
# BACKTEST-ONLY regime precomputation.
#
# WHY THIS EXISTS
# ----------------
# bot/backtesting/backtester.py's main loop used to call
# RegimeDetector.detect(current_df) once per candle. Every call
# recomputes ADX, ATR ratio, EMA spread, and Bollinger Band width
# from scratch via pandas .rolling()/.ewm() on that candle's window.
# Profiling a real backtest showed this alone was ~65% of total
# runtime — not because the math is expensive, but because pandas
# pays real per-call overhead (Series construction, dtype checks,
# __finalize__) and detect() was being called ~2,000+ times.
#
# RegimeDetector is shared with the LIVE bot (bot/core/bot_runner.py
# calls the exact same detect() method), so it is deliberately left
# untouched here — this module does not change what RegimeDetector
# computes or how the live bot behaves.
#
# WHAT THIS MODULE DOES INSTEAD
# ------------------------------
# Every indicator RegimeDetector.detect() computes (ADX, ATR ratio,
# EMA spread, BB width, EMA direction) is causal — it only ever looks
# backward from the current row, never forward. That means computing
# each one ONCE, vectorized, over the whole backtest DataFrame gives
# EXACTLY the same value at row i as calling RegimeDetector.detect()
# on df.iloc[:i+1] fresh at every candle — not an approximation, the
# same pandas rolling()/ewm() math, just evaluated once instead of
# once per candle. The four indicator formulas below are copied
# verbatim from RegimeDetector's private methods (_calculate_adx,
# _calculate_atr_ratio, _calculate_ema_spread, _calculate_bb_width,
# _get_ema_direction) for exactly this reason — any drift between
# this file and RegimeDetector's math would silently make backtests
# disagree with the live bot, so the formulas must be copied, not
# "improved on," every time RegimeDetector's math changes.
#
# Verified (see PR/session notes) to produce byte-for-byte identical
# RegimeResult objects to calling RegimeDetector.detect() per-candle
# on real historical data.
# ============================================================

import logging
from typing import List

import numpy as np
import pandas as pd

from bot.engines.regime_detector import RegimeDetector, RegimeResult

logger = logging.getLogger(__name__)


def precompute_regime_results(df: pd.DataFrame, detector: RegimeDetector) -> List[RegimeResult]:
    """
    Vectorized, one-shot replacement for calling detector.detect(df.iloc[:i+1])
    once per candle.

    Parameters
    ----------
    df : pd.DataFrame
        Already-cleaned OHLCV data (same cleaning _prepare_data() already
        applies: numeric coercion + dropna on close/high/low). Must be the
        FULL backtest DataFrame, not a windowed slice — these are causal
        indicators, so computing them on the full series and reading row i
        is identical to computing them on df.iloc[:i+1] and reading the
        last row.

    detector : RegimeDetector
        The same instance the backtest would otherwise call .detect() on.
        Its configured periods/thresholds (adx_period, ema_fast, ema_slow,
        etc.) are read directly so this never drifts from whatever the
        detector was actually configured with.

    Returns
    -------
    List[RegimeResult]
        One RegimeResult per row of df, same order, same index alignment.
        results[i] is exactly what detector.detect(df.iloc[:i+1]) would
        have returned.
    """

    n = len(df)
    if n == 0:
        return []

    high, low, close = df["high"], df["low"], df["close"]

    # ── ADX (copied verbatim from RegimeDetector._calculate_adx) ──────────
    period = detector.adx_period
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0),
        index=df.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0),
        index=df.index,
    )

    atr_smooth = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_dm_smooth = plus_dm.ewm(alpha=1 / period, adjust=False).mean()
    minus_dm_smooth = minus_dm.ewm(alpha=1 / period, adjust=False).mean()

    plus_di = 100 * (plus_dm_smooth / (atr_smooth + 1e-9))
    minus_di = 100 * (minus_dm_smooth / (atr_smooth + 1e-9))
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))

    adx_raw = dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0.0)

    # ── ATR ratio (copied verbatim from RegimeDetector._calculate_atr_ratio) ──
    # tr is identical to the one computed for ADX above — RegimeDetector
    # recomputes it separately per-call; we reuse it here since it's the
    # same formula, applied to the same df.
    current_atr = tr.rolling(detector.atr_period).mean()
    avg_atr = tr.rolling(detector.atr_avg_period).mean()
    atr_ratio_raw = (current_atr / avg_atr)
    invalid_avg = avg_atr.isna() | (avg_atr == 0)
    atr_ratio_raw = atr_ratio_raw.where(~invalid_avg, 1.0).fillna(1.0)

    # ── EMA spread (copied verbatim from RegimeDetector._calculate_ema_spread) ──
    # NOTE: the original rounds to 4 decimals INSIDE the function, and that
    # already-rounded value (not the raw one) is what gets compared against
    # ema_spread_trend_threshold — replicated here, not just for storage.
    ema_fast = close.ewm(span=detector.ema_fast, adjust=False).mean()
    ema_slow = close.ewm(span=detector.ema_slow, adjust=False).mean()
    safe_close = close.replace(0, np.nan)
    ema_spread_pct = ((ema_fast - ema_slow).abs() / safe_close * 100).round(4).fillna(0.0)

    # ── BB width (copied verbatim from RegimeDetector._calculate_bb_width) ──
    # Same note as above: rounded to 4 decimals before being used in the
    # bb_ranging classification, not just for storage.
    bb_middle = close.rolling(detector.bb_period).mean()
    bb_std = close.rolling(detector.bb_period).std()
    bb_upper = bb_middle + detector.bb_std * bb_std
    bb_lower = bb_middle - detector.bb_std * bb_std
    safe_middle = bb_middle.replace(0, np.nan)
    bb_width_pct = ((bb_upper - bb_lower) / safe_middle * 100).round(4).fillna(0.0)

    # ── EMA direction (copied verbatim from RegimeDetector._get_ema_direction) ──
    ema_direction = np.where(
        ema_fast.values > ema_slow.values, "UP",
        np.where(ema_fast.values < ema_slow.values, "DOWN", "NEUTRAL"),
    )

    # ── Classification (copied verbatim from RegimeDetector.detect()) ────────
    # adx_trending/volatility_extreme use the RAW (unrounded) values, exactly
    # like the original — only ema_trending/bb_ranging use pre-rounded values,
    # because that's what _calculate_ema_spread/_calculate_bb_width return.
    adx_values = adx_raw.values
    atr_ratio_values = atr_ratio_raw.values
    ema_spread_values = ema_spread_pct.values
    bb_width_values = bb_width_pct.values

    adx_trending = adx_values >= detector.adx_trend_threshold
    ema_trending = ema_spread_values >= detector.ema_spread_threshold
    bb_ranging = bb_width_values <= detector.bb_narrow_threshold
    volatility_extreme = atr_ratio_values >= detector.atr_extreme_multiplier

    trending_signals = adx_trending.astype(int) + ema_trending.astype(int) + (~bb_ranging).astype(int)
    ranging_signals = (~adx_trending).astype(int) + (~ema_trending).astype(int) + bb_ranging.astype(int)

    regime = np.full(n, "RANGING", dtype=object)
    confidence = np.full(n, "LOW", dtype=object)

    # Priority order matches the original if/elif/else exactly:
    # HIGH_VOLATILITY overrides everything, then trending, then ranging,
    # then mixed -> RANGING/LOW (already the array defaults above).
    vol_mask = volatility_extreme
    regime[vol_mask] = "HIGH_VOLATILITY"
    confidence[vol_mask] = "HIGH"

    trend_mask = (~vol_mask) & (trending_signals >= 2)
    up_mask = trend_mask & (ema_direction == "UP")
    down_mask = trend_mask & (ema_direction == "DOWN")
    neutral_trend_mask = trend_mask & ~up_mask & ~down_mask   # EMAs equal while "trending" -> RANGING, same as original
    regime[up_mask] = "TRENDING_UP"
    regime[down_mask] = "TRENDING_DOWN"
    regime[neutral_trend_mask] = "RANGING"
    confidence[trend_mask] = np.where(trending_signals[trend_mask] == 3, "HIGH", "MEDIUM")

    rest_mask = (~vol_mask) & (~trend_mask)
    ranging_mask = rest_mask & (ranging_signals >= 2)
    regime[ranging_mask] = "RANGING"
    confidence[ranging_mask] = np.where(ranging_signals[ranging_mask] == 3, "HIGH", "MEDIUM")
    # mixed_mask = rest_mask & ~ranging_mask -> stays at array defaults ("RANGING", "LOW")

    adx_rounded = np.round(adx_values, 2)
    atr_ratio_rounded = np.round(atr_ratio_values, 2)

    results: List[RegimeResult] = [None] * n
    for i in range(n):
        summary = (
            f"{regime[i]} | {confidence[i]} confidence | "
            f"ADX: {adx_values[i]:.1f} | "
            f"ATR ratio: {atr_ratio_values[i]:.2f} | "
            f"EMA spread: {ema_spread_values[i]:.2f}% | "
            f"BB width: {bb_width_values[i]:.2f}%"
        )
        results[i] = RegimeResult(
            regime=str(regime[i]),
            confidence=str(confidence[i]),
            adx=float(adx_rounded[i]),
            atr_ratio=float(atr_ratio_rounded[i]),
            ema_spread_pct=float(ema_spread_values[i]),
            bb_width_pct=float(bb_width_values[i]),
            adx_trending=bool(adx_trending[i]),
            volatility_extreme=bool(volatility_extreme[i]),
            summary=summary,
        )

    logger.info(
        f"[regime_precomputer] Precomputed {n:,} regime results in one vectorized pass "
        f"(replaces {n:,} individual RegimeDetector.detect() calls)"
    )

    return results
