# ============================================================
# bot/research_lab/orchestrator.py
# Research Lab — ties spec -> policy gate -> tool calls -> verdict ->
# ResearchExperiment together. This is the ONLY place that mutates an
# experiment's evidence/verdict fields once it leaves PENDING.
#
# claude code changed: new file. Architecture: User -> Hypothesis ->
# Specification -> Policy Gate -> Research Tools -> Python Engines ->
# Evidence -> Verdict -> AI Explanation (section 27.C's required diagram),
# implemented literally as this function's own control flow.
# ============================================================

from __future__ import annotations

import math
import subprocess

from django.utils import timezone

from bot.research_lab.capability_registry import capability_for_hypothesis_type  # claude code changed: new — Advanced Quant Research Capability Architecture
from bot.research_lab.data_availability import check_data_availability
from bot.research_lab.entitlements import ResearchEntitlementService  # claude code changed: new — Advanced Quant Research Capability Architecture
from bot.research_lab.interpreter import explain_evidence
from bot.research_lab.models import ResearchExperiment
from bot.research_lab.policy_gate import evaluate_policy
from bot.research_lab.spec import ResearchSpec, validate_spec
from bot.research_lab.tools import run_tool
from bot.research_lab.verdict import compute_verdict, compute_verdict_conditional, compute_verdict_pairs  # claude code changed: +compute_verdict_conditional (Conditional Hypothesis Integrity fix), +compute_verdict_pairs (Advanced Quant Research Capability Architecture)

DEFAULT_RANDOM_SEED = 42


