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

import pandas as pd   # claude code changed: new — Phase 1D, only used by run_kalman_pairs_test's pd.notna() check

from bot.backtesting.backtester import backtest
from bot.engines.strategy_scorer import StrategyScorer
from bot.research.cointegration_engine import CointegrationEngine
from bot.research.kalman_filter_engine import KalmanFilterEngine   # claude code changed: new — Phase 1D, Objective 8 (Kalman Research Integration)
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


@register_tool("run_kalman_pairs_test")
def run_kalman_pairs_test(
    asset_a:             str,
    asset_b:             str,
    process_noise_beta:  float = None,
    process_noise_alpha: float = None,
    observation_noise:   float = None,
) -> dict:
    """
    claude code changed: new — Phase 1D, Objective 8 (Kalman Research
    Integration). NOT registered in bot/research_lab/policy_gate.py's
    ALLOWED_TOOLS — see that module's comment on kalman_filter_engine.py
    for why (risk_tier="HIGH" is a deliberate statistical-risk judgment,
    not an implementation gap this tool resolves). This function exists
    and is directly unit-tested so the capability registry's
    "PARTIALLY_IMPLEMENTED... not the typed, arbitrary-pair tool-call shape
    every other capability uses" gap has a real, verified answer on record
    — reachability through the live Research Lab is a separate, deliberately
    un-made decision.

    Follows run_cointegration_test's exact pattern: loads both legs via
    load_ohlcv() (never a raw filename guess, never touches execution/
    order-placement code), rejects a timeframe mismatch the same way, then
    computes a FRESH cointegration test via CointegrationEngine._test_pair()
    for the OLS hedge-ratio seed (never a second OLS implementation — same
    single-pair path run_cointegration_test itself uses), and runs
    KalmanFilterEngine.run_on_prices() on the already-loaded, already-
    validated price Series (never KalmanFilterEngine.run()'s file-reading
    path, never a dependency on a pre-existing cointegration_pairs.csv row
    for this exact pair).

    process_noise_beta/process_noise_alpha/observation_noise default to
    None here and are resolved to kalman_filter_engine.py's own module
    defaults (PROCESS_NOISE_BETA/PROCESS_NOISE_ALPHA/OBSERVATION_NOISE)
    inside KalmanFilterEngine's constructor when omitted — this tool never
    silently substitutes a DIFFERENT default of its own. See Phase 1D's
    engineering report (Objective 2) for why these are surfaced as
    explicit, inspectable parameters rather than estimated automatically:
    automatic noise estimation was evaluated and deliberately NOT
    implemented this phase, pending its own look-ahead-bias analysis.

    Returns a dict split into OBSERVATION / STATISTICAL_EVIDENCE / VERDICT
    (Phase 1D, Objective 12) — never a single flat blob conflating what was
    measured with what it means.
    """
    df_a = load_ohlcv(asset_a)
    df_b = load_ohlcv(asset_b)
    price_a = df_a["close"]
    price_b = df_b["close"]

    # claude code changed: same timeframe-mismatch guard run_cointegration_test
    # uses — both legs must share a candle resolution for either the OLS
    # seed or the Kalman filter's per-candle recursion to mean anything.
    instrument_a = df_a.attrs.get("instrument")
    instrument_b = df_b.attrs.get("instrument")
    timeframe_a = instrument_a.timeframe if instrument_a else None
    timeframe_b = instrument_b.timeframe if instrument_b else None
    if timeframe_a != timeframe_b:
        return {
            "asset_a": asset_a, "asset_b": asset_b,
            "observation": None,
            "statistical_evidence": None,
            "verdict": "INCONCLUSIVE",
            "reject_reason": f"timeframe mismatch: {asset_a} is {timeframe_a}, {asset_b} is {timeframe_b} — Kalman filtering requires both legs on the same candle resolution",
        }

    # Fresh OLS seed — the SAME single-pair path run_cointegration_test uses,
    # never a duplicate cointegration implementation.
    coint_engine = CointegrationEngine(timeframe=timeframe_a or "1h")
    coint_result = coint_engine._test_pair(asset_a, asset_b, price_a, price_b)

    kalman_kwargs = {}   # claude code changed: only pass noise overrides the caller actually supplied — let KalmanFilterEngine's own constructor defaults apply otherwise
    if process_noise_beta is not None:
        kalman_kwargs["process_noise_beta"] = process_noise_beta
    if process_noise_alpha is not None:
        kalman_kwargs["process_noise_alpha"] = process_noise_alpha
    if observation_noise is not None:
        kalman_kwargs["observation_noise"] = observation_noise

    kalman_engine = KalmanFilterEngine(
        symbol_a=asset_a, symbol_b=asset_b,
        ols_beta=coint_result.hedge_ratio, ols_alpha=coint_result.intercept,
        half_life_h=coint_result.half_life,
        **kalman_kwargs,
    )
    kalman_df = kalman_engine.run_on_prices(price_a, price_b)

    post_warmup = kalman_df.loc[~kalman_df["is_warmup"]]
    if post_warmup.empty:
        return {
            "asset_a": asset_a, "asset_b": asset_b,
            "observation": None,
            "statistical_evidence": {
                "seed_adf_pvalue": coint_result.adf_pvalue,
                "seed_coint_pvalue": coint_result.coint_pvalue,
                "seed_half_life_hours": coint_result.half_life,
                "seed_is_cointegrated": coint_result.is_cointegrated,
            },
            "verdict": "INCONCLUSIVE",
            "reject_reason": f"insufficient candles after warmup ({len(kalman_df)} total, warmup={kalman_engine.warmup}) — cannot report a trusted dynamic estimate",
        }

    latest = post_warmup.iloc[-1]   # claude code changed: leakage-free — kalman_beta_pred/kalman_alpha_pred, the pre-update state, is what a spread computed AT this candle must use (see _calculate_dynamic_spread)
    return {
        "asset_a": asset_a, "asset_b": asset_b,
        # claude code changed: OBSERVATION — measured, not evaluated for significance
        "observation": {
            "dynamic_hedge_ratio": float(latest["kalman_beta_pred"]),
            "dynamic_intercept": float(latest["kalman_alpha_pred"]),
            "dynamic_spread": float(latest["kalman_spread"]),
            "dynamic_zscore": float(latest["kalman_zscore"]) if pd.notna(latest.get("kalman_zscore")) else None,
            "n_candles": len(kalman_df),
            "n_post_warmup_candles": len(post_warmup),
            "warmup_candles": kalman_engine.warmup,
        },
        # claude code changed: STATISTICAL EVIDENCE — from the fresh cointegration
        # seed test; Kalman itself doesn't run a second significance test —
        # its evidence IS the seed's, refined by a dynamic (not static) hedge ratio.
        "statistical_evidence": {
            "seed_adf_pvalue": coint_result.adf_pvalue,
            "seed_coint_pvalue": coint_result.coint_pvalue,
            "seed_half_life_hours": coint_result.half_life,
            "seed_is_cointegrated": coint_result.is_cointegrated,
            "seed_ols_hedge_ratio": coint_result.hedge_ratio,
            "beta_drift_from_ols_seed": float(latest["kalman_beta_pred"]) - coint_result.hedge_ratio,
        },
        # claude code changed: VERDICT — a deterministic function of the statistical
        # evidence above, never an AI judgment call (Phase 1D, Objective 12).
        "verdict": "SUPPORTED" if (coint_result.is_cointegrated and coint_result.passes_filters) else (
            "REJECTED" if not coint_result.is_cointegrated else "INCONCLUSIVE"
        ),
        "config": {
            "process_noise_beta": kalman_engine.qb,
            "process_noise_alpha": kalman_engine.qa,
            "observation_noise": kalman_engine.R,
            "warmup_candles": kalman_engine.warmup,
        },
        "warnings": (
            ["seed pair did not pass cointegration_engine.py's own half-life filter — dynamic estimate is not backed by a validated pair relationship"]
            if coint_result.is_cointegrated and not coint_result.passes_filters else []
        ),
    }


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
