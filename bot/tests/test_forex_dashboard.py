# claude code changed: new file — Forex Research Dashboard mission.
# View-level coverage via the real Django test client (real HTTP,
# no mocking of the view itself), matching this project's established
# convention. No login is required (see bot/views/forex_dashboard.py's
# own docstring on the confirmed no-auth-gating convention).

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from bot.research_lab.models import ResearchExperiment


class ForexDashboardViewTest(TestCase):

    def test_valid_symbol_loads(self):
        resp = self.client.get(reverse("forex_dashboard"), {"symbol": "EUR/USD"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "EUR/USD")
        self.assertContains(resp, "yahoo_finance")

    def test_no_symbol_param_defaults_to_a_real_registered_symbol(self):
        resp = self.client.get(reverse("forex_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "is not a registered Forex instrument")

    def test_unknown_symbol_fails_closed_not_a_crash(self):
        resp = self.client.get(reverse("forex_dashboard"), {"symbol": "NOT_A_REAL_SYMBOL"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "is not a registered Forex instrument")

    def test_crypto_symbol_is_rejected_on_the_forex_page(self):
        """claude code changed: real boundary check — a crypto symbol
        must not silently render on the Forex dashboard just because it's
        a registered instrument somewhere in the platform."""
        resp = self.client.get(reverse("forex_dashboard"), {"symbol": "BTC/USDT"})
        self.assertContains(resp, "is not a registered Forex instrument")

    def test_page_requires_no_login(self):
        resp = self.client.get(reverse("forex_dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_symbol_selector_is_populated_from_the_real_registry_not_hardcoded(self):
        resp = self.client.get(reverse("forex_dashboard"), {"symbol": "EUR/USD"})
        html = resp.content.decode()
        self.assertIn('<option value="USD/CAD"', html)
        self.assertIn('<option value="NZD/USD"', html)

    def test_never_makes_a_live_network_call(self):
        """claude code changed: the exact locked-in product decision —
        this page must read only the local cache, never Yahoo Finance,
        inside the request/response cycle."""
        with patch("bot.forex_data_fetcher.get_forex_klines") as mock_fetch:
            resp = self.client.get(reverse("forex_dashboard"), {"symbol": "EUR/USD"})
        self.assertEqual(resp.status_code, 200)
        mock_fetch.assert_not_called()

    def test_backtest_section_is_honestly_unavailable(self):
        # claude code changed: cross-sectional research has since been
        # run for real (bot.research.cross_section_engine.
        # run_forex_cross_section_research()) — see
        # ForexCrossSectionDataTest below for that section's own coverage.
        # The backtest-derived section remains unavailable: no engine
        # change was made here, only a UI wiring for cross-sectional data.
        resp = self.client.get(reverse("forex_dashboard"), {"symbol": "EUR/USD"})
        html = resp.content.decode()
        self.assertIn("No Forex-cost-aware backtest exists yet", html)

    def test_cross_sectional_section_shows_real_data_when_available(self):
        resp = self.client.get(reverse("forex_dashboard"), {"symbol": "EUR/USD"})
        html = resp.content.decode()
        if "cross_section_engine.py has not been run" in html:
            self.skipTest("no research_data/forex/*_cross_section.csv on this machine")
        self.assertIn("Cross-Sectional Opportunities", html)
        self.assertNotIn("cross_section_engine.py has not been run", html)

    def test_research_hypothesis_form_posts_to_the_existing_research_lab_endpoint(self):
        resp = self.client.get(reverse("forex_dashboard"), {"symbol": "EUR/USD"})
        html = resp.content.decode()
        self.assertIn(f'action="{reverse("research_lab_dashboard")}"', html)
        self.assertIn('name="asset_class" value="FOREX"', html)

    def test_submitting_the_embedded_hypothesis_form_creates_a_real_forex_experiment(self):
        user = User.objects.create_user(username="forexdashuser", password="x")
        self.client.login(username="forexdashuser", password="x")
        resp = self.client.post(
            reverse("research_lab_dashboard"),
            data={"hypothesis_text": "I believe AUD/USD and NZD/USD are cointegrated", "asset_class": "FOREX"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            ResearchExperiment.objects.filter(
                student=user, hypothesis_text__icontains="AUD/USD"
            ).exists()
        )
