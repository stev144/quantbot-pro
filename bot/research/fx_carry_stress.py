# bot/research/fx_carry_stress.py  # claude code changed: 2026-10-28 — new: FX carry study step 2 (stress test)
"""Stress test of a volatility-sized carry position: LONG USD/JPY + LONG USD/CHF (the two pairs the broker pays to hold,
FX carry accounting 2026-10-28). Purpose: SET RISK LIMITS (size, maximum loss) — not to prove an edge or pick a winner.
EXPLORATORY: history up to the FX research cutoff is pseudo-holdout (CRRP-2026-10-02).

Rules (declared before running, never tuned):
    data      daily closes (last hourly close of each UTC day) from data/forex, through FX_RESEARCH_DATA_CUTOFF
    price     return on a USD notional of a long USD/XXX position from day t−1 to t:  1 − P_{t−1} / P_t
    carry     historical, rebuilt from central-bank rates (FRED), forward-filled, per calendar day:
                  (r_USD − r_XXX − broker_markup) / 365 · days elapsed
              r_USD = fed funds (DFF); r_JPY = Japan call rate (IRSTCI01JPM156N); r_CHF = Swiss 3-month interbank
              (IR3TIB01CHM156N — the overnight series ends 2024-03). broker_markup = the per-side markup measured from
              the broker's live swaps (carry accounting), held constant — an assumption, stated.
    sizing    weights ∝ 1 / σ_i (equal risk), scaled so the portfolio's ex-ante volatility (trailing 60 trading days
              covariance, data up to the previous day) equals TARGET_VOL; recomputed on the first trading day of each
              month; total notional capped at LEVERAGE_CAP × equity (ESMA retail 1:30 for majors)
    costs     every rebalance pays half the round-trip spread (live snapshot) on the traded notional, × 2 for safety
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

PAIRS = ("USD/JPY", "USD/CHF")
QUOTE_RATE_SERIES = {"USD/JPY": "IRSTCI01JPM156N", "USD/CHF": "IR3TIB01CHM156N"}
USD_RATE_SERIES = "DFF"
VOL_LOOKBACK = 60
TRADING_DAYS = 260.0
LEVERAGE_CAP = 30.0
COST_SAFETY = 2.0
EPISODES = {   # named stress windows, fixed before running
    "2022 rate shock (USD/JPY 115 → 151)": ("2022-01-01", "2022-10-31"),
    "Nov 2022 – Jan 2023 reversal": ("2022-10-21", "2023-01-16"),
    "Aug 2024 yen-carry unwind": ("2024-07-01", "2024-08-09"),
    "Apr 2025 tariff shock": ("2025-03-31", "2025-04-30"),
}


@dataclass(frozen=True)
class StressConfig:
    target_vol: float = 0.10                 # annual, fraction
    markups_pct_yr: Dict[str, float] = None  # per side, from the carry accounting
    spreads_pct: Dict[str, float] = None     # round trip, from the carry accounting


def price_return_usd_notional(close: pd.Series) -> pd.Series:
    """Return on a USD notional held long in USD/XXX: P&L in XXX is N·ΔP, in USD N·ΔP/P_t, so r_t = 1 − P_{t−1}/P_t."""
    return (1.0 - close.shift(1) / close).dropna()


def daily_carry(index: pd.DatetimeIndex, usd_rate: pd.Series, quote_rate: pd.Series, markup_pct_yr: float) -> pd.Series:
    """Carry accrued from the previous trading day to each day (fraction of notional): rates in % a year, forward-filled
    to each day (monthly series apply from their observation date), times calendar days elapsed / 365."""
    rates = pd.DataFrame({"usd": usd_rate, "q": quote_rate}).sort_index()
    rates = rates.reindex(rates.index.union(index)).ffill().reindex(index)
    diff = (rates["usd"] - rates["q"] - markup_pct_yr) / 100.0
    days = pd.Series(index, index=index).diff().dt.days.fillna(0)
    return (diff.shift(1) * days / 365.0).fillna(0.0)          # carry earned over (t−1, t] at the rate known at t−1


def run_stress(closes: Dict[str, pd.Series], rates: Dict[str, pd.Series], cfg: StressConfig) -> Dict:
    px = pd.DataFrame(closes).dropna()
    ret = pd.DataFrame({p: price_return_usd_notional(px[p]) for p in PAIRS}).dropna()
    carry = pd.DataFrame({p: daily_carry(ret.index, rates[USD_RATE_SERIES], rates[QUOTE_RATE_SERIES[p]],
                                         cfg.markups_pct_yr[p]) for p in PAIRS})
    idx = ret.index
    w = pd.DataFrame(np.nan, index=idx, columns=list(PAIRS))
    months = pd.Series(idx.to_period("M"), index=idx)
    first_days = idx[(months != months.shift(1)).to_numpy()]
    for d in first_days:
        hist = ret.loc[:d].iloc[:-1].tail(VOL_LOOKBACK)            # data up to the previous day only
        if len(hist) < VOL_LOOKBACK:
            continue
        sig = hist.std(ddof=1) * math.sqrt(TRADING_DAYS)
        raw = 1.0 / sig
        cov = hist.cov() * TRADING_DAYS
        port = math.sqrt(float(raw @ cov @ raw))
        wt = raw * (cfg.target_vol / port)
        if wt.sum() > LEVERAGE_CAP:
            wt *= LEVERAGE_CAP / wt.sum()
        w.loc[d] = wt.to_numpy()
    w = w.ffill().dropna()
    ret, carry = ret.loc[w.index], carry.loc[w.index]
    turnover = w.diff().abs().fillna(w.abs())                       # first day: build the position
    cost = (turnover * pd.Series({p: cfg.spreads_pct[p] / 100.0 / 2.0 * COST_SAFETY for p in PAIRS})).sum(axis=1)
    price_pnl = (w * ret).sum(axis=1)
    carry_pnl = (w * carry).sum(axis=1)
    daily = price_pnl + carry_pnl - cost
    equity = (1.0 + daily).cumprod()
    return {"daily": daily, "price": price_pnl, "carry": carry_pnl, "cost": cost, "equity": equity, "weights": w}


def max_drawdown(equity: pd.Series) -> Dict:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    trough = dd.idxmin()
    start = equity.loc[:trough].idxmax()
    rec = equity.loc[trough:]
    recovered = rec[rec >= equity.loc[start]]
    return {"max_dd_pct": float(dd.min() * 100), "peak": str(start.date()), "trough": str(trough.date()),
            "recovered": str(recovered.index[0].date()) if len(recovered) else None,
            "days_peak_to_trough": int((trough - start).days)}


def summarise(res: Dict) -> Dict:
    d, eq = res["daily"], res["equity"]
    years = (d.index[-1] - d.index[0]).days / 365.25
    out = {"start": str(d.index[0].date()), "end": str(d.index[-1].date()), "years": round(years, 2),
           "cagr_pct": float((eq.iloc[-1] ** (1 / years) - 1) * 100),
           "vol_pct": float(d.std(ddof=1) * math.sqrt(TRADING_DAYS) * 100),
           "carry_pct_yr": float(res["carry"].sum() / years * 100),
           "price_pct_yr": float(res["price"].sum() / years * 100),
           "cost_pct_yr": float(res["cost"].sum() / years * 100),
           "worst_day_pct": float(d.min() * 100), "worst_day": str(d.idxmin().date()),
           "worst_week_pct": float((((1 + d).rolling(5).apply(np.prod, raw=True)) - 1).min() * 100),
           "worst_month_pct": float((((1 + d).rolling(21).apply(np.prod, raw=True)) - 1).min() * 100),
           "avg_gross_leverage": float(res["weights"].sum(axis=1).mean()),
           **max_drawdown(eq)}
    out["carry_years_lost_in_max_dd"] = abs(out["max_dd_pct"]) / out["carry_pct_yr"] if out["carry_pct_yr"] > 0 else None
    by_year = pd.DataFrame({"total": res["daily"], "price": res["price"], "carry": res["carry"], "cost": -res["cost"]})
    out["by_year_pct"] = (by_year.groupby(by_year.index.year).sum() * 100).round(2).to_dict(orient="index")
    eps = {}
    for name, (a, b) in EPISODES.items():
        e = eq.loc[a:b]
        if len(e) > 1:
            eps[name] = {"return_pct": float((e.iloc[-1] / e.iloc[0] - 1) * 100),
                         "max_dd_pct": float((e / e.cummax() - 1).min() * 100)}
    out["episodes"] = eps
    return out
