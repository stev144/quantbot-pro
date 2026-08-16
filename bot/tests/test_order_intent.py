# claude code changed: new file — Kraken Multi-Venue Execution, Step 6.
# Validation tests, factory-equivalence tests (proving for_entry()/for_exit()/
# for_position() reproduce execution_engine.py's real inline kwarg-building
# logic exactly), and end-to-end venue-agnostic consumability tests against
# both real adapters (no mocking, matching this project's convention).

import ccxt
from django.test import SimpleTestCase

from bot.engines.binance_adapter import BinanceAdapter
from bot.engines.kraken_adapter import KrakenAdapter
from bot.engines.order_intent import OrderIntent, OrderSide, OrderType, StopLossIntent


class OrderIntentValidationTest(SimpleTestCase):

    def test_rejects_non_positive_quantity(self):
        with self.assertRaises(ValueError):
            OrderIntent("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0, price=None)
        with self.assertRaises(ValueError):
            OrderIntent("BTC/USDT", OrderSide.BUY, OrderType.MARKET, -1, price=None)

    def test_limit_order_requires_a_price(self):
        with self.assertRaises(ValueError):
            OrderIntent("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.01, price=None)

    def test_market_order_rejects_a_stray_price(self):
        # claude code changed: unlike BinanceAdapter, which silently drops
        # a stray price on a market order (see test_binance_adapter.py's
        # test_market_order_type_ignores_a_stray_price), OrderIntent
        # rejects it outright at construction time.
        with self.assertRaises(ValueError):
            OrderIntent("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.01, price=50000.0)

    def test_valid_limit_order_constructs(self):
        intent = OrderIntent("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.01, price=50000.0)
        self.assertEqual(intent.price, 50000.0)

    def test_valid_market_order_constructs(self):
        intent = OrderIntent("BTC/USDT", OrderSide.SELL, OrderType.MARKET, 0.01)
        self.assertIsNone(intent.price)


class StopLossIntentValidationTest(SimpleTestCase):

    def test_rejects_non_positive_quantity(self):
        with self.assertRaises(ValueError):
            StopLossIntent("BTC/USDT", OrderSide.SELL, 0, stop_price=45000.0)

    def test_rejects_non_positive_stop_price(self):
        with self.assertRaises(ValueError):
            StopLossIntent("BTC/USDT", OrderSide.SELL, 0.01, stop_price=0)


class OrderIntentFactoryEquivalenceTest(SimpleTestCase):
    """
    Proves the factories reproduce execution_engine.py's real inline
    kwarg-building logic exactly, using the same signal/position dict
    shapes documented in execute_signal()'s docstring and STEP 7's
    position dict construction.
    """

    def setUp(self):
        # claude code changed: literal signal dict shape copied from
        # execute_signal()'s own docstring.
        self.signal = {
            "signal": "BUY",
            "entry": 5.00,
            "sl": 4.90,
            "tp": 5.20,
            "rsi": 46.2,
            "reason": "ema_buy_setup",
            "strategy": "MovingAverageStrategy",
            "symbol": "NEAR/USDT",
        }

    def test_for_entry_matches_execute_signals_inline_logic(self):
        # execute_signal() STEP 4-5: order_side = signal["signal"].lower();
        # order_manager.place_order(symbol=symbol, side=order_side,
        # quantity=quantity, price=signal["entry"]) — always a limit order.
        intent = OrderIntent.for_entry(self.signal, quantity=100.0)
        self.assertEqual(intent.symbol, "NEAR/USDT")
        self.assertEqual(intent.side, OrderSide.BUY)
        self.assertEqual(intent.order_type, OrderType.LIMIT)
        self.assertEqual(intent.quantity, 100.0)
        self.assertEqual(intent.price, 5.00)

    def test_for_entry_inverts_side_for_sell_signal(self):
        sell_signal = dict(self.signal, signal="SELL")
        intent = OrderIntent.for_entry(sell_signal, quantity=100.0)
        self.assertEqual(intent.side, OrderSide.SELL)

    def test_for_exit_matches_close_positions_inline_logic(self):
        # _close_position(): exit_side = "sell" if position["side"] == "BUY"
        # else "buy"; order_manager.place_order(symbol=, side=exit_side,
        # quantity=position["quantity"], price=None) — always market.
        position = {
            "symbol": "NEAR/USDT",
            "side": "BUY",
            "entry_price": 5.00,
            "sl": 4.90,
            "tp": 5.20,
            "quantity": 100.0,
            "order_id": "ABC123",
            "fee_entry": 0.05,
            "risk_amount": 10.0,
            "rsi": 46.2,
            "strategy": "MovingAverageStrategy",
            "reason": "ema_buy_setup",
            "sl_order_id": None,
        }
        intent = OrderIntent.for_exit("NEAR/USDT", position)
        self.assertEqual(intent.side, OrderSide.SELL)  # opposite of BUY
        self.assertEqual(intent.order_type, OrderType.MARKET)
        self.assertEqual(intent.quantity, 100.0)
        self.assertIsNone(intent.price)

    def test_for_exit_inverts_side_for_short_position(self):
        position = {"side": "SELL", "quantity": 50.0}
        intent = OrderIntent.for_exit("NEAR/USDT", position)
        self.assertEqual(intent.side, OrderSide.BUY)  # opposite of SELL

    def test_for_position_matches_step_7bs_inline_logic(self):
        # execute_signal() STEP 7B: exit_side_for_stop = "sell" if
        # signal["signal"] == "BUY" else "buy"; order_manager.place_stop_loss(
        # symbol=, side=exit_side_for_stop, quantity=result["filled_qty"],
        # stop_price=signal["sl"]).
        intent = StopLossIntent.for_position("NEAR/USDT", self.signal, filled_qty=100.0)
        self.assertEqual(intent.symbol, "NEAR/USDT")
        self.assertEqual(intent.side, OrderSide.SELL)  # protects a BUY
        self.assertEqual(intent.quantity, 100.0)
        self.assertEqual(intent.stop_price, 4.90)

    def test_for_position_inverts_side_for_sell_signal(self):
        sell_signal = dict(self.signal, signal="SELL")
        intent = StopLossIntent.for_position("NEAR/USDT", sell_signal, filled_qty=100.0)
        self.assertEqual(intent.side, OrderSide.BUY)  # protects a SELL


class OrderIntentKwargConversionTest(SimpleTestCase):

    def test_to_place_order_kwargs_shape(self):
        intent = OrderIntent("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.01, price=50000.0)
        self.assertEqual(
            intent.to_place_order_kwargs(),
            {"symbol": "BTC/USDT", "side": "buy", "order_type": "limit",
             "quantity": 0.01, "price": 50000.0},
        )

    def test_to_place_stop_loss_kwargs_shape(self):
        intent = StopLossIntent("BTC/USDT", OrderSide.SELL, 0.01, stop_price=45000.0)
        self.assertEqual(
            intent.to_place_stop_loss_kwargs(),
            {"symbol": "BTC/USDT", "side": "sell", "quantity": 0.01, "stop_price": 45000.0},
        )


class OrderIntentCrossVenueConsumabilityTest(SimpleTestCase):
    """
    End-to-end proof (no mocking) that the SAME OrderIntent/StopLossIntent
    object is genuinely consumable by both adapters already built in
    Steps 3-4 — not just type-compatible on paper.
    """

    def test_same_order_intent_consumed_by_both_adapters_dry_run(self):
        intent = OrderIntent("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.001)
        kwargs = intent.to_place_order_kwargs()

        binance = BinanceAdapter(ccxt.binance(), dry_run=True)
        kraken = KrakenAdapter(ccxt.kraken(), dry_run=True)

        binance_result = binance.place_order(**kwargs)
        kraken_result = kraken.place_order(**kwargs)

        for result in (binance_result, kraken_result):
            for key in ("fill_price", "filled_qty", "fee_usdt", "order_id"):
                self.assertIn(key, result)
            self.assertEqual(result["filled_qty"], 0.001)

    def test_same_stop_loss_intent_consumed_by_both_adapters_dry_run(self):
        intent = StopLossIntent("BTC/USDT", OrderSide.SELL, 0.001, stop_price=45000.0)
        kwargs = intent.to_place_stop_loss_kwargs()

        binance = BinanceAdapter(ccxt.binance(), dry_run=True)
        kraken = KrakenAdapter(ccxt.kraken(), dry_run=True)

        binance_result = binance.place_stop_loss(**kwargs)
        kraken_result = kraken.place_stop_loss(**kwargs)

        for result in (binance_result, kraken_result):
            self.assertIsNone(result["order_id"])
            self.assertEqual(result["status"], "dry_run_skipped")
            self.assertEqual(result["stop_price"], 45000.0)
