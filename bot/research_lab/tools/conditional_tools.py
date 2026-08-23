# ============================================================
# bot/research_lab/tools/conditional_tools.py
# Research Lab tool: run_conditional_test (Conditional Hypothesis
# Integrity fix — Bug 1/7).
#
# claude code changed: new file. A CONDITIONAL/event hypothesis
# ("WHEN RSI falls below 30, subsequent returns are higher") is a
# genuinely different research question from a continuous FEATURE
# hypothesis ("RSI correlates with subsequent returns") — see
# bot/research_lab/spec.py's module docstring for the full statement of
# this principle. feature_validator.py has no conditional/event-filter
# test today (only continuous IC and quartile-based tests), so this tool
# does NOT reuse it for this question — that would silently substitute a
# different, easier-to-compute hypothesis for the one actually asked.
#
# This is real, new statistical code: a two-sample Mann-Whitney U test
# (rows where the condition holds vs. rows where it doesn't) plus a
# block-permutation significance test. The block-permutation TECHNIQUE
# (chop into contiguous blocks, shuffle block order, recompute the
# statistic, compare to the observed value with a +1 correction) is the
# same one already used independently in permutation_test_engine.py,
# kalman_filter_engine.py-adjacent research, and feature_validator.py —
# not copy-pasted from any of them, but the same accepted, repeated
# pattern in this codebase, applied fresh to a different statistic
# (difference in mean forward return, not IC or trade P&L).
# ============================================================

from __future__ import annotations

import numpy as np
from scipy import stats as scipy_stats

from bot.research.feature_calculator import FeatureCalculator
from bot.research_lab.spec import SUPPORTED_HORIZONS
from bot.research_lab.tools._data import load_ohlcv
from bot.research_lab.tools.base import register_tool

MIN_CONDITION_SAMPLE = 30  # claude code changed: below this, a "difference in means" isn't a trustworthy result — same floor philosophy as feature_validator.py's own MIN_OBS-style guards
BLOCK_PERMUTATION_N = 100  # claude code changed: matches permutation_test_engine.py's own DEFAULT_N_PERMUTATIONS — smallest achievable p-value is 1/(N+1)
BLOCK_SIZE_CANDLES = 24  # claude code changed: one day of 1h candles — short enough to preserve statistical power, long enough to respect short-range autocorrelation, same reasoning as this codebase's other block-permutation tools

_OPERATORS = {
    "<": lambda series, threshold: series < threshold,
    "<=": lambda series, threshold: series <= threshold,
    ">": lambda series, threshold: series > threshold,
    ">=": lambda series, threshold: series >= threshold,
}


def _block_permutation_pvalue(condition_mask: np.ndarray, forward_returns: np.ndarray, observed_diff: float, seed: int) -> float:
    """
    Block-shuffles WHICH rows count as condition=True (preserving each
    block's internal contiguity, same as this codebase's other
    block-shuffle implementations), recomputes the true-vs-false mean
    difference on each shuffle, and returns the fraction of shuffles at
    least as extreme as the real, observed difference.
    """
    rng = np.random.default_rng(seed)
    n = len(condition_mask)
    n_blocks = int(np.ceil(n / BLOCK_SIZE_CANDLES))
    block_bounds = [(i * BLOCK_SIZE_CANDLES, min((i + 1) * BLOCK_SIZE_CANDLES, n)) for i in range(n_blocks)]

    at_least_as_extreme = 0
    valid_shuffles = 0
    for _ in range(BLOCK_PERMUTATION_N):
        order = rng.permutation(n_blocks)
        shuffled_positions = np.concatenate([np.arange(start, end) for start, end in (block_bounds[i] for i in order)])
        shuffled_mask = condition_mask[shuffled_positions]

        true_vals = forward_returns[shuffled_mask]
        false_vals = forward_returns[~shuffled_mask]
        if len(true_vals) == 0 or len(false_vals) == 0:
            continue  # claude code changed: a degenerate shuffle (all-true or all-false) contributes no information either way
        valid_shuffles += 1
        shuffled_diff = float(true_vals.mean() - false_vals.mean())
        if abs(shuffled_diff) >= abs(observed_diff):
            at_least_as_extreme += 1

    if valid_shuffles == 0:
        return float("nan")
    return (at_least_as_extreme + 1) / (valid_shuffles + 1)  # claude code changed: standard +1 correction, same as permutation_test_engine.py


@register_tool("run_conditional_test")
def run_conditional_test(asset: str, feature_name: str, operator: str, threshold: float, horizon: int, random_seed: int = 42) -> dict:
    """
    Tests: "when {feature_name} {operator} {threshold} holds, is the
    subsequent forward return over `horizon` candles significantly
    different from when it doesn't?" — a real event/conditional test, not
    a continuous IC test wearing a conditional label.
    """
    if operator not in _OPERATORS:
        raise ValueError(f"operator '{operator}' is not supported — must be one of {sorted(_OPERATORS)}")
    if horizon not in SUPPORTED_HORIZONS:
        raise ValueError(f"horizon={horizon} is not supported — only {sorted(SUPPORTED_HORIZONS)} candles are available as labels")

    df = load_ohlcv(asset)
    calc = FeatureCalculator(min_data_required=100)
    enriched = calc.calculate_all_features(df, symbol=asset)

    if feature_name not in enriched.columns:
        raise ValueError(f"'{feature_name}' is not a column feature_calculator.py produced")

    forward_col = SUPPORTED_HORIZONS[horizon]
    valid = enriched[[feature_name, forward_col]].dropna()
    if valid.empty:
        raise ValueError(f"no valid rows for {feature_name}/{forward_col} on {asset}")

    condition_mask = _OPERATORS[operator](valid[feature_name], threshold).to_numpy()
    forward_returns = valid[forward_col].to_numpy()

    n_true = int(condition_mask.sum())
    n_false = int((~condition_mask).sum())
    if n_true < MIN_CONDITION_SAMPLE or n_false < MIN_CONDITION_SAMPLE:
        raise ValueError(
            f"condition '{feature_name} {operator} {threshold}' produces too few observations "
            f"(true={n_true}, false={n_false}, minimum={MIN_CONDITION_SAMPLE} each) for a trustworthy comparison"
        )

    mean_true = float(forward_returns[condition_mask].mean())
    mean_false = float(forward_returns[~condition_mask].mean())
    observed_diff = mean_true - mean_false

    mannwhitney_stat, mannwhitney_p = scipy_stats.mannwhitneyu(
        forward_returns[condition_mask], forward_returns[~condition_mask], alternative="two-sided",
    )

    block_p = _block_permutation_pvalue(condition_mask, forward_returns, observed_diff, seed=random_seed)

    return {
        "asset": asset, "feature_name": feature_name, "operator": operator, "threshold": threshold, "horizon": horizon,
        "metric": "conditional_event_test",  # claude code changed: explicit metric label so downstream code (verdict.py, explain_evidence) never confuses this with "information_coefficient"
        "n_condition_true": n_true,
        "n_condition_false": n_false,
        "mean_return_when_true": mean_true,
        "mean_return_when_false": mean_false,
        "observed_diff": observed_diff,
        "mannwhitney_p_value": float(mannwhitney_p),
        "block_permutation_p_value": block_p,
        "sample_size": n_true + n_false,
        "random_seed": random_seed,
    }
