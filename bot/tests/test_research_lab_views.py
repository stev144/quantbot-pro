# claude code changed: new file — Research Lab MVP, view-level coverage
# via the real Django test client (real HTTP requests, real session/auth,
# no mocking) — same convention as bot/tests/test_academy_models_and_views.py.

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from bot.research_lab.models import ResearchExperiment


class DashboardTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="labuser1", password="x")

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("research_lab_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_submitting_a_hypothesis_creates_an_experiment_and_redirects_to_formalize(self):
        self.client.login(username="labuser1", password="x")
        response = self.client.post(reverse("research_lab_dashboard"), data={"hypothesis_text": "Bitcoin falls after volume spikes"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ResearchExperiment.objects.filter(student=self.user).exists())

    def test_empty_hypothesis_shows_error_creates_nothing(self):
        self.client.login(username="labuser1", password="x")
        response = self.client.post(reverse("research_lab_dashboard"), data={"hypothesis_text": "   "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ResearchExperiment.objects.count(), 0)


class AssetSelectorGroupedByAssetClassTest(TestCase):
    # claude code changed: new — Crypto/Forex Navigation Discoverability.
    # Real HTTP request to the real formalize page, confirming the asset
    # dropdown is genuinely grouped by real registry asset_class, not just
    # that the view function returns the right context dict in isolation.

    def setUp(self):
        self.user = User.objects.create_user(username="groupedassetuser", password="x")
        self.client.login(username="groupedassetuser", password="x")
        self.experiment = ResearchExperiment.objects.create(student=self.user, hypothesis_text="test")

    def test_formalize_page_renders_crypto_and_forex_optgroups(self):
        resp = self.client.get(reverse("research_lab_formalize", kwargs={"experiment_id": self.experiment.id}))
        html = resp.content.decode()
        self.assertIn('<optgroup label="CRYPTO">', html)
        self.assertIn('<optgroup label="FOREX">', html)
        self.assertIn('<option value="EUR/USD"', html)
        self.assertIn('<option value="BTC/USDT"', html)

    def test_forex_asset_is_a_real_selectable_option_for_both_asset_and_asset_b(self):
        html = self.client.get(reverse("research_lab_formalize", kwargs={"experiment_id": self.experiment.id})).content.decode()
        # claude code changed: both the primary "Asset" <select> and the
        # pairs "Second asset (asset_b)" <select> must offer every Forex
        # symbol, not just the first one — proves the fix applies to both
        # dropdowns identified in the original bug report, not just one.
        self.assertEqual(html.count('value="EUR/USD"'), 2)
        self.assertEqual(html.count('value="GBP/USD"'), 2)

    def test_forex_symbol_survives_a_real_pairs_submission(self):
        """claude code changed: end-to-end proof, not just that the option
        renders — a POST selecting two real Forex symbols for a pairs
        hypothesis must be accepted by validate_spec() and saved, exactly
        like a crypto pairs submission already was before this task."""
        from bot.research_lab.models import ResearchSubscription
        ResearchSubscription.objects.create(user=self.user, tier="PRO", status="ACTIVE", expires_at=None)
        resp = self.client.post(
            reverse("research_lab_formalize", kwargs={"experiment_id": self.experiment.id}),
            data={
                "hypothesis_type": "pairs", "asset": "EUR/USD", "asset_b": "GBP/USD",
                "timeframe": "1h", "direction": "", "features": [],
            },
        )
        self.experiment.refresh_from_db()
        self.assertEqual(resp.status_code, 302)  # redirect to plan — spec accepted
        self.assertEqual(self.experiment.structured_spec.get("asset"), "EUR/USD")
        self.assertEqual(self.experiment.structured_spec.get("asset_b"), "GBP/USD")


class FullWorkflowTest(TestCase):
    """One student, one hypothesis, all the way from intake to a
    completed, evidence-backed verdict — through real HTTP requests."""

    def setUp(self):
        self.user = User.objects.create_user(username="labuser2", password="x")
        self.client.login(username="labuser2", password="x")

    def test_full_workflow_reaches_a_completed_result_with_real_evidence(self):
        create_response = self.client.post(reverse("research_lab_dashboard"), data={"hypothesis_text": "BTC RSI predicts short-term returns"})
        experiment = ResearchExperiment.objects.get(student=self.user)
        self.assertRedirects(create_response, reverse("research_lab_formalize", kwargs={"experiment_id": experiment.id}))

        formalize_get = self.client.get(reverse("research_lab_formalize", kwargs={"experiment_id": experiment.id}))
        self.assertEqual(formalize_get.status_code, 200)

        formalize_post = self.client.post(reverse("research_lab_formalize", kwargs={"experiment_id": experiment.id}), data={
            "asset": "BTC/USDT", "timeframe": "1h", "feature_name": "rsi",
            "direction": "", "horizon": "4", "risk_tier": "LOW",
        })
        self.assertRedirects(formalize_post, reverse("research_lab_plan", kwargs={"experiment_id": experiment.id}))

        plan_get = self.client.get(reverse("research_lab_plan", kwargs={"experiment_id": experiment.id}))
        self.assertEqual(plan_get.status_code, 200)
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "PLANNED")

        run_response = self.client.post(reverse("research_lab_plan", kwargs={"experiment_id": experiment.id}))
        self.assertRedirects(run_response, reverse("research_lab_results", kwargs={"experiment_id": experiment.id}))
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "COMPLETED")
        self.assertTrue(experiment.verdict)

        results_response = self.client.get(reverse("research_lab_results", kwargs={"experiment_id": experiment.id}))
        self.assertEqual(results_response.status_code, 200)
        self.assertContains(results_response, experiment.verdict)

        report_response = self.client.get(reverse("research_lab_report", kwargs={"experiment_id": experiment.id}))
        self.assertEqual(report_response.status_code, 200)
        self.assertContains(report_response, str(experiment.id))

        history_response = self.client.get(reverse("research_lab_history"))
        self.assertEqual(history_response.status_code, 200)
        self.assertContains(history_response, "BTC RSI predicts short-term returns")

    def test_invalid_asset_selection_blocks_at_formalize_stage(self):
        self.client.post(reverse("research_lab_dashboard"), data={"hypothesis_text": "test"})
        experiment = ResearchExperiment.objects.get(student=self.user)

        response = self.client.post(reverse("research_lab_formalize", kwargs={"experiment_id": experiment.id}), data={
            "asset": "", "timeframe": "1h", "feature_name": "rsi", "horizon": "4", "risk_tier": "LOW",
        })
        self.assertEqual(response.status_code, 200)  # claude code changed: re-renders the form with errors, does not proceed
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "PENDING")


class OwnershipIsolationTest(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="x")
        self.other = User.objects.create_user(username="other", password="x")
        self.experiment = ResearchExperiment.objects.create(student=self.owner, hypothesis_text="private hypothesis")

    def test_another_student_cannot_view_someone_elses_experiment(self):
        self.client.login(username="other", password="x")
        response = self.client.get(reverse("research_lab_results", kwargs={"experiment_id": self.experiment.id}))
        self.assertEqual(response.status_code, 404)  # claude code changed: get_object_or_404 scoped to student=request.user — no cross-student leakage
