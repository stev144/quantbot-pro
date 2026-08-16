# claude code changed: new file — Kraken Multi-Venue Execution, Step 11.
# No mocking, matching this project's convention — real order books from
# both venues (public endpoint). Also exercises Step 5's normalize_symbol
# fallback/non-availability findings for the first time in real use
# outside their own tests.

import ccxt
from django.test import SimpleTestCase

from bot.engines.binance_adapter import BinanceAdapter
from bot.engines.execution_comparison import _walk_book, compare_venues, get_venue_quote
from bot.engines.kraken_adapter import KrakenAdapter


class WalkBookTest(SimpleTestCase):

    def test_enough_depth_returns_quantity_weighted_price(self):
        # 1.0 @ 100 + 0.5 @ 101 = fill 1.5 units -> (100*1 + 101*0.5)/1.5
        levels = [[100.0, 1.0], [101.0, 1.0], [102.0, 1.0]]
        price = _walk_book(levels, 1.5)
        expected = (100.0 * 1.0 + 101.0 * 0.5) / 1.5
        self.assertAlmostEqual(price, expected)

    def test_exact_single_level_depth(self):
        levels = [[100.0, 2.0]]
        price = _walk_book(levels, 2.0)
        self.assertAlmostEqual(price, 100.0)

    def test_insufficient_depth_returns_none(self):
        levels = [[100.0, 1.0], [101.0, 1.0]]
        self.assertIsNone(_walk_book(levels, 5.0))


class GetVenueQuoteRealDataTest(SimpleTestCase):

    def test_binance_btc_usdt_is_available(self):
        adapter = BinanceAdapter(ccxt.binance(), dry_run=True)
        quote = get_venue_quote(adapter, "BTC/USDT", "buy", 0.01)

        self.assertTrue(quote.available)
        self.assertEqual(quote.venue_id, "binance")
        self.assertGreater(quote.estimated_fill_price, 0)
        self.assertGreaterEqual(quote.estimated_slippage_pct, 0)
        self.assertGreater(quote.spread_pct, -0.0001)

    def test_kraken_btc_usdt_is_available_with_krakens_fee(self):
        adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)
        quote = get_venue_quote(adapter, "BTC/USDT", "buy", 0.01)

        self.assertTrue(quote.available)
        self.assertEqual(quote.venue_id, "kraken")
        self.assertEqual(quote.fee_rate, 0.0026)

    def test_kraken_matic_usdt_is_unavailable(self):
        # claude code changed: Step 5's confirmed real, fully-unlisted case
        # (Polygon rebranded to POL; Kraken tracks the new ticker, not the
        # old MATIC base symbol). No order book call is even attempted
        # once normalize_symbol() returns None.
        adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)
        quote = get_venue_quote(adapter, "MATIC/USDT", "buy", 1.0)

        self.assertFalse(quote.available)
        self.assertIn("not tradeable", quote.unavailable_reason)

    def test_kraken_aave_usdt_falls_back_to_usd_quote(self):
        # claude code changed: Step 5's confirmed /USD-fallback case — the
        # first real end-to-end use of that fallback mechanism outside its
        # own tests.
        adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)
        quote = get_venue_quote(adapter, "AAVE/USDT", "buy", 0.1)

        self.assertTrue(quote.available)
        self.assertEqual(quote.symbol, "AAVE/USD")

    def test_rejects_non_positive_quantity(self):
        adapter = BinanceAdapter(ccxt.binance(), dry_run=True)
        with self.assertRaises(ValueError):
            get_venue_quote(adapter, "BTC/USDT", "buy", 0)


class CompareVenuesRealDataTest(SimpleTestCase):

    def test_returns_one_sorted_quote_per_adapter(self):
        adapters = [
            BinanceAdapter(ccxt.binance(), dry_run=True),
            KrakenAdapter(ccxt.kraken(), dry_run=True),
        ]
        quotes = compare_venues(adapters, "BTC/USDT", "buy", 0.01)

        self.assertEqual(len(quotes), 2)
        self.assertTrue(all(q.available for q in quotes))
        # cheapest-first
        self.assertLessEqual(quotes[0].estimated_total_cost_pct, quotes[1].estimated_total_cost_pct)
