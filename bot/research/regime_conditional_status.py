# ============================================================
# bot/research/regime_conditional_status.py
#
# REGIME-AWARE READINESS VERDICT (Phase 18)
#
# claude code changed: new file — Regime-Conditional Quantitative Research
# mission. Produces exactly the mission's own nine-state vocabulary
# (NO_EVIDENCE / STATISTICALLY_DETECTABLE / REGIME_DEPENDENT /
# OOS_SUPPORTED / PERMUTATION_SUPPORTED / ECONOMICALLY_SUPPORTED /
# RESEARCH_NEGATIVE / INSUFFICIENT_SAMPLE / INSUFFICIENT_DATA) — never a
# plain PROFITABLE/NOT PROFITABLE verdict, per the mission's explicit rule.
#
# DELIBERATELY SEPARATE FROM bot.research_lab.verdict.compute_verdict():
# that function answers "is there an edge at all" for ONE, single-
# hypothesis, whole-dataset test, and already has a formally defined but
# intentionally UNREACHABLE REGIME_DEPENDENT state in its own 9-state
# taxonomy (see that file's own docstring — "this MVP pass has no
# regime-stratified tool output... rather than fabricating a path to them
# here"). This module is the regime-stratified tool output that was
# missing — but it answers a DIFFERENT question ("under which regime does
# this relationship hold up, and how far does the evidence go") and uses
# a DIFFERENT vocabulary on purpose. compute_verdict() is NOT modified by
# this mission; wiring these two together (e.g. having a real
# ResearchExperiment's verdict field ever actually read REGIME_DEPENDENT)
# is a genuine, separate orchestrator/data-model decision, named as an
# open item in this mission's final report rather than made unilaterally
# here.
#
# THE CORE ANTI-CHERRY-PICKING RULE (the mission's explicit "must not be
# promoted simply because one regime looks strong"): this function
# requires the CALLER to name one `regime_of_interest` up front — it never
# scans multiple regimes internally and reports back whichever looked
# best. And no promotion past STATISTICALLY_DETECTABLE is possible
# without the DIRECT interaction test (Phase 10) being significant first
# — a regime that merely "happens to survive FDR while another doesn't"
# is not, by itself, evidence the relationship is regime-dependent; only
# a significant regime x signal interaction term earns that status.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

REGIME_CONDITIONAL_STATUSES = (
    "NO_EVIDENCE",
    "STATISTICALLY_DETECTABLE",
    "REGIME_DEPENDENT",
    "OOS_SUPPORTED",
    "PERMUTATION_SUPPORTED",
    "ECONOMICALLY_SUPPORTED",
    "RESEARCH_NEGATIVE",
    "INSUFFICIENT_SAMPLE",
    "INSUFFICIENT_DATA",
)

STATUS_CRITERIA_VERSION = "1.0.0"


@dataclass
class RegimeConditionalStatus:
    status: str
    explanation: List[str] = field(default_factory=list)
    criteria_version: str = STATUS_CRITERIA_VERSION

    def to_dict(self) -> Dict:
        return {"status": self.status, "explanation": list(self.explanation), "criteria_version": self.criteria_version}


def _find_regime_cell(ic_report, regime_of_interest: str):
    for dimension in ("trend_state", "volatility_state", "regime_label"):
        cell = ic_report.cell(dimension, regime_of_interest)
        if cell is not None:
            return cell
    return None


