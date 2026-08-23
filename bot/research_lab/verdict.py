# ============================================================
# bot/research_lab/verdict.py
# Research Lab — the deterministic verdict engine (section 14).
#
# claude code changed: new file. Every acceptance-criteria constant below
# is explicit and version-controlled (VERDICT_CRITERIA_VERSION) — Claude
# never decides a verdict "from intuition"; it can only explain a verdict
# this module already reached from evidence already produced by the Tool
# Layer. IC thresholds reuse bot/research/feature_decay_analyzer.py's own
# named constants rather than inventing new ones (section 25).
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from bot.research.feature_decay_analyzer import IC_STRONG, IC_MODERATE, IC_WEAK  # claude code changed: reuse, section 25

VERDICT_CRITERIA_VERSION = "1.0.0"

MIN_SAMPLE_SIZE = 1000  # claude code changed: below this, even a "significant" result isn't trustworthy — matches this project's WINSOR_MIN_PERIODS-style floors elsewhere
ALPHA = 0.05


@dataclass
class VerdictResult:
    verdict: str
    explanation: List[str] = field(default_factory=list)
    criteria_version: str = VERDICT_CRITERIA_VERSION

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "explanation": list(self.explanation), "criteria_version": self.criteria_version}


def compute_verdict(
    statistical_test: Optional[Dict],
    fdr_correction: Optional[Dict],
    hypothesized_direction: Optional[str] = None,
) -> VerdictResult:
    """
    Deterministic mapping from evidence already produced by the Tool
    Layer onto exactly one of the nine taxonomy states. Every branch below
    is an explicit, checkable rule — no branch is "judgment."

    claude code changed: REGIME_DEPENDENT and SUPERSEDED_BY_EXISTING_RESEARCH
    are real states in the taxonomy but this MVP pass has no regime-
    stratified tool output and no validated_feature_registry.py cross-
    reference step to ever reach them honestly — see the final engineering
    report's Known Limitations section rather than fabricating a path to
    them here.
    """
    explanation: List[str] = []

    if statistical_test is None:
        return VerdictResult("INCONCLUSIVE", ["no statistical test was run for this experiment"])

    sample_size = statistical_test.get("sample_size", 0)
    if sample_size < MIN_SAMPLE_SIZE:
        return VerdictResult(
            "INVALID_RESEARCH",
            [f"sample_size={sample_size} is below the {MIN_SAMPLE_SIZE}-observation floor for a trustworthy result"],
        )

    ic = statistical_test.get("ic")
    if ic is None:
        return VerdictResult("INVALID_RESEARCH", ["statistical test produced no usable IC value"])

    block_p = statistical_test.get("block_permutation_p_value")
    explanation.append(f"IC={ic:+.4f}, block-permutation p={block_p:.4f} (alpha={ALPHA})")

    if hypothesized_direction in ("positive", "negative"):
        actual_direction = "positive" if ic > 0 else "negative" if ic < 0 else "neutral"
        if actual_direction != "neutral" and actual_direction != hypothesized_direction:
            explanation.append(f"hypothesis claimed direction={hypothesized_direction}, evidence shows {actual_direction}")
            return VerdictResult("REJECTED", explanation)

    is_raw_significant = block_p is not None and block_p < ALPHA
    passes_fdr = fdr_correction.get("passes_fdr") if fdr_correction else None

    if not is_raw_significant:
        explanation.append("not statistically significant even before multiple-testing correction")
        return VerdictResult("REJECTED" if abs(ic) < IC_WEAK else "INCONCLUSIVE", explanation)

    if passes_fdr is False:
        explanation.append("significant before correction, but failed FDR correction — likely a false positive")
        return VerdictResult("INCONCLUSIVE", explanation)

    if passes_fdr is None:
        explanation.append("raw significance clears alpha, but no FDR correction was run for this experiment")
        strength_note = "requires FDR correction before a stronger verdict can be reached"
        explanation.append(strength_note)
        return VerdictResult("REQUIRES_REVIEW", explanation)

    # claude code changed: passes_fdr is True from here on — verdict strength graded by IC magnitude only
    abs_ic = abs(ic)
    if abs_ic >= IC_STRONG:
        explanation.append(f"|IC|={abs_ic:.4f} clears the strong threshold ({IC_STRONG}) and survives FDR correction")
        return VerdictResult("SUPPORTED", explanation)
    if abs_ic >= IC_MODERATE:
        explanation.append(f"|IC|={abs_ic:.4f} clears the moderate threshold ({IC_MODERATE}) and survives FDR correction")
        return VerdictResult("PARTIALLY_SUPPORTED", explanation)

    explanation.append(f"|IC|={abs_ic:.4f} survives FDR correction but is below the moderate threshold ({IC_MODERATE}) — statistically real but too weak to act on")
    return VerdictResult("INCONCLUSIVE", explanation)


