# ============================================================
# bot/research_lab/tools/research_tools.py
# Research Lab tools: run_cointegration_test, run_backtest,
# run_parameter_sensitivity (section 8).
#
# claude code changed: new file. Wraps cointegration_engine.py's
# CointegrationEngine, bot/backtesting/backtester.py's backtest(), and
# strategy_scorer.py's StrategyScorer — never a second implementation
# (section 25). Deliberately does NOT wrap portfolio_backtester.py (bypasses
# the hardened data layer, per the architecture audit) or
# kalman_filter_engine.py/walk_forward_engine.py/permutation_test_engine.py
# (their entry_exit_engine.py dependency has zero test coverage).
# ============================================================

from bot.backtesting.backtester import backtest
from bot.engines.strategy_scorer import StrategyScorer
from bot.research.cointegration_engine import CointegrationEngine
from bot.research_lab.tools._data import load_ohlcv
from bot.research_lab.tools.base import register_tool


@register_tool("run_cointegration_test")
def run_cointegration_test(asset_a: str, asset_b: str) -> dict:
    """
    Real, bounded, single-pair Engle-Granger test — calls the same private
    method the full universe scan calls per pair, just for the one pair
    this hypothesis names, never the full O(n^2) universe sweep.
    """
    price_a = load_ohlcv(asset_a)["close"]
    price_b = load_ohlcv(asset_b)["close"]

    engine = CointegrationEngine()
    result = engine._test_pair(asset_a, asset_b, price_a, price_b)
    output = result.to_dict()
    # claude code changed: FDR correction for this field is only ever
    # computed across a whole tested universe by run_all() — a single-pair
    # call has no cross-pair family to correct against, so these stay
    # honestly None rather than presenting an uncorrected value as if it
    # were FDR-adjusted.
    output["fdr_note"] = "adf_pvalue_fdr/passes_fdr are only computed by a full multi-pair cointegration_engine.py run, not a single-pair test"
    return output


@register_tool("run_backtest")
def run_backtest(asset: str, initial_balance: float = 5000.0) -> dict:
    """
    Wraps the real, regime-gated single-symbol Backtester — never
    portfolio_backtester.py, which bypasses the hardened data layer.
    """
    df = load_ohlcv(asset)
    result = backtest(df, initial_balance=initial_balance)
    return {
        "asset": asset,
        "n_trades": len(result.get("trades", [])),
        "win_rate": result.get("win_rate"),
        "expectancy_r": result.get("expectancy_r"),
        "profit_factor": result.get("profit_factor"),
        "sharpe_ratio": result.get("sharpe_ratio"),
        "max_drawdown": result.get("max_drawdown"),
        "final_balance": result.get("final_balance"),
    }


@register_tool("run_parameter_sensitivity")
def run_parameter_sensitivity(asset: str, initial_balance: float = 5000.0) -> dict:
    """
    Wraps StrategyScorer.evaluate()'s own robustness sub-analysis rather
    than reimplementing parameter-sensitivity testing — that analysis
    already exists, seeded (random.Random(42)), and reproducible.
    """
    df = load_ohlcv(asset)
    result = backtest(df, initial_balance=initial_balance)
    scored = StrategyScorer(result).evaluate()
    return {
        "asset": asset,
        "total_score": scored.get("total_score"),
        "grade": scored.get("grade"),
        "robustness": scored.get("robustness"),
    }
