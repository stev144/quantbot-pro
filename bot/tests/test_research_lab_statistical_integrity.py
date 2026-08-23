# claude code changed: new file — Research Lab: Statistical Integrity
# Hardening & Semantic Consistency Audit. Regression tests for the 6 real
# gaps found while tracing the exact "6-hour forward return" scenario
# through the full pipeline (see the final engineering report for the
# root-cause diagnosis). Deliberately does NOT re-test the 7 bugs already
# covered by test_research_lab_conditional_hypothesis.py — this file only
# covers what's new in this pass.

import numpy as np
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from bot.research_lab.interpreter import suggest_spec
from bot.research_lab.models import ResearchExperiment
from bot.research_lab.orchestrator import plan_experiment, run_experiment
from bot.research_lab.spec import ResearchSpec, validate_spec
from bot.research_lab.tools.conditional_tools import (
    InsufficientEventsError, _count_episodes, run_conditional_test,
)
from bot.research_lab.verdict import compute_verdict_conditional


# ─────────────────────────────────────────────────────────────
# The exact scenario from this brief's section 1 — end to end, never
# silently substituting a supported horizon for the requested one.
# ─────────────────────────────────────────────────────────────

class SixHourScenarioNeverSilentlySubstitutedTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="integrity1", password="x")
        self.client.login(username="integrity1", password="x")

    def test_the_exact_reported_hypothesis_is_blocked_not_silently_run_at_4h(self):
        text = (
            "When BTC/USDT 1-hour RSI falls below 30, the subsequent 6-hour "
            "forward return is significantly higher than normal, indicating "
            "a short-term mean reversion effect."
        )
        suggested = suggest_spec(text)
        self.assertEqual(suggested.hypothesis_type, "conditional")
        self.assertEqual(suggested.conditions[0], {"feature": "rsi", "operator": "<", "threshold": 30.0})
        self.assertEqual(suggested.target.get("horizon"), 6)  # claude code changed: the interpreter must detect the REAL requested horizon, not silently drop to a supported one itself

        # A student cannot submit horizon=6 through the real form (the
        # <select> only offers 1/4/24 — see formalize.html) but a defensive
        # check belongs at validation too, since the interpreter's own
        # suggestion is never trusted blindly.
        result = validate_spec(suggested)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("horizon=6" in e for e in result.errors))

        # End-to-end: even if horizon=6 somehow reached structured_spec
        # (e.g. a future API client bypassing the HTML form), planning must
        # block it — it must NEVER reach RUNNING/COMPLETED with a
        # substituted 4h result.
        experiment = ResearchExperiment.objects.create(
            student=self.user, hypothesis_text=text, structured_spec=suggested.to_dict(),
        )
        plan_experiment(experiment)
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "BLOCKED")
        self.assertIn("horizon=6", experiment.error_message)

        run_experiment(experiment)  # claude code changed: run_experiment() only proceeds from PLANNED — must be a no-op here
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "BLOCKED")
        self.assertEqual(experiment.statistical_results, {})  # claude code changed: no evidence of any kind, certainly not a 4h result

    def test_formalize_form_surfaces_an_explicit_unsupported_horizon_notice(self):
        """claude code changed: gap #1 — before this fix, a detected-but-
        unsupported horizon rendered a silently-blank <select> with no
        explanation at all. The GET-rendered formalize page must now say,
        explicitly, that 6h was seen but is unsupported."""
        text = (
            "When BTC/USDT 1-hour RSI falls below 30, the subsequent 6-hour "
            "forward return is significantly higher than normal."
        )
        experiment = ResearchExperiment.objects.create(student=self.user, hypothesis_text=text)
        response = self.client.get(reverse("research_lab_formalize", kwargs={"experiment_id": experiment.id}))
        self.assertContains(response, "6-hour forward return")
        self.assertContains(response, "cannot be tested")

    def test_formalize_form_says_nothing_extra_when_horizon_is_supported(self):
        text = "When BTC/USDT RSI falls below 30, the subsequent 24-hour forward return is higher."
        experiment = ResearchExperiment.objects.create(student=self.user, hypothesis_text=text)
        response = self.client.get(reverse("research_lab_formalize", kwargs={"experiment_id": experiment.id}))
        self.assertNotContains(response, "cannot be tested")


# ─────────────────────────────────────────────────────────────
# gap #2/#3 — the verdict-layer sample-size gate must check qualifying
# EVENT count, not total dataset size, and INSUFFICIENT_DATA must be
# actually reachable.
# ─────────────────────────────────────────────────────────────

