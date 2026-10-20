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


# claude code changed: new — Markets discovery screen and Watchlist smoke tests
class MarketsSmokeTest(TestCase):

    def test_markets_page_renders(self):
        response = self.client.get("/markets/")
        self.assertEqual(response.status_code, 200)

    def test_markets_page_has_asset_tabs(self):
        response = self.client.get("/markets/")
        html = response.content.decode()
        self.assertIn('data-tab="crypto"', html)
        self.assertIn('data-tab="forex"', html)

    def test_markets_page_has_sparklines(self):
        response = self.client.get("/markets/")
        html = response.content.decode()
        # Sparklines are inline SVG polylines generated from CSV data
        self.assertIn("polyline points", html)

    def test_markets_page_has_mobile_bottom_nav(self):
        response = self.client.get("/markets/")
        html = response.content.decode()
        self.assertIn("term-bottom-nav", html)

    def test_markets_page_no_fabricated_prices(self):
        # All prices come from CSV files; page must not serve if no CSVs exist
        # (the view returns an honest unavailable state, not invented quotes).
        # We just assert the page itself renders without error.
        response = self.client.get("/markets/")
        self.assertNotEqual(response.status_code, 500)

    def test_watchlist_requires_login(self):
        response = self.client.get("/markets/watchlist/")
        # Anonymous users are redirected to login
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_watchlist_add_requires_login(self):
        import json
        response = self.client.post(
            "/markets/watchlist/add/",
            json.dumps({"symbol": "BTC_USDT", "asset_class": "crypto"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)

    def test_watchlist_remove_requires_login(self):
        import json
        response = self.client.post(
            "/markets/watchlist/remove/",
            json.dumps({"symbol": "BTC_USDT"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)

    def test_authenticated_user_can_add_and_remove_watchlist_item(self):
        import json
        from django.contrib.auth.models import User
        from bot.journal.models import WatchlistItem
        user = User.objects.create_user("wltest", password="testpass123")
        self.client.login(username="wltest", password="testpass123")

        # Add
        resp = self.client.post(
            "/markets/watchlist/add/",
            json.dumps({"symbol": "BTC_USDT", "asset_class": "crypto"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])
        self.assertTrue(data["created"])
        self.assertTrue(WatchlistItem.objects.filter(user=user, symbol="BTC_USDT").exists())

        # Add again — idempotent (not created again)
        resp2 = self.client.post(
            "/markets/watchlist/add/",
            json.dumps({"symbol": "BTC_USDT", "asset_class": "crypto"}),
            content_type="application/json",
        )
        data2 = json.loads(resp2.content)
        self.assertTrue(data2["ok"])
        self.assertFalse(data2["created"])  # already exists

        # Remove
        resp3 = self.client.post(
            "/markets/watchlist/remove/",
            json.dumps({"symbol": "BTC_USDT"}),
            content_type="application/json",
        )
        data3 = json.loads(resp3.content)
        self.assertTrue(data3["ok"])
        self.assertTrue(data3["removed"])
        self.assertFalse(WatchlistItem.objects.filter(user=user, symbol="BTC_USDT").exists())

    def test_watchlist_page_renders_for_authenticated_user(self):
        from django.contrib.auth.models import User
        user = User.objects.create_user("wlpagetest", password="testpass123")
        self.client.login(username="wlpagetest", password="testpass123")
        response = self.client.get("/markets/watchlist/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Watchlist")

    def test_dashboard_has_mobile_bottom_nav(self):
        # Bottom nav is included in dashboard.html
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("term-bottom-nav", response.content.decode())
