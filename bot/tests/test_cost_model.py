# claude code changed: new file — Multi-Asset Foundation Refactor Phase
# 1A, STEP 8. Tests for bot/config/cost_model.py — the cost-model
# boundary. Must prove crypto behavior is byte-identical to the existing,
# untouched get_venue_execution_costs(), and that a non-CRYPTO asset class
# fails closed rather than silently inheriting Binance-modeled numbers.

from django.test import SimpleTestCase

from bot.config.cost_model import CryptoCostModel, ForexCostModel, ForexCostModelDataError, UnsupportedAssetClassCostModel, get_cost_model
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


class ForexCostModelTest(SimpleTestCase):
    """claude code changed: new — real positive-case coverage for
    ForexCostModel, which had none: only its ABSENCE was tested before
    the Forex Multi-Asset Integration mission. Uses the real, committed
    data/forex/*.csv fixtures — no mocking, per this project's convention."""

    def test_derives_a_real_reference_price_no_fee_only_slippage(self):
        costs = get_cost_model(ASSET_CLASS_FOREX, symbol="EUR/USD").get_costs()
        self.assertEqual(costs["fee_rate"], 0.0)
        self.assertGreater(costs["slippage_rate"], 0.0)
        # a plausible order of magnitude for a ~1 pip / ~1.1-1.2 price ratio
        self.assertLess(costs["slippage_rate"], 0.001)

    def test_jpy_quote_pip_size_produces_a_plausible_cost_not_off_by_100x(self):
        """USD/JPY prices ~100+ and uses a 0.01 pip (not 0.0001) — real bug
        class this guards: a wrong pip size here would make the resulting
        rate absurdly large or small relative to every other major."""
        jpy = get_cost_model(ASSET_CLASS_FOREX, symbol="USD/JPY").get_costs()
        eur = get_cost_model(ASSET_CLASS_FOREX, symbol="EUR/USD").get_costs()
        ratio = jpy["slippage_rate"] / eur["slippage_rate"]
        self.assertGreater(ratio, 0.3)
        self.assertLess(ratio, 3.0)

    def test_different_pairs_get_their_own_real_price_not_a_shared_one(self):
        prices = {
            pair: ForexCostModel(pair=pair)._reference_price()
            for pair in ("EUR/USD", "USD/JPY", "AUD/USD")
        }
        self.assertGreater(prices["USD/JPY"], 50)   # real JPY price scale
        self.assertLess(prices["EUR/USD"], 5)       # real major-pair scale
        self.assertNotEqual(prices["EUR/USD"], prices["AUD/USD"])

    def test_deterministic_across_calls(self):
        a = get_cost_model(ASSET_CLASS_FOREX, symbol="EUR/USD").get_costs()
        b = get_cost_model(ASSET_CLASS_FOREX, symbol="EUR/USD").get_costs()
        self.assertEqual(a, b)

    def test_unregistered_or_unfetched_pair_fails_closed_not_a_guess(self):
        with self.assertRaises(ForexCostModelDataError):
            ForexCostModel(pair="GBP/JPY").get_costs()   # registered? no data ingested for this cross in the current 8-pair universe


class UnsupportedAssetClassTest(SimpleTestCase):
    """claude code changed: the fail-closed guarantee section 13 of the
    refactor brief and the architecture gate's own risk register both
    called for — a real gap must raise, never silently borrow crypto
    economics."""

    def test_forex_requires_a_symbol_not_unsupported(self):
        # claude code changed: was test_forex_has_no_cost_model_yet,
        # asserting get_cost_model(FOREX) raised UnsupportedAssetClassCostModel
        # — real bug, confirmed by running this test directly: FOREX has
        # had a real ForexCostModel since the Forex Multi-Asset
        # Integration mission, so this stale assertion now fails with a
        # ValueError instead (a *different* fail-closed reason: FOREX
        # needs a `symbol` kwarg to look up a real reference price, it is
        # not "unimplemented"). Never caught before because the full test
        # suite has not completed successfully since that mission.
        with self.assertRaises(ValueError):
            get_cost_model(ASSET_CLASS_FOREX)

    def test_us_equity_has_no_cost_model_yet(self):
        with self.assertRaises(UnsupportedAssetClassCostModel):
            get_cost_model(ASSET_CLASS_US_EQUITY)

    def test_unrecognized_asset_class_string_fails_closed(self):
        with self.assertRaises(UnsupportedAssetClassCostModel):
            get_cost_model("COMMODITIES")
