# claude code changed: new file — 2026-10-21, known-answer tests for the PT-OG-DCA-1 calibration test (G1/G2/G3).
import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research import pt_og_dca_calibration as cal
from bot.research.fx_holdout_guard import FX_RESEARCH_DATA_CUTOFF


class BlockSignFlipTests(SimpleTestCase):
    def test_min_blocks_reaches_holm_first_threshold(self):
        k = cal.min_blocks_for(0.05, 5)
        self.assertEqual(k, 8)
        self.assertLessEqual(2.0 ** (1 - k), 0.05 / 5)
        self.assertGreater(2.0 ** (1 - (k - 1)), 0.05 / 5)

    def test_block_sums_keep_partial_tail(self):
        np.testing.assert_allclose(cal.block_sums(np.arange(10.0), 4), [6.0, 22.0, 17.0])

    def test_too_few_blocks_is_untestable(self):
        r = cal.block_sign_flip_test(np.ones(70), 10, seed=1)
        self.assertEqual(r["status"], "UNTESTABLE")
        self.assertIsNone(r["p_value"])

    def test_non_finite_is_untestable(self):
        self.assertEqual(cal.block_sign_flip_test(np.array([1.0, np.nan] * 100), 10, seed=1)["status"], "UNTESTABLE")

    def test_strong_drift_hits_the_floor_and_sign(self):
        rng = np.random.default_rng(0)
        r = cal.block_sign_flip_test(rng.standard_normal(3000) + 1.0, 288, seed=3)
        self.assertEqual(r["status"], "OK")
        self.assertAlmostEqual(r["p_value"], 1.0 / 1000)
        self.assertTrue(r["positive"])
        neg = cal.block_sign_flip_test(-(rng.standard_normal(3000) + 1.0), 288, seed=3)
        self.assertAlmostEqual(neg["p_value"], 1.0 / 1000)
        self.assertFalse(neg["positive"])

    def test_reproducible_and_bounded(self):
        d = np.random.default_rng(5).standard_normal(3000)
        a, b = (cal.block_sign_flip_test(d, 288, seed=9) for _ in range(2))
        self.assertEqual(a["p_value"], b["p_value"])
        self.assertTrue(1.0 / 1000 <= a["p_value"] <= 1.0)

    def test_size_near_nominal_iid(self):
        rates = cal._rates(cal.SYNTHETIC_NULLS["iid_normal"], 3120, [288], 400, seed=11)
        self.assertLess(rates["288"]["two_sided_05"], 0.05 + 3 * np.sqrt(0.05 * 0.95 / 400))

    def test_semi_real_null_has_zero_mean_by_construction(self):
        x = np.random.default_rng(2).standard_t(3, 5000) + 0.7     # a positive-drift path
        gen = cal.semi_real_null(x)
        m = np.mean([gen(3120, np.random.default_rng(i)).mean() for i in range(300)])
        self.assertLess(abs(m), 0.02)


class SealTests(SimpleTestCase):
    def test_refuses_rows_inside_the_sealed_window(self):
        ts = pd.date_range(FX_RESEARCH_DATA_CUTOFF - pd.Timedelta(hours=2), periods=4, freq="h")
        df = pd.DataFrame({"timestamp": ts.astype(str), "equity": [1.0, 2.0, 3.0, 4.0]})
        with self.assertRaises(ValueError):
            cal.load_baseline_increments(rows=df)

    def test_accepts_rows_before_the_seal(self):
        ts = pd.date_range(FX_RESEARCH_DATA_CUTOFF - pd.Timedelta(hours=10), periods=4, freq="h")
        df = pd.DataFrame({"timestamp": ts.astype(str), "equity": [1.0, 2.0, 4.0, 7.0]})
        np.testing.assert_allclose(cal.load_baseline_increments(rows=df), [1.0, 2.0, 3.0])
