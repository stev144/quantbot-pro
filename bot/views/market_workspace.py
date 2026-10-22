# bot/views/market_workspace.py
"""Market Intelligence workspace — page + JSON endpoints.   # claude code changed: 2026-10-21 — new

Thin views (project convention): all computation lives in bot/market_intel/. Each section has its own endpoint so a
slow or failing section (e.g. the Claude news call) never blocks the others. No endpoint places orders or changes
risk settings. The core workspace is free; only forcing a fresh (paid) AI analysis requires sign-in.
"""
import json
import math
import time

from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from bot.market_intel import evidence as ev
from bot.market_intel import exposure as ex
from bot.market_intel import history_lab as hl
from bot.market_intel import lessons as ls
from bot.market_intel import news_ai
from bot.market_intel import quant as q

CORE_TTL = 60   # seconds; per (asset, timeframe)


def _resolve(asset_class, symbol, timeframe="1h"):
    try:
        return q.validate(asset_class, symbol, timeframe)
    except LookupError:
        raise Http404("unknown market")


def _bad(exc, status=400):
    return JsonResponse({"error": str(exc)}, status=status)


def _client_key(request) -> str:
    return request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", "?")


def _throttle(request, name: str, per_minute: int) -> bool:
    key = f"mi_throttle:{name}:{_client_key(request)}:{int(time.time() // 60)}"
    cache.add(key, 0, timeout=70)
    try:
        return cache.incr(key) <= per_minute
    except ValueError:
        return True


def _jsonable(o):
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    return o


def market_workspace(request, asset_class, symbol):
    try:
        asset_class, symbol, _ = _resolve(asset_class, symbol)
    except q.InvalidRequest:
        raise Http404("unknown market")
    from bot.views.markets_data import get_markets_list
    listing = get_markets_list()
    switcher = {cls: [x["symbol"] for x in listing.get(cls, []) if x.get("available")] for cls in q.ASSET_CLASSES}
    return render(request, "market_workspace.html", {
        "active": "markets", "asset_class": asset_class, "symbol": symbol, "display": q.display_symbol(symbol),
        "timeframes": list(q.TIMEFRAMES), "switcher": switcher, "ai": news_ai.public_config(),
        "history_bounds": hl.bounds(asset_class, symbol),
    })


@require_GET
def api_core(request, asset_class, symbol):
    tf = request.GET.get("tf", "1h")
    try:
        asset_class, symbol, tf = _resolve(asset_class, symbol, tf)
    except q.InvalidRequest as exc:
        return _bad(exc)
    key = f"mi_core_v1:{asset_class}:{symbol}:{tf}"
    core = cache.get(key)
    if core is None:
        bars, meta = q.load_bars(asset_class, symbol, tf)
        if bars.empty:
            return JsonResponse({"error": "no market data available for this asset", "bars_meta": meta}, status=503)
        ov = q.overview(asset_class, symbol, tf, bars, meta)
        reg = q.regime(bars, tf)
        evd = q.evidence(bars, tf, meta)
        c = bars["close"]
        atr = evd.get("atr14") if evd.get("available") else None
        price = ov.get("price") or float(c.iloc[-1])
        suggestion = None
        if atr:
            suggestion = {"side": "LONG", "entry": price, "stop": price - 2 * atr, "target": price + 4 * atr,
                          "balance": 1000.0, "risk_pct": 1.0,
                          "note": "starting assumptions only (stop = 2 × ATR(14), target = 2R, $1,000, 1 % risk) — change them"}
        core = _jsonable({
            "overview": ov, "regime": reg, "evidence": evd,
            "chart": {"t": [i.isoformat() for i in c.index], "close": [float(v) for v in c],
                      "ema20": [float(v) for v in c.ewm(span=20, adjust=False).mean()],
                      "ema50": [float(v) for v in c.ewm(span=50, adjust=False).mean()]},
            "lessons": ls.lessons(asset_class, symbol, ov, reg, evd),
            "risk_defaults": suggestion,
            "adverse_context": q.adverse_move_context(bars, abs(2 * atr / price * 100)) if atr else None,
            "generated_at": time.time(),
        })
        cache.set(key, core, CORE_TTL)
    exposure = ex.open_exposure(request.user, asset_class, symbol)
    news_cached = cache.get(f"{news_ai.CACHE_PREFIX}:{asset_class}:{symbol}")
    alerts = ex.build_alerts(asset_class=asset_class, symbol=symbol, exposure=exposure, evidence=core.get("evidence"),
                             regime=core.get("regime"), news=news_cached)
    return JsonResponse({**core, "exposure": _jsonable(exposure), "alerts": _jsonable(alerts)})


