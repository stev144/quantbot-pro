# ============================================================
# bot/tests/test_cache_and_scorer_bugs.py
# claude code changed: new — regression tests for the two real bugs
# found and fixed in bot/views/dashboard.py during the platform redesign:
#   1. live_data() was overwriting dashboard()'s backtest_results_{symbol}
#      cache key with a differently-shaped payload at a 60s TTL.
#   2. live_data() imported StrategyScorer from bot.analytics, which only
#      defines TradeAnalytics — the import always failed, silently caught,
#      so total_score/grade were permanently the fallback 0/"—".
# Both are testable without any network access by pre-seeding the cache,
# since live_data() only fetches live data on a cache miss.
# ============================================================

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse


class LiveDataCacheCollisionRegressionTest(TestCase):
    def test_live_data_does_not_overwrite_dashboards_backtest_cache(self):
        symbol = "REGRESSIONTESTUSDT"
        backtest_cache_key = f"backtest_results_{symbol}"
        fake_backtest_results = {
            "trades": [{"net_pnl": 10.0, "direction": "LONG"}],
            "total_trades": 1,
            "wins": 1,
            "win_rate": 100.0,
            "expectancy": 10.0,
            "profit_factor": 2.0,
            "avg_r_multiple": 1.5,
            "sharpe_ratio": 1.2,
            "sortino_ratio": 1.4,
            "max_drawdown": 0.0,
            "max_consecutive_losses": 0,
        }
        cache.set(backtest_cache_key, fake_backtest_results, 3600)

        response = self.client.get(reverse("live_data"), {"symbol": symbol})
        self.assertEqual(response.status_code, 200)

        # This is the actual bug: before the fix, live_data() wrote its
        # own count-up-widget payload back into this exact key, so a
        # subsequent dashboard() cache hit for the same symbol would see
        # {"success": True, "summary": {...}} instead of the real
        # backtest results dict, and results.get("trades", []) would
        # silently return [].
        still_cached = cache.get(backtest_cache_key)
        self.assertEqual(still_cached, fake_backtest_results)
        self.assertIn("trades", still_cached)


class LiveDataStrategyScorerImportRegressionTest(TestCase):
    def test_live_data_returns_a_real_grade_not_the_import_failure_fallback(self):
        symbol = "REGRESSIONTEST2USDT"
        cache.set(f"backtest_results_{symbol}", {}, 3600)

        response = self.client.get(reverse("live_data"), {"symbol": symbol})
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        grade = payload["summary"]["grade"]["value"]
        # Before the fix, `from bot.analytics import StrategyScorer`
        # always raised ImportError (only TradeAnalytics lives there),
        # was silently caught, and grade was permanently "—" regardless
        # of the actual backtest. StrategyScorer({}).evaluate() for a
        # real (if empty) results dict returns a real grade like "F", not
        # the import-failure placeholder — confirmed directly against
        # bot.engines.strategy_scorer.StrategyScorer.
        self.assertNotEqual(grade, "—")
