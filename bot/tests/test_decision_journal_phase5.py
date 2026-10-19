# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/test_decision_journal_phase5.py
# claude code changed: new file — 2026-10-07, Decision Journal Phase 5 (human annotations). Staff-only writes and
# reads, CSRF, per-kind validation, the hindsight flag, insert-only, and separation from machine data (exports,
# analytics, discovery never see human text).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import ast
import gzip
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

import pandas as pd
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from bot.journal.annotations import AnnotationRejected, add_annotation, written_after_outcome
from bot.journal.models import DecisionEvent, HumanAnnotation, JournalRecordIsImmutableError, JournalRun, TradeRecord

SECRET = "my-private-note-7f3a"


class AnnotationTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("staffer", password="pw", is_staff=True)
        cls.user = User.objects.create_user("visitor", password="pw")
        cls.jrun = JournalRun.objects.create(
            run_id="BT-ANN", environment="BACKTEST", execution_mode="RESEARCH", symbol="BTC_USDT", asset_class="CRYPTO",
            venue="binance", timeframe="1h", status="CLOSED", started_at=timezone.now(), methodology_version="m",
            config_hash="c", data_version="d", zones_version="z", zones_hash="h", strategy_set="s", strategy_version="v",
            taxonomy_version="QBP-JRNL-REASON-1", schema_version="s", gate_order_version="g", feature_set_version="f",
            coverage_version="c", evidence_status="e")
        t = pd.Timestamp("2022-06-01", tz="UTC")
        cls.dec = DecisionEvent.objects.create(
            decision_id=uuid.uuid4(), run=cls.jrun, environment="BACKTEST", execution_mode="RESEARCH", symbol="BTC_USDT",
            asset_class="CRYPTO", venue="binance", timeframe="1h", candle_index=100, candle_open_time=t,
            information_time=t + pd.Timedelta(hours=1), research_zone="SELECTION", zones_hash="h", strategy="s",
            strategy_version="v", status="NO_SIGNAL", reason_code="NO_PULLBACK", reason_stage="STRATEGY",
            gate_order_version="g", methodology_version="m", config_hash="c", data_version="d", taxonomy_version="t",
            lifecycle_stages=["EVALUATED"])
        cls.url = f"/research/journal/{cls.jrun.run_id}/{cls.dec.decision_id}/"

    def test_staff_can_write_and_read(self):
        c = Client(); c.login(username="staffer", password="pw")
        r = c.post(self.url + "annotate/", {"kind": "NOTE", "text": SECRET})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(HumanAnnotation.objects.get().text, SECRET)
        self.assertIn(SECRET, c.get(self.url).content.decode())

    def test_anonymous_and_non_staff_can_neither_write_nor_read(self):
        add_annotation(self.staff, kind="NOTE", text=SECRET, decision=self.dec)
        anon, visitor = Client(), Client(); visitor.login(username="visitor", password="pw")
        for c in (anon, visitor):
            self.assertEqual(c.post(self.url + "annotate/", {"kind": "NOTE", "text": "x"}).status_code, 403)
            self.assertNotIn(SECRET, c.get(self.url).content.decode())
        self.assertEqual(HumanAnnotation.objects.count(), 1)
        with self.assertRaises(AnnotationRejected):
            add_annotation(self.user, kind="NOTE", text="x", decision=self.dec)

    def test_csrf_is_enforced(self):
        c = Client(enforce_csrf_checks=True); c.login(username="staffer", password="pw")
        self.assertEqual(c.post(self.url + "annotate/", {"kind": "NOTE", "text": "x"}).status_code, 403)
        self.assertEqual(c.get(self.url + "annotate/").status_code, 405)

    def test_validation_per_kind_and_single_target(self):
        for kw in ({"kind": "CONFIDENCE", "value": {"confidence": 9}}, {"kind": "CONFIDENCE", "value": {"confidence": True}},
                   {"kind": "TAG", "text": "Not A Slug!"}, {"kind": "CHECKLIST", "value": {"items": {"a": "yes"}}},
                   {"kind": "NOTE", "text": "   "}, {"kind": "SCREENSHOT", "text": "x"}):
            with self.assertRaises(AnnotationRejected, msg=kw):
                add_annotation(self.staff, decision=self.dec, **kw)
        ok = add_annotation(self.staff, kind="TAG", text="Late-Entry", decision=self.dec)
        self.assertEqual(ok.value, {"tag": "late-entry"})
        with self.assertRaises(AnnotationRejected):
            add_annotation(self.staff, kind="NOTE", text="x")

    def test_hindsight_flag(self):
        self.assertTrue(written_after_outcome(decision=self.dec))          # the decision is from 2022
        self.assertFalse(written_after_outcome(decision=self.dec, now=self.dec.information_time + timedelta(hours=2)))
        self.assertTrue(add_annotation(self.staff, kind="LESSON", text="late", decision=self.dec).written_after_outcome)

    def test_insert_only_and_revisions(self):
        a = add_annotation(self.staff, kind="NOTE", text="v1", decision=self.dec)
        a.text = "edited"
        with self.assertRaises(JournalRecordIsImmutableError):
            a.save()
        b = add_annotation(self.staff, kind="NOTE", text="v2", decision=self.dec, supersedes=a)
        self.assertEqual(b.supersedes_id, a.pk)
        self.assertEqual(HumanAnnotation.objects.get(pk=a.pk).text, "v1")

    def test_human_text_never_reaches_exports_or_analytics(self):
        from bot.journal.decision_export import export_run
        add_annotation(self.staff, kind="NOTE", text=SECRET, decision=self.dec)
        with tempfile.TemporaryDirectory() as d:
            path = export_run(self.jrun, d)
            self.assertNotIn(SECRET, gzip.decompress(path.read_bytes()).decode())
        for f in ("bot/journal/analytics.py", "bot/journal/labeler.py", "bot/journal/decision_export.py",
                  "bot/journal/decision_recorder.py"):
            tree = ast.parse(Path(f).read_text(encoding="utf-8"))
            names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
            mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
            self.assertNotIn("HumanAnnotation", names, f)
            self.assertNotIn("bot.journal.annotations", mods, f)
