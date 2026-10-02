# claude code changed: new file — Post-audit Phase 1 (pairs live safety):
# A1 C-04 naked-leg protection, A2 C-05 pre-entry drawdown guard, A3 exit
# minimum-order protection, A4 dry-run fill realism, A5 migration readiness.
#
# NO exchange contact: an in-memory ledger stands in for the isolated-margin
# accounts (deterministic failure injection), and the dry-run tests use a stub
# ccxt object. Expected values are plain arithmetic, never the code under test.

import uuid
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase

from bot.core import pairs_bot_runner as runner
from bot.engines.order_manager import OrderUnconfirmedException
from bot.journal.models import MODE_LIVE, ExecutionIncident, TradeRecord, execution_mode_for   # claude code changed: Post-audit Phase 1 journal-mode separation
from bot.pairs.config import PairConfig
from bot.pairs.execution import (
    MANUAL_REVIEW_REQUIRED, NO_EXPOSURE, UNWOUND_CONFIRMED, PairsExecutionEngine,
)
from bot.pairs.margin_adapter import (
    AmountRules, BinanceIsolatedMarginAdapter, IsolatedAccountState, MarginFillResult,
)
from bot.config.execution_costs import FEE_RATE, SLIPPAGE_RATE
from bot.pairs.trade_logger import PairsTradeLogger
from bot.risk.drawdown_guard import DrawdownGuard

CAP = 500.0


def _cfg(name="A_USDT/B_USDT", a="A/USDT", b="B/USDT"):
    return PairConfig(name, name.split("/")[0], name.split("/")[1], a, b, 1.0, 0.0, 0.6, CAP, True, "", True)


class Ledger:
    """In-memory isolated-margin accounts. `fail[(method, symbol)]` = (exception, executes_first):
    executes_first=True models an order the exchange EXECUTED although the call raised."""

    def __init__(self, prices, step=0.001, min_qty=0.001, min_notional=5.0):
        self.prices, self.step, self.min_qty, self.mn = dict(prices), step, min_qty, min_notional
        self.acc = {s: {"base_free": 0.0, "borrowed": 0.0, "interest": 0.0, "quote_free": 0.0} for s in prices}
        self.fail, self.calls = {}, []
        self.half_spread = {}   # claude code changed: Post-audit Phase 1 A2 — per-symbol half-spread for liquidation marks (0 = bid = ask = price)

    def _maybe_fail(self, method, symbol, effect):
        f = self.fail.get((method, symbol))
        if f and f[1]:
            effect()
        if f:
            raise f[0]
        effect()

    def _fill(self, symbol, side, qty):
        return MarginFillResult(order_id=f"{side}-{symbol}-{qty}", symbol=symbol, side=side, filled_qty=qty,
                                fill_price=self.prices[symbol], fee_usdt=0.0, fee_asset="USDT", fees=((0.0, "USDT"),))

    def get_ticker_price(self, s):
        self.calls.append(("ticker", s))
        if self.fail.get(("ticker", s)):
            raise self.fail[("ticker", s)][0]
        return self.prices[s]

    def get_bid_ask(self, s):   # claude code changed: Post-audit Phase 1 A2 (liquidation mark) — bid/ask = price ∓ half_spread[s]
        self.calls.append(("bid_ask", s))
        if self.fail.get(("ticker", s)):
            raise self.fail[("ticker", s)][0]
        h = self.half_spread.get(s, 0.0)
        return self.prices[s] - h, self.prices[s] + h

    def amount_rules(self, s):
        return AmountRules(step=self.step, min_qty=self.min_qty)

    def min_notional(self, s):
        return self.mn

    def fee_buffer_rate(self, s):
        return 0.0

    def transfer_in(self, s, q):
        self.calls.append(("transfer_in", s)); self.acc[s]["quote_free"] += q

    def transfer_out(self, s, q):
        self.calls.append(("transfer_out", s)); self.acc[s]["quote_free"] -= q

    def open_long(self, s, q):
        self.calls.append(("open_long", s))
        self._maybe_fail("open_long", s, lambda: self.acc[s].__setitem__("base_free", self.acc[s]["base_free"] + q))
        return self._fill(s, "buy", q)

    def open_short(self, s, q):
        self.calls.append(("open_short", s))
        self._maybe_fail("open_short", s, lambda: self.acc[s].__setitem__("borrowed", self.acc[s]["borrowed"] + q))
        return {}, self._fill(s, "sell", q)

    def close_long(self, s, q):
        self.calls.append(("close_long", s))
        self._maybe_fail("close_long", s, lambda: self.acc[s].__setitem__("base_free", self.acc[s]["base_free"] - q))
        return self._fill(s, "sell", q)

    def market_buy_to_cover(self, s, q):
        self.calls.append(("cover", s))
        self._maybe_fail("cover", s, lambda: self.acc[s].__setitem__("base_free", self.acc[s]["base_free"] + q))
        return self._fill(s, "buy", q)

    def repay(self, s, q):
        a = self.acc[s]; pay = min(q, a["borrowed"]); a["borrowed"] -= pay; a["base_free"] -= pay

    def get_isolated_account(self, s):
        if self.fail.get(("account", s)):
            raise self.fail[("account", s)][0]
        a = self.acc[s]
        return IsolatedAccountState(symbol=s, base_asset=s.split("/")[0], quote_asset="USDT", base_free=a["base_free"],
                                    base_borrowed=a["borrowed"], base_interest=a["interest"], quote_free=a["quote_free"],
                                    quote_borrowed=0.0, quote_interest=0.0)

    def flat(self, s):
        a = self.acc[s]
        return a["borrowed"] == 0 and a["base_free"] < self.min_qty


