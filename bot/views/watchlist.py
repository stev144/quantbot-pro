# bot/views/watchlist.py
# claude code changed: new — Watchlist page and AJAX add/remove endpoints.
# All mutations are POST-only and require authentication (no live orders,
# purely a UI preference store). Returns JSON for add/remove so the
# market card button can update in-place without a full page reload.

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from bot.journal.models import WatchlistItem
from bot.views import markets_data as md

logger = logging.getLogger(__name__)


@login_required
def watchlist(request):  # claude code changed: new — watchlist page view
    items = WatchlistItem.objects.filter(user=request.user).order_by("added_at")

    # Group by asset class for the data layer lookup
    by_class: dict[str, list] = {}
    for item in items:
        by_class.setdefault(item.asset_class, []).append(item.symbol)

    quotes = md.get_watchlist_quotes(by_class) if by_class else []

    return render(request, "watchlist.html", {
        "quotes": quotes,
        "n_items": len(items),
        "active": "watchlist",
    })


@login_required
@require_POST
def watchlist_add(request):  # claude code changed: new — AJAX add endpoint
    try:
        body = json.loads(request.body)
        symbol = str(body.get("symbol", "")).strip()[:32]
        asset_class = str(body.get("asset_class", "")).strip()[:16]
        if not symbol or asset_class not in ("crypto", "forex"):
            return JsonResponse({"ok": False, "error": "invalid symbol or asset_class"}, status=400)

        _, created = WatchlistItem.objects.get_or_create(
            user=request.user, symbol=symbol,
            defaults={"asset_class": asset_class},
        )
        return JsonResponse({"ok": True, "created": created, "symbol": symbol})
    except Exception as exc:
        logger.warning("watchlist_add failed: %s", exc)
        return JsonResponse({"ok": False, "error": "server error"}, status=500)


@login_required
@require_POST
def watchlist_remove(request):  # claude code changed: new — AJAX remove endpoint
    try:
        body = json.loads(request.body)
        symbol = str(body.get("symbol", "")).strip()[:32]
        if not symbol:
            return JsonResponse({"ok": False, "error": "missing symbol"}, status=400)

        deleted, _ = WatchlistItem.objects.filter(
            user=request.user, symbol=symbol
        ).delete()
        return JsonResponse({"ok": True, "removed": deleted > 0, "symbol": symbol})
    except Exception as exc:
        logger.warning("watchlist_remove failed: %s", exc)
        return JsonResponse({"ok": False, "error": "server error"}, status=500)
