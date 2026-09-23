# ============================================================
# bot/research_lab/tools/regime_tools.py
# Research Lab tool: run_regime_conditional_test.
#
# claude code changed: new file — closes a real, previously-documented gap.
# bot/research/regime_labels.py, regime_conditional_ic.py, and
# regime_conditional_status.py are real, unit-tested engines (Phase 2/7-10
# of the Regime-Conditional Quantitative Research mission), and
# capability_registry.py's continuous_feature_research entry already
# DECLARES supported_regimes=["trend_state","volatility_state","regime_label"]
# — but nothing in orchestrator.py ever actually called them.
# bot/research_lab/verdict.py's own compute_verdict() docstring says so
# explicitly: "REGIME_DEPENDENT... [is] a real state in the taxonomy but
# this MVP pass has no regime-stratified tool output... rather than
# fabricating a path to them here." This tool is that missing path.
#
# SCOPE, DELIBERATELY LIMITED: this wraps compute_regime_conditional_ic()
# + test_regime_interaction() (Phases 7-10) only — the IC/interaction
# stage. It does NOT wire in regime_conditional_oos.py or
# regime_conditional_permutation.py (Phases 11-12), which each require a
# much heavier real run (a full walk-forward OOS evaluation, or N real
# permutation replicas — the existing cross-sectional permutation test
# alone took ~14.5 minutes for 5 shuffles in this project's own test
# suite). Passing oos_by_regime=None and permutation_verdict_for_regime=None
# to compute_regime_conditional_status() is not a shortcut hack — it is
# that function's own documented, intended behavior for a caller who only
# ran the earlier stage: "a caller who only ran the IC/interaction stage
# gets a status no stronger than REGIME_DEPENDENT, honestly reflecting how
# far the evidence actually goes." Wiring in the OOS/permutation stages is
# a real, separate, future increment — named here, not silently deferred.
# ============================================================

from bot.instruments import ASSET_CLASS_CRYPTO, ASSET_CLASS_FOREX, periods_per_year
from bot.research.cross_section_engine import (
    run_cross_section_research as compute_cross_section_features,
    run_forex_cross_section_research as compute_forex_cross_section_features,
)
from bot.research.oos_validator import WalkForwardConfig
from bot.research.regime_conditional_ic import compute_regime_conditional_ic, test_regime_interaction
from bot.research.regime_conditional_permutation import run_regime_conditional_permutation_test
from bot.research.regime_conditional_status import compute_regime_conditional_status
from bot.research.regime_labels import compute_regime_labels
from bot.research.run_cross_sectional_oos import AVAILABLE_FEATURES, build_long_format_panel
from bot.research_lab.tools._data import load_ohlcv
from bot.research_lab.tools.base import register_tool
from bot.research_lab.tools.statistical_tools import _prepare

REGIME_DIMENSIONS = ("trend_state", "volatility_state", "regime_label")

# claude code changed: new — matches run_cross_sectional_oos.py's own
# unexported per-asset-class defaults (that module's WalkForwardConfig
# block and cost_rate resolution), reused here rather than re-derived, so
# a regime-conditional cross-sectional run and a plain one are directly
# comparable — never a second, silently-different config for the same
# asset class.
_DEFAULT_REGIME_BENCHMARK = {ASSET_CLASS_CRYPTO: "BTC/USDT", ASSET_CLASS_FOREX: "EUR/USD"}