def _engine(ledger, dry_run=False):
    e = PairsExecutionEngine.__new__(PairsExecutionEngine)
    e.adapter, e.drawdown_guard = ledger, DrawdownGuard()
    e.trade_logger = PairsTradeLogger(execution_mode=execution_mode_for(dry_run))   # claude code changed: Post-audit Phase 1 journal-mode separation
    e.dry_run, e._open_positions, e._mismatched_pairs = dry_run, {}, set()
    return e


def _decision(direction="LONG_SPREAD"):
    return SimpleNamespace(action="ENTER", trade=SimpleNamespace(direction=direction, leg_a_usdt=12.5, leg_b_usdt=12.5,
                                                                 executability=""))


# ── A1 — C-04 naked leg ──────────────────────────────────────────────────────
class NakedLegTests(TestCase):
    def setUp(self):
        self.L = Ledger({"A/USDT": 1.0, "B/USDT": 2.0})
        self.e = _engine(self.L)
        self.cfg = _cfg()

    def _incident(self):
        return ExecutionIncident.objects.get()

    def test_01_first_leg_fails_before_fill(self):
        self.L.fail[("open_long", "A/USDT")] = (RuntimeError("insufficient balance"), False)
        self.assertIsNone(self.e.open_pair_trade(self.cfg, _decision()))
        inc = self._incident()
        self.assertEqual((inc.state, inc.requires_manual_review, inc.resolved), (NO_EXPOSURE, False, True))
        self.assertEqual(TradeRecord.objects.count(), 0)
        self.assertTrue(self.e.is_pair_tradeable(self.cfg.pair_name))         # no exposure → not disabled
        self.assertFalse(self.e.has_open_position(self.cfg.pair_name))
        self.assertEqual(self.L.acc["A/USDT"]["quote_free"], 0.0)            # collateral refunded

    def test_02_both_legs_fill(self):
        tid = self.e.open_pair_trade(self.cfg, _decision())
        self.assertIsNotNone(tid)
        self.assertEqual(TradeRecord.objects.filter(pair_trade_id=tid, status="OPEN").count(), 2)
        self.assertEqual(ExecutionIncident.objects.count(), 0)

    def _leg_b_failure(self, exc, executes_first=False):
        self.L.fail[("open_short", "B/USDT")] = (exc, executes_first)
        self.assertIsNone(self.e.open_pair_trade(self.cfg, _decision()))
        return self._incident()

    def test_03_and_06_second_leg_rejects_unwind_confirmed(self):
        inc = self._leg_b_failure(RuntimeError("order rejected: margin insufficient"))
        self.assertEqual((inc.state, inc.requires_manual_review), (UNWOUND_CONFIRMED, False))
        self.assertTrue(self.L.flat("A/USDT") and self.L.flat("B/USDT"))       # no naked exposure
        leg_a = TradeRecord.objects.get(leg="A")
        self.assertEqual(leg_a.exit_reason, "PAIR_OPEN_UNWOUND")             # real entry + real unwind exit
        self.assertNotEqual(leg_a.status, "OPEN")
        self.assertFalse(TradeRecord.objects.filter(leg="B").exists())       # B never filled
        self.assertFalse(self.e.has_open_position(self.cfg.pair_name))       # never a completed pair
        self.assertFalse(self.e.is_pair_tradeable(self.cfg.pair_name))       # no retry in this run
        self.assertIn("A:BUY", inc.legs_filled)

    def test_04_and_08_second_leg_times_out_ambiguous(self):
        inc = self._leg_b_failure(OrderUnconfirmedException("order status unknown"), executes_first=True)
        self.assertEqual((inc.state, inc.requires_manual_review, inc.resolved), (MANUAL_REVIEW_REQUIRED, True, False))
        self.assertTrue(self.L.flat("A/USDT") and self.L.flat("B/USDT"))       # unwind closed what DID exist (incl. B)
        self.assertFalse(self.e.is_pair_tradeable(self.cfg.pair_name))

    def test_05_second_leg_unknown_exception(self):
        inc = self._leg_b_failure(KeyError("unexpected payload"))
        self.assertEqual(inc.state, UNWOUND_CONFIRMED)
        self.assertIn("KeyError", inc.error)
        self.assertTrue(self.L.flat("A/USDT"))

    def test_07_unwind_fails_keeps_exposure_visible_and_collateral_in_place(self):
        self.L.fail[("close_long", "A/USDT")] = (RuntimeError("exchange down"), False)
        inc = self._leg_b_failure(RuntimeError("rejected"))
        self.assertEqual((inc.state, inc.requires_manual_review), (MANUAL_REVIEW_REQUIRED, True))
        self.assertEqual(TradeRecord.objects.get(leg="A").status, "OPEN")    # the real exposure stays on record
        self.assertGreater(self.L.acc["A/USDT"]["quote_free"], 0.0)          # collateral NOT moved out
        self.assertIn("A:A/USDT", inc.legs_open_after)
        self.assertFalse(self.e.is_pair_tradeable(self.cfg.pair_name))

    def test_09_10_restart_after_partial_leg_incident(self):
        self.L.fail[("close_long", "A/USDT")] = (RuntimeError("exchange down"), False)
        self._leg_b_failure(RuntimeError("rejected"))
        fresh = _engine(Ledger({"A/USDT": 1.0, "B/USDT": 2.0}))              # "restart": nothing in memory
        self.assertTrue(fresh.is_pair_tradeable(self.cfg.pair_name))
        runner.reconcile_pairs([self.cfg], fresh)
        self.assertFalse(fresh.is_pair_tradeable(self.cfg.pair_name))
        self.assertTrue(ExecutionIncident.objects.filter(pair_name=self.cfg.pair_name, resolved=False).exists())

    def test_11_12_no_phantom_pair_and_no_silent_naked_exposure_across_all_failures(self):
        for name, (method, symbol, exc, ex_first) in {
                "B reject": ("open_short", "B/USDT", RuntimeError("x"), False),
                "B unknown": ("open_short", "B/USDT", ValueError("x"), False),
                "B ambiguous": ("open_short", "B/USDT", OrderUnconfirmedException("x"), True)}.items():
            with self.subTest(name):
                L = Ledger({"A/USDT": 1.0, "B/USDT": 2.0})
                L.fail[(method, symbol)] = (exc, ex_first)
                e = _engine(L)
                self.assertIsNone(e.open_pair_trade(self.cfg, _decision()))
                self.assertFalse(e.has_open_position(self.cfg.pair_name))
                exposure = not (L.flat("A/USDT") and L.flat("B/USDT"))
                inc = ExecutionIncident.objects.order_by("-id").first()
                self.assertTrue((not exposure) or inc.state == MANUAL_REVIEW_REQUIRED)


