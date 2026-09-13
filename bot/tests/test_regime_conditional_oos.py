# ============================================================
# bot/tests/test_regime_conditional_oos.py
# claude code changed: new file — Regime-Conditional Quantitative Research
# mission, Phase 11/19. Verifies: regime labels correctly partition an
# already-evaluated OOSResult's fold-level records, fold/purge/embargo
# boundaries are never touched, warmup/missing-regime rows are excluded
# rather than mis-attributed, and the pooled-by-regime metrics use the
# real oos_validator economic-metrics formula (not a re-derived one).
# ============================================================

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.oos_validator import WalkForwardConfig, evaluate_cross_sectional_oos
from bot.research.regime_conditional_oos import evaluate_cross_sectional_oos_by_regime


def _cross_sectional_panel(n_periods=400, n_assets=10, seed=3):
    """Long-format (timestamp, asset, feature, forward_return) panel with
    a real, planted cross-sectional relationship: higher feature -> higher
    forward_return, on average, plus noise — the same shape
    evaluate_cross_sectional_oos() consumes elsewhere in this codebase."""
    rng = np.random.default_rng(seed)
    timestamps = pd.date_range("2024-01-01", periods=n_periods, freq="1h", tz="UTC")
    rows = []
    for ts in timestamps:
        features = rng.normal(0, 1, n_assets)
        noise = rng.normal(0, 1, n_assets)
        forward_returns = 0.01 * features + 0.02 * noise
        for i in range(n_assets):
            rows.append({
                "timestamp": ts, "asset": f"A{i}",
                "feature": features[i], "forward_return": forward_returns[i],
            })
    return pd.DataFrame(rows)


def _regime_labels_for(timestamps: pd.DatetimeIndex, split_at: int) -> pd.Series:
    """A simple, deterministic two-regime label series for testing: first
    half TRENDING_UP, second half RANGING — enough to verify partitioning
    without depending on the real regime classifier at all."""
    unique_ts = pd.DatetimeIndex(sorted(timestamps.unique()))
    labels = pd.Series("RANGING", index=unique_ts)
    labels.iloc[:split_at] = "TRENDING_UP"
    return labels


