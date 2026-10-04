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

import numpy as np    # claude code changed: new — see run_cointegration_test's log-price fix below
import pandas as pd   # claude code changed: new — Phase 1D, only used by run_kalman_pairs_test's pd.notna() check

from bot.backtesting.backtester import backtest
from bot.engines.strategy_scorer import StrategyScorer
from bot.research.cointegration_engine import CointegrationEngine, MIN_PRICE
from bot.research.kalman_filter_engine import KalmanFilterEngine   # claude code changed: new — Phase 1D, Objective 8 (Kalman Research Integration)
from bot.instruments import ASSET_CLASS_CRYPTO   # claude code changed: new — Regime-Conditional Research wiring session, closing a real forex gap (see run_cross_sectional_ranking_test below)
from bot.research.run_cross_sectional_oos import AVAILABLE_FEATURES, run_cross_sectional_research   # claude code changed: new — statistics-infrastructure mission, Milestone B4
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
    # claude code changed: real bug fix — was passing raw close prices
    # straight into CointegrationEngine._test_pair(), while the full
    # run_all() universe sweep only ever calls _test_pair() with log
    # prices (see cointegration_engine.py's _validate_and_extract_prices(),
    # whose own docstring explains why: log prices make the hedge ratio a
    # stable relative ratio and give the spread a constant-variance,
    # unit-free scale the OU/half-life model assumes). Feeding raw levels
    # instead silently produced a different hedge ratio, a different ADF/
    # cointegration p-value, and — whenever a pair did test cointegrated —
    # a wrong half-life, despite this docstring's own claim of calling
    # "the same private method the full universe scan calls per pair".
    # Verified empirically: for BTC/USDT vs ETH/USDT, raw prices gave
    # hedge_ratio=12.66 / adf_p=0.151 vs log prices' hedge_ratio=0.877 /
    # adf_p=0.364 — a materially different statistical result, not a
    # rounding difference. Same clip(lower=MIN_PRICE)-then-log treatment
    # as _validate_and_extract_prices(), so this single-pair tool is now
    # actually the same test, not just the same code path.
    price_a = np.log(df_a["close"].clip(lower=MIN_PRICE))
    price_b = np.log(df_b["close"].clip(lower=MIN_PRICE))

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

    # claude code changed: 2026-10-04 (D-8 + D-3) — was engine._test_pair() on the WHOLE history (first-N training
    # window + persistence on the rest, i.e. it read zone V and later). Exploration may use zone S only, so the
    # same single-pair test now runs on zone-S candles only, with seed eligibility recorded. Never claimable.
    from bot.research.cointegration_engine import pair_test_in_selection_zone
    output = pair_test_in_selection_zone(asset_a, asset_b, df_a["close"], df_b["close"], timeframe=timeframe_a or "1h")
    if output["status"] == "UNTESTABLE":
        output["reject_reason"] = f"insufficient zone-S data — {output['reject_reason']}"   # → INSUFFICIENT_DATA, never REJECTED
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
    # claude code changed: real bug fix — same root cause as
    # run_cointegration_test's log-price fix above, but the mismatch here
    # was worse: KalmanFilterEngine.run_on_prices() below (correctly)
    # log-transforms price_a/price_b internally before running its
    # recursion (see _to_log_prices()), but this OLS seed call was handing
    # _test_pair() the raw, un-logged prices — so ols_beta/ols_alpha, the
    # values used to INITIALISE that log-space Kalman recursion, were
    # estimated in a completely different (raw-price) unit system. That's
    # not just an inaccurate seed, it's a unit mismatch feeding the
    # filter's initial state, and it also made "seed_ols_hedge_ratio"/
    # "beta_drift_from_ols_seed" in the returned evidence compare a
    # log-space dynamic hedge ratio against a raw-space static one.
    # price_a/price_b themselves stay raw — run_on_prices() below needs
    # raw input, it does its own log conversion — only this seed test gets
    # a separately log-transformed copy, matching _test_pair()'s real
    # contract.
    # claude code changed: 2026-10-04 (step 3, D-8 + Stage 2D C6) — was a whole-history OLS seed
    # (CointegrationEngine._test_pair on every candle, first-N training window) fed to run_on_prices(): the
    # legacy look-ahead seed (F-03). Now: (1) the pair evidence is the ZONE-S selection test; (2) the Kalman
    # state comes from run_canonical() — causal seed at the S start, θ₀ from the L = 1000 candles before it;
    # (3) the data stop at the END OF ZONE S, so this exploration tool never reads zone V or the sealed holdout.
    from bot.quant_core.protocol import CANONICAL
    from bot.quant_core.research_zones import ZONES
    from bot.research.cointegration_engine import pair_test_in_selection_zone
    sel = pair_test_in_selection_zone(asset_a, asset_b, price_a, price_b, timeframe=timeframe_a or "1h")
    evidence = {
        "selection_basis": sel["selection_basis"], "zone_s": sel["zone_s"], "zones_hash": sel["zones_hash"],
        "zone_s_coint_pvalue": sel.get("coint_pvalue"), "zone_s_half_life_candles": sel.get("half_life_candles"),
        "zone_s_is_cointegrated": sel.get("is_cointegrated"), "zone_s_passes_filters": sel.get("passes_filters"),
        "seed_eligible": sel["seed_eligible"], "seed_reason": sel["seed_reason"],
    }
    if sel["status"] == "BLOCKED":   # claude code changed: 2026-10-04 asset-agnostic — market profile incomplete (FX_CALENDAR_UNAVAILABLE): not tested
        return {
            "asset_a": asset_a, "asset_b": asset_b, "observation": None, "statistical_evidence": evidence,
            "verdict": "REQUIRES_REVIEW", "reject_reason": sel["reject_reason"],
        }
    if sel["status"] != "OK" or not sel["seed_eligible"]:
        return {
            "asset_a": asset_a, "asset_b": asset_b, "observation": None, "statistical_evidence": evidence,
            "verdict": "INSUFFICIENT_DATA",
            "reject_reason": sel.get("reject_reason") if sel["status"] != "OK" else sel["seed_reason"],
        }

    kalman_kwargs = {}   # claude code changed: only pass noise overrides the caller actually supplied — let KalmanFilterEngine's own constructor defaults apply otherwise
    if process_noise_beta is not None:
        kalman_kwargs["process_noise_beta"] = process_noise_beta
    if process_noise_alpha is not None:
        kalman_kwargs["process_noise_alpha"] = process_noise_alpha
    if observation_noise is not None:
        kalman_kwargs["observation_noise"] = observation_noise

    kalman_engine = KalmanFilterEngine(
        pair_name=f"{asset_a}/{asset_b}", symbol_a=asset_a, symbol_b=asset_b,
        ols_beta=1.0, ols_alpha=0.0,   # NOT used for θ₀ by run_canonical (causal_init only)
        **kalman_kwargs,
    )
    s_end = ZONES.S1
    kalman_df = kalman_engine.run_canonical(price_a[price_a.index <= s_end], price_b[price_b.index <= s_end])
    seed = kalman_engine.seed_record
    protocol = kalman_engine.canonical_protocol

    post_warmup = kalman_df.loc[~kalman_df["is_warmup"]]
    if post_warmup.empty:
        return {
            "asset_a": asset_a, "asset_b": asset_b, "observation": None, "statistical_evidence": evidence,
            "verdict": "INCONCLUSIVE",
            "reject_reason": f"no post-warmup canonical state inside zone S ({len(kalman_df)} candles after the seed)",
        }

    latest = post_warmup.iloc[-1]   # leakage-free: the pre-update state is what a spread at this candle uses
    zone_ok = bool(sel.get("is_cointegrated")) and bool(sel.get("passes_filters"))
    return {
        "asset_a": asset_a, "asset_b": asset_b,
        "observation": {
            "as_of": post_warmup.index[-1].isoformat(),          # end of zone S, never later
            "dynamic_hedge_ratio": float(latest["kalman_beta_pred"]),
            "dynamic_intercept": float(latest["kalman_alpha_pred"]),
            "beta_uncertainty": float(latest["beta_uncertainty"]),
            "seed_beta": float(seed.beta0), "seed_timestamp": seed.seed_timestamp,
            "beta_drift_from_canonical_seed": float(latest["kalman_beta_pred"]) - float(seed.beta0),
        },
        "statistical_evidence": evidence,
        "verdict": "OBSERVATION" if zone_ok else ("REJECTED" if sel.get("is_cointegrated") is False else "INCONCLUSIVE"),   # D-7 ladder cap
        "config": {
            "process_noise_beta": kalman_engine.qb,
            "process_noise_alpha": kalman_engine.qa,
            "observation_noise": kalman_engine.R,
            "warmup_candles": kalman_engine.warmup,
            "protocol_hash": protocol.config_hash(),
            "canonical_defaults": protocol.config_hash() == CANONICAL.config_hash(),
        },
        "warnings": (["noise overrides change the protocol hash — this run is NOT on canonical protocol values"]
                     if protocol.config_hash() != CANONICAL.config_hash() else [])
                    + (["zone-S pair did not pass the engine's half-life/persistence filters"]
                       if sel.get("is_cointegrated") and not sel.get("passes_filters") else []),
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


@register_tool("run_cross_sectional_ranking_test")
def run_cross_sectional_ranking_test(
    hypothesis_name: str,
    top_k_values: tuple = (1, 3, 5, 10),
    n_permutations: int = 20,   # claude code changed: new — lower than the 100 a standalone/offline run uses (bot/research/run_cross_sectional_oos.py's own default), so a typed tool call inside an interactive Research Lab session returns in a reasonable time; the smallest achievable p-value at N=20 is 1/21 (~0.048), still enough to clear the standard 0.05 significance bar for a clearly real effect — a researcher who wants a tighter estimate re-runs the standalone script directly with a higher N.
    long_short: bool = True,
    asset_class: str = ASSET_CLASS_CRYPTO,   # claude code changed: new — real gap found and closed. run_cross_sectional_research() has supported asset_class=ASSET_CLASS_FOREX since Forex Integration Stage 3 (real forex data via bot.instruments.resolve_ohlcv_path() -> the real MT5-backed files, confirmed by reading run_forex_cross_section_research()'s own source — not the stale "yahoo_finance" label in _PROVENANCE_BY_ASSET_CLASS's fingerprint-metadata dict, which is cosmetic only), but this tool never threaded it through — every Research Lab call was silently CRYPTO regardless of what a Forex-hypothesis caller asked for. Default preserves every existing call site's behavior exactly.
) -> dict:
    """
    Wraps bot.research.run_cross_sectional_oos.run_cross_sectional_research()
    unchanged — computes cross_section_engine.py's real features across
    the CURRENT universe (never hardcoded — bot.universe_selector's own
    dynamic selection for CRYPTO, the real MT5-backed 58-pair universe for
    FOREX), reshapes to long format, and runs the full Type C evaluator +
    within-timestamp permutation test + top-K FDR sweep
    (bot/research/cross_sectional_permutation_test.py) as one declared
    hypothesis family. No new statistics computed in this wrapper.

    hypothesis_name must be one of AVAILABLE_FEATURES' keys — see that
    module for which of Phase 3's named cross-sectional hypotheses are
    actually computed today (volatility-adjusted ranking is not yet
    implemented anywhere in this codebase — an honest gap, not silently
    substituted with a different feature).
    """
    if hypothesis_name not in AVAILABLE_FEATURES:
        return {
            "hypothesis_name": hypothesis_name,
            "available_hypotheses": list(AVAILABLE_FEATURES.keys()),
            "error": f"'{hypothesis_name}' is not an available cross-sectional hypothesis",
        }
    return run_cross_sectional_research(
        hypothesis_name=hypothesis_name, top_k_values=top_k_values,
        n_permutations=n_permutations, long_short=long_short, asset_class=asset_class,
    )
