# ============================================================
# bot/views/system_health.py
# claude code changed: new file — System Health view. Pure wrapper
# around bot/views/terminal_data.py::get_system_health() — no new
# data-layer logic. deep_health_check.py's full L1-L5 audit stays
# CLI-only (genuinely slow, self-describes as "meant for a CLI run, not
# a page load") — not wired to a web trigger in this pass.
# claude code changed: 2026-10-07 Deep Health — the page now shows the five-gate Deep Health dashboard
# (bot/deep_health/dashboard.py): L0 (the get_system_health() checks above, unchanged) and L1 run live,
# offline and read-only; L2 is shown from its last saved CLI run, and only when it matches this code state.
# ============================================================

from django.shortcuts import render

from bot.deep_health.dashboard import build_dashboard   # claude code changed: 2026-10-07 Deep Health


def system_health(request):
    context = {"dh": build_dashboard()}   # claude code changed: 2026-10-07 Deep Health (was terminal_data.get_system_health() only — now its L0 rows)
    return render(request, "system_health.html", context)
