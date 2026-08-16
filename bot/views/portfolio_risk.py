# ============================================================
# bot/views/portfolio_risk.py
# claude code changed: new file — Portfolio & Risk view.
# ============================================================

from django.shortcuts import render

from bot.views import portfolio_risk_data as prd


def portfolio_risk(request):
    symbol = request.GET.get("symbol", "AVAXUSDT").upper()
    context = prd.get_portfolio_and_risk(symbol)
    return render(request, "portfolio_risk.html", context)
