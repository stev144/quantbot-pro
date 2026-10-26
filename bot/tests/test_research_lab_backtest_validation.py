# claude code changed: 2026-10-26 — new file: Backtest Validation capability (hypothesis_type "strategy_backtest"),
# background execution of Research Lab runs, and staff/billing -> PRO research tier.
# No mocking. Verdict/spec tests feed real evidence dicts to the pure functions; StrategyBacktestEndToEndTest runs the
# real backtester over BTC/USDT's full local history (slow: several minutes).

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from bot.research_lab.capability_registry import IMPLEMENTED_AND_READY, capability_for_hypothesis_type
from bot.research_lab.entitlements import ResearchEntitlementService
from bot.research_lab.models import ResearchExperiment, ResearchSubscription
from bot.research_lab.orchestrator import fail_if_interrupted, plan_experiment, run_experiment
from bot.research_lab.spec import ResearchSpec, validate_spec
from bot.research_lab.verdict import MIN_BACKTEST_TRADES, compute_verdict_backtest


def _evidence(**overrides):
    ev = {"metric": "strategy_backtest", "n_trades_with_net_r": 200, "mean_net_r": 0.12, "p_value_one_sided": 0.004,
          "bootstrap_ci95_mean_net_r": [0.04, 0.20], "lag1_autocorrelation": 0.02}
    ev.update(overrides)
    return ev


def _spec(**overrides):
    kw = dict(hypothesis_text="Does the strategy make money on BTC?", asset="BTC/USDT", timeframe="1h",
              hypothesis_type="strategy_backtest", risk_tier="MEDIUM", target={"type": "forward_return"})
    kw.update(overrides)
    return ResearchSpec(**kw)


class BacktestVerdictTest(TestCase):
    def test_positive_result_is_capped_at_observation(self):
        v = compute_verdict_backtest(_evidence())
        self.assertEqual(v.verdict, "OBSERVATION")
        text = " ".join(v.explanation)
        self.assertIn("in-sample only", text)
        self.assertIn("independent", text)

    def test_negative_mean_is_rejected(self):
        v = compute_verdict_backtest(_evidence(mean_net_r=-0.66, p_value_one_sided=1.0, bootstrap_ci95_mean_net_r=[-0.74, -0.59]))
        self.assertEqual(v.verdict, "REJECTED")

    def test_not_significant_is_inconclusive(self):
        self.assertEqual(compute_verdict_backtest(_evidence(p_value_one_sided=0.2)).verdict, "INCONCLUSIVE")

    def test_ci_touching_zero_is_inconclusive_even_when_t_test_passes(self):
        v = compute_verdict_backtest(_evidence(bootstrap_ci95_mean_net_r=[-0.01, 0.2]))
        self.assertEqual(v.verdict, "INCONCLUSIVE")
        self.assertIn("disagree", " ".join(v.explanation))

    def test_too_few_trades(self):
        v = compute_verdict_backtest(_evidence(n_trades_with_net_r=MIN_BACKTEST_TRADES - 1))
        self.assertEqual(v.verdict, "INSUFFICIENT_DATA")

    def test_missing_or_wrong_evidence_fails_closed(self):
        self.assertEqual(compute_verdict_backtest(None).verdict, "REQUIRES_REVIEW")
        self.assertEqual(compute_verdict_backtest({"ic": 0.1}).verdict, "REQUIRES_REVIEW")
        self.assertEqual(compute_verdict_backtest(_evidence(mean_net_r=None)).verdict, "INVALID_RESEARCH")


class BacktestSpecAndRegistryTest(TestCase):
    def test_spec_needs_no_forward_target(self):
        self.assertTrue(validate_spec(_spec()).is_valid, validate_spec(_spec()).errors)

    def test_only_positive_direction_allowed(self):
        self.assertTrue(validate_spec(_spec(direction="positive")).is_valid)
        result = validate_spec(_spec(direction="negative"))
        self.assertFalse(result.is_valid)
        self.assertIn("positive net expectancy", " ".join(result.errors))

    def test_capability_is_ready_and_mapped(self):
        cap = capability_for_hypothesis_type("strategy_backtest")
        self.assertEqual(cap.id, "backtest_validation")
        self.assertEqual(cap.engine_status, IMPLEMENTED_AND_READY)


class ResearchTierTest(TestCase):
    def test_staff_research_at_pro(self):
        staff = User.objects.create_user("owner", password="x", is_staff=True)
        self.assertEqual(ResearchEntitlementService.get_user_tier(staff), "PRO")

    @override_settings(BILLING_ENFORCED=False)
    def test_plain_user_unchanged_while_billing_is_off(self):
        user = User.objects.create_user("plain", password="x")
        self.assertEqual(ResearchEntitlementService.get_user_tier(user), "CORE")
        self.assertFalse(ResearchEntitlementService.can_access(user, "backtest_validation").allowed)

    @override_settings(BILLING_ENFORCED=True)
    def test_trial_user_is_pro_and_expired_trial_is_not(self):
        user = User.objects.create_user("trialist", password="x")
        self.assertEqual(ResearchEntitlementService.get_user_tier(user), "PRO")   # first check starts the 30-day trial
        sub = user.billing
        sub.trial_ends_at = timezone.now() - timedelta(seconds=1)
        sub.save()
        self.assertEqual(ResearchEntitlementService.get_user_tier(user), "CORE")

    @override_settings(BILLING_ENFORCED=False)
    def test_research_subscription_still_works(self):
        user = User.objects.create_user("legacy", password="x")
        ResearchSubscription.objects.create(user=user, tier="PRO", status="ACTIVE")
        self.assertEqual(ResearchEntitlementService.get_user_tier(user), "PRO")


