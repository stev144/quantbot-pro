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
