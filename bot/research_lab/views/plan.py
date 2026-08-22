# claude code changed: new file — section 11, the transparent Research
# Plan screen. GET builds/shows the plan (never executes a tool); POST is
# the one explicit approval action that actually runs it.

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from bot.research_lab.models import ResearchExperiment
from bot.research_lab.orchestrator import plan_experiment, run_experiment


@login_required
def plan(request, experiment_id):
    experiment = get_object_or_404(ResearchExperiment, id=experiment_id, student=request.user)

    if experiment.status == "PENDING":
        plan_experiment(experiment)
        experiment.refresh_from_db()

    if request.method == "POST" and experiment.status == "PLANNED":
        run_experiment(experiment)
        experiment.refresh_from_db()
        return redirect("research_lab_results", experiment_id=experiment.id)

    return render(request, "research_lab/plan.html", {"experiment": experiment})
