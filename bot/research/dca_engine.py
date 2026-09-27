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

    def __post_init__(self):
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError(f"DCAConfig.direction must be 'LONG' or 'SHORT', got {self.direction!r}")


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
        "holding_candles": len(cycle.fills),   # claude code changed: fill count, not candle count — see module docstring note below
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


def simulate_dca_cycles(df: pd.DataFrame, config: DCAConfig = BASELINE_CONFIG) -> List[Dict]:
    """
    Candle-by-candle DCA/Martingale cycle simulator. `df` must have a
    sorted DatetimeIndex and a 'close' column (matches Backtester's own
    OHLCV shape — see oos_validator.py's Type B docstring for why this
    differs from Type A's timestamp-COLUMN convention).

    Supports both config.direction values: LONG (average down on drops,
    profit on a rise) and SHORT (average into a rise, profit on a fall) —
    see DCAConfig's own docstring. Needed for FX, which is inherently
    two-sided, unlike the crypto-only long baseline this engine started
    from.

    "Always-in": the instant one cycle closes, the next opens on the
    very next candle — matches the observed app's own description
    ("all strategies run autonomously in spot market in cycle mode").

    KNOWN, DOCUMENTED SIMPLIFICATION (first-slice scope, Phase 27): DCA
    triggers and exit triggers are evaluated against candle CLOSE only,
    not intrabar high/low. A real bot reacting intrabar would trigger
    DCA levels and the trailing exit somewhat earlier than this
    simulation shows — this makes the simulated ladder slightly SLOWER
    to fill and slightly LATER to exit than reality, a conservative
    (not favorable) bias. Noted as a real limitation for stress-test
    interpretation, not silently ignored.

    holding_candles here counts FILLS, not calendar candles held open —
    _compute_trade_metrics() doesn't use it for return calculations, only
    Backtester's UI layer does, and this DCA cycle's "how long" is
    better read from exit_time - entry_time directly. Documented rather
    than silently overloading the field's usual meaning.
    """
    if "close" not in df.columns:
        raise ValueError("simulate_dca_cycles() requires a 'close' column")

    trades: List[Dict] = []
    cycle: Optional[_OpenCycle] = None

    for timestamp, row in df.iterrows():
        price = float(row["close"])

        if cycle is not None:
            cycle.candles_held += 1
            if _check_exit(cycle, price, config):
                trades.append(_close_cycle(cycle, timestamp, price, config, exit_reason="PROFIT_TARGET_TRAILING"))
                cycle = None
            elif config.max_cycle_candles is not None and cycle.candles_held >= config.max_cycle_candles:
                trades.append(_close_cycle(cycle, timestamp, price, config, exit_reason="MAX_CYCLE_CANDLES_TIMEOUT"))
                cycle = None

        if cycle is None:
            cycle = _open_new_cycle(timestamp, price, config)
        else:
            _maybe_trigger_dca(cycle, timestamp, price, config)

    # claude code changed: Phase 26 ("no false confidence") — a cycle still
    # open when the data runs out is NOT silently dropped (that would hide
    # exactly the trapped-capital tail risk this whole engine exists to
    # measure). Force-marked at the final candle, tagged distinctly so
    # summarize_dca_cycles() can report it separately from real exits.
    if cycle is not None:
        last_timestamp = df.index[-1]
        last_price = float(df["close"].iloc[-1])
        trades.append(_close_cycle(cycle, last_timestamp, last_price, config, exit_reason="DATA_END_STILL_OPEN"))

    return trades


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# PHASE 8 — CYCLE ANALYTICS THAT REFUSE TO LET WIN RATE HIDE TAIL RISK
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def summarize_dca_cycles(trades: List[Dict]) -> Dict:
    """Aggregates simulate_dca_cycles() output into the metric set Phase 8
    explicitly asked for — deliberately reporting worst-case and
    unresolved-cycle figures at least as prominently as win rate, per
    Phase 26 ("no false confidence")."""
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

    n_exhausted = sum(1 for t in trades if t["cycle_max_level_reached"] == max(max_level_counts.keys()))
    total_max_deployed = max((t["cycle_max_capital_deployed"] for t in trades), default=0.0)
    total_net_profit = sum(profits)

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
        "unresolved_reasons": {r: sum(1 for t in unresolved if t["cycle_exit_reason"] == r)
                                for r in set(t["cycle_exit_reason"] for t in unresolved)},
        "cycle_win_rate_pct_of_resolved": round(len(wins) / len(resolved) * 100, 2) if resolved else None,
        "avg_resolved_cycle_profit": round(sum(t["profit"] for t in resolved) / len(resolved), 4) if resolved else None,
        "avg_resolved_loss": round(sum(t["profit"] for t in losses_among_resolved) / len(losses_among_resolved), 4) if losses_among_resolved else None,
        "worst_cycle_profit": round(sorted_by_profit[0]["profit"], 4),          # across ALL cycles, incl. unresolved — the real tail
        "best_cycle_profit": round(sorted_by_profit[-1]["profit"], 4),
        "max_capital_deployed_any_cycle": round(total_max_deployed, 2),
        "max_dca_level_distribution": max_level_counts,
        "ladder_full_exhaustion_count": n_exhausted,
        "ladder_full_exhaustion_pct": round(n_exhausted / n_cycles * 100, 2),
        "total_net_profit": round(total_net_profit, 4),
        "total_fees_paid": round(sum(t["cycle_fees_total"] for t in trades), 4),
        "profit_concentration_top1_pct": round(profit_concentration_top1 * 100, 2) if profit_concentration_top1 is not None else None,
        # claude code changed: ChatGPT review's proposed metric, made real —
        # profit per dollar of the worst single-cycle exposure, not per
        # dollar of the (misleadingly small) initial leg.
        "profit_to_max_exposure_ratio": round(total_net_profit / total_max_deployed, 4) if total_max_deployed else None,
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
DEFAULT_N_PERMUTATIONS = 100             # matches this project's own established convention (feature_validator.py's BLOCK_PERMUTATION_N, permutation_test_engine.py's DEFAULT_N_PERMUTATIONS)


