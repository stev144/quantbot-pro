# claude code changed: new file — Stage 2D Rev B steps 1, 3–5, 18: tests for
# bot/quant_core/protocol.py and bot/quant_core/kalman.py. The reference
# filter below is written independently (standard (I−KH)P form, explicit
# loops, no shared code) so a defect in the canonical module cannot also be
# present in its own oracle.

import math

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.quant_core.kalman import CanonicalKalman, CanonicalZScore
from bot.quant_core.protocol import CANONICAL, CanonicalProtocol, ProtocolValueMissing


def _reference_filter(log_a, log_b, theta0, q=(1e-5, 1e-5), r=1e-3):
    """Textbook Kalman for x = β·y + α + v with random-walk state; no gaps."""
    beta, alpha = theta0
    p00, p01, p11 = 1.0, 0.0, 1.0
    out = []
    for x, y in zip(log_a, log_b):
        p00 += q[0]; p11 += q[1]                               # prior
        e = x - (beta * y + alpha)
        s = y * y * p00 + 2 * y * p01 + p11 + r
        k0, k1 = (p00 * y + p01) / s, (p01 * y + p11) / s
        beta, alpha = beta + k0 * e, alpha + k1 * e
        # standard form (I − KH)P — equals Joseph form at the optimal gain
        n00 = (1 - k0 * y) * p00 - k0 * p01
        n01 = (1 - k0 * y) * p01 - k0 * p11
        n11 = -k1 * y * p01 + (1 - k1) * p11
        p00, p01, p11 = n00, n01, n11
        out.append((beta, alpha, e, s, k0, k1, p00, p11))
    return np.array(out)


def _pair(n, seed=3):
    g = np.random.default_rng(seed)
    y = np.cumsum(g.normal(0, 0.01, n)) + math.log(10)
    u = np.zeros(n)
    for t in range(1, n):
        u[t] = 0.9 * u[t - 1] + g.normal(0, 0.01)
    return 0.2 + 1.3 * y + u, y


