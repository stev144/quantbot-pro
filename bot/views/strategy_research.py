# ============================================================
# bot/views/strategy_research.py
# claude code changed: new file — Strategy Research view.
# ============================================================

from django.shortcuts import render

from bot.views import strategy_research_data as srd


def strategy_research(request):
    pairs_param = request.GET.get("pairs", "")
    symbols = None
    if pairs_param:
        raw = [p.strip().upper() for p in pairs_param.split(",") if p.strip()]

        def to_slash(symbol):
            for quote in ["USDT", "BTC", "ETH", "BNB"]:
                if symbol.endswith(quote) and symbol != quote:
                    return symbol[:-len(quote)] + "/" + quote
            return symbol
        symbols = [to_slash(s) for s in raw]

    context = {
        "leaderboard": srd.get_strategy_leaderboard(symbols),
        "pairs_param": pairs_param,
    }
    return render(request, "strategy_research.html", context)
