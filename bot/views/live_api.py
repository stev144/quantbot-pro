from django.http import JsonResponse
from bot.engines.analytics_engine import TradeAnalytics

# IMPORTANT: this should use your existing data source
# (replace this with your actual data pipeline)

def live_data(request):
    try:
        analytics = TradeAnalytics()

        # ⚠️ Replace with your real trade source
        trades = []  # <-- plug your trades source here

        for t in trades:
            analytics.record_trade(t)

        summary = analytics.summary()

        return JsonResponse({
            "success": True,
            "summary": summary,
            "timestamp": "live"
        })

    except Exception as e:
        return JsonResponse({
            "success": False,
            "error": str(e)
        })