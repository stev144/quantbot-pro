# bot/market_intel/asset_metrics.py  # claude code changed: 2026-10-27 — new; home page risk & cost context per asset
"""Four descriptive numbers per asset for the home page's relative-value cards. None of them is a signal.

  volatility   annualised realised volatility of 1 h log returns over the last 7 days, plus RangeAnalytics' own
               volatility ratio (mean daily range of the last 7 days / last 30 days) and its COMPRESSED / NORMAL /
               EXPANDING state, so the thresholds stay the engine's (bot/engines/range_analytics.py)
  half-life    exact AR(1) half-life of the 1 h log price over the last 7 days (bot/quant_core/half_life.py), shown ONLY
               when a Dickey-Fuller test on the same hourly pairs rejects a random walk at 5% (MacKinnon p-value). A
               7-day AR(1) fit on a pure random walk gives phi ~ 0.97 by small-sample bias, i.e. a fake ~20 h
               half-life, so an ungated number would mislead. Across ~30 assets about 1 in 20 passes by chance
  RVOL         mean hourly volume of the last 24 candles / mean hourly volume of the 29 days before them
               (crypto: USDT quote volume; forex: MT5 tick volume, the only volume a decentralised market has)
  spread       live best bid/ask, in basis points of the mid and as a % of one typical day's move (1 h σ × √24)
"""
import math

import numpy as np
import pandas as pd

from bot.engines.range_analytics import RangeAnalytics
from bot.quant_core.half_life import ar1_half_life
from bot.quant_core.time_grid import lag_pairs

BARS_PER_YEAR = {"crypto": 24 * 365, "forex": 24 * 260}   # crypto trades every hour; forex ~24 h × 260 weekdays
WINDOW_BARS = 168              # 7 days of 1 h candles: volatility and half-life window
RVOL_RECENT_BARS = 24
RVOL_BASE_BARS = 29 * 24
HALF_LIFE_MIN_OBS = 100        # quant_core requires the caller to declare it; 100 of the 167 possible pairs
DF_ALPHA = 0.05                # Dickey-Fuller significance level for showing a half-life
RATIO_RECENT_DAYS, RATIO_BASE_DAYS = 7, 30   # RangeAnalytics: recent ADR = last 7 days, ADR = lookback_days (30)
CANDLES_NEEDED = 31 * 24       # 30 complete days for the ratio, plus the current partial day


def realized_vol_pct(close: pd.Series, bars_per_year: int, window: int = WINDOW_BARS):
    r = np.log(close.astype(float)).diff().dropna().tail(window)
    if len(r) < window // 2:
        return None
    return float(r.std(ddof=1) * math.sqrt(bars_per_year) * 100)


def volatility_ratio(df: pd.DataFrame):
    """(ratio, state) with RangeAnalytics' definition and thresholds, from hourly candles; (None, None) when fewer than
    30 complete days exist. Forex weekend days have no candles and are simply absent."""
    daily = df[["high", "low"]].astype(float).resample("1D").agg({"high": "max", "low": "min"}).dropna()
    daily = daily.iloc[:-1]                                   # the current day is incomplete
    rng = (daily["high"] - daily["low"]).tail(RATIO_BASE_DAYS)
    if len(rng) < RATIO_BASE_DAYS or rng.mean() <= 0:
        return None, None
    ratio = float(rng.tail(RATIO_RECENT_DAYS).mean() / rng.mean())
    return ratio, RangeAnalytics()._volatility_state(ratio)


