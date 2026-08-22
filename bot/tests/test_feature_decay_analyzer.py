# claude code changed: new file — Research Agent architecture Phase 1
# (published blueprint, §04): feature_decay_analyzer.py had zero automated
# tests despite v2.0 already containing five documented peer-review fixes
# (synthetic Sharpe removed from grading, fixed IC bar scale, regression-
# based trend detection). These tests prove those fixes actually hold —
# real behavioral coverage of _grade(), _determine_trend()'s six trend
# categories, and a full analyze() pipeline run with a planted decaying
# correlation — not just shape/type checks.

import shutil
import tempfile

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.feature_decay_analyzer import (  # claude code changed: module under test
    FeatureDecayAnalyzer, IC_STRONG, IC_MODERATE, IC_WEAK,
)


class GradeThresholdTest(SimpleTestCase):
    # claude code changed: exact boundary values from _grade()'s own
    # documented thresholds — deterministic, no simulation needed.

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep test output out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp output dir

    def test_grade_thresholds_match_documented_boundaries(self):
        analyzer = FeatureDecayAnalyzer(output_dir=self.tmp_dir)  # claude code changed: module under test

        self.assertEqual(analyzer._grade(0.07, 0.01), "EXCELLENT")  # claude code changed: >=0.07
        self.assertEqual(analyzer._grade(0.05, 0.01), "STRONG")  # claude code changed: >=0.05
        self.assertEqual(analyzer._grade(0.03, 0.01), "GOOD")  # claude code changed: >=0.03
        self.assertEqual(analyzer._grade(0.02, 0.01), "MODERATE")  # claude code changed: >=0.02
        self.assertEqual(analyzer._grade(0.01, 0.01), "WEAK")  # claude code changed: >=0.01
        self.assertEqual(analyzer._grade(0.005, 0.01), "DEAD")  # claude code changed: below 0.01

    def test_grade_ignores_sharpe_entirely(self):
        """FIX 1/2 regression test: _grade() must produce the identical
        result no matter what sharpe value is passed — Sharpe was removed
        from grading per the module's own v2.0 changelog. A future
        regression that re-introduces Sharpe into the grade computation
        would break this."""
        analyzer = FeatureDecayAnalyzer(output_dir=self.tmp_dir)  # claude code changed: module under test

        grade_no_sharpe = analyzer._grade(0.06, 0.01, sharpe=np.nan)
        grade_huge_sharpe = analyzer._grade(0.06, 0.01, sharpe=99.0)  # claude code changed: implausible Sharpe must not change the grade
        grade_negative_sharpe = analyzer._grade(0.06, 0.01, sharpe=-99.0)  # claude code changed: same, opposite sign

        self.assertEqual(grade_no_sharpe, grade_huge_sharpe)  # claude code changed: Sharpe value must not affect grade
        self.assertEqual(grade_no_sharpe, grade_negative_sharpe)  # claude code changed: Sharpe value must not affect grade

    def test_grade_not_significant_overrides_ic_strength(self):
        analyzer = FeatureDecayAnalyzer(output_dir=self.tmp_dir, alpha=0.05)  # claude code changed: module under test

        # claude code changed: IC is EXCELLENT-strength but p-value fails alpha -> NOT_SIG must win
        self.assertEqual(analyzer._grade(0.09, 0.20), "NOT_SIG")

    def test_grade_nan_ic_returns_na(self):
        analyzer = FeatureDecayAnalyzer(output_dir=self.tmp_dir)  # claude code changed: module under test
        self.assertEqual(analyzer._grade(np.nan, 0.01), "N/A")  # claude code changed: guard clause


