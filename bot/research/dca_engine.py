# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/dca_engine.py
#
# DCA / MARTINGALE CYCLE RESEARCH ENGINE
#
# claude code changed: new file — 2026-09-26. Built against a real,
# screenshot-verified third-party bot ("TradeFlow," Hybrid DCA Martingale,
# 7 levels, XRP, $6 initial / $762 max deployment) and a 30-phase build
# spec pasted by the user. Per that spec's own Phase 1 ("audit before
# building") and Phase 27 ("do not overengineer the first version"), this
# file is the MINIMAL first slice, not the full spec:
#   - baseline cycle simulator (Phase 3/4)
#   - theoretical ladder math, independently verifiable (Phase 24)
#   - cycle analytics that refuse to let win rate hide tail risk (Phase 8)
#   - a conforming run_strategy_fn for the EXISTING oos_validator.py
#
# Explicitly NOT in this slice (deferred until this slice's results are
# reviewed): regime/support-resistance/Z-score filters (Phase 9-10),
# portfolio-level multi-cycle exposure (Phase 16), paper/live execution
# (Phase 18-19), dashboard (Phase 20), Academy (Phase 21), parameter
# sweeps through research governance (Phase 14-15). Building those now,
# before this slice has produced a real answer to "does this have edge,"
# would be building UI and execution plumbing for a strategy nobody has
# shown works — exactly what the spec's own Phase 27 warns against.
#
# ARCHITECTURAL AUDIT FINDING (the one that matters most): the spec
# assumed a Type B (strategy -> trade-outcome) OOS evaluator needed to be
# built. It doesn't — bot/research/oos_validator.py already has
# evaluate_strategy_oos() (real, leakage-safe, chronological folds,
# purge, warmup context, reuses Backtester's own metric formulas). This
# engine's ONLY job is to produce trade dicts conforming to that
# function's existing contract — no new OOS machinery here.
#
# REUSED, NOT DUPLICATED:
#   - bot.config.execution_costs.FEE_RATE / SLIPPAGE_RATE — the single
#     cost-model source of truth, same one Backtester/OrderManager use.
#   - bot.engines.simulation.apply_slippage() — the exact same
#     entry-pays-more/exit-receives-less convention used everywhere else
#     in this project's fill simulation.
#   - bot.research.oos_validator.evaluate_strategy_oos() — the existing
#     Type B evaluator (see above).
#   - bot.instruments.resolve_ohlcv_path() — the canonical price-file
#     resolver, same one pca_engine.py uses.
#
# NOT REUSED, DELIBERATELY: bot.journal.models.TradeRecord's fee
# calculation is per-single-trade (calc_fees(entry, exit, qty) assumes
# exactly one entry and one exit); a DCA cycle has up to 7 entries and
# one exit, so fees are computed per-fill here using the SAME formula
# (price * quantity * FEE_RATE) calc_fees() itself documents, not a new
# invented formula.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from bot.config.execution_costs import FEE_RATE, SLIPPAGE_RATE
from bot.engines.simulation import apply_slippage
from bot.instruments import resolve_ohlcv_path
from bot.pairs.kalman_online import RollingZScore, ZSCORE_MIN_PERIODS, ZSCORE_WINDOW

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# CONFIG — the observed baseline is DATA, not business logic (Phase 4's explicit requirement)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class DCALevel:
    """One rung of the ladder. drop_pct is measured from the PREVIOUS
    fill's price (sequential), not the original entry — this is the
    reading confirmed against the real TradeFlow screenshots' per-row
    "Drop X%" labels and independently verified (weighted avg came out
    to 59.1%, matching a hand-computed cross-check before any code was
    written). multiplier is relative to the cycle's initial_usdt, matching
    the screenshots' own "x2/x4/x8/x16/x32/x64" labels exactly (6 * 2 =
    12, 6 * 64 = 384 — confirmed, not assumed)."""
    drop_pct: float
    multiplier: float


# claude code changed: Post-audit Phase 2 — fill models and financing states
# (DCA_RESEARCH_PROTOCOL_2026-10-02.md §2, §3).
FILL_MODEL_NEXT_OPEN = "NEXT_OPEN"
FILL_MODEL_INTRABAR_LADDER = "INTRABAR_LADDER"
FILL_MODEL_LEGACY_CLOSE = "LEGACY_CLOSE"
FILL_MODELS = (FILL_MODEL_NEXT_OPEN, FILL_MODEL_INTRABAR_LADDER, FILL_MODEL_LEGACY_CLOSE)

FINANCING_NOT_APPLICABLE = "NOT_APPLICABLE"
FINANCING_KNOWN = "KNOWN"
FINANCING_UNAVAILABLE = "UNAVAILABLE"
FINANCING_ASSUMED_ZERO = "ASSUMED_ZERO"
FINANCING_STATUSES = (FINANCING_NOT_APPLICABLE, FINANCING_KNOWN, FINANCING_UNAVAILABLE, FINANCING_ASSUMED_ZERO)


@dataclass(frozen=True)
class DCAConfig:
    """Every tunable in one place, per the project's own per-engine
    config pattern (cointegration_engine.py, kalman_filter_engine.py,
    pca_engine.py all do this — no shared research config.py exists to
    extend instead)."""

    initial_usdt: float = 6.0
    levels: Tuple[DCALevel, ...] = field(default_factory=tuple)

    # claude code changed: new — "make all longs and shorts possible."
    # LONG: buy more as price DROPS (average down); profit when price
    # RISES above the weighted-average cost basis. SHORT: sell more as
    # price RISES (average into an adverse upward move — FX is inherently
    # two-sided, unlike the crypto-only long baseline this engine started
    # from); profit when price FALLS below the weighted-average basis.
    # Every DCALevel's drop_pct is reinterpreted as "adverse move %" in
    # the direction that hurts THIS position — a rise for SHORT, a fall
    # for LONG — everything else (multiplier ladder, profit/trailing
    # logic, fees) is structurally identical, just mirrored.
    direction: str = "LONG"   # "LONG" | "SHORT"

    profit_activation_pct: float = 0.025   # +2.5% from weighted-average price
    trailing_pct: float = 0.005            # then trail the peak by 0.5%

    fee_rate: float = FEE_RATE
    slippage_rate: float = SLIPPAGE_RATE

    # claude code changed: Phase 6's "optional time-based exit" — the
    # screenshot's real strategy has NO stop-loss and NO time cutoff
    # (confirmed: "No stop-loss" is printed in the app itself). Default
    # None reproduces that faithfully. Setting this is a deliberate
    # RESEARCH deviation from the observed baseline, for stress-testing
    # only — never silently applied.
    max_cycle_candles: Optional[int] = None

    # claude code changed: Post-audit Phase 2 (DCA_RESEARCH_PROTOCOL_2026-10-02.md).
    # fill_model — the clock + ladder-fill convention (§1-§2): NEXT_OPEN
    # (canonical default: decide at close(c), execute at open(c+1), one ladder
    # level per decision), INTRABAR_LADDER (resting limit ladder filled from the
    # candle's low/high — the maximum-exposure bracket), LEGACY_CLOSE (the pre-
    # 2026-10-02 same-close decide-and-fill clock, kept ONLY so earlier results
    # reproduce; not canonical).
    fill_model: str = "NEXT_OPEN"
    # B8 — elapsed-time timeout (hours). max_cycle_candles above is, under the
    # canonical models, read as max_cycle_candles × the bar interval of ELAPSED
    # time (never a row count); max_cycle_hours wins when both are set.
    max_cycle_hours: Optional[float] = None
    # B6 — financing (crypto short borrow / FX swap-rollover). None = derived:
    # LONG → NOT_APPLICABLE, SHORT → UNAVAILABLE. KNOWN requires
    # financing_rate_per_hour (fraction of the cost basis per elapsed hour, a
    # COST: positive = paid). ASSUMED_ZERO is an explicit, labelled opt-in.
    # UNAVAILABLE is never charged AND never presented as a real zero.
    financing_status: Optional[str] = None
    financing_rate_per_hour: Optional[float] = None

    def __post_init__(self):
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError(f"DCAConfig.direction must be 'LONG' or 'SHORT', got {self.direction!r}")
        if self.fill_model not in FILL_MODELS:   # claude code changed: Post-audit Phase 2
            raise ValueError(f"DCAConfig.fill_model must be one of {FILL_MODELS}, got {self.fill_model!r}")
        if self.financing_status is not None and self.financing_status not in FINANCING_STATUSES:
            raise ValueError(f"DCAConfig.financing_status must be one of {FINANCING_STATUSES} or None, got {self.financing_status!r}")
        if self.financing_status == FINANCING_KNOWN and self.financing_rate_per_hour is None:
            raise ValueError("financing_status KNOWN requires financing_rate_per_hour")
        if self.financing_rate_per_hour is not None and self.financing_status != FINANCING_KNOWN:
            raise ValueError("financing_rate_per_hour is only used with financing_status KNOWN — never applied silently")

    @property
    def resolved_financing_status(self) -> str:   # claude code changed: Post-audit Phase 2 B6
        if self.financing_status is not None:
            return self.financing_status
        return FINANCING_NOT_APPLICABLE if self.direction == "LONG" else FINANCING_UNAVAILABLE


# claude code changed: the exact observed baseline — 5/7/10/15/15/10% drops,
# x2/4/8/16/32/64 multipliers, $6 initial. Verified against 4 real
# TradeFlow screenshots (WhatsApp Image 2026-09-26 21.28.26/27), not
# assumed from a description.
BASELINE_LEVELS: Tuple[DCALevel, ...] = (
    DCALevel(drop_pct=0.05, multiplier=2.0),
    DCALevel(drop_pct=0.07, multiplier=4.0),
    DCALevel(drop_pct=0.10, multiplier=8.0),
    DCALevel(drop_pct=0.15, multiplier=16.0),
    DCALevel(drop_pct=0.15, multiplier=32.0),
    DCALevel(drop_pct=0.10, multiplier=64.0),
)

