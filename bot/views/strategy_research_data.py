# ============================================================
# bot/views/strategy_research_data.py
# claude code changed: new file — Strategy Research data layer. Reuses
# bot/backtesting/portfolio_backtester.py::PortfolioBacktester exactly as
# bot/views/dashboard.py::portfolio_backtest_view already does (same
# ccxt.binance construction, same real backtest()+StrategyScorer per
# symbol) — this is the same real, already-wired computation, just
# rendered as a full leaderboard page instead of a raw JSON endpoint.
# Public OHLCV data does not require API keys; BINANCE_API_KEY/SECRET are
# used if configured but ccxt's public endpoints work without them.
# ============================================================

import os

import ccxt

from bot.backtesting.portfolio_backtester import PortfolioBacktester


def get_strategy_leaderboard(symbols=None):
    try:
        exchange = ccxt.binance({
            "apiKey": os.getenv("BINANCE_API_KEY"),
            "secret": os.getenv("BINANCE_API_SECRET"),
        })
        exchange.load_markets()

        portfolio = PortfolioBacktester(
            exchange=exchange,
            symbols=symbols,
            candle_limit=2000,
            initial_balance=5000,
        )
        results = portfolio.run()
        results["available"] = True
        return results
    except Exception as e:
        return {"available": False, "reason": str(e)[:200]}
