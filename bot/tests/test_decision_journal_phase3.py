# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/test_decision_journal_phase3.py
# claude code changed: new file — 2026-10-07, Decision Journal Phase 3 (legacy backfill). Legacy rows are clearly
# limited (no snapshot, NOT_RECORDED provenance, incomplete population), pair legs are ONE decision, nothing is
# invented (unknown modes skipped), old rows only gain their own empty link, and the run is idempotent.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import io
import uuid

import pandas as pd
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from bot.journal import taxonomy as T
from bot.journal.legacy_backfill import backfill
from bot.journal.models import (DecisionEvent, ExecutionIncident, JournalRun, MarketSnapshot, SignalRejection,
                                TradeRecord)


def trade(**kw):
    base = dict(symbol="BTC/USDT", side="BUY", entry_price=100.0, sl=98.0, tp=104.0, quantity=1.0, order_id=str(uuid.uuid4()),
                execution_mode="DRY_RUN", status="WIN", strategy="MovingAverageStrategy")
    base.update(kw)
    return TradeRecord.objects.create(**base)


class LegacyBackfillTests(TestCase):

    def setUp(self):
        pid = uuid.uuid4()
        self.leg_a = trade(symbol="DODO/USDT", side="SELL", pair_trade_id=pid, leg="A", strategy="PairsTrading_DODO_USDT/FIDA_USDT")
        self.leg_b = trade(symbol="FIDA/USDT", side="BUY", pair_trade_id=pid, leg="B", strategy="PairsTrading_DODO_USDT/FIDA_USDT")
        self.single = trade(execution_mode="LIVE")
        self.unknown = trade(execution_mode="UNKNOWN")
        self.inc = ExecutionIncident.objects.create(pair_name="DODO/FIDA", kind="LEG_FAILURE", state="UNWOUND_CONFIRMED",
                                                    execution_mode="DRY_RUN")
        SignalRejection.objects.create(pair_name="X/Y", decision_timestamp=pd.Timestamp("2026-09-01", tz="UTC"),
                                       intended_direction="LONG_SPREAD", zscore=-2.1, held_spread=0.1, alpha_e=0.0,
                                       beta_e=1.0, price_a=1.0, price_b=1.0, reason="ENTRY_SIGN_MISMATCH",
                                       methodology_version="m", protocol_hash="p")

    def test_dry_run_writes_nothing(self):
        out = backfill(commit=False)
        self.assertFalse(out["committed"])
        self.assertEqual(out["decisions"], 3)                               # pair (1) + single LIVE (1) + incident (1)
        self.assertFalse(JournalRun.objects.exists())
        self.assertIsNone(TradeRecord.objects.get(pk=self.leg_a.pk).decision_id)

    def test_commit_groups_pair_legs_and_limits_every_legacy_row(self):
        out = backfill(commit=True)
        self.assertEqual(out["decisions"], 3)
        a, b = TradeRecord.objects.get(pk=self.leg_a.pk), TradeRecord.objects.get(pk=self.leg_b.pk)
        self.assertIsNotNone(a.decision_id)
        self.assertEqual(a.decision_id, b.decision_id)                      # one pair trade = one decision
        d = DecisionEvent.objects.get(pk=a.decision_id)
        self.assertEqual((d.status, d.terminal_source, d.taxonomy_version, d.zone_basis, d.data_version, d.candle_index),
                         ("EXECUTED", "LEGACY_BACKFILL", T.LEGACY_TAXONOMY_VERSION, "LEGACY_RECORDED_TIME", "NOT_RECORDED", -1))
        self.assertEqual(sorted(d.reason_detail["trade_record_pks"]), sorted([a.pk, b.pk]))
        self.assertFalse(MarketSnapshot.objects.filter(decision__terminal_source="LEGACY_BACKFILL").exists())   # nothing reconstructed
        for run in JournalRun.objects.all():
            self.assertEqual(run.coverage["population"], "LEGACY_INCOMPLETE_POPULATION")
            self.assertIn("never evidence", run.evidence_status)
            self.assertEqual(run.environment, run.execution_mode)          # environment from the recorded mode only
        inc_d = DecisionEvent.objects.get(pk=ExecutionIncident.objects.get(pk=self.inc.pk).decision_id)
        self.assertEqual((inc_d.status, inc_d.reason_code, inc_d.reason_detail["kind"]),
                         ("EXECUTION_FAILED", "LEGACY_EXECUTION_INCIDENT", "LEG_FAILURE"))

    def test_unknown_environments_are_skipped_never_invented(self):
        out = backfill(commit=True)
        reasons = {(s["source"], s["pk"]) for s in out["skipped"]}
        self.assertIn(("TradeRecord", self.unknown.pk), reasons)
        self.assertIn("SignalRejection", {s["source"] for s in out["skipped"]})
        self.assertIsNone(TradeRecord.objects.get(pk=self.unknown.pk).decision_id)
        self.assertFalse(SignalRejection.objects.exclude(decision_id=None).exists())

    def test_idempotent_and_old_rows_otherwise_untouched(self):
        before = {t.pk: (t.entry_price, t.net_pnl, t.status, t.updated_at) for t in TradeRecord.objects.all()}
        backfill(commit=True)
        second = backfill(commit=True)
        self.assertEqual(second["decisions"], 0)                            # nothing left to link
        self.assertEqual(JournalRun.objects.count(), 2)                     # DRY_RUN + LIVE, created once
        after = {t.pk: (t.entry_price, t.net_pnl, t.status) for t in TradeRecord.objects.all()}
        self.assertEqual({k: v[:3] for k, v in before.items()}, after)      # only decision_id changed

    def test_command_dry_run_reports(self):
        out = io.StringIO()
        call_command("backfill_journal_legacy", stdout=out)
        self.assertIn("DRY RUN", out.getvalue())
        self.assertIn("skipped TradeRecord", out.getvalue())

    def test_relaxed_constraints_still_bind_non_legacy_rows(self):
        backfill(commit=True)
        d = DecisionEvent.objects.filter(terminal_source="LEGACY_BACKFILL", status="EXECUTED").first()
        fields = {f.attname: getattr(d, f.attname) for f in DecisionEvent._meta.concrete_fields}
        with self.assertRaises(IntegrityError), transaction.atomic():      # equal times are only allowed for legacy
            DecisionEvent.objects.create(**{**fields, "decision_id": uuid.uuid4(), "terminal_source": "ENGINE"})
        with self.assertRaises(IntegrityError), transaction.atomic():
            DecisionEvent.objects.create(**{**fields, "decision_id": uuid.uuid4(), "terminal_source": "MADE_UP"})

    def test_legacy_vocabulary_is_separate(self):
        T.validate_legacy_terminal("EXECUTION_FAILED", "LEGACY_EXECUTION_INCIDENT", "EXECUTION")
        with self.assertRaises(ValueError):
            T.validate_legacy_terminal("EXECUTION_FAILED", "NO_PULLBACK", "STRATEGY")
        self.assertNotIn("LEGACY_EXECUTION_INCIDENT", T.REASON_CODES)       # never enters the backtest taxonomy

    def test_legacy_run_page_carries_the_incomplete_population_notice(self):
        backfill(commit=True)
        run = JournalRun.objects.first()
        html = self.client.get(f"/research/journal/{run.run_id}/").content.decode()
        self.assertIn("LEGACY — incomplete population", html)