BASELINE_CONFIG = DCAConfig(
    initial_usdt=6.0,
    levels=BASELINE_LEVELS,
    profit_activation_pct=0.025,
    trailing_pct=0.005,
)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# PHASE 24 — THEORETICAL LADDER MATH (pure, no price data — this must be provably
# correct before any simulation over real prices is trusted)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def compute_theoretical_ladder(config: DCAConfig) -> Dict:
    """Pure arithmetic — no market data. Answers "if every level fills
    exactly at its trigger, what does the ladder look like" — the
    numbers a user should see BEFORE ever risking a candle of real
    simulation. Every value here is independently checked by
    bot/tests/test_dca_engine.py against hand-computed reference values.

    claude code changed: real bug caught by the simulator's own test
    suite disagreeing with this function — the two disagreed by ~1.6
    percentage points on an identical fill sequence, which should be
    mathematically impossible if both compute "average entry price"
    correctly. The bug was here: this function originally computed
    sum(investment_i * price_i) / total_investment — a DOLLAR-WEIGHTED
    AVERAGE OF PRICES. That is NOT the same thing as a real average cost
    basis. Confirmed on a toy example: buying $100 at $10 and $100 at $5
    gives sum(100*10 + 100*5)/200 = $7.50 by that formula, but the real
    cost basis — what any exchange actually shows you, total dollars
    spent divided by total units owned — is $200 / (10+20) = $6.67. These
    are genuinely different statistics; only the second is "average entry
    price" in the sense a trader/exchange means it. The simulator's own
    _OpenCycle.weighted_avg_price (total_cost_usdt / total_quantity) was
    already correct; this function was the one that was wrong, and it's
    the same formula an earlier verbal analysis in this project's own
    conversation history repeated without independently re-deriving it.
    Fixed to compute quantity per fill (investment / price) and divide
    total investment by total quantity, matching the simulator exactly.
    """
    investments = [config.initial_usdt] + [
        config.initial_usdt * lvl.multiplier for lvl in config.levels
    ]
    total_deployment = sum(investments)

    # price_path_pct[0] = 100% (entry). Each subsequent level's price is
    # the PREVIOUS level's price reduced by that level's drop_pct —
    # sequential, not cumulative-from-entry (see DCALevel's docstring).
    price_path_pct = [100.0]
    for lvl in config.levels:
        price_path_pct.append(price_path_pct[-1] * (1.0 - lvl.drop_pct))

    total_quantity = sum(inv / p for inv, p in zip(investments, price_path_pct))
    weighted_avg_entry_pct = total_deployment / total_quantity
    bottom_price_pct = price_path_pct[-1]
    profit_target_pct = weighted_avg_entry_pct * (1.0 + config.profit_activation_pct)
    recovery_from_bottom_pct = (profit_target_pct - bottom_price_pct) / bottom_price_pct * 100.0
    recovery_from_original_entry_pct = (100.0 - bottom_price_pct) / bottom_price_pct * 100.0

    return {
        "investments": investments,
        "total_deployment": round(total_deployment, 2),
        "price_path_pct": [round(p, 4) for p in price_path_pct],
        "weighted_avg_entry_pct": round(weighted_avg_entry_pct, 4),
        "bottom_price_pct": round(bottom_price_pct, 4),
        "profit_target_pct": round(profit_target_pct, 4),
        "recovery_needed_from_bottom_pct": round(recovery_from_bottom_pct, 2),
        "recovery_needed_from_original_entry_pct": round(recovery_from_original_entry_pct, 2),
        # claude code changed: the ChatGPT review's own "$6-vs-$762" framing —
        # made a first-class, computed output rather than something a
        # dashboard would have to re-derive.
        "max_level_multiplier": investments[-1] / investments[0],
        "total_deployment_multiplier": total_deployment / investments[0],
        "n_levels": len(config.levels),
    }


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# CYCLE SIMULATOR — candle-by-candle, "always-in" (matches the observed app's
# "cycle mode... run autonomously" behavior: the moment one cycle closes,
# the next opens on the very next candle)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass
class _Fill:
    level: int              # 0 = initial leg, 1..N = DCA level N
    timestamp: object
    price: float            # post-slippage fill price
    usdt_amount: float      # gross notional committed (pre-fee)
    quantity: float
    fee_usdt: float


@dataclass
class _OpenCycle:
    fills: List[_Fill]
    entry_time: object
    profit_activated: bool = False
    peak_price_since_activation: Optional[float] = None
    candles_held: int = 0   # incremented once per loop iteration this cycle is open — avoids repeated index.get_loc() lookups
    # claude code changed: Post-audit Phase 2 — B3/B6/B8 per-cycle state
    max_unrealised_loss: float = 0.0     # most negative MTM P&L seen at any close while open (≤ 0)
    financing_accrued: float = 0.0       # KNOWN financing only
    last_accrual_time: object = None
    gap_count: int = 0
    gap_hours: float = 0.0

    @property
    def next_level_index(self) -> int:
        return len(self.fills) - 1   # fills[0] = initial; next DCA level to check

    @property
    def total_quantity(self) -> float:
        return sum(f.quantity for f in self.fills)

    @property
    def total_cost_usdt(self) -> float:
        return sum(f.usdt_amount for f in self.fills)

    @property
    def total_fees_usdt(self) -> float:
        return sum(f.fee_usdt for f in self.fills)

    @property
    def weighted_avg_price(self) -> float:
        return self.total_cost_usdt / self.total_quantity


def _open_new_cycle(timestamp, price: float, config: DCAConfig) -> _OpenCycle:
    fill_price = apply_slippage(price, config.direction, is_entry=True, slippage_rate=config.slippage_rate)
    quantity = config.initial_usdt / fill_price
    fee = fill_price * quantity * config.fee_rate
    return _OpenCycle(
        fills=[_Fill(level=0, timestamp=timestamp, price=fill_price,
                     usdt_amount=config.initial_usdt, quantity=quantity, fee_usdt=fee)],
        entry_time=timestamp,
    )


def _maybe_trigger_dca(cycle: _OpenCycle, timestamp, price: float, config: DCAConfig) -> None:
    idx = cycle.next_level_index
    if idx >= len(config.levels):
        return   # ladder exhausted — no more ammunition, cycle stays open, unrealized
    level = config.levels[idx]
    last_fill_price = cycle.fills[-1].price
    # claude code changed: LONG triggers on a DROP from the last fill;
    # SHORT triggers on a RISE from the last fill (averaging into an
    # adverse upward move) — same drop_pct field, opposite direction of
    # "adverse" for each side of the market.
    if config.direction == "LONG":
        adverse_move = (last_fill_price - price) / last_fill_price
    else:
        adverse_move = (price - last_fill_price) / last_fill_price
    if adverse_move < level.drop_pct:
        return
    fill_price = apply_slippage(price, config.direction, is_entry=True, slippage_rate=config.slippage_rate)
    usdt_amount = config.initial_usdt * level.multiplier
    quantity = usdt_amount / fill_price
    fee = fill_price * quantity * config.fee_rate
    cycle.fills.append(_Fill(level=idx + 1, timestamp=timestamp, price=fill_price,
                              usdt_amount=usdt_amount, quantity=quantity, fee_usdt=fee))


def _check_exit(cycle: _OpenCycle, price: float, config: DCAConfig) -> bool:
    """Returns True if the trailing-exit condition fires at this candle's
    close. Mutates cycle.profit_activated/peak_price_since_activation —
    this is deliberately stateful across candles, matching the real
    strategy's "activate once, then trail" behavior (re-crossing the
    activation threshold from below a second time does NOT reset the
    trail).

    claude code changed: LONG activates on a RISE above weighted-average
    and trails the PEAK downward; SHORT activates on a FALL below
    weighted-average and trails the TROUGH upward — mirror images of the
    same mechanism. peak_price_since_activation is reused as the tracked
    extreme in both cases (a trough for SHORT), not renamed, to avoid
    touching every other reference to it for a naming-only change."""
    weighted_avg = cycle.weighted_avg_price
    if config.direction == "LONG":
        profit_pct = (price - weighted_avg) / weighted_avg
    else:
        profit_pct = (weighted_avg - price) / weighted_avg

    if not cycle.profit_activated:
        if profit_pct >= config.profit_activation_pct:
            cycle.profit_activated = True
            cycle.peak_price_since_activation = price
        return False

    if config.direction == "LONG":
        cycle.peak_price_since_activation = max(cycle.peak_price_since_activation, price)
        trail_stop_price = cycle.peak_price_since_activation * (1.0 - config.trailing_pct)
        return price <= trail_stop_price
    else:
        cycle.peak_price_since_activation = min(cycle.peak_price_since_activation, price)
        trail_stop_price = cycle.peak_price_since_activation * (1.0 + config.trailing_pct)
        return price >= trail_stop_price


def _close_cycle(cycle: _OpenCycle, timestamp, price: float, config: DCAConfig, exit_reason: str) -> Dict:
    exit_price = apply_slippage(price, config.direction, is_entry=False, slippage_rate=config.slippage_rate)
    quantity = cycle.total_quantity
    weighted_avg = cycle.weighted_avg_price
    exit_fee = exit_price * quantity * config.fee_rate

    # claude code changed: LONG profits when exit > entry (sold higher
    # than bought); SHORT profits when exit < entry (bought back lower
    # than sold) — the standard short-P&L mirror.
    if config.direction == "LONG":
        gross_pnl = (exit_price - weighted_avg) * quantity
    else:
        gross_pnl = (weighted_avg - exit_price) * quantity
    total_fees = cycle.total_fees_usdt + exit_fee
    net_pnl = gross_pnl - total_fees

    max_capital_deployed = cycle.total_cost_usdt
    # claude code changed: deliberate design choice, not the classic
    # stop-loss-distance R-multiple — this strategy has NO stop-loss, so
    # "risk" is defined as the MAXIMUM CAPITAL THE CYCLE ACTUALLY
    # COMMITTED, matching the ChatGPT review's own "Profit-to-Maximum-
    # Exposure" metric and Phase 8's explicit ask for it. Documented here
    # rather than silently reusing the single-stop-loss convention every
    # OTHER strategy in this codebase uses, which would not apply.
    r_multiple = net_pnl / max_capital_deployed if max_capital_deployed else 0.0

    return {
        # ── Required by oos_validator.evaluate_strategy_oos() ──
        "entry_time": cycle.entry_time,
        "exit_time": timestamp,
        # ── Backtester/Trade.to_dict()-compatible keys, for _compute_trade_metrics() ──
        "direction": config.direction,
        "signal": config.direction,
        "entry_price": round(weighted_avg, 8),
        "exit_price": round(exit_price, 8),
        "sl": None,
        "tp": None,
        "quantity": round(quantity, 8),
        "profit": round(net_pnl, 6),
        "gross_profit": round(gross_pnl, 6),
        "r_multiple": round(r_multiple, 6),
        # claude code changed: real bug fix — was len(cycle.fills) (a FILL
        # count, mislabeled as "holding_candles"; cycle_n_fills below
        # already reports that correctly under its real name). This now
        # reuses cycle.candles_held, the actual per-candle counter this
        # class already maintains for the max_cycle_candles check — the
        # real number of candles the position was open, which matters for
        # "how long does this strategy keep capital trapped."
        "holding_candles": cycle.candles_held,
        "rsi": None,
        # ── DCA-specific, additive-only (safe — no existing caller reads an exhaustive key set) ──
        "cycle_max_level_reached": len(cycle.fills) - 1,
        "cycle_max_capital_deployed": round(max_capital_deployed, 2),
        "cycle_fees_total": round(total_fees, 6),
        "cycle_exit_reason": exit_reason,
        "cycle_n_fills": len(cycle.fills),
        "cycle_fills": [
            {"level": f.level, "timestamp": f.timestamp, "price": round(f.price, 8),
             "usdt_amount": f.usdt_amount, "quantity": round(f.quantity, 8), "fee_usdt": round(f.fee_usdt, 6)}
            for f in cycle.fills
        ],
    }


