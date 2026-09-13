# ============================================================
# bot/tests/test_regime_conditional_pairs.py
# claude code changed: new file — Regime-Conditional Quantitative Research
# mission, Phase 4/5/19. Verifies: full-sample cointegration is reproduced
# unchanged, episodes shorter than MIN_CANDLES are marked TOO_SHORT (never
# silently tested or silently dropped), regime episodes never splice
# non-adjacent time windows, and Kalman regime-bucketing groups an
# already-computed trajectory post hoc without re-running the filter.
# ============================================================

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.cointegration_engine import MIN_CANDLES, CointegrationEngine
from bot.research.regime_conditional_pairs import (
    regime_conditional_cointegration,
    regime_conditional_kalman_hedge_ratio,
)


def _cointegrated_pair(n=2500, seed=5):
    """A real, planted cointegrated pair: B follows a random walk, A = B
    + a stationary mean-reverting spread — the standard synthetic
    construction for testing an Engle-Granger pipeline."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    b_returns = rng.normal(0, 0.01, n)
    log_b = np.cumsum(b_returns) + 5.0   # log-price around e^5 ~ 148
    spread = np.zeros(n)
    for t in range(1, n):
        spread[t] = 0.9 * spread[t - 1] + rng.normal(0, 0.02)   # AR(1), mean-reverting
    log_a = log_b + spread + 0.001 * np.arange(n) / n  # tiny drift, harmless
    price_a = pd.Series(np.exp(log_a), index=idx, name="price_a")
    price_b = pd.Series(np.exp(log_b), index=idx, name="price_b")
    return np.log(price_a), np.log(price_b)   # engine expects log prices (this session's own established convention)


class RegimeConditionalCointegrationTest(SimpleTestCase):
    def test_full_sample_result_matches_calling_test_pair_directly(self):
        log_a, log_b = _cointegrated_pair()
        engine = CointegrationEngine()
        labels = pd.Series(np.where(np.arange(len(log_a)) % 2 == 0, "TRENDING_UP", "RANGING"), index=log_a.index)

        direct = engine._test_pair("A", "B", log_a, log_b)
        report = regime_conditional_cointegration(engine, "A", "B", log_a, log_b, labels, min_episode_length=MIN_CANDLES)

        self.assertEqual(report.full_sample.is_cointegrated, direct.is_cointegrated)
        self.assertAlmostEqual(report.full_sample.adf_pvalue, direct.adf_pvalue, places=10)
        self.assertAlmostEqual(report.full_sample.hedge_ratio, direct.hedge_ratio, places=10)

    def test_episodes_shorter_than_min_length_are_marked_too_short_not_silently_dropped(self):
        log_a, log_b = _cointegrated_pair(n=2500)
        engine = CointegrationEngine()
        # alternating every 2 rows -> every episode has length 2, far below MIN_CANDLES
        labels = pd.Series(np.where(np.arange(len(log_a)) % 4 < 2, "TRENDING_UP", "RANGING"), index=log_a.index)
        report = regime_conditional_cointegration(engine, "A", "B", log_a, log_b, labels, min_episode_length=MIN_CANDLES)
        self.assertGreater(len(report.episodes), 0)
        self.assertTrue(all(e.status == "TOO_SHORT" for e in report.episodes))
        for regime, summary in report.by_regime_summary.items():
            self.assertEqual(summary["status"], "INSUFFICIENT_SAMPLE")

    def test_a_long_enough_contiguous_episode_produces_a_real_cointegration_result(self):
        log_a, log_b = _cointegrated_pair(n=2500)
        engine = CointegrationEngine()
        # ONE long TRENDING_UP episode covering the whole series, easily clearing MIN_CANDLES
        labels = pd.Series("TRENDING_UP", index=log_a.index)
        report = regime_conditional_cointegration(engine, "A", "B", log_a, log_b, labels, min_episode_length=MIN_CANDLES)
        self.assertEqual(len(report.episodes), 1)
        self.assertEqual(report.episodes[0].status, "OK")
        self.assertEqual(report.by_regime_summary["TRENDING_UP"]["status"], "OK")
        self.assertEqual(report.by_regime_summary["TRENDING_UP"]["n_qualifying_episodes"], 1)

    def test_episode_windows_never_cross_a_regime_boundary(self):
        log_a, log_b = _cointegrated_pair(n=3000)
        engine = CointegrationEngine()
        labels = pd.Series("TRENDING_UP", index=log_a.index)
        labels.iloc[1500:] = "RANGING"
        report = regime_conditional_cointegration(engine, "A", "B", log_a, log_b, labels, min_episode_length=500)
        for ep in report.episodes:
            window_labels = labels.loc[ep.start:ep.end]
            self.assertEqual(window_labels.nunique(), 1)
            self.assertEqual(window_labels.iloc[0], ep.regime)


class RegimeConditionalKalmanTest(SimpleTestCase):
    def _fake_kalman_output(self, n=1000, seed=1):
        rng = np.random.default_rng(seed)
        idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
        # beta drifts differently in two halves -> real, detectable cross-regime instability
        beta = np.concatenate([
            1.0 + rng.normal(0, 0.02, n // 2),
            1.5 + rng.normal(0, 0.02, n - n // 2),
        ])
        return pd.DataFrame({"kalman_beta_pred": beta}, index=idx)

    def test_groups_existing_trajectory_without_recomputation(self):
        kalman_output = self._fake_kalman_output()
        labels = pd.Series(np.where(np.arange(len(kalman_output)) < 500, "TRENDING_UP", "RANGING"), index=kalman_output.index)
        result = regime_conditional_kalman_hedge_ratio(kalman_output, labels, min_obs=30)
        self.assertIn("TRENDING_UP", result["by_regime"])
        self.assertIn("RANGING", result["by_regime"])
        self.assertAlmostEqual(result["by_regime"]["TRENDING_UP"]["beta_mean"], 1.0, delta=0.05)
        self.assertAlmostEqual(result["by_regime"]["RANGING"]["beta_mean"], 1.5, delta=0.05)

    def test_detects_real_cross_regime_instability(self):
        kalman_output = self._fake_kalman_output()
        labels = pd.Series(np.where(np.arange(len(kalman_output)) < 500, "TRENDING_UP", "RANGING"), index=kalman_output.index)
        result = regime_conditional_kalman_hedge_ratio(kalman_output, labels, min_obs=30)
        self.assertIsNotNone(result["cross_regime_instability_ratio"])
        self.assertGreater(result["cross_regime_instability_ratio"], 1.0)  # regime means differ far more than within-regime noise

    def test_insufficient_sample_regime_flagged_explicitly(self):
        kalman_output = self._fake_kalman_output(n=200)
        labels = pd.Series("RANGING", index=kalman_output.index)
        labels.iloc[:5] = "TRENDING_UP"   # tiny bucket
        result = regime_conditional_kalman_hedge_ratio(kalman_output, labels, min_obs=30)
        self.assertEqual(result["by_regime"]["TRENDING_UP"]["status"], "INSUFFICIENT_SAMPLE")
        self.assertFalse(result["by_regime"]["TRENDING_UP"]["sufficient_sample"])

    def test_raises_on_unknown_beta_column(self):
        kalman_output = self._fake_kalman_output()
        labels = pd.Series("RANGING", index=kalman_output.index)
        with self.assertRaises(ValueError):
            regime_conditional_kalman_hedge_ratio(kalman_output, labels, beta_col="does_not_exist")

    def test_rows_with_missing_regime_label_are_excluded(self):
        kalman_output = self._fake_kalman_output()
        labels = pd.Series("RANGING", index=kalman_output.index)
        labels.iloc[:100] = np.nan
        result = regime_conditional_kalman_hedge_ratio(kalman_output, labels, min_obs=30)
        self.assertEqual(result["by_regime"]["RANGING"]["n_obs"], len(kalman_output) - 100)
