# ============================================================
# bot/views/robustness_analysis.py
# claude code changed: new file — Robustness Analysis view. Pure
# extraction: reuses research_lab_data.py's get_strategy_comparison()
# (walk-forward leaderboard) and get_robustness_testing() (permutation
# test verdicts) unchanged — these used to be Research Lab panels 7 and
# 9, now their own dedicated page. No new data-layer logic.
# ============================================================

from django.shortcuts import render

from bot.views.research_lab_data import get_strategy_comparison, get_robustness_testing


def robustness_analysis(request):
    context = {
        "strategy_comparison": get_strategy_comparison(),
        "robustness": get_robustness_testing(),
    }
    return render(request, "robustness_analysis.html", context)