def _bar_interval(index) -> pd.Timedelta:   # claude code changed: new — Post-audit Phase 2 B8
    """The series' modal bar interval Δ. Requires a sorted, unique
    DatetimeIndex — the canonical clock is defined in elapsed time."""
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError("DCA simulation requires a DatetimeIndex (the canonical clock is defined in elapsed time)")
    if not index.is_monotonic_increasing or index.has_duplicates:
        raise ValueError("DCA simulation requires a sorted, duplicate-free DatetimeIndex")
    if len(index) < 2:
        return pd.Timedelta(0)
    return index.to_series().diff().dropna().mode().iloc[0]


def _append_fill(cycle: _OpenCycle, level_idx: int, timestamp, fill_price: float, config: DCAConfig) -> None:   # claude code changed: new — shared by market and resting-limit ladder fills
    usdt_amount = config.initial_usdt * config.levels[level_idx].multiplier
    quantity = usdt_amount / fill_price
    fee = fill_price * quantity * config.fee_rate
    cycle.fills.append(_Fill(level=level_idx + 1, timestamp=timestamp, price=fill_price,
                             usdt_amount=usdt_amount, quantity=quantity, fee_usdt=fee))


def _trigger_price(cycle: _OpenCycle, config: DCAConfig) -> Optional[float]:   # claude code changed: new — Post-audit Phase 2 B1
    """Price at which the NEXT ladder level triggers, relative to the last
    fill (sequential drops — see DCALevel). None when the ladder is exhausted."""
    idx = cycle.next_level_index
    if idx >= len(config.levels):
        return None
    last = cycle.fills[-1].price
    d = config.levels[idx].drop_pct
    return last * (1.0 - d) if config.direction == "LONG" else last * (1.0 + d)


def _level_triggered_at(cycle: _OpenCycle, price: float, config: DCAConfig) -> bool:   # claude code changed: new — same >= rule as _maybe_trigger_dca, decision only
    idx = cycle.next_level_index
    if idx >= len(config.levels):
        return False
    last = cycle.fills[-1].price
    adverse = (last - price) / last if config.direction == "LONG" else (price - last) / last
    return adverse >= config.levels[idx].drop_pct


def _mtm_pnl(cycle: _OpenCycle, mark_price: float, config: DCAConfig) -> float:   # claude code changed: new — Post-audit Phase 2 B3
    """Liquidation-value P&L of an open cycle at mark_price: what closing it
    now would realise (adverse slippage + exit fee), net of entry fees and
    KNOWN financing accrued."""
    liq = apply_slippage(mark_price, config.direction, is_entry=False, slippage_rate=config.slippage_rate)
    qty = cycle.total_quantity
    gross = (liq - cycle.weighted_avg_price) * qty if config.direction == "LONG" else (cycle.weighted_avg_price - liq) * qty
    return gross - cycle.total_fees_usdt - liq * qty * config.fee_rate - cycle.financing_accrued


def _accrue_financing(cycle: _OpenCycle, until, config: DCAConfig) -> None:   # claude code changed: new — Post-audit Phase 2 B6
    if config.resolved_financing_status != FINANCING_KNOWN or cycle.last_accrual_time is None:
        cycle.last_accrual_time = until
        return
    hours = (until - cycle.last_accrual_time).total_seconds() / 3600.0
    if hours > 0:
        cycle.financing_accrued += config.financing_rate_per_hour * cycle.total_cost_usdt * hours
    cycle.last_accrual_time = until


def _finalize_trade(trade: Dict, cycle: _OpenCycle, config: DCAConfig, *, censored: bool,
                    exit_pending: bool = False) -> Dict:   # claude code changed: new — Post-audit Phase 2 labels (B1/B3/B6/B8)
    status = config.resolved_financing_status
    if status == FINANCING_KNOWN and cycle.financing_accrued:
        trade["profit"] = round(trade["profit"] - cycle.financing_accrued, 6)
        dep = trade["cycle_max_capital_deployed"]
        trade["r_multiple"] = round(trade["profit"] / dep, 6) if dep else 0.0
    trade.update({
        "fill_model": config.fill_model,
        "censored": censored,                                   # True = data ended with the cycle open; P&L is a LIQUIDATION MARK, not a realised exit
        "exit_pending_at_data_end": exit_pending,               # an exit was decided at the last close but could not execute
        "holding_hours": round((trade["exit_time"] - cycle.entry_time).total_seconds() / 3600.0, 4),
        "gap_count": cycle.gap_count,
        "gap_hours": round(cycle.gap_hours, 4),
        "financing_status": status,
        "financing_cost_usdt": round(cycle.financing_accrued, 6) if status == FINANCING_KNOWN else None,
        "cycle_max_unrealised_loss": round(cycle.max_unrealised_loss, 6),
    })
    return trade


def _simulate_legacy_close(df: pd.DataFrame, config: DCAConfig) -> Tuple[List[Dict], pd.DataFrame]:
    """claude code changed: the PRE-2026-10-02 clock, logic unchanged — decide
    AND fill at close(c) (zero latency, same print); a new cycle opens at the
    exit close; timeout counts ROWS. Kept only so earlier results reproduce
    (fill_model=LEGACY_CLOSE); not canonical. An MTM row is recorded after
    each candle for the tail metrics."""
    trades: List[Dict] = []
    rows = []
    realised = 0.0
    cycle: Optional[_OpenCycle] = None
    closes = df["close"].to_numpy(dtype=float)
    for timestamp, price in zip(df.index, closes):
        price = float(price)

        if cycle is not None:
            cycle.candles_held += 1
            if _check_exit(cycle, price, config):
                t = _finalize_trade(_close_cycle(cycle, timestamp, price, config, exit_reason="PROFIT_TARGET_TRAILING"), cycle, config, censored=False)
                trades.append(t)
                realised += t["profit"]
                cycle = None
            elif config.max_cycle_candles is not None and cycle.candles_held >= config.max_cycle_candles:
                t = _finalize_trade(_close_cycle(cycle, timestamp, price, config, exit_reason="MAX_CYCLE_CANDLES_TIMEOUT"), cycle, config, censored=False)
                trades.append(t)
                realised += t["profit"]
                cycle = None

        if cycle is None:
            cycle = _open_new_cycle(timestamp, price, config)
        else:
            _maybe_trigger_dca(cycle, timestamp, price, config)

        unreal = _mtm_pnl(cycle, price, config)
        cycle.max_unrealised_loss = min(cycle.max_unrealised_loss, unreal)
        rows.append((timestamp, realised, unreal, realised + unreal, cycle.total_cost_usdt, len(cycle.fills) - 1, True))

    if cycle is not None:
        trades.append(_finalize_trade(_close_cycle(cycle, df.index[-1], float(closes[-1]), config, exit_reason="DATA_END_STILL_OPEN"),
                                      cycle, config, censored=True))
    return trades, _equity_frame(rows)


def _equity_frame(rows) -> pd.DataFrame:   # claude code changed: new — Post-audit Phase 2 B3
    return pd.DataFrame(rows, columns=["timestamp", "realised_pnl", "unrealised_pnl", "mtm_equity_pnl",
                                       "capital_deployed", "ladder_depth", "in_cycle"]).set_index("timestamp")


