# ============================================================
# bot/views/portfolio_risk_data.py
# claude code changed: new file — Portfolio & Risk data layer. Reuses
# bot/views/terminal_data.py::get_portfolio_risk() (real max-drawdown +
# live price correlation) and the position-summary half of
# get_execution_state() (real TradeRecord query) unchanged — no new
# computation, just assembled for a dedicated page instead of being
# folded into the Executive Dashboard.
# ============================================================

from django.core.cache import cache

from bot.data_fetcher import get_klines
from bot.backtesting.backtester import backtest
from bot.views import terminal_data


def _get_cached_backtest(symbol):
    """
    Reuses the exact same cache key bot/views/dashboard.py::dashboard()
    writes to (f"backtest_results_{symbol}") so this page and the
    Executive Dashboard share one computed backtest per symbol instead
    of paying for it twice.
    """
    cache_key = f"backtest_results_{symbol}"
    results = cache.get(cache_key)
    if results is None:
        df = get_klines(symbol, interval="1h", total_candles=10000)
        if df is None or df.empty:
            return {}
        results = backtest(df) or {}
        cache.set(cache_key, results, 3600)
    return results


def get_portfolio_and_risk(symbol):
    backtest_results = _get_cached_backtest(symbol)
    portfolio_risk = terminal_data.get_portfolio_risk(backtest_results)
    execution_state = terminal_data.get_execution_state()

    return {
        "symbol": symbol,
        "portfolio_risk": portfolio_risk,
        "open_positions": execution_state.get("open_positions", []) if execution_state.get("available") else [],
        "open_count": execution_state.get("open_count", 0) if execution_state.get("available") else 0,
        "execution_available": execution_state.get("available", False),
        "execution_reason": execution_state.get("reason"),
    }
