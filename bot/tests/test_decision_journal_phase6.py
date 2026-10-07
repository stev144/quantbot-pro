# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/test_decision_journal_phase6.py
# claude code changed: new file — 2026-10-07, Decision Journal Phase 6 (DRY_RUN live recorder). Owner: "First prove
# recorder-ON vs recorder-OFF equivalence. Any behavioral difference is a blocker." The REAL bot_runner
# process_venue_candle + REAL ExecutionEngine (DRY_RUN) are driven offline over replayed local candles
# (bot/tests/journal_replay.py) — identical inputs, recorder off vs on, everything the bot does compared.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import json
import os
import uuid
from pathlib import Path
from unittest import mock, skipUnless

from django.test import TestCase

DATA = Path("data/BTC_USDT_1h.csv")
STEPS, START = 400, 30000


def make_session(start=START):
    from bot.core.bot_runner import VenueSession
    from bot.engines.execution_engine import ExecutionEngine
    from bot.engines.regime_detector import RegimeDetector
    from bot.engines.strategy_router import StrategyRouter
    from bot.engines.trade_narrative import TradeNarrativeGenerator
    from bot.tests.journal_replay import ReplayExchange
    ex = ReplayExchange(str(DATA), start_row=start)
    eng = ExecutionEngine(ex, dry_run=True)
    eng.position_tracker.is_reconciled = True
    return ex, VenueSession(venue_id="binance", exchange=ex, symbol="BTC/USDT", engine=eng, detector=RegimeDetector(),
                            router=StrategyRouter(allow_unvalidated_strategies=True), narrator=TradeNarrativeGenerator())


def drive(recorder_cls=None, steps=STEPS):
    """Run the live loop; return everything the BOT did (never anything the recorder wrote)."""
    from bot.core.bot_runner import process_venue_candle
    from bot.journal.models import TradeRecord
    ex, s = make_session()
    rec = recorder_cls(s.engine, symbol=s.symbol, venue=s.venue_id).attach(s) if recorder_cls else None
    trace = []
    for _ in range(steps):
        trace.append((process_venue_candle(s), s.last_signal, s.last_regime))
        ex.advance()
    if rec:
        rec.close()
    fields = ("symbol", "side", "entry_price", "sl", "tp", "quantity", "fee_entry", "exit_price", "fee_exit",
              "exit_reason", "gross_pnl", "net_pnl", "r_multiple", "status", "execution_mode", "venue")
    trades = [tuple(getattr(t, f) for f in fields) for t in TradeRecord.objects.order_by("id")]
    e = s.engine
    state = {"positions": json.dumps(e.position_tracker.get_all_positions(), sort_keys=True, default=str),
             "slippage": (e.total_slippage_pct, e.slippage_count), "dd": (e.drawdown_guard.tripped, e.drawdown_guard.peak_balance)}
    return trace, trades, state, rec


@skipUnless(DATA.exists(), "data/BTC_USDT_1h.csv not present")
class EquivalenceTests(TestCase):
    """THE blocker test: the recorder must not change a single thing the bot does."""

    def test_recorder_on_and_off_are_identical(self):
        from bot.journal.live_recorder import LiveDecisionRecorder
        from bot.journal.models import DecisionEvent, TradeRecord
        off = drive(None)
        self.assertGreater(len(off[1]), 3)                                   # real dry-run trades happened
        TradeRecord.objects.all().delete()                                  # same starting state for the second run
        on = drive(LiveDecisionRecorder)
        self.assertEqual(on[0], off[0])                                     # every status, signal and regime, step by step
        self.assertEqual(on[1], off[1])                                     # every trade: prices, sizes, fees, P&L, exits
        self.assertEqual(on[2], off[2])                                     # final positions, slippage stats, drawdown guard
        self.assertTrue(DecisionEvent.objects.filter(run=on[3].run).exists())

    def test_the_comparison_detects_a_recorder_that_interferes(self):
        """Negative control: a recorder that touches the engine changes the bot, and the same comparison sees it."""
        from bot.journal.live_recorder import LiveDecisionRecorder
        from bot.journal.models import TradeRecord

        class Meddling(LiveDecisionRecorder):
            def on_evaluation(self, *a):
                self.engine.position_tracker.is_reconciled = False         # blocks every entry
                return super().on_evaluation(*a)
        off = drive(None)
        TradeRecord.objects.all().delete()
        bad = drive(Meddling)
        self.assertNotEqual(bad[1], off[1])

    def test_the_recorder_only_ever_receives_copies(self):
        """A recorder that scribbles on the signal/frame it is given must not reach the bot: bot_runner passes
        copies (dict(signal), df.copy()). Without the copies this test fails."""
        from bot.journal.live_recorder import LiveDecisionRecorder
        from bot.journal.models import TradeRecord

        class Scribbler(LiveDecisionRecorder):
            def on_evaluation(self, df, regime, signal, in_position, price_checked):
                if signal.get("signal") in ("BUY", "SELL"):
                    signal["sl"] = float(signal["sl"]) * 0.9; signal["tp"] = float(signal["tp"]) * 1.1
                df.iloc[-1, df.columns.get_loc("close")] *= 2
                return super().on_evaluation(df, regime, signal, in_position, price_checked)
        off = drive(None)
        TradeRecord.objects.all().delete()
        on = drive(Scribbler)
        self.assertEqual((on[0], on[1], on[2]), (off[0], off[1], off[2]))

    def test_a_crashing_recorder_cannot_change_the_bot(self):
        from bot.journal.live_recorder import LiveDecisionRecorder
        from bot.journal.models import JournalGap, TradeRecord

        class Exploding(LiveDecisionRecorder):
            n = 0
            def on_evaluation(self, *a):
                Exploding.n += 1
                if Exploding.n == 50:
                    raise RuntimeError("simulated journal crash")
                return super().on_evaluation(*a)
        off = drive(None)
        TradeRecord.objects.all().delete()
        on = drive(Exploding)
        self.assertEqual((on[0], on[1], on[2]), (off[0], off[1], off[2]))
        self.assertEqual(on[3].run.status, "INCOMPLETE")
        self.assertTrue(JournalGap.objects.filter(run=on[3].run, kind="RECORDER_FAILURE").exists())