def simulate_dca_path(df: pd.DataFrame, config: DCAConfig = BASELINE_CONFIG) -> Tuple[List[Dict], pd.DataFrame]:
    """
    claude code changed: new — Post-audit Phase 2 (DCA_RESEARCH_PROTOCOL_2026-10-02.md).
    Returns (cycle trade dicts, per-candle mark-to-market frame).

    CANONICAL CLOCK (NEXT_OPEN / INTRABAR_LADDER): every decision is taken at
    a candle CLOSE from candles <= c only, and executes at the NEXT available
    candle's OPEN (gaps never invent prices); an exit at open(c+1) is followed
    by an entry decided at close(c+1), executed at open(c+2). Profit
    activation / trailing / timeout are evaluated at closes; the timeout is
    ELAPSED time. Ladder levels: NEXT_OPEN — close-confirmed, one level per
    decision; INTRABAR_LADDER — resting limits at the known trigger prices,
    filled from the candle's low (LONG) / high (SHORT), at the open on a gap
    through the trigger, several levels per candle when the range crosses
    them (deterministic on a continuous path). The two models bracket how
    FAST the ladder deploys in a crash — NOT P&L or drawdown (INTRABAR fills
    at the triggers, NEXT_OPEN after a crashed close; on real BTC 2021-26
    INTRABAR showed the smaller drawdown). Neither is called conservative.

    A cycle open when the data ends is CENSORED: its row carries a
    liquidation MARK (labelled, censored=True), never a fabricated fill.

    MTM frame (one row per candle, state at that candle's CLOSE):
    realised_pnl, unrealised_pnl (liquidation value), mtm_equity_pnl,
    capital_deployed, ladder_depth, in_cycle.
    """
    if config.fill_model == FILL_MODEL_LEGACY_CLOSE:
        if "close" not in df.columns:
            raise ValueError("simulate_dca_cycles() requires a 'close' column")
        return _simulate_legacy_close(df, config)

    needed = ["open", "close"] + (["high", "low"] if config.fill_model == FILL_MODEL_INTRABAR_LADDER else [])
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"fill_model {config.fill_model} requires columns {needed}; missing {missing} "
                         f"(no prices are invented — use fill_model='LEGACY_CLOSE' only to reproduce old close-only results)")
    delta = _bar_interval(df.index)
    delta_h = delta.total_seconds() / 3600.0
    limit_hours = config.max_cycle_hours if config.max_cycle_hours is not None else (
        config.max_cycle_candles * delta_h if config.max_cycle_candles is not None else None)
    intrabar = config.fill_model == FILL_MODEL_INTRABAR_LADDER

    ts = df.index
    o = df["open"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float) if intrabar else None
    lo = df["low"].to_numpy(dtype=float) if intrabar else None

    trades: List[Dict] = []
    rows = []
    realised = 0.0
    cycle: Optional[_OpenCycle] = None
    pending: Optional[str] = None          # "OPEN" | "DCA" | "EXIT", decided at the previous close
    pending_reason: Optional[str] = None

    for i in range(len(ts)):
        t_open = ts[i]
        t_close = t_open + delta
        # ── 0. gap accounting for an open cycle (B8) ──
        if cycle is not None and i > 0:
            step = ts[i] - ts[i - 1]
            if step > delta:
                cycle.gap_count += 1
                cycle.gap_hours += (step - delta).total_seconds() / 3600.0
        # ── 1. execute what was decided at the previous close, at this OPEN ──
        if pending == "OPEN":
            cycle = _open_new_cycle(t_open, float(o[i]), config)
            cycle.last_accrual_time = t_open
        elif pending == "DCA":
            fill_price = apply_slippage(float(o[i]), config.direction, is_entry=True, slippage_rate=config.slippage_rate)
            _append_fill(cycle, cycle.next_level_index, t_open, fill_price, config)
        elif pending == "EXIT":
            _accrue_financing(cycle, t_open, config)
            t = _finalize_trade(_close_cycle(cycle, t_open, float(o[i]), config, exit_reason=pending_reason), cycle, config, censored=False)
            trades.append(t)
            realised += t["profit"]
            cycle = None
        pending, pending_reason = None, None

        # ── 2. resting ladder limits fill intrabar (INTRABAR_LADDER only) ──
        if intrabar and cycle is not None:
            while True:
                trig = _trigger_price(cycle, config)
                if trig is None:
                    break
                if config.direction == "LONG":
                    if lo[i] > trig:
                        break
                    fill_price = min(trig, float(o[i]))   # gap through the limit → filled at the (better) open
                else:
                    if h[i] < trig:
                        break
                    fill_price = max(trig, float(o[i]))
                _append_fill(cycle, cycle.next_level_index, t_open, fill_price, config)

        # ── 2b. a fresh cycle's own first candle is held from its open ──
        # ── 3. mark-to-market at this CLOSE, then decide (information <= this candle) ──
        if cycle is not None:
            cycle.candles_held += 1
            _accrue_financing(cycle, t_close, config)
            unreal = _mtm_pnl(cycle, float(c[i]), config)
            cycle.max_unrealised_loss = min(cycle.max_unrealised_loss, unreal)
            rows.append((t_open, realised, unreal, realised + unreal, cycle.total_cost_usdt, len(cycle.fills) - 1, True))
            if _check_exit(cycle, float(c[i]), config):
                pending, pending_reason = "EXIT", "PROFIT_TARGET_TRAILING"
            elif limit_hours is not None and (t_close - cycle.entry_time).total_seconds() / 3600.0 >= limit_hours:
                pending, pending_reason = "EXIT", "MAX_CYCLE_CANDLES_TIMEOUT"
            elif not intrabar and _level_triggered_at(cycle, float(c[i]), config):
                pending = "DCA"
        else:
            rows.append((t_open, realised, 0.0, realised, 0.0, 0, False))
            pending = "OPEN"

    if cycle is not None:
        trades.append(_finalize_trade(
            _close_cycle(cycle, ts[-1], float(c[-1]), config, exit_reason="DATA_END_STILL_OPEN"),
            cycle, config, censored=True, exit_pending=(pending == "EXIT")))
    return trades, _equity_frame(rows)


def simulate_dca_cycles(df: pd.DataFrame, config: DCAConfig = BASELINE_CONFIG) -> List[Dict]:
    """
    Candle-by-candle DCA/Martingale cycle simulator — returns the cycle
    trade dicts only (the oos_validator.evaluate_strategy_oos() contract).
    claude code changed: Post-audit Phase 2 — now a thin wrapper over
    simulate_dca_path(); see that function and
    DCA_RESEARCH_PROTOCOL_2026-10-02.md for the canonical clock, the fill
    models and censoring.

    Supports both config.direction values: LONG (average down on drops,
    profit on a rise) and SHORT (average into a rise, profit on a fall).

    claude code changed: the former docstring called close-only evaluation
    "a conservative (not favorable) bias". That was not demonstrated and is
    not true for exposure: a close-only clock fills at most one level per
    candle, UNDER-stating an intrabar bot's deployment speed in a crash.
    Removed; NEXT_OPEN / INTRABAR_LADDER now bracket the deployment speed
    explicitly (their P&L ordering is empirical, not assumed).
    """
    return simulate_dca_path(df, config)[0]


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# PHASE 8 — CYCLE ANALYTICS THAT REFUSE TO LET WIN RATE HIDE TAIL RISK
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def summarize_dca_cycles(trades: List[Dict], config: DCAConfig = BASELINE_CONFIG) -> Dict:
    """Aggregates simulate_dca_cycles() output into the metric set Phase 8
    explicitly asked for — deliberately reporting worst-case and
    unresolved-cycle figures at least as prominently as win rate, per
    Phase 26 ("no false confidence").

    claude code changed: real bug fix, caught by external review, not by
    this module's own tests (a real test-coverage gap — see the new
    regression tests this fix ships with). config is now a REQUIRED
    context (defaults to BASELINE_CONFIG for existing callers, but every
    caller running a non-baseline config must pass its own) because
    "full ladder exhaustion" must be measured against the CONFIGURED
    number of levels, not against whatever level happened to be the
    deepest one actually reached in this particular trade list. The old
    code (`max(max_level_counts.keys())`) silently mislabeled the deepest
    OBSERVED level as "full exhaustion" — on a dataset where the ladder's
    true final level (e.g. level 6) never once got triggered, every
    level-5 cycle would have been wrongly counted as fully exhausted, even
    though real capital/ammunition remained unused at level 6.
    """
    if not trades:
        return {"n_cycles": 0}

    resolved = [t for t in trades if t["cycle_exit_reason"] == "PROFIT_TARGET_TRAILING"]
    unresolved = [t for t in trades if t["cycle_exit_reason"] != "PROFIT_TARGET_TRAILING"]

    n_cycles = len(trades)
    wins = [t for t in resolved if t["profit"] > 0]
    losses_among_resolved = [t for t in resolved if t["profit"] <= 0]

    profits = [t["profit"] for t in trades]
    sorted_by_profit = sorted(trades, key=lambda t: t["profit"])

    max_level_counts: Dict[int, int] = {}
    for t in trades:
        lvl = t["cycle_max_level_reached"]
        max_level_counts[lvl] = max_level_counts.get(lvl, 0) + 1

    configured_max_level = len(config.levels)   # claude code changed: the real fix — was max(max_level_counts.keys())
    n_exhausted = sum(1 for t in trades if t["cycle_max_level_reached"] == configured_max_level)
    total_max_deployed = max((t["cycle_max_capital_deployed"] for t in trades), default=0.0)
    total_net_profit = sum(profits)

    # claude code changed: real bug fix, also caught by external review —
    # DATA_END_STILL_OPEN ("the dataset ended before we know the outcome")
    # and MAX_CYCLE_CANDLES_TIMEOUT ("our own research rule force-closed
    # it") are economically different events that were only separated in
    # the unresolved_reasons sub-dict, not in the headline metrics anyone
    # skimming the summary would actually look at first.
    unresolved_reasons = {r: sum(1 for t in unresolved if t["cycle_exit_reason"] == r)
                           for r in set(t["cycle_exit_reason"] for t in unresolved)}

    # claude code changed: profit concentration (Phase 8) — what fraction
    # of TOTAL net profit comes from the single best cycle. A number close
    # to 1.0 means "a few lucky cycles are carrying the whole result,"
    # exactly the pattern that makes a high win rate deceptive.
    best_cycle_profit = sorted_by_profit[-1]["profit"] if trades else 0.0
    profit_concentration_top1 = (best_cycle_profit / total_net_profit) if total_net_profit > 0 else None

    return {
        "n_cycles": n_cycles,
        "n_resolved_cycles": len(resolved),
        "n_unresolved_cycles": len(unresolved),   # claude code changed: NEVER silently dropped — see simulate_dca_cycles()
        "n_unresolved_data_end": unresolved_reasons.get("DATA_END_STILL_OPEN", 0),
        "n_unresolved_timeout": unresolved_reasons.get("MAX_CYCLE_CANDLES_TIMEOUT", 0),
        "unresolved_reasons": unresolved_reasons,   # full breakdown retained for any other exit_reason value
        "cycle_win_rate_pct_of_resolved": round(len(wins) / len(resolved) * 100, 2) if resolved else None,
        "avg_resolved_cycle_profit": round(sum(t["profit"] for t in resolved) / len(resolved), 4) if resolved else None,
        "avg_resolved_loss": round(sum(t["profit"] for t in losses_among_resolved) / len(losses_among_resolved), 4) if losses_among_resolved else None,
        "worst_cycle_profit": round(sorted_by_profit[0]["profit"], 4),          # across ALL cycles, incl. unresolved — the real tail
        "best_cycle_profit": round(sorted_by_profit[-1]["profit"], 4),
        "max_capital_deployed_any_cycle": round(total_max_deployed, 2),
        "max_dca_level_distribution": max_level_counts,
        "configured_max_level": configured_max_level,   # claude code changed: new — makes the exhaustion definition auditable, not implicit
        "ladder_full_exhaustion_count": n_exhausted,
        "ladder_full_exhaustion_pct": round(n_exhausted / n_cycles * 100, 2),
        "total_net_profit": round(total_net_profit, 4),
        "total_fees_paid": round(sum(t["cycle_fees_total"] for t in trades), 4),
        "profit_concentration_top1_pct": round(profit_concentration_top1 * 100, 2) if profit_concentration_top1 is not None else None,
        # claude code changed: ChatGPT review's proposed metric, made real —
        # profit per dollar of the worst single-cycle exposure, not per
        # dollar of the (misleadingly small) initial leg.
        "profit_to_max_exposure_ratio": round(total_net_profit / total_max_deployed, 4) if total_max_deployed else None,
        # claude code changed: Post-audit Phase 2 B3 — realised vs censored are
        # NEVER mixed silently. total_net_profit above keeps its historical
        # meaning (= total MARK-TO-MARKET P&L: realised + the censored cycle's
        # liquidation mark) so earlier numbers stay comparable; the split:
        "total_realised_net_profit": round(sum(t["profit"] for t in trades if not _is_censored(t)), 4),
        "censored_mtm_pnl": round(sum(t["profit"] for t in trades if _is_censored(t)), 4),
        "total_mark_to_market_pnl": round(total_net_profit, 4),
        "n_censored_cycles": sum(1 for t in trades if _is_censored(t)),
        "fill_models": sorted({t.get("fill_model", FILL_MODEL_LEGACY_CLOSE) for t in trades}),
        # B6 — cost completeness: False whenever any cycle's financing is UNAVAILABLE / ASSUMED_ZERO
        "financing_statuses": sorted({t.get("financing_status", config.resolved_financing_status) for t in trades}),
        "cost_complete": all(t.get("financing_status", config.resolved_financing_status) in (FINANCING_NOT_APPLICABLE, FINANCING_KNOWN)
                             for t in trades),
    }


