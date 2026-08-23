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
from bot.research_lab.tools.conditional_tools import MIN_CONDITION_SAMPLE  # claude code changed: new — Statistical Integrity Hardening (gap #2), single source for "enough qualifying events," not a second constant that could drift from the tool layer's own floor

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
    insufficient_events: bool = False,
) -> VerdictResult:
    """
    Deterministic verdict for a CONDITIONAL/event hypothesis, from
    run_conditional_test()'s evidence shape only (metric ==
    "conditional_event_test") — never from IC-based evidence.

    claude code changed: new `insufficient_events` param — Statistical
    Integrity Hardening (gap #3). "The condition was never resolved"
    (REQUIRES_REVIEW, e.g. a relative threshold like "above average" that
    couldn't be parsed) and "the condition WAS resolved and computed but
    too few qualifying/non-qualifying candles exist to trust the
    comparison" (INSUFFICIENT_DATA — a real taxonomy state that was
    previously unreachable for any conditional hypothesis) are different
    failure modes with different remedies for the researcher. The
    orchestrator sets this flag when run_conditional_test() raised
    InsufficientEventsError specifically, not for any other tool failure.
    """
    explanation: List[str] = []

    if insufficient_events:
        return VerdictResult("INSUFFICIENT_DATA", ["the condition was computed, but too few qualifying (or too few non-qualifying) observations exist for a trustworthy comparison — see the experiment's warnings for the exact counts"])

    if conditional_test is None:
        return VerdictResult("REQUIRES_REVIEW", ["hypothesis_type is 'conditional' but no conditional test was executed — the requested research question was not actually tested"])

    if conditional_test.get("metric") != "conditional_event_test":
        # claude code changed: defensive — if this is ever called with the
        # wrong evidence shape (e.g. a feature-test result passed in by
        # mistake), fail closed rather than misinterpret it.
        return VerdictResult("REQUIRES_REVIEW", ["evidence shape does not match a conditional/event test — the requested conditional hypothesis was not actually tested"])

    # claude code changed: was `sample_size < MIN_SAMPLE_SIZE` — `sample_size`
    # here is n_condition_true + n_condition_false, effectively the WHOLE
    # dataset (~50k rows for this project), which clears a 1000-row floor
    # unconditionally regardless of how few qualifying EVENTS exist (gap
    # #2 — the exact "must not report the total dataset size as though it
    # represents the conditional sample" failure the hardening brief warns
    # against, except here it was the actual gating variable, not just a
    # display bug). The correct floor is on the smaller of the two group
    # sizes, using the SAME constant run_conditional_test() itself already
    # enforces (MIN_CONDITION_SAMPLE) — single source, not a second number
    # that could drift from the tool layer's own guard.
    n_true = conditional_test.get("n_condition_true", 0)
    n_false = conditional_test.get("n_condition_false", 0)
    if n_true < MIN_CONDITION_SAMPLE or n_false < MIN_CONDITION_SAMPLE:
        return VerdictResult(
            "INSUFFICIENT_DATA",
            [f"n_condition_true={n_true}, n_condition_false={n_false} — below the {MIN_CONDITION_SAMPLE}-observation floor for a trustworthy comparison"],
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


# claude code changed: new — Advanced Quant Research Capability
# Architecture. compute_verdict() (IC-based) and compute_verdict_conditional()
# (event-based) both stay completely unchanged — a pairs/cointegration
# hypothesis is a THIRD, genuinely different evidence shape (Engle-Granger
# ADF p-value + half-life, not IC or a mean-difference test) and gets its
# own function for the same reason those two are separate: never let one
# evidence shape's thresholds be silently applied to a different research
# question.


def compute_verdict_pairs(cointegration_test: Optional[Dict]) -> VerdictResult:
    """
    Deterministic verdict for a PAIRS/cointegration hypothesis, from
    run_cointegration_test()'s evidence shape only (a PairResult.to_dict()
    — has 'is_cointegrated'/'passes_filters'/'reject_reason' keys, never
    'ic' or 'metric'=='conditional_event_test').

    Unlike compute_verdict()/compute_verdict_conditional(), there is no
    hypothesized_direction parameter — "are these two assets cointegrated"
    has no directional claim to contradict, only a yes/no answer plus the
    half-life/tradability filter cointegration_engine.py itself already
    applies.
    """
    explanation: List[str] = []

    if cointegration_test is None:
        return VerdictResult("REQUIRES_REVIEW", ["hypothesis_type is 'pairs' but no cointegration test was executed — the requested research question was not actually tested"])

    if "is_cointegrated" not in cointegration_test:
        # claude code changed: defensive — same fail-closed pattern as
        # compute_verdict_conditional()'s metric-shape check.
        return VerdictResult("REQUIRES_REVIEW", ["evidence shape does not match a cointegration test — the requested pairs hypothesis was not actually tested"])

    reject_reason = (cointegration_test.get("reject_reason") or "").lower()
    if "insufficient" in reject_reason:
        return VerdictResult("INSUFFICIENT_DATA", [cointegration_test.get("reject_reason")])
    if "ols failed" in reject_reason:
        return VerdictResult("INVALID_RESEARCH", [cointegration_test.get("reject_reason")])

    coint_p = cointegration_test.get("coint_pvalue")
    half_life = cointegration_test.get("half_life_hours")
    is_cointegrated = bool(cointegration_test.get("is_cointegrated"))
    passes_filters = bool(cointegration_test.get("passes_filters"))

    explanation.append(
        f"coint_pvalue={coint_p}, half_life={half_life}h, "
        f"is_cointegrated={is_cointegrated}, passes_filters={passes_filters}"
        + (f" ({cointegration_test.get('reject_reason')})" if cointegration_test.get("reject_reason") else "")
    )

    if not is_cointegrated:
        explanation.append("the raw Engle-Granger test did not find this pair cointegrated")
        return VerdictResult("REJECTED", explanation)

    if passes_filters:
        explanation.append("cointegrated and passes the half-life/tradability filter")
        return VerdictResult("SUPPORTED", explanation)

    # claude code changed: cointegrated on the raw test, but rejected by
    # cointegration_engine.py's own half-life (or FDR, in a full-universe
    # run) filter — a real, partial finding, not a wrong one: the
    # statistical relationship exists, it just doesn't clear the
    # practical tradability bar. Not REJECTED (that would misstate what
    # the raw test actually found) and not SUPPORTED (a half-life outside
    # the accepted range is a real caveat, not a footnote).
    explanation.append("cointegrated on the raw test, but rejected by the half-life/tradability filter — a partial finding")
    return VerdictResult("PARTIALLY_SUPPORTED", explanation)
