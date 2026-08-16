# claude code changed: new file — Phase 4 (DB persistence of trade
# provenance). First TestCase in this repo exercising TradeLogger
# end-to-end against a real DB, since nothing previously touched it.

from django.test import TestCase

from bot.engines.trade_narrative import TradeNarrativeGenerator
from bot.journal.models import TradeRecord
from bot.journal.trade_logger import TradeLogger


class TradePersistenceTest(TestCase):

    def _entry_signal(self):
        return {
            "signal": "BUY", "symbol": "BTC/USDT", "entry": 50000.0,
            "sl": 49000.0, "tp": 53000.0, "rsi": 55.0,
            "reason": "ema_buy_setup", "strategy": "MovingAverageStrategy",
            "regime": "TRENDING_UP", "regime_confidence": "HIGH",
            "adx": 30.0, "atr_ratio": 1.0, "ema_spread_pct": 0.5, "bb_width_pct": 1.5,
        }

    def _fill_result(self, price=50010.0):
        return {
            "fill_price": price, "filled_qty": 0.1, "fee_usdt": 5.0,
            "order_id": "abc123", "slippage_pct": 0.02,
        }

    def test_log_entry_persists_provenance_fields(self):
        narrative = TradeNarrativeGenerator().generate_entry(self._entry_signal(), "BTC/USDT")

        TradeLogger().log_entry(self._entry_signal(), self._fill_result(), risk_amount=100.0, narrative=narrative)

        record = TradeRecord.objects.get(symbol="BTC/USDT", status="OPEN")
        self.assertEqual(record.research_verdict, narrative.research_verdict)
        self.assertEqual(record.production_eligible, narrative.production_eligible)
        self.assertEqual(record.verdict_rejection_reason, narrative.verdict_rejection_reason)
        self.assertEqual(record.entry_narrative, narrative.entry_narrative)
        self.assertNotEqual(record.entry_narrative, "")

    def test_log_exit_completes_narrative_with_real_outcome(self):
        signal = self._entry_signal()
        narrative = TradeNarrativeGenerator().generate_entry(signal, "BTC/USDT")
        TradeLogger().log_entry(signal, self._fill_result(price=50010.0), risk_amount=100.0, narrative=narrative)

        TradeLogger().log_exit(
            symbol="BTC/USDT",
            exit_result=self._fill_result(price=51000.0),
            risk_amount=100.0,
            reason="take_profit",
            narrative=narrative,
        )

        record = TradeRecord.objects.get(symbol="BTC/USDT")
        self.assertEqual(record.status, "WIN")
        self.assertNotEqual(record.exit_narrative, "")
        # exit_narrative should reflect the actual computed outcome, not a
        # placeholder -- net_pnl was positive, so the WIN outcome and the
        # exit_reason we passed should both appear in the generated text
        # (generate_exit() maps "take_profit" -> "Take profit target...").
        self.assertIn("WIN", record.exit_narrative)
        self.assertIn("take profit", record.exit_narrative.lower())
        # research_verdict etc. set at entry time must survive untouched.
        self.assertEqual(record.research_verdict, narrative.research_verdict)
        self.assertEqual(record.production_eligible, narrative.production_eligible)

    def test_log_entry_without_narrative_keeps_field_defaults(self):
        TradeLogger().log_entry(self._entry_signal(), self._fill_result(), risk_amount=100.0)

        record = TradeRecord.objects.get(symbol="BTC/USDT", status="OPEN")
        self.assertEqual(record.research_verdict, "")
        self.assertFalse(record.production_eligible)
        self.assertEqual(record.entry_narrative, "")

    def test_log_exit_without_narrative_leaves_exit_narrative_blank(self):
        TradeLogger().log_entry(self._entry_signal(), self._fill_result(), risk_amount=100.0)
        TradeLogger().log_exit(
            symbol="BTC/USDT", exit_result=self._fill_result(price=51000.0),
            risk_amount=100.0, reason="take_profit",
        )

        record = TradeRecord.objects.get(symbol="BTC/USDT")
        self.assertEqual(record.exit_narrative, "")
        self.assertEqual(record.status, "WIN")   # PnL calc itself unaffected


