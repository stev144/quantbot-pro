# claude code changed: new file — Kraken Multi-Venue Execution, Step 9.
# Unit tests against literal, hand-computed fixture order books, plus an
# end-to-end real-data pass against both live venues (no mocking).

import ccxt
from django.test import SimpleTestCase

from bot.engines.binance_adapter import BinanceAdapter
from bot.engines.kraken_adapter import KrakenAdapter
from bot.engines.liquidity import compute_liquidity_snapshot


class ComputeLiquiditySnapshotFixtureTest(SimpleTestCase):

    def setUp(self):
        # claude code changed: hand-computed fixture — mid = (100 + 100.2) / 2
        # = 100.1, spread_abs = 0.2, spread_pct = 0.2 / 100.1.
        self.order_book = {
            "symbol": "BTC/USDT",
            "bids": [[100.0, 1.0], [99.9, 2.0], [99.0, 5.0]],
            "asks": [[100.2, 1.5], [100.3, 2.5], [101.0, 4.0]],
            "timestamp": 1700000000000,
        }

    def test_best_bid_ask_and_mid(self):
        snap = compute_liquidity_snapshot(self.order_book)
        self.assertEqual(snap.best_bid, 100.0)
        self.assertEqual(snap.best_ask, 100.2)
        self.assertAlmostEqual(snap.mid_price, 100.1)

    def test_spread(self):
        snap = compute_liquidity_snapshot(self.order_book)
        self.assertAlmostEqual(snap.spread_abs, 0.2)
        self.assertAlmostEqual(snap.spread_pct, 0.2 / 100.1)

    def test_depth_within_default_band(self):
        # depth_band_pct=0.005 -> mid=100.1, bid_floor=99.6005, ask_ceiling=100.6005
        # bids within band: 100.0 (1.0) and 99.9 (2.0) -> 99.0 excluded (< 99.6005)
        # asks within band: 100.2 (1.5) and 100.3 (2.5) -> 101.0 excluded (> 100.6005)
        snap = compute_liquidity_snapshot(self.order_book, depth_band_pct=0.005)
        expected_bid_depth = 100.0 * 1.0 + 99.9 * 2.0
        expected_ask_depth = 100.2 * 1.5 + 100.3 * 2.5
        self.assertAlmostEqual(snap.bid_depth_notional, expected_bid_depth)
        self.assertAlmostEqual(snap.ask_depth_notional, expected_ask_depth)

    def test_symbol_and_band_are_carried_through(self):
        snap = compute_liquidity_snapshot(self.order_book, depth_band_pct=0.01)
        self.assertEqual(snap.symbol, "BTC/USDT")
        self.assertEqual(snap.depth_band_pct, 0.01)


class ComputeLiquiditySnapshotEdgeCaseTest(SimpleTestCase):

    def test_empty_bids_raises(self):
        with self.assertRaises(ValueError):
            compute_liquidity_snapshot({"symbol": "BTC/USDT", "bids": [], "asks": [[100.2, 1.0]]})

    def test_empty_asks_raises(self):
        with self.assertRaises(ValueError):
            compute_liquidity_snapshot({"symbol": "BTC/USDT", "bids": [[100.0, 1.0]], "asks": []})

    def test_crossed_book_raises(self):
        crossed = {"symbol": "BTC/USDT", "bids": [[101.0, 1.0]], "asks": [[100.0, 1.0]]}
        with self.assertRaises(ValueError):
            compute_liquidity_snapshot(crossed)


class ComputeLiquiditySnapshotRealDataTest(SimpleTestCase):
    """End-to-end: real order books from both venues (no mocking)."""

    def _assert_sane(self, snap):
        self.assertGreater(snap.best_ask, snap.best_bid)
        self.assertGreaterEqual(snap.spread_pct, 0)
        self.assertGreaterEqual(snap.bid_depth_notional, 0)
        self.assertGreaterEqual(snap.ask_depth_notional, 0)

    def test_real_binance_order_book(self):
        adapter = BinanceAdapter(ccxt.binance(), dry_run=True)
        ob = adapter.get_order_book("BTC/USDT", limit=20)
        self._assert_sane(compute_liquidity_snapshot(ob))

    def test_real_kraken_order_book(self):
        adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)
        ob = adapter.get_order_book("BTC/USDT", limit=20)
        self._assert_sane(compute_liquidity_snapshot(ob))
