# claude code changed: new file — Conditional Hypothesis Integrity &
# Experiment Provenance fix. Regression tests for the 7 bugs found in the
# audit (see the final engineering report for the full diagnosis table).
# Covers the brief's own required Tests 1-7 (Test 8 is "run the existing
# suites" — done separately via manage.py test, reported in the final
# report, not itself a new test case).

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from bot.research_lab.interpreter import suggest_spec, explain_evidence, describe_executed_spec
from bot.research_lab.models import ResearchExperiment
from bot.research_lab.orchestrator import plan_experiment, run_experiment
from bot.research_lab.spec import ResearchSpec, validate_spec
from bot.research_lab.verdict import compute_verdict_conditional


# ─────────────────────────────────────────────────────────────
# Test 1 — Conditional parsing
# ─────────────────────────────────────────────────────────────

class ConditionalParsingTest(SimpleTestCase):

    def test_brief_example_parses_as_a_fully_specified_conditional_hypothesis(self):
        text = "When BTC/USDT 1-hour RSI falls below 30, the subsequent 24-hour forward return is higher than normal."
        spec = suggest_spec(text)

        self.assertEqual(spec.hypothesis_type, "conditional")
        self.assertEqual(spec.asset, "BTC/USDT")
        self.assertEqual(len(spec.conditions), 1)
        self.assertEqual(spec.conditions[0]["feature"], "rsi")
        self.assertEqual(spec.conditions[0]["operator"], "<")
        self.assertEqual(spec.conditions[0]["threshold"], 30.0)
        self.assertEqual(spec.target.get("horizon"), 24)  # claude code changed: must be 24 (the forward-return horizon), not 1 (the RSI timeframe mention)
        self.assertEqual(spec.direction, "positive")  # claude code changed: "higher than normal" -> positive, must not be confused by "falls" inside the condition clause

    def test_above_operator_variants_all_map_to_greater_than(self):
        for phrase in ("RSI > 70", "RSI above 70", "RSI rises above 70"):
            spec = suggest_spec(f"Bitcoin: when {phrase}, returns fall.")
            self.assertEqual(spec.hypothesis_type, "conditional", phrase)
            self.assertEqual(spec.conditions[0]["operator"], ">", phrase)
            self.assertEqual(spec.conditions[0]["threshold"], 70.0, phrase)

    def test_below_operator_variants_all_map_to_less_than(self):
        for phrase in ("RSI < 30", "RSI below 30", "RSI falls below 30"):
            spec = suggest_spec(f"Bitcoin: when {phrase}, returns rise.")
            self.assertEqual(spec.hypothesis_type, "conditional", phrase)
            self.assertEqual(spec.conditions[0]["operator"], "<", phrase)
            self.assertEqual(spec.conditions[0]["threshold"], 30.0, phrase)

    def test_relative_threshold_condition_is_honestly_ambiguous_not_invented(self):
        """'volume > average' — the interpreter can recognize this is
        condition-shaped language but cannot resolve 'average' to a fixed
        number, and must not invent one."""
        spec = suggest_spec("Bitcoin: when volume is above average, price rises.")
        self.assertEqual(spec.hypothesis_type, "conditional")
        self.assertIsNone(spec.conditions[0]["operator"])
        self.assertIsNone(spec.conditions[0]["threshold"])
        self.assertIn("conditions", spec.ambiguous_fields)


# ─────────────────────────────────────────────────────────────
# Test 2 — Conditional is not silently converted to continuous testing
# ─────────────────────────────────────────────────────────────

class ConditionalNotConvertedToFeatureTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="conduser1", password="x")

    def test_conditional_experiment_calls_run_conditional_test_never_run_statistical_test(self):
        spec = ResearchSpec(
            hypothesis_text="When BTC/USDT RSI < 30, forward return is higher.",
            asset="BTC/USDT", timeframe="1h", hypothesis_type="conditional",
            conditions=[{"feature": "rsi", "operator": "<", "threshold": 30}],
            target={"type": "forward_return", "horizon": 24}, risk_tier="LOW",
        )
        experiment = ResearchExperiment.objects.create(student=self.user, hypothesis_text=spec.hypothesis_text, structured_spec=spec.to_dict())

        plan_experiment(experiment)
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "PLANNED")

        run_experiment(experiment)
        experiment.refresh_from_db()

        tools_called = {entry["tool_name"] for entry in experiment.tool_call_log}
        self.assertIn("run_conditional_test", tools_called)
        self.assertNotIn("run_statistical_test", tools_called)  # claude code changed: the exact assertion Bug 1/2 requires
        self.assertNotIn("run_fdr_correction", tools_called)  # claude code changed: no FDR tool exists for conditional evidence in this pass
        self.assertEqual(experiment.statistical_results.get("metric"), "conditional_event_test")


