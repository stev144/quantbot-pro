# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/test_step17_calibration.py
# claude code changed: new file — 2026-10-07, D-4 size calibration under the signed OPEN-36 criterion.
# Known answers for the verdict (Wilson bound, replicate count, D-4 band, untestable accounting), refusals of
# non-signed values, determinism of the null pairs, and one complete replicate with a cheap statistic.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import numpy as np
from django.test import SimpleTestCase

from bot.research import step17_calibration as sc


def recs(n_ok, k, n_untestable=0):
    out = [{"rep": i, "status": "OK", "reject": i < k, "p_value": 0.01 if i < k else 0.5, "seconds": 1.0}
           for i in range(n_ok)]
    out += [{"rep": n_ok + j, "status": "UNTESTABLE", "reject": False, "p_value": None, "seconds": 1.0}
            for j in range(n_untestable)]
    return out


class VerdictTests(SimpleTestCase):

    def test_170_of_3202_passes_171_fails(self):
        self.assertEqual(sc.evaluate(recs(3202, 170), target=3202)["status"], "PASS")
        r = sc.evaluate(recs(3202, 171), target=3202)
        self.assertEqual((r["status"], r["open36_gate"]), ("FAIL", "FAIL"))
        self.assertGreater(r["wilson_upper_one_sided_95"], 0.06)

    def test_fewer_testable_replicates_is_incomplete_never_pass(self):
        r = sc.evaluate(recs(3201, 100, n_untestable=50), target=3202)
        self.assertEqual(r["status"], "INCOMPLETE")
        self.assertEqual((r["n_testable"], r["n_untestable"]), (3201, 50))

    def test_untestable_replicates_do_not_dilute_the_rate(self):
        r = sc.evaluate(recs(3202, 170, n_untestable=500), target=3202)
        self.assertAlmostEqual(r["rejection_rate"], 170 / 3202)          # not 170 / 3702
        self.assertEqual(r["status"], "PASS")

    def test_under_sized_test_fails_the_signed_d4_band(self):
        r = sc.evaluate(recs(3202, 90), target=3202)                        # 2.8 %: Wilson passes, band fails
        self.assertEqual((r["open36_gate"], r["d4_band"], r["status"]), ("PASS", "FAIL", "FAIL"))

    def test_a_pilot_size_never_gets_a_verdict(self):
        self.assertEqual(sc.evaluate(recs(8, 0), target=8)["status"], "NOT_EVIDENCE")
        self.assertEqual(sc.evaluate(recs(8, 8), target=8)["status"], "NOT_EVIDENCE")

    def test_reported_bound_is_the_wilson_upper_bound_not_the_rate(self):
        from bot.quant_core.calibration import wilson_upper_one_sided
        r = sc.evaluate(recs(3202, 160), target=3202)
        self.assertEqual(r["wilson_upper_one_sided_95"], float(wilson_upper_one_sided(160, 3202, 0.05)))
        self.assertGreater(r["wilson_upper_one_sided_95"], r["rejection_rate"])


class RefusalTests(SimpleTestCase):

    def test_non_signed_values_refused_without_pilot(self):
        with self.assertRaisesRegex(ValueError, "signed values"):
            sc.run_size_calibration(target=500)
        with self.assertRaisesRegex(ValueError, "signed values"):
            sc.run_size_calibration(n_perm=99)

    def test_constants_are_the_signed_ones(self):
        from bot.quant_core.calibration import OPEN36_MIN_REPS
        self.assertEqual((sc.SIGNED_B, sc.REJECT_AT, OPEN36_MIN_REPS), (999, 0.05, 3202))


class NullPairTests(SimpleTestCase):

    def test_pairs_are_deterministic_unrelated_and_span_zone_v(self):
        from bot.quant_core.research_zones import ZONES
        a1, b1 = sc.scenario_pair("RW_GAUSSIAN_UNRELATED", 3)
        a2, b2 = sc.scenario_pair("RW_GAUSSIAN_UNRELATED", 3)
        a3, _ = sc.scenario_pair("RW_GAUSSIAN_UNRELATED", 4)
        self.assertTrue(a1.equals(a2) and b1.equals(b2))
        self.assertFalse(a1.equals(a3))
        self.assertEqual((a1.index[0], a1.index[-1]), (ZONES.init_window()[0], ZONES.V1))
        ra, rb = np.diff(np.log(a1.to_numpy())), np.diff(np.log(b1.to_numpy()))
        self.assertLess(abs(np.corrcoef(ra, rb)[0, 1]), 0.03)            # independent legs (n ≈ 43,800)

    def test_one_complete_replicate_with_a_cheap_statistic(self):
        stat = lambda pa, pb: float(np.corrcoef(np.diff(np.log(pa.to_numpy()))[:2000], np.diff(np.log(pb.to_numpy()))[:2000])[0, 1])
        r = sc.run_replicate("RW_GAUSSIAN_UNRELATED", 0, 19, statistic=stat)
        self.assertEqual(r["status"], "OK")
        self.assertEqual(r["reject"], r["p_value"] <= 0.05)
        self.assertEqual(r, {**sc.run_replicate("RW_GAUSSIAN_UNRELATED", 0, 19, statistic=stat), "seconds": r["seconds"]})
