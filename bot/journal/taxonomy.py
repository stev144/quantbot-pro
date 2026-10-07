# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/journal/taxonomy.py
#
# claude code changed: new file — 2026-10-07, Trading Decision Journal Phase 1 (backtester-only recorder).
# Owner sign-off: TRADING_DECISION_JOURNAL_PHASE1_OWNER_SIGNOFF_2026-10-07.md (OD-J1/J5/J6/J7a/J13-backtester/
# J15, D2–D8). Design: TRADING_DECISION_JOURNAL_DESIGN_REVB_2026-10-07.md.
#
# The ONE controlled vocabulary of the decision journal: environments, terminal statuses, lifecycle stages,
# reason stages, reason codes, the raw-reason mapping for the EXISTING backtester / router / strategies, and
# the versioned gate order (D3). Nothing here changes trading behaviour; it only names what the engine did.
#
# Rules (owner D4): a raw reason is mapped only by an exact entry below; anything else raises UnmappedReason
# (the recorder then writes a MAPPING_FAILURE gap and marks the run INCOMPLETE — never a guessed outcome).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

TAXONOMY_VERSION = "QBP-JRNL-REASON-1"
SCHEMA_VERSION = "QBP-JRNL-SCHEMA-1"
GATE_ORDER_VERSION = "QBP-JRNL-GATES-BT-1"
FEATURE_SET_VERSION = "QBP-JRNL-SNAP-BT-1"
COVERAGE_VERSION = "QBP-JRNL-COVERAGE-BT-1"

# ── identity (OD-J15 / D7) ────────────────────────────────────────────────────────────────────────────────
ENVIRONMENTS = ("BACKTEST", "RESEARCH", "PAPER", "DRY_RUN", "LIVE")
# execution_mode is the EXISTING TradeRecord vocabulary (bot.journal.models.EXECUTION_MODES); owner decision
# 2026-10-07: Phase 1 backtest decisions use the existing RESEARCH value ("offline backtest/research output"),
# no SIMULATED value is added (it would encode the same concept twice).
PHASE1_ENVIRONMENT = "BACKTEST"

# ── terminal statuses (the 14 of the brief minus SIGNAL_GENERATED, + SYSTEM_REJECTED (OD-J1), + D5) ──────────
EXECUTED = "EXECUTED"
NO_SIGNAL = "NO_SIGNAL"
REJECTED = "REJECTED"
REGIME_REJECTED = "REGIME_REJECTED"
COST_REJECTED = "COST_REJECTED"
LIQUIDITY_REJECTED = "LIQUIDITY_REJECTED"
TIMING_REJECTED = "TIMING_REJECTED"
RISK_REJECTED = "RISK_REJECTED"
PORTFOLIO_REJECTED = "PORTFOLIO_REJECTED"
BORROW_REJECTED = "BORROW_REJECTED"
DATA_REJECTED = "DATA_REJECTED"
DUPLICATE_REJECTED = "DUPLICATE_REJECTED"
EXECUTION_FAILED = "EXECUTION_FAILED"
SYSTEM_REJECTED = "SYSTEM_REJECTED"
CENSORED_END_OF_DATA = "CENSORED_END_OF_DATA"
TERMINAL_STATUSES = (EXECUTED, NO_SIGNAL, REJECTED, REGIME_REJECTED, COST_REJECTED, LIQUIDITY_REJECTED,
                     TIMING_REJECTED, RISK_REJECTED, PORTFOLIO_REJECTED, BORROW_REJECTED, DATA_REJECTED,
                     DUPLICATE_REJECTED, EXECUTION_FAILED, SYSTEM_REJECTED, CENSORED_END_OF_DATA)
# excluded from every fill-dependent statistic (D5)
FILL_DEPENDENT_EXCLUDED = (CENSORED_END_OF_DATA,)

