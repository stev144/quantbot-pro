# ============================================================
# bot/tests/test_position_tracker.py
# claude code changed: new file — Forensic Audit & Deep Health Check
# Hardening mission, Milestone A item 2. No test coverage existed for
# PositionTracker/reconcile_with_exchange() before this, despite it
# being the one hard safety gate execute_signal() relies on before any
# live trade is allowed. A forensic audit found reconcile_with_exchange()
# used to set self.is_reconciled = True unconditionally, regardless of
# what it actually found — a real detected exchange/local mismatch was
# correctly logged but never blocked trading. These tests are the
# regression guard for that fix.
# ============================================================

import ccxt
from django.test import SimpleTestCase

from bot.engines.position_tracker import PositionTracker


class _FakeExchange:
    """Deterministic stand-in for a ccxt exchange — this test needs
    precise, controlled per-symbol responses (open orders present vs.
    absent, a raised NetworkError, an untracked closed order) that a
    real exchange call can't reliably produce on demand. Matches this
    project's own documented exception here: reconciliation LOGIC
    correctness is exactly what needs a controlled fixture; real network
    behavior is exercised elsewhere (dry_run_test.py, health_check)."""

    def __init__(self, open_orders_by_symbol=None, closed_orders_by_symbol=None, raise_for_symbol=None, raise_exc=None):
        self._open = open_orders_by_symbol or {}
        self._closed = closed_orders_by_symbol or {}
        self._raise_for_symbol = raise_for_symbol
        self._raise_exc = raise_exc or ccxt.NetworkError("simulated network failure")

    def fetch_open_orders(self, symbol):
        if symbol == self._raise_for_symbol:
            raise self._raise_exc
        return self._open.get(symbol, [])

    def fetch_orders(self, symbol, limit=5):
        return self._closed.get(symbol, [])


class ReconcileWithExchangeTest(SimpleTestCase):

    def test_clean_reconciliation_sets_is_reconciled_true(self):
        tracker = PositionTracker()
        exchange = _FakeExchange()
        tracker.reconcile_with_exchange(exchange, ["NEAR/USDT"])
        self.assertTrue(tracker.is_reconciled)

    def test_real_open_order_blocks_reconciliation(self):
        """The exact scenario this fix targets: a real open order on the
        exchange, with a guaranteed-empty in-memory tracker — this must
        now block trading instead of just being logged."""
        tracker = PositionTracker()
        exchange = _FakeExchange(open_orders_by_symbol={
            "NEAR/USDT": [{"id": "123", "side": "buy", "amount": 10, "price": 5.0}],
        })
        tracker.reconcile_with_exchange(exchange, ["NEAR/USDT"])
        self.assertFalse(tracker.is_reconciled)

    def test_network_error_during_reconciliation_blocks_trading(self):
        tracker = PositionTracker()
        exchange = _FakeExchange(raise_for_symbol="NEAR/USDT", raise_exc=ccxt.NetworkError("timeout"))
        tracker.reconcile_with_exchange(exchange, ["NEAR/USDT"])
        self.assertFalse(tracker.is_reconciled, "a symbol that could not be verified must not count as reconciled")

    def test_generic_exception_during_reconciliation_blocks_trading(self):
        tracker = PositionTracker()
        exchange = _FakeExchange(raise_for_symbol="NEAR/USDT", raise_exc=ValueError("unexpected"))
        tracker.reconcile_with_exchange(exchange, ["NEAR/USDT"])
        self.assertFalse(tracker.is_reconciled)

    def test_untracked_closed_order_alone_does_not_block(self):
        """Deliberately NOT gating on this signal — see
        reconcile_with_exchange()'s own docstring. self.open_positions is
        always empty at call time, so every historical closed trade
        would otherwise be flagged as 'untracked' on every startup,
        permanently blocking any account with real trading history."""
        tracker = PositionTracker()
        exchange = _FakeExchange(closed_orders_by_symbol={
            "NEAR/USDT": [{"id": "999", "status": "closed", "side": "sell", "filled": 10, "average": 5.2}],
        })
        tracker.reconcile_with_exchange(exchange, ["NEAR/USDT"])
        self.assertTrue(tracker.is_reconciled)

    def test_one_bad_symbol_blocks_reconciliation_globally(self):
        """is_reconciled is a single global flag (matches this project's
        current single-symbol-per-venue design) — a mismatch on ANY
        tracked symbol must block trading, not just that symbol."""
        tracker = PositionTracker()
        exchange = _FakeExchange(open_orders_by_symbol={
            "ETH/USDT": [{"id": "1", "side": "buy", "amount": 1, "price": 2000}],
        })
        tracker.reconcile_with_exchange(exchange, ["NEAR/USDT", "ETH/USDT"])
        self.assertFalse(tracker.is_reconciled)

    def test_reconciliation_never_mutates_open_positions_directly(self):
        """reconcile_with_exchange() detects and logs mismatches but —
        per its own documented, unchanged scope — never writes into
        self.open_positions itself; only logs + the is_reconciled gate
        this fix corrects."""
        tracker = PositionTracker()
        exchange = _FakeExchange(open_orders_by_symbol={
            "NEAR/USDT": [{"id": "123", "side": "buy", "amount": 10, "price": 5.0}],
        })
        tracker.reconcile_with_exchange(exchange, ["NEAR/USDT"])
        self.assertEqual(tracker.open_positions, {})
