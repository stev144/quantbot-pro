# claude code changed: new file — Stage 2D tests for bot/quant_core/calibration.py.
# Tooling correctness only. No calibration gate is declared PASS here: the
# tolerances are owner decisions (Rev B OPEN-36).

import math

import numpy as np
from django.test import SimpleTestCase

from bot.quant_core import calibration as cal
from bot.quant_core.status import Status


class WilsonTests(SimpleTestCase):
    def test_reproduces_stage1_interval(self):
        lo, hi = cal.wilson_bounds(31, 450, 0.95)      # Stage 1 NULL A pooled FPR
        self.assertAlmostEqual(lo, 0.0490, places=3)
        self.assertAlmostEqual(hi, 0.0961, places=3)

    def test_one_sided_bounds_bracket_the_rate(self):
        self.assertGreater(cal.wilson_upper_one_sided(10, 200, 0.05), 0.05)
        self.assertLess(cal.wilson_lower_one_sided(190, 200, 0.05), 0.95)

    def test_invalid_counts_rejected(self):
        with self.assertRaises(ValueError):
            cal.wilson_bounds(5, 0, 0.95)


class GateAndPowerTests(SimpleTestCase):
    def test_size_gate_passes_and_fails(self):
        self.assertTrue(cal.size_gate(40, 1000, nominal=0.05, delta=0.02, alpha=0.05).ok)
        self.assertIs(cal.size_gate(90, 1000, nominal=0.05, delta=0.02, alpha=0.05).status, Status.FAIL)

    def test_small_samples_cannot_pass_a_tight_gate(self):
        """The Stage 2A flaw: with few repetitions the bound is wide, so a
        correctly sized test cannot demonstrate it. The gate must FAIL, not pass."""
        self.assertIs(cal.size_gate(7, 150, nominal=0.05, delta=0.02, alpha=0.05).status, Status.FAIL)

    def test_coverage_gate(self):
        self.assertTrue(cal.coverage_gate(950, 1000, nominal=0.95, delta=0.02, alpha=0.05).ok)
        self.assertIs(cal.coverage_gate(900, 1000, nominal=0.95, delta=0.02, alpha=0.05).status, Status.FAIL)

    def test_minimum_repetitions_reaches_power(self):
        for delta in (0.02, 0.025):
            m = cal.minimum_repetitions(nominal=0.05, delta=delta, alpha=0.05, power=0.8)
            self.assertGreaterEqual(cal.pass_probability(m, 0.05, nominal=0.05, delta=delta, alpha=0.05), 0.8)
            self.assertLess(cal.pass_probability(m - 1, 0.05, nominal=0.05, delta=delta, alpha=0.05), 0.8)

    def test_closed_form_shortcut_underpowers(self):
        """Rev B §4's shortcut M ≥ 0.2938/δ² (735 at δ = 0.02) gives < 80 % power."""
        self.assertLess(cal.pass_probability(735, 0.05, nominal=0.05, delta=0.02, alpha=0.05), 0.8)

    def test_pass_probability_matches_brute_force(self):
        n, d = 300, 0.03
        brute = sum(math.comb(n, k) * 0.05 ** k * 0.95 ** (n - k)
                    for k in range(n + 1) if cal.wilson_upper_one_sided(k, n, 0.05) <= 0.05 + d)
        self.assertAlmostEqual(cal.pass_probability(n, 0.05, nominal=0.05, delta=d, alpha=0.05), brute, places=10)


class NullWorldTests(SimpleTestCase):
    def test_worlds_are_finite_and_aligned(self):
        for world in ("rw", "corr_rw", "betawalk"):
            for inn in ("gaussian", "student_t3", "garch"):
                x, y = cal.null_world(world, 1, 2000, inn)
                self.assertTrue(x.index.equals(y.index))
                self.assertTrue(np.isfinite(x).all() and np.isfinite(y).all())

    def test_student_t_has_fat_tails(self):
        x, _ = cal.null_world("rw", 3, 20000, "student_t3")
        r = np.diff(x.to_numpy())
        kurt = ((r - r.mean()) ** 4).mean() / r.var() ** 2
        self.assertGreater(kurt, 5.0)                         # Gaussian ≈ 3

    def test_garch_clusters_volatility(self):
        x, _ = cal.null_world("rw", 4, 20000, "garch")
        r2 = np.diff(x.to_numpy()) ** 2
        self.assertGreater(np.corrcoef(r2[1:], r2[:-1])[0, 1], 0.05)   # iid ≈ 0

    def test_unknown_world_rejected(self):
        with self.assertRaises(KeyError):
            cal.null_world("ou", 1, 100)

    def test_eg_size_run_counts_every_replicate(self):
        out = cal.eg_size_run("rw", innovations="gaussian", M=10, n_obs=600, seed_base=500)
        self.assertEqual(out["M"], 10)
        self.assertLessEqual(out["rejections"] + out["untestable"], 10)
