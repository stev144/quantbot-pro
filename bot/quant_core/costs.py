# ============================================================
# bot/quant_core/costs.py
# claude code changed: new file — Stage 2D Phase K, Stage 2C §12.
#
# Costs never silently default to zero. Each component is either a known
# number or None = UNAVAILABLE. A net P&L is only produced when every cost
# that applies to the slice is known; otherwise the status is
# COST_UNAVAILABLE and the result carries what IS known (gross, net excluding
# the unknown component, and — when only borrow is unknown — the break-even
# borrow rate). Perpetual funding is explicitly excluded: this strategy holds
# spot and isolated-margin positions only.
# ============================================================

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

from bot.pairs.pnl import PairCosts, PairPosition, PairSlice, close_pair_slice, _accrual_hours
from bot.quant_core.status import Result, Status, bad, ok

FUNDING_EXCLUDED_REASON = "spot / isolated-margin strategy: perpetual funding does not apply"


@dataclass(frozen=True)
class CostModel:
    fee_rate: Optional[float]                 # per fill, on fill notional
    slippage_rate: Optional[float]            # per fill, adverse
    borrow_rate_hourly_a: Optional[float]     # only charged while leg A is short
    borrow_rate_hourly_b: Optional[float]     # only charged while leg B is short

    def __post_init__(self):
        for name in ("fee_rate", "slippage_rate", "borrow_rate_hourly_a", "borrow_rate_hourly_b"):
            v = getattr(self, name)
            if v is not None and not (math.isfinite(v) and v >= 0):
                raise ValueError(f"{name} must be None (unavailable) or a finite number >= 0")


@dataclass(frozen=True)
class SliceEvaluation:
    gross_mark_pnl: float
    net_pnl: Optional[float]                  # None unless every applicable cost is known
    net_excluding_borrow: Optional[float]     # known costs except borrow
    breakeven_borrow_rate_hourly: Optional[float]
    missing: Tuple[str, ...]
    slice: Optional[PairSlice]                # full pnl.py slice when net is complete


def evaluate_slice(position: PairPosition, fraction: float, mark_a: float, mark_b: float,
                   exit_time, costs: CostModel) -> Result:
    """Close `fraction` of `position` under `costs`. OK(SliceEvaluation) when
    all applicable costs are known, COST_UNAVAILABLE (value still carries the
    partial evaluation) otherwise."""
    missing = []
    if costs.fee_rate is None:
        missing.append("fee_rate")
    if costs.slippage_rate is None:
        missing.append("slippage_rate")
    short_leg_rate = None
    short_qty, short_mark = 0.0, None
    if position.qty_a < 0:
        short_leg_rate, short_qty, short_mark, leg = costs.borrow_rate_hourly_a, abs(position.qty_a), mark_a, "a"
    elif position.qty_b < 0:
        short_leg_rate, short_qty, short_mark, leg = costs.borrow_rate_hourly_b, abs(position.qty_b), mark_b, "b"
    borrow_missing = short_qty > 0 and short_leg_rate is None
    if borrow_missing:
        missing.append(f"borrow_rate_hourly_{leg}")

    # Gross mark P&L never depends on costs.
    zero = PairCosts(0.0, 0.0, 0.0, 0.0)
    gross_slice, _ = close_pair_slice(position, fraction, mark_a, mark_b, exit_time, zero)
    gross = gross_slice.mark_pnl

    if costs.fee_rate is None or costs.slippage_rate is None:
        ev = SliceEvaluation(gross, None, None, None, tuple(missing), None)
        return bad(Status.COST_UNAVAILABLE, f"unknown: {', '.join(missing)}", value=ev)

    # A borrow rate only matters for the SHORT leg; the long leg's rate (even if
    # unknown) is never charged by pnl.py, so it is passed as 0.0. The short leg's
    # rate is passed when known; when unknown it is charged as 0.0 here and the
    # result is reported as COST_UNAVAILABLE below — never as a complete net.
    rate_a = costs.borrow_rate_hourly_a if costs.borrow_rate_hourly_a is not None else 0.0
    rate_b = costs.borrow_rate_hourly_b if costs.borrow_rate_hourly_b is not None else 0.0
    known = PairCosts(costs.fee_rate, costs.slippage_rate, rate_a, rate_b)
    known_slice, _ = close_pair_slice(position, fraction, mark_a, mark_b, exit_time, known)
    if not borrow_missing:
        ev = SliceEvaluation(gross, known_slice.net_pnl, known_slice.net_pnl + known_slice.borrow_cost,
                             None, (), known_slice)
        return ok(ev)

    # Only borrow is unknown: report net excluding borrow and the break-even rate
    # r* such that net = 0, using pnl.py's own borrow formula |q|·r·H·F_exit.
    s = costs.slippage_rate
    exit_fill = short_mark * (1.0 + s)                     # buying back a short pays up
    hours = (exit_time - position.entry_time).total_seconds() / 3600.0
    h = _accrual_hours(hours)
    denom = fraction * short_qty * h * exit_fill
    net_ex = known_slice.net_pnl                            # borrow was charged as 0 above
    breakeven = net_ex / denom if denom > 0 else None
    ev = SliceEvaluation(gross, None, net_ex, breakeven, tuple(missing), None)
    return bad(Status.COST_UNAVAILABLE, f"unknown: {', '.join(missing)}", value=ev)