def _is_censored(trade: Dict) -> bool:   # claude code changed: new — Post-audit Phase 2 B3
    return bool(trade.get("censored", trade["cycle_exit_reason"] == "DATA_END_STILL_OPEN"))


def summarize_dca_tail_risk(trades: List[Dict], equity: pd.DataFrame, config: DCAConfig = BASELINE_CONFIG) -> Dict:
    """claude code changed: new — Post-audit Phase 2 B3. The risk a
    martingale ladder actually carries — capital trapped while unrealised
    loss grows — from the per-candle mark-to-market frame of
    simulate_dca_path(). Win rate and realised profit cannot show it."""
    if equity.empty:
        return {"available": False, "reason": "no candles"}
    eq = equity["mtm_equity_pnl"]
    running_peak = eq.cummax().clip(lower=0.0)           # equity starts at 0 P&L
    drawdown = running_peak - eq
    full_ladder = compute_theoretical_ladder(config)["total_deployment"]
    # longest underwater spell (equity below its prior peak), in elapsed hours
    under = drawdown > 1e-12
    longest_h, start, unrecovered = 0.0, None, False
    for ts_, u in under.items():
        if u and start is None:
            start = ts_
        elif not u and start is not None:
            longest_h = max(longest_h, (ts_ - start).total_seconds() / 3600.0)
            start = None
    if start is not None:
        unrecovered = True
        longest_h = max(longest_h, (eq.index[-1] - start).total_seconds() / 3600.0)
    exhausted = sum(1 for t in trades if t["cycle_max_level_reached"] == len(config.levels))
    censored = [t for t in trades if _is_censored(t)]
    return {
        "available": True,
        "fill_model": config.fill_model,
        "max_capital_deployed": round(float(equity["capital_deployed"].max()), 2),
        "theoretical_full_ladder_deployment": full_ladder,
        "max_ladder_depth": int(equity["ladder_depth"].max()),
        "configured_max_level": len(config.levels),
        "ladder_full_exhaustion_count": exhausted,
        "max_unrealised_loss": round(float(equity["unrealised_pnl"].min()), 4),
        "worst_open_cycle_loss": round(min((t.get("cycle_max_unrealised_loss", 0.0) for t in trades), default=0.0), 4),
        "max_mtm_drawdown_usd": round(float(drawdown.max()), 4),
        "max_mtm_drawdown_pct_of_full_ladder": round(float(drawdown.max()) / full_ladder * 100, 2) if full_ladder else None,
        "time_in_cycle_pct": round(float(equity["in_cycle"].mean()) * 100, 2),
        "longest_cycle_hours": round(max((t.get("holding_hours", 0.0) for t in trades), default=0.0), 2),
        "longest_underwater_hours": round(longest_h, 2),
        "underwater_unrecovered_at_data_end": unrecovered,
        "final_mtm_equity_pnl": round(float(eq.iloc[-1]), 4),
        "censored_cycles": [{"entry_time": t["entry_time"], "mark_pnl": t["profit"], "level_reached": t["cycle_max_level_reached"],
                             "holding_hours": t.get("holding_hours"), "exit_pending_at_data_end": t.get("exit_pending_at_data_end", False)}
                            for t in censored],
    }


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# GENUINE-EDGE TEST — block-permutation on the price path itself
#
# claude code changed: new. The single most important test this engine
# runs. A DCA/Martingale ladder can look highly profitable on ANY
# sufficiently-choppy-but-bounded price series purely from bet-sizing
# mechanics (buying more as price falls manufactures a high win rate
# regardless of whether the asset has any real mean-reverting structure)
# — see this project's own conversation history on why martingale's win
# rate is not evidence of edge. The only way to tell "this works because
# XRP has real structure" from "this works because martingale sizing
# does this on almost any volatile-but-range-bound crypto path" is to
# destroy the real path's structure and see if performance survives.
#
# REUSED, NOT DUPLICATED: the block-shuffle-then-compare METHODOLOGY is
# the same one already established twice in this codebase
# (permutation_test_engine.py's _block_shuffle() for Kalman spread
# columns; feature_validator.py's _block_shuffle_1d() for a single
# feature array) and the identical empirical p-value formula both already
# use: (extreme_count + 1) / (n_permutations + 1) — never exactly zero,
# since the real result is itself one possible draw. NEITHER existing
# implementation's internals fit here directly: one is hard-wired to
# Kalman-specific columns, the other is a private instance method shaped
# for a feature-vs-forward-return test, not a full strategy re-simulation
# per shuffle. This is a fresh, minimal implementation of the SAME
# principle for DCA's actual need: shuffle blocks of LOG-RETURNS (not raw
# price levels — shuffling price levels directly would create artificial
# discontinuous jumps at block boundaries), reconstruct a continuous
# synthetic price path from the shuffled returns, and re-run the exact
# same simulate_dca_cycles() on it.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

DEFAULT_PERMUTATION_BLOCK_CANDLES = 24   # 1 day of hourly candles — long enough to preserve real intraday volatility clustering, short enough for a rich shuffle distribution over a multi-year series
# claude code changed: Post-audit Phase 2 B4 — was 100 (resolution 1/101). The
# smallest attainable empirical p is 1/(B+1); below MIN_PERMUTATIONS_FOR_CLAIM
# the assessment is INSUFFICIENT_RESOLUTION and no claim may be made.
DEFAULT_N_PERMUTATIONS = 999
MIN_PERMUTATIONS_FOR_CLAIM = 999
DEFAULT_SENSITIVITY_BLOCK_SIZES = (24, 168, 720)   # 1 day, 1 week, ~1 month of 1h candles

# claude code changed: Post-audit Phase 2 B4 — the two canonical nulls
# (DCA_RESEARCH_PROTOCOL_2026-10-02.md §4).
NULL_BLOCK_SHUFFLE = "BLOCK_SHUFFLE"
NULL_DRIFT_REMOVED = "DRIFT_REMOVED_BLOCK_SHUFFLE"
NULL_MODELS = (NULL_BLOCK_SHUFFLE, NULL_DRIFT_REMOVED)
NULL_MODEL_SEMANTICS = {
    NULL_BLOCK_SHUFFLE: "preserves drift, start/end price, candle shapes and block-local structure; destroys ordering beyond "
                        "the block length — tests PATH DEPENDENCE given the real drift",
    NULL_DRIFT_REMOVED: "preserves candle shapes, volatility and block-local structure; removes drift (terminal price = start) "
                        "— tests whether the strategy makes money WITHOUT the drift",
}


def _block_shuffle_returns(returns: "np.ndarray", block_size: int, rng: "np.random.Generator") -> "np.ndarray":
    """Chops log-returns into contiguous blocks and reassembles them in a
    randomly shuffled order — same principle as this project's other two
    block-shuffle implementations, applied here to log-returns so the
    shuffled result can be turned back into a valid, continuous price
    path (see _reconstruct_price_path). claude code changed: also accepts a
    2-D (n, k) array — rows are shuffled in blocks (Post-audit Phase 2 B4)."""
    n = len(returns)
    n_blocks = int(np.ceil(n / block_size))
    blocks = [returns[i * block_size:(i + 1) * block_size] for i in range(n_blocks)]
    order = rng.permutation(n_blocks)
    shuffled = np.concatenate([blocks[i] for i in order])
    return shuffled[:n]


def _reconstruct_price_path(initial_price: float, log_returns: "np.ndarray") -> "np.ndarray":
    """Cumulative-compounds a log-return series back into a price path
    starting from initial_price — the standard, only-correct way to turn
    a shuffled RETURN series back into something simulate_dca_cycles()
    can run on."""
    log_prices = np.log(initial_price) + np.cumsum(log_returns)
    return np.exp(log_prices)


def _synthetic_path(df: pd.DataFrame, block_size: int, rng: "np.random.Generator", null_model: str) -> pd.DataFrame:
    """claude code changed: new — Post-audit Phase 2 B4. One null draw.
    Each candle i >= 1 is the tuple log(open, high, low, close / previous
    close) (close only when the frame has no OHLC), so block-shuffling rows
    keeps every candle's intrabar shape and opening gap. DRIFT_REMOVED
    subtracts the mean close-to-close log return μ from all four entries
    of every candle (a level shift of the whole candle), so the synthetic
    terminal price equals the start. Candle 0 is the real candle; the real
    timestamps are reused (gap positions are kept; the candles that follow
    them are shuffled like any other)."""
    if null_model not in NULL_MODELS:
        raise ValueError(f"null_model must be one of {NULL_MODELS}, got {null_model!r}")
    close = df["close"].to_numpy(dtype=float)
    prev = close[:-1]
    has_ohlc = all(col in df.columns for col in ("open", "high", "low"))
    cols = [np.log(close[1:] / prev)]
    if has_ohlc:
        cols = [np.log(df[k].to_numpy(dtype=float)[1:] / prev) for k in ("open", "high", "low")] + cols
    x = np.column_stack(cols)
    if null_model == NULL_DRIFT_REMOVED:
        x = x - x[:, -1].mean()
    xs = _block_shuffle_returns(x, block_size, rng)
    new_close = np.concatenate([[close[0]], _reconstruct_price_path(close[0], xs[:, -1])])
    out = {"close": new_close}
    if has_ohlc:
        new_prev = new_close[:-1]
        for j, k in enumerate(("open", "high", "low")):
            out[k] = np.concatenate([[float(df[k].iloc[0])], new_prev * np.exp(xs[:, j])])
    return pd.DataFrame(out, index=df.index)


def _mtm_statistics(df: pd.DataFrame, config: DCAConfig) -> Tuple[float, float]:   # claude code changed: new — the two tested statistics
    s = summarize_dca_cycles(simulate_dca_cycles(df, config), config)
    mtm = s.get("total_mark_to_market_pnl", 0.0) if s.get("n_cycles") else 0.0
    dep = s.get("max_capital_deployed_any_cycle") or 0.0
    return mtm, (mtm / dep if dep else 0.0)


