# claude code changed: new file — LIVE-01 / LIVE-02 (Quantitative Reality Audit,
# 2026-09-29). Written BEFORE the fix. Known-answer and invariant tests for the
# live pairs accounting in bot/pairs/trade_logger.py and bot/pairs/margin_adapter.py.
#
# Every expected number is plain arithmetic written out here from the asset
# flows (quote cash in/out, base units received/delivered/owed) — never taken
# from the code under test. Exchange-response tests feed ccxt's REAL Binance
# parser (ccxt.binance().parse_order) with Binance's documented response shape,
# not a mocked exchange object, matching this repo's no-mocking convention.

import math
import uuid

import ccxt
from django.test import SimpleTestCase, TestCase

from bot.journal.models import TradeRecord
from bot.pairs.margin_adapter import MarginFillResult, build_fill_result, extract_fees, value_fees_in_quote
from bot.pairs.trade_logger import PairsTradeLogger, allocate_short_close, compute_leg_pnl

OLD_AUDIT_RESULT = -1.041008   # what the pre-fix logger produced for the LIVE-01 example


def _binance_full_response(fills, side="BUY", qty="1958.84", quote_qty="36.434"):
    """Binance's documented margin order response with newOrderRespType=FULL
    (what ccxt requests for spot/margin market orders)."""
    return {"symbol": "DODOUSDT", "orderId": 28, "clientOrderId": "x", "transactTime": 1507725176595,
            "price": "0", "origQty": qty, "executedQty": qty, "cummulativeQuoteQty": quote_qty,
            "status": "FILLED", "timeInForce": "GTC", "type": "MARKET", "side": side,
            "isIsolated": True, "fills": fills}


def _binance_status_response():
    """What GET margin/order (fetch_order) returns: no fills, no commission."""
    return {"symbol": "DODOUSDT", "orderId": 28, "price": "0", "origQty": "1958.84", "executedQty": "1958.84",
            "cummulativeQuoteQty": "36.434", "status": "FILLED", "type": "MARKET", "side": "BUY",
            "isIsolated": True, "time": 1507725176595, "updateTime": 1507725176595}


# ─────────────────────────────────────────────────────────────── LIVE-01

class Live01ShortInterestTest(SimpleTestCase):

    def test_exact_audit_example(self):
        # Sell 1000 borrowed @0.020; after 4h at 0.0001/h owe 1000.4; buy 1000.4 @0.021.
        entry_fee = 0.001 * 1000 * 0.020          # 0.02 USDT
        exit_fee = 0.001 * 1000.4 * 0.021         # 0.0210084 USDT
        r = compute_leg_pnl("SELL", 1000.0, 0.020, ((entry_fee, "USDT"),),
                            1000.4, 0.021, ((exit_fee, "USDT"),), "X", "USDT", interest_qty=0.4)
        self.assertAlmostEqual(r.gross_pnl, 20.0 - 21.0084, places=10)            # −1.0084
        self.assertAlmostEqual(r.net_pnl, 20.0 - 21.0084 - 0.02 - 0.0210084, places=10)   # −1.0494084
        self.assertAlmostEqual(r.residual_base, 0.0, places=12)
        self.assertGreater(abs(r.net_pnl - OLD_AUDIT_RESULT), 0.008)              # old result is gone

    def test_zero_interest_reproduces_old_quantity_based_result(self):
        r = compute_leg_pnl("SELL", 1000.0, 0.020, ((0.02, "USDT"),),
                            1000.0, 0.021, ((0.021, "USDT"),), "X", "USDT", interest_qty=0.0)
        old = (0.020 - 0.021) * 1000.0 - 0.02 - 0.021
        self.assertAlmostEqual(r.net_pnl, old, places=12)

    def test_partial_close_allocates_interest_pro_rata_and_conserves_quantity(self):
        a = allocate_short_close(principal_qty=1000.0, interest_qty=0.4, closed_principal=250.0)
        self.assertAlmostEqual(a.closed_principal, 250.0)
        self.assertAlmostEqual(a.closed_interest, 0.1, places=12)
        self.assertAlmostEqual(a.repay_qty, 250.1, places=12)
        self.assertAlmostEqual(a.remaining_principal, 750.0, places=12)
        self.assertAlmostEqual(a.remaining_interest, 0.3, places=12)
        self.assertAlmostEqual(a.closed_principal + a.remaining_principal, 1000.0, places=12)
        self.assertAlmostEqual(a.closed_interest + a.remaining_interest, 0.4, places=12)
        r = compute_leg_pnl("SELL", a.closed_principal, 0.020, ((0.001 * 250 * 0.020, "USDT"),),
                            a.repay_qty, 0.021, ((0.001 * 250.1 * 0.021, "USDT"),), "X", "USDT",
                            interest_qty=a.closed_interest)
        self.assertAlmostEqual(r.net_pnl, 250 * 0.020 - 250.1 * 0.021 - 0.005 - 0.0052521, places=10)
        self.assertAlmostEqual(r.residual_base, 0.0, places=12)

    def test_partial_close_bounds(self):
        for bad in (0.0, -1.0, 1000.0001):
            with self.assertRaises(ValueError):
                allocate_short_close(1000.0, 0.4, bad)

    def test_long_leg_unchanged(self):
        r = compute_leg_pnl("BUY", 100.0, 10.0, ((1.0, "USDT"),), 100.0, 11.0, ((1.1, "USDT"),), "X", "USDT")
        self.assertAlmostEqual(r.net_pnl, (11.0 - 10.0) * 100.0 - 1.0 - 1.1, places=12)
        self.assertAlmostEqual(r.gross_pnl, 100.0, places=12)

    def test_existing_dry_run_trade_reproduces_exactly(self):
        # DB rows 40/41 (2026-09-28 dry run, USDT fees, zero simulated interest).
        a = compute_leg_pnl("SELL", 1958.8412935525066, 0.018590699999999998, ((0.036416, "USDT"),),
                            1958.8412935525066, 0.018719355, ((0.036668, "USDT"),), "DODO", "USDT", interest_qty=0.0)
        b = compute_leg_pnl("BUY", 351.63856617740385, 0.02123061, ((0.007466, "USDT"),),
                            351.63856617740385, 0.02110944, ((0.007423, "USDT"),), "FIDA", "USDT")
        self.assertAlmostEqual(round(a.net_pnl, 4), -0.3251, places=4)
        self.assertAlmostEqual(round(b.net_pnl, 4), -0.0575, places=4)

    def test_unknown_interest_on_a_short_assumes_exit_buy_exactly_repaid(self):
        r = compute_leg_pnl("SELL", 1000.0, 0.020, ((0.02, "USDT"),), 1000.4, 0.021, ((0.0210084, "USDT"),),
                            "X", "USDT", interest_qty=None)
        self.assertAlmostEqual(r.net_pnl, 20.0 - 21.0084 - 0.02 - 0.0210084, places=10)
        self.assertTrue(r.interest_inferred)


