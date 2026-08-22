# claude code changed: new file — section 16, the persistent research
# report. Rendered entirely from ResearchExperiment's own stored fields —
# no second copy of the evidence is ever created. Reproducible: shows
# code_version + random_seed alongside every result, per section 16.

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from bot.research_lab.models import ResearchExperiment
from bot.research_lab.verdict import VERDICT_CRITERIA_VERSION


@login_required
def report(request, experiment_id):
    experiment = get_object_or_404(ResearchExperiment, id=experiment_id, student=request.user)
    return render(request, "research_lab/report.html", {
        "experiment": experiment,
        "verdict_criteria_version": VERDICT_CRITERIA_VERSION,
    })
