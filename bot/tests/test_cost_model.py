# claude code changed: new file — Multi-Asset Foundation Refactor Phase
# 1A, STEP 8. Tests for bot/config/cost_model.py — the cost-model
# boundary. Must prove crypto behavior is byte-identical to the existing,
# untouched get_venue_execution_costs(), and that a non-CRYPTO asset class
# fails closed rather than silently inheriting Binance-modeled numbers.

from django.test import SimpleTestCase

from bot.config.cost_model import CryptoCostModel, UnsupportedAssetClassCostModel, get_cost_model
from bot.config.execution_costs import get_venue_execution_costs
from bot.instruments import ASSET_CLASS_CRYPTO, ASSET_CLASS_FOREX, ASSET_CLASS_US_EQUITY


class CryptoCostModelTest(SimpleTestCase):

    def test_matches_existing_venue_costs_exactly(self):
        """claude code changed: the exact backward-compatibility guarantee
        — this new boundary must never produce a different number than
        the existing, unmodified get_venue_execution_costs()."""
        for venue_id in ("binance", "kraken"):
            with self.subTest(venue_id=venue_id):
                model = get_cost_model(ASSET_CLASS_CRYPTO, venue_id=venue_id)
                self.assertEqual(model.get_costs(), get_venue_execution_costs(venue_id))

    def test_defaults_to_binance(self):
        model = get_cost_model(ASSET_CLASS_CRYPTO)
        self.assertIsInstance(model, CryptoCostModel)
        self.assertEqual(model.venue_id, "binance")


class UnsupportedAssetClassTest(SimpleTestCase):
    """claude code changed: the fail-closed guarantee section 13 of the
    refactor brief and the architecture gate's own risk register both
    called for — a real gap must raise, never silently borrow crypto
    economics."""

    def test_forex_has_no_cost_model_yet(self):
        with self.assertRaises(UnsupportedAssetClassCostModel):
            get_cost_model(ASSET_CLASS_FOREX)

    def test_us_equity_has_no_cost_model_yet(self):
        with self.assertRaises(UnsupportedAssetClassCostModel):
            get_cost_model(ASSET_CLASS_US_EQUITY)

    def test_unrecognized_asset_class_string_fails_closed(self):
        with self.assertRaises(UnsupportedAssetClassCostModel):
            get_cost_model("COMMODITIES")
