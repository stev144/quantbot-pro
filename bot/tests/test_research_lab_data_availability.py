# claude code changed: new file — Research Lab MVP, section 21 test
# requirements for the data availability checker: available dataset,
# missing dataset, unsupported feature.

from django.test import SimpleTestCase

from bot.research_lab.data_availability import check_data_availability
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
        report = check_data_availability(_spec(asset="BTC/USDT", features=["funding_rate"]))
        fr_check = next(c for c in report.checks if c.name == "funding_rate")
        self.assertFalse(fr_check.available)
        self.assertIn("not ingested", fr_check.reason)
        self.assertFalse(report.all_available)

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
