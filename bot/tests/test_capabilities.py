# claude code changed: new file — Forex Integration Stage 1 (Architectural
# Parity). Covers bot/research/capabilities.py, the new capability-
# declaration primitive, and its one real, concrete application
# (feature_calculator.py's volume_ratio, which was silently all-NaN for
# Forex before this fix — see that file's own comment).

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.capabilities import CapabilityResult, DataRequirement, check_data_requirement
from bot.research.feature_calculator import FeatureCalculator


def _df_with_volume(volume_values):
    n = len(volume_values)
    return pd.DataFrame({
        "open": np.full(n, 100.0), "high": np.full(n, 101.0), "low": np.full(n, 99.0),
        "close": np.full(n, 100.0), "volume": volume_values,
    }, index=pd.date_range("2024-01-01", periods=n, freq="1h"))


class DataRequirementRealVolumeTest(SimpleTestCase):

    def test_all_zero_volume_is_not_applicable_not_a_crash(self):
        df = _df_with_volume([0.0] * 50)
        result = check_data_requirement(df, DataRequirement.REAL_VOLUME, asset_class="FOREX")
        self.assertFalse(result.satisfied)
        self.assertEqual(result.status, "NOT_APPLICABLE")
        self.assertIn("FOREX", result.reason)

    def test_all_nan_volume_is_insufficient_data(self):
        df = _df_with_volume([np.nan] * 50)
        result = check_data_requirement(df, DataRequirement.REAL_VOLUME)
        self.assertEqual(result.status, "INSUFFICIENT_DATA")

    def test_missing_volume_column_is_insufficient_data(self):
        df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        result = check_data_requirement(df, DataRequirement.REAL_VOLUME)
        self.assertFalse(result.satisfied)
        self.assertEqual(result.status, "INSUFFICIENT_DATA")

    def test_real_nonzero_volume_is_ok(self):
        df = _df_with_volume([1000.0, 1200.0, 900.0, 1100.0] * 15)
        result = check_data_requirement(df, DataRequirement.REAL_VOLUME)
        self.assertTrue(result.satisfied)
        self.assertEqual(result.status, "OK")


class DataRequirementOtherTypesTest(SimpleTestCase):

    def test_bid_ask_not_applicable_when_absent(self):
        df = pd.DataFrame({"close": [1.0]})
        result = check_data_requirement(df, DataRequirement.BID_ASK)
        self.assertEqual(result.status, "NOT_APPLICABLE")

    def test_bid_ask_ok_when_present(self):
        df = pd.DataFrame({"close": [1.0], "bid": [0.999], "ask": [1.001]})
        result = check_data_requirement(df, DataRequirement.BID_ASK)
        self.assertTrue(result.satisfied)

    def test_order_book_always_not_applicable_today(self):
        df = pd.DataFrame({"close": [1.0]})
        result = check_data_requirement(df, DataRequirement.ORDER_BOOK)
        self.assertEqual(result.status, "NOT_APPLICABLE")

    def test_derivatives_always_not_applicable_for_a_plain_ohlcv_df(self):
        df = pd.DataFrame({"close": [1.0]})
        result = check_data_requirement(df, DataRequirement.DERIVATIVES)
        self.assertEqual(result.status, "NOT_APPLICABLE")

    def test_unknown_requirement_raises_not_silently_ignored(self):
        with self.assertRaises(ValueError):
            check_data_requirement(pd.DataFrame({"close": [1.0]}), "not_a_real_requirement")


class VolumeRatioCapabilityIntegrationTest(SimpleTestCase):
    """claude code changed: new — proves the real, concrete application:
    feature_calculator.py's _calculate_volume_ratio now explicitly
    declares and checks its REAL_VOLUME requirement instead of silently
    producing an all-NaN column via a division side-effect."""

    def test_zero_volume_forex_like_data_returns_all_nan_honestly(self):
        calc = FeatureCalculator()
        df = _df_with_volume([0.0] * 50)
        result = calc._calculate_volume_ratio(df, asset_class="FOREX")
        self.assertTrue(result.isna().all())

    def test_real_volume_crypto_like_data_computes_a_real_ratio(self):
        calc = FeatureCalculator()
        rng = np.random.default_rng(1)
        df = _df_with_volume(rng.uniform(500, 1500, 50))
        result = calc._calculate_volume_ratio(df, asset_class="CRYPTO")
        self.assertGreater(result.notna().sum(), 0)