# ── A3 — exit minimum order / size ───────────────────────────────────────────
class ExitClosabilityTests(TestCase):
    def setUp(self):
        self.L = Ledger({"A/USDT": 1.0, "B/USDT": 2.0})
        self.e = _engine(self.L)
        self.cfg = _cfg()

    def _open(self, direction="LONG_SPREAD"):
        tid = self.e.open_pair_trade(self.cfg, _decision(direction))
        self.assertIsNotNone(tid)
        return tid

    def test_valid_close_long_and_short_legs(self):
        tid = self._open()
        self.assertTrue(self.e.close_pair_trade(self.cfg, "TIMESTOP"))
        a, b = TradeRecord.objects.get(leg="A"), TradeRecord.objects.get(leg="B")
        self.assertNotEqual(a.status, "OPEN"); self.assertNotEqual(b.status, "OPEN")
        self.assertEqual((a.borrow_cost_status, b.borrow_cost_status), ("NOT_APPLICABLE", "KNOWN"))
        self.assertTrue(self.L.flat("A/USDT") and self.L.flat("B/USDT"))

    def test_exactly_minimum_notional_is_closable(self):
        self._open()
        self.L.prices["A/USDT"] = 5.0 / 12.5                                # 12.5 units × 0.4 = exactly $5
        self.assertTrue(self.e.close_pair_trade(self.cfg, "TIMESTOP"))

    def test_below_min_notional_blocks_that_leg_only_and_fabricates_nothing(self):
        self._open()
        self.L.prices["A/USDT"] = 0.3                                       # 12.5 × 0.3 = $3.75 < $5
        self.assertFalse(self.e.close_pair_trade(self.cfg, "TIMESTOP"))
        self.assertNotIn(("close_long", "A/USDT"), self.L.calls)             # no order for the blocked leg
        self.assertEqual(TradeRecord.objects.get(leg="A").status, "OPEN")    # not marked closed
        self.assertNotEqual(TradeRecord.objects.get(leg="B").status, "OPEN") # the closable leg closed and journaled
        pos = self.e._open_positions[self.cfg.pair_name]
        self.assertEqual((pos.leg_a_open, pos.leg_b_open), (True, False))     # actual remaining position preserved
        inc = ExecutionIncident.objects.get()
        self.assertEqual((inc.kind, inc.state), ("CLOSE_BLOCKED_EXCHANGE_RULES", MANUAL_REVIEW_REQUIRED))
        self.assertFalse(self.e.is_pair_tradeable(self.cfg.pair_name))
        # partial close completes later (multiple fills): price recovers, only the remaining leg is closed
        self.L.prices["A/USDT"] = 1.0
        self.assertTrue(self.e.close_pair_trade(self.cfg, "TIMESTOP"))
        self.assertEqual([c for c in self.L.calls if c[0] in ("close_long", "cover")],
                         [("cover", "B/USDT"), ("close_long", "A/USDT")])

    def test_short_leg_below_min_notional_blocked(self):
        self._open("SHORT_SPREAD")                                           # A short, B long
        self.L.prices["A/USDT"] = 0.3
        self.assertFalse(self.e.close_pair_trade(self.cfg, "TIMESTOP"))
        self.assertNotIn(("cover", "A/USDT"), self.L.calls)
        self.assertGreater(self.L.acc["A/USDT"]["borrowed"], 0)              # debt still on record, not hidden

    def test_below_min_qty_and_tiny_remaining_position(self):
        self._open()
        self.L.min_qty = 50.0                                                # 12.5 units < minQty
        self.assertFalse(self.e.close_pair_trade(self.cfg, "TIMESTOP"))
        self.assertEqual(TradeRecord.objects.get(leg="A").status, "OPEN")

    def test_step_rounding_on_close(self):
        self._open()
        self.L.acc["A/USDT"]["base_free"] = 12.4999                          # base-asset fee left less than filled
        self.assertTrue(self.e.close_pair_trade(self.cfg, "TIMESTOP"))
        self.assertAlmostEqual(TradeRecord.objects.get(leg="A").exit_price, 1.0)
        self.assertAlmostEqual(self.L.acc["A/USDT"]["base_free"], 0.0009, places=9)   # floored to 12.499, dust left


