# ═══════════════════════════════════════════════════════════════════════════════
# bot/research/cross_sectional_permutation_test.py
#
# CROSS-SECTIONAL (TYPE C) PERMUTATION FALSIFICATION TEST
#
# claude code changed: new file — Milestone B2 of the statistics-
# infrastructure mission. Completes evaluate_cross_sectional_oos()
# (bot/research/oos_validator.py) with the one thing Phase 1's audit
# found genuinely missing: a permutation significance test appropriate
# to the CROSS-SECTIONAL research question, reusing (never duplicating)
# the same statistics permutation_test_engine.py already proved correct
# for the pairs question — see bot/research/permutation_stats.py.
#
# ─────────────────────────────────────────────────────────────────────────────
# METHODOLOGY — stated explicitly before implementation, per the mission's
# own requirement to explain: the null hypothesis, what is permuted, what
# structure is preserved, why this permutation is appropriate, and how
# multiple testing is controlled.
# ─────────────────────────────────────────────────────────────────────────────
#
# THE RESEARCH QUESTION (Phase 3 of the mission): "At a given timestamp,
# does the relative ranking of assets across the universe contain
# predictive information about subsequent returns?" This is a claim about
# the ASSIGNMENT of feature values to assets, not about calendar
# alignment — a fundamentally different question from the pairs
# cointegration test's "does WHICH WEEK follows WHICH WEEK matter?"
# (permutation_test_engine.py's block-shuffle, correct for THAT
# question, would be the WRONG null here — it doesn't touch which asset
# has which rank at all).
#
# NULL HYPOTHESIS: at a given timestamp, the specific assignment of a
# feature's value to a specific asset carries no information about that
# asset's subsequent relative performance. Equivalently: the ranking is
# exchangeable across assets within a timestamp.
#
# WHAT IS PERMUTED: within EACH timestamp, independently, the mapping
# between `feature_col` values and assets is shuffled — asset labels are
# randomly reassigned to feature values AT THAT SINGLE TIMESTAMP ONLY.
# A different, independent shuffle is drawn at every timestamp (not one
# shuffle applied uniformly across all time).
#
# WHAT IS PRESERVED:
#   - The full time-series/fold structure: build_folds()/purge/embargo/
#     assert_temporal_disjoint() are entirely untouched — the shuffle
#     never changes which TIMESTAMPS exist or how they're split into
#     folds, only which asset's feature value sits at which row within a
#     timestamp. evaluate_cross_sectional_oos() is called unmodified.
#   - The cross-sectional DISTRIBUTION of the feature at each timestamp
#     (same set of values, same day — a market-wide condition like "every
#     asset's z-score was elevated today" survives the shuffle intact;
#     only WHICH asset had WHICH value is randomized).
#   - The cross-sectional distribution of forward returns at each
#     timestamp (untouched — returns stay attached to their real asset)
#     — a shuffle of FEATURE labels cannot manufacture market-wide drift.
#
# WHY THIS IS THE APPROPRATE PERMUTATION: this is the standard technique
# for testing cross-sectional/panel ranking significance (the same
# family as randomization tests used in cross-sectional equity-factor
# research) — it destroys exactly the one thing being tested (does THIS
# asset's rank predict THIS asset's return) while leaving every other
# statistical property of the panel (marginal feature distribution,
# marginal return distribution, calendar structure) exactly as it was.
#
# MULTIPLE TESTING: Phase 5 requires testing several top-K configurations
# (1/1, 3/3, 5/5, 10/10) without treating any one of them as "the"
# strategy. run_topk_sweep() below runs the full permutation test once
# per configuration, then applies statsmodels.stats.multitest.
# multipletests() (Benjamini-Hochberg FDR, the same method and library
# already used identically in cointegration_engine.py's
# _apply_fdr_correction() and contagion_engine.py's DivergenceICReporter)
# across every configuration's p-value for a given metric BEFORE any
# configuration is called significant — the sweep is one hypothesis
# family, not four independent tests.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from statsmodels.stats.multitest import multipletests

from bot.research.oos_validator import WalkForwardConfig, evaluate_cross_sectional_oos
from bot.research.permutation_stats import SIGNIFICANCE_PERCENTILE, compute_permutation_verdict, is_significant

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

