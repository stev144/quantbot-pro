# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/test_decision_journal_ui.py
# claude code changed: new file — 2026-10-07, Decision Journal UI (read-only pages + record_journal_backtest).
# A real recorded run (local data, no network) is rendered and every number on the pages is checked against
# the database, so the UI cannot drift from what was recorded.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import io
import re
import uuid
from pathlib import Path
from unittest import skipUnless

import pandas as pd
from django.core.management import call_command
from django.test import TestCase

from bot.journal.models import DecisionEvent, JournalRun

DATA = Path("data/BTC_USDT_1h.csv")
BANNER = "Research record, not evidence."


class EmptyJournalTests(TestCase):

    def test_list_page_renders_with_no_runs_and_explains_how_to_record(self):
        r = self.client.get("/research/journal/")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("No recorded runs yet", html)
        self.assertIn("record_journal_backtest", html)
        self.assertIn(BANNER, html)

    def test_unknown_run_and_bad_ids_are_404(self):
        self.assertEqual(self.client.get("/research/journal/NOPE/").status_code, 404)
        self.assertEqual(self.client.get(f"/research/journal/NOPE/{uuid.uuid4()}/").status_code, 404)
        self.assertEqual(self.client.get("/research/journal/NOPE/not-a-uuid/").status_code, 404)

    def test_read_only(self):
        self.assertEqual(self.client.post("/research/journal/").status_code, 405)

    def test_navbar_links_the_journal_under_research(self):
        html = self.client.get("/research/journal/").content.decode()
        nav = html.split('class="term-nav"', 1)[1].split("</nav>", 1)[0]
        self.assertIn('href="/research/journal/">Decision Journal</a>', nav.replace('class="active" ', ""))
        self.assertRegex(nav, r'class="term-nav-link active">Research</summary>')   # section highlighted on its page

    def test_incomplete_run_is_flagged(self):
        JournalRun.objects.create(run_id="BT-INCOMPLETE", environment="BACKTEST", execution_mode="RESEARCH",
                                  symbol="X", asset_class="CRYPTO", venue="binance", timeframe="1h", status="INCOMPLETE",
                                  incomplete_reasons=["MAPPING_FAILURE"], started_at=pd.Timestamp("2026-01-01", tz="UTC"),
                                  methodology_version="m", config_hash="c", data_version="d", zones_version="z",
                                  zones_hash="h", strategy_set="s", strategy_version="v", taxonomy_version="t",
                                  schema_version="s", gate_order_version="g", feature_set_version="f",
                                  coverage_version="c", evidence_status="e")
        html = self.client.get("/research/journal/BT-INCOMPLETE/").content.decode()
        self.assertIn("INCOMPLETE", html)
        self.assertIn("not complete research evidence", html)
        self.assertIn("MAPPING_FAILURE", html)


@skipUnless(DATA.exists(), "data/BTC_USDT_1h.csv not present")
class RecordedRunPagesTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        out = io.StringIO()
        call_command("record_journal_backtest", symbol="BTC_USDT", candles=600, allow_unvalidated=True, stdout=out)
        cls.out = out.getvalue()
        cls.jrun = JournalRun.objects.get()
        cls.decisions = DecisionEvent.objects.filter(run=cls.jrun)

    def test_command_reports_the_run_it_recorded(self):
        self.assertIn(self.jrun.run_id, self.out)
        self.assertIn(f"/research/journal/{self.jrun.run_id}/", self.out)
        self.assertEqual(self.jrun.status, "CLOSED")
        self.assertEqual(self.jrun.n_decisions, self.decisions.count())
        self.assertGreater(self.jrun.n_decisions, 100)

    def test_list_page_shows_the_run(self):
        html = self.client.get("/research/journal/").content.decode()
        self.assertIn(self.jrun.run_id, html)
        self.assertIn(f">{self.jrun.n_decisions}<", html)
        self.assertIn(BANNER, html)

    def test_run_page_numbers_match_the_database(self):
        r = self.client.get(f"/research/journal/{self.jrun.run_id}/")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn(BANNER, html)
        for status in set(self.decisions.values_list("status", flat=True)):
            n = self.decisions.filter(status=status).count()
            self.assertRegex(html, rf"\?status={status}\"><span class=\"badge [a-z]+\">{status}</span></a></td><td class=\"num\">{n}</td>")
        self.assertIn("never counted as NO_SIGNAL", html)
        self.assertIn(f"{min(200, self.decisions.count())}", html)
        self.assertIn(self.jrun.data_version, html)                 # provenance shown in full

    def test_status_filter_and_pagination(self):
        status = self.decisions.values_list("status", flat=True).first()
        html = self.client.get(f"/research/journal/{self.jrun.run_id}/?status={status}").content.decode()
        rows = re.findall(r'<td><span class="badge [a-z]+">([A-Z_]+)</span></td>\s*<td class="mono"', html)
        self.assertTrue(rows)
        self.assertEqual(set(rows), {status})
        self.assertEqual(self.client.get(f"/research/journal/{self.jrun.run_id}/?status=BOGUS").status_code, 404)
        self.assertEqual(self.client.get(f"/research/journal/{self.jrun.run_id}/?page=999").status_code, 200)   # clamps
        self.assertEqual(self.client.get(f"/research/journal/{self.jrun.run_id}/?page=abc").status_code, 200)

    def test_decision_page_shows_the_record_exactly(self):
        d = self.decisions.exclude(status="EXECUTED").order_by("candle_index").first()
        r = self.client.get(f"/research/journal/{self.jrun.run_id}/{d.decision_id}/")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn(BANNER, html)
        self.assertIn(d.status, html)
        self.assertIn(d.reason_code, html)
        fails = [g for g, v in d.gate_vector.items() if v == "FAIL"]
        self.assertEqual(len(fails), 1)
        self.assertRegex(html, rf"{fails[0]}</td><td><span class=\"badge neg\">FAIL</span>")
        self.assertIn("not causal credit", html)
        self.assertIn("candle_close", html)                           # snapshot rendered
        self.assertIn(d.snapshot.input_window_sha256[:16], html)

    def test_executed_decision_shows_its_simulated_trade_link(self):
        d = self.decisions.filter(status="EXECUTED").first()
        if d is None:
            self.skipTest("no fill in this 600-candle slice")
        html = self.client.get(f"/research/journal/{self.jrun.run_id}/{d.decision_id}/").content.decode()
        self.assertIn(d.backtest_trade_id, html)