# ── A2 — C-05 drawdown guard ─────────────────────────────────────────────────
def _closed_leg(net, pair="A_USDT/B_USDT"):
    return TradeRecord.objects.create(execution_mode=MODE_LIVE, symbol="A/USDT", side="BUY", strategy=f"PairsTrading_{pair}", entry_price=1.0,
                                      sl=1.0, tp=1.0, quantity=1.0, order_id=str(uuid.uuid4()), net_pnl=net,
                                      status="WIN" if (net or 0) > 0 else "LOSS", pair_trade_id=uuid.uuid4(), leg="A",
                                      venue=f"v{uuid.uuid4().hex[:8]}")


class DrawdownGuardTests(TestCase):
    """Peak C + realised path; threshold 15 %; drawdown >= 15 % blocks."""

    def setUp(self):
        self.L = Ledger({"A/USDT": 1.0, "B/USDT": 2.0})
        self.e = _engine(self.L)

    def test_no_history_allows(self):
        allowed, _ = self.e.drawdown_entry_check(CAP)
        self.assertTrue(allowed)

    def _history(self, *nets):
        for n in nets:
            _closed_leg(n)

    def test_threshold_boundary_and_above(self):
        # peak 500 + 100 = 600; 15 % of 600 = 90 → equity 510 is EXACTLY at the threshold
        for nets, expected in (((100.0, -89.0), True), ((100.0, -90.0), False), ((100.0, -95.0), False)):
            TradeRecord.objects.all().delete()
            self._history(*nets)
            with self.subTest(nets=nets):
                self.assertEqual(_engine(self.L).drawdown_entry_check(CAP)[0], expected)

    def test_unrealised_open_legs_count(self):
        TradeRecord.objects.create(execution_mode=MODE_LIVE, symbol="A/USDT", side="BUY", strategy="PairsTrading_A_USDT/B_USDT", entry_price=1.0,
                                   sl=1.0, tp=1.0, quantity=100.0, order_id="o", status="OPEN", leg="A")
        self.L.prices["A/USDT"] = 0.2                                        # unrealised −80 → equity 420 = −16 %
        allowed, why = self.e.drawdown_entry_check(CAP)
        self.assertFalse(allowed)
        self.assertIn("TRIPPED", why)

    def test_restart_rebuilds_peak_from_journal(self):
        self._history(100.0, -95.0)
        self.assertFalse(_engine(self.L).drawdown_entry_check(CAP)[0])
        self.assertFalse(_engine(self.L).drawdown_entry_check(CAP)[0])        # a fresh guard is not re-seeded lower

    def test_unknown_equity_fails_closed(self):
        _closed_leg(None)
        self.assertFalse(self.e.drawdown_entry_check(CAP)[0])
        TradeRecord.objects.all().delete()
        TradeRecord.objects.create(execution_mode=MODE_LIVE, symbol="A/USDT", side="BUY", strategy="PairsTrading_X", entry_price=1.0, sl=1.0,
                                   tp=1.0, quantity=1.0, order_id="o", status="OPEN", leg="A")
        self.L.fail[("ticker", "A/USDT")] = (RuntimeError("no price"), False)
        self.assertFalse(self.e.drawdown_entry_check(CAP)[0])

    def test_blocked_entry_never_reaches_execution(self):
        self._history(100.0, -95.0)
        self.assertIsNone(self.e.open_pair_trade(_cfg(), _decision()))
        self.assertFalse([c for c in self.L.calls if c[0] in ("transfer_in", "open_long", "open_short")])

    def test_existing_positions_still_close_while_tripped(self):
        cfg = _cfg()
        self.assertIsNotNone(self.e.open_pair_trade(cfg, _decision()))
        self._history(100.0, -200.0)                                         # now deep in drawdown
        self.assertFalse(self.e.drawdown_entry_check(CAP)[0])
        self.assertTrue(self.e.close_pair_trade(cfg, "TIMESTOP"))

    def test_multiple_signals_each_rechecked_including_open_legs(self):
        L = Ledger({"A/USDT": 1.0, "B/USDT": 2.0, "C/USDT": 1.0, "D/USDT": 1.0})
        e = _engine(L)
        self.assertIsNotNone(e.open_pair_trade(_cfg(), _decision()))
        L.prices["A/USDT"] = 0.0001                                          # first pair's open legs collapse
        L.prices["B/USDT"] = 14.0                                          # unrealised: A −12.5, B short 6.25 × (14 − 2) = −75 → −87.5 = −17.5 % of 500
        self.assertIsNone(e.open_pair_trade(_cfg("C_USDT/D_USDT", "C/USDT", "D/USDT"), _decision()))


# ── A4 — dry-run fill realism ────────────────────────────────────────────────
class _StubExchange:
    def __init__(self, bid=0.99, ask=1.01, last=1.00):
        self.t = {"bid": bid, "ask": ask, "last": last}

    def fetch_ticker(self, s):
        return dict(self.t)

    def market(self, s):
        return {"base": s.split("/")[0], "quote": "USDT", "precision": {"amount": None}, "limits": {}}


