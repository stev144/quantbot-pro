# ============================================================
# bot/views/venue_comparison.py
# claude code changed: new file — Venue Comparison view. Thin wrapper
# around bot/views/venue_comparison_data.py::get_venue_comparison_table(),
# matching the established execution_monitor.py/system_health.py pattern.
# ============================================================

from django.shortcuts import render

from bot.views import venue_comparison_data
from bot.views.recorded_books import recorded_books_payload   # claude code changed: 2026-10-25 — recorded books panel
from bot.views.cross_exchange_gaps import gap_context   # claude code changed: 2026-10-25 — Phase B research block


def venue_comparison(request):
    symbol = request.GET.get("symbol", venue_comparison_data.DEFAULT_SYMBOL).strip().upper() or venue_comparison_data.DEFAULT_SYMBOL

    try:
        quantity = float(request.GET.get("quantity", venue_comparison_data.DEFAULT_QUANTITY))
        if quantity <= 0:
            quantity = venue_comparison_data.DEFAULT_QUANTITY
    except (TypeError, ValueError):
        quantity = venue_comparison_data.DEFAULT_QUANTITY

    try:   # claude code changed: 2026-10-25 — the recorder's latest books; a read problem must not break the page
        recorded = recorded_books_payload()
    except Exception as exc:
        recorded = {"available": False, "reason": f"Recorded books could not be read: {exc}"}
    try:   # claude code changed: 2026-10-25 — Phase B gap research shown after the books; a read problem must not break the page
        xg = gap_context()
    except Exception as exc:
        xg = {"report": None, "command": f"(gap report could not be read: {exc})"}
    context = {
        "comparison": venue_comparison_data.get_venue_comparison_table(symbol=symbol, quantity=quantity),
        "recorded_books": recorded,   # claude code changed: 2026-10-25
        "xg": xg,                     # claude code changed: 2026-10-25
    }
    return render(request, "venue_comparison.html", context)
