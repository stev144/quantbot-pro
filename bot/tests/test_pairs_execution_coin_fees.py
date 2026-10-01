# claude code changed: new file — live pairs execution: base-asset fee & quantity
# handling. Written BEFORE the fix.
#
# Exchange semantics used for the expected values (verified in the installed
# ccxt source, not assumed):
#   * ccxt's Binance fee metadata is 'feeSide': 'get' (ccxt/binance.py, fees.trading):
#     the commission is charged in the asset RECEIVED — base on a buy, quote on a
#     sell — unless it is paid in a third asset (BNB).
#   * Order quantities are cut to the LOT_SIZE stepSize with TRUNCATE
#     (Exchange.amount_to_precision, precisionMode TICK_SIZE), i.e. rounded DOWN.
#   * ccxt 'filled' is Binance executedQty — the gross quantity traded, before any
#     commission; the commission is reported separately per fill.
#
# `_Ledger` below is an independent, hand-written model of one isolated-margin
# account under those semantics. Expected values come from it or from plain
# arithmetic — never from the production code under test.

import random
from decimal import Decimal

import ccxt
from ccxt.base.decimal_to_precision import TICK_SIZE, TRUNCATE, decimal_to_precision
from django.test import SimpleTestCase

from bot.engines.order_manager import OrderUnconfirmedException
from bot.pairs.margin_adapter import (
    AmountRules,
    CloseQuantityUnavailableException,
    RepaymentIncompleteException,
    build_fill_result,
    ceil_to_step,
    cover_buy_quantity,
    floor_to_step,
    remaining_debt_or_raise,
    sellable_quantity,
)
from bot.pairs.trade_logger import allocate_short_close, compute_leg_pnl


class _Ledger:
    """Independent model of one Binance isolated-margin account ('feeSide: get')."""

    def __init__(self, step=None):
        self.base_free, self.borrowed, self.interest, self.quote = 0.0, 0.0, 0.0, 0.0
        self.step = step

    def _trunc(self, q):
        if not self.step:
            return q
        return float(decimal_to_precision(q, TRUNCATE, self.step, TICK_SIZE))

    def buy(self, qty, price, fee_rate, fee_ccy):
        q = self._trunc(qty)
        self.quote -= q * price
        self.base_free += q - (q * fee_rate if fee_ccy == "BASE" else 0.0)
        if fee_ccy == "USDT":
            self.quote -= q * price * fee_rate
        return q

    def sell(self, qty, price, fee_rate, fee_ccy):
        q = self._trunc(qty)
        if q > self.base_free + 1e-12:
            raise ValueError("insufficient balance")          # what Binance does
        self.base_free -= q
        self.quote += q * price - (q * price * fee_rate if fee_ccy == "USDT" else 0.0)
        return q

    def repay(self, qty):
        if qty > self.base_free + 1e-12:
            raise ValueError("insufficient balance to repay")
        self.base_free -= qty
        pay_interest = min(qty, self.interest)
        self.interest -= pay_interest
        self.borrowed -= qty - pay_interest

    @property
    def debt(self):
        return self.borrowed + self.interest


def _parse(fills, executed="1000", orig="1000", side="BUY", status="FILLED"):
    return ccxt.binance().parse_order({
        "symbol": "XUSDT", "orderId": 7, "clientOrderId": "c", "transactTime": 1700000000000,
        "price": "0", "origQty": orig, "executedQty": executed, "cummulativeQuoteQty": "10",
        "status": status, "timeInForce": "GTC", "type": "MARKET", "side": side, "isIsolated": True, "fills": fills})


# ─────────────────────────────────────────────────────────────── LONG LEGS

