# ============================================================
# bot/views/execution_monitor.py
# claude code changed: new file — Execution Monitor view. Pure wrapper
# around bot/views/terminal_data.py::get_execution_state() (real
# TradeRecord query) — no new data-layer logic.
# ============================================================

from django.shortcuts import render

from bot.views import terminal_data


def execution_monitor(request):
    context = {"execution": terminal_data.get_execution_state()}
    return render(request, "execution_monitor.html", context)
