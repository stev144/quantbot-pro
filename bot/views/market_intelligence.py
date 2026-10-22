# ============================================================
# bot/views/market_intelligence.py
# claude code changed: new file — Market Intelligence view. Thin, per
# the established split (bot/views/research_lab.py / research_lab_data.py
# etc.) — real-vs-unavailable logic lives in market_intelligence_data.py.
# ============================================================

from django.shortcuts import render

from bot.views import market_intelligence_data as mid
from bot.views import markets_data as md   # claude code changed: 2026-10-22 — Markets cards on the Regime page


def _attach_market_cards(context, user):
    """claude code changed: 2026-10-22 — give every regime row and every z-score extreme the SAME quote the Markets
    tab shows (price, 24 h change, sparkline, name, watchlist state), so the page renders Markets cards instead of a
    plain list. Display only: the regime and z-score values themselves are unchanged."""
    quotes = md.card_quotes("crypto", user)

    ro = context.get("regime_overview") or {}
    ro["cards"] = [{"q": quotes[r["symbol"]], "r": r} for r in ro.get("rows", []) if r["symbol"] in quotes]
    ro["n_without_quote"] = len(ro.get("rows", [])) - len(ro["cards"])
    cs = context.get("cross_section") or {}
    cs["cards"] = [{"q": quotes[e["symbol"]], "z": e["cs_zscore"]} for e in cs.get("top_extremes", []) if e["symbol"] in quotes]
    return context


def market_intelligence(request):
    context = mid.get_market_intelligence()
    context = _attach_market_cards(context, request.user)   # claude code changed: 2026-10-22
    return render(request, "market_intelligence.html", context)
