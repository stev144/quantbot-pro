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
        # claude code changed: 2026-10-07 — owner-signed OPEN-36 makes the gate STRICTER: R = 600 at 5 % (which passed
        # the old observed-band rule) is now refused; a pass needs >= 3,202 replicates, a consistent rejection count and a
        # one-sided 95 % Wilson upper bound <= 6 % (at R = 3,202: at most 170 rejections, 5.31 %).
        def cal(k, r, **extra):
            return {"null_version": nl.NULL_VERSION, "reps_size": r, "rejections": k, "size": k / r, "power": {"ou": 0.8}, **extra}
        for bad in (cal(30, 600),                                   # old rule passed; fewer than 3,202 replicates
                    cal(171, 3202),                                 # 5.34 %: inside the D-4 band, Wilson UB > 6 %
                    {**cal(160, 3202), "rejections": None},         # no raw count → the bound cannot be verified
                    {**cal(160, 3202), "size": 0.04}):              # count and size disagree
            art.write_text(json.dumps(bad))
            with self.subTest(bad=bad), self.assertRaises(nl.Step17NotCalibrated):
                nl.require_calibrated(art)
        art.write_text(json.dumps(cal(170, 3202)))
        self.assertEqual(nl.require_calibrated(art)["rejections"], 170)
        art.write_text(json.dumps(cal(90, 3202)))                   # 2.8 %: Wilson passes but below the signed D-4 3–7 % band
        with self.assertRaises(nl.Step17NotCalibrated):
            nl.require_calibrated(art)

    def test_signed_open36_constants(self):   # claude code changed: 2026-10-07 OPEN-36 (signed)
        from bot.quant_core import calibration as cal
        self.assertEqual((cal.OPEN36_NOMINAL, cal.OPEN36_DELTA, cal.OPEN36_ALPHA, cal.OPEN36_POWER), (0.05, 0.01, 0.05, 0.80))
        self.assertEqual(cal.OPEN36_MIN_REPS, cal.minimum_repetitions(nominal=0.05, delta=0.01, alpha=0.05, power=0.80))
        self.assertEqual(cal.OPEN36_MIN_REPS, 3202)
        self.assertEqual(cal.signed_size_gate(40, 800).status.value, "UNTESTABLE")   # too few replicates is never PASS
        self.assertTrue(cal.signed_size_gate(170, 3202).ok)
        self.assertFalse(cal.signed_size_gate(171, 3202).ok)


class CanonicalPipelineSmokeTest(SimpleTestCase):
    def test_full_pipeline_statistic_runs_on_real_and_shifted_legs(self):
        a, b = _coint_pair()
        r = nl.step17_null_test(a, b, nl.canonical_net_mean_return_per_trade, seed=1, n_perm=2, canonical=False)
        self.assertIn(r["status"], ("OK", "UNTESTABLE"))
        self.assertIsNotNone(r.get("null_version"))


# claude code changed: 2026-10-04 D-4 speed-up — the lean in-memory statistic must equal the canonical FILE pipeline
# exactly (same trades, same per-trade P&L, same statistic), refuse bad lineage, and the canonical Kalman CSV must
# now round-trip bit-exactly (pandas' default float parser did not: ~65 % of floats came back changed).
def _file_pipeline(a, b, fund, d):
    from bot.research.entry_exit_engine import EntryExitEngine
    from bot.research.kalman_filter_engine import KalmanFilterEngine
    KalmanFilterEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT", ols_beta=1.0,
                       ols_alpha=0.0).run_canonical(a, b, output_dir=d)
    tl, _, _ = EntryExitEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT", output_dir=d,
                               rule_set="CANONICAL", funding_rates_a=fund, funding_rates_b=fund) \
        .run(kalman_csv=str(Path(d) / "canonical" / "X_USDT_Y_USDT_kalman.csv"))
    return tl


class LeanStatisticEquivalenceTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        idx = pd.date_range(ZONES.init_window()[0], pd.Timestamp("2024-06-30 23:00", tz="UTC"), freq="h")
        from bot.tests.synthetic_data import cointegrated_pair
        cls.a, cls.b = cointegrated_pair(idx, 3)                                    # reaches into zone V
        cls.fund = pd.Series(0.0001, index=pd.date_range("2021-09-01", "2024-12-31", freq="8h", tz="UTC"))
        cls.d = tempfile.mkdtemp()
        cls.file_tl = _file_pipeline(cls.a, cls.b, cls.fund, cls.d)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)
        super().tearDownClass()

    def test_lean_trade_log_and_statistic_equal_the_file_pipeline_exactly(self):
        from bot.research.entry_exit_engine import EntryExitEngine
        from bot.research.kalman_filter_engine import KalmanFilterEngine
        keng = KalmanFilterEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT", ols_beta=1.0, ols_alpha=0.0)
        res = keng.run_canonical(self.a, self.b)
        lean = EntryExitEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT", output_dir=self.d,
                               rule_set="CANONICAL", funding_rates_a=self.fund, funding_rates_b=self.fund) \
            .run_canonical_frame(res, keng.seed_record)
        self.assertGreater(len(lean), 50)
        lineage_cols = [c for c in lean.columns if "run_id" in c]                 # the file run carries the input run id
        pd.testing.assert_frame_equal(lean.drop(columns=lineage_cols).reset_index(drop=True),
                                      self.file_tl.drop(columns=lineage_cols).reset_index(drop=True), check_exact=True)
        v = nl.zone_v_label_complete(self.file_tl)
        self.assertGreater(len(v), 0)
        expected = float(v["net_pnl_pct"].astype(float).mean())
        got = nl.canonical_net_mean_return_per_trade(self.a, self.b, funding_rates_a=self.fund, funding_rates_b=self.fund)
        self.assertEqual(got, expected)                                            # exact, not approximate

    def test_lean_path_refuses_bad_lineage(self):
        from dataclasses import replace
        from bot.research.entry_exit_engine import CanonicalProvenanceError, EntryExitEngine
        from bot.research.kalman_filter_engine import KalmanFilterEngine
        keng = KalmanFilterEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT", ols_beta=1.0, ols_alpha=0.0)
        res = keng.run_canonical(self.a, self.b)
        mk = lambda **kw: EntryExitEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT",
                                          output_dir=self.d, funding_rates_a=self.fund, funding_rates_b=self.fund, **kw)
        with self.assertRaisesRegex(ValueError, "CANONICAL rule set only"):
            mk(rule_set="CANONICAL_UNCERTIFIED").run_canonical_frame(res, keng.seed_record)
        with self.assertRaisesRegex(CanonicalProvenanceError, "engine pair"):
            mk(rule_set="CANONICAL").run_canonical_frame(res, replace(keng.seed_record, pair_name="A_USDT/B_USDT"))
        with self.assertRaises(CanonicalProvenanceError):                         # a seed from another methodology
            mk(rule_set="CANONICAL").run_canonical_frame(res, replace(keng.seed_record, methodology_version="STAGE2D-REVB-3"))
        with self.assertRaisesRegex(CanonicalProvenanceError, "seed candle"):     # frame does not start at its seed
            mk(rule_set="CANONICAL").run_canonical_frame(res.iloc[5:], keng.seed_record)

    def test_canonical_kalman_csv_round_trips_bit_exactly(self):
        from bot.research.entry_exit_engine import EntryExitEngine
        from bot.research.kalman_filter_engine import KalmanFilterEngine
        keng = KalmanFilterEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT", ols_beta=1.0, ols_alpha=0.0)
        res = keng.run_canonical(self.a, self.b, output_dir=self.d)
        ee = EntryExitEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT", output_dir=self.d,
                             rule_set="CANONICAL", funding_rates_a=self.fund, funding_rates_b=self.fund)
        back = ee._load_kalman_data(str(Path(self.d) / "canonical" / "X_USDT_Y_USDT_kalman.csv"))
        cols = ["kalman_zscore", "kalman_beta_pred", "kalman_alpha_pred", "close_a", "close_b"]
        for c in cols:
            x, y = res.loc[back.index, c].to_numpy(float), back[c].to_numpy(float)
            same = (x == y) | (np.isnan(x) & np.isnan(y))
            self.assertTrue(same.all(), f"{c}: {int((~same).sum())} values changed by the CSV round trip")


class LeanFundingGapParityTests(SimpleTestCase):
    """claude code changed: 2026-10-04 — the lean path must flag missing funding exactly as run() does (closes the
    S5 mutation gap: the statistic alone cannot see the borrow flag)."""

    def test_funding_gaps_and_borrow_flag_match_the_file_run(self):
        from bot.research.entry_exit_engine import EntryExitEngine
        from bot.research.kalman_filter_engine import KalmanFilterEngine
        from bot.tests.synthetic_data import cointegrated_pair
        idx = pd.date_range(ZONES.init_window()[0], pd.Timestamp("2022-09-30 23:00", tz="UTC"), freq="h")
        a, b = cointegrated_pair(idx, 3)
        fund = pd.Series(0.0001, index=pd.date_range("2021-09-01", "2022-03-31", freq="8h", tz="UTC"))   # ends early
        d = tempfile.mkdtemp()
        try:
            keng = KalmanFilterEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT", ols_beta=1.0, ols_alpha=0.0)
            res = keng.run_canonical(a, b, output_dir=d)
            mk = lambda: EntryExitEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT", output_dir=d,
                                         rule_set="CANONICAL", funding_rates_a=fund, funding_rates_b=fund)
            file_eng, lean_eng = mk(), mk()
            file_eng.run(kalman_csv=str(Path(d) / "canonical" / "X_USDT_Y_USDT_kalman.csv"))
            lean_eng.run_canonical_frame(res, keng.seed_record)
            self.assertGreater(len(file_eng.funding_gaps), 0)
            self.assertEqual(lean_eng.funding_gaps, file_eng.funding_gaps)
            self.assertFalse(file_eng.borrow_cost_modeled)
            self.assertEqual(lean_eng.borrow_cost_modeled, file_eng.borrow_cost_modeled)
            self.assertEqual(lean_eng._borrow_status(), "BORROW_UNAVAILABLE")
        finally:
            shutil.rmtree(d, ignore_errors=True)
