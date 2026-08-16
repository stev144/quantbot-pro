# ============================================================
# bot/views/market_intelligence.py
# claude code changed: new file — Market Intelligence view. Thin, per
# the established split (bot/views/research_lab.py / research_lab_data.py
# etc.) — real-vs-unavailable logic lives in market_intelligence_data.py.
# ============================================================

from django.shortcuts import render

from bot.views import market_intelligence_data as mid


def market_intelligence(request):
    context = mid.get_market_intelligence()
    return render(request, "market_intelligence.html", context)
