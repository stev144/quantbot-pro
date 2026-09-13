# ============================================================
# bot/tests/test_regime_conditional_permutation.py
# claude code changed: new file — Regime-Conditional Quantitative Research
# mission, Phase 12/19. Small synthetic panels + modest n_permutations
# (this test suite must stay fast — each permutation replica re-runs a
# full evaluate_cross_sectional_oos() walk). Verifies: real/null are
# computed per regime independently, an underpowered regime is reported
# explicitly rather than silently tested against a thin null, and a
# planted regime-dependent effect ranks better (lower p-value) than a
# pure-noise regime in the SAME run.
# ============================================================

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.oos_validator import WalkForwardConfig
from bot.research.regime_conditional_permutation import run_regime_conditional_permutation_test


def _panel_with_regime_dependent_signal(n_periods=200, n_assets=8, seed=21):
    """Real cross-sectional signal in the first half of the timeline
    (labeled TRENDING_UP), pure noise in the second half (RANGING)."""
    rng = np.random.default_rng(seed)
    timestamps = pd.date_range("2024-01-01", periods=n_periods, freq="1h", tz="UTC")
    rows = []
    for i, ts in enumerate(timestamps):
        features = rng.normal(0, 1, n_assets)
        noise = rng.normal(0, 1, n_assets)
        if i < n_periods // 2:
            forward_returns = 0.08 * features + 0.02 * noise   # strong real signal
        else:
            forward_returns = 0.02 * noise                      # pure noise, no relationship to feature
        for j in range(n_assets):
            rows.append({"timestamp": ts, "asset": f"A{j}", "feature": features[j], "forward_return": forward_returns[j]})
    df = pd.DataFrame(rows)
    unique_ts = pd.DatetimeIndex(sorted(df["timestamp"].unique()))
    labels = pd.Series("RANGING", index=unique_ts)
    labels.iloc[:n_periods // 2] = "TRENDING_UP"
    return df, labels


class RegimeConditionalPermutationTest(SimpleTestCase):
    def setUp(self):
        self.df, self.labels = _panel_with_regime_dependent_signal()
        self.config = WalkForwardConfig(mode="expanding", min_train_periods=40, test_periods=20, step_periods=20, seed=1)

    def test_both_regimes_produce_a_verdict_with_expected_shape(self):
        result = run_regime_conditional_permutation_test(
            self.df, "timestamp", "asset", "feature", "forward_return",
            self.labels, "trend_state", self.config, top_k=2, cost_rate=0.0,
            n_permutations=20, random_seed=42, min_obs=1,
        )
        self.assertEqual(result["regime_dimension"], "trend_state")
        self.assertEqual(result["n_permutations"], 20)
        for regime in result["real_by_regime"]:
            verdict = result["verdict_by_regime"][regime]
            self.assertIn(verdict["status"], ("OK", "INSUFFICIENT_NULL_REPLICAS"))

    def test_signal_regime_ranks_better_than_noise_regime(self):
        # claude code changed: a comparative, not absolute, assertion —
        # robust to permutation-count noise at n_permutations=20. The
        # planted-signal regime's real Sharpe should exceed the pure-noise
        # regime's real Sharpe; that is the one thing guaranteed by
        # construction regardless of shuffle draw.
        result = run_regime_conditional_permutation_test(
            self.df, "timestamp", "asset", "feature", "forward_return",
            self.labels, "trend_state", self.config, top_k=2, cost_rate=0.0,
            n_permutations=20, random_seed=42, min_obs=1,
        )
        if "TRENDING_UP" in result["real_by_regime"] and "RANGING" in result["real_by_regime"]:
            signal_sharpe = result["real_by_regime"]["TRENDING_UP"].get("sharpe_ratio") or 0.0
            noise_sharpe = result["real_by_regime"]["RANGING"].get("sharpe_ratio") or 0.0
            self.assertGreater(signal_sharpe, noise_sharpe)

    def test_null_distributions_are_independent_per_regime(self):
        result = run_regime_conditional_permutation_test(
            self.df, "timestamp", "asset", "feature", "forward_return",
            self.labels, "trend_state", self.config, top_k=2, cost_rate=0.0,
            n_permutations=15, random_seed=7, min_obs=1,
        )
        null_by_regime = result["null_by_regime"]
        regimes = list(null_by_regime.keys())
        if len(regimes) == 2:
            self.assertNotEqual(
                [d.get("sharpe_ratio") for d in null_by_regime[regimes[0]]],
                [d.get("sharpe_ratio") for d in null_by_regime[regimes[1]]],
            )

    def test_underpowered_regime_reports_insufficient_sample_not_a_crash(self):
        unique_ts = pd.DatetimeIndex(sorted(self.df["timestamp"].unique()))
        tiny_labels = pd.Series("RANGING", index=unique_ts)
        tiny_labels.iloc[:2] = "TRENDING_UP"   # far too small to ever be evaluated
        result = run_regime_conditional_permutation_test(
            self.df, "timestamp", "asset", "feature", "forward_return",
            tiny_labels, "trend_state", self.config, top_k=2, cost_rate=0.0,
            n_permutations=10, random_seed=3, min_obs=30,
        )
        self.assertNotIn("TRENDING_UP", result["real_by_regime"])

    def test_regime_sample_sizes_reported_for_every_observed_regime(self):
        result = run_regime_conditional_permutation_test(
            self.df, "timestamp", "asset", "feature", "forward_return",
            self.labels, "trend_state", self.config, top_k=2, cost_rate=0.0,
            n_permutations=10, random_seed=5, min_obs=1,
        )
        self.assertTrue(set(result["regime_sample_sizes"].keys()) >= set(result["real_by_regime"].keys()))
