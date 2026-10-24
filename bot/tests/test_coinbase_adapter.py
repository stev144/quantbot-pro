# claude code changed: new file — 2026-10-22, CoinbaseAdapter (third venue). Real PUBLIC Coinbase Advanced Trade
# endpoints through ccxt — no mocked exchange (project convention); no credentials, no order ever leaves the process.
import os
from unittest.mock import patch

import ccxt
from django.test import SimpleTestCase

from bot.config.execution_costs import VENUE_FEE_PROVENANCE, get_venue_execution_costs
from bot.engines.ccxt_spot_adapter import CcxtSpotAdapter
from bot.engines.coinbase_adapter import CoinbaseAdapter, CoinbaseLiveStopUnverified, build_coinbase_adapter
from bot.engines.exchange_adapter import ExchangeAdapter

_EX = None


def loaded_exchange():
    global _EX
    if _EX is None:
        _EX = ccxt.coinbase()
        _EX.load_markets()
    return _EX


class CoinbaseContractTest(SimpleTestCase):
    def test_is_an_instantiable_exchange_adapter_sharing_the_spot_base(self):
        a = CoinbaseAdapter(ccxt.coinbase(), dry_run=True)
        self.assertIsInstance(a, ExchangeAdapter)
        self.assertIsInstance(a, CcxtSpotAdapter)
        self.assertEqual(a.venue_id, "coinbase")


class CoinbaseDryRunTest(SimpleTestCase):
    def test_dry_run_fill_uses_coinbases_own_fee_not_binances(self):
        a = CoinbaseAdapter(ccxt.coinbase(), dry_run=True)
        r = a.place_order("BTC/USDT", "buy", "market", 0.001)
        self.assertGreater(r["fill_price"], 0)
        self.assertAlmostEqual(r["fee_usdt"] / (r["fill_price"] * 0.001), 0.0060, places=6)

    def test_stop_loss_is_skipped_in_dry_run_and_refused_live(self):
        dry = CoinbaseAdapter(ccxt.coinbase(), dry_run=True).place_stop_loss("BTC/USDT", "sell", 0.001, 50_000)
        self.assertEqual(dry["status"], "dry_run_skipped")
        self.assertIsNone(dry["order_id"])
        live = CoinbaseAdapter(ccxt.coinbase(), dry_run=False)
        with self.assertRaises(CoinbaseLiveStopUnverified):
            live.place_stop_loss("BTC/USDT", "sell", 0.001, 50_000)


class CoinbaseNormalizationTest(SimpleTestCase):
    def setUp(self):
        self.a = CoinbaseAdapter(loaded_exchange(), dry_run=True)

    def test_direct_listing(self):
        self.assertEqual(self.a.normalize_symbol("BTC/USDT"), "BTC/USDT")

    def test_usd_fallback_for_an_alt_without_a_usdt_market(self):
        self.assertNotIn("AAVE/USDT", self.a.exchange.markets)
        self.assertEqual(self.a.normalize_symbol("AAVE/USDT"), "AAVE/USD")

    def test_unlisted_returns_none(self):
        self.assertIsNone(self.a.normalize_symbol("NOTACOIN123/USDT"))

    def test_inactive_market_is_not_treated_as_listed(self):
        markets = dict(self.a.exchange.markets)
        with patch.object(self.a.exchange, "markets", {**markets, "BTC/USDT": {**markets["BTC/USDT"], "active": False}}):
            self.assertEqual(self.a.normalize_symbol("BTC/USDT"), "BTC/USD")     # falls back, never the inactive one

    def test_precision_matches_ccxt(self):
        self.assertEqual(self.a.normalize_quantity("BTC/USD", 0.123456789123),
                         float(self.a.exchange.amount_to_precision("BTC/USD", 0.123456789123)))


class CoinbaseOrderBookAndLimitsTest(SimpleTestCase):
    def setUp(self):
        self.a = CoinbaseAdapter(loaded_exchange(), dry_run=True)

    def test_order_book_levels_are_price_amount_pairs(self):
        ob = self.a.get_order_book("BTC/USD", 10)
        self.assertTrue(ob["bids"] and ob["asks"])
        self.assertTrue(all(len(level) == 2 for level in ob["bids"] + ob["asks"]))
        self.assertLess(ob["bids"][0][0], ob["asks"][0][0])

    def test_validate_order_uses_real_limits(self):
        ok, _ = self.a.validate_order("BTC/USD", 0.001, price=80_000)
        self.assertTrue(ok)
        bad, msg = self.a.validate_order("BTC/USD", 0.00001, price=50)   # $0.0005 notional < $1 minimum
        self.assertFalse(bad)
        self.assertIn("minimum", msg)
        self.assertFalse(self.a.validate_order("NOPE/USD", 1.0)[0])


class CoinbaseCostsTest(SimpleTestCase):
    def test_venue_table_and_provenance(self):
        self.assertEqual(get_venue_execution_costs("coinbase")["fee_rate"], 0.0060)
        self.assertFalse(VENUE_FEE_PROVENANCE["coinbase"]["verified"])     # official page could not be read
        self.assertTrue(VENUE_FEE_PROVENANCE["kraken"]["verified"])


class BuildCoinbaseAdapterTest(SimpleTestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(build_coinbase_adapter())

    def test_enabled_dry_run_needs_no_credentials(self):
        with patch.dict(os.environ, {"COINBASE_ENABLED": "true"}, clear=True):
            a = build_coinbase_adapter()
        self.assertIsInstance(a, CoinbaseAdapter)
        self.assertTrue(a.dry_run)

    def test_live_without_credentials_is_refused(self):
        with patch.dict(os.environ, {"COINBASE_ENABLED": "true", "COINBASE_DRY_RUN": "false"}, clear=True):
            self.assertIsNone(build_coinbase_adapter())


class CoinbaseCredentialedMethodsFailCleanlyTest(SimpleTestCase):
    def test_validate_connection_reports_missing_credentials(self):
        ok, msg = CoinbaseAdapter(ccxt.coinbase(), dry_run=True).validate_connection()
        self.assertFalse(ok)
        self.assertIn("fetch_balance", msg)
