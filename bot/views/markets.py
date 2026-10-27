# bot/views/markets.py
# claude code changed: new — Markets discovery screen view.
# Thin view per the project convention; data logic lives in markets_data.py.

from django.shortcuts import render

from bot.views import markets_data as md
from bot.journal.models import WatchlistItem


def markets(request):  # claude code changed: new
    data = md.get_markets_list()

    # Build set of symbols this user has watchlisted (empty for anon users)
    watchlisted = set()
    if request.user.is_authenticated:
        watchlisted = set(
            WatchlistItem.objects.filter(user=request.user)
            .values_list("symbol", flat=True)
        )

    # claude code changed: 2026-10-27 — the home page's risk & cost metrics on every card (background snapshot + live
    # spread; None while the snapshot is still computing, and the page never waits for it)
    from bot.views.home_intelligence import get_universe_snapshot, quote_metrics
    try:
        live, snapshot = md.get_live_quotes(), get_universe_snapshot()
    except Exception:   # a metrics problem must not take the Markets page down
        live, snapshot = {}, {}

    # Tag each quote with whether it's in the user's watchlist
    for asset_class in ("crypto", "forex"):
        for q in data.get(asset_class, []):
            if q.get("available"):
                q["in_watchlist"] = q["symbol"] in watchlisted
                q["metrics"] = quote_metrics(q["symbol"], asset_class, snapshot, live)   # claude code changed: 2026-10-27

    from bot.views.paper_trading import cached_paper_summary   # claude code changed: 2026-10-25 — Paper Trader card (cache only)

    return render(request, "markets.html", {
        **data,
        "active": "markets",
        "paper": cached_paper_summary(),   # claude code changed: 2026-10-25 — None until the Paper Trader has run
    })


def markets_live(request):  # claude code changed: 2026-10-21 — new; JSON live prices polled by the Markets / Watchlist pages
    from django.http import JsonResponse
    resp = JsonResponse(md.get_live_quotes())
    resp["Cache-Control"] = "no-store"
    return resp