# ─────────────────────────────────────────────────────────────── LIVE-02

class Live02FeeCurrencyTest(SimpleTestCase):

    def test_case_a_fee_in_usdt(self):
        self.assertAlmostEqual(value_fees_in_quote(((1.0, "USDT"),), "BTC", "USDT", 50000.0), 1.0)

    def test_case_b_fee_in_base_asset(self):
        v = value_fees_in_quote(((1.95884, "DODO"),), "DODO", "USDT", 0.0186)
        self.assertAlmostEqual(v, 1.95884 * 0.0186, places=12)       # ≈ 0.036434
        self.assertNotAlmostEqual(v, 1.95884, places=3)

    def test_case_c_fee_in_third_asset_is_not_valued(self):
        self.assertIsNone(value_fees_in_quote(((0.00006, "BNB"),), "DODO", "USDT", 0.0186))
        r = compute_leg_pnl("BUY", 100.0, 10.0, ((0.00006, "BNB"),), 100.0, 11.0, ((1.1, "USDT"),), "X", "USDT")
        self.assertIsNone(r.net_pnl)
        self.assertFalse(r.valued)
        self.assertIn((0.00006, "BNB"), r.unvalued_fees)

    def test_no_fee_information_is_unknown_not_zero(self):
        self.assertIsNone(value_fees_in_quote((), "DODO", "USDT", 0.0186))

    def test_fee_currency_invariant(self):
        self.assertAlmostEqual(value_fees_in_quote(((1.0, "DODO"),), "DODO", "USDT", 0.5), 0.5)
        self.assertAlmostEqual(value_fees_in_quote(((1.0, "DODO"),), "DODO", "USDT", 1.0), 1.0)
        self.assertNotAlmostEqual(value_fees_in_quote(((1.0, "DODO"),), "DODO", "USDT", 0.0186), 1.0)

    def test_base_fee_on_long_entry_is_counted_once(self):
        # Buy 1958.84 DODO @0.0186, fee 1.95884 DODO -> hold 1956.88116; sell all held @0.019, fee in USDT.
        held = 1958.84 - 1.95884
        sell_fee = 0.001 * held * 0.019
        r = compute_leg_pnl("BUY", 1958.84, 0.0186, ((1.95884, "DODO"),), held, 0.019, ((sell_fee, "USDT"),), "DODO", "USDT")
        cash = held * 0.019 - 1958.84 * 0.0186 - sell_fee
        self.assertAlmostEqual(r.net_pnl, cash, places=10)                  # base fee NOT subtracted a second time
        self.assertAlmostEqual(r.residual_base, 0.0, places=10)
        self.assertAlmostEqual(r.fee_entry_quote, 1.95884 * 0.0186, places=12)
        self.assertAlmostEqual(r.gross_pnl, held * (0.019 - 0.0186), places=10)   # P&L on the coins actually held
        self.assertAlmostEqual(r.net_pnl, r.gross_pnl - r.fee_entry_quote - r.fee_exit_quote, places=10)

    def test_base_fee_shortfall_on_short_cover_is_charged_at_exit_price(self):
        # Cover buy of exactly 1000.4 with a 1.0004-unit base fee -> loan short by 1.0004 units.
        r = compute_leg_pnl("SELL", 1000.0, 0.020, ((0.02, "USDT"),), 1000.4, 0.021, ((1.0004, "X"),), "X", "USDT", interest_qty=0.4)
        self.assertAlmostEqual(r.residual_base, -1.0004, places=10)
        self.assertAlmostEqual(r.net_pnl, 20.0 - 21.0084 - 0.02 - 1.0004 * 0.021, places=10)