def run_dca_permutation_test(
    df: pd.DataFrame,
    config: DCAConfig = BASELINE_CONFIG,
    n_permutations: int = DEFAULT_N_PERMUTATIONS,
    block_size: int = DEFAULT_PERMUTATION_BLOCK_CANDLES,
    seed: int = 42,
    null_model: str = NULL_BLOCK_SHUFFLE,
    alpha: float = 0.05,
) -> Dict:
    """
    claude code changed: Post-audit Phase 2 B4 (rewritten). One null, one
    block size. Tested statistics (both required — an intersection-union
    test, valid at alpha across these two without further correction):
    total MARK-TO-MARKET P&L (realised + censored liquidation mark — a
    realised-only statistic would reward hiding losses in an open cycle)
    and MTM P&L / max capital deployed.

    WHAT THE FORMER TEST PRESERVED (still available as null_model=
    BLOCK_SHUFFLE): shuffling log returns leaves their sum unchanged, so the
    start AND end price — the drift — are preserved exactly; only ordering
    beyond the block length is destroyed. It cannot attribute profit to
    drift. DRIFT_REMOVED_BLOCK_SHUFFLE removes it. Use run_dca_null_suite()
    for both nulls across block sizes.

    raw_assessment is UNCORRECTED for the caller's family (symbols, configs,
    nulls, block sizes): never "significant" on its own. B below
    MIN_PERMUTATIONS_FOR_CLAIM → INSUFFICIENT_RESOLUTION.
    """
    real_mtm, real_ratio = _mtm_statistics(df, config)
    rng = np.random.default_rng(seed)
    sh_mtm, sh_ratio = [], []
    for i in range(n_permutations):
        m, r = _mtm_statistics(_synthetic_path(df, block_size, rng, null_model), config)
        sh_mtm.append(m)
        sh_ratio.append(r)
        if (i + 1) % 100 == 0:
            logger.info(f"  DCA permutation test [{null_model}, block {block_size}]: {i + 1}/{n_permutations}")
    # empirical p-value, this project's convention: (k + 1)/(B + 1) — never exactly zero
    p_mtm = (sum(1 for v in sh_mtm if v >= real_mtm) + 1) / (n_permutations + 1)
    p_ratio = (sum(1 for v in sh_ratio if v >= real_ratio) + 1) / (n_permutations + 1)
    if n_permutations < MIN_PERMUTATIONS_FOR_CLAIM:
        assessment = "INSUFFICIENT_RESOLUTION"
    elif p_mtm < alpha and p_ratio < alpha:
        assessment = "REJECTS_NULL_UNCORRECTED"
    else:
        assessment = "DOES_NOT_REJECT_NULL"
    return {
        "null_model": null_model,
        "null_semantics": NULL_MODEL_SEMANTICS[null_model],
        "n_permutations": n_permutations,
        "p_value_resolution": round(1.0 / (n_permutations + 1), 6),
        "block_size_candles": block_size,
        "seed": seed,
        "fill_model": config.fill_model,
        "real_total_mtm_pnl": round(real_mtm, 4),
        "shuffled_mtm_mean": round(float(np.mean(sh_mtm)), 4) if sh_mtm else None,
        "shuffled_mtm_std": round(float(np.std(sh_mtm)), 4) if sh_mtm else None,
        "p_value_mtm_pnl": round(p_mtm, 6),
        "real_mtm_to_max_exposure_ratio": round(real_ratio, 6),
        "p_value_mtm_to_max_exposure_ratio": round(p_ratio, 6),
        "alpha": alpha,
        "raw_assessment": assessment,
    }


def run_dca_null_suite(
    df: pd.DataFrame,
    config: DCAConfig = BASELINE_CONFIG,
    null_models: Tuple[str, ...] = NULL_MODELS,
    block_sizes: Tuple[int, ...] = DEFAULT_SENSITIVITY_BLOCK_SIZES,
    n_permutations: int = DEFAULT_N_PERMUTATIONS,
    seed: int = 42,
    alpha: float = 0.05,
) -> Dict:
    """claude code changed: new — Post-audit Phase 2 B4. Both nulls × every
    block size. Within this suite the family is len(null_models) ×
    len(block_sizes) tests; each p-value is also reported Bonferroni-adjusted
    for it. Readings per block size use the ADJUSTED p-values (both
    statistics): drift-preserving rejected / drift-removed rejected →
      no / yes  PROFIT_EXPLAINED_BY_DRIFT
      yes / yes CONSISTENT_WITH_PATH_STRUCTURE
      no / no   NO_EVIDENCE
      yes / no  INCONSISTENT_REVIEW
    robust_across_block_sizes is True only when every block size gives the
    same reading. A caller testing several symbols/configs must correct
    across them as well — this suite does not know that family."""
    family = len(null_models) * len(block_sizes)
    rows = []
    for k, bs in enumerate(block_sizes):
        for j, nm in enumerate(null_models):
            r = run_dca_permutation_test(df, config, n_permutations=n_permutations, block_size=bs,
                                         seed=seed + 1000 * k + j, null_model=nm, alpha=alpha)
            r["p_value_mtm_pnl_bonferroni"] = min(1.0, r["p_value_mtm_pnl"] * family)
            r["p_value_ratio_bonferroni"] = min(1.0, r["p_value_mtm_to_max_exposure_ratio"] * family)
            rows.append(r)
    readings = {}
    if n_permutations < MIN_PERMUTATIONS_FOR_CLAIM:
        readings = {bs: "INSUFFICIENT_RESOLUTION" for bs in block_sizes}
    elif set(null_models) == set(NULL_MODELS):
        for bs in block_sizes:
            rej = {r["null_model"]: (r["p_value_mtm_pnl_bonferroni"] < alpha and r["p_value_ratio_bonferroni"] < alpha)
                   for r in rows if r["block_size_candles"] == bs}
            s, d = rej[NULL_BLOCK_SHUFFLE], rej[NULL_DRIFT_REMOVED]
            readings[bs] = {(False, True): "PROFIT_EXPLAINED_BY_DRIFT", (True, True): "CONSISTENT_WITH_PATH_STRUCTURE",
                            (False, False): "NO_EVIDENCE", (True, False): "INCONSISTENT_REVIEW"}[(s, d)]
    return {
        "family_size_within_suite": family,
        "results": rows,
        "reading_by_block_size": readings,
        "robust_across_block_sizes": len(set(readings.values())) == 1 if readings else False,
        "note": "raw evidence only — correct across symbols/configs before any claim; never 'significant' on this alone",
    }


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# INTELLIGENT-DCA RESEARCH, EXPERIMENT 1 — Z-SCORE
#
# claude code changed: new. Purely OBSERVATIONAL research, per the pasted
# spec's own Section 9: "Do not assume Z-score < -2 = buy. Instead
# investigate the conditional relationship." This does NOT gate, block,
# or resize any DCA decision — the blind baseline (BASELINE_CONFIG,
# simulate_dca_cycles()) is untouched and unmodified, exactly as the
# spec's Section 2 requires. This only ANSWERS: at the moment each DCA
# level actually fired in the real historical backtest, what was the
# rolling Z-score of price, and did that value carry any information
# about how the cycle eventually turned out?
#
# REUSED, NOT DUPLICATED: bot.pairs.kalman_online.RollingZScore — the
# same class already reused by pca_engine.py this session, same
# ZSCORE_WINDOW (504 candles / 3 weeks of 1h data) the Kalman pairs pilot
# itself uses. No new Z-score implementation.
#
# NO LOOK-AHEAD (Section 12): RollingZScore is inherently causal — each
# candle's Z-score is computed only from that candle and everything
# before it (deque-based trailing window), so a Z-score value that
# appears at fill timestamp T never uses information from T+1 onward.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def compute_price_zscore_series(
    df: pd.DataFrame,
    window: int = ZSCORE_WINDOW,
    min_periods: int = ZSCORE_MIN_PERIODS,
) -> pd.Series:
    """Rolling Z-score of log(close), via the existing RollingZScore class.
    Returns a Series aligned to df's index; None (converted to NaN) for
    any candle before min_periods have accumulated — never fabricated."""
    roller = RollingZScore(window=window, min_periods=min_periods)
    log_price = np.log(df["close"])
    values = [roller.update(v) for v in log_price.values]
    return pd.Series(values, index=df.index, dtype=float)


def research_feature_at_dca_events(
    df: pd.DataFrame,
    config: DCAConfig,
    feature_series: pd.Series,
    feature_col_name: str,
) -> pd.DataFrame:
    """
    Generic version of Experiment 1's event-extraction logic — pulled out
    once a second feature family (ATR/volatility, Experiment 2) needed
    the identical join, rather than copy-pasting it a second time. Runs
    the UNMODIFIED blind baseline (simulate_dca_cycles), then for every
    DCA-level fill (level >= 1 — deliberately excluding the initial entry
    at level 0, since the research question is specifically about
    moments where price has ALREADY moved against an open position)
    across every cycle, records feature_series's value at that exact
    instant alongside the eventual cycle outcome.

    feature_series must already be computed causally (no look-ahead) —
    this function only performs the timestamp lookup and join; it has no
    way to detect a feature that peeked at future candles, so that
    responsibility sits with whichever compute_*_series() function built
    feature_series (see compute_price_zscore_series/compute_atr_ratio_series
    for how each satisfies it).

    Returns one row per DCA-level fill, column named feature_col_name for
    the feature's value, plus:
      - cycle_final_profit / cycle_exit_reason / cycle_final_level_reached:
        how the CYCLE THIS FILL BELONGS TO eventually turned out (a fill
        at level 2 in a cycle that later reached level 6 and lost money
        is tagged with that cycle's real, eventual outcome — not a
        fabricated per-fill P&L, which doesn't exist for an intermediate
        DCA leg on its own)
      - levels_remaining_at_fill: how much ladder capacity was left when
        this fill happened
      - hours_from_fill_to_resolution: real elapsed time from this fill
        to the cycle's eventual close (or data-end if still open)

    Multiple rows can belong to the same cycle (a cycle that reached
    level 4 contributes 4 rows, one per DCA level, all sharing the same
    eventual outcome) — deliberate, see Experiment 1's original docstring
    for why.
    """
    trades = simulate_dca_cycles(df, config)

    rows = []
    for trade in trades:
        for fill in trade["cycle_fills"]:
            if fill["level"] == 0:
                continue   # initial entry, not a DCA event
            ts = fill["timestamp"]
            feature_value = feature_series.get(ts)
            hours_to_resolution = (trade["exit_time"] - ts).total_seconds() / 3600.0
            rows.append({
                "cycle_entry_time": trade["entry_time"],   # real, unique per-cycle identifier — see summarize_feature_experiment()'s de-duplication
                "fill_timestamp": ts,
                "fill_level": fill["level"],
                "fill_price": fill["price"],
                feature_col_name: feature_value,
                "cycle_final_profit": trade["profit"],
                "cycle_exit_reason": trade["cycle_exit_reason"],
                "cycle_final_level_reached": trade["cycle_max_level_reached"],
                "levels_remaining_at_fill": len(config.levels) - fill["level"],
                "hours_from_fill_to_resolution": hours_to_resolution,
            })
    return pd.DataFrame(rows)