@require_GET
def api_risk(request, asset_class, symbol):
    tf = request.GET.get("tf", "1h")
    try:
        asset_class, symbol, tf = _resolve(asset_class, symbol, tf)
        f = {k: float(request.GET.get(k, "nan")) for k in ("entry", "stop", "target", "balance", "risk_pct")}
        bars, meta = q.load_bars(asset_class, symbol, tf)
        evd = q.evidence(bars, tf, meta)
        res = q.risk_scenario(asset_class, symbol, side=request.GET.get("side", "LONG"), atr=evd.get("atr14"), **f)
        dist = abs(f["entry"] - f["stop"]) / f["entry"] * 100
        res["adverse_context"] = q.adverse_move_context(bars, dist)
        res["timeframe"] = tf
    except q.InvalidRequest as exc:
        return _bad(exc)
    except ValueError:
        return _bad("all scenario fields must be numbers")
    return JsonResponse(_jsonable(res))


@require_GET
def api_news(request, asset_class, symbol):
    asset_class, symbol, _ = _resolve(asset_class, symbol)
    if not _throttle(request, "news", 20):
        return JsonResponse({"status": "RATE_LIMITED", "message": "too many requests — wait a minute"}, status=429)
    force = request.GET.get("refresh") == "1"
    if force and not request.user.is_authenticated:
        return JsonResponse({"status": "FORBIDDEN", "message": "sign in to request a fresh AI analysis"}, status=403)
    core = cache.get(f"mi_core_v1:{asset_class}:{symbol}:1h") or {}
    reg, evd = core.get("regime") or {}, core.get("evidence") or {}
    metrics = {"regime_1h": reg.get("regime"), "indicator_agreement": reg.get("agreement"),
               **{m["key"]: m.get("value") for m in evd.get("metrics", []) if m["key"] in ("ret_window", "vol_now", "vol_rank")}}
    return JsonResponse(_jsonable(news_ai.get_news_intel(asset_class, symbol, metrics, force=force)))


@require_GET
def api_research(request, asset_class, symbol):
    asset_class, symbol, _ = _resolve(asset_class, symbol)
    return JsonResponse(_jsonable(ev.strategy_evidence(asset_class, symbol)))


@require_GET
def api_history_start(request, asset_class, symbol):
    asset_class, symbol, _ = _resolve(asset_class, symbol)
    try:
        return JsonResponse(_jsonable(hl.start(asset_class, symbol, request.GET.get("date", ""), int(request.GET.get("horizon", "24")))))
    except q.InvalidRequest as exc:
        return _bad(exc)
    except ValueError:
        return _bad("horizon must be an integer")


@require_POST
def api_history_reveal(request, asset_class, symbol):
    asset_class, symbol, _ = _resolve(asset_class, symbol)
    try:
        body = json.loads(request.body or "{}")
        return JsonResponse(_jsonable(hl.reveal(asset_class, symbol, str(body.get("token", "")), str(body.get("hypothesis", "")))))
    except q.InvalidRequest as exc:
        return _bad(exc)
    except (json.JSONDecodeError, KeyError):
        return _bad("malformed request")
