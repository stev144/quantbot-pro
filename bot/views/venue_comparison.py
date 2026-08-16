# ============================================================
# bot/views/venue_comparison.py
# claude code changed: new file — Venue Comparison view. Thin wrapper
# around bot/views/venue_comparison_data.py::get_venue_comparison_table(),
# matching the established execution_monitor.py/system_health.py pattern.
# ============================================================

from django.shortcuts import render

from bot.views import venue_comparison_data


def venue_comparison(request):
    symbol = request.GET.get("symbol", venue_comparison_data.DEFAULT_SYMBOL).strip().upper() or venue_comparison_data.DEFAULT_SYMBOL

    try:
        quantity = float(request.GET.get("quantity", venue_comparison_data.DEFAULT_QUANTITY))
        if quantity <= 0:
            quantity = venue_comparison_data.DEFAULT_QUANTITY
    except (TypeError, ValueError):
        quantity = venue_comparison_data.DEFAULT_QUANTITY

    context = {
        "comparison": venue_comparison_data.get_venue_comparison_table(symbol=symbol, quantity=quantity),
    }
    return render(request, "venue_comparison.html", context)