def research_zscore_at_dca_events(df: pd.DataFrame, config: DCAConfig = BASELINE_CONFIG) -> pd.DataFrame:
    """EXPERIMENT 1 (Z-score) — thin wrapper over research_feature_at_dca_events()."""
    return research_feature_at_dca_events(df, config, compute_price_zscore_series(df), "zscore_at_fill")


def summarize_feature_experiment(
    events_df: pd.DataFrame,
    feature_col: str,
    bucket_fn: Callable[[float], str],
    bucket_col_name: str = "feature_bucket",
    configured_max_level: Optional[int] = None,   # claude code changed: Post-audit Phase 2 B7
) -> pd.DataFrame:
    """
    Generic version of Experiment 1's bucket-and-summarize logic. Buckets
    DCA events by feature_col's value (via caller-supplied bucket_fn —
    each feature family gets its own economically-sensible regions, not
    one arbitrary threshold; see Section 9's "very negative / moderately
    negative / near equilibrium" for Z-score, and the ATR-specific
    buckets in summarize_atr_experiment for why they differ) and reports
    the conditional outcome distribution per bucket. A monotonic pattern
    (deeper feature value -> consistently better or worse outcome) is
    interesting; a flat/noisy pattern means the feature isn't carrying
    information for this decision.
    """
    if events_df.empty:
        return pd.DataFrame()

    # claude code changed: Post-audit Phase 2 B7 — "full exhaustion" means the
    # CONFIGURED final level, never the deepest level a bucket happened to
    # observe (was: each bucket's own .max(), so a bucket whose deepest cycle
    # reached level 3 of 6 reported every level-3 cycle as "fully
    # exhausted"). Every event row carries fill_level + levels_remaining_at_fill
    # = the configured level count (research_feature_at_dca_events); it must
    # be one value and agree with configured_max_level when given.
    derived = set((events_df["fill_level"] + events_df["levels_remaining_at_fill"]).astype(int))
    if len(derived) != 1:
        raise ValueError(f"events mix ladder definitions (configured level counts {sorted(derived)}) — summarize each config separately")
    n_levels = derived.pop()
    if configured_max_level is not None and configured_max_level != n_levels:
        raise ValueError(f"configured_max_level={configured_max_level} disagrees with the events' ladder ({n_levels} levels)")

    events_df = events_df.copy()
    events_df[bucket_col_name] = events_df[feature_col].apply(bucket_fn)

    # claude code changed: "eventual cycle outcome" per fill-event needs
    # de-duplication by cycle before averaging profit/exhaustion —
    # otherwise a single 6-level-exhaustion cycle contributes 6 identical
    # copies of its own outcome, silently overweighting it relative to a
    # 1-level cycle. Grouped on cycle_entry_time, the real unique-per-
    # cycle identifier (see research_feature_at_dca_events()).
    summary_rows = []
    for bucket_name, group in events_df.groupby(bucket_col_name):
        cycles_touched = group.drop_duplicates(subset=["cycle_entry_time"])
        summary_rows.append({
            bucket_col_name: bucket_name,
            "n_dca_events": len(group),
            "n_distinct_cycles_touched": len(cycles_touched),
            "eventual_ladder_full_exhaustion_rate_pct": round(
                (cycles_touched["cycle_final_level_reached"] == n_levels).mean() * 100, 2   # claude code changed: B7 — configured, was the bucket's observed .max()
            ) if len(cycles_touched) else None,
            "configured_max_level": n_levels,   # claude code changed: B7 — the denominator, auditable
            "mean_eventual_cycle_profit": round(cycles_touched["cycle_final_profit"].mean(), 4) if len(cycles_touched) else None,
            "mean_hours_to_resolution": round(group["hours_from_fill_to_resolution"].mean(), 1),
        })
    return pd.DataFrame(summary_rows)


def _zscore_bucket(z: float) -> str:
    if pd.isna(z):
        return "no_zscore_yet"
    if z <= -3.0:
        return "very_negative (<= -3.0)"
    if z <= -1.5:
        return "moderately_negative (-3.0, -1.5]"
    return "near_equilibrium (> -1.5)"


def summarize_zscore_experiment(events_df: pd.DataFrame) -> pd.DataFrame:
    """EXPERIMENT 1 (Z-score) — thin wrapper over summarize_feature_experiment()."""
    return summarize_feature_experiment(events_df, "zscore_at_fill", _zscore_bucket, "zscore_bucket")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# INTELLIGENT-DCA RESEARCH, EXPERIMENT 2 — VOLATILITY / ATR
#
# claude code changed: new. Same observational discipline as Experiment 1
# (Section 7: "do not assume volatility-adjusted spacing is better,
# measure it" — this experiment only measures the conditional
# relationship; it does not modify DCA spacing/sizing/gating).
#
# REUSED, NOT DUPLICATED: bot.engines.regime_detector.RegimeDetector
# already computes an ATR ratio (current 14-period ATR / 50-period
# average ATR) internally, via _calculate_atr_ratio() — but that method
# is written to return only the CURRENT (last-row) value, meant to be
# called once per live candle in bot_runner.py's loop, not a full
# historical series. Rather than call it 43,797 times per asset (a slow
# Python loop for what's already a vectorized pandas computation
# underneath), compute_atr_ratio_series() below reuses the EXACT SAME
# formula and default periods (atr_period=14, atr_avg_period=50,
# matching RegimeDetector.__init__'s own defaults) as a single vectorized
# pass over the whole series — same math, not a second ATR
# implementation with independently-drifting constants.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

DEFAULT_ATR_PERIOD = 14
DEFAULT_ATR_AVG_PERIOD = 50
# claude code changed: matches RegimeDetector's own atr_extreme_multiplier
# default exactly — reused as the "extreme volatility" bucket boundary
# below, not an independently chosen number.
ATR_EXTREME_MULTIPLIER = 1.7


def compute_atr_ratio_series(
    df: pd.DataFrame,
    atr_period: int = DEFAULT_ATR_PERIOD,
    atr_avg_period: int = DEFAULT_ATR_AVG_PERIOD,
) -> pd.Series:
    """
    Vectorized whole-series version of RegimeDetector._calculate_atr_ratio()'s
    exact formula (True Range -> rolling ATR -> ratio to its own longer-
    period rolling average). Causal by construction (every value at row i
    uses only rows <= i via pandas' backward-looking .rolling()) — no
    look-ahead, matching Section 12's requirement. NaN for the first
    atr_avg_period rows, same as the source method's own guard (never
    fabricated as a fake "neutral" 1.0 the way the live, single-value
    method does for a genuinely missing baseline — that fallback exists
    there to keep a live trading loop from crashing on cold start, which
    doesn't apply to offline research over full historical series).
    """
    high, low, prev_close = df["high"], df["low"], df["close"].shift(1)
    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    current_atr = true_range.rolling(atr_period).mean()
    avg_atr = true_range.rolling(atr_avg_period).mean()
    ratio = current_atr / avg_atr.replace(0, np.nan)
    return ratio


def research_atr_ratio_at_dca_events(df: pd.DataFrame, config: DCAConfig = BASELINE_CONFIG) -> pd.DataFrame:
    """EXPERIMENT 2 (volatility/ATR) — thin wrapper over research_feature_at_dca_events()."""
    return research_feature_at_dca_events(df, config, compute_atr_ratio_series(df), "atr_ratio_at_fill")


def _atr_ratio_bucket(ratio: float) -> str:
    if pd.isna(ratio):
        return "no_atr_yet"
    if ratio < 0.8:
        return "low_vol (< 0.8x)"
    if ratio < 1.3:
        return "normal_vol (0.8-1.3x)"
    if ratio < ATR_EXTREME_MULTIPLIER:
        return f"elevated_vol (1.3-{ATR_EXTREME_MULTIPLIER}x)"
    return f"extreme_vol (>= {ATR_EXTREME_MULTIPLIER}x)"


def summarize_atr_experiment(events_df: pd.DataFrame) -> pd.DataFrame:
    """EXPERIMENT 2 (volatility/ATR) — thin wrapper over summarize_feature_experiment()."""
    return summarize_feature_experiment(events_df, "atr_ratio_at_fill", _atr_ratio_bucket, "atr_ratio_bucket")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# OOS INTEGRATION — a conforming run_strategy_fn for the EXISTING evaluate_strategy_oos(),
# no new OOS machinery
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def make_run_strategy_fn(config: DCAConfig = BASELINE_CONFIG) -> Callable[[pd.DataFrame, Dict], List[Dict]]:
    """Returns a closure matching oos_validator.evaluate_strategy_oos()'s
    run_strategy_fn(df_slice, fitted_params) -> List[trade_dict] contract
    exactly. fitted_params is unused — like MeanReversionStrategy (per
    that function's own docstring), this baseline has no data-fitted
    parameters, so fit_fn=None is the correct call at the use site."""
    def run_strategy_fn(df_slice: pd.DataFrame, fitted_params: Dict) -> List[Dict]:
        return simulate_dca_cycles(df_slice, config)
    return run_strategy_fn


def load_close_series_for_oos(canonical_symbol: str) -> pd.DataFrame:
    """Loads one symbol's OHLCV into the DatetimeIndex + 'close' shape
    evaluate_strategy_oos()/simulate_dca_cycles() both require — via
    resolve_ohlcv_path(), the same canonical resolver pca_engine.py uses,
    not a hand-built path string. Works for FOREX symbols too (e.g.
    'EUR/USD') — resolve_ohlcv_path() already branches on asset class
    internally; nothing here is crypto-specific."""
    path = resolve_ohlcv_path(canonical_symbol)
    if not path.exists():
        raise FileNotFoundError(f"No price data for {canonical_symbol} at {path}")
    df = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
    return df[~df.index.duplicated(keep="first")]


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# FX COST MODEL INTEGRATION — reuses the EXISTING bot.config.cost_model.ForexCostModel,
# no new/second cost system
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# VOLATILITY-SCALED THRESHOLDS — the observed 5/7/10/15/15/10% ladder was
# calibrated for crypto and is nearly dormant on FX (verified: EUR/USD
# produced only 7 cycles vs. crypto's 190-585 over the identical 5-year
# window, max deployment only $42 of $762). Scaling every percentage
# threshold (DCA drops, profit activation, trailing) by the REAL,
# measured ratio of hourly volatility between the two asset classes —
# not a guessed round number — so the ladder engages with FX at a
# comparable relative frequency to how it engages with crypto.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def _vol_ratio_on_window(ref_close: pd.Series, target_close: pd.Series, start=None, end=None) -> float:
    """claude code changed: new — Post-audit Phase 2 B5. target / reference
    hourly log-return std using ONLY rows in [start, end) (end exclusive;
    start None = from the beginning). Raises when a window holds too little
    data to estimate a standard deviation."""
    def window(s):
        s = s.sort_index()
        if start is not None:
            s = s[s.index >= pd.Timestamp(start)]
        if end is not None:
            s = s[s.index < pd.Timestamp(end)]
        return np.log(s).diff().dropna()
    r, g = window(ref_close), window(target_close)
    if len(r) < 30 or len(g) < 30:
        raise ValueError(f"calibration window [{start}, {end}) has too few returns (reference {len(r)}, target {len(g)})")
    return float(g.std() / r.std())


