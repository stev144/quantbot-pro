# claude code changed: new file — Forex Research Dashboard mission.
# Covers bot/views/forex_terminal_data.py's real, non-fabricated data
# builders, and the one generalized shared primitive
# (terminal_data._compute_price_correlation's new `directory` param).

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from bot.research_lab.models import ResearchExperiment
from bot.views import forex_terminal_data, terminal_data


class CorrelationDirectoryParamRegressionTest(SimpleTestCase):
    """claude code changed: real regression guard for the
    terminal_data._compute_price_correlation() signature change — the
    existing crypto call site (get_portfolio_risk()) must be completely
    unaffected."""

    def test_default_directory_still_reads_crypto_data_dir(self):
        # No directory kwarg passed — must behave byte-identical to the
        # pre-existing crypto call site.
        result_default = terminal_data._compute_price_correlation()
        result_explicit_data_dir = terminal_data._compute_price_correlation(directory=terminal_data.DATA_DIR)
        self.assertEqual(result_default, result_explicit_data_dir)

    def test_forex_directory_reads_a_different_dataset(self):
        forex_result = terminal_data._compute_price_correlation(directory=forex_terminal_data.FOREX_DATA_DIR)
        crypto_result = terminal_data._compute_price_correlation()
        # claude code changed: not asserting specific values (real data,
        # changes over time) — just that pointing at a different
        # directory actually reads different symbols, proving the param
        # is wired through, not silently ignored.
        if forex_result.get("available") and crypto_result.get("available"):
            self.assertNotEqual(forex_result.get("top_pairs"), crypto_result.get("top_pairs"))


class ForexDataProvenanceTest(SimpleTestCase):

    def test_no_local_data_is_honest_not_fabricated(self):
        import pandas as pd
        from bot.instruments import get_instrument

        instrument = get_instrument("EUR/USD")
        result = forex_terminal_data.get_forex_data_provenance("EUR/USD", instrument, pd.DataFrame())
        self.assertFalse(result["available"])
        self.assertIn("reason", result)

    def test_real_local_data_reports_real_fields(self):
        from bot.views.forex_dashboard import _load_local_forex_ohlcv
        from bot.instruments import get_instrument

        df = _load_local_forex_ohlcv("EUR/USD")
        if df.empty:
            self.skipTest("no local data/forex/EUR_USD_1h.csv on this machine")
        instrument = get_instrument("EUR/USD")
        result = forex_terminal_data.get_forex_data_provenance("EUR/USD", instrument, df)
        self.assertTrue(result["available"])
        self.assertEqual(result["provider"], "yahoo_finance")
        self.assertEqual(result["row_count"], len(df))
        self.assertIsInstance(result["fingerprint"], str)
        self.assertGreater(len(result["fingerprint"]), 0)


class ForexResearchStateTest(TestCase):
    """claude code changed: real bug avoided here — structured_spec's
    asset_class field is almost always None by design (see
    forex_terminal_data._forex_experiments()'s own docstring); these
    tests confirm Forex experiments are found by resolving the stored
    `asset` symbol through the instrument registry instead."""

    def setUp(self):
        self.user = User.objects.create_user(username="forexstateuser", password="x")

    def test_forex_experiment_is_counted_crypto_experiment_is_not(self):
        ResearchExperiment.objects.create(
            student=self.user, hypothesis_text="forex one",
            structured_spec={"asset": "EUR/USD", "asset_b": "GBP/USD", "hypothesis_type": "pairs", "asset_class": None},
        )
        ResearchExperiment.objects.create(
            student=self.user, hypothesis_text="crypto one",
            structured_spec={"asset": "BTC/USDT", "hypothesis_type": "feature", "asset_class": None},
        )
        state = forex_terminal_data.get_forex_research_state()
        self.assertEqual(state["total_experiments"], 1)
        self.assertEqual(state["status_counts"].get("PENDING"), 1)

    def test_experiment_with_no_structured_spec_yet_is_not_counted(self):
        ResearchExperiment.objects.create(student=self.user, hypothesis_text="nothing formalized yet")
        state = forex_terminal_data.get_forex_research_state()
        self.assertEqual(state["total_experiments"], 0)

    def test_features_and_strategies_are_honestly_unavailable(self):
        state = forex_terminal_data.get_forex_research_state()
        self.assertFalse(state["features_evaluated"]["available"])
        self.assertFalse(state["strategies_active"]["available"])


class ForexAlphaIntelligenceTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="forexalphauser", password="x")

    def test_completed_pairs_experiment_surfaces_real_evidence(self):
        ResearchExperiment.objects.create(
            student=self.user, hypothesis_text="eur gbp cointegrated",
            structured_spec={"asset": "EUR/USD", "asset_b": "GBP/USD", "hypothesis_type": "pairs"},
            statistical_results={
                "pair_name": "EUR/USD/GBP/USD", "is_cointegrated": True, "adf_pvalue": 0.01,
                "hedge_ratio": 0.9, "half_life_hours": 40.0, "passes_filters": True,
            },
            verdict="SUPPORTED", status="COMPLETED",
        )
        result = forex_terminal_data.get_forex_alpha_intelligence()
        self.assertTrue(result["pairs_available"])
        self.assertEqual(result["pairs_results"][0]["verdict"], "SUPPORTED")
        self.assertEqual(result["pairs_results"][0]["adf_pvalue"], 0.01)
        self.assertFalse(result["feature_power"]["available"])
        self.assertFalse(result["strategy_expectancy"]["available"])

    def test_pending_experiment_with_no_statistical_results_is_excluded(self):
        ResearchExperiment.objects.create(
            student=self.user, hypothesis_text="not run yet",
            structured_spec={"asset": "EUR/USD", "asset_b": "GBP/USD", "hypothesis_type": "pairs"},
        )
        result = forex_terminal_data.get_forex_alpha_intelligence()
        self.assertFalse(result["pairs_available"])

    def test_feature_type_forex_experiment_is_excluded_from_pairs_table(self):
        ResearchExperiment.objects.create(
            student=self.user, hypothesis_text="eur usd rsi",
            structured_spec={"asset": "EUR/USD", "hypothesis_type": "feature"},
            statistical_results={"ic": 0.05},
        )
        result = forex_terminal_data.get_forex_alpha_intelligence()
        self.assertFalse(result["pairs_available"])


class ForexPortfolioRiskTest(SimpleTestCase):

    def test_currency_exposure_reflects_the_real_registry(self):
        result = forex_terminal_data.get_forex_portfolio_risk()
        currencies = {row["currency"] for row in result["currency_exposure"]}
        self.assertIn("USD", currencies)  # every registered major involves USD on one side
        usd_row = next(r for r in result["currency_exposure"] if r["currency"] == "USD")
        self.assertGreaterEqual(usd_row["pair_count"], 6)  # confirmed 6+ of 8 majors involve USD

    def test_max_drawdown_and_crash_risk_are_honestly_unavailable(self):
        result = forex_terminal_data.get_forex_portfolio_risk()
        self.assertFalse(result["max_drawdown"]["available"])
        self.assertFalse(result["crash_risk"]["available"])


class ForexExecutionStateTest(TestCase):

    def test_no_forex_trades_is_honestly_reported_not_fabricated(self):
        result = forex_terminal_data.get_forex_execution_state()
        self.assertTrue(result["available"])
        self.assertEqual(result["open_count"], 0)
        self.assertEqual(result["total_closed"], 0)
        self.assertEqual(result["realized_pnl"], 0)

    def test_crypto_trades_never_leak_into_forex_execution_state(self):
        from bot.journal.models import TradeRecord
        TradeRecord.objects.create(
            symbol="BTC/USDT", side="BUY", entry_price=50000, sl=49000, tp=52000,
            quantity=0.01, status="OPEN", order_id="test-order-1",
        )
        result = forex_terminal_data.get_forex_execution_state()
        self.assertEqual(result["open_count"], 0)


class ForexSystemHealthTest(SimpleTestCase):

    def test_never_calls_the_networked_provider_connectivity_check(self):
        # claude code changed: AST-based, not a substring check — this
        # function's own docstring legitimately MENTIONS
        # check_forex_provider_connectivity to explain why it's excluded,
        # which a naive substring search would misread as a violation.
        # Walks the real import statements instead: the networked check
        # must never be one of the names actually imported for use.
        import ast
        import inspect

        source = inspect.getsource(forex_terminal_data.get_forex_system_health)
        tree = ast.parse(source)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_names.update(alias.name for alias in node.names)
        self.assertNotIn("check_forex_provider_connectivity", imported_names)
        self.assertIn("check_forex_architecture", imported_names)

    def test_returns_real_checks_with_a_valid_overall_status(self):
        result = forex_terminal_data.get_forex_system_health()
        self.assertIn(result["overall"], ("ok", "warn", "err"))
        self.assertGreater(len(result["checks"]), 0)
        for check in result["checks"]:
            self.assertIn(check["status"], ("ok", "warn", "err"))
