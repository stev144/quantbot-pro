# claude code changed: new file — Phase 2C, Step 12 (Research Lab
# navigation UX). Verifies every Research Lab page's back-navigation
# control renders with the correct destination, per the real inbound-link
# graph audited before writing templates/research_lab/_back_nav.html
# (see that file's own docstring for the full audit).
#
# Experiments are constructed directly via the ORM (not run through the
# real formalize/plan/results pipeline) so these tests need no real
# data/*.csv fixtures — a page's rendered HTML is what's under test here,
# not the research pipeline itself (already covered by
# test_research_lab_views.py's FullWorkflowTest).

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from bot.research_lab.models import ResearchExperiment


class BackNavUnambiguousParentTest(TestCase):
    """Pages with exactly one real inbound link — a plain <a href>,
    no browser-history JS needed."""

    def setUp(self):
        self.user = User.objects.create_user(username="navuser1", password="x")
        self.client.login(username="navuser1", password="x")

    def test_capabilities_back_link_points_to_dashboard(self):
        response = self.client.get(reverse("research_lab_capabilities"))
        self.assertContains(response, f'href="{reverse("research_lab_dashboard")}"')

    def test_history_back_link_points_to_dashboard(self):
        response = self.client.get(reverse("research_lab_history"))
        self.assertContains(response, f'href="{reverse("research_lab_dashboard")}"')

    def test_formalize_back_link_points_to_dashboard(self):
        experiment = ResearchExperiment.objects.create(student=self.user, hypothesis_text="test", status="PENDING")
        response = self.client.get(reverse("research_lab_formalize", kwargs={"experiment_id": experiment.id}))
        self.assertContains(response, f'href="{reverse("research_lab_dashboard")}"')

    def test_plan_back_link_points_to_formalize_for_the_same_experiment(self):
        experiment = ResearchExperiment.objects.create(
            student=self.user, hypothesis_text="test", status="PLANNED",
            structured_spec={"asset": "BTC/USDT", "timeframe": "1h", "feature_name": "rsi", "horizon": 4, "risk_tier": "LOW"},
        )
        response = self.client.get(reverse("research_lab_plan", kwargs={"experiment_id": experiment.id}))
        expected_url = reverse("research_lab_formalize", kwargs={"experiment_id": experiment.id})
        self.assertContains(response, f'href="{expected_url}"')


class BackNavAmbiguousParentUsesBrowserHistoryTest(TestCase):
    """Pages reachable from more than one real page — prefers browser
    history via JS, with a real explicit href as the safe fallback (works
    even with JS disabled or no history entry)."""

    def setUp(self):
        self.user = User.objects.create_user(username="navuser2", password="x")
        self.client.login(username="navuser2", password="x")

    def test_results_back_link_falls_back_to_history_and_prefers_browser_back(self):
        experiment = ResearchExperiment.objects.create(
            student=self.user, hypothesis_text="test", status="COMPLETED", verdict="SUPPORTED",
        )
        response = self.client.get(reverse("research_lab_results", kwargs={"experiment_id": experiment.id}))
        self.assertContains(response, f'href="{reverse("research_lab_history")}"')
        self.assertContains(response, "window.history.back()")   # claude code changed: proves the JS-preferred-path is actually present, not just the fallback href

    def test_report_back_link_falls_back_to_results_for_the_same_experiment(self):
        experiment = ResearchExperiment.objects.create(
            student=self.user, hypothesis_text="test", status="COMPLETED", verdict="SUPPORTED",
        )
        response = self.client.get(reverse("research_lab_report", kwargs={"experiment_id": experiment.id}))
        expected_url = reverse("research_lab_results", kwargs={"experiment_id": experiment.id})
        self.assertContains(response, f'href="{expected_url}"')
        self.assertContains(response, "window.history.back()")


class BackNavDoesNotBreakExistingPageContentTest(TestCase):
    """Step 12's explicit requirement: do not break forms, query
    parameters, pagination, filters, or tool results."""

    def setUp(self):
        self.user = User.objects.create_user(username="navuser3", password="x")
        self.client.login(username="navuser3", password="x")

    def test_dashboard_form_still_submits_correctly_after_nav_changes(self):
        # claude code changed: dashboard deliberately has NO back link (it's
        # Research Lab's own root — see _back_nav.html's docstring) — this
        # test instead proves the unrelated nav work didn't regress its
        # actual functionality.
        response = self.client.post(reverse("research_lab_dashboard"), data={"hypothesis_text": "nav regression check"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ResearchExperiment.objects.filter(student=self.user, hypothesis_text="nav regression check").exists())

    def test_results_page_still_renders_verdict_alongside_the_back_link(self):
        experiment = ResearchExperiment.objects.create(
            student=self.user, hypothesis_text="test", status="COMPLETED", verdict="SUPPORTED",
        )
        response = self.client.get(reverse("research_lab_results", kwargs={"experiment_id": experiment.id}))
        self.assertContains(response, "SUPPORTED")   # claude code changed: real page content still present, not replaced by the nav addition
