# bot/quant_metrics/ranking.py  # claude code changed: 2026-10-28 — new
"""Percentile ranks. Cross-sectional ranks compare instruments at the same instant; time-series percentiles compare an
instrument with its own past. They answer different questions and are never mixed."""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


def cross_sectional_percentile(values: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    """Percentile rank 0–100 of each instrument's value within the universe at one instant.

        pct_i = 100 · (rank_i − 1) / (N − 1),   rank = average rank (ties share it)

    Lowest = 0, highest = 100. Instruments with a missing value get None and do not count in N. N < 2 → all None.
    Caveat (survivorship): the universe is today's listed symbols, so the rank describes the present universe only."""
    valid = {k: v for k, v in values.items() if v is not None and np.isfinite(v)}
    out: Dict[str, Optional[float]] = {k: None for k in values}
    if len(valid) < 2:
        return out
    s = pd.Series(valid)
    ranks = s.rank(method="average")
    pct = 100.0 * (ranks - 1.0) / (len(s) - 1.0)
    out.update({k: float(v) for k, v in pct.items()})
    return out


def time_series_percentile(history: pd.Series, min_obs: int) -> Optional[float]:
    """Percentile 0–100 of the LAST value within `history` (which includes it), using only past and present values:
    100 · share of values ≤ the last one. Fewer than `min_obs` usable values → None."""
    h = history.dropna().to_numpy(dtype=float)
    if h.size < min_obs:
        return None
    return float(100.0 * np.mean(h <= h[-1]))