class LongLegTest(SimpleTestCase):
    RULES = AmountRules(step=1.0, min_qty=1.0)

    def test_1_long_usdt_fee_does_not_reduce_base(self):
        L = _Ledger(step=1.0)
        L.buy(1000, 0.01, 0.001, "USDT")
        self.assertEqual(L.base_free, 1000.0)                                  # invariant A
        q = sellable_quantity(1000.0, L.base_free, self.RULES)
        self.assertEqual(q, 1000.0)
        L.sell(q, 0.011, 0.001, "USDT")                                        # succeeds
        self.assertEqual(L.base_free, 0.0)

    def test_2_long_base_fee_sells_only_what_is_held(self):
        L = _Ledger(step=1.0)
        L.buy(1000, 0.01, 0.001, "BASE")
        self.assertEqual(L.base_free, 999.0)                                   # invariant B: reduced exactly once
        with self.assertRaises(ValueError):                                    # the OLD close path: sell the gross fill
            _Ledger.sell(L, 1000, 0.011, 0.001, "USDT")
        q = sellable_quantity(1000.0, L.base_free, self.RULES)
        self.assertEqual(q, 999.0)                                             # invariant G
        L.sell(q, 0.011, 0.001, "USDT")
        self.assertEqual(L.base_free, 0.0)

    def test_3_long_third_asset_fee_leaves_base_intact_and_pnl_unvalued(self):
        L = _Ledger(step=1.0)
        L.buy(1000, 0.01, 0.001, "BNB")
        self.assertEqual(L.base_free, 1000.0)                                  # invariant C
        self.assertEqual(sellable_quantity(1000.0, L.base_free, self.RULES), 1000.0)
        r = compute_leg_pnl("BUY", 1000.0, 0.01, ((0.00006, "BNB"),), 1000.0, 0.011, ((0.011, "USDT"),), "X", "USDT")
        self.assertIsNone(r.net_pnl)

    def test_7_partial_long_close_after_base_fee_conserves_quantity(self):
        L = _Ledger(step=1.0)
        L.buy(1000, 0.01, 0.001, "BASE")                                       # hold 999
        q = sellable_quantity(400.0, L.base_free, self.RULES)
        self.assertEqual(q, 400.0)
        L.sell(q, 0.011, 0.001, "USDT")
        self.assertEqual(L.base_free, 599.0)                                   # invariant H: 999 − 400
        self.assertEqual(sellable_quantity(1000.0 - 400.0, L.base_free, self.RULES), 599.0)

    def test_12_filled_less_than_requested(self):
        o = _parse([{"price": "0.01", "qty": "600", "commission": "0.6", "commissionAsset": "X"}], executed="600",
                   status="EXPIRED")
        fill = build_fill_result("7", "X/USDT", "buy", o, o)
        self.assertEqual(fill.filled_qty, 600.0)                               # invariant F: actual, not requested 1000
        self.assertEqual(sellable_quantity(fill.filled_qty, 600.0 - 0.6, AmountRules(step=0.1, min_qty=0.1)), 599.4)

    def test_13_insufficient_available_raises_instead_of_submitting(self):
        with self.assertRaises(CloseQuantityUnavailableException):
            sellable_quantity(1000.0, 0.0, self.RULES)
        with self.assertRaises(CloseQuantityUnavailableException):
            sellable_quantity(1000.0, 0.7, self.RULES)                         # floors to 0 < min_qty
        self.assertTrue(issubclass(CloseQuantityUnavailableException, OrderUnconfirmedException))   # caught by close_pair_trade → pair disabled


# ─────────────────────────────────────────────────────────────── SHORT LEGS

