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

    # Tag each quote with whether it's in the user's watchlist
    for asset_class in ("crypto", "forex"):
        for q in data.get(asset_class, []):
            if q.get("available"):
                q["in_watchlist"] = q["symbol"] in watchlisted

    return render(request, "markets.html", {
        **data,
        "active": "markets",
    })