@register_tool("run_regime_conditional_test")
def run_regime_conditional_test(
    asset: str, feature_name: str, horizon: int, regime_of_interest: str, random_seed: int = 42,
) -> dict:
    """
    Real IC + FDR correction per regime cell, plus a direct interaction
    test (does the feature-return relationship ACTUALLY differ by regime,
    not just "significant in one cell and not another") — never inferred,
    never cherry-picked: regime_of_interest is named by the caller up
    front (the spec, set before this tool ever runs), and this function
    computes every regime cell's real result regardless of which one
    turns out to look best.
    """
    enriched, forward_col, timeframe, asset_class = _prepare(asset, horizon)
    if feature_name not in enriched.columns:
        raise ValueError(f"'{feature_name}' is not a column feature_calculator.py produced")

    # claude code changed: reuses compute_regime_labels() on the SAME
    # OHLCV this feature panel was built from (enriched carries a
    # DatetimeIndex the same way every other tool in this package
    # assumes) — never a second, independently-fetched regime source that
    # could silently disagree with the feature panel's own timestamps.
    # compute_regime_labels() only requires high/low/close (verified by
    # reading its own required-columns check), both of which
    # FeatureCalculator preserves from the source OHLCV onto `enriched`.
    regime_df = compute_regime_labels(enriched[["high", "low", "close"]])
    # claude code changed: real bug fix, caught by actually running this
    # tool end-to-end — feature_calculator.py's own output already has a
    # "volatility_state" column (a different, separate concept). regime_
    # labels.py's whole purpose is being THE canonical regime source for
    # research use (see its own module docstring), so its columns must
    # win here, not silently collide/error — drop any pre-existing
    # feature-calculator columns with the same name before joining.
    conflicting = [c for c in REGIME_DIMENSIONS if c in enriched.columns]
    working = enriched.drop(columns=conflicting).join(regime_df[list(REGIME_DIMENSIONS)])

    ic_report = compute_regime_conditional_ic(
        working, feature_col=feature_name, forward_return_col=forward_col, regime_dimensions=list(REGIME_DIMENSIONS),
    )

    # Find which dimension actually contains regime_of_interest, matching
    # compute_regime_conditional_status()'s own _find_regime_cell() search
    # order exactly — the interaction test needs one concrete column name.
    interaction_dimension = None
    for dimension in REGIME_DIMENSIONS:
        if ic_report.cell(dimension, regime_of_interest) is not None:
            interaction_dimension = dimension
            break

    interaction_result = None
    if interaction_dimension is not None:
        interaction_result = test_regime_interaction(
            working, feature_col=feature_name, forward_return_col=forward_col, regime_col=interaction_dimension,
        )

    status = compute_regime_conditional_status(
        regime_of_interest=regime_of_interest,
        ic_report=ic_report,
        interaction_result=interaction_result,
        oos_by_regime=None,  # claude code changed: deliberately not wired — see module docstring
        permutation_verdict_for_regime=None,  # claude code changed: deliberately not wired — see module docstring
    )

    return {
        "asset": asset, "feature_name": feature_name, "horizon": horizon,
        "regime_of_interest": regime_of_interest, "regime_dimension_matched": interaction_dimension,
        "ic_report": ic_report.to_dict(),
        "interaction_test": interaction_result,
        "regime_conditional_status": status.to_dict(),
        "random_seed": random_seed,
        "scope_note": "IC + interaction test only (Phases 7-10) — OOS/permutation stages (Phases 11-12) not yet wired in for THIS (single-asset feature) hypothesis shape, so status can reach REGIME_DEPENDENT at most here. See run_cross_sectional_regime_conditional_test() below for the cross-sectional hypothesis shape, which DOES reach OOS/permutation/economic evidence.",
    }


def _resolve_cross_sectional_cost_and_config(panel, asset_class: str, cost_rate, config):
    """claude code changed: new — deliberately mirrors
    bot.research.run_cross_sectional_oos.run_cross_sectional_research()'s
    own cost_rate/config resolution EXACTLY (same real Forex average-cost
    lookup via get_cost_model, same crypto 0.0015 default, same
    WalkForwardConfig parameters), so a regime-conditional run and a
    plain run of the same hypothesis are directly comparable — never a
    second, silently-different default living in this file."""
    from bot.config.cost_model import get_cost_model

    if cost_rate is None:
        if asset_class == ASSET_CLASS_FOREX:
            round_trip_costs = []
            for underscore_symbol in sorted(panel["asset"].unique()):
                canonical = underscore_symbol.replace("_", "/", 1)
                try:
                    costs = get_cost_model(ASSET_CLASS_FOREX, symbol=canonical).get_costs()
                    round_trip_costs.append(2 * (costs["fee_rate"] + costs["slippage_rate"]))
                except Exception:
                    continue
            if not round_trip_costs:
                raise ValueError("cost_rate was not supplied and no real Forex cost could be resolved for any instrument in the panel")
            cost_rate = sum(round_trip_costs) / len(round_trip_costs)
        else:
            cost_rate = 0.0015

    if config is None:
        config = WalkForwardConfig(
            mode="expanding", min_train_periods=int(periods_per_year("1h", asset_class)), test_periods=720,
            horizon=1, purge_periods=1, embargo_periods=0, min_test_periods=168, seed=42,
        )
    return cost_rate, config


