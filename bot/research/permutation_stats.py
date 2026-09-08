# ═══════════════════════════════════════════════════════════════════════════════
# bot/research/permutation_stats.py
#
# SHARED PERMUTATION-TEST STATISTICS
#
# claude code changed: new file. Extracted, verbatim in behavior, from
# PermutationTestEngine._compare() (bot/research/permutation_test_engine.py).
# WHY: the mission to build a cross-sectional (Type C) permutation test
# explicitly requires reusing the existing statistical machinery rather
# than inventing a second, incompatible significance framework — the
# percentile/p-value/significance math itself has nothing pairs-specific
# about it (it operates on plain dicts of metric name -> float), so it
# belongs here as a pure, dependency-free function both
# permutation_test_engine.py (pairs) and cross_sectional_permutation_test.py
# (rankings) call identically. permutation_test_engine.py's own 9 tests
# (bot/tests/test_permutation_test_engine.py) are the regression guard
# that this extraction changed nothing — they exercise this exact logic
# through PermutationTestEngine._compare(), which now simply delegates
# here.
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

# A result is only called "statistically real" if the actual, unshuffled
# run beats at least this fraction of shuffled replicas. 0.95 is the
# conventional two-sided-adjacent one-sided threshold (p < 0.05).
SIGNIFICANCE_PERCENTILE: float = 0.95


def compute_permutation_verdict(
    real: Dict[str, float],
    shuffled: List[Dict[str, float]],
    metrics: List[str],
    significance_percentile: float = SIGNIFICANCE_PERCENTILE,
) -> Dict:
    """
    Rank the real result against the shuffled distribution for each
    metric in `metrics` and compute an empirical permutation p-value: the
    fraction of shuffled replicas that matched or beat the real result.

    A small p-value (real result rarely matched by shuffled noise) means
    the edge is statistically distinguishable from whatever mechanical
    effect the permutation is designed to destroy. A large p-value means
    it isn't — shuffled data already produces results this good.

    This function is intentionally generic: `real` and every element of
    `shuffled` are plain {metric_name: float} dicts. It has no opinion on
    WHAT was shuffled or WHY (that's the caller's job — see
    permutation_test_engine.py's block-shuffle for the pairs/temporal-
    alignment null, or cross_sectional_permutation_test.py's within-
    timestamp label shuffle for the cross-sectional-ranking null) — only
    on how to compare a real result to a shuffled distribution honestly.

    Parameters
    ----------
    real : Dict[str, float]
        The real (unshuffled) run's metric values.
    shuffled : List[Dict[str, float]]
        One dict per shuffled replica.
    metrics : List[str]
        Which keys of `real`/`shuffled` to compute a percentile/p-value
        for. Every metric gets its own `{metric}_percentile` and
        `{metric}_p_value` entry in the returned dict.
    significance_percentile : float
        See module docstring — 0.95 means p < 0.05 is "significant".

    Returns
    -------
    Dict with, for every metric in `metrics`:
        f"{metric}_percentile" : fraction of shuffled replicas the real
            result beats (NaN if nothing usable to compare against).
        f"{metric}_p_value" : empirical p-value, using the standard "+1"
            correction (Davison & Hinkley; Phipson & Smyth 2010 —
            "permutation p-values should never be zero"). NaN if nothing
            usable to compare against.
    """
    verdict: Dict = {}
    for metric in metrics:
        shuffled_values = np.array([r[metric] for r in shuffled if not pd.isna(r.get(metric))])
        real_value = real.get(metric)

        if len(shuffled_values) == 0 or real_value is None or pd.isna(real_value):
            verdict[f"{metric}_percentile"] = np.nan
            verdict[f"{metric}_p_value"] = np.nan
            continue

        percentile = float(np.mean(shuffled_values <= real_value))
        p_value = float((np.sum(shuffled_values >= real_value) + 1) / (len(shuffled_values) + 1))
        verdict[f"{metric}_percentile"] = percentile
        verdict[f"{metric}_p_value"] = p_value

    return verdict


def is_significant(verdict: Dict, metric: str, significance_percentile: float = SIGNIFICANCE_PERCENTILE) -> bool:
    """
    True iff `verdict[f"{metric}_p_value"]` exists, is not NaN, and clears
    the significance threshold. Small helper so callers don't each
    re-derive `1 - significance_percentile` and re-handle the NaN case.
    """
    p_value = verdict.get(f"{metric}_p_value", np.nan)
    return (not pd.isna(p_value)) and p_value < (1 - significance_percentile)