# ── lifecycle stages (Rev B §5) and the allowed terminal per last stage reached ──────────────────────────
EVALUATED, SIGNAL_GENERATED, GATES_EVALUATED, EXECUTION_ATTEMPTED = (
    "EVALUATED", "SIGNAL_GENERATED", "GATES_EVALUATED", "EXECUTION_ATTEMPTED")
STAGE_ORDER = (EVALUATED, SIGNAL_GENERATED, GATES_EVALUATED, EXECUTION_ATTEMPTED)
ALLOWED_TERMINAL = {
    EVALUATED: frozenset({NO_SIGNAL, REGIME_REJECTED, DATA_REJECTED, SYSTEM_REJECTED}),
    SIGNAL_GENERATED: frozenset({CENSORED_END_OF_DATA}),
    GATES_EVALUATED: frozenset({REJECTED, COST_REJECTED, LIQUIDITY_REJECTED, TIMING_REJECTED, RISK_REJECTED,
                                PORTFOLIO_REJECTED, BORROW_REJECTED, DUPLICATE_REJECTED, DATA_REJECTED,
                                SYSTEM_REJECTED}),
    EXECUTION_ATTEMPTED: frozenset({EXECUTED, EXECUTION_FAILED}),
}

# ── non-evaluation coverage categories (D8; never decision events, never NO_SIGNAL) ─────────────────────────
COV_PRE_LOOP = "PRE_LOOP"                        # candle index < the backtester's loop start (100)
COV_EVALUATED = "EVALUATED"                      # a DecisionEvent exists for the candle
COV_WARMUP = "WARMUP"                            # evaluation not yet possible (initial history, D4 #9/#12)
COV_IN_POSITION = "NOT_EVALUATED_IN_POSITION"
COV_EXIT_CANDLE = "NOT_EVALUATED_EXIT_CANDLE"
COV_ENTRY_PENDING = "NOT_EVALUATED_ENTRY_PENDING"  # structurally 0 in this engine: a pending entry fills at the next loop top
COV_MAPPING_FAILURE = "EVALUATED_UNMAPPED"       # evaluated, but the raw reason has no mapping (run INCOMPLETE)
COVERAGE_CATEGORIES = (COV_PRE_LOOP, COV_EVALUATED, COV_WARMUP, COV_IN_POSITION, COV_EXIT_CANDLE,
                       COV_ENTRY_PENDING, COV_MAPPING_FAILURE)

# ── reason stages, origins ────────────────────────────────────────────────────────────────────────────────
STAGE_REGIME, STAGE_GOVERNANCE, STAGE_CONFIGURATION = "REGIME", "GOVERNANCE", "CONFIGURATION"
STAGE_STRATEGY_RUNTIME, STAGE_DATA, STAGE_STRATEGY, STAGE_RISK = "STRATEGY_RUNTIME", "DATA", "STRATEGY", "RISK"
ORIGIN_BY_STAGE = {STAGE_REGIME: "STRATEGY", STAGE_STRATEGY: "STRATEGY", STAGE_GOVERNANCE: "SYSTEM",
                   STAGE_CONFIGURATION: "SYSTEM", STAGE_STRATEGY_RUNTIME: "SYSTEM", STAGE_DATA: "DATA",
                   STAGE_RISK: "RISK"}