class ConditionalSampleSizeGateTest(SimpleTestCase):

    def test_huge_total_dataset_with_few_qualifying_events_is_insufficient_not_supported(self):
        """claude code changed: the exact gap #2 scenario — a dataset of
        49,993 total rows but only 10 qualifying (condition-true)
        observations must NOT clear a sample-size floor calibrated for
        total dataset size."""
        evidence = {
            "metric": "conditional_event_test",
            "sample_size": 49993,  # claude code changed: this alone used to be enough to clear MIN_SAMPLE_SIZE=1000 regardless of event count
            "n_condition_true": 10,
            "n_condition_false": 49983,
            "mean_return_when_true": 0.02,
            "mean_return_when_false": 0.001,
            "observed_diff": 0.019,
            "block_permutation_p_value": 0.01,
        }
        result = compute_verdict_conditional(evidence, hypothesized_direction="positive")
        self.assertEqual(result.verdict, "INSUFFICIENT_DATA")

    def test_sufficient_events_with_significant_effect_reaches_a_confident_verdict(self):
        evidence = {
            "metric": "conditional_event_test",
            "sample_size": 49993,
            "n_condition_true": 1247,
            "n_condition_false": 48746,
            "mean_return_when_true": 0.02,
            "mean_return_when_false": 0.001,
            "observed_diff": 0.019,
            "block_permutation_p_value": 0.005,
        }
        result = compute_verdict_conditional(evidence, hypothesized_direction="positive")
        self.assertIn(result.verdict, ("SUPPORTED", "PARTIALLY_SUPPORTED"))

    def test_insufficient_events_flag_returns_insufficient_data_directly(self):
        result = compute_verdict_conditional(None, hypothesized_direction="positive", insufficient_events=True)
        self.assertEqual(result.verdict, "INSUFFICIENT_DATA")

    def test_unresolved_condition_still_reaches_requires_review_not_insufficient_data(self):
        """claude code changed: these are two DIFFERENT failure modes and
        must stay distinguishable — "never resolved" vs "resolved but too
        few events" — same distinction test_research_lab_conditional_
        hypothesis.py's VerdictIntegrityTest already covers for the
        default insufficient_events=False case; asserted again here
        explicitly against the new parameter for regression safety."""
        result = compute_verdict_conditional(None, hypothesized_direction="positive", insufficient_events=False)
        self.assertEqual(result.verdict, "REQUIRES_REVIEW")


class InsufficientEventsEndToEndTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="integrity2", password="x")

    def test_an_extremely_rare_condition_produces_insufficient_data_not_requires_review(self):
        """claude code changed: gap #3 end-to-end, real data, no mocking.
        RSI < 1 essentially never happens in real BTC/USDT history —
        exercises run_conditional_test()'s real InsufficientEventsError
        path and confirms the orchestrator routes it to INSUFFICIENT_DATA."""
        spec = ResearchSpec(
            hypothesis_text="When BTC RSI falls below 1, returns are higher.",
            asset="BTC/USDT", timeframe="1h", hypothesis_type="conditional",
            conditions=[{"feature": "rsi", "operator": "<", "threshold": 1}],
            target={"type": "forward_return", "horizon": 24}, direction="positive", risk_tier="LOW",
        )
        experiment = ResearchExperiment.objects.create(student=self.user, hypothesis_text=spec.hypothesis_text, structured_spec=spec.to_dict())
        plan_experiment(experiment)
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "PLANNED")

        run_experiment(experiment)
        experiment.refresh_from_db()

        self.assertEqual(experiment.status, "COMPLETED")  # claude code changed: a real, honest INSUFFICIENT_DATA verdict IS a completed experiment, not a FAILED one
        self.assertEqual(experiment.verdict, "INSUFFICIENT_DATA")
        self.assertNotEqual(experiment.verdict, "REJECTED")  # claude code changed: the exact property brief section 10 requires


# ─────────────────────────────────────────────────────────────
# gap #4/#5/#6 — evidence completeness: pct qualifying, median, FDR (m=1
# identity), episode count.
# ─────────────────────────────────────────────────────────────