class ProtocolTests(SimpleTestCase):
    def test_frozen_baseline_values(self):
        p = CANONICAL
        self.assertEqual((p.q_beta, p.q_alpha, p.r), (1e-5, 1e-5, 1e-3))
        self.assertEqual((p.z_window_candles, p.z_min_valid, p.z_clip, p.warmup_candles), (504, 168, 5.0, 168))
        self.assertEqual((p.entry_threshold, p.execution_delay_candles, p.gap_max_candles), (2.0, 1, 3))
        self.assertEqual((p.pair_allocation_fraction, p.permutation_b), (0.05, 999))
        self.assertFalse(p.partial_exit)

    def test_unset_holding_horizon_fails_closed(self):   # claude code changed: Stage 2D Rev B — H = 24 is now frozen; an UNSET H must still fail closed
        self.assertEqual(CANONICAL.max_holding_candles, 24)
        with self.assertRaises(ProtocolValueMissing):
            CANONICAL.with_overrides(max_holding_candles=None).time_stop_decision_offset()

    def test_unset_init_window_fails_closed(self):   # claude code changed: Stage 2D Rev B — L = 1000 now frozen (owner baseline); an UNSET L must still fail closed
        self.assertEqual(CANONICAL.init_window_candles, 1000)
        self.assertEqual(CANONICAL.seed_design, "A_PRE_SELECTION")
        with self.assertRaises(ProtocolValueMissing):
            CANONICAL.with_overrides(init_window_candles=None).require_init_window()
        with self.assertRaises(ValueError):
            CANONICAL.with_overrides(seed_design="B_SELECTION_WARMUP")
        with self.assertRaises(ValueError):
            CANONICAL.with_overrides(init_window_candles=1)

    def test_d3_time_stop_decision_is_h_minus_d_after_entry_fill(self):
        self.assertEqual(CANONICAL.with_overrides(max_holding_candles=4).time_stop_decision_offset(), 3)
        with self.assertRaises(ValueError):
            CANONICAL.with_overrides(max_holding_candles=1)      # exit decision would precede/coincide with entry fill

    def test_invalid_values_rejected(self):
        for kw in ({"r": 0.0}, {"z_min_valid": 600}, {"partial_exit": True},
                   {"pair_allocation_fraction": 0.06}, {"gap_max_candles": 0}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                CanonicalProtocol(**kw)

    def test_config_hash_changes_with_any_value(self):
        self.assertNotEqual(CANONICAL.config_hash(), CANONICAL.with_overrides(r=2e-3).config_hash())
        self.assertNotEqual(CANONICAL.config_hash(), CANONICAL.with_overrides(max_holding_candles=48).config_hash())   # claude code changed: Stage 2D Rev B — 24 is now the default
        self.assertNotEqual(CANONICAL.config_hash(), CANONICAL.with_overrides(init_window_candles=504).config_hash())   # claude code changed: Stage 2D Rev B C6 — the L = 504 sensitivity check is a different protocol
        self.assertEqual(CANONICAL.config_hash(), CanonicalProtocol().config_hash())


class CanonicalKalmanMathTests(SimpleTestCase):
    def test_matches_independent_reference_filter(self):
        la, lb = _pair(800)
        ref = _reference_filter(la, lb, (1.25, 0.25))
        kf = CanonicalKalman(np.array([1.25, 0.25]))
        got = np.array([(s.theta_post[0], s.theta_post[1], s.innovation, s.innovation_variance,
                         s.gain[0], s.gain[1], s.P_post[0, 0], s.P_post[1, 1])
                        for s in (kf.step(a, b) for a, b in zip(la, lb))])
        np.testing.assert_allclose(got, ref, rtol=1e-8, atol=1e-12)

    def test_filtered_residual_is_innovation_times_r_over_s(self):
        la, lb = _pair(300)
        kf = CanonicalKalman(np.array([1.3, 0.2]))
        for a, b in zip(la, lb):
            s = kf.step(a, b)
            self.assertAlmostEqual(s.filtered_residual, s.innovation * CANONICAL.r / s.innovation_variance, places=14)
            if s.innovation != 0:
                self.assertEqual(math.copysign(1, s.filtered_residual), math.copysign(1, s.innovation))

    def test_posterior_covariance_symmetric_positive_definite(self):
        la, lb = _pair(2000)
        kf = CanonicalKalman(np.array([1.3, 0.2]))
        for a, b in zip(la, lb):
            P = kf.step(a, b).P_post
            self.assertLessEqual(abs(P[0, 1] - P[1, 0]), 1e-12 * abs(P[0, 1]) + 1e-18)   # Joseph form: symmetric to rounding
            self.assertTrue((np.linalg.eigvalsh(P) > 0).all())

    def test_prior_is_previous_posterior_plus_q(self):
        la, lb = _pair(50)
        kf = CanonicalKalman(np.array([1.3, 0.2]))
        prev = kf.step(la[0], lb[0])
        for a, b in zip(la[1:], lb[1:]):
            s = kf.step(a, b)
            np.testing.assert_array_equal(s.theta_prior, prev.theta_post)
            np.testing.assert_allclose(s.P_prior, prev.P_post + np.diag([1e-5, 1e-5]), rtol=0, atol=1e-15)
            prev = s


class GapPolicyTests(SimpleTestCase):
    """D8: g ≤ 3 → P(t+g|t) = P(t|t) + gQ on the grid; g > 3 → invalid, re-seed."""

    def _run_with_gap(self, g, n_before=400):
        la, lb = _pair(n_before + g + 5)
        kf = CanonicalKalman(np.array([1.3, 0.2]))
        steps = [kf.step(la[i], lb[i]) for i in range(n_before)]
        steps += [kf.step(math.nan, math.nan) for _ in range(g)]
        steps.append(kf.step(la[n_before + g], lb[n_before + g]))
        return steps, n_before

    def test_gap_of_one_to_three_adds_g_q_without_compression(self):
        Q = np.diag([1e-5, 1e-5])
        for g in (1, 2, 3):
            with self.subTest(g=g):
                steps, k = self._run_with_gap(g)
                P_t = steps[k - 1].P_post
                np.testing.assert_allclose(steps[k + g - 1].P_post, P_t + g * Q, rtol=0, atol=1e-15)
                after = steps[k + g]
                np.testing.assert_allclose(after.P_prior, P_t + (g + 1) * Q, rtol=0, atol=1e-15)
                np.testing.assert_array_equal(after.theta_prior, steps[k - 1].theta_post)
                self.assertTrue(after.state_valid)
                self.assertFalse(after.reseeded)
                self.assertEqual(after.segment, 0)
                for s in steps[k:k + g]:
                    self.assertFalse(s.observed)
                    self.assertTrue(math.isnan(s.innovation))

    def test_gap_longer_than_g_max_invalidates_and_reseeds(self):
        steps, k = self._run_with_gap(4)
        self.assertTrue(steps[k + 2].state_valid)           # 3rd missing: still within g_max
        self.assertFalse(steps[k + 3].state_valid)          # 4th missing: invalid
        after = steps[k + 4]
        self.assertTrue(after.reseeded)
        self.assertEqual(after.segment, 1)
        self.assertTrue(after.in_warmup)
        np.testing.assert_array_equal(after.theta_prior, np.array([1.3, 0.2]))
        np.testing.assert_allclose(after.P_prior, np.eye(2) + np.diag([1e-5, 1e-5]), rtol=0, atol=1e-15)

    def test_reseed_equals_a_fresh_filter_started_at_that_candle(self):
        la, lb = _pair(900)
        kf = CanonicalKalman(np.array([1.3, 0.2]))
        for i in range(300):
            kf.step(la[i], lb[i])
        for _ in range(10):
            kf.step(math.nan, math.nan)
        fresh = CanonicalKalman(np.array([1.3, 0.2]))
        for i in range(310, 900):
            a, b = kf.step(la[i], lb[i]), fresh.step(la[i], lb[i])
            np.testing.assert_array_equal(a.theta_post, b.theta_post)
            self.assertEqual(a.in_warmup, b.in_warmup)

    def test_warmup_counts_grid_candles_after_seed(self):
        la, lb = _pair(200)
        kf = CanonicalKalman(np.array([1.3, 0.2]))
        flags = [kf.step(la[i], lb[i]).in_warmup for i in range(200)]
        self.assertTrue(all(flags[:168]))
        self.assertFalse(any(flags[168:]))


class CanonicalZScoreTests(SimpleTestCase):
    def test_equals_pandas_rolling_on_a_nan_bearing_grid(self):
        g = np.random.default_rng(5)
        v = g.normal(0, 1, 1500)
        v[[100, 101, 700, 1200, 1201, 1202]] = np.nan
        z = CanonicalZScore()
        got = np.array([np.nan if (r := z.update(x, 0)) is None else r for x in v])
        s = pd.Series(v)
        m, sd = s.rolling(504, min_periods=168).mean(), s.rolling(504, min_periods=168).std()
        ref = ((s - m) / sd).clip(-5, 5).to_numpy()
        np.testing.assert_allclose(got, ref, rtol=1e-9, atol=1e-12, equal_nan=True)

    def test_segment_change_clears_history(self):
        z = CanonicalZScore()
        for x in np.random.default_rng(1).normal(0, 1, 300):
            z.update(x, 0)
        self.assertIsNotNone(z.update(0.5, 0))
        self.assertIsNone(z.update(0.5, 1))                 # new segment: one value, below minimum

    def test_clip_and_undefined_cases(self):
        z = CanonicalZScore()
        for _ in range(200):
            z.update(0.0, 0)
        self.assertIsNone(z.update(0.0, 0))                 # zero sd → undefined, never ±inf
        z2 = CanonicalZScore()
        for x in np.random.default_rng(2).normal(0, 1, 200):
            z2.update(x, 0)
        self.assertEqual(z2.update(1e6, 0), 5.0)
        self.assertIsNone(z2.update(math.nan, 0))
