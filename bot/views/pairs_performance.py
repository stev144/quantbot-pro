# ============================================================
# bot/views/pairs_performance.py
# claude code changed: new file — Pairs Performance page. Visualizes
# bot/research/entry_exit_engine.py's Kalman/cointegration pairs-trading
# output (equity curve, drawdown curve, trade log, permutation/walk-forward
# verdicts) for a chosen pair — the pairs-research equivalent of what
# templates/dashboard.html does for single-symbol technical strategies.
# Mirrors bot/views/research_archive.py's thinness: real data in, template
# out, no derived logic here.
# ============================================================

from django.shortcuts import render

from bot.views.pairs_performance_data import discover_pairs, get_pair_performance


def pairs_performance(request):
    pairs = discover_pairs()
    selected = request.GET.get("pair") or (pairs[0] if pairs else None)

    performance = get_pair_performance(selected) if selected else {
        "available": False,
        "reason": "No pairs research output found in research_data/.",
    }

    context = {
        "pairs": pairs,
        "pair": selected,
        "performance": performance,
    }
    return render(request, "pairs_performance.html", context)