@skipUnless(DATA.exists(), "data/BTC_USDT_1h.csv not present")
class LiveRecordingTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        from bot.journal.live_recorder import LiveDecisionRecorder
        cls.trace, cls.trades, cls.state, cls.rec = drive(LiveDecisionRecorder)

    def test_run_identity_and_vocabulary(self):
        from bot.journal import taxonomy as T
        r = self.rec.run
        self.assertEqual((r.environment, r.execution_mode, r.taxonomy_version, r.gate_order_version, r.status),
                         ("DRY_RUN", "DRY_RUN", T.LIVE_TAXONOMY_VERSION, T.LIVE_GATE_ORDER_VERSION, "CLOSED"))

    def test_every_executed_decision_links_one_trade_and_was_written_ahead(self):
        from bot.journal.models import DecisionAttempt, DecisionEvent, TradeRecord
        ex = DecisionEvent.objects.filter(run=self.rec.run, status="EXECUTED")
        self.assertEqual(ex.count(), TradeRecord.objects.count())
        for d in ex:
            self.assertEqual(TradeRecord.objects.filter(decision_id=d.decision_id).count(), 1)
            a = DecisionAttempt.objects.get(decision_id=d.decision_id)          # write-ahead row exists ...
            self.assertLessEqual(a.attempted_at, d.recorded_at)                # ... and came first
            self.assertEqual(d.lifecycle_stages, ["EVALUATED", "SIGNAL_GENERATED", "GATES_EVALUATED", "EXECUTION_ATTEMPTED"])
            self.assertTrue(d.execution_eligible)

    def test_decisions_only_on_flat_candles_and_in_position_signals_are_marked(self):
        from bot.journal.models import DecisionEvent
        decs = DecisionEvent.objects.filter(run=self.rec.run)
        cov = self.rec.run.coverage["counts"]
        self.assertEqual(decs.count(), cov["EVALUATED"])
        self.assertEqual(decs.count() + cov["NOT_EVALUATED_IN_POSITION"] + cov["WARMUP"], STEPS)   # every hour accounted for
        for d in decs.filter(reason_code__in=["DUPLICATE_SIGNAL", "POSITION_ALREADY_OPEN"]):
            self.assertFalse(d.execution_eligible)
        self.assertTrue(all(d.snapshot.snapshot_kind == "POINT_IN_TIME_LIVE" for d in decs[:50]))
        self.assertTrue(all(d.information_time - d.candle_open_time == __import__("datetime").timedelta(hours=1) for d in decs))

    def test_live_labels_are_refused_as_sealed(self):
        from bot.journal.labeler import LabelerRefused, label_run
        with self.assertRaises(LabelerRefused):
            label_run(self.rec.run)


