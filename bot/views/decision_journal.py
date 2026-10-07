# ============================================================
# bot/views/decision_journal.py
# claude code changed: new file — 2026-10-07, Decision Journal UI (read-only, GET only). Data: decision_journal_data.py.
# ============================================================

from django.shortcuts import render
from django.views.decorators.http import require_GET

from bot.views.decision_journal_data import get_decision, get_run, get_runs
from django.http import Http404   # claude code changed: 2026-10-07 Phase 4 — analytics page


@require_GET
def decision_journal(request):
    return render(request, "decision_journal.html", get_runs())


@require_GET
def decision_journal_run(request, run_id):
    try:
        page = int(request.GET.get("page", 1))
    except ValueError:
        page = 1
    return render(request, "decision_journal_run.html",
                  get_run(run_id, status=request.GET.get("status", ""), page=page))


@require_GET
def decision_journal_analytics(request, run_id):   # claude code changed: 2026-10-07 Phase 4 — analytics page
    from bot.journal.analytics import GROUPINGS, LABELS, ZoneReadRefused, outcome_table
    from bot.views.decision_journal_data import _run_or_404
    run = _run_or_404(run_id)
    by, label = request.GET.get("by", "status"), request.GET.get("label", "rule")
    if by not in GROUPINGS or label not in LABELS:
        raise Http404("unknown grouping or label")
    try:
        table, refused = outcome_table(run, by=by, label=label), ""
    except ZoneReadRefused as e:
        table, refused = None, str(e)
    return render(request, "decision_journal_analytics.html",
                  {"run": run, "table": table, "refused": refused, "by": by, "label": label,
                   "groupings": GROUPINGS, "labels": list(LABELS)})


@require_GET
def decision_journal_decision(request, run_id, decision_id):
    return render(request, "decision_journal_decision.html", get_decision(run_id, decision_id))