class DryRunFillModelTests(TestCase):
    def test_touch_plus_declared_slippage_and_fee(self):
        ad = BinanceIsolatedMarginAdapter(_StubExchange(), dry_run=True)
        buy, sell = ad._simulate_fill("A/USDT", "buy", 10.0), ad._simulate_fill("A/USDT", "sell", 10.0)
        self.assertAlmostEqual(buy.fill_price, 1.01 * (1 + SLIPPAGE_RATE), places=12)
        self.assertAlmostEqual(sell.fill_price, 0.99 * (1 - SLIPPAGE_RATE), places=12)
        self.assertAlmostEqual(buy.fee_usdt, round(buy.fill_price * 10.0 * FEE_RATE, 6), places=12)
        self.assertEqual((buy.filled_qty, buy.price_source), (10.0, "DRY_RUN_TOUCH+SLIPPAGE"))   # full fill assumed, labelled

    def test_missing_touch_falls_back_to_last_with_label(self):
        ad = BinanceIsolatedMarginAdapter(_StubExchange(bid=None, ask=None), dry_run=True)
        f = ad._simulate_fill("A/USDT", "buy", 1.0)
        self.assertAlmostEqual(f.fill_price, 1.00 * (1 + SLIPPAGE_RATE), places=12)
        self.assertEqual(f.price_source, "DRY_RUN_LAST_FALLBACK+SLIPPAGE")

    def test_dry_run_journal_labels_fill_model_and_unavailable_borrow(self):
        e = PairsExecutionEngine(_StubExchange(), dry_run=True)
        cfg = _cfg()
        self.assertIsNotNone(e.open_pair_trade(cfg, _decision("LONG_SPREAD")))   # A long, B short
        self.assertTrue(e.close_pair_trade(cfg, "TIMESTOP"))
        a, b = TradeRecord.objects.get(leg="A"), TradeRecord.objects.get(leg="B")
        self.assertEqual(b.borrow_cost_status, "UNAVAILABLE")                 # never a silent zero
        self.assertEqual(a.borrow_cost_status, "NOT_APPLICABLE")
        self.assertTrue(a.fill_model.startswith("DRY_RUN_TOUCH+SLIPPAGE|exit:DRY_RUN_TOUCH+SLIPPAGE"))


# ── A5 — migration readiness ─────────────────────────────────────────────────
class MigrationReadinessTests(TestCase):
    def test_schema_is_current_in_tests(self):
        self.assertEqual(runner.pending_migrations(), [])

    def test_runner_refuses_to_start_with_pending_migrations(self):
        with mock.patch.object(runner, "setup_logging"), \
                mock.patch.object(runner, "pending_migrations", return_value=["bot.0013_x"]), \
                mock.patch.object(runner, "load_pair_configs") as load:
            with self.assertLogs(runner.__name__, level="CRITICAL") as logs:
                runner.run_bot()
        load.assert_not_called()
        self.assertIn("manage.py migrate", logs.output[0])


# ── Phase 1 adversarial review — regressions for defects found AFTER the first
#    Phase 1 pass (claude code changed: new section) ───────────────────────────
class Phase1ReviewRegressionTests(TestCase):
    def setUp(self):
        self.L = Ledger({"A/USDT": 1.0, "B/USDT": 2.0})
        self.cfg = _cfg()

    def _leg(self, net, ptid, status=None, leg="A", symbol="A/USDT", qty=1.0, entry=1.0):
        return TradeRecord.objects.create(execution_mode=MODE_LIVE, symbol=symbol, side="BUY", strategy="PairsTrading_A_USDT/B_USDT",
                                          entry_price=entry, sl=1.0, tp=1.0, quantity=qty, order_id=str(uuid.uuid4()),
                                          net_pnl=net, status=status or ("WIN" if (net or 0) > 0 else "LOSS"),
                                          pair_trade_id=ptid, leg=leg, venue=f"v{uuid.uuid4().hex[:8]}")

    def test_equity_at_or_below_zero_fails_closed_on_a_fresh_guard(self):
        # DrawdownGuard.update(<=0) returns its previous state (False on a fresh guard) — must still block
        for loss in (-500.0, -650.0):                                        # equity exactly 0, then −150
            TradeRecord.objects.all().delete()
            _closed_leg(loss)
            with self.subTest(loss=loss):
                allowed, why = _engine(self.L).drawdown_entry_check(CAP)
                self.assertFalse(allowed)
                self.assertIn("<= 0", why)

    def test_peak_counts_only_completed_pair_trades(self):
        # one pair: leg A +100, leg B −100 → pair net 0. The pilot never held 600:
        # peak 500, equity 500 → 0 % drawdown → allowed (leg-level peak 600 would read −16.7 % and block)
        ptid = uuid.uuid4()
        self._leg(100.0, ptid, leg="A")
        self._leg(-100.0, ptid, leg="B", symbol="B/USDT")
        allowed, why = _engine(self.L).drawdown_entry_check(CAP)
        self.assertTrue(allowed, why)

    def test_peak_ignores_half_closed_pair_until_its_open_leg_closes(self):
        # leg A closed +100; leg B still OPEN, bought 100 @ 2.0, now 1.0 → unrealised −100.
        # equity = 500 + 100 − 100 = 500; peak must stay 500 (no completed pair) → allowed
        ptid = uuid.uuid4()
        self._leg(100.0, ptid, leg="A")
        self._leg(None, ptid, status="OPEN", leg="B", symbol="B/USDT", qty=100.0, entry=2.0)
        self.L.prices["B/USDT"] = 1.0
        equity, peak = _engine(self.L).strategy_equity(CAP)
        self.assertAlmostEqual(equity, 499.9)    # claude code changed: liquidation mark — 500 + 100 − 100 − 0.1 est. exit fee (100 × 1.0 × 0.001)
        self.assertAlmostEqual(peak, 500.0)      # no completed pair → peak stays C

    def test_pre_0010_rows_without_pair_id_are_separate_peak_points(self):
        self._leg(100.0, None)
        self._leg(-90.0, None)                                               # peak 600, equity 510 → exactly 15 % → blocks
        self.assertFalse(_engine(self.L).drawdown_entry_check(CAP)[0])

    def test_borrowed_but_unsold_short_is_repaid_not_market_sold(self):
        # leg B short: borrow succeeds, the sell then raises → base_free = borrowed = q
        def borrow_then_fail(s, q):
            self.L.calls.append(("open_short", s))
            self.L.acc[s]["borrowed"] += q
            self.L.acc[s]["base_free"] += q
            raise RuntimeError("sell rejected after borrow")
        self.L.open_short = borrow_then_fail
        e = _engine(self.L)
        self.assertIsNone(e.open_pair_trade(self.cfg, _decision("LONG_SPREAD")))
        self.assertTrue(self.L.flat("A/USDT") and self.L.flat("B/USDT"))
        self.assertNotIn(("close_long", "B/USDT"), self.L.calls)            # no market SELL of borrowed coins
        self.assertNotIn(("cover", "B/USDT"), self.L.calls)                 # and so no cover buy needed
        self.assertEqual(ExecutionIncident.objects.get().state, UNWOUND_CONFIRMED)

    def test_filled_leg_without_unwind_fill_is_manual_review_not_unwound(self):
        # leg A reports a fill but the account never shows it (e.g. exchange-side lag): accounts read flat,
        # nothing to unwind — the journaled leg stays OPEN, so the state cannot be UNWOUND_CONFIRMED
        def ghost_long(s, q):
            self.L.calls.append(("open_long", s))
            return self.L._fill(s, "buy", q)
        self.L.open_long = ghost_long
        self.L.fail[("open_short", "B/USDT")] = (RuntimeError("rejected"), False)
        e = _engine(self.L)
        self.assertIsNone(e.open_pair_trade(self.cfg, _decision("LONG_SPREAD")))
        inc = ExecutionIncident.objects.get()
        self.assertEqual((inc.state, inc.requires_manual_review), (MANUAL_REVIEW_REQUIRED, True))
        self.assertEqual(TradeRecord.objects.get(leg="A").status, "OPEN")
        self.assertIn("A:A/USDT", inc.legs_open_after)


