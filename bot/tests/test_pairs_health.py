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


class HealthBaselineSourceTest(SimpleTestCase):   # claude code changed: F-17 — drift must be measured against the canonical seed
    def _session(self, seed_ok, beta0=None):
        from types import SimpleNamespace
        seed = SimpleNamespace(beta0=beta0) if beta0 is not None else None
        return SimpleNamespace(seed_ok=seed_ok, seed=seed, pair_config=SimpleNamespace(hedge_ratio=1.625))

    def test_validated_canonical_seed_is_the_baseline(self):
        from bot.core.pairs_bot_runner import _health_baseline_beta
        self.assertEqual(_health_baseline_beta(self._session(True, beta0=0.802)), (0.802, "CANONICAL_SEED"))

    def test_legacy_ols_used_only_without_a_validated_seed_and_labelled(self):
        from bot.core.pairs_bot_runner import _health_baseline_beta
        self.assertEqual(_health_baseline_beta(self._session(False, beta0=0.802)), (1.625, "LEGACY_OLS_HEDGE_RATIO"))
        self.assertEqual(_health_baseline_beta(self._session(True, beta0=None)), (1.625, "LEGACY_OLS_HEDGE_RATIO"))

    def test_baseline_choice_changes_the_verdict(self):
        # Same live beta: a sign reversal vs the legacy OLS (+1.625 → −0.307, the real MINA/ONG case)
        # is not a reversal vs a canonical seed of −0.25. The baseline matters, so it must be canonical.
        from bot.core.pairs_bot_runner import _health_baseline_beta
        legacy = evaluate_pair_health("P", _health_baseline_beta(self._session(False))[0], -0.307, True)
        canon = evaluate_pair_health("P", _health_baseline_beta(self._session(True, beta0=-0.25))[0], -0.307, True)
        self.assertTrue(legacy.beta_sign_reversed)
        self.assertFalse(canon.beta_sign_reversed)

    def test_never_raises_on_a_seed_without_a_numeric_beta(self):   # claude code changed: F-17 — runs mid entry path; must not raise
        from types import SimpleNamespace
        from unittest import mock
        from bot.core.pairs_bot_runner import _health_baseline_beta
        for seed in (mock.Mock(data_version="d" * 64), SimpleNamespace(beta0=float("nan")), SimpleNamespace(beta0=None)):
            s = SimpleNamespace(seed_ok=True, seed=seed, pair_config=SimpleNamespace(hedge_ratio=1.625))
            self.assertEqual(_health_baseline_beta(s), (1.625, "LEGACY_OLS_HEDGE_RATIO"))
