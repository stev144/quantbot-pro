# ============================================================
# bot/views/feature_intelligence.py
# claude code changed: new file — views for the Feature Intelligence
# module (research/features/...). Thin: all real-vs-unavailable logic
# lives in bot/views/feature_intelligence_data.py, same split as
# bot/views/research_lab.py / research_lab_data.py.
# ============================================================

import json

from django.shortcuts import render
from django.http import Http404

from bot.views import feature_intelligence_data as fid


def feature_leaderboard(request):
    context = {
        "leaderboard": fid.get_feature_leaderboard(),
        "status_board": fid.get_research_status_board(),
    }
    return render(request, "feature_leaderboard.html", context)


def feature_detail(request, feature_name):
    correlation_symbol = request.GET.get("symbol", "BTC_USDT").upper()
    detail = fid.get_feature_detail(feature_name, correlation_symbol=correlation_symbol)
    if not detail.get("available"):
        raise Http404(detail.get("reason", "Feature not found"))

    context = {
        "feature_name": feature_name,
        "detail": detail,
        "correlation_symbol": correlation_symbol,
        "symbols": fid.discover_observation_symbols(),
    }
    return render(request, "feature_detail.html", context)


def feature_comparison(request):
    selected = request.GET.getlist("f") or [f for f in request.GET.get("features", "").split(",") if f]
    comparison = fid.get_feature_comparison(selected)
    series_json = json.dumps([
        {"feature": r["feature"], "data": r.get("mean_yearly_ic", {})}
        for r in comparison["rows"]
    ])

    context = {
        "selected": selected,
        "comparison": comparison,
        "series_json": series_json,
    }
    return render(request, "feature_comparison.html", context)


def feature_correlation(request):
    symbol = request.GET.get("symbol", "BTC_USDT").upper()
    matrix = fid.get_feature_correlation_matrix(symbol)
    if matrix.get("available"):
        matrix = dict(matrix)
        matrix["rows"] = [
            {"label": label, "values": row}
            for label, row in zip(matrix["features"], matrix["matrix"])
        ]

    context = {
        "symbol": symbol,
        "matrix": matrix,
        "symbols": fid.discover_observation_symbols(),
    }
    return render(request, "feature_correlation.html", context)
