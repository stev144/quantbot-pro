# ============================================================
# bot/quant_core/lifecycle.py
# claude code changed: new file — Stage 2D Rev B Steps 9–10 (owner decisions
# C1, C2, D10; STAGE2D_DECISION_LOCK.md Decisions 1–2): the canonical trade
# lifecycle rules, shared by research (EntryExitEngine rule_set="CANONICAL")
# and live (PairSignalEngine). Pure functions — no state, no I/O.
#
#   ENTRY  at cutoff t:  |z_t| ≥ threshold, direction σ = −sign(z_t),
#                        AND the held spread at t has the direction's sign
#                        (LONG: h_t < 0, SHORT: h_t > 0) — else ENTRY_SIGN_MISMATCH.
#   FILL   at close(t + d) = E.
#   HELD   h_u = ln P_A,u − β_e ln P_B,u − α_e  (α_e, β_e frozen at t).
#   EXIT decisions at cutoffs u ≥ E + 1 only (C2: the entry-fill close is not
#          also an exit-decision close):
#          NORMALISATION  LONG: h_u ≥ 0,  SHORT: h_u ≤ 0
#          TIMESTOP       u − E ≥ H − d   (decision E + H − 1, fill E + H for d = 1)
#   No partial exit, no target-z exit, no z stop-loss.
# Candle offsets are WALL-CLOCK grid candles (missing candles count).
# ============================================================

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional

import pandas as pd

from bot.quant_core.protocol import CANONICAL, CanonicalProtocol

LONG, SHORT = "LONG_SPREAD", "SHORT_SPREAD"
NORMALISATION, TIMESTOP = "NORMALISATION", "TIMESTOP"
ENTRY_SIGN_MISMATCH = "ENTRY_SIGN_MISMATCH"
HOUR = pd.Timedelta(hours=1)


def held_spread(price_a: float, price_b: float, beta_e: float, alpha_e: float) -> float:
    """h = ln P_A − β_e ln P_B − α_e. NaN if any input is unusable (fail closed downstream)."""
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (price_a, price_b, beta_e, alpha_e)):
        return math.nan
    if price_a <= 0 or price_b <= 0:
        return math.nan
    return math.log(price_a) - beta_e * math.log(price_b) - alpha_e


def entry_direction(z: Optional[float], protocol: CanonicalProtocol = CANONICAL) -> Optional[str]:
    """σ = −sign(z) when |z| ≥ threshold; None otherwise (including undefined z)."""
    if z is None or not math.isfinite(z) or abs(z) < protocol.entry_threshold:
        return None
    return SHORT if z > 0 else LONG


def entry_sign_valid(direction: str, h: float) -> bool:
    """C1: LONG requires h < 0, SHORT requires h > 0 (h = 0 or NaN → invalid)."""
    if not math.isfinite(h):
        return False
    return h < 0 if direction == LONG else h > 0 if direction == SHORT else False


def candles_between(start: pd.Timestamp, end: pd.Timestamp) -> int:
    """Whole hourly grid candles from `start` to `end` (wall clock)."""
    n = (pd.Timestamp(end) - pd.Timestamp(start)) / HOUR
    if n != int(n):
        raise ValueError(f"{start} → {end} is not a whole number of hourly candles")
    return int(n)


def exit_decision(direction: str, h_u: float, candles_since_entry_fill: int,
                  protocol: CanonicalProtocol = CANONICAL) -> Optional[str]:
    """Exit reason decided at cutoff u, or None. candles_since_entry_fill = u − E."""
    if candles_since_entry_fill < 1:                      # C2: no decision on the entry-fill close
        return None
    if math.isfinite(h_u) and ((direction == LONG and h_u >= 0) or (direction == SHORT and h_u <= 0)):
        return NORMALISATION
    if candles_since_entry_fill >= protocol.time_stop_decision_offset():   # H − d
        return TIMESTOP
    return None


@dataclass(frozen=True)
class EntrySignMismatch:
    """A rejected entry (C1) — recorded, counted, never a trade."""
    decision_timestamp: pd.Timestamp
    intended_direction: str
    zscore: float
    held_spread: float
    alpha_e: float
    beta_e: float
    price_a: float          # decision-candle closes used for h (the only prices known at the cutoff)
    price_b: float
    reason: str = ENTRY_SIGN_MISMATCH

    def as_dict(self) -> dict:
        return asdict(self)