def compute_volatility_scale_factor(reference_symbol: str, target_symbol: str,
                                    calibration_end=None, calibration_start=None) -> float:
    """
    Returns target's hourly-return std / reference's hourly-return std,
    computed from real OHLCV data (never hardcoded) — the empirical
    factor by which target_symbol moves less (factor < 1) or more
    (factor > 1) than reference_symbol on the same 1h candle grid.

    claude code changed: new. get_fx_baseline_config() below defaults its
    reference symbol to BTC/USDT because it's the flagship asset already
    central to this engine's crypto investigation (the "why is BTC
    closest to significant" analysis) — not because it's assumed
    representative of "crypto" broadly; a caller wanting a different
    reference asset can pass one directly.
    """
    # claude code changed: Post-audit Phase 2 B5 — was the FULL history of both
    # series, then used to scale thresholds applied to every fold: temporal
    # leakage (future volatility shaped past decisions). A scale factor is a
    # FITTED PARAMETER: it must come from a pre-specified calibration window
    # [calibration_start, calibration_end) — or, inside walk-forward, from each
    # fold's purged training slice (make_fx_scaled_oos_fns). Full-history use
    # is refused.
    if calibration_end is None:
        raise ValueError("compute_volatility_scale_factor requires calibration_end (exclusive): a full-history volatility "
                         "ratio leaks future information into every earlier decision — use a pre-specified calibration "
                         "window, or make_fx_scaled_oos_fns() for per-fold fitting")
    ref_df = load_close_series_for_oos(reference_symbol)
    target_df = load_close_series_for_oos(target_symbol)
    return _vol_ratio_on_window(ref_df["close"], target_df["close"], calibration_start, calibration_end)


def scale_config_thresholds(base_config: DCAConfig, scale_factor: float) -> DCAConfig:
    """
    Scales EVERY percentage-based threshold in base_config (each DCA
    level's drop_pct, profit_activation_pct, trailing_pct) by
    scale_factor — deliberately uniform across all three, not just the
    DCA levels: a profit target sized for crypto's volatility would be
    nearly as dormant on FX as the unscaled DCA ladder was if left
    unscaled, for the identical reason. Multipliers (2x-64x) and
    initial_usdt are NOT scaled — the ladder's capital-deployment shape
    is a capital/risk decision, not a volatility one, and stays intended
    exactly as designed.
    """
    scaled_levels = tuple(
        DCALevel(drop_pct=lvl.drop_pct * scale_factor, multiplier=lvl.multiplier)
        for lvl in base_config.levels
    )
    return replace(
        base_config,
        levels=scaled_levels,
        profit_activation_pct=base_config.profit_activation_pct * scale_factor,
        trailing_pct=base_config.trailing_pct * scale_factor,
    )


def get_fx_baseline_config(
    reference_symbol: str = "BTC/USDT",
    target_symbol: str = "EUR/USD",
    base_config: DCAConfig = BASELINE_CONFIG,
    *,
    calibration_end=None,     # claude code changed: Post-audit Phase 2 B5 — REQUIRED (exclusive); see compute_volatility_scale_factor
    calibration_start=None,
) -> DCAConfig:
    """
    Derived, not guessed — real BTC/USDT vs EUR/USD hourly-return-std
    ratio (computed on demand from the same OHLCV data every other
    result in this module uses). BTC and EUR/USD are each the flagship,
    most-liquid instrument in their asset class and are already the two
    symbols this session's investigation centers on; pass different
    symbols for a different reference pair.

    claude code changed: deliberately a FUNCTION, not a module-level
    constant. An eager `FX_BASELINE_CONFIG = ...` at import time would
    mean every import of dca_engine.py — including pure-crypto callers —
    reads two CSV files off disk and hard-crashes the whole module import
    if either is ever missing/moved. BASELINE_CONFIG itself does no I/O;
    this keeps that same property and only touches disk when a caller
    actually asks for the FX-scaled config.
    """
    scale_factor = compute_volatility_scale_factor(reference_symbol, target_symbol,
                                                   calibration_end=calibration_end, calibration_start=calibration_start)
    return scale_config_thresholds(base_config, scale_factor)


def make_fx_scaled_oos_fns(reference_close: pd.Series, base_config: DCAConfig = BASELINE_CONFIG):
    """claude code changed: new — Post-audit Phase 2 B5. (fit_fn,
    run_strategy_fn) for evaluate_strategy_oos(): fit_fn receives ONLY the
    purged training slice (the evaluator's own leakage enforcement) and
    fits the volatility scale factor on that slice's time range — the
    reference series is cut to the same [first, last] training timestamps,
    so nothing after the training end is read. run_strategy_fn applies the
    fold's own fitted factor to the test slice."""
    def fit_fn(train_df: pd.DataFrame) -> Dict:
        start, end = train_df.index.min(), train_df.index.max()
        ref = reference_close[(reference_close.index >= start) & (reference_close.index <= end)]
        factor = _vol_ratio_on_window(ref, train_df["close"])
        return {"scale_factor": factor, "calibration_start": start, "calibration_end_inclusive": end}

    def run_strategy_fn(df_slice: pd.DataFrame, fitted_params: Dict) -> List[Dict]:
        return simulate_dca_cycles(df_slice, scale_config_thresholds(base_config, fitted_params["scale_factor"]))

    return fit_fn, run_strategy_fn


def build_config_with_real_costs(
    base_config: DCAConfig,
    asset_class: str,
    symbol: Optional[str] = None,
    venue_id: str = "binance",
) -> DCAConfig:
    """
    Returns a copy of base_config with fee_rate/slippage_rate replaced by
    the REAL, asset-class-appropriate cost model from
    bot.config.cost_model.get_cost_model() — the existing factory this
    project already built for exactly this purpose (Multi-Asset
    Foundation Refactor, Phase 1A). DCAConfig's own fee_rate/slippage_rate
    defaults (bot.config.execution_costs.FEE_RATE/SLIPPAGE_RATE) are
    crypto-modeled (a maker/taker fee); applying them to FOREX would be
    exactly the Phase 6 mistake this function exists to prevent.

    For asset_class=FOREX: cost = get_cost_model(...).get_costs() ->
    ForexCostModel(symbol).get_costs() -> {"fee_rate": 0.0,
    "slippage_rate": spread_pips * pip_size / real_reference_price}. This
    is a REAL, existing, previously-verified cost estimate (see
    research_data/model_governance_log.md's "Forex data provenance fixed"
    entry) — not a new number invented here.

    HONEST, DOCUMENTED GAP — NOT SILENTLY IGNORED: ForexCostModel prices
    only the bid-ask SPREAD, crossed once per side. It does NOT model
    swap/rollover (overnight financing cost for a position held past a
    session close). bot.instruments.Instrument.swap_long/swap_short exist
    in the schema but are unpopulated (None) everywhere in this codebase
    — "require a real broker, Stage 2," per that field's own comment — so
    there is no real swap data to compute from yet, and inventing a
    number would violate this project's own "never invent data"
    principle. This matters concretely for DCA: this engine's own crypto
    backtests already show real cycles held for up to 11 months (see the
    BTC investigation, 2022-04-02 -> 2023-03-18) — any FX result from
    this function should be read as UNDERSTATING true cost for
    long-held cycles until real swap data exists. Documented here so
    every FX result carries this caveat, not discovered later.
    """
    from bot.config.cost_model import get_cost_model

    costs = get_cost_model(asset_class, venue_id=venue_id, symbol=symbol).get_costs()
    # claude code changed: Post-audit Phase 2 B6 — FX swap/rollover applies to
    # BOTH directions and no real data exists (see above): label it
    # UNAVAILABLE so every result says cost_complete=False, instead of
    # silently pricing financing at zero. An explicit KNOWN/ASSUMED_ZERO set by
    # the caller is kept.
    financing = base_config.financing_status
    if asset_class.upper() == "FOREX" and financing is None:
        financing = FINANCING_UNAVAILABLE
    return replace(base_config, fee_rate=costs["fee_rate"], slippage_rate=costs["slippage_rate"], financing_status=financing)


def run_dca_research_oos(
    canonical_symbol: str = "XRP/USDT",
    config: DCAConfig = BASELINE_CONFIG,
    walk_forward_config=None,
):
    """Convenience entry point: real price data -> the EXISTING
    evaluate_strategy_oos() -> a real OOSResult. Deliberately thin — all
    the actual logic lives in the reused evaluator and this module's
    simulator, not duplicated here."""
    from bot.research.oos_validator import WalkForwardConfig, evaluate_strategy_oos

    df = load_close_series_for_oos(canonical_symbol)
    wf_config = walk_forward_config or WalkForwardConfig()
    return evaluate_strategy_oos(
        df=df,
        run_strategy_fn=make_run_strategy_fn(config),
        config=wf_config,
        fit_fn=None,
        strategy_name="dca_martingale_baseline",
        strategy_version=f"2026-10-02.2-canonical-clock-{config.fill_model}",   # claude code changed: Post-audit Phase 2 — the clock/fill model is part of the method version
    )


if __name__ == "__main__":
    theoretical = compute_theoretical_ladder(BASELINE_CONFIG)
    print("=" * 100)
    print("THEORETICAL LADDER (no price data)")
    print("=" * 100)
    for k, v in theoretical.items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 100)
    print("REAL SIMULATION — XRP/USDT")
    print("=" * 100)
    result = run_dca_research_oos()
    all_trades = [t for f in result.folds for t in (f.trades or [])]
    summary = summarize_dca_cycles(all_trades, BASELINE_CONFIG)
    for k, v in summary.items():
        print(f"  {k}: {v}")
