# ============================================================
# bot/tests/test_regime_conditional_ic.py
# claude code changed: new file — Regime-Conditional Quantitative Research
# mission, Phase 7/8/9/10/19. Uses synthetic data with a KNOWN, PLANTED
# regime-dependent effect (feature predicts return only in one regime) so
# these tests assert the machinery detects a real signal, not just "runs
# without crashing."
# ============================================================

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.regime_conditional_ic import compute_regime_conditional_ic, test_regime_interaction


def _planted_regime_dependent_panel(n=2000, seed=11):
    """feature predicts forward_return strongly in TRENDING_UP, not at all
    (pure noise) in RANGING — the textbook case Phase 10's interaction
    test exists to catch."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    regime = np.where(np.arange(n) % 2 == 0, "TRENDING_UP", "RANGING")
    feature = rng.normal(0, 1, n)
    noise = rng.normal(0, 1, n)
    forward_return = np.where(regime == "TRENDING_UP", 0.05 * feature + 0.01 * noise, 0.01 * noise)
    df = pd.DataFrame({"feature": feature, "forward_return": forward_return, "trend_state": regime}, index=idx)
    return df


def _no_regime_dependence_panel(n=2000, seed=12):
    """feature predicts forward_return identically regardless of regime —
    the negative-control case: the interaction test must NOT fire here."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    regime = np.where(np.arange(n) % 2 == 0, "TRENDING_UP", "RANGING")
    feature = rng.normal(0, 1, n)
    noise = rng.normal(0, 1, n)
    forward_return = 0.03 * feature + 0.02 * noise
    df = pd.DataFrame({"feature": feature, "forward_return": forward_return, "trend_state": regime}, index=idx)
    return df


class RegimeConditionalICPlantedSignalTest(SimpleTestCase):
    def test_ic_is_much_stronger_in_the_regime_where_the_effect_was_planted(self):
        df = _planted_regime_dependent_panel()
        report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=30)
        trending = report.cell("trend_state", "TRENDING_UP")
        ranging = report.cell("trend_state", "RANGING")
        self.assertIsNotNone(trending)
        self.assertIsNotNone(ranging)
        self.assertGreater(abs(trending.ic), abs(ranging.ic) * 2)

    def test_overall_row_is_always_present(self):
        df = _planted_regime_dependent_panel()
        report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=30)
        overall = report.cell("OVERALL", "ALL")
        self.assertIsNotNone(overall)
        self.assertEqual(overall.n_obs, len(df))

    def test_fdr_correction_applied_across_the_whole_family_at_once(self):
        df = _planted_regime_dependent_panel()
        report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=30)
        # n_tested should be OVERALL + 2 regime cells = 3
        self.assertEqual(report.n_tested, 3)
        for cell in report.cells:
            if cell.ic_pvalue is not None:
                self.assertIsNotNone(cell.ic_pvalue_fdr)
                self.assertGreaterEqual(cell.ic_pvalue_fdr, cell.ic_pvalue - 1e-9)  # FDR-adjusted p-value is never smaller than raw

    def test_insufficient_sample_cell_is_excluded_from_fdr_family_not_marked_failed(self):
        df = _planted_regime_dependent_panel(n=200)
        report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=1000)
        for cell in report.cells:
            self.assertFalse(cell.sufficient_sample)
            self.assertIsNone(cell.ic)
            self.assertIsNone(cell.passes_fdr)
        self.assertEqual(report.n_tested, 0)

    def test_block_ic_stability_metrics_are_populated_for_sufficient_cells(self):
        df = _planted_regime_dependent_panel()
        report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state"], min_obs=30, n_blocks=10)
        trending = report.cell("trend_state", "TRENDING_UP")
        self.assertGreater(trending.n_blocks, 0)
        self.assertIsNotNone(trending.block_ic_mean)
        self.assertIsNotNone(trending.positive_ic_fraction)

    def test_multiple_dimensions_all_appear_in_one_combined_family(self):
        df = _planted_regime_dependent_panel()
        df = df.copy()
        df["volatility_state"] = np.where(np.arange(len(df)) % 3 == 0, "HIGH_VOLATILITY", "NORMAL_VOLATILITY")
        report = compute_regime_conditional_ic(df, "feature", "forward_return", ["trend_state", "volatility_state"], min_obs=30)
        dims_seen = {c.dimension for c in report.cells}
        self.assertEqual(dims_seen, {"OVERALL", "trend_state", "volatility_state"})

    def test_raises_on_missing_column(self):
        df = _planted_regime_dependent_panel()
        with self.assertRaises(ValueError):
            compute_regime_conditional_ic(df, "nonexistent_feature", "forward_return", ["trend_state"])


class RegimeInteractionTest(SimpleTestCase):
    def test_detects_a_real_planted_interaction(self):
        df = _planted_regime_dependent_panel()
        result = test_regime_interaction(df, "feature", "forward_return", "trend_state")
        self.assertEqual(result["status"], "OK")
        self.assertTrue(result["interaction_significant"])
        self.assertLess(result["p_value"], 0.01)

    def test_does_not_fire_a_false_positive_when_relationship_is_regime_independent(self):
        df = _no_regime_dependence_panel()
        result = test_regime_interaction(df, "feature", "forward_return", "trend_state")
        self.assertEqual(result["status"], "OK")
        self.assertFalse(result["interaction_significant"])

    def test_insufficient_sample_reported_explicitly_not_a_crash(self):
        df = _planted_regime_dependent_panel(n=10)
        result = test_regime_interaction(df, "feature", "forward_return", "trend_state")
        self.assertEqual(result["status"], "INSUFFICIENT_SAMPLE")

    def test_single_regime_present_is_insufficient_not_a_crash(self):
        df = _planted_regime_dependent_panel()
        df = df[df["trend_state"] == "TRENDING_UP"].copy()
        result = test_regime_interaction(df, "feature", "forward_return", "trend_state")
        self.assertEqual(result["status"], "INSUFFICIENT_SAMPLE")
