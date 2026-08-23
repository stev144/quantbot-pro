# ============================================================
# bot/research_lab/spec.py
# Research Lab — the structured hypothesis specification schema.
#
# claude code changed: new file — Research Lab MVP, section 6. This is the
# ONLY thing the hypothesis-formalization step (bot/research_lab/
# interpreter.py) is allowed to produce. It never emits Python, never
# emits a tool-call sequence directly — a separate, deterministic
# compiler step (the Research Policy Gate + Tool Layer) turns a validated
# ResearchSpec into an actual research plan. This is the second seam the
# architecture study called for: "AI understood the idea" is a data
# structure, not code.
#
# claude code changed: Conditional Hypothesis Integrity fix. GOVERNING
# STATISTICAL PRINCIPLE (see also verdict.py and tools/conditional_tools.py):
# a research system must test the hypothesis that was specified, not a
# nearby hypothesis that happens to be easier to compute.
# "RSI < 30 -> forward return" (a CONDITIONAL/event hypothesis: what
# happens in the subset of history where RSI is below 30) is NOT the same
# research question as "RSI -> forward return" (a FEATURE/continuous
# hypothesis: does the general level of RSI correlate with forward
# returns everywhere). Before this fix, ResearchSpec.conditions existed
# but was never populated, validated, or read anywhere — every hypothesis,
# conditional or not, silently ran the continuous-IC path. hypothesis_type
# now makes the distinction a first-class, validated field instead of an
# unused one.
#
# claude code changed: SUPPORTED_HORIZONS and DERIVABLE_FROM_OHLCV used to
# be defined independently in tools/statistical_tools.py and
# data_availability.py respectively — two copies of "what's actually
# supported" that could silently drift apart (confirmed: they had, which
# is exactly how horizon=12 passed validation and the policy gate, then
# failed deep inside a tool call). Both now live here, once, as the
# schema's own authoritative domain constants; every other module imports
# them from this file.
#
# claude code changed: Multi-Asset Foundation Refactor, STEP 3/5.
# SUPPORTED_ASSETS/SUPPORTED_TIMEFRAMES are now DERIVED from
# bot/instruments.py's instrument registry instead of importing
# fetch_all_symbols.SYMBOLS/INTERVAL directly — same 20 symbols, same "1h"
# timeframe, byte-for-byte identical values today, but the source of truth
# is now the one place that also knows each instrument's asset_class,
# rather than a bare list with no asset-class dimension at all. Horizon
# support is now a function of timeframe (available_horizons_for_timeframe,
# below) rather than one hardcoded platform-wide dict — the exact
# "represent horizons as capabilities of the current dataset, not
# immutable platform constants" requirement the refactor brief's section 5
# describes. Behavior is unchanged (still only "1h" -> the same 3
# horizons) because that is still, honestly, the only real dataset this
# platform has.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from bot.instruments import ASSET_CLASS_CRYPTO, ASSET_CLASSES, get_instrument, list_instruments, symbols_for_asset_class

# claude code changed: this platform has exactly one native candle interval
# today (every CRYPTO instrument in the registry is "1h") —
# SUPPORTED_TIMEFRAMES is a list of one on purpose, not an oversight. A
# hypothesis naming a different timeframe is a real, honest
# INSUFFICIENT_DATA case, not something to silently coerce to 1h.
SUPPORTED_TIMEFRAMES = sorted({i.timeframe for i in list_instruments(ASSET_CLASS_CRYPTO) if i.timeframe})
SUPPORTED_ASSETS = symbols_for_asset_class(ASSET_CLASS_CRYPTO)

SUPPORTED_TARGET_TYPES = ["forward_return"]  # claude code changed: the only label type every tool in the Tool Layer (§08) can score against today

# claude code changed: moved from tools/statistical_tools.py (single
# source now, see module docstring). feature_calculator.py only ever
# produces these three forward-return label columns — verified by reading
# that module directly, not guessed. A horizon outside this set is a real,
# honest limit of the platform, and must be rejected at validation time,
# not discovered only after a tool call fails.
SUPPORTED_HORIZONS = {1: "forward_return_1h", 4: "forward_return_4h", 24: "forward_return_24h"}

