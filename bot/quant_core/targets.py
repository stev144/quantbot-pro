# ============================================================
# bot/quant_core/targets.py
# claude code changed: new file — Stage 2D Phase F, Stage 2C §4.
#
# The ONLY admissible research label: the gross return per unit of capital of
# an executable hedged position,
#
#   r_{t,d,h} = σ_t · [ (P^A_{t+d+h}/P^A_{t+d} − 1) − β_t (P^B_{t+d+h}/P^B_{t+d} − 1) ] / (1 + |β_t|)
#
#   * t     = information cutoff (close of candle t)
#   * β_t   = hedge ratio known at the cutoff, frozen for the trade
#   * d ≥ 1 = execution delay in candles (entry at the close of t+d)
#   * h ≥ 1 = holding horizon in candles (exit at the close of t+d+h)
#   * σ_t   = +1 long spread (long A, short β·B), −1 short spread, 0 flat
#
# With zero costs this equals bot.pairs.pnl mark P&L / capital exactly
# (tested). Costs are subtracted afterwards via bot.pairs.pnl.
#
# RETIRED: the `spread_forward_N` labels (s_{t+N} − s_t). They start at the
# cutoff (d = 0), share s_t with the z-score, and (on the Kalman path)
# difference a re-estimated series no position can hold. They must not be
# used as evidence — see RETIRED_LABEL_PREFIXES / assert_label_admissible.
# ============================================================

from __future__ import annotations

import numpy as np
import pandas as pd

from bot.quant_core.time_grid import assert_complete_grid

RETIRED_LABEL_PREFIXES = ("spread_forward_",)


class RetiredLabelError(ValueError):
    """A retired (non-tradeable) label was offered as research evidence."""


def assert_label_admissible(label_name: str) -> None:
    for prefix in RETIRED_LABEL_PREFIXES:
        if str(label_name).startswith(prefix):
            raise RetiredLabelError(
                f"label '{label_name}' is retired (Stage 2C §4): s_(t+N) − s_t is not the return of an "
                f"executable position. Use bot.quant_core.targets.tradeable_return instead."
            )


def side_from_zscore(z: pd.Series, *, entry_threshold: float) -> pd.Series:
    """σ_t = −sign(z_t) when |z_t| ≥ entry_threshold, else 0. NaN z → 0 (no
    position: an unknown signal never creates a side)."""
    if not (np.isfinite(entry_threshold) and entry_threshold > 0):
        raise ValueError("entry_threshold must be a finite positive number")
    zz = z.to_numpy(dtype=float)
    side = np.where(np.isfinite(zz) & (np.abs(zz) >= entry_threshold), -np.sign(zz), 0.0)
    return pd.Series(side, index=z.index)


def tradeable_return(price_a: pd.Series, price_b: pd.Series, beta_at_cutoff: pd.Series,
                     side: pd.Series, *, d: int, h: int) -> pd.Series:
    """Gross tradeable return r_{t,d,h} for every cutoff t on a complete grid.

    NaN (never a substitute) where: a required price at t+d or t+d+h is
    missing or non-positive, β_t is non-finite or ≤ 0 (outside the executable
    domain), or the side is missing. A zero side gives 0.0 (flat)."""
    if not (isinstance(d, (int, np.integer)) and d >= 1):
        raise ValueError("execution delay d must be an integer >= 1 (no fill at the information cutoff)")
    if not (isinstance(h, (int, np.integer)) and h >= 1):
        raise ValueError("holding horizon h must be an integer >= 1")
    for s in (price_a, price_b, beta_at_cutoff, side):
        assert_complete_grid(s.index)
    if not (price_a.index.equals(price_b.index) and price_a.index.equals(beta_at_cutoff.index)
            and price_a.index.equals(side.index)):
        raise ValueError("price_a, price_b, beta_at_cutoff and side must share one grid")

    pa = price_a.where(price_a > 0)
    pb = price_b.where(price_b > 0)
    ra = pa.shift(-(d + h)) / pa.shift(-d) - 1.0       # grid is complete, so shift == time shift
    rb = pb.shift(-(d + h)) / pb.shift(-d) - 1.0
    beta = beta_at_cutoff.where(np.isfinite(beta_at_cutoff) & (beta_at_cutoff > 0))
    gross = (ra - beta * rb) / (1.0 + beta.abs())
    out = side * gross
    out[side == 0] = 0.0
    out[side.isna()] = np.nan
    out[(side != 0) & gross.isna()] = np.nan
    return out