class ConditionalEvidenceCompletenessTest(TestCase):

    def test_run_conditional_test_exposes_the_full_evidence_set_on_real_data(self):
        result = run_conditional_test(asset="BTC/USDT", feature_name="rsi", operator="<", threshold=30, horizon=24, random_seed=42)

        self.assertIn("pct_condition_true", result)
        self.assertAlmostEqual(result["pct_condition_true"], result["n_condition_true"] / result["sample_size"], places=6)

        self.assertIn("median_return_when_true", result)
        self.assertIn("median_return_when_false", result)

        self.assertIn("observed_direction", result)
        self.assertIn(result["observed_direction"], ("positive", "negative", "neutral"))

        self.assertIn("n_condition_episodes", result)
        self.assertGreater(result["n_condition_episodes"], 0)
        self.assertLessEqual(result["n_condition_episodes"], result["n_condition_true"])  # claude code changed: an episode is >=1 candle, so episode count can never exceed candle count

        # claude code changed: gap #5 — for a single condition (m=1 family),
        # FDR-adjusted p is EXACTLY the raw block-permutation p (Benjamini-
        # Hochberg identity at m=1), not skipped.
        self.assertEqual(result["fdr_adjusted_p_value"], result["block_permutation_p_value"])
        self.assertEqual(result["passes_fdr"], result["block_permutation_p_value"] < 0.05)

    def test_episode_count_less_than_or_equal_to_raw_candle_count_for_a_clustered_condition(self):
        """claude code changed: a condition that tends to persist for
        several consecutive candles (RSI staying under a threshold across a
        multi-hour dip) should show n_condition_episodes meaningfully lower
        than n_condition_true — verifies the metric isn't just echoing the
        candle count back."""
        result = run_conditional_test(asset="BTC/USDT", feature_name="rsi", operator="<", threshold=40, horizon=4, random_seed=42)
        self.assertLess(result["n_condition_episodes"], result["n_condition_true"])


class EpisodeCountingUnitTest(SimpleTestCase):

    def test_counts_contiguous_runs_not_raw_true_count(self):
        mask = np.array([True, True, True, False, False, True, True, False, True])
        self.assertEqual(_count_episodes(mask), 3)  # claude code changed: [T,T,T] [T,T] [T] = 3 episodes, 6 raw True candles

    def test_all_false_is_zero_episodes(self):
        mask = np.array([False, False, False])
        self.assertEqual(_count_episodes(mask), 0)

    def test_all_true_is_one_episode(self):
        mask = np.array([True, True, True, True])
        self.assertEqual(_count_episodes(mask), 1)

    def test_empty_mask_is_zero_episodes(self):
        self.assertEqual(_count_episodes(np.array([])), 0)


# ─────────────────────────────────────────────────────────────
# gap #14 (brief-wide invariant, re-verified for the new fields specifically)
# — every number the explanation/results/report layers display for a
# conditional experiment must trace back to the one stored evidence object.
# ─────────────────────────────────────────────────────────────

class ConditionalEvidenceNumberFidelityTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="integrity3", password="x")
        self.client.login(username="integrity3", password="x")

    def test_pct_qualifying_and_direction_in_explanation_match_stored_evidence(self):
        spec = ResearchSpec(
            hypothesis_text="When BTC RSI falls below 30, forward return is higher.",
            asset="BTC/USDT", timeframe="1h", hypothesis_type="conditional",
            conditions=[{"feature": "rsi", "operator": "<", "threshold": 30}],
            target={"type": "forward_return", "horizon": 24}, direction="positive", risk_tier="LOW",
        )
        experiment = ResearchExperiment.objects.create(student=self.user, hypothesis_text=spec.hypothesis_text, structured_spec=spec.to_dict())
        plan_experiment(experiment)
        experiment.refresh_from_db()
        run_experiment(experiment)
        experiment.refresh_from_db()

        stat = experiment.statistical_results
        if stat.get("metric") != "conditional_event_test":
            self.skipTest("condition did not produce a completed conditional test on this data slice")

        observed_dir = stat["observed_direction"]
        self.assertIn(observed_dir, experiment.ai_interpretation)

        results_response = self.client.get(reverse("research_lab_results", kwargs={"experiment_id": experiment.id}))
        self.assertContains(results_response, f"{stat['n_condition_episodes']}")
        self.assertContains(results_response, f"{stat['pct_condition_true']:.2f}")

        report_response = self.client.get(reverse("research_lab_report", kwargs={"experiment_id": experiment.id}))
        report_body = report_response.content.decode()
        self.assertIn(str(stat["n_condition_episodes"]), report_body)
        self.assertIn(observed_dir, report_body)