DEFAULT_N_PERMUTATIONS: int = 100   # Same floor rationale as permutation_test_engine.py: 1/(N+1) must clear p<0.05.
DEFAULT_METRICS: List[str] = ["sharpe_ratio", "hit_rate_pct"]   # Keys present in OOSResult.aggregate for evaluation_type="cross_sectional_ranking"


def _within_timestamp_shuffle(
    df: pd.DataFrame, timestamp_col: str, feature_col: str, rng: np.random.Generator,
) -> pd.DataFrame:
    """
    Independently permute `feature_col` values across rows sharing the
    same `timestamp_col` value. `asset_col`/`forward_return_col` (and
    every other column) stay attached to their original row — only the
    feature value each row carries is reassigned within its own
    timestamp group. See module docstring for why this, and not a
    temporal block-shuffle, is the correct null for this question.
    """
    shuffled = df.copy()
    original_values = df[feature_col].to_numpy(copy=True)   # snapshot BEFORE any reassignment — read-only source for every group
    values = original_values.copy()                          # write target — mutated group-by-group below
    # claude code changed: single-pass positional shuffle via a groupby of
    # row positions — avoids a per-group DataFrame copy (pandas groupby.
    # apply materializes a new frame per group; this project's data sizes
    # for a 100-asset universe make that a real, avoidable cost at
    # n_permutations=100+), and avoids recomputing to_numpy() per group.
    for _, position_idx in shuffled.groupby(timestamp_col, sort=False).groups.items():
        pos = shuffled.index.get_indexer(position_idx)
        permuted = rng.permutation(pos)
        values[pos] = original_values[permuted]
    shuffled[feature_col] = values
    return shuffled


def run_cross_sectional_permutation_test(
    df: pd.DataFrame,
    timestamp_col: str,
    asset_col: str,
    feature_col: str,
    forward_return_col: str,
    config: WalkForwardConfig,
    top_k: int = 3,
    long_short: bool = True,
    cost_rate: float = 0.0,
    fit_fn: Optional[Callable[[pd.DataFrame], Dict]] = None,
    fold_initial_balance: float = 10_000.0,
    strategy_name: str = "",
    strategy_version: str = "",
    data_fingerprint: Optional[str] = None,
    n_permutations: int = DEFAULT_N_PERMUTATIONS,
    random_seed: Optional[int] = None,
    metrics: Sequence[str] = tuple(DEFAULT_METRICS),
    significance_percentile: float = SIGNIFICANCE_PERCENTILE,
    rebalance_frequency: int = 1,   # claude code changed: new — threaded through to evaluate_cross_sectional_oos(), see its own docstring for the real bug this fixes (hourly full-book-turnover cost assumption)
) -> Dict:
    """
    Run evaluate_cross_sectional_oos() once on the real data and once per
    within-timestamp-shuffled replica, then compare via the SAME
    percentile/p-value statistics permutation_test_engine.py already
    uses for pairs (bot/research/permutation_stats.py) — never a second,
    incompatible framework.

    Returns
    -------
    Dict with:
        "real": OOSResult.aggregate for the real (unshuffled) run
        "shuffled": List[Dict] — OOSResult.aggregate for every replica
        "verdict": output of compute_permutation_verdict() plus
            f"{metric}_significant" flags and "edge_appears_real"
        "real_result" / "n_folds_evaluated": passthrough for callers that
            want the full OOSResult, not just its aggregate
    """
    rng = np.random.default_rng(random_seed)

    logger.info("=" * 70)
    logger.info(f"CROSS-SECTIONAL PERMUTATION TEST — top_k={top_k}, long_short={long_short}")
    logger.info("=" * 70)

    real_result = evaluate_cross_sectional_oos(
        df, timestamp_col=timestamp_col, asset_col=asset_col, feature_col=feature_col,
        forward_return_col=forward_return_col, config=config, top_k=top_k, long_short=long_short,
        cost_rate=cost_rate, fit_fn=fit_fn, fold_initial_balance=fold_initial_balance,
        strategy_name=strategy_name, strategy_version=strategy_version, data_fingerprint=data_fingerprint,
        rebalance_frequency=rebalance_frequency,
    )
    real_metrics = real_result.aggregate

    shuffled_metrics: List[Dict] = []
    for i in range(1, n_permutations + 1):
        shuffled_df = _within_timestamp_shuffle(df, timestamp_col, feature_col, rng)
        shuffled_result = evaluate_cross_sectional_oos(
            shuffled_df, timestamp_col=timestamp_col, asset_col=asset_col, feature_col=feature_col,
            forward_return_col=forward_return_col, config=config, top_k=top_k, long_short=long_short,
            cost_rate=cost_rate, fit_fn=fit_fn, fold_initial_balance=fold_initial_balance,
            strategy_name=strategy_name, strategy_version=strategy_version, data_fingerprint=data_fingerprint,
            rebalance_frequency=rebalance_frequency,
        )
        shuffled_metrics.append(shuffled_result.aggregate)
        if i % 10 == 0 or i == n_permutations:
            logger.info(f"  Completed {i}/{n_permutations} within-timestamp shuffles")

    verdict = compute_permutation_verdict(
        real_metrics, shuffled_metrics, metrics=list(metrics), significance_percentile=significance_percentile,
    )
    for metric in metrics:
        verdict[f"{metric}_significant"] = is_significant(verdict, metric, significance_percentile)

    # claude code changed: edge_appears_real requires EVERY configured
    # metric to be significant (conservative AND, same design choice
    # permutation_test_engine.py's own edge_appears_real makes for pairs
    # — a single cherry-picked significant metric out of several tested
    # is not "an edge," it's exactly the multiple-comparisons trap this
    # whole mission exists to avoid).
    verdict["edge_appears_real"] = bool(all(verdict.get(f"{m}_significant", False) for m in metrics))

    return {
        "real": real_metrics,
        "shuffled": shuffled_metrics,
        "verdict": verdict,
        "real_result": real_result,
        "n_permutations": n_permutations,
        "top_k": top_k,
        "long_short": long_short,
    }