# ── reason codes → the ONE (status, stage) each may carry (D4) ──────────────────────────────────────────────
REASON_CODES: Dict[str, Tuple[str, str]] = {
    "WRONG_REGIME": (REGIME_REJECTED, STAGE_REGIME),
    "REGIME_CONFIDENCE_TOO_LOW": (REGIME_REJECTED, STAGE_REGIME),
    "ADX_TOO_LOW": (REGIME_REJECTED, STAGE_REGIME),
    "STRATEGY_NOT_VALIDATED": (SYSTEM_REJECTED, STAGE_GOVERNANCE),
    "LONGS_DISABLED": (SYSTEM_REJECTED, STAGE_CONFIGURATION),
    "SHORTS_DISABLED": (SYSTEM_REJECTED, STAGE_CONFIGURATION),
    "DIRECTION_CONFLICTS_REGIME": (REJECTED, STAGE_REGIME),
    "STRATEGY_OUTPUT_INVALID": (SYSTEM_REJECTED, STAGE_STRATEGY_RUNTIME),
    "DATA_QUALITY_FAILURE": (DATA_REJECTED, STAGE_DATA),
    "STRUCTURE_ANALYSIS_FAILURE": (SYSTEM_REJECTED, STAGE_STRATEGY_RUNTIME),
    "FLAT_MARKET": (NO_SIGNAL, STAGE_STRATEGY),
    "NO_TREND": (NO_SIGNAL, STAGE_STRATEGY),
    "STRUCTURE_NOT_CONFIRMED": (NO_SIGNAL, STAGE_STRATEGY),
    "NO_PULLBACK": (NO_SIGNAL, STAGE_STRATEGY),
    "NO_MOMENTUM": (NO_SIGNAL, STAGE_STRATEGY),
    "NON_RANGING_MARKET": (NO_SIGNAL, STAGE_STRATEGY),
    "RANGE_TOO_WIDE": (NO_SIGNAL, STAGE_STRATEGY),
    "NO_EXTREME": (NO_SIGNAL, STAGE_STRATEGY),
    "BUY_NO_CONFIRMATION": (NO_SIGNAL, STAGE_STRATEGY),
    "SELL_NO_CONFIRMATION": (NO_SIGNAL, STAGE_STRATEGY),
    "INVALID_SIGNAL": (SYSTEM_REJECTED, STAGE_STRATEGY_RUNTIME),
    "SIZE_BELOW_MINIMUM": (REJECTED, STAGE_RISK),
}
# EXECUTED and CENSORED_END_OF_DATA carry no reason code: the status itself is the disposition.
STATUSES_WITHOUT_REASON = (EXECUTED, CENSORED_END_OF_DATA)

# ── gate order (D3), versioned. A gate is PASS only when the executed control flow demonstrably passed it,
# FAIL for the check that stopped the engine, NOT_EVALUATED otherwise (later checks, other branches, checks
# disabled by configuration, and post-signal checks when no candidate exists). PASS never means "the whole
# strategy condition set passed".
PASS, FAIL, NOT_EVALUATED = "PASS", "FAIL", "NOT_EVALUATED"
G_HV, G_CONF, G_PROD = "ROUTER_REGIME_NOT_HIGH_VOLATILITY", "ROUTER_CONFIDENCE_REQUIREMENT", "ROUTER_PRODUCTION_ELIGIBILITY"
G_ADX, G_DIR_PRE = "ROUTER_ADX_MINIMUM", "ROUTER_DIRECTION_ENABLED_PRE"
MA_GATES = ("MA_REQUIRED_COLUMNS", "MA_MIN_CANDLES", "MA_EMA_READY", "MA_STRUCTURE_ANALYSIS", "MA_NOT_FLAT",
            "MA_EMA_TREND", "MA_STRUCTURE_CONFIRMED", "MA_PULLBACK", "MA_MOMENTUM")
G_MATCH = "ROUTER_SIGNAL_MATCHES_REGIME"
MR_GATES = ("MR_MIN_CANDLES", "MR_REGIME_IS_RANGING", "MR_RSI_READY", "MR_ATR_AVAILABLE", "MR_BB_AVAILABLE",
            "MR_RANGE_NOT_TOO_WIDE", "MR_PREVIOUS_CANDLE", "MR_RSI_EXTREME", "MR_CONFIRMATION")
G_OUTPUT, G_DIR_POST = "ROUTER_STRATEGY_OUTPUT_PRESENT", "ROUTER_DIRECTION_ENABLED_POST"
G_WELL_FORMED = "BT_SIGNAL_WELL_FORMED"
FILL_GATES = ("BT_FILL_SIGNAL_VALUE", "BT_FILL_SIZE_POSITIVE", "BT_FILL_STOP_DISTANCE_NONZERO")
GATE_ORDER = (G_HV, G_CONF, G_PROD, G_ADX, G_DIR_PRE) + MA_GATES + (G_MATCH,) + MR_GATES + (
    G_OUTPUT, G_DIR_POST, G_WELL_FORMED) + FILL_GATES
