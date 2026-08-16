# claude code changed: new file — Kraken Multi-Venue Execution, Step 10.
# Real ccxt instances, dry-run, no mocking — proves ExecutionCoordinator's
# routing is real (checked via each engine's own position_tracker/DB
# state, not a mock), matching this project's convention.

import ccxt
from django.test import TestCase

from bot.engines.execution_coordinator import ExecutionCoordinator
from bot.engines.execution_engine import ExecutionEngine
from bot.engines.kraken_adapter import KrakenAdapter


class ExecutionCoordinatorRoutingTest(TestCase):

    def _make_coordinator(self):
        binance_engine = ExecutionEngine(ccxt.binance(), dry_run=True)
        kraken_engine = ExecutionEngine(
            ccxt.kraken(), dry_run=True, adapter=KrakenAdapter(ccxt.kraken(), dry_run=True)
        )
        binance_engine.position_tracker.is_reconciled = True
        kraken_engine.position_tracker.is_reconciled = True
        coordinator = ExecutionCoordinator(
            {"binance": binance_engine, "kraken": kraken_engine},
            default_venue_id="binance",
        )
        return coordinator, binance_engine, kraken_engine

    def test_signal_without_venue_id_routes_to_default(self):
        coordinator, binance_engine, kraken_engine = self._make_coordinator()
        signal = {
            "signal": "BUY", "symbol": "BTC/USDT", "entry": 50000.0,
            "sl": 49000.0, "tp": 53000.0, "rsi": 55.0,
            "reason": "test", "strategy": "MovingAverageStrategy",
        }

        coordinator.execute_signal(signal)

        self.assertTrue(binance_engine.position_tracker.has_position("BTC/USDT"))
        self.assertFalse(kraken_engine.position_tracker.has_position("BTC/USDT"))

    def test_signal_with_venue_id_routes_to_named_venue(self):
        coordinator, binance_engine, kraken_engine = self._make_coordinator()
        signal = {
            "signal": "BUY", "symbol": "ETH/USDT", "entry": 3000.0,
            "sl": 2940.0, "tp": 3120.0, "rsi": 55.0,
            "reason": "test", "strategy": "MovingAverageStrategy",
            "venue_id": "kraken",
        }

        coordinator.execute_signal(signal)

        self.assertTrue(kraken_engine.position_tracker.has_position("ETH/USDT"))
        self.assertFalse(binance_engine.position_tracker.has_position("ETH/USDT"))

    def test_unknown_venue_id_touches_no_engine(self):
        coordinator, binance_engine, kraken_engine = self._make_coordinator()
        signal = {
            "signal": "BUY", "symbol": "XRP/USDT", "entry": 0.5,
            "sl": 0.48, "tp": 0.54, "rsi": 55.0,
            "reason": "test", "strategy": "MovingAverageStrategy",
            "venue_id": "coinbase",
        }

        coordinator.execute_signal(signal)   # must not raise

        self.assertFalse(binance_engine.position_tracker.has_position("XRP/USDT"))
        self.assertFalse(kraken_engine.position_tracker.has_position("XRP/USDT"))


class ExecutionCoordinatorManagePositionsTest(TestCase):

    def test_manage_positions_calls_through_to_every_engine(self):
        binance_engine = ExecutionEngine(ccxt.binance(), dry_run=True)
        kraken_engine = ExecutionEngine(
            ccxt.kraken(), dry_run=True, adapter=KrakenAdapter(ccxt.kraken(), dry_run=True)
        )

        calls = []
        binance_engine.manage_positions = lambda: calls.append("binance")
        kraken_engine.manage_positions = lambda: calls.append("kraken")

        coordinator = ExecutionCoordinator(
            {"binance": binance_engine, "kraken": kraken_engine},
            default_venue_id="binance",
        )
        coordinator.manage_positions()

        self.assertEqual(sorted(calls), ["binance", "kraken"])


class ExecutionCoordinatorConstructionTest(TestCase):

    def test_invalid_default_venue_id_raises(self):
        binance_engine = ExecutionEngine(ccxt.binance(), dry_run=True)
        with self.assertRaises(ValueError):
            ExecutionCoordinator({"binance": binance_engine}, default_venue_id="kraken")