# ── Post-audit Phase 1 (master prompt) — journal-mode separation + A2 cash basis
#    (claude code changed: new section) ──────────────────────────────────────────
class JournalModeAndEquityBasisTests(TestCase):
    def setUp(self):
        self.L = Ledger({"A/USDT": 1.0, "B/USDT": 2.0})
        self.cfg = _cfg()

    def _row(self, mode, net=None, status=None, side="BUY", symbol="A/USDT", qty=1.0, entry=1.0, fee=0.0,
             ptid=None, closed_at=None, order_id=None):
        return TradeRecord.objects.create(
            execution_mode=mode, symbol=symbol, side=side, strategy="PairsTrading_A_USDT/B_USDT", entry_price=entry,
            sl=1.0, tp=1.0, quantity=qty, fee_entry=fee, order_id=order_id or str(uuid.uuid4()), net_pnl=net,
            status=status or ("WIN" if (net or 0) > 0 else "LOSS"), pair_trade_id=ptid or uuid.uuid4(), leg="A",
            venue=f"v{uuid.uuid4().hex[:8]}", closed_at=closed_at)

    def test_live_guard_ignores_dry_run_pnl_and_vice_versa(self):
        self._row("DRY_RUN", net=-200.0)                                      # a −40 % SIMULATED loss
        self.assertTrue(_engine(self.L, dry_run=False).drawdown_entry_check(CAP)[0])   # live: not its P&L
        self.assertFalse(_engine(self.L, dry_run=True).drawdown_entry_check(CAP)[0])   # dry run: its own loss trips
        self._row("LIVE", net=-80.0)                                          # −16 % real
        self.assertFalse(_engine(self.L, dry_run=False).drawdown_entry_check(CAP)[0])

    def test_unknown_mode_row_fails_closed_in_both_modes(self):
        self._row("UNKNOWN", net=10.0)
        for dry in (False, True):
            allowed, why = _engine(self.L, dry_run=dry).drawdown_entry_check(CAP)
            self.assertFalse(allowed)
            self.assertIn("UNKNOWN", why)

    def test_engine_without_a_definite_mode_fails_closed(self):
        e = _engine(self.L)
        e.trade_logger.execution_mode = "UNKNOWN"
        allowed, why = e.drawdown_entry_check(CAP)
        self.assertFalse(allowed)
        self.assertIn("not LIVE or DRY_RUN", why)

    def test_open_leg_entry_fee_is_deducted_now(self):
        self._row("LIVE", status="OPEN", qty=100.0, entry=1.0, fee=5.0)      # price unchanged at 1.0
        equity, _ = _engine(self.L).strategy_equity(CAP)
        self.assertAlmostEqual(equity, 494.9)                                 # 500 + 0 mark − 5 fee paid − 0.1 est. exit fee (100 × 1.0 × 0.001)   # claude code changed: liquidation mark

    def test_live_accrued_interest_deducted_once_per_account_dry_run_not(self):
        self.L.acc["B/USDT"]["interest"] = 10.0                               # 10 base owed @ 2.0 = 20 USDT
        self._row("LIVE", status="OPEN", side="SELL", symbol="B/USDT", qty=1.0, entry=2.0)
        self._row("LIVE", status="OPEN", side="SELL", symbol="B/USDT", qty=1.0, entry=2.0)
        self.assertAlmostEqual(_engine(self.L).strategy_equity(CAP)[0], 479.996)        # 500 − 20 interest (once, not twice) − 2 × (1 × 2.0 × 0.001) exit fee   # claude code changed: liquidation mark
        TradeRecord.objects.update(execution_mode="DRY_RUN")
        self.assertAlmostEqual(_engine(self.L, dry_run=True).strategy_equity(CAP)[0], 499.996)  # interest unmodelled in dry run; exit fees still deducted   # claude code changed: liquidation mark

    def test_realised_path_is_ordered_by_closed_at_not_updated_at(self):
        from datetime import datetime, timezone as tz
        t1, t2 = datetime(2026, 9, 1, tzinfo=tz.utc), datetime(2026, 9, 2, tzinfo=tz.utc)
        self._row("LIVE", net=-90.0, closed_at=t2)                            # written first (earlier updated_at)…
        self._row("LIVE", net=100.0, closed_at=t1)                            # …but closed first
        # true path 500 → 600 → 510: exactly 15 % below the 600 peak → blocks
        # (updated_at order 500 → 410 → 510 would see a 510 peak and allow)
        self.assertFalse(_engine(self.L).drawdown_entry_check(CAP)[0])

    def test_engine_writes_mode_closed_at_and_incident_mode(self):
        e = _engine(self.L)
        tid = e.open_pair_trade(self.cfg, _decision())
        self.assertEqual(set(TradeRecord.objects.filter(pair_trade_id=tid).values_list("execution_mode", flat=True)), {"LIVE"})
        self.assertTrue(e.close_pair_trade(self.cfg, "TIMESTOP"))
        self.assertFalse(TradeRecord.objects.filter(pair_trade_id=tid, closed_at__isnull=True).exists())
        self.L.fail[("open_short", "B/USDT")] = (RuntimeError("rejected"), False)
        e2 = _engine(self.L, dry_run=True)
        e2.open_pair_trade(self.cfg, _decision())
        self.assertEqual(ExecutionIncident.objects.get().execution_mode, "DRY_RUN")

    def test_migration_0014_backfill_never_assumes_live(self):
        from importlib import import_module
        from django.apps import apps
        mig = import_module("bot.migrations.0014_journal_execution_mode_provenance")
        a = self._row("UNKNOWN", order_id="dryrun-A/USDT-buy-1")
        b = self._row("UNKNOWN", order_id="DRYRUN-BTCUSDT-1700000000000")
        c = self._row("UNKNOWN", order_id="1234567890")                      # a real-looking exchange id
        mig.backfill_execution_mode(apps, None)
        modes = dict(TradeRecord.objects.filter(id__in=(a.id, b.id, c.id)).values_list("id", "execution_mode"))
        self.assertEqual((modes[a.id], modes[b.id], modes[c.id]), ("DRY_RUN", "DRY_RUN", "UNKNOWN"))

    def test_main_bot_trade_logger_records_mode_and_closed_at(self):
        from bot.journal.trade_logger import TradeLogger
        sig = {"symbol": "BTC/USDT", "signal": "BUY", "sl": 90.0, "tp": 120.0, "rsi": 50.0}
        fill = {"fill_price": 100.0, "filled_qty": 1.0, "fee_usdt": 0.1, "order_id": "DRYRUN-BTCUSDT-1", "slippage_pct": 0.0}
        TradeLogger(execution_mode="DRY_RUN").log_entry(sig, fill, 10.0, venue="vtest")   # returns None by design
        self.assertEqual(TradeRecord.objects.get(venue="vtest", order_id="DRYRUN-BTCUSDT-1").execution_mode, "DRY_RUN")
        self.assertEqual(TradeLogger().execution_mode, "UNKNOWN")             # nobody set it → never silently LIVE