# claude code changed: new — Conditional Hypothesis Integrity fix (Bug 7).
# compute_verdict() above is IC-specific (IC_STRONG/IC_MODERATE/IC_WEAK
# thresholds are meaningless for a conditional/event test's mean-difference
# statistic) and stays completely unchanged — a conditional hypothesis
# must NEVER be routed through it, since that would produce a confident
# SUPPORTED/REJECTED verdict for a research question that was never
# actually tested (the exact bug this fix targets). This is a genuinely
# separate function, not a branch bolted onto compute_verdict(), so the
# two evidence shapes (IC-based vs event-based) can never be silently
# cross-applied.
#
# No FDR correction exists for conditional/event tests in this pass (see
# tools/conditional_tools.py's module docstring and the final engineering
# report's Known Limitations) — this function's own significance gate is
# therefore the block-permutation p-value alone, at the same ALPHA.
CONDITIONAL_EFFECT_STRONG_P = 0.01  # claude code changed: new — stricter than ALPHA for a "strong" conditional verdict, mirroring compute_verdict()'s IC_STRONG vs IC_MODERATE two-tier grading


def compute_verdict_conditional(
    conditional_test: Optional[Dict],
    hypothesized_direction: Optional[str] = None,
) -> VerdictResult:
    """
    Deterministic verdict for a CONDITIONAL/event hypothesis, from
    run_conditional_test()'s evidence shape only (metric ==
    "conditional_event_test") — never from IC-based evidence.
    """
    explanation: List[str] = []

    if conditional_test is None:
        return VerdictResult("REQUIRES_REVIEW", ["hypothesis_type is 'conditional' but no conditional test was executed — the requested research question was not actually tested"])

    if conditional_test.get("metric") != "conditional_event_test":
        # claude code changed: defensive — if this is ever called with the
        # wrong evidence shape (e.g. a feature-test result passed in by
        # mistake), fail closed rather than misinterpret it.
        return VerdictResult("REQUIRES_REVIEW", ["evidence shape does not match a conditional/event test — the requested conditional hypothesis was not actually tested"])

    sample_size = conditional_test.get("sample_size", 0)
    if sample_size < MIN_SAMPLE_SIZE:
        return VerdictResult(
            "INVALID_RESEARCH",
            [f"sample_size={sample_size} is below the {MIN_SAMPLE_SIZE}-observation floor for a trustworthy result"],
        )

    observed_diff = conditional_test.get("observed_diff")
    block_p = conditional_test.get("block_permutation_p_value")
    if observed_diff is None or block_p is None:
        return VerdictResult("INVALID_RESEARCH", ["conditional test produced no usable difference-in-means or p-value"])

    explanation.append(
        f"mean return when condition true={conditional_test.get('mean_return_when_true'):+.4%}, "
        f"when false={conditional_test.get('mean_return_when_false'):+.4%}, "
        f"block-permutation p={block_p:.4f} (alpha={ALPHA})"
    )

    if hypothesized_direction in ("positive", "negative"):
        actual_direction = "positive" if observed_diff > 0 else "negative" if observed_diff < 0 else "neutral"
        if actual_direction != "neutral" and actual_direction != hypothesized_direction:
            explanation.append(f"hypothesis claimed direction={hypothesized_direction}, evidence shows {actual_direction}")
            return VerdictResult("REJECTED", explanation)

    if block_p >= ALPHA:
        explanation.append("not statistically significant under the block-permutation test")
        return VerdictResult("REJECTED" if abs(observed_diff) < 1e-6 else "INCONCLUSIVE", explanation)

    if block_p <= CONDITIONAL_EFFECT_STRONG_P:
        explanation.append(f"block-permutation p={block_p:.4f} clears the strong threshold ({CONDITIONAL_EFFECT_STRONG_P})")
        return VerdictResult("SUPPORTED", explanation)

    explanation.append(f"block-permutation p={block_p:.4f} is significant but above the strong threshold ({CONDITIONAL_EFFECT_STRONG_P})")
    return VerdictResult("PARTIALLY_SUPPORTED", explanation)
