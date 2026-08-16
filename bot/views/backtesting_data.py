# ============================================================
# bot/views/backtesting_data.py
# claude code changed: new file — Backtesting section data layer.
# Reuses bot/views/dashboard.py::run_walk_forward_validation() (the real
# in/out-of-sample split, extracted from walk_forward_view so it isn't
# duplicated) plus bot/backtesting/backtester.py::backtest() +
# bot/engines/strategy_scorer.py::StrategyScorer for a per-symbol
# full-period summary table, reusing the exact classes dashboard() itself
# uses for its single-symbol backtest.
# ============================================================

from bot.data_fetcher import get_klines
from bot.backtesting.backtester import backtest
from bot.engines.strategy_scorer import StrategyScorer
from bot.views.dashboard import run_walk_forward_validation

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "AVAXUSDT", "SOLUSDT", "BNBUSDT"]


def get_walk_forward(symbol, split=0.7):
    """
    Real: run_walk_forward_validation()'s own verdict dict carries emoji
    and hardcoded hex colors (icon/color fields) baked in for its
    original JSON-widget consumer. The template only renders status/
    grade/message from it — icon/color are dropped in favor of the
    shared .badge/health_class CSS system so this page matches the
    platform's restrained, non-decorative color usage.
    """
    return run_walk_forward_validation(symbol, split)


def get_backtest_summaries(symbols=None):
    """
    Real: backtest() + StrategyScorer.evaluate() per symbol, same classes
    bot/views/dashboard.py::dashboard() uses for the single-symbol
    Executive Dashboard view.
    """
    symbols = symbols or DEFAULT_SYMBOLS
    rows = []
    errors = {}
    for symbol in symbols:
        try:
            df = get_klines(symbol, interval="1h", total_candles=3000)
            if df is None or df.empty:
                errors[symbol] = "no data returned"
                continue
            results = backtest(df) or {}
            score = StrategyScorer(results).evaluate()
            rows.append({
                "symbol": symbol,
                "total_trades": results.get("total_trades", 0),
                "win_rate": round(results.get("win_rate", 0), 2),
                "profit_factor": round(results.get("profit_factor", 0), 2),
                "expectancy_r": round(results.get("expectancy", 0), 2),
                "avg_r": round(results.get("avg_r_multiple", 0), 3),
                "max_drawdown": round(results.get("max_drawdown", 0), 2),
                "sharpe": round(results.get("sharpe_ratio", 0), 2),
                "sortino": round(results.get("sortino_ratio", 0), 2),
                "total_score": round(score.get("total_score", 0), 1),
                "grade": score.get("grade", "F"),
                "verdict": score.get("verdict", "Unknown"),
            })
        except Exception as e:
            errors[symbol] = str(e)[:120]

    return {"available": bool(rows), "rows": rows, "errors": errors, "symbols_tested": symbols}