@register_tool("run_cross_sectional_regime_conditional_test")
def run_cross_sectional_regime_conditional_test(
    hypothesis_name: str,
    asset_class: str,
    regime_of_interest: str,
    top_k: int = 1,
    long_short: bool = True,
    n_permutations: int = 20,
    regime_benchmark_asset: str = None,
    random_seed: int = 42,
    rebalance_frequency: int = 24,   # claude code changed: real bug fix, caught by actually running this — matches run_cross_sectional_research()'s own realistic default (daily rebalancing on this hourly panel). run_regime_conditional_permutation_test() itself defaults to 1 (hourly full-book turnover, for its own backward compatibility) — this tool explicitly overrides that here so a regime-conditional run and a plain run of the same hypothesis are testing the SAME realistic strategy, not silently different ones. Without this, the first real run of this tool produced Sharpe -7.3 and 99.8% capital loss purely from unrealistic hourly-churn transaction costs, not from the underlying signal.
) -> dict:
    """
    Real Phase 11/12 evidence for a CROSS-SECTIONAL ranking hypothesis
    (never the single-asset continuous-feature shape
    run_regime_conditional_test above handles — see
    bot.research.regime_conditional_permutation's own module docstring
    for why these are genuinely different hypothesis shapes, not two
    ways of asking the same question).

    Builds the SAME long-format panel run_cross_sectional_research()
    would (real features across the current universe — real MT5-backed
    58-pair universe for FOREX, bot.universe_selector's dynamic selection
    for CRYPTO), resolves the SAME cost_rate/WalkForwardConfig defaults,
    then computes ONE benchmark instrument's regime label series
    (default: BTC/USDT for CRYPTO, EUR/USD for FOREX — "the market's own
    regime," not a per-asset one, matching how every other regime-
    conditional tool in this project reasons about regime) and runs
    bot.research.regime_conditional_permutation.
    run_regime_conditional_permutation_test(), which internally computes
    BOTH the real (Phase 11) and permutation-null (Phase 12) OOS-by-regime
    evidence in one pass — no separate OOS-only call needed.
    """
    if hypothesis_name not in AVAILABLE_FEATURES:
        return {
            "hypothesis_name": hypothesis_name,
            "available_hypotheses": list(AVAILABLE_FEATURES.keys()),
            "error": f"'{hypothesis_name}' is not an available cross-sectional hypothesis",
        }
    feature_col = AVAILABLE_FEATURES[hypothesis_name]

    if asset_class == ASSET_CLASS_FOREX:
        enriched_data = compute_forex_cross_section_features()
    else:
        enriched_data = compute_cross_section_features()
    panel = build_long_format_panel(enriched_data, feature_col=feature_col)

    cost_rate, config = _resolve_cross_sectional_cost_and_config(panel, asset_class, None, None)

    benchmark = regime_benchmark_asset or _DEFAULT_REGIME_BENCHMARK.get(asset_class)
    if benchmark is None:
        return {"error": f"no default regime benchmark instrument known for asset_class={asset_class!r} — pass regime_benchmark_asset explicitly"}
    benchmark_df = load_ohlcv(benchmark)
    regime_df = compute_regime_labels(benchmark_df[["high", "low", "close"]])

    regime_dimension = None
    for dimension in REGIME_DIMENSIONS:
        if regime_of_interest in set(regime_df[dimension].dropna().unique()):
            regime_dimension = dimension
            break
    if regime_dimension is None:
        return {
            "error": f"regime {regime_of_interest!r} was never observed for benchmark {benchmark!r} in any taxonomy dimension",
            "regime_conditional_status": compute_regime_conditional_status(regime_of_interest, oos_by_regime=None).to_dict(),
        }

    result = run_regime_conditional_permutation_test(
        df=panel, timestamp_col="timestamp", asset_col="asset", feature_col="feature", forward_return_col="forward_return",
        regime_labels=regime_df[regime_dimension], regime_dimension=regime_dimension, config=config,
        top_k=top_k, long_short=long_short, cost_rate=cost_rate, n_permutations=n_permutations, random_seed=random_seed,
        rebalance_frequency=rebalance_frequency,
    )

    # claude code changed: adapts run_regime_conditional_permutation_test()'s
    # flat real_by_regime/regime_sample_sizes dicts into the
    # .pooled_by_regime shape compute_regime_conditional_status() expects
    # (matches RegimeConditionalOOSResult's real shape) — cheaper than a
    # second call into evaluate_cross_sectional_oos_by_regime(), since
    # real_by_regime already only contains regimes that had
    # sufficient_sample=True (see that function's own filtering).
    class _OOSByRegimeAdapter:
        pooled_by_regime = {
            regime: {"sufficient_sample": True, "metrics": metrics, "n_periods": result["regime_sample_sizes"].get(regime)}
            for regime, metrics in result["real_by_regime"].items()
        }

    permutation_verdict_for_regime = result["verdict_by_regime"].get(regime_of_interest)
    status = compute_regime_conditional_status(
        regime_of_interest, oos_by_regime=_OOSByRegimeAdapter(), permutation_verdict_for_regime=permutation_verdict_for_regime,
    )

    return {
        "hypothesis_name": hypothesis_name, "asset_class": asset_class, "feature_col": feature_col,
        "regime_of_interest": regime_of_interest, "regime_dimension": regime_dimension, "regime_benchmark_asset": benchmark,
        "top_k": top_k, "n_permutations": n_permutations, "cost_rate": cost_rate,
        "real_by_regime": result["real_by_regime"], "regime_sample_sizes": result["regime_sample_sizes"],
        "verdict_by_regime": result["verdict_by_regime"],
        "regime_conditional_status": status.to_dict(),
    }
