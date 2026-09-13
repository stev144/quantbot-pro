# ============================================================
# bot/research/regime_conditional_permutation.py
#
# REGIME-CONDITIONAL PERMUTATION TESTING (Phase 12)
#
# claude code changed: new file — Regime-Conditional Quantitative Research
# mission. Audits and answers Phase 12's exact question: is the existing
# permutation framework reusable for a regime-conditional null, and if so,
# how — without inventing a second, incompatible permutation scheme.
#
# AUDIT FINDING (documented here, not just assumed): the existing
# within-timestamp shuffle (cross_sectional_permutation_test.py's
# _within_timestamp_shuffle) already preserves exactly the right
# structure for a regime-conditional null — it shuffles feature-to-asset
# assignment independently PER TIMESTAMP, which by construction preserves
# each timestamp's regime membership (a timestamp's regime label never
# depends on which asset a feature value is attached to). This means the
# SAME shuffle mechanism, reused unmodified, automatically produces
# a valid null for "does this regime's ranking carry information," with
# no new shuffling logic required. What genuinely does NOT already exist
# is a way to read the REGIME-SPECIFIC metric back out of each shuffled
# replica — run_cross_sectional_permutation_test() (the existing sweep
# wrapper) deliberately discards each replica's full OOSResult after
# extracting only the pooled `.aggregate`, to keep memory bounded across
# up to ~100+ replicas. That discard is exactly why this needed a NEW
# orchestration function rather than a parameter added to the existing
# one — not because the underlying statistics needed to change.
#
# NULL HYPOTHESIS (explicitly, per Phase 12's requirement): identical to
# the existing cross-sectional null (cross_sectional_permutation_test.py's
# own documented hypothesis) — at a given timestamp, the specific
# assignment of a feature's value to a specific asset carries no
# information about that asset's subsequent relative performance —
# evaluated SEPARATELY within each regime's own subset of timestamps.
#
# WHAT IS PERMUTED / PRESERVED: identical to the existing shuffle (see
# _within_timestamp_shuffle's own docstring) — this module changes
# nothing about the shuffle itself, only what is measured afterward.
#
# REGIME PRESERVATION METHOD: the regime label series is computed ONCE,
# before any shuffling, from the real (unshuffled) OHLCV history, and is
# never touched by the shuffle (which only ever permutes feature values
# within cross_section_engine's long-format panel, never timestamps or
# regime labels). Every shuffled replica is therefore evaluated against
# the exact same, real regime assignment as the observed run — the null
# is "would a random assignment of feature values still show a real
# relationship WITHIN this real regime," not "would the regime boundaries
# themselves look different under noise."
# ============================================================

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from bot.research.cross_sectional_permutation_test import DEFAULT_N_PERMUTATIONS, _within_timestamp_shuffle
from bot.research.oos_validator import WalkForwardConfig, evaluate_cross_sectional_oos
from bot.research.permutation_stats import SIGNIFICANCE_PERCENTILE, compute_permutation_verdict, is_significant
from bot.research.regime_conditional_oos import evaluate_cross_sectional_oos_by_regime
from bot.research.regime_labels import MIN_REGIME_OBS

DEFAULT_METRICS: List[str] = ["sharpe_ratio", "hit_rate_pct"]