class ShortLegTest(SimpleTestCase):

    def _short(self, principal, interest, step):
        L = _Ledger(step=step)
        L.borrowed = principal
        L.base_free = principal
        L.sell(principal, 0.02, 0.001, "USDT")                                 # sell fee in quote ('get' side)
        L.interest = interest
        return L

    def test_4_short_usdt_fee(self):
        L = self._short(1000.0, 0.0, step=0.1)
        q = cover_buy_quantity(L.debt, L.base_free, fee_buffer_rate=0.001, rules=AmountRules(step=0.1, min_qty=0.1))
        self.assertAlmostEqual(q, 1001.1, places=9)                             # ceil(1000/0.999 = 1001.001) to 0.1
        L.buy(q, 0.021, 0.001, "USDT")
        L.repay(min(L.debt, L.base_free))
        self.assertAlmostEqual(L.debt, 0.0, places=12)                          # invariant I
        self.assertAlmostEqual(L.base_free, 1.1, places=9)                      # leftover from the fee buffer

    def test_5_short_base_fee_repays_in_full(self):
        L = self._short(1000.0, 0.0, step=0.1)
        old = _Ledger(step=0.1); old.__dict__.update(L.__dict__)
        old.buy(old.debt, 0.021, 0.001, "BASE")                                  # OLD: buy exactly the debt
        with self.assertRaises(ValueError):
            old.repay(old.debt)                                                  # 999.0 received < 1000 owed
        q = cover_buy_quantity(L.debt, L.base_free, 0.001, AmountRules(step=0.1, min_qty=0.1))
        L.buy(q, 0.021, 0.001, "BASE")
        self.assertGreaterEqual(L.base_free, L.debt)
        L.repay(L.debt)
        self.assertAlmostEqual(L.debt, 0.0, places=12)

    def test_6_short_interest_and_base_fee_known_answer(self):
        # principal 1000, interest 0.4 -> debt 1000.4; step 0.1; base fee 0.1%.
        L = self._short(1000.0, 0.4, step=0.1)
        q = cover_buy_quantity(L.debt, L.base_free, 0.001, AmountRules(step=0.1, min_qty=0.1))
        self.assertAlmostEqual(q, 1001.5, places=9)                              # 1000.4/0.999 = 1001.4014… -> 1001.5
        L.buy(q, 0.021, 0.001, "BASE")
        self.assertAlmostEqual(L.base_free, 1001.5 * 0.999, places=9)             # 1000.4985 received
        L.repay(L.debt)
        self.assertAlmostEqual(L.debt, 0.0, places=12)
        self.assertAlmostEqual(L.base_free, 1001.5 * 0.999 - 1000.4, places=9)    # 0.0985 left over
        # accounting sees the same flows: leftover valued at the exit price, not hidden
        r = compute_leg_pnl("SELL", 1000.0, 0.02, ((0.02, "USDT"),), 1001.5, 0.021, ((1.0015, "X"),), "X", "USDT", interest_qty=0.4)
        self.assertAlmostEqual(r.residual_base, 1001.5 * 0.999 - 1000.4, places=9)

    def test_8_partial_short_close_with_base_fee(self):
        L = self._short(1000.0, 0.4, step=0.01)
        a = allocate_short_close(1000.0, 0.4, 400.0)                              # repay 400 principal + 0.16 interest
        q = cover_buy_quantity(a.repay_qty, L.base_free, 0.001, AmountRules(step=0.01, min_qty=0.01))
        self.assertAlmostEqual(q, 400.57, places=9)                               # 400.16/0.999 = 400.5605… -> 400.57
        L.buy(q, 0.021, 0.001, "BASE")
        self.assertGreaterEqual(L.base_free, a.repay_qty)
        L.repay(a.repay_qty)
        self.assertAlmostEqual(L.debt, 600.24, places=9)                          # 600 principal + 0.24 interest remain
        self.assertAlmostEqual(a.remaining_principal + a.remaining_interest, 600.24, places=12)

    def test_precision_old_truncation_left_debt(self):
        # debt 1000.00041 with step 0.1: ccxt truncates a buy of exactly the debt DOWN to 1000.0
        self.assertEqual(decimal_to_precision(1000.00041, TRUNCATE, 0.1, TICK_SIZE), "1000")
        q = cover_buy_quantity(1000.00041, 0.0, 0.0, AmountRules(step=0.1, min_qty=0.1))
        self.assertAlmostEqual(q, 1000.1, places=9)                               # rounded UP instead
        self.assertEqual(decimal_to_precision(q, TRUNCATE, 0.1, TICK_SIZE), "1000.1")   # ccxt leaves it unchanged
        self.assertGreaterEqual(q, 1000.00041)

    def test_existing_free_base_reduces_what_must_be_bought(self):
        self.assertAlmostEqual(cover_buy_quantity(1000.4, 0.4, 0.0, AmountRules(step=0.1, min_qty=0.1)), 1000.0, places=9)
        self.assertEqual(cover_buy_quantity(1000.0, 1000.5, 0.001, AmountRules(step=0.1, min_qty=0.1)), 0.0)

    def test_min_quantity_is_respected_on_a_tiny_cover(self):
        self.assertAlmostEqual(cover_buy_quantity(0.3, 0.0, 0.0, AmountRules(step=0.1, min_qty=1.0)), 1.0, places=9)

    def test_remaining_debt_is_never_silent(self):
        self.assertEqual(remaining_debt_or_raise("X/USDT", 0.0, 0.0), 0.0)
        with self.assertRaises(RepaymentIncompleteException):
            remaining_debt_or_raise("X/USDT", 0.0, 0.0004)
        self.assertTrue(issubclass(RepaymentIncompleteException, OrderUnconfirmedException))

    def test_bad_fee_buffer_rejected(self):
        for bad in (-0.1, 1.0, 1.5):
            with self.assertRaises(ValueError):
                cover_buy_quantity(10.0, 0.0, bad, AmountRules(step=0.1, min_qty=0.1))