# ─────────────────────────────────────────────────────────────
# Test 3 — Horizon integrity
# ─────────────────────────────────────────────────────────────

class HorizonIntegrityTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="conduser2", password="x")

    def test_explanation_and_report_describe_the_executed_horizon_not_original_wording(self):
        """Original text says 'within 12 hours' but the CONFIRMED/EXECUTED
        horizon (what the student actually submitted on the Formalize
        form) is 24 — every generated description must say 24, never 12."""
        original_text = "Bitcoin RSI predicts returns within 12 hours."
        spec = ResearchSpec(
            hypothesis_text=original_text,
            asset="BTC/USDT", timeframe="1h", hypothesis_type="feature",
            features=["rsi"], target={"type": "forward_return", "horizon": 24}, risk_tier="LOW",
        )
        experiment = ResearchExperiment.objects.create(student=self.user, hypothesis_text=original_text, structured_spec=spec.to_dict())

        plan_experiment(experiment)
        experiment.refresh_from_db()
        run_experiment(experiment)
        experiment.refresh_from_db()

        self.assertEqual(experiment.research_plan["spec"]["target"]["horizon"], 24)
        # claude code changed: check for the specific stale PHRASE, not the
        # bare digit "12" — a real p-value elsewhere in the explanation
        # (e.g. "0.8812") legitimately contains "12" as a substring, which
        # made this assertion falsely fail on a correct explanation.
        self.assertNotIn("12 hours", experiment.ai_interpretation)
        self.assertNotIn("within 12", experiment.ai_interpretation)
        self.assertIn("24 candles", experiment.ai_interpretation)

    def test_describe_executed_spec_never_reads_original_hypothesis_text(self):
        spec = ResearchSpec(
            hypothesis_text="this text says 999 hours and must never appear below",
            asset="BTC/USDT", timeframe="1h", hypothesis_type="feature",
            features=["rsi"], target={"type": "forward_return", "horizon": 4},
        )
        description = describe_executed_spec(spec)
        self.assertIn("4", description)
        self.assertNotIn("999", description)


# ─────────────────────────────────────────────────────────────
# Test 4 — Original vs executed hypothesis
# ─────────────────────────────────────────────────────────────

class OriginalVsExecutedTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="conduser3", password="x")

    def test_original_text_is_preserved_unmodified_while_executed_spec_reflects_confirmation(self):
        original = "When RSI is low, BTC bounces."
        experiment = ResearchExperiment.objects.create(student=self.user, hypothesis_text=original)

        confirmed_spec = ResearchSpec(
            hypothesis_text=original, asset="BTC/USDT", timeframe="1h", hypothesis_type="conditional",
            conditions=[{"feature": "rsi", "operator": "<", "threshold": 25}],  # claude code changed: student corrected the suggested threshold
            target={"type": "forward_return", "horizon": 4}, direction="positive",
        )
        experiment.structured_spec = confirmed_spec.to_dict()
        experiment.save()

        plan_experiment(experiment)
        experiment.refresh_from_db()

        self.assertEqual(experiment.hypothesis_text, original)  # claude code changed: immutable, unchanged
        self.assertEqual(experiment.research_plan["spec"]["conditions"][0]["threshold"], 25)  # claude code changed: executed spec reflects the STUDENT'S confirmed value, not any value guessable from the original text


# ─────────────────────────────────────────────────────────────
# Test 5 — Unsupported horizon rejected before execution
# ─────────────────────────────────────────────────────────────

class UnsupportedHorizonTest(SimpleTestCase):

    def test_horizon_12_is_rejected_at_schema_validation(self):
        spec = ResearchSpec(
            hypothesis_text="test", asset="BTC/USDT", timeframe="1h",
            features=["rsi"], target={"type": "forward_return", "horizon": 12},
        )
        result = validate_spec(spec)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("horizon" in e and "12" in e for e in result.errors))

    def test_supported_horizons_are_accepted(self):
        for horizon in (1, 4, 24):
            spec = ResearchSpec(
                hypothesis_text="test", asset="BTC/USDT", timeframe="1h",
                features=["rsi"], target={"type": "forward_return", "horizon": horizon},
            )
            result = validate_spec(spec)
            self.assertTrue(result.is_valid, f"horizon={horizon} should be valid: {result.errors}")


class UnsupportedHorizonAtFormalizeTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="conduser4", password="x")
        self.client.login(username="conduser4", password="x")

    def test_formalize_view_rejects_horizon_12_with_a_clear_message_before_planning(self):
        experiment = ResearchExperiment.objects.create(student=self.user, hypothesis_text="test hypothesis")
        response = self.client.post(reverse("research_lab_formalize", kwargs={"experiment_id": experiment.id}), data={
            "hypothesis_type": "feature", "asset": "BTC/USDT", "timeframe": "1h",
            "feature_name": "rsi", "horizon": "12", "risk_tier": "LOW",
        })
        self.assertEqual(response.status_code, 200)  # claude code changed: re-renders with errors, never silently proceeds to plan
        self.assertContains(response, "horizon")
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "PENDING")  # claude code changed: never reaches PLANNED on a rejected spec


# ─────────────────────────────────────────────────────────────
# Test 6 — Evidence provenance
# ─────────────────────────────────────────────────────────────

class EvidenceProvenanceTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="conduser5", password="x")

    def test_results_report_and_explanation_all_use_the_same_stored_ic_value(self):
        spec = ResearchSpec(
            hypothesis_text="BTC RSI predicts returns", asset="BTC/USDT", timeframe="1h",
            hypothesis_type="feature", features=["rsi"], target={"type": "forward_return", "horizon": 4}, risk_tier="LOW",
        )
        experiment = ResearchExperiment.objects.create(student=self.user, hypothesis_text=spec.hypothesis_text, structured_spec=spec.to_dict())
        plan_experiment(experiment)
        experiment.refresh_from_db()
        run_experiment(experiment)
        experiment.refresh_from_db()

        stored_ic = experiment.statistical_results["ic"]
        formatted_ic = f"{stored_ic:+.4f}"
        self.assertIn(formatted_ic, experiment.ai_interpretation)  # claude code changed: the explanation's IC text must be the SAME stored value, formatted — never independently recomputed or approximated

        self.client.login(username="conduser5", password="x")
        results_response = self.client.get(reverse("research_lab_results", kwargs={"experiment_id": experiment.id}))
        report_response = self.client.get(reverse("research_lab_report", kwargs={"experiment_id": experiment.id}))
        # claude code changed: both pages render the identical stored ic value via {{ experiment.statistical_results.ic }} — floatformat:4 in results.html, raw in report.html — both derived from the one stored number
        self.assertContains(results_response, f"{stored_ic:.4f}")
        self.assertIn(str(stored_ic), report_response.content.decode())


# ─────────────────────────────────────────────────────────────
# Test 7 — Verdict integrity: a wrong/unexecuted test must never produce
# a confident REJECTED/SUPPORTED verdict
# ─────────────────────────────────────────────────────────────

class VerdictIntegrityTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="conduser6", password="x")

    def test_conditional_hypothesis_with_no_executed_test_gets_requires_review_not_rejected(self):
        result = compute_verdict_conditional(None, hypothesized_direction="positive")
        self.assertEqual(result.verdict, "REQUIRES_REVIEW")
        self.assertNotIn(result.verdict, ("REJECTED", "SUPPORTED"))  # claude code changed: the exact property Bug 7 requires

    def test_feature_evidence_passed_to_conditional_verdict_fails_closed(self):
        """If the wrong evidence shape (a continuous IC result) were ever
        passed to the conditional verdict function by mistake, it must not
        produce a confident verdict on a mismatched research question."""
        wrong_shape_evidence = {"metric": "information_coefficient", "ic": 0.08, "sample_size": 50000}
        result = compute_verdict_conditional(wrong_shape_evidence)
        self.assertEqual(result.verdict, "REQUIRES_REVIEW")

    def test_conditional_experiment_with_unresolved_condition_reaches_requires_review(self):
        """End-to-end: a student confirms hypothesis_type=conditional but
        leaves the threshold blank — validate_spec() actually blocks this
        at planning (see UnsupportedHorizonTest-style coverage), but if it
        ever reached execution, the verdict must not be a confident one."""
        spec = ResearchSpec(
            hypothesis_text="test", asset="BTC/USDT", timeframe="1h", hypothesis_type="conditional",
            conditions=[{"feature": "rsi", "operator": None, "threshold": None}],
            target={"type": "forward_return", "horizon": 4},
        )
        validation = validate_spec(spec)
        self.assertFalse(validation.is_valid)  # claude code changed: confirms this case is actually blocked before it could ever reach the verdict engine at all
