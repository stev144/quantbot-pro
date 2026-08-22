# claude code changed: new file — Research Agent architecture Phase 1
# (published blueprint, §04): feature_stability_analyzer.py had zero
# automated tests. These plant a known correlation pattern (a feature
# built to near-perfectly track forward returns, vs. pure noise) and
# assert the analyzer's IC/grading/verdict machinery actually detects
# the difference — real behavioral coverage, not shape/type checks.

import shutil
import tempfile

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.feature_stability_analyzer import (  # claude code changed: module under test
    FeatureStabilityAnalyzer, IC_MODERATE, IC_WEAK,
)


def _planted_observations(n=600, seed=7):
    """
    n hourly rows entirely inside 2024 (clears MIN_OBS=500 for a single
    calendar-year window). One feature is engineered to correlate almost
    perfectly with forward_return_4h; one is pure independent noise.
    """
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")  # claude code changed: single-year window, clears MIN_OBS
    rng = np.random.default_rng(seed)
    forward_return = rng.normal(0, 0.01, n)
    strong_signal = forward_return * 10 + rng.normal(0, 0.0005, n)  # claude code changed: near-perfect positive correlation by construction
    dead_noise = rng.normal(0, 1, n)  # claude code changed: independent of forward_return by construction

    return pd.DataFrame({
        "timestamp": idx,
        "forward_return_4h": forward_return,
        "strong_signal": strong_signal,
        "dead_noise": dead_noise,
    })


class PlantedCorrelationDetectionTest(SimpleTestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep test output out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp CSV output

    def test_strong_signal_graded_far_above_dead_noise(self):
        df = _planted_observations()
        analyzer = FeatureStabilityAnalyzer(output_dir=self.tmp_dir, min_obs=500)  # claude code changed: module under test

        result = analyzer.analyze(df, "TEST_USDT")
        yearly = result["yearly"]

        strong_row = yearly[(yearly["feature"] == "strong_signal") & (yearly["window"] == "2024")].iloc[0]
        noise_row = yearly[(yearly["feature"] == "dead_noise") & (yearly["window"] == "2024")].iloc[0]

        self.assertGreater(abs(strong_row["ic"]), 0.5)  # claude code changed: engineered near-perfect correlation must show up as high IC
        self.assertTrue(strong_row["ic_significant"])  # claude code changed: n=600, this large an IC must clear alpha=0.05
        self.assertIn(strong_row["verdict"], ("STRONG_KEEP", "KEEP"))  # claude code changed: real signal must not be discarded
        self.assertEqual(strong_row["ic_grade"], "EXCELLENT")  # claude code changed: |ic|>0.5 clears IC_STRONG=0.05 by a wide margin

        self.assertLess(abs(noise_row["ic"]), 0.15)  # claude code changed: independent-by-construction noise must not show a strong IC
        self.assertNotEqual(noise_row["verdict"], "STRONG_KEEP")  # claude code changed: noise must not be graded as a keeper

    def test_grading_thresholds_are_monotonic_in_ic_strength(self):
        """Sanity check on the grading thresholds themselves, independent
        of the analyzer pipeline: a stronger IC must never grade lower
        than a weaker one at the same significance level."""
        analyzer = FeatureStabilityAnalyzer(output_dir=self.tmp_dir, min_obs=500)  # claude code changed: module under test
        order = ["N/A", "DEAD", "WEAK", "GOOD", "EXCELLENT"]  # claude code changed: expected ascending strength order

        weak = analyzer._grade_ic(IC_WEAK + 0.001)
        moderate = analyzer._grade_ic(IC_MODERATE + 0.001)
        self.assertLess(order.index(weak), order.index(moderate))  # claude code changed: weak-tier IC must not outrank moderate-tier IC

    def test_ic_significant_requires_pvalue_below_alpha(self):
        df = _planted_observations()
        analyzer = FeatureStabilityAnalyzer(output_dir=self.tmp_dir, min_obs=500, alpha=0.05)  # claude code changed: module under test

        result = analyzer.analyze(df, "TEST_USDT")
        yearly = result["yearly"]
        noise_row = yearly[(yearly["feature"] == "dead_noise") & (yearly["window"] == "2024")].iloc[0]

        self.assertEqual(noise_row["ic_significant"], bool(noise_row["ic_pvalue"] < 0.05))  # claude code changed: flag must exactly track the stated alpha, not an approximation


class GuardClauseTest(SimpleTestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep test output out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp CSV output

    def test_missing_forward_column_returns_empty_dict(self):
        idx = pd.date_range("2024-01-01", periods=600, freq="1h", tz="UTC")  # claude code changed: enough rows, wrong forward column name
        df = pd.DataFrame({"timestamp": idx, "some_feature": np.arange(600, dtype=float)})
        analyzer = FeatureStabilityAnalyzer(output_dir=self.tmp_dir, forward_col="forward_return_4h")  # claude code changed: module under test

        result = analyzer.analyze(df, "TEST_USDT")

        self.assertEqual(result, {})  # claude code changed: fail-closed, not a crash or a silent partial result

    def test_unparseable_timestamp_returns_empty_dict(self):
        df = pd.DataFrame({"some_feature": np.arange(600, dtype=float), "forward_return_4h": np.arange(600, dtype=float)})  # claude code changed: no timestamp/index/Unnamed: 0 column at all
        analyzer = FeatureStabilityAnalyzer(output_dir=self.tmp_dir)  # claude code changed: module under test

        result = analyzer.analyze(df, "TEST_USDT")

        self.assertEqual(result, {})  # claude code changed: fail-closed on unparseable timestamp
