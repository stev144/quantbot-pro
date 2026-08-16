# claude code changed: new file — Kraken Multi-Venue Execution, Step 17.
# Real network calls, no mocking, matching this project's convention.

import ccxt
from django.test import SimpleTestCase

from bot.engines.binance_adapter import BinanceAdapter
from bot.engines.exchange_adapter import ExchangeAdapter
from bot.engines.kraken_adapter import KrakenAdapter
from bot.engines.venue_readiness import VenueReadiness, assess_venue_readiness


class AssessVenueReadinessRealAdaptersTest(SimpleTestCase):

    def test_binance_is_dry_run_ready_without_credentials(self):
        report = assess_venue_readiness(BinanceAdapter(ccxt.binance(), dry_run=True))
        self.assertEqual(report.venue_id, "binance")
        self.assertEqual(report.classification, VenueReadiness.DRY_RUN_READY)
        self.assertTrue(report.checks["dry_run_order_works"])
        self.assertTrue(report.checks["public_connectivity"])
        self.assertFalse(report.checks["authenticated_connectivity"])

    def test_kraken_is_dry_run_ready_without_credentials(self):
        report = assess_venue_readiness(KrakenAdapter(ccxt.kraken(), dry_run=True))
        self.assertEqual(report.venue_id, "kraken")
        self.assertEqual(report.classification, VenueReadiness.DRY_RUN_READY)
        self.assertTrue(report.checks["dry_run_order_works"])
        self.assertTrue(report.checks["public_connectivity"])
        self.assertFalse(report.checks["authenticated_connectivity"])

    def test_every_report_notes_the_paper_trading_caveat(self):
        for adapter in (BinanceAdapter(ccxt.binance(), dry_run=True),
                        KrakenAdapter(ccxt.kraken(), dry_run=True)):
            report = assess_venue_readiness(adapter)
            self.assertTrue(any("PAPER_TRADING_READY" in note for note in report.notes))
            self.assertNotEqual(report.classification, VenueReadiness.PAPER_TRADING_READY)


class AssessVenueReadinessBrokenAdapterTest(SimpleTestCase):

    def test_broken_adapter_classifies_as_not_ready(self):
        class BrokenAdapter(ExchangeAdapter):
            venue_id = "broken"

            def get_ticker(self, symbol):
                return {"last": 100.0}

            def get_ohlcv(self, symbol, timeframe, limit):
                return []

            def get_balance(self, currency):
                return 0.0

            def place_order(self, symbol, side, order_type, quantity, price=None):
                raise RuntimeError("always fails")

            def place_stop_loss(self, symbol, side, quantity, stop_price):
                return {}

            def cancel_order(self, order_id, symbol):
                return {}

            def get_order(self, order_id, symbol):
                return {}

            def get_open_orders(self, symbol):
                return []

            def get_order_book(self, symbol, limit=20):
                return {"symbol": symbol, "bids": [], "asks": [], "timestamp": None}

            def normalize_symbol(self, canonical_symbol):
                return canonical_symbol

            def normalize_quantity(self, symbol, quantity):
                return quantity

            def normalize_price(self, symbol, price):
                return price

            def validate_order(self, symbol, quantity, price=None):
                return True, ""

            def get_execution_costs(self, symbol=None):
                return {"fee_rate": 0.0, "slippage_rate": 0.0}

            def validate_connection(self):
                return False, "never connected"

        report = assess_venue_readiness(BrokenAdapter())
        self.assertEqual(report.classification, VenueReadiness.NOT_READY)
        self.assertFalse(report.checks["dry_run_order_works"])
