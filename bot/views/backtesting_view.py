# ============================================================
# bot/views/backtesting_view.py
# claude code changed: new file — Backtesting section view. Named
# backtesting_view.py (not backtesting.py) to avoid shadowing the
# bot/backtesting/ package on Python's import path.
# ============================================================

from django.shortcuts import render

from bot.views import backtesting_data as bd


def backtesting(request):
    symbol = request.GET.get("symbol", "BTCUSDT").upper()
    split = float(request.GET.get("split", 0.7))
    split = max(0.5, min(0.85, split))

    context = {
        "symbol": symbol,
        "split": split,
        "walk_forward": bd.get_walk_forward(symbol, split),
        "summaries": bd.get_backtest_summaries(),
    }
    return render(request, "backtesting.html", context)
