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
SUPPORTED_DIRECTIONS = ["positive", "negative", "neutral"]
RISK_TIERS = ["LOW", "MEDIUM", "HIGH"]

# claude code changed: new — the fixed, closed set of fields the spec
# tracks as either present-and-confident or explicitly ambiguous. Section 6:
# "If the AI cannot confidently determine an important field, it must
# explicitly mark the field as ambiguous rather than inventing it." A field
# name outside this set can never be marked ambiguous (typo-proofing).
SPEC_FIELDS = ["asset", "timeframe", "direction", "target", "features", "conditions"]


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
    features: List[str] = field(default_factory=list)
    conditions: List[str] = field(default_factory=list)
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
            "features": list(self.features),
            "conditions": list(self.conditions),
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
            features=list(data.get("features", [])),
            conditions=list(data.get("conditions", [])),
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

    if spec.risk_tier not in RISK_TIERS:
        errors.append(f"risk_tier '{spec.risk_tier}' must be one of {RISK_TIERS}")

    return SpecValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        unresolved_ambiguous_fields=unresolved,
    )
