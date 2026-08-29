# claude code changed: new file — Advanced Quant Research Capability
# Architecture. Covers the first production-ready advanced capability
# (Cointegration & Pairs Research) end to end: backend enforcement without
# UI, research-integrity invariants (subscription never alters
# statistics), and the capability's own deterministic/boundary/invalid-
# input/compute-budget/failure-path tests section 18 requires for every
# engine actually exposed. Uses real project data (no mocking), matching
# this repo's existing testing convention.

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from bot.research_lab.entitlements import ResearchEntitlementService
from bot.research_lab.models import ResearchExperiment, ResearchSubscription
from bot.research_lab.orchestrator import plan_experiment, run_experiment
from bot.research_lab.policy_gate import MAX_PAIRS_PER_EXPERIMENT
from bot.research_lab.spec import ResearchSpec, validate_spec
from bot.research_lab.tools.research_tools import run_cointegration_test
from bot.research_lab.verdict import compute_verdict_pairs


def _pairs_spec(asset="AVAX/USDT", asset_b="ATOM/USDT", **overrides):
    defaults = dict(
        hypothesis_text=f"Are {asset} and {asset_b} cointegrated?",
        asset=asset, asset_b=asset_b, timeframe="1h", hypothesis_type="pairs", risk_tier="MEDIUM",
    )
    defaults.update(overrides)
    return ResearchSpec(**defaults)


# ─────────────────────────────────────────────────────────────
# Backend enforcement without UI (section 12/18)
# ─────────────────────────────────────────────────────────────

class BackendEnforcementTest(TestCase):

    def setUp(self):
        self.free_user = User.objects.create_user(username="pairsfree1", password="x")
        self.pro_user = User.objects.create_user(username="pairspro1", password="x")
        ResearchSubscription.objects.create(user=self.pro_user, tier="PRO", status="ACTIVE", expires_at=None)

    def test_free_user_manually_posting_pairs_hypothesis_is_rejected_at_http_level(self):
        """claude code changed: the exact section 12 test — a free user
        POSTing hypothesis_type='pairs' directly (bypassing the disabled
        radio button in the UI entirely) must be rejected by the view
        itself, not merely visually prevented."""
        self.client.login(username="pairsfree1", password="x")
        experiment = ResearchExperiment.objects.create(student=self.free_user, hypothesis_text="Are AVAX and ATOM cointegrated?")
        response = self.client.post(reverse("research_lab_formalize", kwargs={"experiment_id": experiment.id}), data={
            "hypothesis_type": "pairs", "asset": "AVAX/USDT", "asset_b": "ATOM/USDT", "timeframe": "1h", "risk_tier": "MEDIUM",
        })
        self.assertEqual(response.status_code, 200)  # claude code changed: re-renders with an error, never redirects to the plan screen
        self.assertContains(response, "Pro Research")
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "PENDING")  # claude code changed: never even reaches structured_spec, let alone PLANNED

    def test_pro_user_posting_pairs_hypothesis_succeeds_at_http_level(self):
        self.client.login(username="pairspro1", password="x")
        experiment = ResearchExperiment.objects.create(student=self.pro_user, hypothesis_text="Are AVAX and ATOM cointegrated?")
        response = self.client.post(reverse("research_lab_formalize", kwargs={"experiment_id": experiment.id}), data={
            "hypothesis_type": "pairs", "asset": "AVAX/USDT", "asset_b": "ATOM/USDT", "timeframe": "1h", "risk_tier": "MEDIUM",
        })
        self.assertEqual(response.status_code, 302)  # claude code changed: redirects to the plan screen — a Pro subscriber IS authorized
        experiment.refresh_from_db()
        self.assertEqual(experiment.structured_spec.get("hypothesis_type"), "pairs")

    def test_plan_experiment_independently_blocks_a_free_user_even_if_the_view_were_bypassed(self):
        """claude code changed: defense in depth — simulates a
        structured_spec reaching PENDING through some path other than the
        formalize view's own POST handler (e.g. a future API), and
        confirms plan_experiment() still refuses to plan it. No tool call
        occurs."""
        spec = _pairs_spec()
        experiment = ResearchExperiment.objects.create(student=self.free_user, hypothesis_text=spec.hypothesis_text, structured_spec=spec.to_dict())
        plan_experiment(experiment)
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "BLOCKED")
        self.assertIn("Pro Research", experiment.error_message)
        self.assertEqual(experiment.tool_call_log, [])  # claude code changed: no tool ever ran

        run_experiment(experiment)  # claude code changed: run_experiment() only proceeds from PLANNED — must be a no-op
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "BLOCKED")
        self.assertEqual(experiment.statistical_results, {})

    def test_pro_user_reaches_completed_with_real_evidence(self):
        spec = _pairs_spec()
        experiment = ResearchExperiment.objects.create(student=self.pro_user, hypothesis_text=spec.hypothesis_text, structured_spec=spec.to_dict())
        plan_experiment(experiment)
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "PLANNED")
        self.assertEqual(experiment.capability_id, "cointegration_pairs_research")

        run_experiment(experiment)
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "COMPLETED")
        self.assertIn("is_cointegrated", experiment.statistical_results)
        tools_called = {entry["tool_name"] for entry in experiment.tool_call_log}
        self.assertIn("run_cointegration_test", tools_called)