def compute_regime_conditional_status(
    regime_of_interest: str,
    ic_report=None,   # claude code changed: was required (no default) — now optional, see the cross-sectional entry path below. RegimeConditionalICReport from regime_conditional_ic.py
    interaction_result: Optional[Dict] = None,   # from regime_conditional_ic.test_regime_interaction()
    oos_by_regime=None,   # RegimeConditionalOOSResult from regime_conditional_oos.py, optional
    permutation_verdict_for_regime: Optional[Dict] = None,   # verdict_by_regime[regime] from regime_conditional_permutation.py, optional
    min_economic_net_return_bps: float = 0.0,
) -> RegimeConditionalStatus:
    """
    Deterministic, evidence-only mapping — every branch below is an
    explicit, checkable rule, mirroring bot.research_lab.verdict.
    compute_verdict()'s own "no branch is judgment" discipline. Later
    evidence pieces (oos_by_regime, permutation_verdict_for_regime) are
    optional: a caller who only ran the IC/interaction stage gets a
    status no stronger than REGIME_DEPENDENT, honestly reflecting how far
    the evidence actually goes.

    claude code changed: new — OOS/permutation regime-conditional wiring,
    cross-sectional side. ic_report is now optional (was required) to
    support a SECOND, genuinely different evidence shape: a cross-
    sectional ranking hypothesis (bot.research_lab.tools.regime_tools.
    run_cross_sectional_regime_conditional_test), which has no per-feature
    IC or direct-interaction stage at all — regime_conditional_ic.py's
    machinery is single-asset-feature-shaped
    (compute_regime_conditional_ic/test_regime_interaction both require a
    feature_col/forward_return_col pair for ONE asset's own returns),
    while regime_conditional_permutation.py's
    run_regime_conditional_permutation_test() is multi-asset-panel-shaped
    (ranks many assets against each other). Forcing the cross-sectional
    case through the IC-first gate below would silently misrepresent what
    was actually tested. When ic_report is None, this delegates to
    _compute_cross_sectional_status() — a real, separate ladder, not a
    fallback default — documented at that function's own definition for
    why REGIME_DEPENDENT (which depends on the direct interaction test,
    a concept that has no cross-sectional equivalent here) is correctly
    never reachable on this path, and OOS_SUPPORTED is the honest floor
    instead.
    """
    explanation: List[str] = []

    if ic_report is None:
        return _compute_cross_sectional_status(
            regime_of_interest, oos_by_regime, permutation_verdict_for_regime, min_economic_net_return_bps,
        )

    overall_cell = ic_report.cell("OVERALL", "ALL")
    if overall_cell is None or overall_cell.n_obs == 0:
        return RegimeConditionalStatus("INSUFFICIENT_DATA", ["no observations available for this feature/horizon at all"])

    regime_cell = _find_regime_cell(ic_report, regime_of_interest)
    if regime_cell is None:
        return RegimeConditionalStatus("INSUFFICIENT_DATA", [f"regime {regime_of_interest!r} was never observed in this dataset"])

    if not regime_cell.sufficient_sample:
        return RegimeConditionalStatus(
            "INSUFFICIENT_SAMPLE",
            [f"regime {regime_of_interest!r} has {regime_cell.n_obs} observations, below the required minimum"],
        )

    if not overall_cell.passes_fdr and not regime_cell.passes_fdr:
        return RegimeConditionalStatus(
            "NO_EVIDENCE",
            [f"neither the overall row (passes_fdr={overall_cell.passes_fdr}) nor regime {regime_of_interest!r} "
             f"(passes_fdr={regime_cell.passes_fdr}) survives FDR correction"],
        )

    explanation.append(
        f"regime {regime_of_interest!r}: IC={regime_cell.ic:+.4f}, FDR-adjusted p={regime_cell.ic_pvalue_fdr:.4f}, passes_fdr={regime_cell.passes_fdr}"
    )
    status = "STATISTICALLY_DETECTABLE"

    # claude code changed: the anti-cherry-picking gate — no promotion
    # past STATISTICALLY_DETECTABLE without a significant DIRECT
    # interaction test (Phase 10), never inferred from this regime cell's
    # significance alone.
    if interaction_result is None or interaction_result.get("status") != "OK":
        explanation.append("interaction test not available (not run, or INSUFFICIENT_SAMPLE) — cannot establish regime-specificity, stopping at STATISTICALLY_DETECTABLE")
        return RegimeConditionalStatus(status, explanation)
    if not interaction_result.get("interaction_significant"):
        explanation.append(f"interaction test not significant (p={interaction_result.get('p_value')}) — evidence for regime-SPECIFICITY is not established, stopping at STATISTICALLY_DETECTABLE")
        return RegimeConditionalStatus(status, explanation)

    status = "REGIME_DEPENDENT"
    explanation.append(f"interaction test significant (p={interaction_result['p_value']:.4g}) — the relationship genuinely differs by regime")

    if oos_by_regime is not None:
        regime_oos = oos_by_regime.pooled_by_regime.get(regime_of_interest)
        if regime_oos is None:
            explanation.append(f"no OOS result available for regime {regime_of_interest!r} — stopping at REGIME_DEPENDENT")
            return RegimeConditionalStatus(status, explanation)
        if not regime_oos["sufficient_sample"]:
            explanation.append(f"OOS sample for regime {regime_of_interest!r} is insufficient — stopping at REGIME_DEPENDENT")
            return RegimeConditionalStatus(status, explanation)
        mean_net_return = regime_oos["metrics"].get("mean_net_return")
        if mean_net_return is None or mean_net_return <= 0:
            explanation.append(f"OOS mean net return for this regime is {mean_net_return} — not positive out-of-sample")
            return RegimeConditionalStatus("RESEARCH_NEGATIVE", explanation)
        status = "OOS_SUPPORTED"
        explanation.append(f"OOS mean net return for this regime is positive ({mean_net_return:+.6f})")
    else:
        explanation.append("no OOS evidence supplied — stopping at REGIME_DEPENDENT")
        return RegimeConditionalStatus(status, explanation)

    if permutation_verdict_for_regime is not None:
        if permutation_verdict_for_regime.get("status") != "OK":
            explanation.append(f"permutation test for this regime was not evaluable ({permutation_verdict_for_regime.get('status')}) — stopping at OOS_SUPPORTED")
            return RegimeConditionalStatus(status, explanation)
        if not permutation_verdict_for_regime.get("edge_appears_real"):
            explanation.append("permutation test does NOT confirm the edge for this regime (fails to beat the null on at least one configured metric) — demoted")
            return RegimeConditionalStatus("RESEARCH_NEGATIVE", explanation)
        status = "PERMUTATION_SUPPORTED"
        explanation.append("permutation test confirms edge_appears_real for this regime")
    else:
        explanation.append("no permutation evidence supplied — stopping at OOS_SUPPORTED")
        return RegimeConditionalStatus(status, explanation)

    # Economic significance uses the same OOS metrics already fetched above
    net_return_bps = oos_by_regime.pooled_by_regime[regime_of_interest]["metrics"].get("mean_net_return", 0.0) * 10_000
    if net_return_bps > min_economic_net_return_bps:
        status = "ECONOMICALLY_SUPPORTED"
        explanation.append(f"economically meaningful after real transaction costs: {net_return_bps:+.2f}bps mean net return per period")
    else:
        explanation.append(f"economic significance NOT established: {net_return_bps:+.2f}bps mean net return, at or below the {min_economic_net_return_bps}bps threshold")
        return RegimeConditionalStatus("RESEARCH_NEGATIVE", explanation)

    return RegimeConditionalStatus(status, explanation)


