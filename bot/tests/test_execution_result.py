# claude code changed: new file — Kraken Multi-Venue Execution, Step 7.
# Parses the real dicts BinanceAdapter/KrakenAdapter actually return (dry-run,
# no mocking — matching this project's convention), proving ExecutionResult/
# RestingOrderResult are faithful typed parsers of both venues' real output,
# not a speculative shape. Also proves Step 6's OrderIntent and Step 7's
# ExecutionResult genuinely correspond across the same real dry-run call.

import ccxt
from django.test import SimpleTestCase

from bot.engines.binance_adapter import BinanceAdapter
from bot.engines.kraken_adapter import KrakenAdapter
from bot.engines.execution_result import ExecutionResult, RestingOrderResult
from bot.engines.order_intent import OrderIntent, OrderSide, OrderType, StopLossIntent


class ExecutionResultParsingTest(SimpleTestCase):

    def _assert_matches_source_dict(self, execution_result, source: dict):
        self.assertEqual(execution_result.order_id, source["order_id"])
        self.assertEqual(execution_result.fill_price, source["fill_price"])
        self.assertEqual(execution_result.filled_qty, source["filled_qty"])
        self.assertEqual(execution_result.fee_usdt, source["fee_usdt"])
        self.assertEqual(execution_result.fee_currency, source["fee_currency"])
        self.assertEqual(execution_result.slippage_pct, source["slippage_pct"])
        self.assertEqual(execution_result.intended_price, source["intended_price"])
        self.assertEqual(execution_result.symbol, source["symbol"])
        self.assertEqual(execution_result.side.value, source["side"])
        self.assertEqual(execution_result.timestamp, source["timestamp"])
        self.assertEqual(execution_result.raw_order, source["order"])

    def test_parses_real_binance_dry_run_fill(self):
        adapter = BinanceAdapter(ccxt.binance(), dry_run=True)
        raw = adapter.place_order("BTC/USDT", "buy", "market", 0.001)
        result = ExecutionResult.from_adapter_result(raw)
        self._assert_matches_source_dict(result, raw)

    def test_parses_real_kraken_dry_run_fill(self):
        adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)
        raw = adapter.place_order("BTC/USDT", "sell", "market", 0.001)
        result = ExecutionResult.from_adapter_result(raw)
        self._assert_matches_source_dict(result, raw)


class ExecutionResultValidationTest(SimpleTestCase):

    def _base_kwargs(self):
        return dict(
            order_id="ABC", fill_price=50000.0, filled_qty=0.01, fee_usdt=0.5,
            fee_currency="USDT", slippage_pct=0.01, intended_price=50000.0,
            symbol="BTC/USDT", side=OrderSide.BUY, timestamp=1700000000000,
        )

    def test_rejects_negative_filled_qty(self):
        kwargs = self._base_kwargs()
        kwargs["filled_qty"] = -0.01
        with self.assertRaises(ValueError):
            ExecutionResult(**kwargs)

    def test_rejects_negative_fee(self):
        kwargs = self._base_kwargs()
        kwargs["fee_usdt"] = -1.0
        with self.assertRaises(ValueError):
            ExecutionResult(**kwargs)

    def test_accepts_valid_result(self):
        result = ExecutionResult(**self._base_kwargs())
        self.assertEqual(result.filled_qty, 0.01)


class RestingOrderResultParsingTest(SimpleTestCase):

    def test_parses_real_binance_dry_run_stop_loss(self):
        adapter = BinanceAdapter(ccxt.binance(), dry_run=True)
        raw = adapter.place_stop_loss("BTC/USDT", "sell", 0.001, 45000.0)
        result = RestingOrderResult.from_adapter_result(raw)
        self.assertIsNone(result.order_id)
        self.assertEqual(result.stop_price, raw["stop_price"])
        self.assertEqual(result.status, raw["status"])
        self.assertEqual(result.symbol, raw["symbol"])
        self.assertEqual(result.side.value, raw["side"])

    def test_parses_real_kraken_dry_run_stop_loss(self):
        adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)
        raw = adapter.place_stop_loss("BTC/USDT", "sell", 0.001, 45000.0)
        result = RestingOrderResult.from_adapter_result(raw)
        self.assertIsNone(result.order_id)
        self.assertEqual(result.stop_price, raw["stop_price"])
        self.assertEqual(result.status, raw["status"])
        self.assertEqual(result.symbol, raw["symbol"])
        self.assertEqual(result.side.value, raw["side"])


class OrderIntentExecutionResultRoundTripTest(SimpleTestCase):
    """
    Ties Step 6 (OrderIntent) and Step 7 (ExecutionResult) together: build
    one OrderIntent, feed it into both adapters via to_place_order_kwargs(),
    parse each raw result into ExecutionResult, and confirm the response
    genuinely corresponds to the request — on both real venues.
    """

    def test_round_trip_matches_on_both_venues(self):
        intent = OrderIntent("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.002)
        kwargs = intent.to_place_order_kwargs()

        for adapter in (BinanceAdapter(ccxt.binance(), dry_run=True),
                        KrakenAdapter(ccxt.kraken(), dry_run=True)):
            raw = adapter.place_order(**kwargs)
            result = ExecutionResult.from_adapter_result(raw)
            self.assertEqual(result.symbol, intent.symbol)
            self.assertEqual(result.filled_qty, intent.quantity)
            self.assertEqual(result.side, intent.side)

    def test_stop_loss_round_trip_matches_on_both_venues(self):
        intent = StopLossIntent("BTC/USDT", OrderSide.SELL, 0.002, stop_price=45000.0)
        kwargs = intent.to_place_stop_loss_kwargs()

        for adapter in (BinanceAdapter(ccxt.binance(), dry_run=True),
                        KrakenAdapter(ccxt.kraken(), dry_run=True)):
            raw = adapter.place_stop_loss(**kwargs)
            result = RestingOrderResult.from_adapter_result(raw)
            self.assertEqual(result.symbol, intent.symbol)
            self.assertEqual(result.stop_price, intent.stop_price)
            self.assertEqual(result.side, intent.side)
