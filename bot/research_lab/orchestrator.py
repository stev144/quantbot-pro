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

import subprocess

from django.utils import timezone

from bot.research_lab.data_availability import check_data_availability
from bot.research_lab.interpreter import explain_evidence
from bot.research_lab.models import ResearchExperiment
from bot.research_lab.policy_gate import evaluate_policy
from bot.research_lab.spec import ResearchSpec, validate_spec
from bot.research_lab.tools import run_tool
from bot.research_lab.verdict import compute_verdict, compute_verdict_conditional  # claude code changed: +compute_verdict_conditional, Conditional Hypothesis Integrity fix

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
    Validates the spec, checks data availability, and runs the Policy
    Gate — the section 11 "Research Plan" the student reviews before
    anything executes. Never calls a tool. Sets status to PLANNED (allowed)
    or BLOCKED (not allowed) — never RUNNING.
    """
    spec = ResearchSpec.from_dict(experiment.structured_spec)
    validation = validate_spec(spec)

    if not validation.is_valid:
        experiment.status = "BLOCKED"
        experiment.error_message = "; ".join(validation.errors)
        experiment.research_plan = {"spec_errors": validation.errors}
        experiment.save()
        return

    availability = check_data_availability(spec)
    decision = evaluate_policy(spec, availability, active_experiment_count=active_experiment_count(experiment.student))

    experiment.research_plan = {
        "spec": spec.to_dict(),
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
    else:
        _execute_feature_hypothesis(experiment, spec, allowed_tools)


def _finalize_experiment(experiment: ResearchExperiment, tool_log: list, warnings: list, evidence: dict, validation: dict, verdict_result) -> None:
    experiment.tool_call_log = tool_log
    experiment.warnings = warnings
    experiment.statistical_results = evidence or {}
    experiment.validation_results = validation or {}
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
        else:
            warnings.append(f"run_conditional_test failed: {result.error}")

    if not conditional_result:
        warnings.append("no FDR correction applies to conditional/event tests in this pass — known limitation, see the engineering report")

    verdict_result = compute_verdict_conditional(conditional_result, hypothesized_direction=spec.direction)
    _finalize_experiment(experiment, tool_log, warnings, conditional_result, {}, verdict_result)