# claude code changed: new — Multi-Asset Foundation Refactor STEP 3. Which
# horizons are available is a property of a TIMEFRAME's own labeling
# capability (what feature_calculator.py can actually produce for candles
# at that resolution), not a bare platform-wide constant. Today there is
# only one timeframe, so this has exactly one entry — but the shape now
# correctly says "1h candles support these horizons" rather than "the
# platform supports these horizons forever," which is what SUPPORTED_HORIZONS
# on its own actually claimed before this fix.
HORIZONS_BY_TIMEFRAME: Dict[str, Dict[int, str]] = {"1h": SUPPORTED_HORIZONS}


def available_horizons_for_timeframe(timeframe: Optional[str]) -> Dict[int, str]:
    """claude code changed: new. The capability lookup validate_spec() and
    the Tool Layer should use instead of assuming SUPPORTED_HORIZONS
    applies to every timeframe — an unrecognized timeframe honestly has no
    available horizons (empty dict), not a silent fallback to 1h's set."""
    return HORIZONS_BY_TIMEFRAME.get(timeframe, {})

# claude code changed: moved from data_availability.py (single source now).
# The exact, real output columns bot/research/feature_calculator.py
# produces, minus forward_return_*/win_* which are labels, not features a
# hypothesis would reference as a signal input or condition.
DERIVABLE_FROM_OHLCV = {
    "atr", "adr", "range_used", "volatility_state", "efficiency", "atr_ratio",
    "realized_vol", "volume_ratio", "rsi", "ema_12", "ema_26", "adx",
    "atr_indicator", "bb_upper", "bb_middle", "bb_lower", "bb_width",
    "macd", "macd_signal", "macd_histogram", "volume",
}

SUPPORTED_DIRECTIONS = ["positive", "negative", "neutral"]
RISK_TIERS = ["LOW", "MEDIUM", "HIGH"]

# claude code changed: new — Conditional Hypothesis Integrity fix.
# "feature": a continuous-relationship hypothesis ("higher RSI is
# associated with higher subsequent returns") — tested by the existing
# run_statistical_test (Spearman IC) path, unchanged.
# "conditional": an event hypothesis ("WHEN RSI falls below 30, subsequent
# returns are higher") — tested by the new run_conditional_test path
# (tools/conditional_tools.py), never by run_statistical_test.
#
# claude code changed: new — "pairs": a relationship hypothesis between
# TWO assets ("are asset and asset_b cointegrated, with a stable
# mean-reverting spread?") — tested via run_cointegration_test
# (tools/research_tools.py, wraps cointegration_engine.py), never by
# run_statistical_test/run_conditional_test. Gated PRO by
# capability_registry.py's cointegration_pairs_research capability — see
# entitlements.py. Advanced Quant Research Capability Architecture.
HYPOTHESIS_TYPES = ["feature", "conditional", "pairs"]

# claude code changed: new — Multi-Asset Foundation Refactor STEP 10. A
# controlled, closed representation of how many instruments a piece of
# research actually touches — section 10 of the refactor brief: "prevent
# the current single-asset assumption from becoming embedded throughout
# the architecture." Deliberately NOT a field a caller sets independently
# of hypothesis_type (that would let the two disagree — a spec claiming
# research_scope=CROSS_SECTIONAL while hypothesis_type=="feature" would be
# a second, inconsistent source of truth for the same fact). See
# ResearchSpec.research_scope below — it's computed, not stored.
RESEARCH_SCOPES = ["SINGLE_ASSET", "PAIR", "CROSS_SECTIONAL", "PORTFOLIO", "NETWORK"]

# claude code changed: new — the only condition operators the interpreter
# and the conditional-test tool agree to support in this pass. A relative
# condition ("volume > average") is deliberately NOT expressible here —
# see interpreter.py's own comment on why that's left honestly ambiguous
# rather than approximated.
CONDITION_OPERATORS = ["<", "<=", ">", ">="]

# claude code changed: new — the fixed, closed set of fields the spec
# tracks as either present-and-confident or explicitly ambiguous. Section 6:
# "If the AI cannot confidently determine an important field, it must
# explicitly mark the field as ambiguous rather than inventing it." A field
# name outside this set can never be marked ambiguous (typo-proofing).
SPEC_FIELDS = ["asset", "asset_b", "asset_class", "timeframe", "direction", "target", "features", "conditions", "hypothesis_type"]  # claude code changed: +asset_b — Advanced Quant Research Capability Architecture, pairs hypotheses; +asset_class — Multi-Asset Foundation Refactor STEP 5


