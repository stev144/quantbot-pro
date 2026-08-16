# ============================================================
# bot/views/system_health.py
# claude code changed: new file — System Health view. Pure wrapper
# around bot/views/terminal_data.py::get_system_health() — no new
# data-layer logic. deep_health_check.py's full L1-L5 audit stays
# CLI-only (genuinely slow, self-describes as "meant for a CLI run, not
# a page load") — not wired to a web trigger in this pass.
# ============================================================

from django.shortcuts import render

from bot.views import terminal_data


def system_health(request):
    context = {"health": terminal_data.get_system_health()}
    return render(request, "system_health.html", context)