# ─────────────────────────────────────────────────────────────
# Research integrity: subscription must never alter statistics (section 15)
# ─────────────────────────────────────────────────────────────

class SubscriptionNeverAltersStatisticsTest(TestCase):

    def test_tool_output_is_identical_regardless_of_who_or_whether_a_subscription_exists(self):
        """claude code changed: the exact section 15 invariant. Calls the
        SAME real cointegration test (a) directly, with no user/
        entitlement context at all, and (b) through a Pro-subscribed
        user's full orchestrator run, and asserts the numeric evidence is
        bit-identical. The entitlement layer sits ABOVE the engine and
        must never leak into it."""
        direct = run_cointegration_test(asset_a="LINK/USDT", asset_b="UNI/USDT")

        pro_user = User.objects.create_user(username="pairspro2", password="x")
        ResearchSubscription.objects.create(user=pro_user, tier="PRO", status="ACTIVE", expires_at=None)
        spec = _pairs_spec(asset="LINK/USDT", asset_b="UNI/USDT")
        experiment = ResearchExperiment.objects.create(student=pro_user, hypothesis_text=spec.hypothesis_text, structured_spec=spec.to_dict())
        plan_experiment(experiment)
        run_experiment(experiment)
        experiment.refresh_from_db()

        via_orchestrator = experiment.statistical_results
        self.assertEqual(direct["coint_pvalue"], via_orchestrator["coint_pvalue"])
        self.assertEqual(direct["adf_pvalue"], via_orchestrator["adf_pvalue"])
        self.assertEqual(direct["hedge_ratio"], via_orchestrator["hedge_ratio"])
        self.assertEqual(direct["half_life_hours"], via_orchestrator["half_life_hours"])
        self.assertEqual(direct["is_cointegrated"], via_orchestrator["is_cointegrated"])

    def test_same_pair_tested_twice_produces_identical_evidence(self):
        """claude code changed: determinism test — cointegration_engine.py
        has no randomness, so the exact same call must reproduce exactly."""
        first = run_cointegration_test(asset_a="BNB/USDT", asset_b="SOL/USDT")
        second = run_cointegration_test(asset_a="BNB/USDT", asset_b="SOL/USDT")
        self.assertEqual(first, second)


# ─────────────────────────────────────────────────────────────
# Capability-specific tests (section 18: deterministic, boundary, invalid
# input, sample-size, data availability, compute-budget, failure-path)
# ─────────────────────────────────────────────────────────────

class BoundaryAndInvalidInputTest(TestCase):

    def test_asset_b_equal_to_asset_is_rejected(self):
        spec = _pairs_spec(asset="AVAX/USDT", asset_b="AVAX/USDT")
        result = validate_spec(spec)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("distinct" in e for e in result.errors))

    def test_missing_asset_b_is_rejected(self):
        spec = _pairs_spec(asset_b=None)
        result = validate_spec(spec)
        self.assertFalse(result.is_valid)

    def test_unsupported_asset_b_is_rejected(self):
        spec = _pairs_spec(asset_b="NOTREAL/USDT")
        result = validate_spec(spec)
        self.assertFalse(result.is_valid)

    def test_pairs_hypothesis_does_not_require_a_forward_return_horizon(self):
        """claude code changed: the whole point of scoping the target/
        horizon validation block away from hypothesis_type=='pairs' —
        cointegration has no forward-return target at all."""
        spec = _pairs_spec()  # no target set
        result = validate_spec(spec)
        self.assertTrue(result.is_valid, result.errors)


class ComputeBudgetTest(TestCase):

    def test_max_pairs_per_experiment_is_one(self):
        self.assertEqual(MAX_PAIRS_PER_EXPERIMENT, 1)

    def test_pro_subscription_does_not_expand_the_pairs_budget(self):
        """claude code changed: section 13 — 'a Pro user gets access to
        the capability, not unlimited computation'. ResearchSpec.asset_b
        is a single field by construction, so there is structurally no
        way to name more than one pair per experiment regardless of tier."""
        pro_user = User.objects.create_user(username="pairspro3", password="x")
        ResearchSubscription.objects.create(user=pro_user, tier="PRO", status="ACTIVE", expires_at=None)
        spec = _pairs_spec()
        self.assertFalse(hasattr(spec, "asset_c"))  # claude code changed: no third-asset field exists at all, at any tier


