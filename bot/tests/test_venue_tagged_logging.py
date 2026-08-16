# claude code changed: new file — Kraken Multi-Venue Execution, Step 15.
# Proves OrderManager/MarketData log lines are actually venue-tagged, not
# just that self.venue_id is set. Real ccxt instances, no mocking.

import ccxt
from django.test import SimpleTestCase

from bot.engines.market_data import MarketData
from bot.engines.order_manager import OrderManager


class OrderManagerVenueTaggingTest(SimpleTestCase):

    def test_venue_id_set_from_real_exchange(self):
        binance_om = OrderManager(ccxt.binance())
        kraken_om = OrderManager(ccxt.kraken())
        self.assertEqual(binance_om.venue_id, "binance")
        self.assertEqual(kraken_om.venue_id, "kraken")

    def test_venue_id_falls_back_to_unknown_when_exchange_is_none(self):
        om = OrderManager(exchange=None)
        self.assertEqual(om.venue_id, "unknown")

    def test_dry_run_place_order_log_line_carries_venue_tag(self):
        om = OrderManager(ccxt.kraken(), dry_run=True)
        with self.assertLogs("bot.engines.order_manager", level="INFO") as cm:
            om.place_order("BTC/USDT", "buy", 0.001, price=50000.0)

        self.assertTrue(any("[OrderManager:kraken]" in line for line in cm.output))


class MarketDataVenueTaggingTest(SimpleTestCase):

    def test_venue_id_set_from_real_exchange(self):
        binance_md = MarketData(ccxt.binance())
        kraken_md = MarketData(ccxt.kraken())
        self.assertEqual(binance_md.venue_id, "binance")
        self.assertEqual(kraken_md.venue_id, "kraken")

    def test_venue_id_falls_back_to_unknown_when_exchange_is_none(self):
        md = MarketData(exchange=None)
        self.assertEqual(md.venue_id, "unknown")

    def test_dry_run_balance_log_line_carries_venue_tag(self):
        md = MarketData(ccxt.kraken(), dry_run=True)
        with self.assertLogs("bot.engines.market_data", level="INFO") as cm:
            md.get_balance()

        self.assertTrue(any("[MarketData:kraken]" in line for line in cm.output))
