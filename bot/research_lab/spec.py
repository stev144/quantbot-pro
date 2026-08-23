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
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from bot.fetch_all_symbols import SYMBOLS, INTERVAL  # claude code changed: reuse the canonical symbol universe/interval, section 25 — never a second copy

# claude code changed: this platform has exactly one native candle interval
# (fetch_all_symbols.py's INTERVAL='1h') — SUPPORTED_TIMEFRAMES is a list of
# one on purpose, not an oversight. A hypothesis naming a different
# timeframe is a real, honest INSUFFICIENT_DATA case, not something to
# silently coerce to 1h.
SUPPORTED_TIMEFRAMES = [INTERVAL]
SUPPORTED_ASSETS = list(SYMBOLS)

SUPPORTED_TARGET_TYPES = ["forward_return"]  # claude code changed: the only label type every tool in the Tool Layer (§08) can score against today

# claude code changed: moved from tools/statistical_tools.py (single
# source now, see module docstring). feature_calculator.py only ever
# produces these three forward-return label columns — verified by reading
# that module directly, not guessed. A horizon outside this set is a real,
# honest limit of the platform, and must be rejected at validation time,
# not discovered only after a tool call fails.
SUPPORTED_HORIZONS = {1: "forward_return_1h", 4: "forward_return_4h", 24: "forward_return_24h"}

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
HYPOTHESIS_TYPES = ["feature", "conditional"]

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
SPEC_FIELDS = ["asset", "timeframe", "direction", "target", "features", "conditions", "hypothesis_type"]


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

    def to_dict(self) -> Dict:
        return {
            "hypothesis_text": self.hypothesis_text,
            "asset": self.asset,
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

    if "timeframe" not in unresolved:
        if not spec.timeframe:
            errors.append("timeframe is required")
        elif spec.timeframe not in SUPPORTED_TIMEFRAMES:
            errors.append(f"timeframe '{spec.timeframe}' is not supported (only {SUPPORTED_TIMEFRAMES} available)")

    if "direction" not in unresolved and spec.direction is not None:
        if spec.direction not in SUPPORTED_DIRECTIONS:
            errors.append(f"direction '{spec.direction}' must be one of {SUPPORTED_DIRECTIONS}")

    if "target" not in unresolved:
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
        # claude code changed: new — real horizon-set validation, was
        # missing entirely (Bug 4). This is now checked at the SAME point
        # tools/statistical_tools.py enforces it — a horizon that would
        # fail at execution time now fails here instead, before the user
        # ever sees a Research Plan screen.
        elif horizon not in SUPPORTED_HORIZONS:
            errors.append(
                f"target.horizon={horizon} is not a supported horizon — only "
                f"{sorted(SUPPORTED_HORIZONS)} candles are available as labels"
            )

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