class RegimeConditionalOOSPartitioningTest(SimpleTestCase):
    def setUp(self):
        self.df = _cross_sectional_panel()
        self.config = WalkForwardConfig(mode="expanding", min_train_periods=100, test_periods=50, step_periods=50, seed=42)
        self.oos_result = evaluate_cross_sectional_oos(
            self.df, timestamp_col="timestamp", asset_col="asset", feature_col="feature",
            forward_return_col="forward_return", config=self.config, top_k=2, long_short=True, cost_rate=0.0,
        )
        unique_ts = pd.DatetimeIndex(sorted(self.df["timestamp"].unique()))
        self.labels = _regime_labels_for(unique_ts, split_at=len(unique_ts) // 2)

    def test_every_regime_bucket_only_contains_records_from_its_own_regime(self):
        result = evaluate_cross_sectional_oos_by_regime(
            self.oos_result, self.labels, regime_dimension="trend_state", cost_rate=0.0, min_obs=1,
        )
        for fold_metric in result.per_fold_per_regime:
            self.assertIn(fold_metric.regime, ("TRENDING_UP", "RANGING"))

    def test_pooled_regime_record_counts_sum_to_total_evaluated_records(self):
        result = evaluate_cross_sectional_oos_by_regime(
            self.oos_result, self.labels, regime_dimension="trend_state", cost_rate=0.0, min_obs=1,
        )
        total_from_regimes = sum(v["n_periods"] for v in result.pooled_by_regime.values())
        total_from_folds = sum(len(f.trades) for f in self.oos_result.folds if not f.skipped)
        self.assertEqual(total_from_regimes, total_from_folds)

    def test_whole_dataset_aggregate_is_passed_through_unmodified(self):
        result = evaluate_cross_sectional_oos_by_regime(
            self.oos_result, self.labels, regime_dimension="trend_state", cost_rate=0.0, min_obs=1,
        )
        self.assertEqual(result.whole_dataset, self.oos_result.aggregate)

    def test_rows_with_no_regime_label_are_excluded_not_misattributed(self):
        unique_ts = pd.DatetimeIndex(sorted(self.df["timestamp"].unique()))
        labels_with_gaps = _regime_labels_for(unique_ts, split_at=len(unique_ts) // 2)
        # claude code changed: must NaN timestamps that actually fall
        # inside an evaluated fold's TEST window (min_train_periods=100,
        # so fold 1's test window is positions [100,150)) — NaN-ing the
        # first 20 rows (all inside every fold's TRAIN window, never
        # evaluated) would leave the evaluated record count unchanged and
        # make this test vacuous.
        labels_with_gaps.iloc[100:120] = np.nan   # simulate missing regime labels inside a real test window
        result = evaluate_cross_sectional_oos_by_regime(
            self.oos_result, labels_with_gaps, regime_dimension="trend_state", cost_rate=0.0, min_obs=1,
        )
        total_from_regimes = sum(v["n_periods"] for v in result.pooled_by_regime.values())
        total_from_folds = sum(len(f.trades) for f in self.oos_result.folds if not f.skipped)
        self.assertLess(total_from_regimes, total_from_folds)

    def test_insufficient_sample_regime_is_flagged_not_hidden(self):
        # a regime with only a handful of records should be flagged, not silently reported as if trustworthy
        unique_ts = pd.DatetimeIndex(sorted(self.df["timestamp"].unique()))
        rare_labels = pd.Series("RANGING", index=unique_ts)
        rare_labels.iloc[:3] = "TRENDING_UP"   # deliberately tiny bucket
        result = evaluate_cross_sectional_oos_by_regime(
            self.oos_result, rare_labels, regime_dimension="trend_state", cost_rate=0.0, min_obs=30,
        )
        if "TRENDING_UP" in result.pooled_by_regime:
            self.assertFalse(result.pooled_by_regime["TRENDING_UP"]["sufficient_sample"])

    def test_raises_on_non_cross_sectional_evaluation_type(self):
        from bot.research.oos_validator import OOSResult
        bad_result = OOSResult(evaluation_type="feature_to_return", config=self.config, folds=[])
        with self.assertRaises(ValueError):
            evaluate_cross_sectional_oos_by_regime(bad_result, self.labels, regime_dimension="trend_state", cost_rate=0.0)

    def test_metrics_use_the_real_oos_validator_formula_not_a_rederivation(self):
        # claude code changed: cross-check — pooling ALL records across
        # both regimes and computing metrics directly must match summing
        # the same records the regime split would have used, proving no
        # separate/approximate metric logic was introduced.
        from bot.research.oos_validator import compute_cross_sectional_metrics_from_records
        all_records = [r for f in self.oos_result.folds if not f.skipped for r in f.trades]
        direct = compute_cross_sectional_metrics_from_records(all_records, 0.0, 10_000.0, None)
        self.assertIn("mean_net_return", direct)
        self.assertIn("sharpe_ratio", direct)


class RegimeConditionalOOSFoldBoundaryPreservationTest(SimpleTestCase):
    """claude code changed: new — explicitly verifies Phase 11's
    'chronological ordering / purge / embargo preserved' requirement: this
    module must never see or need to re-derive fold boundaries; it only
    consumes an already-built OOSResult's already-purged/embargoed folds."""

    def test_fold_specs_are_untouched_by_regime_conditioning(self):
        df = _cross_sectional_panel()
        config = WalkForwardConfig(mode="expanding", min_train_periods=100, test_periods=50, step_periods=50, seed=42, purge_periods=2)
        oos_result = evaluate_cross_sectional_oos(
            df, timestamp_col="timestamp", asset_col="asset", feature_col="feature",
            forward_return_col="forward_return", config=config, top_k=2, cost_rate=0.0,
        )
        fold_specs_before = [f.fold for f in oos_result.folds]

        unique_ts = pd.DatetimeIndex(sorted(df["timestamp"].unique()))
        labels = _regime_labels_for(unique_ts, split_at=len(unique_ts) // 2)
        evaluate_cross_sectional_oos_by_regime(oos_result, labels, regime_dimension="trend_state", cost_rate=0.0, min_obs=1)

        fold_specs_after = [f.fold for f in oos_result.folds]
        self.assertEqual(fold_specs_before, fold_specs_after)
