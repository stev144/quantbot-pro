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
    df_a = load_ohlcv(asset_a)
    df_b = load_ohlcv(asset_b)
    price_a = df_a["close"]
    price_b = df_b["close"]

    # claude code changed: new — Multi-Asset Foundation Refactor Phase 1B,
    # Objective 2. Half-life is only reported in honest wall-clock time if
    # the engine knows the real candle resolution of the data it's being
    # given — read from the instrument metadata load_ohlcv() attaches
    # (Phase 1A), never assumed. Both legs of a pair MUST share the same
    # timeframe for cointegration testing to mean anything (candle-for-
    # candle alignment) — a mismatch is a real, honest INSUFFICIENT_DATA-
    # shaped failure, not something to silently pick one side's timeframe
    # for.
    instrument_a = df_a.attrs.get("instrument")
    instrument_b = df_b.attrs.get("instrument")
    timeframe_a = instrument_a.timeframe if instrument_a else None
    timeframe_b = instrument_b.timeframe if instrument_b else None
    if timeframe_a != timeframe_b:
        return {
            "asset_a": asset_a, "asset_b": asset_b,
            "is_cointegrated": False, "passes_filters": False,
            "reject_reason": f"timeframe mismatch: {asset_a} is {timeframe_a}, {asset_b} is {timeframe_b} — cointegration requires both legs on the same candle resolution",
            "fdr_note": "not evaluated — rejected before the cointegration test could run",
        }

    engine = CointegrationEngine(timeframe=timeframe_a or "1h")
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