class RecorderRulesTests(TestCase):

    def test_refuses_a_non_dry_run_engine(self):
        from types import SimpleNamespace
        from bot.journal.live_recorder import LiveDecisionRecorder, RecorderRefused
        with self.assertRaises(RecorderRefused):
            LiveDecisionRecorder(SimpleNamespace(dry_run=False), symbol="BTC/USDT", venue="binance")

    @skipUnless(DATA.exists(), "data/BTC_USDT_1h.csv not present")
    def test_missed_candles_become_gaps_and_repeats_are_not_new_points(self):
        from bot.core.bot_runner import process_venue_candle
        from bot.journal.live_recorder import LiveDecisionRecorder
        from bot.journal.models import DecisionEvent, JournalGap
        ex, s = make_session()
        rec = LiveDecisionRecorder(s.engine, symbol=s.symbol, venue=s.venue_id).attach(s)
        process_venue_candle(s)
        process_venue_candle(s)                                              # same candle again (e.g. 60 s retry)
        ex.advance(3)                                                        # two hours never evaluated
        process_venue_candle(s)
        rec.close()
        self.assertEqual(rec.coverage["REEVALUATED_SAME_CANDLE"], 1)
        g = JournalGap.objects.get(run=rec.run, kind="EVALUATION_NOT_PERFORMED")
        self.assertEqual(g.detail["n_candles"], 2)
        self.assertLessEqual(DecisionEvent.objects.filter(run=rec.run).count(), 2)
        self.assertEqual(rec.run.status, "CLOSED")                           # skipped hours are visible, not defects

    def test_orphaned_attempt_marks_the_old_run_incomplete(self):
        from types import SimpleNamespace
        from django.utils import timezone
        from bot.journal.live_recorder import LiveDecisionRecorder
        from bot.journal.models import DecisionAttempt, JournalGap, JournalRun
        eng = SimpleNamespace(dry_run=True, position_sizer=SimpleNamespace(risk_pct=0.01), journal_hook=None)
        first = LiveDecisionRecorder(eng, symbol="BTC/USDT", venue="binance")
        DecisionAttempt.objects.create(decision_id=uuid.uuid4(), run=first.run, symbol="BTC/USDT", candle_open_time=timezone.now())
        LiveDecisionRecorder(eng, symbol="BTC/USDT", venue="binance")       # the next start (process crashed)
        old = JournalRun.objects.get(pk=first.run.pk)
        self.assertEqual(old.status, "INCOMPLETE")
        self.assertIn("ORPHANED_ATTEMPT", old.incomplete_reasons)
        self.assertTrue(JournalGap.objects.filter(run=old, kind="ORPHANED_ATTEMPT").exists())

    def test_attach_is_off_by_default_and_never_on_live(self):
        from types import SimpleNamespace
        from bot.core.bot_runner import attach_journal_recorders
        sess = SimpleNamespace(engine=SimpleNamespace(dry_run=True, position_sizer=SimpleNamespace(), journal_hook=None),
                               symbol="BTC/USDT", venue_id="binance", journal=None)
        live = SimpleNamespace(engine=SimpleNamespace(dry_run=False), symbol="ETH/USDT", venue_id="binance", journal=None)
        with mock.patch.dict(os.environ, {"JOURNAL_LIVE_RECORDER": ""}):
            attach_journal_recorders([sess])
        self.assertIsNone(sess.journal)
        with mock.patch.dict(os.environ, {"JOURNAL_LIVE_RECORDER": "1"}):
            attach_journal_recorders([sess, live])
        self.assertIsNotNone(sess.journal)
        self.assertIsNone(live.journal)


class RecorderOffPathTests(TestCase):
    """claude code changed: 2026-10-07 Phase 6 — the full regression found the hooks read engine state
    (has_position / open_positions / _last_refusal) even with NO recorder attached, turning 'ok' into 'error' for a
    minimal engine (test_phase3_engineering FormingCandleTests). With the recorder off the runner must touch exactly
    what it touched before Phase 6 — pinned here on the BUY path, which that Phase 3 test (NO_SIGNAL) does not reach."""

    def test_recorder_off_reads_nothing_new_from_the_engine(self):
        from types import SimpleNamespace
        import ccxt
        from bot.core import bot_runner
        hour = 3_600_000
        t0 = 1_700_000_000_000 - (1_700_000_000_000 % hour)
        candles = [[t0 + i * hour, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0] for i in range(150)]
        now = candles[-1][0] + hour // 3
        executed = []

        class Exch:
            id = "stub"
            parse_timeframe = staticmethod(ccxt.Exchange.parse_timeframe)
            def fetch_ohlcv(self, symbol, timeframe, limit):
                return [list(c) for c in candles]
            def milliseconds(self):
                return now

        signal = {"signal": "BUY", "entry": 249.0, "sl": 245.0, "tp": 257.0, "rsi": 40.0, "reason": "t", "strategy": "S"}
        # Only what the pre-Phase-6 runner used: no _last_refusal, no has_position, no open_positions, no journal_hook.
        engine = SimpleNamespace(dry_run=True, manage_positions=lambda: None,
                                 execute_signal=lambda s: executed.append(dict(s)),
                                 position_tracker=SimpleNamespace(get_all_positions=lambda: {}))
        session = bot_runner.VenueSession(
            venue_id="stub", exchange=Exch(), symbol="X/USDT", engine=engine,
            detector=SimpleNamespace(detect=lambda df: SimpleNamespace(regime="TRENDING_UP", confidence="HIGH", adx=30.0,
                                                                       atr_ratio=1.0)),
            router=SimpleNamespace(route=lambda df, regime: dict(signal, regime="TRENDING_UP", regime_confidence="HIGH")),
            narrator=SimpleNamespace(generate_entry=lambda s, sym: SimpleNamespace(entry_narrative="n")))
        self.assertIsNone(session.journal)
        self.assertEqual(bot_runner.process_venue_candle(session), "ok")
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0]["signal"], "BUY")
