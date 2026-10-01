# ============================================================
# bot/pairs/pnl.py
# claude code changed: new file — Phase 1, pairs quantitative ground truth.
#
# Implements PAIRS_PNL_SPECIFICATION.md exactly and nothing more: fixed-leg
# quantities, entry/exit notional, mark P&L, slippage, per-leg fees, borrow
# interest, net P&L and return on capital for one two-leg position,
# including partial exits. Pure functions, no I/O, no signal logic, no
# sizing policy. Section numbers (§) refer to that specification.
#
# Deliberately knows nothing about Kalman α/β, spreads or z-scores: what a
# position earns depends only on the quantities held and the prices they
# trade at (audit C-01).
# ============================================================

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Optional, Tuple

_DIRECTIONS = {"LONG_SPREAD": 1, "SHORT_SPREAD": -1}


@dataclass(frozen=True)
class PairCosts:
    """Per-fill fee and slippage fractions (§5, §6) and hourly borrow rates of
    each leg's base asset (§7). A leg's borrow rate only matters while that
    leg is short."""
    fee_rate: float
    slippage_rate: float
    borrow_rate_hourly_a: float = 0.0
    borrow_rate_hourly_b: float = 0.0

    def __post_init__(self):
        for name in ("fee_rate", "slippage_rate", "borrow_rate_hourly_a", "borrow_rate_hourly_b"):
            v = getattr(self, name)
            if not (math.isfinite(v) and v >= 0.0):
                raise ValueError(f"PairCosts.{name} must be a finite, non-negative fraction, got {v!r}")


@dataclass(frozen=True)
class PairPosition:
    """The fixed quantities held (§3). qty_* are signed: + long, − short."""
    direction: str
    beta: float
    qty_a: float
    qty_b: float
    entry_mark_a: float
    entry_mark_b: float
    entry_time: object
    notional_a: float        # |qty_a| · entry_mark_a
    notional_b: float        # |qty_b| · entry_mark_b
    capital: float           # §10: USDT committed at entry for the quantities still held


@dataclass(frozen=True)
class PairSlice:
    """The realised result of closing `fraction` of a position (§4–§10)."""
    fraction: float
    exit_time: object
    hours_held: float
    exit_mark_a: float
    exit_mark_b: float
    entry_fill_notional: float
    exit_fill_notional: float
    mark_pnl: float
    slippage_cost: float
    fee_cost: float
    borrow_cost: float
    net_pnl: float
    capital: float
    roc: float


def open_pair_position(direction: str, beta: float, gross_notional: float,
                       mark_a: float, mark_b: float, entry_time) -> PairPosition:
    """§3: N_A = T/(1+|β|), N_B = T·|β|/(1+|β|); q_A = d·N_A/M_A,0; q_B = −d·β·N_A/M_B,0."""
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction must be LONG_SPREAD or SHORT_SPREAD, got {direction!r}")
    for name, v in (("beta", beta), ("gross_notional", gross_notional), ("mark_a", mark_a), ("mark_b", mark_b)):
        if not math.isfinite(v):
            raise ValueError(f"{name} must be finite, got {v!r}")
    if gross_notional <= 0 or mark_a <= 0 or mark_b <= 0:
        raise ValueError("gross_notional, mark_a and mark_b must be positive")
    d = _DIRECTIONS[direction]
    notional_a = gross_notional / (1.0 + abs(beta))
    qty_a = d * notional_a / mark_a
    qty_b = -d * beta * notional_a / mark_b
    return PairPosition(
        direction=direction, beta=beta, qty_a=qty_a, qty_b=qty_b,
        entry_mark_a=mark_a, entry_mark_b=mark_b, entry_time=entry_time,
        notional_a=abs(qty_a) * mark_a, notional_b=abs(qty_b) * mark_b,
        capital=gross_notional,
    )


def _accrual_hours(hours_held: float) -> int:
    """§7 / assumption A4: first hour charged at borrow, then each started hour."""
    return max(1, math.ceil(hours_held - 1e-9))


def close_pair_slice(position: PairPosition, fraction: float, mark_a: float, mark_b: float,
                     exit_time, costs: PairCosts) -> Tuple[PairSlice, Optional[PairPosition]]:
    """Close `fraction` (0 < φ ≤ 1) of the CURRENT quantities of both legs at the
    given marks. Returns (realised slice, remaining position or None). Entry-side
    slippage/fees are carried pro rata by each slice (§9)."""
    if not (0.0 < fraction <= 1.0):
        raise ValueError(f"fraction must be in (0, 1], got {fraction!r}")
    if not (math.isfinite(mark_a) and math.isfinite(mark_b)) or mark_a <= 0 or mark_b <= 0:
        raise ValueError("exit marks must be finite and positive")
    hours_held = (exit_time - position.entry_time).total_seconds() / 3600.0
    if hours_held < 0:
        raise ValueError("exit_time is before the position's entry_time")

    s, f = costs.slippage_rate, costs.fee_rate
    h = _accrual_hours(hours_held)
    legs = (
        (fraction * position.qty_a, position.entry_mark_a, mark_a, costs.borrow_rate_hourly_a),
        (fraction * position.qty_b, position.entry_mark_b, mark_b, costs.borrow_rate_hourly_b),
    )
    mark_pnl = slip = fees = borrow = entry_fill = exit_fill = 0.0
    for q, m0, m1, r in legs:
        if q == 0.0:
            continue
        units = abs(q)
        f0 = m0 * (1.0 + s) if q > 0 else m0 * (1.0 - s)      # entry: buy a long, sell a short
        f1 = m1 * (1.0 - s) if q > 0 else m1 * (1.0 + s)      # exit: the reverse
        mark_pnl += q * (m1 - m0)
        slip += units * s * (m0 + m1)
        fees += f * units * (f0 + f1)
        entry_fill += units * f0
        exit_fill += units * f1
        if q < 0:
            borrow += units * r * h * f1

    net = mark_pnl - slip - fees - borrow
    capital = fraction * position.capital
    result = PairSlice(
        fraction=fraction, exit_time=exit_time, hours_held=hours_held,
        exit_mark_a=mark_a, exit_mark_b=mark_b,
        entry_fill_notional=entry_fill, exit_fill_notional=exit_fill,
        mark_pnl=mark_pnl, slippage_cost=slip, fee_cost=fees, borrow_cost=borrow,
        net_pnl=net, capital=capital, roc=net / capital,
    )
    if fraction == 1.0:
        return result, None
    keep = 1.0 - fraction
    remaining = replace(
        position, qty_a=position.qty_a * keep, qty_b=position.qty_b * keep,
        notional_a=position.notional_a * keep, notional_b=position.notional_b * keep,
        capital=position.capital * keep,
    )
    return result, remaining
