# ============================================================
# bot/tests/test_data_layer_contracts.py
# claude code changed: new — asserts the "real data or explicit
# {"available": False, "reason": ...}" contract every data-layer module
# in this project follows (terminal_data.py, research_lab_data.py,
# feature_intelligence_data.py, and the new sections' *_data.py modules)
# never raises and never returns a bare number where an "unavailable"
# marker is expected. These call the real functions against whatever
# data/ and research_data/ happen to exist in this checkout — no
# network calls, no fixtures required.
# ============================================================

from django.test import TestCase

from bot.views import terminal_data
from bot.views import market_intelligence_data as mid
from bot.views import portfolio_risk_data as prd


class TerminalDataContractTest(TestCase):
    def test_get_system_health_never_raises_and_has_overall_status(self):
        result = terminal_data.get_system_health()
        self.assertIn(result["overall"], ("ok", "warn", "err"))
        self.assertTrue(len(result["checks"]) > 0)
        for check in result["checks"]:
            self.assertIn(check["status"], ("ok", "warn", "err"))

    def test_get_execution_state_available_or_explicit_reason(self):
        result = terminal_data.get_execution_state()
        if result["available"]:
            self.assertIsInstance(result["open_count"], int)
            self.assertIn("reason", result["unrealized_pnl"])
            self.assertIn("reason", result["pending_orders"])
        else:
            self.assertIn("reason", result)

    def test_get_cross_sectional_dispersion_available_or_explicit_reason(self):
        result = terminal_data.get_cross_sectional_dispersion()
        if not result["available"]:
            self.assertIn("reason", result)

    def test_get_portfolio_risk_never_raises_with_empty_backtest_results(self):
        result = terminal_data.get_portfolio_risk({})
        self.assertIn("max_drawdown", result)
        self.assertIn("crash_risk", result)
        self.assertFalse(result["crash_risk"]["available"])
        self.assertIn("reason", result["crash_risk"])


class MarketIntelligenceDataContractTest(TestCase):
    def test_discover_symbol_universe_returns_a_list(self):
        symbols = mid.discover_symbol_universe()
        self.assertIsInstance(symbols, list)

    def test_get_market_intelligence_never_raises(self):
        result = mid.get_market_intelligence()
        self.assertIn("regime_overview", result)
        self.assertIn("cross_section", result)
        self.assertIn("available", result["regime_overview"])


class PortfolioRiskDataContractTest(TestCase):
    def test_get_portfolio_and_risk_never_raises(self):
        result = prd.get_portfolio_and_risk("NONEXISTENTSYMBOLUSDT")
        self.assertIn("portfolio_risk", result)
        self.assertIn("execution_available", result)



# claude code changed: 2026-10-05 — cross-sectional readings compare symbols at ONE timestamp only. The research
# files end on different dates; mixing each symbol's "latest" value across dates is not a cross-section.
class CrossSectionSameTimestampTest(TestCase):
    def test_only_symbols_at_the_common_latest_timestamp_are_compared(self):
        import os
        import shutil
        import tempfile
        from unittest import mock
        import pandas as pd
        d = tempfile.mkdtemp()
        try:
            def write(sym, ts, z):
                pd.DataFrame({"timestamp": [ts], "cs_zscore": [z]}).to_csv(os.path.join(d, f"{sym}_cross_section.csv"), index=False)
            for sym, z in (("AAA_USDT", 1.0), ("BBB_USDT", -1.0), ("CCC_USDT", 0.5)):
                write(sym, "2026-09-24 22:00:00+00:00", z)
            write("OLD_USDT", "2026-08-19 19:00:00+00:00", 9.0)            # an extreme value from another date
            with mock.patch.object(terminal_data, "RESEARCH_DATA_DIR", d):
                r = terminal_data.get_cross_sectional_dispersion()
            self.assertTrue(r["available"])
            self.assertEqual((r["n_symbols"], r["n_excluded_other_timestamp"]), (3, 1))
            self.assertEqual(r["as_of"], "2026-09-24 22:00 UTC")
            self.assertNotIn("OLD_USDT", [e["symbol"] for e in r["top_extremes"]])   # it would otherwise rank first
            self.assertAlmostEqual(r["dispersion_std"], round(float(pd.Series([1.0, -1.0, 0.5]).std()), 4))
        finally:
            shutil.rmtree(d, ignore_errors=True)