class ExchangeResponseParsingTest(SimpleTestCase):
    """Real ccxt parsing of Binance's documented responses."""

    def setUp(self):
        self.ex = ccxt.binance()

    def test_full_response_fee_in_usdt(self):
        o = self.ex.parse_order(_binance_full_response([{"price": "0.0186", "qty": "1958.84", "commission": "0.036434", "commissionAsset": "USDT"}]))
        self.assertEqual(extract_fees(o), ((0.036434, "USDT"),))

    def test_full_response_fee_in_base_multi_fill(self):
        o = self.ex.parse_order(_binance_full_response([
            {"price": "0.0186", "qty": "1000", "commission": "1.0", "commissionAsset": "DODO"},
            {"price": "0.0186", "qty": "958.84", "commission": "0.95884", "commissionAsset": "DODO"}]))
        fees = extract_fees(o)
        self.assertEqual(len(fees), 1)
        self.assertAlmostEqual(fees[0][0], 1.95884, places=12)
        self.assertEqual(fees[0][1], "DODO")

    def test_full_response_mixed_currencies_keeps_every_fee(self):
        o = self.ex.parse_order(_binance_full_response([
            {"price": "0.0186", "qty": "1000", "commission": "1.0", "commissionAsset": "DODO"},
            {"price": "0.0186", "qty": "958.84", "commission": "0.00003", "commissionAsset": "BNB"}]))
        self.assertEqual(sorted(c for _, c in extract_fees(o)), ["BNB", "DODO"])

    def test_status_response_has_no_fee_information(self):
        self.assertEqual(extract_fees(self.ex.parse_order(_binance_status_response())), ())

    def test_build_fill_prefers_creation_response_fees(self):
        created = self.ex.parse_order(_binance_full_response([{"price": "0.0186", "qty": "1958.84", "commission": "1.95884", "commissionAsset": "DODO"}]))
        status = self.ex.parse_order(_binance_status_response())
        fill = build_fill_result("28", "DODO/USDT", "buy", created, status)
        self.assertEqual(fill.fees, ((1.95884, "DODO"),))
        self.assertEqual(fill.fee_asset, "DODO")
        self.assertAlmostEqual(fill.fee_usdt, 1.95884 * fill.fill_price, places=12)
        self.assertNotAlmostEqual(fill.fee_usdt, 1.95884, places=3)

    def test_build_fill_without_any_fee_information_is_unknown(self):
        status = self.ex.parse_order(_binance_status_response())
        fill = build_fill_result("28", "DODO/USDT", "buy", {"id": "28"}, status)
        self.assertEqual(fill.fees, ())
        self.assertIsNone(fill.fee_usdt)
        self.assertEqual(fill.fee_asset, "UNKNOWN")


# ─────────────────────────────────────────────────────────────── INVARIANTS

