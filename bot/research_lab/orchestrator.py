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
from bot.research_lab.verdict import compute_verdict

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

    experiment.tool_call_log = tool_log
    experiment.warnings = warnings
    experiment.statistical_results = statistical_result or {}
    experiment.validation_results = fdr_result or {}

    if statistical_result is None:
        verdict_result = compute_verdict(None, None)
    else:
        verdict_result = compute_verdict(statistical_result, fdr_result, hypothesized_direction=spec.direction)

    experiment.verdict = verdict_result.verdict
    experiment.robustness_results = {"verdict_explanation": verdict_result.explanation, "criteria_version": verdict_result.criteria_version}
    experiment.ai_interpretation = explain_evidence(experiment)
    experiment.status = "COMPLETED"
    experiment.completed_at = timezone.now()
    experiment.save()