def half_life(close: pd.Series) -> dict:
    """{"text", "title", "hours"} for display. Rows missing from the hourly grid (forex weekends) become gaps, so the
    regression never pairs Friday's close with Monday's open."""
    s = np.log(close.astype(float)).tail(WINDOW_BARS)
    if len(s) < 2:
        return {"text": "n/a", "title": "too few candles", "hours": None}
    grid = s.reindex(pd.date_range(s.index[0], s.index[-1], freq="h"))
    p_df = dickey_fuller_p(grid)
    if p_df is None:
        return {"text": "n/a", "title": f"fewer than {HALF_LIFE_MIN_OBS} adjacent hourly pairs", "hours": None}
    if p_df >= DF_ALPHA:
        return {"text": "none", "hours": None,
                "title": f"no evidence of mean reversion over the last 7 days (Dickey-Fuller p = {p_df:.2f})"}
    res = ar1_half_life(grid, candle_hours=1.0, min_obs=HALF_LIFE_MIN_OBS)
    if not res.ok:
        return {"text": "n/a", "title": res.reason, "hours": None}
    h = res.value.half_life_hours
    title = (f"AR(1) phi = {res.value.phi:.4f} on {res.value.n_obs} hourly pairs; Dickey-Fuller p = {p_df:.3f}; "
             f"descriptive, not a signal")
    if h > WINDOW_BARS:
        return {"text": "> 7 d", "title": title + "; longer than the 7-day window, so not reliable", "hours": h}
    return {"text": f"{h:.1f} h" if h < 48 else f"{h / 24:.1f} d", "title": title, "hours": h}


def dickey_fuller_p(series: pd.Series):
    """MacKinnon p-value of the Dickey-Fuller t-statistic (constant, no lags) from the adjacent-candle pairs that the
    AR(1) half-life uses; None when there are fewer than HALF_LIFE_MIN_OBS pairs."""
    from statsmodels.tsa.adfvalues import mackinnonp
    pairs = lag_pairs(series)
    n = len(pairs)
    if n < HALF_LIFE_MIN_OBS:
        return None
    x_lag = pairs["x_lag"].to_numpy()
    dx = pairs["x"].to_numpy() - x_lag
    X = np.column_stack([np.ones(n), x_lag])
    coef, *_ = np.linalg.lstsq(X, dx, rcond=None)
    resid = dx - X @ coef
    s2 = resid @ resid / (n - 2)
    se = math.sqrt(s2 * np.linalg.inv(X.T @ X)[1, 1])
    if not se > 0:
        return None
    return float(mackinnonp(coef[1] / se, regression="c", N=1))


def relative_volume(volume: pd.Series):
    v = volume.astype(float).dropna()
    if len(v) < RVOL_RECENT_BARS + RVOL_BASE_BARS // 2:
        return None
    base = v.iloc[:-RVOL_RECENT_BARS].tail(RVOL_BASE_BARS).mean()
    return float(v.tail(RVOL_RECENT_BARS).mean() / base) if base > 0 else None


def spread(bid, ask, vol_pct, bars_per_year: int) -> dict:
    """Spread in basis points of the mid, and as a % of one typical day's move. {} when no valid quote."""
    try:
        bid, ask = float(bid), float(ask)
    except (TypeError, ValueError):
        return {}
    if not (bid > 0 and ask >= bid):
        return {}
    frac = (ask - bid) / ((ask + bid) / 2)
    out = {"spread_bps": frac * 1e4}
    if vol_pct:
        day_sigma = vol_pct / 100 / math.sqrt(bars_per_year) * math.sqrt(24)
        out["spread_pct_of_day"] = frac / day_sigma * 100 if day_sigma > 0 else None
    return out


def asset_metrics(df: pd.DataFrame, asset_class: str, volume_col: str) -> dict:
    """Everything but the spread (which needs the live quote) from hourly candles indexed by UTC open time."""
    bpy = BARS_PER_YEAR[asset_class]
    ratio, state = volatility_ratio(df)
    return {
        "vol_7d_pct": realized_vol_pct(df["close"], bpy),
        "vol_ratio": ratio,
        "vol_state": state,
        "half_life": half_life(df["close"]),
        "rvol": relative_volume(df[volume_col]) if volume_col and volume_col in df else None,
        "bars_per_year": bpy,
        "last_candle": df.index[-1].isoformat() if len(df) else None,
    }
