# ============================================================
# bot/views/pairs_pilot.py
# claude code changed: new file — live Pairs Pilot monitor page. Thin
# view (real data query lives in pairs_pilot_data.py), same convention
# as bot/views/execution_monitor.py.
# ============================================================

import json

from django.shortcuts import render

from bot.views.pairs_pilot_data import get_pairs_pilot_state


def pairs_pilot(request):
    state = get_pairs_pilot_state()
    pairs = state["pairs"]
    for p in pairs:
        # pre-serialize for the template's {{ ...|safe }} + inline <script> pattern,
        # matching templates/dashboard.html's window.equityData/window.drawdownData convention
        p["equity_curve_json"] = json.dumps(p["equity_curve"])
        p["drawdown_curve_json"] = json.dumps(p["drawdown_curve"])
    context = {
        "pairs": pairs,
        "health_snapshot_available": state["health_snapshot_available"],  # claude code changed: new — see bot/pairs/health.py
        "health_snapshot_reason": state["health_snapshot_reason"],
    }
    return render(request, "pairs_pilot.html", context)
