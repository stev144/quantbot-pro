# claude code changed: new file — section 6, the hypothesis-formalization
# screen. Shows the placeholder interpreter's best-effort suggestions,
# requires the student to explicitly confirm/correct every field it
# marked ambiguous before the spec can proceed — this confirmation step
# IS how "ambiguous, not invented" is enforced end to end (see
# bot/research_lab/interpreter.py's own docstring).
#
# claude code changed: Conditional Hypothesis Integrity fix. Now collects
# hypothesis_type + a structured condition (feature/operator/threshold)
# for conditional hypotheses, instead of only ever offering a plain
# feature dropdown. Horizon is now a <select> restricted to
# SUPPORTED_HORIZONS's real keys (Bug 4) — the restriction is visible
# BEFORE submission, not discovered only after a tool call fails.

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from bot.research_lab.interpreter import suggest_spec, INTERPRETER_NAME
from bot.research_lab.models import ResearchExperiment
from bot.research_lab.spec import (
    ResearchSpec, SUPPORTED_ASSETS, SUPPORTED_TIMEFRAMES, SUPPORTED_DIRECTIONS,
    SUPPORTED_HORIZONS, DERIVABLE_FROM_OHLCV, HYPOTHESIS_TYPES, CONDITION_OPERATORS,
    validate_spec,
)


def _context(experiment, spec, errors=None):
    return {
        "experiment": experiment, "spec": spec, "errors": errors or [],
        "supported_assets": SUPPORTED_ASSETS, "supported_timeframes": SUPPORTED_TIMEFRAMES,
        "supported_directions": SUPPORTED_DIRECTIONS, "derivable_features": sorted(DERIVABLE_FROM_OHLCV),
        "supported_horizons": sorted(SUPPORTED_HORIZONS),  # claude code changed: new — real, closed horizon set, shown before submit
        "hypothesis_types": HYPOTHESIS_TYPES, "condition_operators": CONDITION_OPERATORS,  # claude code changed: new
        "interpreter_name": INTERPRETER_NAME,
    }


def _spec_from_post(post_data, hypothesis_text):
    horizon_raw = post_data.get("horizon", "").strip()
    hypothesis_type = post_data.get("hypothesis_type", "feature")

    conditions = []
    features = []
    if hypothesis_type == "conditional":
        cond_feature = post_data.get("condition_feature") or None
        cond_operator = post_data.get("condition_operator") or None
        cond_threshold_raw = post_data.get("condition_threshold", "").strip()
        cond_threshold = float(cond_threshold_raw) if cond_threshold_raw else None
        if cond_feature:
            conditions = [{"feature": cond_feature, "operator": cond_operator, "threshold": cond_threshold}]
    else:
        features = [f for f in [post_data.get("feature_name")] if f]

    return ResearchSpec(
        hypothesis_text=hypothesis_text,
        asset=post_data.get("asset") or None,
        timeframe=post_data.get("timeframe") or None,
        hypothesis_type=hypothesis_type,
        features=features,
        conditions=conditions,
        direction=post_data.get("direction") or None,
        target={"type": "forward_return", "horizon": int(horizon_raw)} if horizon_raw.isdigit() else {"type": "forward_return"},
        risk_tier=post_data.get("risk_tier", "LOW"),
    )


@login_required
def formalize(request, experiment_id):
    experiment = get_object_or_404(ResearchExperiment, id=experiment_id, student=request.user)

    if request.method == "POST":
        spec = _spec_from_post(request.POST, experiment.hypothesis_text)
        validation = validate_spec(spec)
        if not validation.is_valid:
            return render(request, "research_lab/formalize.html", _context(experiment, spec, validation.errors))

        experiment.structured_spec = spec.to_dict()
        experiment.save()
        return redirect("research_lab_plan", experiment_id=experiment.id)

    suggested = suggest_spec(experiment.hypothesis_text)
    return render(request, "research_lab/formalize.html", _context(experiment, suggested))