class InterruptedRunTest(TestCase):
    def test_stale_running_experiment_is_marked_failed(self):
        user = User.objects.create_user("r", password="x")
        exp = ResearchExperiment.objects.create(student=user, hypothesis_text="x", structured_spec=_spec().to_dict(),
                                                status="RUNNING", started_at=timezone.now() - timedelta(hours=3))
        self.assertTrue(fail_if_interrupted(exp))
        exp.refresh_from_db()
        self.assertEqual(exp.status, "FAILED")
        self.assertIn("Interrupted", exp.error_message)

    def test_recent_running_experiment_is_left_alone(self):
        user = User.objects.create_user("r2", password="x")
        exp = ResearchExperiment.objects.create(student=user, hypothesis_text="x", structured_spec=_spec().to_dict(),
                                                status="RUNNING", started_at=timezone.now() - timedelta(minutes=5))
        self.assertFalse(fail_if_interrupted(exp))
        resp = self.client.get(reverse("research_lab_results", kwargs={"experiment_id": exp.id}))   # anonymous: login redirect
        self.assertEqual(resp.status_code, 302)

    def test_run_experiment_claims_atomically(self):
        user = User.objects.create_user("r3", password="x")
        exp = ResearchExperiment.objects.create(student=user, hypothesis_text="x", structured_spec=_spec().to_dict(),
                                                status="RUNNING", started_at=timezone.now())
        stale_copy = ResearchExperiment.objects.get(pk=exp.pk)
        stale_copy.status = "PLANNED"   # an in-memory copy that thinks it is still PLANNED
        run_experiment(stale_copy)      # must not run: the DB row is already RUNNING
        exp.refresh_from_db()
        self.assertEqual(exp.status, "RUNNING")
        self.assertFalse(exp.verdict)   # never ran: still the empty default


class FormalizeBacktestOptionTest(TestCase):
    def test_staff_can_choose_strategy_backtest(self):
        user = User.objects.create_user("owner", password="x", is_staff=True)
        self.client.login(username="owner", password="x")
        self.client.post(reverse("research_lab_dashboard"), data={"hypothesis_text": "Does the strategy make money on BTC?"})
        exp = ResearchExperiment.objects.get(student=user)
        page = self.client.get(reverse("research_lab_formalize", kwargs={"experiment_id": exp.id}))
        self.assertContains(page, 'value="strategy_backtest"')
        resp = self.client.post(reverse("research_lab_formalize", kwargs={"experiment_id": exp.id}), data={
            "hypothesis_type": "strategy_backtest", "asset": "BTC/USDT", "timeframe": "1h", "risk_tier": "LOW"})
        self.assertRedirects(resp, reverse("research_lab_plan", kwargs={"experiment_id": exp.id}))
        exp.refresh_from_db()
        self.assertEqual(exp.structured_spec["hypothesis_type"], "strategy_backtest")
        self.assertEqual(exp.structured_spec["risk_tier"], "MEDIUM")   # forced: run_backtest is MEDIUM-only

    @override_settings(BILLING_ENFORCED=False)
    def test_core_user_is_refused_by_the_backend(self):
        user = User.objects.create_user("core", password="x")
        self.client.login(username="core", password="x")
        self.client.post(reverse("research_lab_dashboard"), data={"hypothesis_text": "crafted"})
        exp = ResearchExperiment.objects.get(student=user)
        resp = self.client.post(reverse("research_lab_formalize", kwargs={"experiment_id": exp.id}), data={
            "hypothesis_type": "strategy_backtest", "asset": "BTC/USDT", "timeframe": "1h"})
        self.assertEqual(resp.status_code, 200)   # re-rendered with the entitlement error, not saved
        exp.refresh_from_db()
        self.assertFalse(exp.structured_spec)


class StrategyBacktestEndToEndTest(TestCase):
    """Real backtester, real BTC/USDT history, real statistics — slow (several minutes)."""

    def test_btc_strategy_backtest_completes_with_net_r_evidence(self):
        user = User.objects.create_user("owner", password="x", is_staff=True)
        spec = _spec()
        exp = ResearchExperiment.objects.create(student=user, hypothesis_text=spec.hypothesis_text, structured_spec=spec.to_dict())
        plan_experiment(exp)
        exp.refresh_from_db()
        self.assertEqual(exp.status, "PLANNED", exp.error_message)
        run_experiment(exp)
        exp.refresh_from_db()
        self.assertEqual(exp.status, "COMPLETED", exp.error_message)
        ev = exp.statistical_results
        self.assertEqual(ev["metric"], "strategy_backtest")
        self.assertTrue(ev["research_mode"])
        self.assertGreaterEqual(ev["n_trades_with_net_r"], MIN_BACKTEST_TRADES)   # research mode: the validation gate no longer blocks every trade
        lo, hi = ev["bootstrap_ci95_mean_net_r"]
        self.assertLessEqual(lo, ev["mean_net_r"])
        self.assertLessEqual(ev["mean_net_r"], hi)
        self.assertGreaterEqual(ev["max_drawdown_usd"], 0)
        self.assertEqual(exp.verdict, compute_verdict_backtest(ev).verdict)
        self.client.login(username="owner", password="x")
        page = self.client.get(reverse("research_lab_results", kwargs={"experiment_id": exp.id}))
        self.assertContains(page, "Mean net R / trade")
