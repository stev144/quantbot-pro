# bot/quant_metrics/regimes.py  # claude code changed: 2026-10-28 — new
"""Regime labels. Every cut-off is either a standard-deviation unit, a 5 % significance level, or an equal-probability
quantile — no hand-tuned thresholds."""
from __future__ import annotations

from typing import Optional

Z_STRONG = 2.0        # two standard deviations
Z_MODERATE = 1.0      # one standard deviation
VR_CRITICAL = 1.959964  # two-sided 5 % critical value of N(0,1)
VOL_QUINTILES = (20.0, 40.0, 60.0, 80.0)   # equal-probability bins of the asset's own history
VOL_CLASSES = ("VERY_LOW", "LOW", "NORMAL", "HIGH", "EXTREME")


def zscore_labels(z: Optional[float]) -> dict:
    """strength: WEAK |z| < 1, MODERATE 1 ≤ |z| < 2, STRONG |z| ≥ 2.
    regime: EXTENDED_ABOVE z ≥ 2, EXTENDED_BELOW z ≤ −2, otherwise NEAR_MEAN. Descriptive only."""
    if z is None:
        return {"strength": None, "regime": None}
    a = abs(z)
    strength = "STRONG" if a >= Z_STRONG else "MODERATE" if a >= Z_MODERATE else "WEAK"
    regime = "EXTENDED_ABOVE" if z >= Z_STRONG else "EXTENDED_BELOW" if z <= -Z_STRONG else "NEAR_MEAN"
    return {"strength": strength, "regime": regime}


def volatility_regime(percentile: Optional[float]) -> Optional[str]:
    """Quintile of today's realised volatility within the asset's own trailing year."""
    if percentile is None:
        return None
    for cut, label in zip(VOL_QUINTILES, VOL_CLASSES):
        if percentile < cut:
            return label
    return VOL_CLASSES[-1]


def variance_ratio_regime(z_robust: Optional[float]) -> Optional[str]:
    """TRENDING / MEAN_REVERTING only when the Lo–MacKinlay z* rejects a random walk at 5 % (two-sided);
    otherwise RANDOM_WALK (not rejected). This replaces fixed Hurst cut-offs (0.45 / 0.55), which sit inside the
    estimator's own sampling noise at these sample sizes."""
    if z_robust is None:
        return None
    if z_robust > VR_CRITICAL:
        return "TRENDING"
    if z_robust < -VR_CRITICAL:
        return "MEAN_REVERTING"
    return "RANDOM_WALK"
