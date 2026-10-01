# ============================================================
# bot/quant_core/half_life.py
# claude code changed: new file — Stage 2D Phase G, Stage 2C §8.
#
# Half-life of the process the strategy actually trades: the FIXED-β residual
# ε_t = x_t − α̂ − β̂ y_t (never the Kalman innovation, whose AR(1) half-life
# reflects filter memory, not mean reversion).
#
#   ε_t = c + φ ε_{t−1} + u_t        (OLS, on adjacent grid candles only)
#   HL_candles = −ln 2 / ln φ        (exact, not the first-order −ln 2/(φ−1))
#   HL_hours   = HL_candles × candle_hours
#
#   0 < φ < 1 → OK;  φ ≥ 1 → NOT_MEAN_REVERTING;  φ ≤ 0 → FAIL (no OU half-life);
#   fewer than `min_obs` usable pairs → UNTESTABLE.
#
# There is no fallback value of any kind (the former 120 h substitute is gone).
# `min_obs` has no default: it is an owner decision (Rev B OPEN-7) the caller
# must declare.
# ============================================================

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from bot.quant_core.status import Result, Status, bad, ok
from bot.quant_core.time_grid import lag_pairs


@dataclass(frozen=True)
class HalfLife:
    phi: float
    intercept: float
    half_life_candles: float
    half_life_hours: float
    n_obs: int


def ar1_half_life(residual: pd.Series, *, candle_hours: float, min_obs: int) -> Result:
    """Exact AR(1) half-life of a fixed-β residual on a complete hourly grid."""
    if not (math.isfinite(candle_hours) and candle_hours > 0):
        raise ValueError("candle_hours must be a finite positive number")
    if not (isinstance(min_obs, int) and min_obs >= 3):
        raise ValueError("min_obs must be an integer >= 3 (declared by the caller)")
    pairs = lag_pairs(residual)
    pairs = pairs[np.isfinite(pairs["x"]) & np.isfinite(pairs["x_lag"])]
    n = len(pairs)
    if n < min_obs:
        return bad(Status.UNTESTABLE, f"only {n} adjacent observation pairs (< min_obs={min_obs})")
    x_lag = pairs["x_lag"].to_numpy()
    if np.var(x_lag) == 0:
        return bad(Status.FAIL, "lagged residual has zero variance")
    X = np.column_stack([np.ones(n), x_lag])
    (c, phi), *_ = np.linalg.lstsq(X, pairs["x"].to_numpy(), rcond=None)
    if not math.isfinite(phi):
        return bad(Status.FAIL, "non-finite AR(1) coefficient")
    if phi >= 1.0:
        return bad(Status.NOT_MEAN_REVERTING, f"phi={phi:.6f} >= 1", value=float(phi))
    if phi <= 0.0:
        return bad(Status.FAIL, f"phi={phi:.6f} <= 0: no OU half-life", value=float(phi))
    hl = -math.log(2.0) / math.log(phi)
    return ok(HalfLife(float(phi), float(c), hl, hl * candle_hours, n))