# claude code changed: new class — Kraken Multi-Venue Execution, Step 15.
# Proves TradeRecord.venue and the (symbol, venue) unique-OPEN constraint
# fix actually work — the real gap Step 10's ExecutionCoordinator exposed.
class TradeRecordVenueTest(TestCase):

    def _entry_signal(self, symbol="BTC/USDT"):
        return {
            "signal": "BUY", "symbol": symbol, "entry": 50000.0,
            "sl": 49000.0, "tp": 53000.0, "rsi": 55.0,
            "reason": "ema_buy_setup", "strategy": "MovingAverageStrategy",
        }

    def _fill_result(self, price=50010.0):
        return {
            "fill_price": price, "filled_qty": 0.1, "fee_usdt": 5.0,
            "order_id": "abc123", "slippage_pct": 0.02,
        }

    def test_venue_omitted_defaults_to_binance(self):
        TradeLogger().log_entry(self._entry_signal(), self._fill_result(), risk_amount=100.0)

        record = TradeRecord.objects.get(symbol="BTC/USDT", status="OPEN")
        self.assertEqual(record.venue, "binance")

    def test_explicit_venue_is_persisted(self):
        TradeLogger().log_entry(
            self._entry_signal(), self._fill_result(), risk_amount=100.0, venue="kraken",
        )

        record = TradeRecord.objects.get(symbol="BTC/USDT", status="OPEN")
        self.assertEqual(record.venue, "kraken")

    def test_same_symbol_open_on_two_different_venues_is_allowed(self):
        # claude code changed: THE regression test for the constraint fix —
        # this would have raised IntegrityError before Step 15.
        TradeLogger().log_entry(
            self._entry_signal(), self._fill_result(), risk_amount=100.0, venue="binance",
        )
        TradeLogger().log_entry(
            self._entry_signal(), self._fill_result(), risk_amount=100.0, venue="kraken",
        )

        open_records = TradeRecord.objects.filter(symbol="BTC/USDT", status="OPEN")
        self.assertEqual(open_records.count(), 2)
        self.assertEqual(
            set(open_records.values_list("venue", flat=True)), {"binance", "kraken"},
        )

    def test_true_duplicate_same_symbol_same_venue_still_blocked(self):
        # The original bug protection must survive the constraint change.
        from django.db import IntegrityError, transaction

        TradeLogger().log_entry(
            self._entry_signal(), self._fill_result(), risk_amount=100.0, venue="binance",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TradeRecord.objects.create(
                    symbol="BTC/USDT", side="BUY", venue="binance",
                    entry_price=50000.0, sl=49000.0, tp=53000.0, quantity=0.1,
                    order_id="dupe", status="OPEN",
                )

    def test_log_exit_scoped_by_venue_updates_the_correct_record(self):
        TradeLogger().log_entry(
            self._entry_signal(), self._fill_result(price=50000.0), risk_amount=100.0, venue="binance",
        )
        TradeLogger().log_entry(
            self._entry_signal(), self._fill_result(price=50000.0), risk_amount=100.0, venue="kraken",
        )

        # Close only the Kraken leg.
        TradeLogger().log_exit(
            symbol="BTC/USDT", exit_result=self._fill_result(price=51000.0),
            risk_amount=100.0, reason="take_profit", venue="kraken",
        )

        binance_record = TradeRecord.objects.get(symbol="BTC/USDT", venue="binance")
        kraken_record = TradeRecord.objects.get(symbol="BTC/USDT", venue="kraken")
        self.assertEqual(binance_record.status, "OPEN")   # untouched
        self.assertEqual(kraken_record.status, "WIN")     # closed