class DashboardModeSeparationTests(TestCase):   # claude code changed: new — Post-audit Phase 1 journal-mode separation
    def _closed(self, mode, net, strategy="PairsTrading_A_USDT/B_USDT", ptid=None, leg="A", symbol="A/USDT"):
        return TradeRecord.objects.create(execution_mode=mode, symbol=symbol, side="BUY", strategy=strategy,
                                          entry_price=1.0, sl=1.0, tp=1.0, quantity=1.0, order_id=str(uuid.uuid4()),
                                          net_pnl=net, status="WIN" if (net or 0) > 0 else "LOSS",
                                          pair_trade_id=ptid or uuid.uuid4(), leg=leg, venue=f"v{uuid.uuid4().hex[:8]}")

    def test_execution_monitor_headline_is_live_only(self):
        from bot.views.terminal_data import get_execution_state
        self._closed("DRY_RUN", -40.0)
        self._closed("LIVE", 7.5)
        self._closed("UNKNOWN", 3.0)
        self._closed("LIVE", None)                                            # unvalued: counted, never 0
        s = get_execution_state()
        self.assertEqual(s["realized_pnl"], 7.5)
        self.assertEqual(s["realized_pnl_by_mode"], {"DRY_RUN": -40.0, "LIVE": 7.5, "UNKNOWN": 3.0})
        self.assertEqual(s["unvalued_pnl_count"], 1)

    def test_pairs_pilot_shows_one_mode_never_a_blend(self):
        from bot.views.pairs_pilot_data import get_pairs_pilot_state
        from bot.pairs.config import PAIR_NAMES
        pair = PAIR_NAMES[0]
        tag = f"PairsTrading_{pair}"
        d = uuid.uuid4()
        self._closed("DRY_RUN", -5.0, strategy=tag, ptid=d, leg="A")
        self._closed("DRY_RUN", -5.0, strategy=tag, ptid=d, leg="B", symbol="B/USDT")
        st = get_pairs_pilot_state()
        self.assertEqual(st["execution_mode"], "DRY_RUN")
        p = next(x for x in st["pairs"] if x["pair_name"] == pair)
        self.assertEqual((p["n_trades"], p["net_pnl"]), (1, -10.0))
        live = uuid.uuid4()
        self._closed("LIVE", 2.0, strategy=tag, ptid=live, leg="A")
        self._closed("LIVE", 1.0, strategy=tag, ptid=live, leg="B", symbol="B/USDT")
        st = get_pairs_pilot_state()
        self.assertEqual(st["execution_mode"], "LIVE")                        # live exists → live only
        p = next(x for x in st["pairs"] if x["pair_name"] == pair)
        self.assertEqual((p["n_trades"], p["net_pnl"]), (1, 3.0))             # the dry-run −10 is NOT blended in
        self.assertEqual(st["other_mode_counts"], {"DRY_RUN": 2})

    def test_pages_render(self):
        for url in ("/execution/", "/execution/pairs-pilot/"):
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertIn(r.status_code, (200, 302))


