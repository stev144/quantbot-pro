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
from bot.instruments import symbols_for_asset_class, ASSET_CLASS_CRYPTO  # claude code changed: new — see DEFAULT_SYMBOLS comment below
from bot.views.dashboard import run_walk_forward_validation

# claude code changed: was a frozen 5-symbol hardcoded list — forensic
# audit found this silently substituted a tiny stale set for the real
# ~100-coin dynamic universe on EVERY load of the Backtesting dashboard
# page (bot/views/backtesting_view.py's `backtesting()` calls
# get_backtest_summaries() with zero args, no query-param escape hatch
# exists for this one at all). Kept the SAME count (5) rather than the
# full dynamic universe — this is a synchronous Django view running a
# real backtest per symbol on every page load, and 100x the per-symbol
# work would risk a real request timeout, a genuine usability regression
# that "more universe-correct" would not have been worth (same
# reasoning already applied to portfolio_backtester.py's default). Takes
# the top 5 most liquid symbols from the SAME dynamic, liquidity-ranked
# universe (symbols_for_asset_class() returns most-liquid-first) instead
# of a frozen, potentially-stale hand-picked set — same performance
# profile as before, but tracks whichever 5 coins are actually most
# liquid today. Converted to the same no-slash "BTCUSDT" form the
# original list used (symbols_for_asset_class() returns "BTC/USDT") so
# this page's URLs/display (templates/backtesting.html links to
# ?symbol={{ r.symbol }}) keep their exact existing format — scoped
# purely to "use the dynamic universe," not a display-format change too.
# A function (not a plain constant) so it's evaluated fresh on each call
# rather than frozen at import time.
def _default_symbols():
    return [s.replace("/", "") for s in symbols_for_asset_class(ASSET_CLASS_CRYPTO)[:5]]


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
    symbols = symbols or _default_symbols()
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