class GradeIcOnlyTest(SimpleTestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep test output out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp output dir

    def test_grade_ic_only_thresholds(self):
        analyzer = FeatureDecayAnalyzer(output_dir=self.tmp_dir)  # claude code changed: module under test
        self.assertEqual(analyzer._grade_ic_only(IC_STRONG + 0.001), "STRONG")  # claude code changed: boundary+epsilon
        self.assertEqual(analyzer._grade_ic_only(IC_MODERATE + 0.001), "MODERATE")  # claude code changed: boundary+epsilon
        self.assertEqual(analyzer._grade_ic_only(IC_WEAK + 0.001), "WEAK")  # claude code changed: boundary+epsilon
        self.assertEqual(analyzer._grade_ic_only(IC_WEAK - 0.001), "DEAD")  # claude code changed: below weak threshold


class DetermineTrendTest(SimpleTestCase):
    """FIX 5 regression coverage: trend classification now uses OLS
    regression slope over all yearly |IC| points, not an early-vs-recent
    mean comparison. Each case below is handpicked, exact IC-per-year data
    (not simulated), so the classification is deterministic."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep test output out of research_data/
        self.analyzer = FeatureDecayAnalyzer(output_dir=self.tmp_dir)  # claude code changed: module under test

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp output dir

    def _feat_df(self, ic_by_year):
        return pd.DataFrame({"year": list(ic_by_year.keys()), "ic": list(ic_by_year.values())})  # claude code changed: minimal shape _determine_trend needs

    def test_dead_when_recent_ic_below_weak_threshold(self):
        feat_df = self._feat_df({"2020": 0.08, "2021": 0.07, "2024": 0.005, "2025": 0.004})  # claude code changed: strong early, near-zero recent
        self.assertEqual(self.analyzer._determine_trend(feat_df), "DEAD")  # claude code changed: DEAD checked before DECAYING, wins regardless of early strength

    def test_decaying_when_slope_negative_and_early_ic_meaningful(self):
        feat_df = self._feat_df({  # claude code changed: monotonically declining but still above IC_WEAK in recent years
            "2020": 0.09, "2021": 0.08, "2022": 0.06, "2023": 0.04,
            "2024": 0.02, "2025": 0.015, "2026": 0.012,
        })
        self.assertEqual(self.analyzer._determine_trend(feat_df), "DECAYING")

    def test_gaining_when_slope_positive_and_recent_ic_meaningful(self):
        feat_df = self._feat_df({  # claude code changed: mirror image of the decaying case
            "2020": 0.012, "2021": 0.015, "2022": 0.02, "2023": 0.04,
            "2024": 0.06, "2025": 0.08, "2026": 0.09,
        })
        self.assertEqual(self.analyzer._determine_trend(feat_df), "GAINING")

    def test_emerging_when_early_near_zero_and_recent_strong(self):
        feat_df = self._feat_df({  # claude code changed: early below IC_WEAK, recent above IC_MODERATE
            "2020": 0.005, "2021": 0.004, "2022": 0.01, "2023": 0.02,
            "2024": 0.05, "2025": 0.07, "2026": 0.08,
        })
        self.assertEqual(self.analyzer._determine_trend(feat_df), "EMERGING")

    def test_stable_when_ic_roughly_constant(self):
        feat_df = self._feat_df({  # claude code changed: small noise around a constant mean, near-zero slope
            "2020": 0.04, "2021": 0.038, "2022": 0.041,
            "2023": 0.039, "2024": 0.04, "2025": 0.042, "2026": 0.038,
        })
        self.assertEqual(self.analyzer._determine_trend(feat_df), "STABLE")

    def test_insufficient_data_with_fewer_than_two_points(self):
        feat_df = self._feat_df({"2024": 0.05})  # claude code changed: single data point, regression impossible
        self.assertEqual(self.analyzer._determine_trend(feat_df), "INSUFFICIENT_DATA")


class AnalyzePipelineTest(SimpleTestCase):
    """End-to-end analyze() run with a planted decaying correlation —
    strong signal in 2020, dead noise-only in 2024 for the same feature
    column — confirms decay_data grading and regime_breakdown both work
    through the full pipeline, not just the private helpers."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep test output out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp output dir

    def _planted_decaying_observations(self, seed=5):
        rng = np.random.default_rng(seed)  # claude code changed: reproducible synthetic data

        idx_2020 = pd.date_range("2020-02-01", periods=400, freq="1h", tz="UTC")  # claude code changed: clears MIN_OBS=300, inside 2020 window
        fwd_2020 = rng.normal(0, 0.01, 400)
        strong_signal_2020 = fwd_2020 * 10 + rng.normal(0, 0.0005, 400)  # claude code changed: near-perfect correlation, 2020

        idx_2024 = pd.date_range("2024-08-01", periods=400, freq="1h", tz="UTC")  # claude code changed: clears MIN_OBS=300, inside post_etf regime window too
        fwd_2024 = rng.normal(0, 0.01, 400)
        strong_signal_2024 = rng.normal(0, 1, 400)  # claude code changed: same column name, now pure noise — signal has "decayed"

        df = pd.DataFrame({
            "timestamp": list(idx_2020) + list(idx_2024),
            "forward_return_4h": np.concatenate([fwd_2020, fwd_2024]),
            "strong_signal": np.concatenate([strong_signal_2020, strong_signal_2024]),
        })
        return df

    def test_decaying_feature_graded_strong_early_and_weak_recent(self):
        df = self._planted_decaying_observations()
        analyzer = FeatureDecayAnalyzer(output_dir=self.tmp_dir, min_obs=300)  # claude code changed: module under test

        result = analyzer.analyze(df, "TEST_USDT")
        decay_data = result["decay_data"]

        row_2020 = decay_data[(decay_data["feature"] == "strong_signal") & (decay_data["year"] == "2020")].iloc[0]
        row_2024 = decay_data[(decay_data["feature"] == "strong_signal") & (decay_data["year"] == "2024")].iloc[0]

        self.assertGreater(abs(row_2020["ic"]), 0.5)  # claude code changed: engineered near-perfect correlation in 2020
        self.assertIn(row_2020["grade"], ("STRONG", "EXCELLENT"))  # claude code changed: strong early signal must grade highly
        self.assertLess(abs(row_2024["ic"]), 0.15)  # claude code changed: pure noise in 2024, IC must not look strong
        self.assertNotIn(row_2024["grade"], ("STRONG", "EXCELLENT"))  # claude code changed: decayed signal must not still grade as strong

    def test_sharpe_column_always_nan_through_full_pipeline(self):
        """FIX 1/2 regression test at the pipeline level, not just _grade()
        in isolation — confirms no code path re-derives a synthetic Sharpe
        anywhere between analyze() and the saved decay_data."""
        df = self._planted_decaying_observations()
        analyzer = FeatureDecayAnalyzer(output_dir=self.tmp_dir, min_obs=300)  # claude code changed: module under test

        result = analyzer.analyze(df, "TEST_USDT")
        decay_data = result["decay_data"]

        self.assertTrue(decay_data["sharpe"].isna().all())  # claude code changed: Sharpe must never be populated, per FIX 1/2

    def test_regime_breakdown_populated_for_covered_window(self):
        df = self._planted_decaying_observations()
        analyzer = FeatureDecayAnalyzer(output_dir=self.tmp_dir, min_obs=300)  # claude code changed: module under test

        result = analyzer.analyze(df, "TEST_USDT")
        regime_data = result["regime_breakdown"]

        # claude code changed: 2024-08-01 + 400 hourly rows falls inside the
        # "post_etf" regime window (2024-07-01..2024-12-31) — confirms the
        # regime-specific breakdown actually ran, not just the yearly one.
        self.assertIn("post_etf", set(regime_data["regime"]))


class GuardClauseTest(SimpleTestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep test output out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp output dir

    def test_missing_forward_column_returns_empty_dict(self):
        idx = pd.date_range("2024-01-01", periods=400, freq="1h", tz="UTC")  # claude code changed: enough rows, wrong forward column name
        df = pd.DataFrame({"timestamp": idx, "some_feature": np.arange(400, dtype=float)})
        analyzer = FeatureDecayAnalyzer(output_dir=self.tmp_dir, forward_col="forward_return_4h")  # claude code changed: module under test

        result = analyzer.analyze(df, "TEST_USDT")

        self.assertEqual(result, {})  # claude code changed: fail-closed, not a crash or partial result

    def test_unparseable_timestamp_returns_empty_dict(self):
        df = pd.DataFrame({  # claude code changed: no timestamp/index/Unnamed: 0 column at all
            "some_feature": np.arange(400, dtype=float),
            "forward_return_4h": np.arange(400, dtype=float),
        })
        analyzer = FeatureDecayAnalyzer(output_dir=self.tmp_dir)  # claude code changed: module under test

        result = analyzer.analyze(df, "TEST_USDT")

        self.assertEqual(result, {})  # claude code changed: fail-closed on unparseable timestamp