# the control-flow path per routed regime, in execution order (router.route → strategy → backtester → fill)
PATH_TREND = (G_HV, G_CONF, G_PROD, G_ADX, G_DIR_PRE) + MA_GATES + (G_MATCH, G_WELL_FORMED) + FILL_GATES
PATH_RANGING = (G_HV, G_CONF, G_PROD) + MR_GATES + (G_OUTPUT, G_DIR_POST, G_WELL_FORMED) + FILL_GATES
PATH_HIGH_VOL = (G_HV,)
# the first gate of the path that governs a CANDIDATE (everything from here on is post-signal)
FIRST_POST_SIGNAL_GATE = {"TREND": G_MATCH, "RANGING": G_OUTPUT}
REGIME_PATH = {"TRENDING_UP": "TREND", "TRENDING_DOWN": "TREND", "RANGING": "RANGING", "HIGH_VOLATILITY": "HIGH_VOL"}
PATHS = {"TREND": PATH_TREND, "RANGING": PATH_RANGING, "HIGH_VOL": PATH_HIGH_VOL}
STRATEGY_FOR_PATH = {"TREND": "MovingAverageStrategy", "RANGING": "MeanReversionStrategy", "HIGH_VOL": "StrategyRouter"}


class UnmappedReason(ValueError):
    """A raw reason with no exact mapping (D4 #15): never classified, the run becomes INCOMPLETE."""


@dataclass(frozen=True)
class Mapped:
    status: str
    stage: str
    code: str
    gate: str                      # the gate that FAILED (the engine's actual refusal point)
    candidate: bool                # a BUY/SELL existed (D2) — post-signal refusal
    direction: str = ""            # LONG / SHORT when a candidate existed
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Warmup:
    gate: str
    raw: str


def _m(code, gate, *, candidate=False, direction="", detail=None) -> Mapped:
    status, stage = REASON_CODES[code]
    return Mapped(status, stage, code, gate, candidate, direction, dict(detail or {}))


