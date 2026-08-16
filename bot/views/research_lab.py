# ============================================================
# bot/views/research_lab.py
# claude code changed: new file — view for the Research Lab interface.
# Reads the same backtest-results cache the main dashboard populates
# (bot/views/dashboard.py) rather than triggering its own fetch+backtest,
# so Research Lab stays fast; if nothing is cached yet for the requested
# symbol, Regime Analysis / Backtest Results sections say so explicitly
# instead of silently showing nothing or forcing a slow reload here.
# ============================================================

from django.shortcuts import render
from django.core.cache import cache

from bot.views import research_lab_data as rl


def research_lab(request):
    symbol = request.GET.get("symbol", "AVAXUSDT").upper()
    cache_key = f"backtest_results_{symbol}"
    backtest_results = cache.get(cache_key)

    context = {
        "symbol": symbol,
        "has_cached_backtest": backtest_results is not None,

        "dataset": rl.get_dataset_explorer(),
        "features": rl.get_feature_explorer(),
        "stability": rl.get_feature_stability(),
        "predictive_power": rl.get_predictive_power(),
        "cross_asset": rl.get_cross_asset_analysis(),
        "regime": rl.get_regime_analysis(backtest_results),
        "strategy_comparison": rl.get_strategy_comparison(),
        "backtest": rl.get_backtest_results(symbol, backtest_results),
        "robustness": rl.get_robustness_testing(),
        "conclusions": rl.get_research_conclusions(),
    }

    return render(request, "research_lab.html", context)
