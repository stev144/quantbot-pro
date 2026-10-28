# bot/quant_metrics/factors.py  # claude code changed: 2026-10-28 — new
"""Single-instrument factors. Inputs are COMPLETED candles only (api.py drops the open candle), oldest first; every
value at the last candle uses data up to and including that candle, never later."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from bot.quant_metrics.statistics import newey_west_se_of_mean, normal_cdf

# Window rationale (hourly candles unless stated). Each is a calendar unit, not a tuned value:
ZSCORE_WINDOW = 168          # one week: covers every hour of the weekly cycle once
EFFICIENCY_WINDOW = 24       # one day: the shortest window containing a full intraday cycle
TREND_WINDOW = 168           # one week of hourly returns for the drift test (n large enough for Newey–West)
VOL_WINDOW_DAYS = 20         # ~one trading month of daily returns (industry standard realised-vol horizon)
MOMENTUM_DAYS = 30           # one calendar month
AMIHUD_DAYS = 30             # one calendar month of daily |return| / volume


def rolling_zscore(close: pd.Series, window: int = ZSCORE_WINDOW) -> Optional[float]:
    """z of the latest LOG price against the trailing window (inclusive of the latest candle):

        z_t = (ln P_t − mean(ln P_{t−w+1..t})) / std(ln P_{t−w+1..t}),  ddof = 1

    Log price makes the measure scale-free across assets. Assumption to keep in mind: prices are not stationary, so
    z is a DISTANCE in standard-deviation units, not a probability — |z| = 2 does not mean "2.3 % likely".
    Zero dispersion or fewer than `window` candles → None."""
    x = np.log(close.astype(float).to_numpy()[-window:])
    if x.size < window or not np.all(np.isfinite(x)):
        return None
    sd = float(x.std(ddof=1))
    if sd <= 0:
        return None
    return float((x[-1] - x.mean()) / sd)


def efficiency_ratio(close: pd.Series, window: int = EFFICIENCY_WINDOW) -> Optional[float]:
    """Kaufman efficiency ratio ER = |P_t − P_{t−n}| / Σ_{i=t−n+1..t} |P_i − P_{i−1}|  ∈ [0, 1].

    0 = all movement cancelled out (noise), 1 = every step in the same direction. A flat path (denominator 0) → None."""
    p = close.astype(float).to_numpy()[-(window + 1):]
    if p.size < window + 1:
        return None
    path = float(np.abs(np.diff(p)).sum())
    if path <= 0:
        return None
    return float(abs(p[-1] - p[0]) / path)


def trend_quality(close: pd.Series, window: int = TREND_WINDOW) -> Optional[dict]:
    """Drift significance of hourly log returns, mapped to 0–100.

        t = mean(r) / SE_NW(mean(r)),   TQ = 100 · Φ(t)

    One statistic carries all four ingredients the spec asked for: the slope (mean return), its volatility adjustment
    (the SE), directional consistency (a consistent drift lowers the SE relative to the mean) and persistence (the
    Newey–West SE widens when returns are autocorrelated, so persistent noise is not mistaken for trend). No
    weights to choose. 50 = no drift; > 97.5 or < 2.5 = drift significant at 5 % (two-sided). Correlated with the
    Sharpe ratio by construction — Sharpe reports magnitude, TQ reports statistical confidence."""
    r = np.diff(np.log(close.astype(float).to_numpy()[-(window + 1):]))
    if r.size < window:
        return None
    se = newey_west_se_of_mean(r)
    if se is None:
        return None
    t = float(r.mean() / se)
    return {"t_stat": t, "score": 100.0 * normal_cdf(t), "n": int(r.size)}


def realized_vol_series(daily_close: pd.Series, periods_per_year: float, window: int = VOL_WINDOW_DAYS) -> pd.Series:
    """Annualised realised volatility of daily LOG returns (percent), rolling, causal:

        σ_t = std(r_{t−w+1..t}, ddof=1) · sqrt(A) · 100

    Computed on returns, never on price levels."""
    r = np.log(daily_close.astype(float)).diff()
    return r.rolling(window, min_periods=window).std(ddof=1) * math.sqrt(periods_per_year) * 100.0


def momentum_return(daily_close: pd.Series, days: int = MOMENTUM_DAYS) -> Optional[float]:
    """Log return over the last `days` completed days: ln(P_t / P_{t−days})."""
    c = daily_close.astype(float).to_numpy()
    if c.size < days + 1 or c[-days - 1] <= 0 or c[-1] <= 0:
        return None
    return float(math.log(c[-1] / c[-days - 1]))


def amihud_illiquidity(daily_close: pd.Series, daily_quote_volume: pd.Series, days: int = AMIHUD_DAYS) -> Optional[float]:
    """Amihud (2002) illiquidity: mean over days of |r_d| / dollar volume_d, scaled to "% move per $1M traded".

        ILLIQ = (1/D) Σ_d |r_d| / V_d · 1e6 · 100

    Higher = less liquid (price moves more per dollar). Days with zero volume are excluded; fewer than D/2 usable
    days → None. Crypto only: forex tick volume is a count of quote changes, not dollars."""
    df = pd.DataFrame({"c": daily_close.astype(float), "v": daily_quote_volume.astype(float)}).tail(days + 1)
    r = np.log(df["c"]).diff().abs().iloc[1:]
    v = df["v"].iloc[1:]
    ok = (v > 0) & r.notna()
    if ok.sum() < days // 2:
        return None
    return float((r[ok] / v[ok]).mean() * 1e6 * 100.0)


def fx_spread_cost_ratio(spread_price: float, hourly_close: pd.Series, window: int = ZSCORE_WINDOW) -> Optional[float]:
    """Forex liquidity proxy: quoted spread as a fraction of one typical hourly move, σ_1h = std of hourly log
    returns × price. Lower = cheaper to trade relative to how much the pair moves. None if inputs are unusable."""
    p = hourly_close.astype(float).to_numpy()[-(window + 1):]
    if p.size < window + 1 or spread_price is None or spread_price < 0:
        return None
    sd = float(np.diff(np.log(p)).std(ddof=1)) * p[-1]
    return None if sd <= 0 else float(spread_price / sd)
