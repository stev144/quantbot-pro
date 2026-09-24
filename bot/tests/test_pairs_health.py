# ============================================================
# bot/tests/test_pairs_health.py
# claude code changed: new file — pairs pilot beta-drift/health audit,
# 2026-09-24. Deterministic tests for bot/pairs/health.py: every check
# is a pure function of its inputs, so every case below is exact, not
# statistical.
# ============================================================

import math

from django.test import SimpleTestCase

from bot.pairs.health import (
    PairHealthState,
    BETA_DRIFT_DEGRADED_RATIO,
    evaluate_pair_health,
)


class BetaDriftMagnitudeTest(SimpleTestCase):
    def test_no_drift_is_normal(self):
        h = evaluate_pair_health("A/B", seed_beta=1.0, current_beta=1.0, passes_filters=True)
        self.assertEqual(h.state, PairHealthState.NORMAL)
        self.assertEqual(h.beta_drift_ratio, 0.0)

    def test_small_drift_is_normal(self):
        h = evaluate_pair_health("A/B", seed_beta=1.0, current_beta=1.1, passes_filters=True)
        self.assertEqual(h.state, PairHealthState.NORMAL)
        self.assertAlmostEqual(h.beta_drift_ratio, 0.1, places=6)

    def test_exactly_at_threshold_is_not_flagged(self):
        # claude code changed: strict '>' in health.py — exactly at the
        # threshold is NOT flagged, only strictly beyond it.
        current = 1.0 * (1 + BETA_DRIFT_DEGRADED_RATIO)
        h = evaluate_pair_health("A/B", seed_beta=1.0, current_beta=current, passes_filters=True)
        self.assertEqual(h.state, PairHealthState.NORMAL)

    def test_above_threshold_is_degraded(self):
        current = 1.0 * (1 + BETA_DRIFT_DEGRADED_RATIO) + 0.01
        h = evaluate_pair_health("A/B", seed_beta=1.0, current_beta=current, passes_filters=True)
        self.assertEqual(h.state, PairHealthState.DEGRADED)
        self.assertIn("BETA_DRIFT_MAGNITUDE", h.reason_codes)

    def test_negative_beta_same_sign_large_drift(self):
        h = evaluate_pair_health("A/B", seed_beta=-1.0, current_beta=-3.0, passes_filters=True)
        self.assertEqual(h.state, PairHealthState.DEGRADED)
        self.assertFalse(h.beta_sign_reversed)  # same sign, just large magnitude drift


class BetaSignReversalTest(SimpleTestCase):
    def test_sign_flip_positive_to_negative_is_degraded(self):
        h = evaluate_pair_health("A/B", seed_beta=1.625044, current_beta=-0.307434, passes_filters=True)
        self.assertEqual(h.state, PairHealthState.DEGRADED)
        self.assertTrue(h.beta_sign_reversed)
        self.assertIn("BETA_SIGN", h.reason_codes)

    def test_sign_flip_negative_to_positive_is_degraded(self):
        h = evaluate_pair_health("A/B", seed_beta=-1.0, current_beta=0.5, passes_filters=True)
        self.assertTrue(h.beta_sign_reversed)
        self.assertEqual(h.state, PairHealthState.DEGRADED)

    def test_sign_flip_is_flagged_even_with_small_magnitude_drift(self):
        # claude code changed: a sign flip near zero (small absolute
        # change) must still be flagged — it's a qualitative category
        # change, not a magnitude one. Real governance requirement, not
        # just an artifact of the ratio formula.
        h = evaluate_pair_health("A/B", seed_beta=0.01, current_beta=-0.01, passes_filters=True)
        self.assertTrue(h.beta_sign_reversed)
        self.assertEqual(h.state, PairHealthState.DEGRADED)

    def test_zero_seed_beta_never_flagged_as_sign_flip(self):
        h = evaluate_pair_health("A/B", seed_beta=0.0, current_beta=-5.0, passes_filters=True)
        self.assertFalse(h.beta_sign_reversed)
        self.assertEqual(h.state, PairHealthState.NORMAL)

    def test_very_small_seed_beta_nonzero_still_computes_ratio(self):
        h = evaluate_pair_health("A/B", seed_beta=1e-6, current_beta=1e-6 * 3, passes_filters=True)
        self.assertFalse(h.beta_sign_reversed)
        self.assertGreater(h.beta_drift_ratio, BETA_DRIFT_DEGRADED_RATIO)
        self.assertEqual(h.state, PairHealthState.DEGRADED)


class NonFiniteBetaTest(SimpleTestCase):
    def test_nan_current_beta_does_not_crash(self):
        h = evaluate_pair_health("A/B", seed_beta=1.0, current_beta=float("nan"), passes_filters=True)
        # NaN comparisons are always False in Python — sign check reads
        # this as "not reversed" (nan > 0 is False, same as seed>0 being
        # True gives a mismatch) and drift_ratio becomes nan; the
        # function must return without raising either way.
        self.assertIn(h.state, (PairHealthState.NORMAL, PairHealthState.DEGRADED))
        self.assertTrue(math.isnan(h.beta_drift_ratio) or h.state == PairHealthState.DEGRADED)

    def test_infinite_current_beta_does_not_crash(self):
        h = evaluate_pair_health("A/B", seed_beta=1.0, current_beta=float("inf"), passes_filters=True)
        self.assertEqual(h.state, PairHealthState.DEGRADED)  # infinite drift ratio > threshold


class CointegrationGateTest(SimpleTestCase):
    def test_fails_cointegration_is_invalidated_regardless_of_beta(self):
        h = evaluate_pair_health(
            "AVA_USDT/PHA_USDT", seed_beta=0.729686, current_beta=0.729686,  # identical beta, zero drift
            passes_filters=False, reject_reason="half-life 129.4 candles > max 120 candles",
        )
        self.assertEqual(h.state, PairHealthState.INVALIDATED)
        self.assertIn("COINTEGRATION_GATE", h.reason_codes)
        self.assertIn("129.4", h.summary())

    def test_invalidated_outranks_degraded_when_both_present(self):
        # claude code changed: multiple simultaneous warnings — overall
        # state must be the WORST check, not the first or last one
        # evaluated.
        h = evaluate_pair_health(
            "A/B", seed_beta=1.0, current_beta=-5.0,  # sign flip AND large drift AND
            passes_filters=False, reject_reason="not cointegrated",  # fails cointegration
        )
        self.assertEqual(h.state, PairHealthState.INVALIDATED)
        self.assertTrue(h.beta_sign_reversed)
        self.assertEqual(len(h.reason_codes), 3)  # all three checks flagged


class ReasonCodeDeterminismTest(SimpleTestCase):
    def test_identical_inputs_produce_identical_reason_codes(self):
        h1 = evaluate_pair_health("A/B", 1.625044, -0.307434, True, "")
        h2 = evaluate_pair_health("A/B", 1.625044, -0.307434, True, "")
        self.assertEqual(h1.reason_codes, h2.reason_codes)
        self.assertEqual(h1.state, h2.state)

    def test_policy_version_is_stamped(self):
        h = evaluate_pair_health("A/B", 1.0, 1.0, True)
        self.assertTrue(h.policy_version)
