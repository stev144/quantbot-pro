# ============================================================
# bot/tests/test_drawdown_guard.py
# claude code changed: new — tests for the portfolio drawdown kill
# switch (bot/risk/drawdown_guard.py) and its wiring into
# ExecutionEngine.execute_signal(). No network/exchange access needed:
# DrawdownGuard itself is pure Python, and the ExecutionEngine test
# below only needs to observe that execute_signal() returns before
# reaching order placement once the guard trips — it never has to
# reach OrderManager, so no ccxt exchange object is required.
# ============================================================

from django.test import TestCase

from bot.risk.drawdown_guard import DrawdownGuard
from bot.engines.execution_engine import ExecutionEngine


class DrawdownGuardTest(TestCase):
    def test_not_tripped_below_threshold(self):
        guard = DrawdownGuard(max_drawdown_pct=0.15)
        guard.update(1000.0)          # establishes peak
        tripped = guard.update(900.0)  # 10% drawdown — below 15% limit
        self.assertFalse(tripped)
        self.assertFalse(guard.tripped)

    def test_trips_at_threshold(self):
        guard = DrawdownGuard(max_drawdown_pct=0.15)
        guard.update(1000.0)
        tripped = guard.update(850.0)  # exactly 15% drawdown
        self.assertTrue(tripped)
        self.assertTrue(guard.tripped)

    def test_peak_only_ratchets_up(self):
        guard = DrawdownGuard(max_drawdown_pct=0.15)
        guard.update(1000.0)
        guard.update(1200.0)  # new peak
        guard.update(1100.0)  # a real balance drop after the new peak
        self.assertEqual(guard.peak_balance, 1200.0)
        # drawdown is measured from the new peak (1200), not the old one
        self.assertAlmostEqual(guard.current_drawdown_pct(1100.0), (1200 - 1100) / 1200)

    def test_recovers_and_clears_after_tripping(self):
        guard = DrawdownGuard(max_drawdown_pct=0.15)
        guard.update(1000.0)
        self.assertTrue(guard.update(800.0))   # trips (20% drawdown)
        self.assertFalse(guard.update(950.0))  # recovers to 5% — clears

    def test_bad_balance_reading_does_not_corrupt_state(self):
        guard = DrawdownGuard(max_drawdown_pct=0.15)
        guard.update(1000.0)
        guard.update(0.0)   # a failed balance fetch — must be ignored
        self.assertEqual(guard.peak_balance, 1000.0)
        self.assertFalse(guard.tripped)

    def test_zero_peak_reports_zero_drawdown(self):
        guard = DrawdownGuard(max_drawdown_pct=0.15)
        self.assertEqual(guard.current_drawdown_pct(500.0), 0.0)


class ExecutionEngineDrawdownGuardWiringTest(TestCase):
    def test_execute_signal_blocked_when_guard_already_tripped(self):
        # No real exchange needed — the guard trips and returns before
        # execute_signal() ever reaches self.order_manager.place_order().
        engine = ExecutionEngine(exchange=None)
        engine.drawdown_guard.update(1000.0)   # establish peak
        engine.drawdown_guard.update(800.0)    # 20% drawdown — trips

        # Force get_balance() to return a value without hitting a real
        # exchange, mirroring how the guard is fed in execute_signal().
        engine.market_data.get_balance = lambda: 800.0

        signal = {
            "symbol": "BTC/USDT",
            "signal": "BUY",
            "entry": 100.0,
            "sl": 95.0,
            "tp": 110.0,
            "rsi": 50.0,
            "reason": "test",
            "strategy": "MovingAverageStrategy",
        }

        engine.position_tracker.is_reconciled = True
        result = engine.execute_signal(signal)

        self.assertIsNone(result)
        self.assertFalse(engine.position_tracker.has_position("BTC/USDT"))