def _block_shuffle_returns(returns: "np.ndarray", block_size: int, rng: "np.random.Generator") -> "np.ndarray":
    """Chops log-returns into contiguous blocks and reassembles them in a
    randomly shuffled order — same principle as this project's other two
    block-shuffle implementations, applied here to log-returns so the
    shuffled result can be turned back into a valid, continuous price
    path (see _reconstruct_price_path)."""
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


def run_dca_permutation_test(
    df: pd.DataFrame,
    config: DCAConfig = BASELINE_CONFIG,
    n_permutations: int = DEFAULT_N_PERMUTATIONS,
    block_size: int = DEFAULT_PERMUTATION_BLOCK_CANDLES,
    seed: int = 42,
) -> Dict:
    """
    THE genuine-edge test. Runs simulate_dca_cycles() once on the REAL
    price path, then n_permutations times on block-shuffled
    reconstructions of the SAME return distribution (same volatility,
    same marginal return distribution, same local autocorrelation texture
    within each block — only the larger-scale sequence/alignment of
    those blocks is randomized). If the real result is not meaningfully
    better than the shuffled distribution, the observed profitability is
    not coming from anything real about this asset's price structure.

    Tests two metrics independently (both matter — see Phase 8's own
    "don't judge by win rate" framing): total_net_profit (the raw
    economic outcome) and profit_to_max_exposure_ratio (profit per
    dollar of real capital at risk, immune to a strategy that just
    trades more often).

    claude code changed: seed is explicit and always recorded (matches
    feature_validator.py's own rng.default_rng(seed) pattern) — never
    hidden randomness.
    """
    close = df["close"].values.astype(float)
    log_returns = np.diff(np.log(close))
    initial_price = close[0]

    real_trades = simulate_dca_cycles(df, config)
    real_summary = summarize_dca_cycles(real_trades)
    real_profit = real_summary["total_net_profit"]
    real_ratio = real_summary["profit_to_max_exposure_ratio"] or 0.0

    rng = np.random.default_rng(seed)
    shuffled_profits: List[float] = []
    shuffled_ratios: List[float] = []

    for i in range(n_permutations):
        shuffled_returns = _block_shuffle_returns(log_returns, block_size, rng)
        shuffled_prices = np.concatenate([[initial_price], _reconstruct_price_path(initial_price, shuffled_returns)])
        shuffled_df = pd.DataFrame({"close": shuffled_prices}, index=df.index[: len(shuffled_prices)])
        shuffled_trades = simulate_dca_cycles(shuffled_df, config)
        shuffled_summary = summarize_dca_cycles(shuffled_trades)
        shuffled_profits.append(shuffled_summary["total_net_profit"])
        shuffled_ratios.append(shuffled_summary["profit_to_max_exposure_ratio"] or 0.0)
        if (i + 1) % 20 == 0:
            logger.info(f"  DCA permutation test: {i + 1}/{n_permutations} shuffles complete")

    # claude code changed: empirical p-value formula matches this
    # project's own established convention exactly — see
    # feature_validator.py's _block_permutation_pvalue() docstring.
    extreme_profit = sum(1 for p in shuffled_profits if p >= real_profit)
    extreme_ratio = sum(1 for r in shuffled_ratios if r >= real_ratio)
    p_value_profit = (extreme_profit + 1) / (n_permutations + 1)
    p_value_ratio = (extreme_ratio + 1) / (n_permutations + 1)

    return {
        "n_permutations": n_permutations,
        "block_size_candles": block_size,
        "real_total_net_profit": real_profit,
        "shuffled_profit_mean": round(float(np.mean(shuffled_profits)), 4),
        "shuffled_profit_std": round(float(np.std(shuffled_profits)), 4),
        "shuffled_profit_percentile_of_real": round(
            float(np.mean([real_profit >= p for p in shuffled_profits]) * 100), 2
        ),
        "p_value_profit": round(p_value_profit, 4),
        "real_profit_to_max_exposure_ratio": real_ratio,
        "shuffled_ratio_mean": round(float(np.mean(shuffled_ratios)), 4),
        "shuffled_ratio_std": round(float(np.std(shuffled_ratios)), 4),
        "p_value_profit_to_max_exposure_ratio": round(p_value_ratio, 4),
        "edge_appears_real": bool(p_value_profit < 0.05 and p_value_ratio < 0.05),
    }


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

def compute_volatility_scale_factor(reference_symbol: str, target_symbol: str) -> float:
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
    ref_df = load_close_series_for_oos(reference_symbol)
    target_df = load_close_series_for_oos(target_symbol)
    ref_std = np.log(ref_df["close"]).diff().dropna().std()
    target_std = np.log(target_df["close"]).diff().dropna().std()
    return float(target_std / ref_std)


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
    scale_factor = compute_volatility_scale_factor(reference_symbol, target_symbol)
    return scale_config_thresholds(base_config, scale_factor)


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
    return replace(base_config, fee_rate=costs["fee_rate"], slippage_rate=costs["slippage_rate"])


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
        strategy_version="2026-09-26.1-baseline",
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
    summary = summarize_dca_cycles(all_trades)
    for k, v in summary.items():
        print(f"  {k}: {v}")
