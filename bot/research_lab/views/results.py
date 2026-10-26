# claude code changed: new file — section 13 (evidence) + section 14
# (verdict) combined into one results screen for this MVP pass. Only ever
# displays metrics that are actually sitting in the experiment's own
# stored fields — never a fabricated "N/A" that could be mistaken for a
# computed value (section 13's own instruction).

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from bot.research_lab.models import ResearchExperiment
from bot.research_lab.orchestrator import fail_if_interrupted   # claude code changed: 2026-10-26 — background runs
from bot.research.evidence_registry import classify_research_experiment   # claude code changed: Post-CRRP Task 4


@login_required
def results(request, experiment_id):
    experiment = get_object_or_404(ResearchExperiment, id=experiment_id, student=request.user)
    fail_if_interrupted(experiment)   # claude code changed: 2026-10-26 — a run whose thread died with a restart
    evidence = classify_research_experiment(experiment.created_at, experiment.capability_id or "", experiment.verdict or "")   # claude code changed: Post-CRRP Task 4
    return render(request, "research_lab/results.html", {"experiment": experiment, "evidence": evidence})
