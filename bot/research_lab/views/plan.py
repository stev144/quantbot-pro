# claude code changed: new file — section 11, the transparent Research
# Plan screen. GET builds/shows the plan (never executes a tool); POST is
# the one explicit approval action that actually runs it.

from django.conf import settings   # claude code changed: 2026-10-26 — RESEARCH_LAB_BACKGROUND_RUNS
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from bot.research_lab.models import ResearchExperiment
from bot.research_lab.orchestrator import plan_experiment, run_experiment, start_experiment_in_background   # claude code changed: 2026-10-26


@login_required
def plan(request, experiment_id):
    experiment = get_object_or_404(ResearchExperiment, id=experiment_id, student=request.user)

    if experiment.status == "PENDING":
        plan_experiment(experiment)
        experiment.refresh_from_db()

    if request.method == "POST" and experiment.status == "PLANNED":
        if settings.RESEARCH_LAB_BACKGROUND_RUNS:   # claude code changed: 2026-10-26 — long runs outlive a web request
            start_experiment_in_background(experiment)
        else:
            run_experiment(experiment)
            experiment.refresh_from_db()
        return redirect("research_lab_results", experiment_id=experiment.id)

    return render(request, "research_lab/plan.html", {"experiment": experiment})