# claude code changed: new — OOS/permutation regime-conditional wiring,
# cross-sectional side. See compute_regime_conditional_status()'s own
# docstring for why this is a genuinely separate ladder, not a shortcut.
def _compute_cross_sectional_status(
    regime_of_interest: str,
    oos_by_regime,
    permutation_verdict_for_regime: Optional[Dict],
    min_economic_net_return_bps: float,
) -> RegimeConditionalStatus:
    """
    Evidence ladder for a cross-sectional ranking hypothesis, entered
    directly from OOS evidence — there is no IC/interaction stage for
    this hypothesis shape to pass through first (see
    bot.research.regime_conditional_permutation's own module docstring:
    the existing within-timestamp shuffle already IS this hypothesis
    family's null; there is no separate "does the relationship differ by
    regime" interaction test to run, because the regime-sliced OOS result
    itself, compared against its own regime-sliced permutation null, is
    the direct test). Reuses regime_conditional_status.py's OWN
    REGIME_CONDITIONAL_STATUSES vocabulary (never a third, competing
    vocabulary) — just never passes through NO_EVIDENCE/
    STATISTICALLY_DETECTABLE/REGIME_DEPENDENT, all three of which are
    meaningless without an IC/interaction stage behind them. OOS_SUPPORTED
    is therefore the honest floor a real, positive result can reach here,
    exactly mirroring how the IC-based ladder above treats
    STATISTICALLY_DETECTABLE as ITS floor before any interaction evidence
    exists.

    Anti-cherry-picking note: the same structural safeguard as the
    IC-based path applies unchanged — regime_of_interest has no default
    (see test_never_promotes_past_statistically_detectable_without_being_
    asked_for_a_specific_regime, which checks the shared function
    signature both paths go through), so a caller must still declare
    which regime it cares about before any evidence is computed, not
    after seeing which one looks best.
    """
    if oos_by_regime is None:
        return RegimeConditionalStatus("INSUFFICIENT_DATA", ["no OOS evidence was supplied for this cross-sectional hypothesis"])

    regime_result = oos_by_regime.pooled_by_regime.get(regime_of_interest)
    if regime_result is None:
        return RegimeConditionalStatus("INSUFFICIENT_DATA", [f"regime {regime_of_interest!r} was never observed with sufficient sample in this OOS evaluation"])
    if not regime_result.get("sufficient_sample"):
        return RegimeConditionalStatus("INSUFFICIENT_SAMPLE", [f"regime {regime_of_interest!r} has insufficient OOS sample"])

    mean_net_return = regime_result["metrics"].get("mean_net_return")
    explanation = [f"regime {regime_of_interest!r} real OOS mean net return: {mean_net_return}"]
    if mean_net_return is None or mean_net_return <= 0:
        explanation.append("OOS mean net return for this regime is not positive")
        return RegimeConditionalStatus("RESEARCH_NEGATIVE", explanation)

    status = "OOS_SUPPORTED"
    explanation.append("OOS mean net return for this regime is positive")

    if permutation_verdict_for_regime is None:
        explanation.append("no permutation evidence supplied — stopping at OOS_SUPPORTED")
        return RegimeConditionalStatus(status, explanation)
    if permutation_verdict_for_regime.get("status") != "OK":
        explanation.append(f"permutation test for this regime was not evaluable ({permutation_verdict_for_regime.get('status')}) — stopping at OOS_SUPPORTED")
        return RegimeConditionalStatus(status, explanation)
    if not permutation_verdict_for_regime.get("edge_appears_real"):
        explanation.append("permutation test does NOT confirm the edge for this regime — demoted")
        return RegimeConditionalStatus("RESEARCH_NEGATIVE", explanation)

    status = "PERMUTATION_SUPPORTED"
    explanation.append("permutation test confirms edge_appears_real for this regime")

    net_return_bps = mean_net_return * 10_000
    if net_return_bps > min_economic_net_return_bps:
        explanation.append(f"economically meaningful after real transaction costs: {net_return_bps:+.2f}bps mean net return per period")
        return RegimeConditionalStatus("ECONOMICALLY_SUPPORTED", explanation)

    explanation.append(f"economic significance NOT established: {net_return_bps:+.2f}bps mean net return, at or below the {min_economic_net_return_bps}bps threshold")
    return RegimeConditionalStatus("RESEARCH_NEGATIVE", explanation)
