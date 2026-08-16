# claude code changed: new file — Kraken Multi-Venue Execution, Step 9.
# Proves ExchangeAdapter.get_order_book()'s normalization is real, not a
# no-op, by fetching real order books (public endpoint, no mocking) from
# both venues and showing Kraken's raw 3-element levels get trimmed to 2.

import ccxt
from django.test import SimpleTestCase

from bot.engines.binance_adapter import BinanceAdapter
from bot.engines.kraken_adapter import KrakenAdapter


class GetOrderBookShapeTest(SimpleTestCase):

    def _assert_well_formed(self, order_book, symbol):
        self.assertEqual(order_book["symbol"], symbol)
        self.assertGreater(len(order_book["bids"]), 0)
        self.assertGreater(len(order_book["asks"]), 0)

        for level in order_book["bids"] + order_book["asks"]:
            self.assertEqual(len(level), 2)   # THE normalization proof
            price, amount = level
            self.assertGreater(price, 0)
            self.assertGreater(amount, 0)

        best_bid = order_book["bids"][0][0]
        best_ask = order_book["asks"][0][0]
        self.assertLess(best_bid, best_ask)

    def test_binance_order_book_is_well_formed(self):
        adapter = BinanceAdapter(ccxt.binance(), dry_run=True)
        ob = adapter.get_order_book("BTC/USDT", limit=10)
        self._assert_well_formed(ob, "BTC/USDT")

    def test_kraken_order_book_is_well_formed(self):
        adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)
        ob = adapter.get_order_book("BTC/USDT", limit=10)
        self._assert_well_formed(ob, "BTC/USDT")

    def test_kraken_raw_ccxt_levels_actually_have_a_third_element(self):
        # claude code changed: new — proves the adapter's normalization is
        # doing real work, not a no-op. Bypasses the adapter to show the
        # PRE-normalization shape ccxt itself returns for Kraken.
        exchange = ccxt.kraken()
        raw = exchange.fetch_order_book("BTC/USDT", 10)
        self.assertTrue(all(len(level) == 3 for level in raw["bids"]))
        self.assertTrue(all(len(level) == 3 for level in raw["asks"]))

        # Now confirm the adapter trims it to 2 on the SAME real fetch.
        adapter = KrakenAdapter(exchange, dry_run=True)
        ob = adapter.get_order_book("BTC/USDT", limit=10)
        self.assertTrue(all(len(level) == 2 for level in ob["bids"]))
        self.assertTrue(all(len(level) == 2 for level in ob["asks"]))
