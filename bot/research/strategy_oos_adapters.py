# ============================================================
# bot/research/strategy_oos_adapters.py
# claude code changed: new file — Type B Strategy->Trade-Outcome OOS
# Evaluator mission, Section 14 (existing-strategy integration).
#
# bot/research/oos_validator.py's evaluate_strategy_oos() is deliberately
# strategy-agnostic — it knows nothing about Backtester, RegimeDetector,
# or any specific strategy (see that module's Type B section docstring
# for the full architectural rationale: Backtester itself hardcodes its
# own regime-routed strategy pipeline with no injection point, so making
# the EVALUATOR generic means writing it against a plain trade-dict
# contract, not against Backtester directly).
#
# THIS file is the other half: a thin, project-specific adapter that
# implements that contract by wrapping the real, existing Backtester —
# reused exactly as-is, not forked or reimplemented. A future non-
# Backtester strategy (a cointegration model, an ML predictor, a live
# IBKR strategy) would get its own adapter file; nothing about
# oos_validator.py needs to change for that.
# ============================================================

from __future__ import annotations

from typing import Dict, List

import pandas as pd


def run_backtester_strategy(
    df_slice: pd.DataFrame,
    fitted_params: Dict,
    *,
    initial_balance: float = 10_000.0,
    venue_id: str = "binance",
    allow_unvalidated_strategies: bool = True,
) -> List[Dict]:
    """
    Implements evaluate_strategy_oos()'s `run_strategy_fn` contract by
    running this project's real, existing regime -> router -> strategy
    pipeline (bot.backtesting.backtester.Backtester) over `df_slice`
    (warmup context + one fold's TEST candles, exactly as
    evaluate_strategy_oos() constructs it) and converting Backtester's
    own trade dicts into the OOS evaluator's minimal trade-dict contract
    (entry_time/exit_time/profit/gross_profit/r_multiple).

    `fitted_params` is accepted (required by the contract) but unused —
    this project's existing MovingAverageStrategy/MeanReversionStrategy
    have fixed, code-defined parameters (RSI thresholds, EMA periods,
    ATR multipliers), not parameters fit from data anywhere in this
    codebase today. Passing fit_fn=None to evaluate_strategy_oos() when
    using this adapter is the honest choice — inventing a fake
    calibration step here would misrepresent what these strategies
    actually do (Section 5 of the Type B mission: "do not force existing
    functionality into the generic evaluator merely to demonstrate
    compatibility").

    allow_unvalidated_strategies=True (this adapter's own default, NOT
    Backtester's — Backtester itself defaults this to False): StrategyRouter
    gates every strategy behind validated_feature_registry.py's real
    research verdicts, and RSI/EMA-based signals are not currently
    production-approved (see bot/engines/strategy_router.py). Without
    this override, Backtester would legitimately produce ZERO trades for
    every fold, which would prove nothing about the OOS evaluator's own
    mechanics. This is explicitly a research/integration-proof setting —
    documented here, and surfaced again in every result this adapter
    produces should never be read as "this strategy is approved for live
    capital." See bot/backtesting/backtester.py's own log line: "Set
    allow_unvalidated_strategies=True to override (test-only)."

    Backtester itself has a hard `len(df) >= 100` floor (returns empty
    results otherwise) — evaluate_strategy_oos()'s warmup_periods default
    (bot.backtesting.backtester.INDICATOR_LOOKBACK_CANDLES = 300) already
    comfortably clears this for any fold that wasn't already skipped for
    being too short.
    """
    from bot.backtesting.backtester import Backtester

    result = Backtester(
        df=df_slice, initial_balance=initial_balance,
        allow_unvalidated_strategies=allow_unvalidated_strategies, venue_id=venue_id,
    ).run()

    idx = df_slice.index
    n = len(idx)
    out: List[Dict] = []
    for t in result.get("trades", []):
        entry_pos = t.get("entry_index")
        exit_pos = t.get("exit_index")
        if entry_pos is None or exit_pos is None or not (0 <= entry_pos < n) or not (0 <= exit_pos < n):
            continue   # claude code changed: defensive — skip any trade Backtester couldn't position-index (should not happen; never silently mis-times a trade instead)
        out.append({
            "entry_time": idx[entry_pos], "exit_time": idx[exit_pos],
            "profit": t.get("profit", 0.0), "gross_profit": t.get("gross_profit", t.get("profit", 0.0)),
            "r_multiple": t.get("r_multiple", 0.0),
            "direction": t.get("direction"), "exit_reason": t.get("exit_reason"),
        })
    return out
