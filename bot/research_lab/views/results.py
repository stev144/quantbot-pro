# claude code changed: new file — section 13 (evidence) + section 14
# (verdict) combined into one results screen for this MVP pass. Only ever
# displays metrics that are actually sitting in the experiment's own
# stored fields — never a fabricated "N/A" that could be mistaken for a
# computed value (section 13's own instruction).

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from bot.research_lab.models import ResearchExperiment


@login_required
def results(request, experiment_id):
    experiment = get_object_or_404(ResearchExperiment, id=experiment_id, student=request.user)
    return render(request, "research_lab/results.html", {"experiment": experiment})