class LiquidationMarkTests(TestCase):   # claude code changed: new — Post-audit Phase 1 A2, owner decision 2026-10-02 (liquidation-value marking)
    """Open legs are valued at what closing them NOW would realise: long at the
    bid, short at the ask, minus FEE_RATE × q × mark. Expected values are hand
    arithmetic (fee rate 0.001)."""

    def setUp(self):
        self.L = Ledger({"A/USDT": 1.0, "B/USDT": 2.0})

    def _open(self, side, symbol, qty, entry):
        return TradeRecord.objects.create(execution_mode=MODE_LIVE, symbol=symbol, side=side,
                                          strategy="PairsTrading_A_USDT/B_USDT", entry_price=entry, sl=1.0, tp=1.0,
                                          quantity=qty, order_id=str(uuid.uuid4()), status="OPEN",
                                          pair_trade_id=uuid.uuid4(), leg="A", venue=f"v{uuid.uuid4().hex[:8]}")

    def test_long_marked_at_bid(self):
        self.L.half_spread["A/USDT"] = 0.01                                   # bid 0.99 / ask 1.01
        self._open("BUY", "A/USDT", 100.0, 1.0)
        # 100 × (0.99 − 1.00) = −1.00 ; exit fee 100 × 0.99 × 0.001 = 0.099 → 500 − 1.099
        self.assertAlmostEqual(_engine(self.L).strategy_equity(CAP)[0], 498.901)

    def test_short_marked_at_ask(self):
        self.L.half_spread["B/USDT"] = 0.02                                   # bid 1.98 / ask 2.02
        self._open("SELL", "B/USDT", 10.0, 2.0)
        # −1 × 10 × (2.02 − 2.00) = −0.20 ; exit fee 10 × 2.02 × 0.001 = 0.0202 → 500 − 0.2202
        self.assertAlmostEqual(_engine(self.L).strategy_equity(CAP)[0], 499.7798)

    def test_spread_alone_can_trip_the_guard_at_the_boundary(self):
        # peak 600 after +100; open long 1,000 @ 1.0, bid 0.91 → −90 − 0.91 fee → 509.09 < 510 → blocks
        _closed_leg(100.0)
        self.L.half_spread["A/USDT"] = 0.09
        self._open("BUY", "A/USDT", 1000.0, 1.0)
        allowed, why = _engine(self.L).drawdown_entry_check(CAP)
        self.assertFalse(allowed, why)
        self.assertIn("TRIPPED", why)

    def test_missing_or_crossed_book_fails_closed(self):
        from bot.pairs.margin_adapter import BinanceIsolatedMarginAdapter
        for ticker, msg in (({"bid": None, "ask": 1.0, "last": 1.0}, "no usable bid/ask"),
                            ({"bid": 0.0, "ask": 1.0, "last": 1.0}, "no usable bid/ask"),
                            ({"bid": 1.02, "ask": 1.0, "last": 1.0}, "crossed book")):
            stub = _StubExchange(**ticker)
            ad = BinanceIsolatedMarginAdapter.__new__(BinanceIsolatedMarginAdapter)
            ad.exchange = stub
            with self.subTest(ticker=ticker):
                with self.assertRaisesRegex(ValueError, msg):
                    ad.get_bid_ask("A/USDT")
        def broken(s):
            raise ValueError("A/USDT: crossed book (bid 1.02 > ask 1.0)")
        self.L.get_bid_ask = broken
        self._open("BUY", "A/USDT", 1.0, 1.0)
        allowed, why = _engine(self.L).drawdown_entry_check(CAP)
        self.assertFalse(allowed)
        self.assertIn("crossed book", why)

    def test_no_open_legs_needs_no_quote(self):
        _closed_leg(5.0)
        self.assertTrue(_engine(self.L).drawdown_entry_check(CAP)[0])
        self.assertNotIn("bid_ask", [c[0] for c in self.L.calls])
