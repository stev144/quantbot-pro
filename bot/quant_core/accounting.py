# ============================================================
# bot/quant_core/accounting.py
# claude code changed: new file — Stage 2D Phases H, J, M; Stage 2C §9, §11, §14.
#
# One authoritative definition of the leg split, the executable-β domain,
# exchange feasibility, and the Kelly fraction. bot/pairs/pnl.py remains the
# canonical P&L accounting; this module adds the domain checks pnl.py
# deliberately does not make.
#
#   N_A = T / (1 + |β|),   N_B = T·|β| / (1 + |β|)      (β > 0 enforced, so |β| = β)
#
# β ≤ 0 (same-side legs, net exposure +T), β = 0 (no hedge) and non-finite β
# are outside the executable domain → SKIP. Never 1 + β.
# ============================================================

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from bot.pairs.margin_adapter import AmountRules, floor_to_step   # reuse the live rounding rules
from bot.pairs.pnl import PairPosition, open_pair_position
from bot.quant_core.status import Result, Status, bad, ok


def validate_beta(beta: float) -> Result:
    if beta is None or not isinstance(beta, (int, float)) or not math.isfinite(beta):
        return bad(Status.SKIP, f"non-finite hedge ratio {beta!r}")
    if beta <= 0:
        return bad(Status.SKIP, f"hedge ratio {beta} <= 0 is outside the executable domain")
    return ok(float(beta))


def leg_notionals(total_usdt: float, beta: float) -> Result:
    """OK((N_A, N_B)) or SKIP. The single leg-split formula for research,
    sizing and live execution."""
    b = validate_beta(beta)
    if not b.ok:
        return b
    if not (math.isfinite(total_usdt) and total_usdt > 0):
        return bad(Status.SKIP, f"gross notional {total_usdt!r} must be finite and > 0")
    beta = b.value
    n_a = total_usdt / (1.0 + abs(beta))
    return ok((n_a, total_usdt * abs(beta) / (1.0 + abs(beta))))


def open_position_checked(direction: str, beta: float, total_usdt: float, mark_a: float, mark_b: float,
                          entry_time) -> Result:
    """bot.pairs.pnl.open_pair_position behind the executable-domain check."""
    legs = leg_notionals(total_usdt, beta)
    if not legs.ok:
        return legs
    return ok(open_pair_position(direction, float(beta), float(total_usdt), mark_a, mark_b, entry_time))


def minimum_gross_notional(beta: float, min_notional_a: float, min_notional_b: float, *,
                           buffer_kappa: float) -> Result:
    """Smallest T for which both legs clear their minimum notional with a
    declared safety buffer κ ≥ 0 (owner decision, no default):
        N_A ≥ (1+κ) m_A  ⇔  T ≥ (1+κ) m_A (1+β)
        N_B ≥ (1+κ) m_B  ⇔  T ≥ (1+κ) m_B (1+β)/β
    The result is always ≥ m_A + m_B (equality at κ = 0, β = m_B/m_A)."""
    b = validate_beta(beta)
    if not b.ok:
        return b
    if not (math.isfinite(buffer_kappa) and buffer_kappa >= 0):
        raise ValueError("buffer_kappa must be a finite number >= 0")
    for m in (min_notional_a, min_notional_b):
        if not (math.isfinite(m) and m >= 0):
            raise ValueError("minimum notionals must be finite and >= 0")
    beta = b.value
    t_min = max((1 + buffer_kappa) * min_notional_a * (1 + beta),
                (1 + buffer_kappa) * min_notional_b * (1 + beta) / beta)
    return ok(t_min)


@dataclass(frozen=True)
class ExecutableLegs:
    qty_a: float      # unsigned, floored to the exchange step
    qty_b: float
    notional_a: float
    notional_b: float


def executable_quantities(position: PairPosition, *, rules_a: AmountRules, rules_b: AmountRules,
                          min_notional_a: float, min_notional_b: float) -> Result:
    """Floor both legs to their exchange step and verify minQty / minNotional
    AFTER flooring, at the entry marks. OK(ExecutableLegs) or SKIP."""
    out = []
    for qty, mark, rules, min_notional, leg in (
        (abs(position.qty_a), position.entry_mark_a, rules_a, min_notional_a, "A"),
        (abs(position.qty_b), position.entry_mark_b, rules_b, min_notional_b, "B"),
    ):
        q = floor_to_step(qty, rules.step)
        if q <= 0 or (rules.min_qty is not None and q < rules.min_qty):
            return bad(Status.SKIP, f"leg {leg}: floored quantity {q} below minQty {rules.min_qty}")
        if q * mark < min_notional:
            return bad(Status.SKIP, f"leg {leg}: notional {q * mark:.6f} below minNotional {min_notional}")
        out.append((q, q * mark))
    (qa, na), (qb, nb) = out
    return ok(ExecutableLegs(qa, qb, na, nb))


def exit_notional_ok(qty: float, exit_price: float, min_notional: float) -> bool:
    """A leg can only be closed by a market order if it still clears the
    minimum notional at the exit price (Stage 2C §9)."""
    return abs(qty) * exit_price >= min_notional


@dataclass(frozen=True)
class KellyEstimate:
    fraction: float       # f* = p − q/b  (may be ≤ 0)
    payoff_ratio: float   # b = average win / average loss


def kelly_fraction(win_rate: float, average_win: float, average_loss: float) -> Result:
    """f* = p − (1−p)/b, b = W̄/L̄ (payoff ratio). Inputs must come from the
    training zone only (caller's responsibility, Stage 2C §14).
    OK(KellyEstimate) when f* > 0, NO_EDGE (value carries the estimate) when
    f* ≤ 0, FAIL for invalid inputs. Never sizes a negative-expectancy rule."""
    p, w, l = win_rate, average_win, average_loss
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (p, w, l)):
        return bad(Status.FAIL, "non-finite Kelly input")
    if not (0.0 < p < 1.0):
        return bad(Status.FAIL, f"win rate {p} must be strictly between 0 and 1")
    if w <= 0 or l <= 0:
        return bad(Status.FAIL, "average win and average loss must both be > 0 (loss as a magnitude)")
    b = w / l
    f = p - (1.0 - p) / b
    est = KellyEstimate(f, b)
    if f <= 0:
        return bad(Status.NO_EDGE, f"Kelly fraction {f:.6f} <= 0: no position", value=est)
    return ok(est)