class AccountingInvariantTest(SimpleTestCase):

    def test_zero_fee_does_not_alter_pnl(self):
        r = compute_leg_pnl("SELL", 1000.0, 0.020, ((0.0, "USDT"),), 1000.4, 0.021, ((0.0, "USDT"),), "X", "USDT", interest_qty=0.4)
        self.assertAlmostEqual(r.net_pnl, r.gross_pnl, places=12)
        self.assertAlmostEqual(r.net_pnl, 20.0 - 21.0084, places=12)

    def test_higher_fee_never_improves_net(self):
        for ccy in ("USDT", "X"):
            prev = None
            for fee in (0.0, 0.01, 0.1, 1.0, 5.0):
                r = compute_leg_pnl("BUY", 100.0, 10.0, ((fee, ccy),), 100.0 - (fee if ccy == "X" else 0.0), 11.0,
                                    ((0.0, "USDT"),), "X", "USDT")
                if prev is not None:
                    self.assertLessEqual(r.net_pnl, prev + 1e-12)
                prev = r.net_pnl

    def test_positive_interest_never_improves_a_short(self):
        prev = None
        for i in (0.0, 0.1, 0.4, 2.0, 10.0):
            r = compute_leg_pnl("SELL", 1000.0, 0.020, ((0.02, "USDT"),), 1000.0 + i, 0.021,
                                ((0.001 * (1000.0 + i) * 0.021, "USDT"),), "X", "USDT", interest_qty=i)
            if prev is not None:
                self.assertLess(r.net_pnl, prev)
            prev = r.net_pnl

    def test_quantity_conservation_flags_unrepaid_loan(self):
        # Bought only the principal while 0.4 interest was owed -> 0.4 units still owed.
        r = compute_leg_pnl("SELL", 1000.0, 0.020, ((0.02, "USDT"),), 1000.0, 0.021, ((0.021, "USDT"),), "X", "USDT", interest_qty=0.4)
        self.assertAlmostEqual(r.residual_base, -0.4, places=12)
        self.assertAlmostEqual(r.net_pnl, 20.0 - 21.0 - 0.041 - 0.4 * 0.021, places=12)


# ─────────────────────────────────────────────────────────────── LOGGER (DB)

class PairsTradeLoggerRecordsTest(TestCase):

    def _fill(self, side, qty, price, fees, symbol="X/USDT"):
        fee_quote = value_fees_in_quote(fees, symbol.split("/")[0], symbol.split("/")[1], price)
        return MarginFillResult(order_id=f"t-{side}-{qty}", symbol=symbol, side=side, filled_qty=qty, fill_price=price,
                                fee_usdt=fee_quote, fee_asset=fees[0][1] if len(fees) == 1 else "MIXED", fees=fees)

    def test_short_leg_with_interest_records_corrected_pnl(self):
        tid, logger_ = uuid.uuid4(), PairsTradeLogger()
        logger_.log_leg_entry(tid, "A", "X/USDT", "SELL", self._fill("sell", 1000.0, 0.020, ((0.02, "USDT"),)), 20.0, "X_USDT/Y_USDT")
        rec = logger_.log_leg_exit(tid, "A", self._fill("buy", 1000.4, 0.021, ((0.0210084, "USDT"),)), "TIMESTOP", interest_qty=0.4)
        self.assertAlmostEqual(rec.net_pnl, round(20.0 - 21.0084 - 0.0410084, 4), places=4)
        self.assertAlmostEqual(rec.gross_pnl, round(20.0 - 21.0084, 4), places=4)
        self.assertEqual(rec.status, "LOSS")

    def test_base_fee_is_stored_in_quote_value(self):
        tid, logger_ = uuid.uuid4(), PairsTradeLogger()
        rec = logger_.log_leg_entry(tid, "A", "DODO/USDT", "BUY",
                                    self._fill("buy", 1958.84, 0.0186, ((1.95884, "DODO"),), symbol="DODO/USDT"), 36.43, "D_USDT/F_USDT")
        self.assertAlmostEqual(rec.fee_entry, 1.95884 * 0.0186, places=9)

    def test_unvalued_fee_leaves_net_unknown(self):
        tid, logger_ = uuid.uuid4(), PairsTradeLogger()
        entry = self._fill("buy", 100.0, 10.0, ((0.00006, "BNB"),))
        created = logger_.log_leg_entry(tid, "A", "X/USDT", "BUY", entry, 1000.0, "X_USDT/Y_USDT")
        self.assertIsNotNone(created, "an unvalued fee must not prevent the leg from being recorded")
        rec = logger_.log_leg_exit(tid, "A", self._fill("sell", 100.0, 11.0, ((1.1, "USDT"),)), "TIMESTOP",
                                   entry_fees=entry.fees)
        rec.refresh_from_db()
        self.assertIsNone(rec.net_pnl)
        self.assertEqual(rec.status, "UNVALUED")
        self.assertAlmostEqual(rec.gross_pnl, 100.0, places=4)
