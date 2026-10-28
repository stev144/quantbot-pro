# bot/quant_metrics/statistics.py  # claude code changed: 2026-10-28 — new
"""Statistical building blocks. Pure numpy, deterministic, no look-ahead (every function sees only the array passed)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np


def newey_west_lag(n: int) -> int:
    """Newey & West (1994) automatic bandwidth L = floor(4 (n/100)^(2/9)).

    A data-dependent rule, not a tuned constant: it grows slowly with n so the long-run variance is consistent."""
    if n < 2:
        return 0
    return int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0)))


def newey_west_se_of_mean(x: np.ndarray, lag: Optional[int] = None) -> Optional[float]:
    """Standard error of the sample mean, robust to autocorrelation and heteroskedasticity (Bartlett kernel).

        LRV = γ0 + 2 Σ_{l=1..L} (1 − l/(L+1)) γ_l,   γ_l = (1/n) Σ_t (x_t − x̄)(x_{t−l} − x̄)
        SE(x̄) = sqrt(LRV / n)

    Edge cases: n < 3 or a non-positive long-run variance → None (never a fake number)."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = x.size
    if n < 3:
        return None
    L = newey_west_lag(n) if lag is None else int(lag)
    d = x - x.mean()
    lrv = float(d @ d) / n
    for l in range(1, min(L, n - 1) + 1):
        lrv += 2.0 * (1.0 - l / (L + 1.0)) * float(d[l:] @ d[:-l]) / n
    if not np.isfinite(lrv) or lrv <= 0:
        return None
    return math.sqrt(lrv / n)


@dataclass(frozen=True)
class VarianceRatio:
    vr: float          # Var(q-period return) / (q · Var(1-period return)); 1 under a random walk
    z_robust: float    # Lo–MacKinlay heteroskedasticity-robust z*  (asymptotically N(0,1) under the null)
    hurst: float       # 0.5 + ln(VR) / (2 ln q): the exponent implied by VR = q^(2H − 1)
    q: int
    n: int


def lo_mackinlay_variance_ratio(log_prices: np.ndarray, q: int) -> Optional[VarianceRatio]:
    """Lo & MacKinlay (1988) overlapping variance-ratio test with bias corrections and the heteroskedasticity-
    consistent statistic z*(q).

        r_t = p_t − p_{t−1},  μ = (p_T − p_0)/T
        σ_a² = Σ (r_t − μ)² / (T − 1)
        σ_c² = Σ_{t=q..T} (p_t − p_{t−q} − qμ)² / m,   m = q (T − q + 1)(1 − q/T)
        VR(q) = σ_c² / σ_a²
        δ_j = Σ_{t=j+1..T} (r_t − μ)² (r_{t−j} − μ)² / [Σ (r_t − μ)²]²
        θ(q) = Σ_{j=1..q−1} [2(q − j)/q]² δ_j,     z* = (VR − 1) / sqrt(θ)

    VR > 1 ↔ positive autocorrelation (trending), VR < 1 ↔ negative (mean reverting). Assumptions: returns are
    uncorrelated under the null but may be heteroskedastic. Needs T/q comfortably large (here T ≥ 10 q).
    Returns None when inputs are too short or degenerate."""
    p = np.asarray(log_prices, dtype=float)
    p = p[np.isfinite(p)]
    T = p.size - 1
    if q < 2 or T < 10 * q:
        return None
    r = np.diff(p)
    mu = (p[-1] - p[0]) / T
    e = r - mu
    ss = float(e @ e)
    if ss <= 0:
        return None
    sig_a = ss / (T - 1)
    m = q * (T - q + 1) * (1.0 - q / T)
    agg = p[q:] - p[:-q] - q * mu
    sig_c = float(agg @ agg) / m
    vr = sig_c / sig_a
    e2 = e * e
    theta = 0.0
    for j in range(1, q):
        delta = float(e2[j:] @ e2[:-j]) / ss ** 2
        theta += (2.0 * (q - j) / q) ** 2 * delta
    if theta <= 0 or vr <= 0:
        return None
    return VarianceRatio(vr=vr, z_robust=(vr - 1.0) / math.sqrt(theta),
                         hurst=0.5 + math.log(vr) / (2.0 * math.log(q)), q=q, n=T)


@dataclass(frozen=True)
class Sharpe:
    sharpe: float      # annualised
    se: float          # annualised standard error (Lo 2002, IID approximation)
    n: int


def sharpe_ratio(simple_returns: np.ndarray, periods_per_year: float, risk_free_annual: float = 0.0) -> Optional[Sharpe]:
    """Annualised Sharpe ratio of per-period simple returns, with its standard error.

        SR_p = (mean(r) − rf/A) / std(r)   (ddof = 1),   SR = SR_p · sqrt(A)
        SE(SR) ≈ sqrt((1 + SR_p²/2) / n) · sqrt(A)          (Lo 2002, IID returns)

    A = periods per year (365 for crypto daily, 260 for forex daily). The IID SE understates uncertainty when returns
    are autocorrelated; it is shown so a short-window Sharpe is never read as precise. None when n < 10 or std = 0."""
    r = np.asarray(simple_returns, dtype=float)
    r = r[np.isfinite(r)]
    n = r.size
    if n < 10:
        return None
    sd = float(r.std(ddof=1))
    if sd <= 0:
        return None
    sr_p = (float(r.mean()) - risk_free_annual / periods_per_year) / sd
    return Sharpe(sharpe=sr_p * math.sqrt(periods_per_year),
                  se=math.sqrt((1.0 + 0.5 * sr_p ** 2) / n) * math.sqrt(periods_per_year), n=n)


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
