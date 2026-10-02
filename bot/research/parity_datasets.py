# ============================================================
# bot/research/parity_datasets.py
# claude code changed: new file — Stage 2D Phase O deterministic parity
# datasets. Seeds are fixed and recorded here; nothing is random at call time.
# ============================================================

from __future__ import annotations

import math
from typing import Tuple

import numpy as np
import pandas as pd

DATASET_A_SEED = 21          # recorded seed
TRUE_BETA = 1.3
TRUE_ALPHA_LOG = 0.2         # in log space, before the price-scale offset
PRICE_SCALE = 10.0
HALF_LIFE_CANDLES = 6.0
SIGMA_Y = 0.010              # per-candle log-return sd of leg B
SIGMA_U = 0.012              # per-candle innovation sd of the OU spread


def seed_intercept() -> float:
    """OLS-seed intercept consistent with the price scale: ln(P_A) = a + β ln(P_B)."""
    return TRUE_ALPHA_LOG + math.log(PRICE_SCALE) - TRUE_BETA * math.log(PRICE_SCALE)


def dataset_a(n: int = 1500, seed: int = DATASET_A_SEED) -> Tuple[pd.Series, pd.Series]:
    """Clean cointegrated pair: x = α + β y + u, u ~ AR(1) with the declared half-life."""
    g = np.random.default_rng(seed)
    y = np.cumsum(g.normal(0, SIGMA_Y, n))
    phi = math.exp(-math.log(2) / HALF_LIFE_CANDLES)
    u, e = np.zeros(n), g.normal(0, SIGMA_U, n)
    for t in range(1, n):
        u[t] = phi * u[t - 1] + e[t]
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    pa = pd.Series(np.exp(TRUE_ALPHA_LOG + TRUE_BETA * y + u) * PRICE_SCALE, index=idx)
    pb = pd.Series(np.exp(y) * PRICE_SCALE, index=idx)
    return pa, pb


# Dataset B: deterministic gaps (positions in dataset A) — a 3-candle exchange
# gap (absent for BOTH paths) and a single candle the live loop skips although
# the exchange has it.
DATASET_B_DATA_GAP = (700, 701, 702)
DATASET_B_LIVE_ONLY_SKIP = (1000,)
