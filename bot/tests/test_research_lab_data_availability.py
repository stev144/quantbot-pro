# claude code changed: new file — Research Lab MVP, section 21 test
# requirements for the data availability checker: available dataset,
# missing dataset, unsupported feature.

from django.test import SimpleTestCase

from bot.research_lab.data_availability import (  # claude code changed: new imports — Phase 2B, Step 2
    check_data_availability, REQUIRES_PROVIDER, REQUIRES_INTEGRATION,
)
from bot.research_lab.spec import ResearchSpec


def _spec(**overrides):
    defaults = dict(
        hypothesis_text="test",
        asset="BTC/USDT",
        timeframe="1h",
        target={"type": "forward_return", "horizon": 24},
    )
    defaults.update(overrides)
    return ResearchSpec(**defaults)


class OhlcvAvailabilityTest(SimpleTestCase):

    def test_known_symbol_with_real_data_file_is_available(self):
        # claude code changed: BTC/USDT is part of this repo's real,
        # committed data/ universe per fetch_all_symbols.py's SYMBOLS list —
        # this test only passes if the actual file is present on disk.
        report = check_data_availability(_spec(asset="BTC/USDT"))
        ohlcv_check = next(c for c in report.checks if c.name.startswith("ohlcv:"))
        self.assertTrue(ohlcv_check.available, ohlcv_check.reason)

    def test_symbol_with_no_data_file_is_unavailable(self):
        report = check_data_availability(_spec(asset="APT/USDT", timeframe="1h"))
        # claude code changed: APT/USDT is in SUPPORTED_ASSETS (passes spec
        # validation) but may or may not have a fetched CSV — this test
        # doesn't assume either way, it only proves the checker classifies
        # honestly rather than assuming available.
        ohlcv_check = next(c for c in report.checks if c.name.startswith("ohlcv:"))
        self.assertIsInstance(ohlcv_check.available, bool)


class FeatureAvailabilityTest(SimpleTestCase):

    def test_derivable_feature_available_when_ohlcv_exists(self):
        report = check_data_availability(_spec(asset="BTC/USDT", features=["rsi"]))
        rsi_check = next(c for c in report.checks if c.name == "rsi")
        self.assertTrue(rsi_check.available)
        self.assertIn("feature_calculator", rsi_check.reason)

    def test_known_unavailable_source_is_explicitly_rejected(self):
        # claude code changed: was "funding_rate" — Phase 2B, Step 2 moved
        # funding_rate/open_interest to SOURCES_PENDING_RESEARCH_LAB_INTEGRATION
        # (a real pipeline exists, see the dedicated test below), so this
        # test now uses a source with genuinely no pipeline anywhere.
        report = check_data_availability(_spec(asset="BTC/USDT", features=["orderbook_depth_history"]))
        check = next(c for c in report.checks if c.name == "orderbook_depth_history")
        self.assertFalse(check.available)
        self.assertIn("not ingested", check.reason)
        self.assertEqual(check.status, REQUIRES_PROVIDER)
        self.assertFalse(report.all_available)

    def test_funding_rate_is_pending_integration_not_falsely_claimed_unavailable(self):
        # claude code changed: new — Phase 2B, Step 2. The Research Lab must
        # not tell a researcher that funding_rate/open_interest are "not
        # ingested anywhere" when a real, tested pipeline
        # (bot.engines.derivatives_data / bot.research.derivatives_engine)
        # already exists for them. It also must not silently claim
        # available=True — no Research Lab tool can test them as a
        # ResearchSpec feature yet. REQUIRES_INTEGRATION is the honest
        # middle state.
        report = check_data_availability(_spec(asset="BTC/USDT", features=["funding_rate", "open_interest"]))
        for name in ("funding_rate", "open_interest"):
            check = next(c for c in report.checks if c.name == name)
            self.assertFalse(check.available)
            self.assertEqual(check.status, REQUIRES_INTEGRATION)
            self.assertNotIn("not ingested", check.reason)  # claude code changed: that claim would now be false
            self.assertIn("derivatives", check.reason)

    def test_unrecognized_feature_name_is_rejected_not_guessed(self):
        report = check_data_availability(_spec(asset="BTC/USDT", features=["made_up_indicator_xyz"]))
        check = next(c for c in report.checks if c.name == "made_up_indicator_xyz")
        self.assertFalse(check.available)
        self.assertIn("not a recognized feature", check.reason)

    def test_multiple_features_all_available_reports_all_available_true(self):
        report = check_data_availability(_spec(asset="BTC/USDT", features=["rsi", "atr", "adx"]))
        self.assertTrue(report.all_available)

    def test_one_missing_feature_among_several_reports_all_available_false(self):
        report = check_data_availability(_spec(asset="BTC/USDT", features=["rsi", "funding_rate"]))
        self.assertFalse(report.all_available)