class FailurePathTest(TestCase):

    def setUp(self):
        self.pro_user = User.objects.create_user(username="pairspro4", password="x")
        ResearchSubscription.objects.create(user=self.pro_user, tier="PRO", status="ACTIVE", expires_at=None)

    def test_missing_ohlcv_for_asset_b_blocks_at_planning_not_at_execution(self):
        spec = _pairs_spec(asset="AVAX/USDT", asset_b="NOTREAL/USDT")
        # claude code changed: bypass validate_spec()'s own asset_b-universe
        # check by constructing the spec dict directly, to isolate and test
        # data_availability's independent OHLCV-file check specifically —
        # NOTREAL/USDT is not in SUPPORTED_ASSETS either, which already
        # covers the schema-level rejection (BoundaryAndInvalidInputTest);
        # this test targets the second, independent data-layer gate.
        experiment = ResearchExperiment.objects.create(student=self.pro_user, hypothesis_text=spec.hypothesis_text, structured_spec=spec.to_dict())
        plan_experiment(experiment)
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "BLOCKED")


# ─────────────────────────────────────────────────────────────
# compute_verdict_pairs() — direct unit tests, all real evidence shapes
# ─────────────────────────────────────────────────────────────

class ComputeVerdictPairsTest(TestCase):

    def test_no_evidence_requires_review(self):
        result = compute_verdict_pairs(None)
        self.assertEqual(result.verdict, "REQUIRES_REVIEW")

    def test_wrong_evidence_shape_fails_closed(self):
        result = compute_verdict_pairs({"metric": "information_coefficient", "ic": 0.1})
        self.assertEqual(result.verdict, "REQUIRES_REVIEW")

    def test_insufficient_aligned_data_is_insufficient_data(self):
        result = compute_verdict_pairs({
            "is_cointegrated": False, "passes_filters": False,
            "reject_reason": "insufficient aligned data (300 rows)",
        })
        self.assertEqual(result.verdict, "INSUFFICIENT_DATA")

    def test_ols_failure_is_invalid_research(self):
        result = compute_verdict_pairs({
            "is_cointegrated": False, "passes_filters": False,
            "reject_reason": "OLS failed: singular matrix",
        })
        self.assertEqual(result.verdict, "INVALID_RESEARCH")

    def test_not_cointegrated_is_rejected(self):
        """claude code changed: real evidence shape, AVAX/ATOM — re-verified
        against the expanded 50-symbol/5-year universe (was BNB/SOL against
        the old 20-symbol universe; real cointegration test outcomes are
        universe/data-dependent and were re-checked directly, not assumed,
        when this test started failing after the universe expansion:
        AVAX/ATOM is genuinely NOT cointegrated on the current data,
        adf_pvalue=0.23 — and BNB/SOL, which used to demonstrate this
        branch, is now the pair used by the PARTIALLY_SUPPORTED test below)."""
        result = compute_verdict_pairs(run_cointegration_test(asset_a="AVAX/USDT", asset_b="ATOM/USDT"))
        self.assertEqual(result.verdict, "REJECTED")

    def test_cointegrated_but_half_life_too_long_is_partially_supported(self):
        """claude code changed: real evidence shape, BNB/SOL — re-verified
        against the expanded 50-symbol/5-year universe (was AVAX/ATOM
        against the old 20-symbol universe; see test_not_cointegrated_is_rejected's
        comment for why these two pairs swapped roles): is_cointegrated=True,
        passes_filters=False (half-life 252 candles > 120 max)."""
        result = compute_verdict_pairs(run_cointegration_test(asset_a="BNB/USDT", asset_b="SOL/USDT"))
        self.assertEqual(result.verdict, "PARTIALLY_SUPPORTED")

    def test_cointegrated_and_passes_filters_is_supported(self):
        """claude code changed: synthetic evidence — no pair in this
        project's fixed 20-symbol universe happened to clear the half-life
        filter as of this test's writing, so the SUPPORTED branch is
        exercised directly against a hand-built, schema-accurate dict
        rather than skipped."""
        result = compute_verdict_pairs({
            "is_cointegrated": True, "passes_filters": True,
            "coint_pvalue": 0.001, "adf_pvalue": 0.0001, "half_life_hours": 48.0,
            "hedge_ratio": 1.2, "reject_reason": "",
        })
        self.assertEqual(result.verdict, "SUPPORTED")
