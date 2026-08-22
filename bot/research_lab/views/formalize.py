# claude code changed: new file — section 6, the hypothesis-formalization
# screen. Shows the placeholder interpreter's best-effort suggestions,
# requires the student to explicitly confirm/correct every field it
# marked ambiguous before the spec can proceed — this confirmation step
# IS how "ambiguous, not invented" is enforced end to end (see
# bot/research_lab/interpreter.py's own docstring).

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from bot.research_lab.data_availability import DERIVABLE_FROM_OHLCV
from bot.research_lab.interpreter import suggest_spec, INTERPRETER_NAME
from bot.research_lab.models import ResearchExperiment
from bot.research_lab.spec import ResearchSpec, SUPPORTED_ASSETS, SUPPORTED_TIMEFRAMES, SUPPORTED_DIRECTIONS, validate_spec


@login_required
def formalize(request, experiment_id):
    experiment = get_object_or_404(ResearchExperiment, id=experiment_id, student=request.user)

    if request.method == "POST":
        horizon_raw = request.POST.get("horizon", "").strip()
        spec = ResearchSpec(
            hypothesis_text=experiment.hypothesis_text,
            asset=request.POST.get("asset") or None,
            timeframe=request.POST.get("timeframe") or None,
            direction=request.POST.get("direction") or None,
            features=[f for f in [request.POST.get("feature_name")] if f],
            target={"type": "forward_return", "horizon": int(horizon_raw)} if horizon_raw.isdigit() else {"type": "forward_return"},
            risk_tier=request.POST.get("risk_tier", "LOW"),
        )
        validation = validate_spec(spec)
        if not validation.is_valid:
            return render(request, "research_lab/formalize.html", {
                "experiment": experiment, "spec": spec, "errors": validation.errors,
                "supported_assets": SUPPORTED_ASSETS, "supported_timeframes": SUPPORTED_TIMEFRAMES,
                "supported_directions": SUPPORTED_DIRECTIONS, "derivable_features": sorted(DERIVABLE_FROM_OHLCV),
                "interpreter_name": INTERPRETER_NAME,
            })

        experiment.structured_spec = spec.to_dict()
        experiment.save()
        return redirect("research_lab_plan", experiment_id=experiment.id)

    suggested = suggest_spec(experiment.hypothesis_text)
    return render(request, "research_lab/formalize.html", {
        "experiment": experiment, "spec": suggested,
        "supported_assets": SUPPORTED_ASSETS, "supported_timeframes": SUPPORTED_TIMEFRAMES,
        "supported_directions": SUPPORTED_DIRECTIONS, "derivable_features": sorted(DERIVABLE_FROM_OHLCV),
        "interpreter_name": INTERPRETER_NAME,
    })
