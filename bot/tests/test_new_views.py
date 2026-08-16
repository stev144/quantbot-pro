# ============================================================
# bot/tests/test_new_views.py
# claude code changed: new — template smoke tests for every page in the
# platform redesign (the 6 pre-existing pages + the 9 new sections).
# Asserts status_code == 200 and that no unhandled server error occurred.
#
# These call the real views against whatever data/research_data/ exists
# in this checkout, same convention as the rest of this project (no
# mocking framework is used anywhere else in the codebase — health_check.py,
# dry_run_test.py, and RegimePrecomputerEquivalenceTest all run against
# real data or skip). Pages that fetch live OHLCV (dashboard, market
# intelligence, backtesting, strategy research) rely on
# bot.data_fetcher's own disk cache to stay fast on a warm cache; on a
# fully cold cache/no network these may be slow rather than fail, since
# every view already degrades to an explicit "unavailable" state rather
# than raising when data can't be fetched.
# ============================================================

from django.test import TestCase


class ExistingPagesSmokeTest(TestCase):
    def test_dashboard(self):
        response = self.client.get("/", {"symbol": "AVAXUSDT"})
        self.assertEqual(response.status_code, 200)

    def test_research_lab(self):
        response = self.client.get("/research/", {"symbol": "AVAXUSDT"})
        self.assertEqual(response.status_code, 200)

    def test_feature_leaderboard(self):
        response = self.client.get("/research/features/")
        self.assertEqual(response.status_code, 200)

    def test_feature_detail(self):
        response = self.client.get("/research/features/rsi/")
        self.assertEqual(response.status_code, 200)

    def test_feature_comparison(self):
        response = self.client.get("/research/features/compare/", {"f": "rsi"})
        self.assertEqual(response.status_code, 200)

    def test_feature_correlation(self):
        response = self.client.get("/research/features/correlation/")
        self.assertEqual(response.status_code, 200)


class NewSectionsSmokeTest(TestCase):
    def test_market_intelligence(self):
        response = self.client.get("/market/")
        self.assertEqual(response.status_code, 200)

    def test_robustness_analysis(self):
        response = self.client.get("/research/robustness/")
        self.assertEqual(response.status_code, 200)

    def test_rejected_research(self):
        response = self.client.get("/research/rejected/")
        self.assertEqual(response.status_code, 200)

    def test_research_archive(self):
        response = self.client.get("/research/archive/")
        self.assertEqual(response.status_code, 200)

    def test_portfolio_risk(self):
        response = self.client.get("/portfolio/")
        self.assertEqual(response.status_code, 200)

    def test_execution_monitor(self):
        response = self.client.get("/execution/")
        self.assertEqual(response.status_code, 200)

    def test_system_health(self):
        response = self.client.get("/system/health/")
        self.assertEqual(response.status_code, 200)

    def test_strategy_research(self):
        response = self.client.get("/research/strategies/")
        self.assertEqual(response.status_code, 200)

    def test_backtesting(self):
        response = self.client.get("/research/backtests/")
        self.assertEqual(response.status_code, 200)

    def test_strategy_pipeline(self):
        # claude code changed: new — Phase 3 (UI pipeline view)
        response = self.client.get("/research/pipeline/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MeanReversionStrategy")
        self.assertContains(response, "REJECTED")

    def test_pairs_performance(self):
        # claude code changed: new — Pairs Performance page
        response = self.client.get("/research/pairs/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AVAX_ATOM")
        self.assertContains(response, "DOT_LINK")

    def test_pairs_performance_specific_pair(self):
        # claude code changed: new — Pairs Performance page
        response = self.client.get("/research/pairs/", {"pair": "DOT_LINK"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DOT_LINK")
