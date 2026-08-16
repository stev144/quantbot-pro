# claude code changed: new file — Kraken Multi-Venue Execution, Step 2.
# Confirms the ExchangeAdapter contract is actually enforced in both
# directions: an incomplete subclass cannot be instantiated (the whole
# point of using abc.ABC — a missing method is a hard error, not a
# silent gap a venue could exploit later), and a fully-implemented one can.

from django.test import SimpleTestCase

from bot.engines.exchange_adapter import ExchangeAdapter


class ExchangeAdapterContractTest(SimpleTestCase):

    def test_cannot_instantiate_the_bare_interface(self):
        with self.assertRaises(TypeError):
            ExchangeAdapter()

    def test_incomplete_subclass_cannot_be_instantiated(self):
        class IncompleteAdapter(ExchangeAdapter):
            venue_id = "incomplete"
            # every other abstract method deliberately left unimplemented

        with self.assertRaises(TypeError):
            IncompleteAdapter()

    def test_fully_implemented_subclass_can_be_instantiated(self):
        class DummyAdapter(ExchangeAdapter):
            venue_id = "dummy"

            def get_ticker(self, symbol):
                return {"last": 1.0, "bid": 0.99, "ask": 1.01, "timestamp": 0}

            def get_ohlcv(self, symbol, timeframe, limit):
                return []

            def get_balance(self, currency):
                return 0.0

            def place_order(self, symbol, side, order_type, quantity, price=None):
                return {}

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
                return True, "ok"

        adapter = DummyAdapter()
        self.assertEqual(adapter.venue_id, "dummy")
        self.assertEqual(adapter.get_execution_costs(), {"fee_rate": 0.0, "slippage_rate": 0.0})
