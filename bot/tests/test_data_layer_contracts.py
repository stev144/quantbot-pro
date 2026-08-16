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