# claude code changed: new — per-config checkpointing. A single top_k
# config in the real 100-asset universe (~100 permutations, each a full
# evaluate_cross_sectional_oos() fold walk) takes hours; run_topk_sweep()
# tests 4 configs in sequence. Without a checkpoint, ANY interruption
# (machine sleep/reboot, a killed process — this is exactly what happened
# overnight: a run that had already finished 2 of 4 configs was lost
# entirely because nothing on disk recorded that) forces a full restart
# from config 1. This writes one small JSON file per COMPLETED config
# (never per-shuffle — the mission's own ask was per-config granularity,
# and per-shuffle would mean re-touching disk on every one of ~400 total
# shuffles across a sweep, real added I/O cost for no real benefit at
# this granularity) and skips recomputing any config whose checkpoint is
# already on disk.

def _json_safe(obj):
    """Recursively convert numpy scalar types to native Python so
    json.dump produces real numbers, not stringified ones (default=str
    as the json.dump fallback would silently turn e.g. np.float64(1.23)
    into the STRING "1.23" instead of the number 1.23)."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    return obj


def _checkpoint_path(checkpoint_dir: str, top_k: int) -> Path:
    return Path(checkpoint_dir) / f"topk_{top_k}.json"


def _load_checkpoint(checkpoint_dir: str, top_k: int) -> Optional[Dict]:
    path = _checkpoint_path(checkpoint_dir, top_k)
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _save_checkpoint(checkpoint_dir: str, top_k: int, result: Dict) -> None:
    path = _checkpoint_path(checkpoint_dir, top_k)
    path.parent.mkdir(parents=True, exist_ok=True)
    # claude code changed: real_result (an OOSResult dataclass instance)
    # is deliberately EXCLUDED from the checkpoint — it is not JSON-
    # serializable, and nothing downstream of run_topk_sweep() (verified:
    # run_cross_sectional_oos.py's run_cross_sectional_research() only
    # reads per_config[k]["real"]/["verdict"]) ever reads it back out.
    # A config restored from checkpoint gets real_result=None instead —
    # a caller relying on the live OOSResult object would need to re-run
    # that specific config with force_recompute=True, never silently get
    # a stale/wrong object.
    payload = {
        "top_k": result["top_k"], "real": result["real"], "shuffled": result["shuffled"],
        "verdict": result["verdict"], "n_permutations": result["n_permutations"],
        "long_short": result["long_short"],
    }
    with open(path, "w") as f:
        json.dump(_json_safe(payload), f, indent=2, default=str)


def run_topk_sweep(
    df: pd.DataFrame,
    timestamp_col: str,
    asset_col: str,
    feature_col: str,
    forward_return_col: str,
    config: WalkForwardConfig,
    top_k_values: Sequence[int] = (1, 3, 5, 10),
    metric_for_fdr: str = "sharpe_ratio",
    checkpoint_dir: Optional[str] = None,
    force_recompute: bool = False,
    **kwargs,
) -> Dict:
    """
    Phase 5: test several top-K portfolio-construction configurations
    without changing the core evaluator, treating the whole sweep as ONE
    declared hypothesis family — every configuration's p-value on
    `metric_for_fdr` is collected and passed through Benjamini-Hochberg
    FDR correction together (statsmodels.stats.multitest.multipletests,
    same call already used in cointegration_engine.py/contagion_engine.py)
    before any one configuration is called significant. A configuration
    surviving its own single-test p-value but failing FDR is exactly the
    "looks good until you account for how many configs you tried" trap
    this exists to catch — top_k / bottom_k is a portfolio-construction
    HYPOTHESIS to test, per the mission, never presented as "the"
    strategy.

    checkpoint_dir : Optional[str]
        When given, each config's result is saved to
        f"{checkpoint_dir}/topk_{k}.json" the moment it finishes, and any
        config whose checkpoint already exists is loaded from disk
        instead of recomputed — so an interrupted sweep (machine
        sleep/reboot, a killed process) resumes from the first
        NOT-yet-completed config on the next call with the same
        checkpoint_dir, rather than restarting the whole sweep. None
        (default) preserves this function's original behavior exactly —
        no checkpoint files written or read, matching every existing
        caller (including this module's own tests, none of which pass
        checkpoint_dir).

    force_recompute : bool
        True bypasses any existing checkpoint for every config in this
        call (recomputing and overwriting) — the deliberate escape hatch
        for "the underlying code changed, trust a checkpoint no longer."
    """
    per_config: Dict[int, Dict] = {}
    for k in top_k_values:
        if checkpoint_dir is not None and not force_recompute:
            cached = _load_checkpoint(checkpoint_dir, k)
            if cached is not None:
                logger.info(f"  top_k={k}: loaded from checkpoint {_checkpoint_path(checkpoint_dir, k)} — skipping recomputation")
                per_config[k] = {**cached, "real_result": None}   # claude code changed: see _save_checkpoint's comment on why real_result is None here
                continue

        per_config[k] = run_cross_sectional_permutation_test(
            df, timestamp_col, asset_col, feature_col, forward_return_col, config, top_k=k, **kwargs,
        )

        if checkpoint_dir is not None:
            _save_checkpoint(checkpoint_dir, k, per_config[k])
            logger.info(f"  top_k={k}: checkpoint saved to {_checkpoint_path(checkpoint_dir, k)}")

    raw_pvalues = [per_config[k]["verdict"].get(f"{metric_for_fdr}_p_value", np.nan) for k in top_k_values]
    usable = [(k, p) for k, p in zip(top_k_values, raw_pvalues) if not pd.isna(p)]

    fdr_results: Dict[int, Dict] = {}
    if usable:
        ks, pvals = zip(*usable)
        reject, pvals_corrected, _, _ = multipletests(list(pvals), alpha=(1 - SIGNIFICANCE_PERCENTILE), method="fdr_bh")
        for k, rej, p_corr in zip(ks, reject, pvals_corrected):
            fdr_results[k] = {"passes_fdr": bool(rej), "p_value_fdr": float(p_corr)}
    for k in top_k_values:
        if k not in fdr_results:
            fdr_results[k] = {"passes_fdr": False, "p_value_fdr": np.nan}

    for k in top_k_values:
        per_config[k]["verdict"][f"{metric_for_fdr}_passes_fdr"] = fdr_results[k]["passes_fdr"]
        per_config[k]["verdict"][f"{metric_for_fdr}_p_value_fdr"] = fdr_results[k]["p_value_fdr"]
        # claude code changed: FDR-gated edge_appears_real — a config must
        # clear BOTH its own per-metric significance (edge_appears_real
        # above) AND survive family-wide FDR correction on metric_for_fdr.
        per_config[k]["verdict"]["edge_appears_real_after_fdr"] = bool(
            per_config[k]["verdict"]["edge_appears_real"] and fdr_results[k]["passes_fdr"]
        )

    any_survivor = any(per_config[k]["verdict"]["edge_appears_real_after_fdr"] for k in top_k_values)
    return {
        "per_config": per_config,
        "top_k_values": list(top_k_values),
        "metric_for_fdr": metric_for_fdr,
        "any_survivor": any_survivor,
    }