@dataclass
class ResearchSpec:
    """
    The canonical structured research specification. Every field below is
    either directly usable by the Data Availability Checker / Policy Gate /
    Tool Layer, or explicitly listed in `ambiguous_fields` — there is no
    third state where a field is silently guessed.
    """

    hypothesis_text: str

    asset: Optional[str] = None
    asset_b: Optional[str] = None  # claude code changed: new — Advanced Quant Research Capability Architecture. Only meaningful when hypothesis_type=="pairs"; a pair is exactly (asset, asset_b)
    # claude code changed: new — Multi-Asset Foundation Refactor STEP 5.
    # Optional and independent of `asset` on purpose: this lets a future AI
    # interpreter declare "the student wants FOREX research" before any
    # specific instrument is even confirmed (section 15 of the refactor
    # brief's future-AI-input-context requirement), without forcing every
    # existing single-asset spec to start setting it. See
    # `resolved_asset_class` below for the value actually trusted once
    # `asset` is known — this field is intent, that property is fact.
    asset_class: Optional[str] = None
    timeframe: Optional[str] = None
    hypothesis_type: str = "feature"  # claude code changed: new — "feature" | "conditional", see module docstring
    features: List[str] = field(default_factory=list)
    # claude code changed: was List[str] (never populated/used). Now a real,
    # validated list of {"feature": str, "operator": "<"|"<="|">"|">=",
    # "threshold": float} dicts — only meaningful when hypothesis_type ==
    # "conditional".
    conditions: List[Dict] = field(default_factory=list)
    direction: Optional[str] = None
    target: Dict = field(default_factory=dict)  # {"type": "forward_return", "horizon": int}
    data_requirements: List[Dict] = field(default_factory=list)  # [{"source": str, "availability": "ingested"|"not_ingested"}]
    research_methods: List[str] = field(default_factory=list)
    risk_tier: str = "LOW"

    # claude code changed: new — explicit ambiguity list, section 6's own
    # requirement. Any field named here is treated as unresolved regardless
    # of whatever value currently sits in it (a placeholder/default value is
    # not evidence the field was actually understood).
    ambiguous_fields: List[str] = field(default_factory=list)

    @property
    def resolved_asset_class(self) -> Optional[str]:
        """
        claude code changed: new — Multi-Asset Foundation Refactor STEP 5.
        The AUTHORITATIVE asset class, looked up from the instrument
        registry for `self.asset` — never trusted from the caller-settable
        `asset_class` field when the two could disagree. Falls back to the
        explicitly-declared `asset_class` only when `asset` isn't (yet, or
        never will be) a registered instrument — e.g. during interpreter
        suggestion, before a specific symbol is confirmed.
        """
        if self.asset:
            instrument = get_instrument(self.asset)
            if instrument is not None:
                return instrument.asset_class
        return self.asset_class

    @property
    def research_scope(self) -> str:
        """claude code changed: new — Multi-Asset Foundation Refactor STEP
        10. Computed from hypothesis_type, never independently settable
        (see RESEARCH_SCOPES' own comment for why). Every experiment that
        predates this field implicitly returns SINGLE_ASSET, exactly as
        the refactor brief's section 10 requires."""
        if self.hypothesis_type == "pairs":
            return "PAIR"
        return "SINGLE_ASSET"

    @property
    def instruments(self) -> List[str]:
        """claude code changed: new — the canonical symbols this spec
        actually touches, regardless of hypothesis_type. [asset] for a
        single-asset/conditional spec, [asset, asset_b] for a pairs spec.
        A future CROSS_SECTIONAL/PORTFOLIO hypothesis_type would extend
        this list without any existing caller needing to change how it
        asks "which instruments does this spec need data for.\""""
        return [s for s in (self.asset, self.asset_b) if s]

    def to_dict(self) -> Dict:
        return {
            "hypothesis_text": self.hypothesis_text,
            "asset": self.asset,
            "asset_b": self.asset_b,
            "asset_class": self.asset_class,
            "timeframe": self.timeframe,
            "hypothesis_type": self.hypothesis_type,
            "features": list(self.features),
            "conditions": [dict(c) for c in self.conditions],  # claude code changed: deep-ish copy — conditions are now dicts, not strings
            "direction": self.direction,
            "target": dict(self.target),
            "data_requirements": list(self.data_requirements),
            "research_methods": list(self.research_methods),
            "risk_tier": self.risk_tier,
            "ambiguous_fields": list(self.ambiguous_fields),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ResearchSpec":
        return cls(
            hypothesis_text=data.get("hypothesis_text", ""),
            asset=data.get("asset"),
            asset_b=data.get("asset_b"),
            asset_class=data.get("asset_class"),
            timeframe=data.get("timeframe"),
            hypothesis_type=data.get("hypothesis_type", "feature"),
            features=list(data.get("features", [])),
            conditions=[dict(c) for c in data.get("conditions", [])],
            direction=data.get("direction"),
            target=dict(data.get("target", {})),
            data_requirements=list(data.get("data_requirements", [])),
            research_methods=list(data.get("research_methods", [])),
            risk_tier=data.get("risk_tier", "LOW"),
            ambiguous_fields=list(data.get("ambiguous_fields", [])),
        )


@dataclass
class SpecValidationResult:
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    unresolved_ambiguous_fields: List[str] = field(default_factory=list)


def validate_spec(spec: ResearchSpec) -> SpecValidationResult:
    """
    Schema validation per section 6: "The schema must be validated before
    any research is executed." Real, checked constraints — not a shape-only
    check. Any field the interpreter marked ambiguous makes the spec
    invalid regardless of what value sits in it, since that value was never
    confidently determined.
    """
    errors: List[str] = []

    unresolved = [f for f in spec.ambiguous_fields if f in SPEC_FIELDS]  # claude code changed: ignore garbage field names rather than trusting them
    if unresolved:
        errors.append(f"ambiguous fields require clarification: {', '.join(unresolved)}")

    if not spec.hypothesis_text or not spec.hypothesis_text.strip():
        errors.append("hypothesis_text is required")

    if spec.hypothesis_type not in HYPOTHESIS_TYPES:
        errors.append(f"hypothesis_type '{spec.hypothesis_type}' must be one of {HYPOTHESIS_TYPES}")

    if "asset" not in unresolved:
        if not spec.asset:
            errors.append("asset is required")
        elif spec.asset not in SUPPORTED_ASSETS:
            errors.append(f"asset '{spec.asset}' is not in the supported universe ({len(SUPPORTED_ASSETS)} symbols)")

    # claude code changed: new — Multi-Asset Foundation Refactor STEP 5.
    # asset_class is optional (None is valid — it means "infer from asset",
    # which is what every spec predating this field implicitly does). When
    # it IS set, it must be a real asset class, and if `asset` already
    # resolves to a known instrument, the two must agree — this is the one
    # place a future AI-declared "I want FOREX research" could disagree
    # with an actually-picked crypto symbol, and that disagreement must be
    # a validation error, not silently resolved in either direction.
    if "asset_class" not in unresolved and spec.asset_class is not None:
        if spec.asset_class not in ASSET_CLASSES:
            errors.append(f"asset_class '{spec.asset_class}' must be one of {ASSET_CLASSES}")
        elif spec.asset:
            instrument = get_instrument(spec.asset)
            if instrument is not None and instrument.asset_class != spec.asset_class:
                errors.append(
                    f"asset_class '{spec.asset_class}' does not match asset '{spec.asset}', "
                    f"which is {instrument.asset_class}"
                )

    if "timeframe" not in unresolved:
        if not spec.timeframe:
            errors.append("timeframe is required")
        elif spec.timeframe not in SUPPORTED_TIMEFRAMES:
            errors.append(f"timeframe '{spec.timeframe}' is not supported (only {SUPPORTED_TIMEFRAMES} available)")

    if "direction" not in unresolved and spec.direction is not None:
        if spec.direction not in SUPPORTED_DIRECTIONS:
            errors.append(f"direction '{spec.direction}' must be one of {SUPPORTED_DIRECTIONS}")

    # claude code changed: new — Advanced Quant Research Capability
    # Architecture. A pairs (cointegration) hypothesis has no forward-return
    # target at all — run_cointegration_test(asset_a, asset_b) tests spread
    # stationarity, not IC against a forward-return horizon. Requiring
    # target.horizon for a pairs spec would demand a field the tool never
    # reads and could never satisfy meaningfully, so this whole block is
    # scoped away from hypothesis_type=="pairs" rather than forcing a
    # placeholder horizon value through it.
    if spec.hypothesis_type != "pairs" and "target" not in unresolved:
        target_type = spec.target.get("type")
        if not target_type:
            errors.append("target.type is required")
        elif target_type not in SUPPORTED_TARGET_TYPES:
            errors.append(f"target.type '{target_type}' is not supported (only {SUPPORTED_TARGET_TYPES} available)")

        horizon = spec.target.get("horizon")
        if horizon is None:
            errors.append("target.horizon is required")
        elif not isinstance(horizon, int) or horizon <= 0:
            errors.append("target.horizon must be a positive integer (candles)")
        else:
            # claude code changed: real horizon-set validation, was missing
            # entirely (Bug 4). This is now checked at the SAME point
            # tools/statistical_tools.py enforces it — a horizon that would
            # fail at execution time now fails here instead, before the
            # user ever sees a Research Plan screen.
            #
            # claude code changed: Multi-Asset Foundation Refactor STEP 3
            # — now looked up per spec.timeframe via
            # available_horizons_for_timeframe() instead of testing
            # membership in the bare SUPPORTED_HORIZONS constant. Identical
            # behavior today (spec.timeframe can only be "1h", which maps
            # to exactly the same dict) — but a horizon is now honestly
            # validated as "available for THIS timeframe," not "available
            # on the platform, forever, regardless of timeframe."
            horizons = available_horizons_for_timeframe(spec.timeframe)
            if horizon not in horizons:
                errors.append(
                    f"target.horizon={horizon} is not a supported horizon for timeframe '{spec.timeframe}' — only "
                    f"{sorted(horizons)} candles are available as labels"
                )

    # claude code changed: new — Advanced Quant Research Capability
    # Architecture. asset_b is only meaningful (and only required) for a
    # pairs hypothesis — mirrors the asset validation above, plus the
    # pairs-specific "must be two distinct assets" rule a single-asset
    # spec has no equivalent of.
    if spec.hypothesis_type == "pairs" and "asset_b" not in unresolved:
        if not spec.asset_b:
            errors.append("asset_b is required for a pairs hypothesis")
        elif spec.asset_b not in SUPPORTED_ASSETS:
            errors.append(f"asset_b '{spec.asset_b}' is not in the supported universe ({len(SUPPORTED_ASSETS)} symbols)")
        elif spec.asset and spec.asset_b == spec.asset:
            errors.append("asset_b must differ from asset — a pair needs two distinct assets")

    # claude code changed: new — Conditional Hypothesis Integrity fix.
    # "conditions" is only in SPEC_FIELDS's ambiguous-check list, not the
    # unresolved-skip list below, on purpose: even if the interpreter
    # didn't mark "conditions" ambiguous, a hypothesis_type=="conditional"
    # spec with no actual condition content must still fail here — silence
    # is not confirmation.
    if spec.hypothesis_type == "conditional":
        if not spec.conditions:
            errors.append("hypothesis_type is 'conditional' but no condition was specified")
        for c in spec.conditions:
            feature = c.get("feature")
            operator = c.get("operator")
            threshold = c.get("threshold")
            if feature not in DERIVABLE_FROM_OHLCV:
                errors.append(f"condition feature '{feature}' is not a feature this platform can calculate")
            if operator not in CONDITION_OPERATORS:
                errors.append(f"condition operator '{operator}' must be one of {CONDITION_OPERATORS}")
            if not isinstance(threshold, (int, float)):
                errors.append(f"condition threshold must be numeric, got {threshold!r}")

    if spec.risk_tier not in RISK_TIERS:
        errors.append(f"risk_tier '{spec.risk_tier}' must be one of {RISK_TIERS}")

    return SpecValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        unresolved_ambiguous_fields=unresolved,
    )
