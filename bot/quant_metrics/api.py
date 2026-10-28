# bot/quant_metrics/api.py  # claude code changed: 2026-10-28 — new
"""Entry points for pages and research: per-symbol metrics, then universe-level ranks.

Inputs per symbol: hourly and daily candles (DatetimeIndex in UTC, columns open/high/low/close, optional `qav` =
quote volume). The still-open candle at the end is DROPPED first, so no value depends on an unfinished bar."""
from __future__ import annotations

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel

from bot.quant_metrics import factors as F
from bot.quant_metrics import regimes as R
from bot.quant_metrics.ranking import cross_sectional_percentile, time_series_percentile
from bot.quant_metrics.statistics import lo_mackinlay_variance_ratio, sharpe_ratio

logger = logging.getLogger(__name__)

PERIODS_PER_YEAR_DAILY = {"crypto": 365.0, "forex": 260.0}   # crypto trades every day; forex ~260 weekdays
VR_Q = 24                      # variance ratio of 1-day vs 1-hour returns (hourly data)
VOL_HISTORY_DAYS = 365         # own-history window for the volatility percentile
VOL_MIN_HISTORY = 120          # at least ~4 months of the 20-day vol series before a percentile is shown
METRICS_VERSION = "SQT-QM-1"


class SymbolMetrics(BaseModel):
    symbol: str
    asset_class: str
    as_of: Optional[str] = None
    zscore: Optional[float] = None
    zscore_strength: Optional[str] = None
    zscore_regime: Optional[str] = None
    momentum_30d: Optional[float] = None
    momentum_rank: Optional[float] = None          # filled by rank_universe()
    volatility: Optional[float] = None             # annualised %, 20-day
    volatility_percentile: Optional[float] = None
    volatility_regime: Optional[str] = None
    variance_ratio: Optional[float] = None
    variance_ratio_z: Optional[float] = None
    hurst: Optional[float] = None
    hurst_regime: Optional[str] = None
    sharpe_30: Optional[float] = None
    sharpe_30_se: Optional[float] = None
    sharpe_90: Optional[float] = None
    sharpe_90_se: Optional[float] = None
    trend_quality: Optional[float] = None
    trend_t: Optional[float] = None
    efficiency_ratio: Optional[float] = None
    mean_reversion_setup: bool = False             # |z| ≥ 2 AND variance ratio significantly < 1 — a flag, not a probability
    amihud_illiquidity: Optional[float] = None
    liquidity_score: Optional[float] = None        # filled by rank_universe() (crypto)
    version: str = METRICS_VERSION


def completed(df: pd.DataFrame, freq: str, now: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """Drop a trailing candle that has not closed yet (open time + one period > now)."""
    if df is None or df.empty:
        return df
    now = now or pd.Timestamp.now(tz="UTC")
    idx = pd.DatetimeIndex(df.index)
    last = idx[-1].tz_localize("UTC") if idx[-1].tzinfo is None else idx[-1]
    return df.iloc[:-1] if last + pd.Timedelta(freq) > now else df


def symbol_metrics(symbol: str, hourly: pd.DataFrame, daily: Optional[pd.DataFrame], asset_class: str,
                   risk_free_annual: float = 0.0, now: Optional[pd.Timestamp] = None) -> SymbolMetrics:
    """Every single-instrument metric. A metric whose inputs are too short is None, never a guess."""
    if asset_class not in PERIODS_PER_YEAR_DAILY:
        raise ValueError(f"asset_class must be one of {sorted(PERIODS_PER_YEAR_DAILY)}, got {asset_class!r}")
    h = completed(hourly, "1h", now)
    out = SymbolMetrics(symbol=symbol, asset_class=asset_class,
                        as_of=str(h.index[-1]) if h is not None and len(h) else None)
    if h is not None and len(h):
        hc = h["close"]
        out.zscore = F.rolling_zscore(hc)
        labels = R.zscore_labels(out.zscore)
        out.zscore_strength, out.zscore_regime = labels["strength"], labels["regime"]
        out.efficiency_ratio = F.efficiency_ratio(hc)
        tq = F.trend_quality(hc)
        if tq:
            out.trend_quality, out.trend_t = tq["score"], tq["t_stat"]
        vr = lo_mackinlay_variance_ratio(np.log(hc.astype(float).to_numpy()), VR_Q)
        if vr:
            out.variance_ratio, out.variance_ratio_z, out.hurst = vr.vr, vr.z_robust, vr.hurst
            out.hurst_regime = R.variance_ratio_regime(vr.z_robust)
        out.mean_reversion_setup = bool(out.zscore is not None and abs(out.zscore) >= R.Z_STRONG
                                        and out.hurst_regime == "MEAN_REVERTING")
    d = completed(daily, "1D", now) if daily is not None else None
    if d is not None and len(d):
        A = PERIODS_PER_YEAR_DAILY[asset_class]
        dc = d["close"]
        out.momentum_30d = F.momentum_return(dc)
        rv = F.realized_vol_series(dc, A)
        if rv.notna().any():
            out.volatility = float(rv.dropna().iloc[-1])
            out.volatility_percentile = time_series_percentile(rv.tail(VOL_HISTORY_DAYS), VOL_MIN_HISTORY)
            out.volatility_regime = R.volatility_regime(out.volatility_percentile)
        rets = dc.astype(float).pct_change().dropna()
        for n, attr in ((30, "sharpe_30"), (90, "sharpe_90")):
            if len(rets) >= n:
                s = sharpe_ratio(rets.tail(n).to_numpy(), A, risk_free_annual)
                if s:
                    setattr(out, attr, s.sharpe)
                    setattr(out, attr + "_se", s.se)
        if asset_class == "crypto" and "qav" in d.columns:
            out.amihud_illiquidity = F.amihud_illiquidity(dc, d["qav"])
    return out


def rank_universe(rows: Dict[str, SymbolMetrics]) -> Dict[str, SymbolMetrics]:
    """Cross-sectional ranks within ONE asset class at one instant: momentum rank (0–100, higher = stronger 30-day
    return) and, for crypto, liquidity score = 100 − percentile of Amihud illiquidity (higher = more liquid)."""
    classes = {m.asset_class for m in rows.values()}
    if len(classes) > 1:
        raise ValueError(f"rank_universe() ranks one asset class at a time, got {sorted(classes)}")
    mom = cross_sectional_percentile({k: m.momentum_30d for k, m in rows.items()})
    illiq = cross_sectional_percentile({k: m.amihud_illiquidity for k, m in rows.items()})
    for k, m in rows.items():
        m.momentum_rank = mom[k]
        m.liquidity_score = None if illiq[k] is None else 100.0 - illiq[k]
    return rows
