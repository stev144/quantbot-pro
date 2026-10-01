# ============================================================
# bot/quant_core/metrics.py
# claude code changed: new file — Stage 2D Phase L, Stage 2C §13.
#
#   * equity is marked to market on the hourly grid, on the ACTUAL capital;
#   * drawdown is computed from that mark-to-market equity;
#   * profit factor is in dollars; no losing trades → "NO_LOSSES" (∞), never 999;
#   * WFE = OOS / IS only when IS is finite and > 0, otherwise FAIL;
#   * a missing or NaN criterion is a FAIL — it can never drop out of a verdict.
#
# The return convention (compounding vs fixed capital) and the Sharpe
# frequency are open owner decisions (Stage 2C §13) and are NOT implemented
# here; callers pass `periods_per_year` explicitly if they compute a Sharpe.
# ============================================================

from __future__ import annotations

import math
import operator
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from bot.quant_core.status import Result, Status, bad, ok
from bot.quant_core.time_grid import assert_complete_grid


@dataclass(frozen=True)
class ProfitFactor:
    value: float          # math.inf when there are no losing trades
    no_losses: bool


def profit_factor_dollars(net_pnl_usdt: Iterable[float]) -> Result:
    pnl = [float(x) for x in net_pnl_usdt]
    if not pnl:
        return bad(Status.UNTESTABLE, "no trades")
    if not all(math.isfinite(x) for x in pnl):
        return bad(Status.FAIL, "non-finite trade P&L")
    wins = sum(x for x in pnl if x > 0)
    losses = -sum(x for x in pnl if x < 0)
    if losses == 0:
        if wins == 0:
            return bad(Status.UNTESTABLE, "no winning and no losing trades")
        return ok(ProfitFactor(math.inf, True))
    return ok(ProfitFactor(wins / losses, False))


def win_rate(net_pnl_usdt: Iterable[float]) -> Result:
    """Share of complete round-trip trades with net dollar P&L > 0."""
    pnl = [float(x) for x in net_pnl_usdt]
    if not pnl:
        return bad(Status.UNTESTABLE, "no trades")
    if not all(math.isfinite(x) for x in pnl):
        return bad(Status.FAIL, "non-finite trade P&L")
    return ok(sum(1 for x in pnl if x > 0) / len(pnl))


def walk_forward_efficiency(oos_metric: float, is_metric: float) -> Result:
    if not (isinstance(oos_metric, (int, float)) and math.isfinite(oos_metric)):
        return bad(Status.FAIL, "OOS metric missing or non-finite")
    if not (isinstance(is_metric, (int, float)) and math.isfinite(is_metric)) or is_metric <= 0:
        return bad(Status.FAIL, f"IS metric {is_metric!r} must be finite and > 0 for WFE to be defined")
    return ok(oos_metric / is_metric)


_OPS: Dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt,
}


@dataclass(frozen=True)
class Criterion:
    name: str
    value: Optional[float]
    op: str
    threshold: float


@dataclass(frozen=True)
class CriterionOutcome:
    name: str
    passed: bool
    reason: str


def evaluate_criteria(criteria: Sequence[Criterion]) -> Tuple[bool, List[CriterionOutcome]]:
    """Every criterion is evaluated; missing/NaN values FAIL. Passes overall
    only if every criterion passes. An empty criteria list does not pass."""
    outcomes = []
    for c in criteria:
        if c.op not in _OPS:
            raise ValueError(f"unknown operator {c.op!r}")
        v = c.value
        if v is None or (isinstance(v, float) and math.isnan(v)):
            outcomes.append(CriterionOutcome(c.name, False, "missing value → FAIL"))
            continue
        passed = bool(_OPS[c.op](v, c.threshold))
        outcomes.append(CriterionOutcome(c.name, passed, f"{v} {c.op} {c.threshold}"))
    return (bool(outcomes) and all(o.passed for o in outcomes)), outcomes


@dataclass(frozen=True)
class HeldInterval:
    """Quantities held over [start, end) (signed units of each leg), the marks
    they were entered at, and the cash costs already paid at entry."""
    start: pd.Timestamp
    end: pd.Timestamp
    qty_a: float
    qty_b: float
    entry_mark_a: float
    entry_mark_b: float


def mark_to_market_equity(capital_usdt: float, close_a: pd.Series, close_b: pd.Series,
                          held: Sequence[HeldInterval],
                          cash_events: Sequence[Tuple[pd.Timestamp, float]]) -> pd.Series:
    """Hourly equity on the grid:
        E_u = capital + Σ cash events at or before u
                      + Σ open intervals q·(close_u − entry_mark)
    `cash_events` are realised amounts (net P&L of each closed slice) and must
    not double count what the open intervals mark. Missing closes propagate as
    NaN (no filling)."""
    if not (math.isfinite(capital_usdt) and capital_usdt > 0):
        raise ValueError("capital must be finite and > 0")
    if not (isinstance(close_a.index, pd.DatetimeIndex) and close_a.index.is_monotonic_increasing
            and close_a.index.is_unique):
        raise ValueError("marks need a strictly increasing DatetimeIndex (any candle width; gaps stay unmarked)")
    if not close_a.index.equals(close_b.index):
        raise ValueError("close_a and close_b must share one grid")
    idx = close_a.index
    equity = pd.Series(float(capital_usdt), index=idx)
    for ts, amount in cash_events:
        if not math.isfinite(amount):
            raise ValueError("non-finite cash event")
        equity[idx >= ts] += amount
    for iv in held:
        mask = (idx >= iv.start) & (idx < iv.end)
        unreal = iv.qty_a * (close_a[mask] - iv.entry_mark_a) + iv.qty_b * (close_b[mask] - iv.entry_mark_b)
        equity[mask] = equity[mask] + unreal
    return equity


def max_drawdown(equity: pd.Series) -> Result:
    """Most negative (equity / running peak − 1) on a mark-to-market series."""
    e = equity.dropna()
    if e.empty:
        return bad(Status.UNTESTABLE, "no equity observations")
    if (e <= 0).any():
        return bad(Status.FAIL, "equity reached zero or below")
    return ok(float((e / e.cummax() - 1.0).min()))
