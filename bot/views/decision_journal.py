# ============================================================
# bot/views/decision_journal.py
# claude code changed: new file — 2026-10-07, Decision Journal UI (read-only, GET only). Data: decision_journal_data.py.
# ============================================================

from django.shortcuts import render
from django.views.decorators.http import require_GET

from bot.views.decision_journal_data import get_decision, get_run, get_runs


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
def decision_journal_decision(request, run_id, decision_id):
    return render(request, "decision_journal_decision.html", get_decision(run_id, decision_id))