# ─────────────────────────────────────────────────────────────── FILLS / FEES

class FillParsingTest(SimpleTestCase):

    def test_9_multiple_fills_aggregate_quantity_and_fee(self):
        o = _parse([{"price": "0.01", "qty": "600", "commission": "0.6", "commissionAsset": "X"},
                    {"price": "0.0101", "qty": "400", "commission": "0.4", "commissionAsset": "X"}])
        fill = build_fill_result("7", "X/USDT", "buy", o, o)
        self.assertEqual(fill.filled_qty, 1000.0)
        self.assertEqual(len(fill.fees), 1)
        self.assertAlmostEqual(fill.fees[0][0], 1.0, places=12)
        self.assertEqual(fill.fees[0][1], "X")

    def test_10_mixed_fee_currencies_stay_distinct(self):
        o = _parse([{"price": "0.01", "qty": "600", "commission": "0.6", "commissionAsset": "X"},
                    {"price": "0.01", "qty": "400", "commission": "0.00003", "commissionAsset": "BNB"}])
        fill = build_fill_result("7", "X/USDT", "buy", o, o)
        self.assertEqual(sorted(c for _, c in fill.fees), ["BNB", "X"])            # invariant D
        self.assertEqual(fill.fee_asset, "MIXED")
        self.assertIsNone(fill.fee_usdt)
        L = _Ledger(step=0.1); L.base_free = 1000.0 - 0.6                          # only the base part reduces quantity
        self.assertAlmostEqual(sellable_quantity(1000.0, L.base_free, AmountRules(step=0.1, min_qty=0.1)), 999.4, places=9)

    def test_11_zero_commission_reproduces_previous_quantities(self):
        o = _parse([{"price": "0.01", "qty": "1000", "commission": "0", "commissionAsset": "X"}])
        fill = build_fill_result("7", "X/USDT", "buy", o, o)
        self.assertEqual(fill.filled_qty, 1000.0)
        self.assertEqual(sellable_quantity(fill.filled_qty, 1000.0, AmountRules(step=1.0, min_qty=1.0)), 1000.0)
        self.assertEqual(cover_buy_quantity(1000.0, 0.0, 0.0, AmountRules(step=1.0, min_qty=1.0)), 1000.0)


# ─────────────────────────────────────────────────────────────── PRECISION + DRY RUN

class PrecisionAndDryRunTest(SimpleTestCase):

    def test_14_floor_and_ceil_to_step(self):
        self.assertEqual(floor_to_step(999.99, 0.1), 999.9)
        self.assertEqual(ceil_to_step(999.91, 0.1), 1000.0)
        self.assertEqual(floor_to_step(0.30000000000000004, 0.1), 0.3)             # float noise does not lose a step
        self.assertEqual(ceil_to_step(0.30000000000000004, 0.1), 0.3)
        self.assertEqual(floor_to_step(123.456, None), 123.456)                    # no rule -> unchanged

    def test_invariant_J_rounding_never_exceeds_available(self):
        rng = random.Random(3)
        for _ in range(3000):
            step = rng.choice([1.0, 0.1, 0.01, 0.001, 0.00001])
            free = rng.uniform(1, 1e6)
            req = free * rng.uniform(0.5, 1.5)
            try:
                q = sellable_quantity(req, free, AmountRules(step=step, min_qty=step))
            except CloseQuantityUnavailableException:
                continue
            self.assertLessEqual(Decimal(str(q)), Decimal(str(free)))
            self.assertLessEqual(q, req + 1e-9)

    def test_15_dry_run_quantities_unchanged(self):
        # Dry run: no step rule, USDT-only simulated fees (buffer 0) -> exactly the old quantities.
        rules = AmountRules(step=None, min_qty=None)
        self.assertEqual(sellable_quantity(1958.8412935525066, 1958.8412935525066, rules), 1958.8412935525066)
        self.assertEqual(cover_buy_quantity(351.63856617740385, 0.0, 0.0, rules), 351.63856617740385)