def get_code_version() -> str:
    """Best-effort git SHA for reproducibility (section 16) — never raises;
    an experiment must still complete even if git isn't reachable (e.g. a
    packaged deploy without a .git directory)."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
    except Exception:
        return "unknown"


def active_experiment_count(student) -> int:
    return ResearchExperiment.objects.filter(
        student=student, status__in=["PENDING", "PLANNED", "RUNNING"]
    ).count()


def plan_experiment(experiment: ResearchExperiment) -> None:
    """
    Validates the spec, checks entitlement, checks data availability, and
    runs the Policy Gate — the section 11 "Research Plan" the student
    reviews before anything executes. Never calls a tool. Sets status to
    PLANNED (allowed) or BLOCKED (not allowed) — never RUNNING.

    claude code changed: new entitlement step — Advanced Quant Research
    Capability Architecture, section 16's required order (User ->
    Subscription Entitlement -> Research Capability Registry -> Research
    Policy Gate -> ...). This is DEFENSE IN DEPTH: the Formalize view
    (views/formalize.py) already refuses to save a structured_spec whose
    capability the student isn't entitled to, but this function must not
    trust that a spec reaching PENDING status necessarily passed that
    check — same "re-verify at every layer" convention this package
    already applies to is_tool_allowed() at the tool-call layer.
    """
    spec = ResearchSpec.from_dict(experiment.structured_spec)
    validation = validate_spec(spec)

    if not validation.is_valid:
        experiment.status = "BLOCKED"
        experiment.error_message = "; ".join(validation.errors)
        experiment.research_plan = {"spec_errors": validation.errors}
        experiment.save()
        return

    capability = capability_for_hypothesis_type(spec.hypothesis_type)
    if capability is None:
        # claude code changed: fail closed — a hypothesis_type with no
        # registered capability (should be unreachable for any value in
        # spec.py's HYPOTHESIS_TYPES, but this is the exact "unsupported
        # capabilities fail closed" invariant section 18 requires tested)
        experiment.status = "BLOCKED"
        experiment.error_message = f"hypothesis_type '{spec.hypothesis_type}' has no registered research capability"
        experiment.research_plan = {"capability_error": experiment.error_message}
        experiment.save()
        return

    entitlement = ResearchEntitlementService.can_access(experiment.student, capability.id)
    experiment.capability_id = capability.id
    if not entitlement.allowed:
        experiment.status = "BLOCKED"
        experiment.error_message = entitlement.message
        experiment.research_plan = {
            "capability_id": capability.id,
            "entitlement": entitlement.to_dict(),
        }
        experiment.save()
        return

    availability = check_data_availability(spec)
    decision = evaluate_policy(spec, availability, active_experiment_count=active_experiment_count(experiment.student))

    experiment.research_plan = {
        "spec": spec.to_dict(),
        "capability_id": capability.id,
        # claude code changed: new — section 14 "store subscription tier at
        # execution time" — recorded once, at plan time, alongside the
        # rest of this experiment's planning-time context (not re-read
        # live at report time, which could show a DIFFERENT tier than the
        # one that actually gated this specific run if the user's
        # subscription changed afterward).
        "entitlement": {**entitlement.to_dict(), "tier_at_planning": ResearchEntitlementService.get_user_tier(experiment.student)},
        "data_availability": availability.to_dict(),
        "policy_decision": decision.to_dict(),
    }
    experiment.status = "PLANNED" if decision.allowed else "BLOCKED"
    if not decision.allowed:
        experiment.error_message = "; ".join(decision.reasons)
    experiment.save()


def run_experiment(experiment: ResearchExperiment) -> None:
    """
    Executes the plan built by plan_experiment(). Only proceeds from
    PLANNED — never re-derives the plan itself, so what actually runs is
    exactly what the student saw and approved.
    """
    if experiment.status != "PLANNED":
        return

    spec = ResearchSpec.from_dict(experiment.research_plan.get("spec", {}))
    allowed_tools = set(experiment.research_plan.get("policy_decision", {}).get("allowed_tools", []))

    experiment.status = "RUNNING"
    experiment.started_at = timezone.now()
    experiment.random_seed = DEFAULT_RANDOM_SEED
    experiment.code_version = get_code_version()
    experiment.save()

    try:
        _execute_planned_tools(experiment, spec, allowed_tools)
    except Exception as exc:  # claude code changed: distinct from individual tool errors (captured as warnings, experiment still completes) — this is the genuine "orchestration itself broke" path section 12 requires
        experiment.status = "FAILED"
        experiment.error_message = f"{type(exc).__name__}: {exc}"
        experiment.completed_at = timezone.now()
        experiment.save()


def _execute_planned_tools(experiment: ResearchExperiment, spec: ResearchSpec, allowed_tools: set) -> None:
    """
    claude code changed: Conditional Hypothesis Integrity fix (Bug 1/7).
    This is now a dispatcher, not a single execution path. Before this
    fix, EVERY hypothesis — conditional or not — ran through the
    continuous-feature path below, because spec.hypothesis_type didn't
    exist. A conditional hypothesis must never fall through to
    _execute_feature_hypothesis(); the two functions use entirely
    different tools and entirely different verdict functions, on purpose.
    """
    if spec.hypothesis_type == "conditional":
        _execute_conditional_hypothesis(experiment, spec, allowed_tools)
    elif spec.hypothesis_type == "pairs":
        # claude code changed: new — Advanced Quant Research Capability
        # Architecture. A pairs hypothesis must never fall through to the
        # feature path (no forward-return target exists for it — see
        # spec.py's validate_spec() — so it would crash or silently test
        # the wrong thing) nor the conditional path.
        _execute_pairs_hypothesis(experiment, spec, allowed_tools)
    else:
        _execute_feature_hypothesis(experiment, spec, allowed_tools)


def _json_safe(value):
    """
    claude code changed: new — real bug found by out-of-sample-audit
    testing: cointegration_engine.py's PairResult.half_life is a genuine
    float('inf') for any non-cointegrated pair (by design — "never
    reverts" is meaningful, not an error). Python's json.dumps happily
    emits the bare token "Infinity" for that, but Postgres's jsonb column
    type follows the strict JSON spec and rejects it outright
    ("Token 'Infinity' is invalid"), crashing experiment.save() the moment
    ANY tool result reaching this function contains one — which every
    genuinely-rejected pairs hypothesis does. Recursively replaces
    inf/-inf/nan with None (JSON null) before anything is assigned to one
    of ResearchExperiment's JSONField columns below; every other value
    (including finite floats, which are the overwhelming majority) passes
    through completely unchanged.
    """
    if isinstance(value, float):
        return None if (math.isinf(value) or math.isnan(value)) else value
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _finalize_experiment(experiment: ResearchExperiment, tool_log: list, warnings: list, evidence: dict, validation: dict, verdict_result) -> None:
    experiment.tool_call_log = _json_safe(tool_log)
    experiment.warnings = _json_safe(warnings)
    experiment.statistical_results = _json_safe(evidence) or {}
    experiment.validation_results = _json_safe(validation) or {}
    experiment.verdict = verdict_result.verdict
    experiment.robustness_results = {"verdict_explanation": verdict_result.explanation, "criteria_version": verdict_result.criteria_version}
    experiment.ai_interpretation = explain_evidence(experiment)  # claude code changed: reads experiment.research_plan['spec'] (the EXECUTED spec) via describe_executed_spec() — never the stale original hypothesis_text, see interpreter.py
    experiment.status = "COMPLETED"
    experiment.completed_at = timezone.now()
    experiment.save()


def _execute_feature_hypothesis(experiment: ResearchExperiment, spec: ResearchSpec, allowed_tools: set) -> None:
    """Continuous FEATURE hypothesis path — unchanged behavior from before
    the Conditional Hypothesis Integrity fix, still only ever reachable
    when spec.hypothesis_type == "feature"."""
    tool_log = []
    warnings = []
    statistical_result = None
    fdr_result = None

    feature_name = spec.features[0] if spec.features else None

    if "inspect_dataset" in allowed_tools:
        result = run_tool("inspect_dataset", spec.risk_tier, asset=spec.asset)
        tool_log.append(result.to_dict())

    if feature_name is None:
        warnings.append("no feature named in the confirmed specification — statistical test skipped")
    else:
        if "calculate_feature" in allowed_tools:
            result = run_tool("calculate_feature", spec.risk_tier, asset=spec.asset, feature_name=feature_name)
            tool_log.append(result.to_dict())
            if result.status == "error":
                warnings.append(f"calculate_feature failed: {result.error}")

        if "run_statistical_test" in allowed_tools:
            result = run_tool(
                "run_statistical_test", spec.risk_tier, asset=spec.asset, feature_name=feature_name,
                horizon=spec.target.get("horizon"), random_seed=experiment.random_seed,
            )
            tool_log.append(result.to_dict())
            if result.status == "success":
                statistical_result = result.output
            else:
                warnings.append(f"run_statistical_test failed: {result.error}")

        if "run_fdr_correction" in allowed_tools:
            result = run_tool(
                "run_fdr_correction", spec.risk_tier, asset=spec.asset, feature_name=feature_name,
                horizon=spec.target.get("horizon"), random_seed=experiment.random_seed,
            )
            tool_log.append(result.to_dict())
            if result.status == "success":
                fdr_result = result.output
            else:
                warnings.append(f"run_fdr_correction failed: {result.error}")

    verdict_result = compute_verdict(None, None) if statistical_result is None else compute_verdict(statistical_result, fdr_result, hypothesized_direction=spec.direction)
    _finalize_experiment(experiment, tool_log, warnings, statistical_result, fdr_result, verdict_result)


def _execute_conditional_hypothesis(experiment: ResearchExperiment, spec: ResearchSpec, allowed_tools: set) -> None:
    """
    claude code changed: new — CONDITIONAL/event hypothesis path. Calls
    run_conditional_test(), never run_statistical_test() — the two are not
    interchangeable (see spec.py's module docstring). Routes evidence
    through compute_verdict_conditional(), never compute_verdict() — an IC
    threshold has no meaning for this evidence shape.
    """
    tool_log = []
    warnings = []
    conditional_result = None
    insufficient_events = False  # claude code changed: new — Statistical Integrity Hardening (gap #3), see verdict.compute_verdict_conditional()'s docstring

    if "inspect_dataset" in allowed_tools:
        result = run_tool("inspect_dataset", spec.risk_tier, asset=spec.asset)
        tool_log.append(result.to_dict())

    condition = spec.conditions[0] if spec.conditions else None
    condition_fully_specified = bool(condition and condition.get("operator") is not None and condition.get("threshold") is not None)

    if not condition_fully_specified:
        # claude code changed: this is the fail-closed branch Bug 1 requires
        # — a conditional hypothesis with no resolvable condition must NOT
        # silently fall back to a continuous feature test. It produces no
        # evidence at all, and the verdict engine reports REQUIRES_REVIEW
        # rather than a confident SUPPORTED/REJECTED on an untested question.
        warnings.append("hypothesis_type is 'conditional' but no fully-specified condition was confirmed — no conditional test was executed")
    elif "run_conditional_test" not in allowed_tools:
        warnings.append("run_conditional_test is not an approved tool for this experiment's risk tier")
    else:
        result = run_tool(
            "run_conditional_test", spec.risk_tier, asset=spec.asset,
            feature_name=condition["feature"], operator=condition["operator"], threshold=condition["threshold"],
            horizon=spec.target.get("horizon"), random_seed=experiment.random_seed,
        )
        tool_log.append(result.to_dict())
        if result.status == "success":
            conditional_result = result.output
            conditional_result["expected_direction"] = spec.direction  # claude code changed: new — gap #4/#12, persist what the hypothesis predicted alongside what run_conditional_test() observed, so results/report can show both without re-deriving from hypothesis_text
        elif result.error_type == "InsufficientEventsError":
            # claude code changed: new — Statistical Integrity Hardening
            # (gap #3). Distinct from the generic "tool failed" branch below
            # — this is specifically "the condition WAS computable, there
            # just aren't enough qualifying/non-qualifying candles," which
            # must reach VERDICT=INSUFFICIENT_DATA, not REQUIRES_REVIEW.
            insufficient_events = True
            warnings.append(f"run_conditional_test: {result.error.splitlines()[0]}")
        else:
            warnings.append(f"run_conditional_test failed: {result.error}")

    if not conditional_result and not insufficient_events:
        # claude code changed: was "no FDR correction applies to
        # conditional/event tests in this pass — known limitation" — no
        # longer accurate, run_conditional_test() now computes an exact
        # m=1 FDR-adjusted p-value on success (see conditional_tools.py).
        # This branch only means no test ran at all, so there's nothing to
        # correct.
        warnings.append("no conditional test evidence was produced for this experiment — nothing to statistically correct")

    verdict_result = compute_verdict_conditional(conditional_result, hypothesized_direction=spec.direction, insufficient_events=insufficient_events)
    _finalize_experiment(experiment, tool_log, warnings, conditional_result, {}, verdict_result)


def _execute_pairs_hypothesis(experiment: ResearchExperiment, spec: ResearchSpec, allowed_tools: set) -> None:
    """
    claude code changed: new — Advanced Quant Research Capability
    Architecture. PAIRS/cointegration hypothesis path. Calls
    run_cointegration_test(), never run_statistical_test()/
    run_conditional_test() — cointegration_engine.py's evidence shape
    (ADF p-value, half-life) has no meaning as an IC or mean-difference
    result. Routes through compute_verdict_pairs(), never the other two
    verdict functions, for the same reason.
    """
    tool_log = []
    warnings = []
    cointegration_result = None

    if "inspect_dataset" in allowed_tools:
        result = run_tool("inspect_dataset", spec.risk_tier, asset=spec.asset)
        tool_log.append(result.to_dict())

    if not spec.asset_b:
        # claude code changed: should be unreachable — validate_spec()
        # already requires asset_b for hypothesis_type=="pairs" — but this
        # branch is the fail-closed backstop matching the conditional
        # path's "no fully-specified condition" branch, not an assumption
        # that validation always ran first.
        warnings.append("hypothesis_type is 'pairs' but no asset_b was confirmed — no cointegration test was executed")
    elif "run_cointegration_test" not in allowed_tools:
        warnings.append("run_cointegration_test is not an approved tool for this experiment's risk tier")
    else:
        result = run_tool(
            "run_cointegration_test", spec.risk_tier, asset_a=spec.asset, asset_b=spec.asset_b,
        )
        tool_log.append(result.to_dict())
        if result.status == "success":
            cointegration_result = result.output
        else:
            warnings.append(f"run_cointegration_test failed: {result.error}")

    if not cointegration_result:
        warnings.append("no cointegration test evidence was produced for this experiment")

    verdict_result = compute_verdict_pairs(cointegration_result)
    _finalize_experiment(experiment, tool_log, warnings, cointegration_result, {}, verdict_result)