# exact raw reasons emitted by StrategyRouter / MovingAverageStrategy / MeanReversionStrategy (verified in source,
# 2026-10-07) → (code, failing gate, paths it may occur on)
_EXACT = {
    "high_volatility_blocked": ("WRONG_REGIME", G_HV, {"HIGH_VOL"}),
    "adx_below_minimum_for_trend": ("ADX_TOO_LOW", G_ADX, {"TREND"}),
    "mean_reversion_returned_none": ("STRATEGY_OUTPUT_INVALID", G_OUTPUT, {"RANGING"}),
    "missing_data": ("DATA_QUALITY_FAILURE", "MA_REQUIRED_COLUMNS", {"TREND"}),
    "structure_analysis_failed": ("STRUCTURE_ANALYSIS_FAILURE", "MA_STRUCTURE_ANALYSIS", {"TREND"}),
    "flat_market": ("FLAT_MARKET", "MA_NOT_FLAT", {"TREND"}),
    "no_ema_trend": ("NO_TREND", "MA_EMA_TREND", {"TREND"}),
    "no_pullback": ("NO_PULLBACK", "MA_PULLBACK", {"TREND"}),
    "no_momentum": ("NO_MOMENTUM", "MA_MOMENTUM", {"TREND"}),
    "non_ranging_market": ("NON_RANGING_MARKET", "MR_REGIME_IS_RANGING", {"RANGING"}),
    "range_too_wide": ("RANGE_TOO_WIDE", "MR_RANGE_NOT_TOO_WIDE", {"RANGING"}),
    "no_extreme": ("NO_EXTREME", "MR_RSI_EXTREME", {"RANGING"}),
    "buy_no_confirmation": ("BUY_NO_CONFIRMATION", "MR_CONFIRMATION", {"RANGING"}),
    "sell_no_confirmation": ("SELL_NO_CONFIRMATION", "MR_CONFIRMATION", {"RANGING"}),
}
# warm-up-class raw reasons per path → the gate they fail (D4 #9/#12). MR's second "not_enough_data" (previous
# candle, MR_PREVIOUS_CANDLE) is unreachable once MR_MIN_CANDLES passed while min_candles >= 2 — the recorder
# verifies that precondition at run start and treats the string as MR_MIN_CANDLES.
_WARMUP_CLASS = {
    "TREND": {"not_enough_data": "MA_MIN_CANDLES", "indicator_warmup": "MA_EMA_READY"},
    "RANGING": {"not_enough_data": "MR_MIN_CANDLES", "rsi_not_ready": "MR_RSI_READY",
                "atr_unavailable": "MR_ATR_AVAILABLE", "bb_unavailable": "MR_BB_AVAILABLE"},
}
_PREFIX = (
    ("confidence_too_low_", "REGIME_CONFIDENCE_TOO_LOW", G_CONF, {"TREND", "RANGING"}, "confidence"),
    ("strategy_not_validated_", "STRATEGY_NOT_VALIDATED", G_PROD, {"TREND", "RANGING"}, "verdict"),
    ("structure_not_uptrend_", "STRUCTURE_NOT_CONFIRMED", "MA_STRUCTURE_CONFIRMED", {"TREND"}, "label"),
    ("structure_not_downtrend_", "STRUCTURE_NOT_CONFIRMED", "MA_STRUCTURE_CONFIRMED", {"TREND"}, "label"),
)


def path_for_regime(regime: str) -> str:
    if regime not in REGIME_PATH:
        raise UnmappedReason(f"regime {regime!r} has no routed path")
    return REGIME_PATH[regime]


def map_no_signal_reason(raw: str, regime: str, *, warmup_open: bool):
    """Map a NO_SIGNAL dict's raw reason (as the backtester received it from the router). Returns Mapped, or
    Warmup when the raw reason is warm-up-class AND the strategy's initial warm-up is still open; raises
    UnmappedReason for anything without an exact entry or used outside the path that can emit it."""
    raw = (raw or "").strip()
    path = path_for_regime(regime)
    if raw in _EXACT:
        code, gate, paths = _EXACT[raw]
        if path not in paths:
            raise UnmappedReason(f"raw reason {raw!r} is not emitted on the {path} path (regime {regime})")
        return _m(code, gate)
    for prefix, code, gate, paths, key in _PREFIX:
        if raw.startswith(prefix) and len(raw) > len(prefix):
            if path not in paths:
                raise UnmappedReason(f"raw reason {raw!r} is not emitted on the {path} path (regime {regime})")
            detail = {key: raw[len(prefix):]}
            if code == "STRUCTURE_NOT_CONFIRMED":
                detail["required"] = "UPTREND" if prefix == "structure_not_uptrend_" else "DOWNTREND"
            return _m(code, gate, detail=detail)
    if raw in ("longs_disabled", "shorts_disabled"):
        code = "LONGS_DISABLED" if raw == "longs_disabled" else "SHORTS_DISABLED"
        if path == "TREND":                    # checked BEFORE the strategy runs: no candidate
            return _m(code, G_DIR_PRE)
        if path == "RANGING":                  # checked AFTER MeanReversionStrategy returned BUY/SELL: a candidate
            return _m(code, G_DIR_POST, candidate=True, direction="LONG" if raw == "longs_disabled" else "SHORT")
        raise UnmappedReason(f"raw reason {raw!r} on the {path} path")
    if raw in ("sell_rejected_in_uptrend", "buy_rejected_in_downtrend"):
        if path != "TREND":
            raise UnmappedReason(f"raw reason {raw!r} on the {path} path")
        return _m("DIRECTION_CONFLICTS_REGIME", G_MATCH, candidate=True,
                  direction="SHORT" if raw == "sell_rejected_in_uptrend" else "LONG")
    warm = _WARMUP_CLASS.get(path, {})
    if raw in warm:
        if warmup_open:
            return Warmup(warm[raw], raw)
        return _m("DATA_QUALITY_FAILURE", warm[raw], detail={"post_warmup_raw_reason": raw})
    raise UnmappedReason(f"raw reason {raw!r} has no mapping in {TAXONOMY_VERSION}")


