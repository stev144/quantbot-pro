# ============================================================
# bot/views/research_archive.py
# claude code changed: new file — Research Archive + Rejected Research
# views. Both call research_lab_data.py::get_research_conclusions() —
# one data source (research_data/model_governance_log.md), two filtered
# views, per the plan's decision to avoid parsing the log twice.
# ============================================================

from django.shortcuts import render

from bot.views.research_lab_data import get_research_conclusions


def research_archive(request):
    context = {"conclusions": get_research_conclusions()}
    return render(request, "research_archive.html", context)


def rejected_research(request):
    context = {"conclusions": get_research_conclusions(verdict_filter="REJECTED")}
    return render(request, "rejected_research.html", context)
