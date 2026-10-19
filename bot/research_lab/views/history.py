# claude code changed: new file — section 17, experiment history. Read-
# only list; experiments are immutable research records, there is no edit
# path here by design.

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from bot.research_lab.models import ResearchExperiment
from bot.research.evidence_registry import classify_research_experiment   # claude code changed: Post-CRRP Task 4


@login_required
def history(request):
    experiments = list(ResearchExperiment.objects.filter(student=request.user))
    for e in experiments:   # claude code changed: Post-CRRP Task 4 — evidence status per experiment (pre-reset rows are never current evidence)
        e.evidence = classify_research_experiment(e.created_at, e.capability_id or "", e.verdict or "")
    return render(request, "research_lab/history.html", {"experiments": experiments})