def map_invalid_router_output() -> Mapped:
    """D4 #14: the router output was not a dict, or a dict whose signal is not BUY/SELL/NO_SIGNAL."""
    return _m("INVALID_SIGNAL", G_WELL_FORMED)


FILL_REFUSALS = {   # raw fill-time refusal tag (set by Backtester._open_trade_from_signal) → mapping (D4 #16–#18)
    "unexpected_signal_value": ("INVALID_SIGNAL", "BT_FILL_SIGNAL_VALUE"),
    "size_not_positive": ("SIZE_BELOW_MINIMUM", "BT_FILL_SIZE_POSITIVE"),
    "zero_stop_distance": ("INVALID_SIGNAL", "BT_FILL_STOP_DISTANCE_NONZERO"),
}


def map_fill_refusal(tag: Optional[str], direction: str) -> Mapped:
    if tag not in FILL_REFUSALS:
        raise UnmappedReason(f"fill refusal {tag!r} has no mapping in {TAXONOMY_VERSION}")
    code, gate = FILL_REFUSALS[tag]
    return _m(code, gate, candidate=True, direction=direction)


def gate_vector(path: str, *, failed: Optional[str], passed_through: Optional[str] = None,
                disabled: Tuple[str, ...] = ()) -> Dict[str, str]:
    """The observed control flow over the full GATE_ORDER (D3).
    failed:          the gate that stopped the engine (None = no gate failed)
    passed_through:  when nothing failed, the LAST gate demonstrably passed (gates after it stay NOT_EVALUATED);
                     ignored when `failed` is given
    disabled:        gates on the path that configuration switched off (evaluated by nobody → NOT_EVALUATED)"""
    order = PATHS[path]
    stop = failed if failed is not None else passed_through
    if stop is not None and stop not in order:
        raise ValueError(f"gate {stop!r} is not on the {path} path")
    vec = {g: NOT_EVALUATED for g in GATE_ORDER}
    if stop is None:
        return vec
    for g in order:
        if g == stop:
            vec[g] = FAIL if failed is not None else PASS
            break
        vec[g] = NOT_EVALUATED if g in disabled else PASS
    # post-signal gates govern a candidate: when the strategy itself refused, none of them was evaluated
    return vec


def validate_terminal(stages, status: str, reason_code: Optional[str], reason_stage: Optional[str]) -> None:
    """Enforce the lifecycle transition table and the reason/status pairing; raises ValueError."""
    stages = list(stages)
    if not stages or stages[0] != EVALUATED:
        raise ValueError(f"lifecycle must start at EVALUATED, got {stages}")
    if stages != list(STAGE_ORDER[:len(stages)]):
        raise ValueError(f"lifecycle stages out of order: {stages}")
    if status not in ALLOWED_TERMINAL[stages[-1]]:
        raise ValueError(f"terminal {status} is not allowed after stage {stages[-1]}")
    if status in STATUSES_WITHOUT_REASON:
        if reason_code is not None or reason_stage is not None:
            raise ValueError(f"{status} carries no reason code")
        return
    if reason_code not in REASON_CODES:
        raise ValueError(f"unknown reason code {reason_code!r}")
    if REASON_CODES[reason_code] != (status, reason_stage):
        raise ValueError(f"reason {reason_code} must be {REASON_CODES[reason_code]}, got {(status, reason_stage)}")
