# claude code changed: new file — 2026-10-03, owner decision D-4 (Step-17 canonical pairs null).
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from bot.tests.synthetic_data import cointegrated_log_pair   # claude code changed: 2026-10-04 — shared generator (bot/tests/synthetic_data.py), identical draws
from django.test import SimpleTestCase

from bot.quant_core.research_zones import ZONES
from bot.research import pairs_step17_null as nl


def _coint_pair(n=3500, seed=3):
    la, lb = cointegrated_log_pair(n, np.random.default_rng(seed))   # claude code changed: 2026-10-04 — shared generator (bot/tests/synthetic_data.py), identical draws
    idx = pd.date_range(ZONES.init_window()[0], periods=n, freq="h", tz="UTC")
    return pd.Series(np.exp(la), index=idx), pd.Series(np.exp(lb), index=idx)


class ShiftTests(SimpleTestCase):
    def test_shift_preserves_leg_b_returns_and_start_price(self):
        a, b = _coint_pair()
        s = nl.shift_leg_b(a, b, 1000)
        self.assertEqual(s.iloc[0], b.iloc[0])
        self.assertTrue(s.index.equals(b.index) and (s > 0).all())
        np.testing.assert_allclose(np.sort(np.diff(np.log(s.to_numpy()))), np.sort(np.diff(np.log(b.to_numpy()))), atol=1e-12)

    def test_shift_destroys_the_cross_asset_relationship(self):
        a, b = _coint_pair()
        ra = np.diff(np.log(a.to_numpy()))
        self.assertGreater(np.corrcoef(ra, np.diff(np.log(b.to_numpy())))[0, 1], 0.5)
        shifted = nl.shift_leg_b(a, b, 1500)
        self.assertLess(abs(np.corrcoef(ra, np.diff(np.log(shifted.to_numpy())))[0, 1]), 0.05)

    def test_shift_bounds_and_refusals(self):
        a, b = _coint_pair(n=2000)
        sh = nl.draw_shifts(2000, 5000, np.random.default_rng(0))
        self.assertTrue((sh >= 720).all() and (sh <= 2000 - 1 - 720).all())
        with self.assertRaises(ValueError):
            nl.draw_shifts(1400, 10, np.random.default_rng(0))
        with self.assertRaises(ValueError):
            nl.shift_leg_b(a, b.iloc[1:], 800)
        with self.assertRaises(ValueError):
            nl.stationary_block_bootstrap(np.ones(10), None, np.random.default_rng(0))   # owner parameter unset


class TestMechanicsTests(SimpleTestCase):
    def test_exact_one_sided_p_and_reproducibility(self):
        a, b = _coint_pair()
        stat = lambda pa, pb: float(np.corrcoef(np.diff(np.log(pa)), np.diff(np.log(pb)))[0, 1])
        r = nl.step17_null_test(a, b, stat, seed=7, n_perm=49, canonical=False)
        sh = nl.draw_shifts(len(b), 49, np.random.default_rng(7))
        null = np.array([stat(a, nl.shift_leg_b(a, b, int(s))) for s in sh])
        self.assertEqual(r["p_value"], (1 + int((null >= r["statistic"]).sum())) / 50)
        self.assertEqual(r["p_value"], 1 / 50)                      # a real relationship beats every shift
        self.assertEqual(r["label"], "CALIBRATION/TEST ONLY — NOT EVIDENCE")
        self.assertEqual(r, nl.step17_null_test(a, b, stat, seed=7, n_perm=49, canonical=False))

    def test_cheap_statistic_is_size_controlled_on_unrelated_random_walks(self):
        # Sanity of the SHIFT MECHANICS only (not the canonical pipeline): under H0 rejections ≈ α.
        rng = np.random.default_rng(11)
        stat = lambda pa, pb: float(np.corrcoef(np.diff(np.log(pa)), np.diff(np.log(pb)))[0, 1])
        idx = pd.date_range("2022-01-01", periods=2000, freq="h", tz="UTC")
        rej = 0
        for i in range(200):
            a = pd.Series(np.exp(np.cumsum(0.01 * rng.standard_normal(2000))), idx)
            b = pd.Series(np.exp(np.cumsum(0.01 * rng.standard_normal(2000))), idx)
            rej += nl.step17_null_test(a, b, stat, seed=i, n_perm=99, canonical=False)["p_value"] <= 0.05
        self.assertTrue(0.01 <= rej / 200 <= 0.10, rej / 200)        # 200 reps: SE ≈ 1.5 %


class CalibrationGateTests(SimpleTestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_canonical_use_refused_without_a_passing_calibration(self):
        a, b = _coint_pair(n=2000)
        stat = lambda pa, pb: 0.0
        art = self.d / "cal.json"
        with self.assertRaises(nl.Step17NotCalibrated):
            nl.step17_null_test(a, b, stat, seed=0, n_perm=9, artifact=art)
        for bad in ({"null_version": nl.NULL_VERSION, "reps_size": 100, "size": 0.05, "power": {}},     # R too small
                    {"null_version": nl.NULL_VERSION, "reps_size": 600, "size": 0.09, "power": {}},     # size out of band
                    {"null_version": nl.NULL_VERSION, "reps_size": 600, "size": 0.05},                  # no power
                    {"null_version": "OLD", "reps_size": 600, "size": 0.05, "power": {}}):
            art.write_text(json.dumps(bad))
            with self.subTest(bad=bad), self.assertRaises(nl.Step17NotCalibrated):
                nl.require_calibrated(art)
        art.write_text(json.dumps({"null_version": nl.NULL_VERSION, "reps_size": 600, "size": 0.05, "power": {"ou": 0.8}}))
        self.assertEqual(nl.require_calibrated(art)["size"], 0.05)


class CanonicalPipelineSmokeTest(SimpleTestCase):
    def test_full_pipeline_statistic_runs_on_real_and_shifted_legs(self):
        a, b = _coint_pair()
        r = nl.step17_null_test(a, b, nl.canonical_net_mean_return_per_trade, seed=1, n_perm=2, canonical=False)
        self.assertIn(r["status"], ("OK", "UNTESTABLE"))
        self.assertIsNotNone(r.get("null_version"))