def run_regime_conditional_permutation_test(
    df: pd.DataFrame,
    timestamp_col: str,
    asset_col: str,
    feature_col: str,
    forward_return_col: str,
    regime_labels: pd.Series,
    regime_dimension: str,
    config: WalkForwardConfig,
    top_k: int = 3,
    long_short: bool = True,
    cost_rate: float = 0.0,
    n_permutations: int = DEFAULT_N_PERMUTATIONS,
    random_seed: Optional[int] = None,
    metrics: Sequence[str] = tuple(DEFAULT_METRICS),
    significance_percentile: float = SIGNIFICANCE_PERCENTILE,
    min_obs: int = MIN_REGIME_OBS,
) -> Dict:
    """
    Real (observed) run: evaluate_cross_sectional_oos() once, unshuffled,
    then regime-sliced via evaluate_cross_sectional_oos_by_regime().

    Null distribution: n_permutations within-timestamp-shuffled replicas,
    each evaluated and regime-sliced the SAME way — never a second,
    approximate metric definition.

    Per-regime empirical significance via permutation_stats's existing,
    generic compute_permutation_verdict()/is_significant() — the SAME
    functions the whole-dataset (non-regime) permutation test uses.

    Returns
    -------
    Dict with:
        "real_by_regime": {regime: metrics dict}
        "null_by_regime": {regime: List[metrics dict]} — one per replica that had sufficient sample in that regime
        "verdict_by_regime": {regime: {..., f"{metric}_significant": bool, "edge_appears_real": bool, "n_null_replicas": int, "sufficient_sample": bool}}
        "n_permutations", "regime_dimension"
    """
    real_oos = evaluate_cross_sectional_oos(
        df, timestamp_col=timestamp_col, asset_col=asset_col, feature_col=feature_col,
        forward_return_col=forward_return_col, config=config, top_k=top_k, long_short=long_short, cost_rate=cost_rate,
    )
    real_by_regime_full = evaluate_cross_sectional_oos_by_regime(
        real_oos, regime_labels, regime_dimension=regime_dimension, cost_rate=cost_rate, min_obs=min_obs,
    )
    real_by_regime = {r: v["metrics"] for r, v in real_by_regime_full.pooled_by_regime.items() if v["sufficient_sample"]}
    regime_sample_sizes = {r: v["n_periods"] for r, v in real_by_regime_full.pooled_by_regime.items()}

    rng = np.random.default_rng(random_seed)
    null_by_regime: Dict[str, List[Dict]] = {r: [] for r in real_by_regime}

    for i in range(1, n_permutations + 1):
        shuffled_df = _within_timestamp_shuffle(df, timestamp_col, feature_col, rng)
        shuffled_oos = evaluate_cross_sectional_oos(
            shuffled_df, timestamp_col=timestamp_col, asset_col=asset_col, feature_col=feature_col,
            forward_return_col=forward_return_col, config=config, top_k=top_k, long_short=long_short, cost_rate=cost_rate,
        )
        shuffled_by_regime = evaluate_cross_sectional_oos_by_regime(
            shuffled_oos, regime_labels, regime_dimension=regime_dimension, cost_rate=cost_rate, min_obs=min_obs,
        )
        for regime in real_by_regime:
            regime_result = shuffled_by_regime.pooled_by_regime.get(regime)
            if regime_result is not None and regime_result["sufficient_sample"]:
                null_by_regime[regime].append(regime_result["metrics"])

    verdict_by_regime: Dict[str, Dict] = {}
    for regime, real_metrics in real_by_regime.items():
        null_metrics = null_by_regime[regime]
        if len(null_metrics) < max(10, n_permutations // 2):
            # claude code changed: a regime that too rarely has enough
            # sample under shuffling to even form a null distribution
            # cannot be honestly tested — reported explicitly, never
            # silently tested against a thin, unreliable null.
            verdict_by_regime[regime] = {
                "status": "INSUFFICIENT_NULL_REPLICAS",
                "n_null_replicas": len(null_metrics), "n_permutations": n_permutations,
                "reason": f"only {len(null_metrics)}/{n_permutations} shuffled replicas had sufficient sample in regime {regime!r}",
            }
            continue
        verdict = compute_permutation_verdict(real_metrics, null_metrics, metrics=list(metrics), significance_percentile=significance_percentile)
        for metric in metrics:
            verdict[f"{metric}_significant"] = is_significant(verdict, metric, significance_percentile)
        verdict["edge_appears_real"] = bool(all(verdict.get(f"{m}_significant", False) for m in metrics))
        verdict["status"] = "OK"
        verdict["n_null_replicas"] = len(null_metrics)
        verdict_by_regime[regime] = verdict

    return {
        "regime_dimension": regime_dimension,
        "n_permutations": n_permutations,
        "regime_sample_sizes": regime_sample_sizes,
        "real_by_regime": real_by_regime,
        "null_by_regime": null_by_regime,
        "verdict_by_regime": verdict_by_regime,
    }
