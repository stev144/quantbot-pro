# claude code changed: new file — Kraken Multi-Venue Execution, Step 10.
# Proves ExecutionEngine's adapter rewiring (STEP 5/7B entry+stop-loss,
# _close_position()'s exit) actually works end-to-end, not just imports
# cleanly — real ccxt instances, dry-run, no mocking, real DB writes
# (django.test.TestCase, matching test_trade_persistence.py's precedent).

import ccxt
from django.test import TestCase

from bot.engines.binance_adapter import BinanceAdapter
from bot.engines.execution_engine import ExecutionEngine
from bot.engines.kraken_adapter import KrakenAdapter
from bot.journal.models import TradeRecord


class ExecutionEngineDefaultAdapterEntryExitTest(TestCase):
    """
    Proves the zero-line-diff default path (no `adapter` arg — exactly
    what bot_runner.py's existing ExecutionEngine(exchange, dry_run=DRY_RUN)
    call does) works end-to-end through the new
    self.adapter.place_order()/place_stop_loss() rewiring.
    """

    def _signal(self):
        return {
            "signal": "BUY", "symbol": "BTC/USDT", "entry": 50000.0,
            "sl": 49000.0, "tp": 53000.0, "rsi": 55.0,
            "reason": "test_entry", "strategy": "MovingAverageStrategy",
        }

    def test_entry_creates_trade_record_and_open_position(self):
        engine = ExecutionEngine(ccxt.binance(), dry_run=True)
        engine.position_tracker.is_reconciled = True

        engine.execute_signal(self._signal())

        self.assertTrue(engine.position_tracker.has_position("BTC/USDT"))
        record = TradeRecord.objects.get(symbol="BTC/USDT", status="OPEN")
        self.assertEqual(record.side, "BUY")
        self.assertTrue(record.order_id.startswith("DRYRUN-"))
        self.assertGreater(record.entry_price, 0)
        self.assertEqual(
            record.quantity,
            engine.position_tracker.open_positions["BTC/USDT"]["quantity"],
        )
        # claude code changed: new — Step 15.
        self.assertEqual(record.venue, "binance")
        self.assertEqual(record.venue, engine.venue_id)

    def test_exit_updates_trade_record(self):
        engine = ExecutionEngine(ccxt.binance(), dry_run=True)
        engine.position_tracker.is_reconciled = True
        engine.execute_signal(self._signal())

        position = engine.position_tracker.open_positions["BTC/USDT"]
        engine._close_position("BTC/USDT", position, "take_profit")

        self.assertFalse(engine.position_tracker.has_position("BTC/USDT"))
        record = TradeRecord.objects.get(symbol="BTC/USDT")
        self.assertIn(record.status, ("WIN", "LOSS"))
        self.assertIsNotNone(record.exit_price)
        self.assertEqual(record.exit_reason, "take_profit")


class ExecutionEngineKrakenAdapterCostTest(TestCase):
    """Ties Step 8's venue-cost-model fix into the live ExecutionEngine
    path for the first time — proves a real, persisted TradeRecord reflects
    Kraken's fee rate, not Binance's, when constructed with a KrakenAdapter."""

    def test_kraken_entry_fee_reflects_krakens_rate(self):
        adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)
        engine = ExecutionEngine(ccxt.kraken(), dry_run=True, adapter=adapter)
        engine.position_tracker.is_reconciled = True
        self.assertEqual(engine.venue_id, "kraken")

        signal = {
            "signal": "BUY", "symbol": "ETH/USDT", "entry": 3000.0,
            "sl": 2940.0, "tp": 3120.0, "rsi": 55.0,
            "reason": "test_entry", "strategy": "MovingAverageStrategy",
        }
        engine.execute_signal(signal)

        record = TradeRecord.objects.get(symbol="ETH/USDT", status="OPEN")
        effective_fee_rate = record.fee_entry / (record.entry_price * record.quantity)
        self.assertAlmostEqual(effective_fee_rate, 0.0026, places=6)
        # claude code changed: new — Step 15.
        self.assertEqual(record.venue, "kraken")


class ExecutionEngineCrossVenueSameSymbolTest(TestCase):
    """
    THE real regression test for Step 15's constraint fix: the same
    symbol open concurrently on two different venues, via two separate
    ExecutionEngines — exactly what ExecutionCoordinator (Step 10) makes
    possible. Before Step 15 this would fail at the DB layer for the
    second engine's entry (symbol-only unique-OPEN constraint).
    """

    def test_same_symbol_open_on_binance_and_kraken_engines_simultaneously(self):
        binance_engine = ExecutionEngine(ccxt.binance(), dry_run=True)
        kraken_engine = ExecutionEngine(
            ccxt.kraken(), dry_run=True, adapter=KrakenAdapter(ccxt.kraken(), dry_run=True)
        )
        binance_engine.position_tracker.is_reconciled = True
        kraken_engine.position_tracker.is_reconciled = True

        signal = {
            "signal": "BUY", "symbol": "BTC/USDT", "entry": 50000.0,
            "sl": 49000.0, "tp": 53000.0, "rsi": 55.0,
            "reason": "test_entry", "strategy": "MovingAverageStrategy",
        }

        binance_engine.execute_signal(signal)
        kraken_engine.execute_signal(signal)   # would previously raise/fail to log

        self.assertTrue(binance_engine.position_tracker.has_position("BTC/USDT"))
        self.assertTrue(kraken_engine.position_tracker.has_position("BTC/USDT"))

        open_records = TradeRecord.objects.filter(symbol="BTC/USDT", status="OPEN")
        self.assertEqual(open_records.count(), 2)
        self.assertEqual(
            set(open_records.values_list("venue", flat=True)), {"binance", "kraken"},
        )
