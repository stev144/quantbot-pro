# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/test_decision_journal_phase2.py
# claude code changed: new file — 2026-10-07, Decision Journal Phase 2 (outcome labels + post-trade metrics).
# G2 known answers on hand-built paths; G4 causality/gaps/sealing; G5 provenance refusal + insert-only;
# separation from real P&L; zone-S-only display. Offline (local data only).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import ast
import io
import math
from pathlib import Path
from unittest import skipUnless

import numpy as np
import pandas as pd
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from bot.journal import labeler as L
from bot.journal.models import DecisionEvent, DecisionLabel, JournalRecordIsImmutableError, JournalRun, PostTradeMetrics

DATA = Path("data/BTC_USDT_1h.csv")
F, S = 0.001, 0.0005   # fee, slippage (the binance defaults; passed explicitly so the arithmetic is checkable)


def path(rows):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


class RulePathKnownAnswers(SimpleTestCase):
    """Fill = open of the first bar. Long: stop 98, target 104; entry 100 → risk 2, reward 4."""

    def run_long(self, rows, **kw):
        return L.simulate_rule_path(*path(rows), "LONG", 98.0, 104.0, **kw)

    def test_target_hit(self):
        st, _, outs = self.run_long([(100, 101, 99.5, 100.5), (100.5, 104.5, 100, 104)])
        self.assertEqual((st, len(outs), outs[0].exit_reason, outs[0].exit_index, outs[0].exit_price), ("OK", 1, "TARGET", 1, 104.0))

    def test_stop_hit(self):
        st, _, outs = self.run_long([(100, 100.5, 97.5, 98.2)])
        self.assertEqual((outs[0].exit_reason, outs[0].exit_index, outs[0].exit_price), ("STOP", 0, 98.0))

    def test_same_bar_both_is_ambiguous_and_stores_both_variants(self):
        st, _, outs = self.run_long([(100, 101, 99, 100), (100, 105, 97, 101)])
        self.assertEqual([(o.exit_reason, o.assumption, o.ambiguity) for o in outs],
                         [("STOP", "STOP_FIRST_ASSUMED", "STOP_AND_TARGET_SAME_BAR"),
                          ("TARGET", "TARGET_FIRST_ASSUMED", "STOP_AND_TARGET_SAME_BAR")])

    def test_fill_bar_both_touched(self):
        _, _, outs = self.run_long([(100, 105, 97, 100)])
        self.assertEqual(outs[0].ambiguity, "FILL_BAR_BOTH_TOUCHED")

    def test_gap_through_stop_exits_at_the_open_not_the_stop(self):
        _, _, outs = self.run_long([(100, 100.5, 99, 99.5), (96, 97, 95, 96)])
        self.assertEqual((outs[0].exit_reason, outs[0].exit_price), ("GAP_STOP", 96.0))

    def test_gap_through_target(self):
        _, _, outs = self.run_long([(100, 100.5, 99, 100.2), (106, 107, 105, 106)])
        self.assertEqual((outs[0].exit_reason, outs[0].exit_price), ("GAP_TARGET", 106.0))

    def test_time_exit_at_the_close_of_the_last_bar(self):
        rows = [(100, 100.5, 99.5, 100.1)] * 3
        _, _, outs = self.run_long(rows, max_hold=3)
        self.assertEqual((outs[0].exit_reason, outs[0].exit_index, outs[0].exit_price), ("TIME", 2, 100.1))

    def test_censored_when_fewer_bars_than_the_hold(self):
        st, why, outs = self.run_long([(100, 100.5, 99.5, 100.1)] * 2, max_hold=3)
        self.assertEqual((st, outs), ("CENSORED", []))

    def test_untestable_cases(self):
        self.assertEqual(L.simulate_rule_path(*path([(97, 98, 96, 97)]), "LONG", 98.0, 104.0)[0], "UNTESTABLE")   # stop not beyond fill
        self.assertEqual(L.simulate_rule_path(*path([(100, 101, 99, np.nan)]), "LONG", 98.0, 104.0)[0], "UNTESTABLE")
        self.assertEqual(L.simulate_rule_path(*path([(100, 101, 99, 100)]), "LONG", float("nan"), 104.0)[0], "UNTESTABLE")
        self.assertEqual(L.simulate_rule_path(*path([(100, 101, 99, 100)]), "", 98.0, 104.0)[0], "UNTESTABLE")

    def test_short_mirror(self):
        _, _, outs = L.simulate_rule_path(*path([(100, 100.5, 99, 99.5), (99.5, 100, 95.5, 96)]), "SHORT", 102.0, 96.0)
        self.assertEqual((outs[0].exit_reason, outs[0].exit_price), ("TARGET", 96.0))


class ArithmeticKnownAnswers(SimpleTestCase):

    def test_long_gross_and_net_r(self):
        gross, net, d = L.rule_r(100.0, 104.0, 98.0, "LONG", F, S, None)
        self.assertAlmostEqual(gross, 2.0)
        e_in, e_out = 100 * (1 + S), 104 * (1 - S)
        self.assertAlmostEqual(net, ((e_out - e_in) - F * (e_in + e_out)) / 2.0, places=12)
        self.assertEqual(d["financing_per_unit"], "NOT_APPLICABLE_LONG_SPOT")

    def test_short_needs_financing_or_net_is_unavailable(self):
        gross, net, _ = L.rule_r(100.0, 96.0, 102.0, "SHORT", F, S, None)
        self.assertAlmostEqual(gross, 2.0)
        self.assertIsNone(net)                                              # never a silent zero financing
        _, net2, _ = L.rule_r(100.0, 96.0, 102.0, "SHORT", F, S, 0.05)
        e_in, e_out = 100 * (1 - S), 96 * (1 + S)
        self.assertAlmostEqual(net2, ((e_in - e_out) - F * (e_in + e_out) - 0.05) / 2.0, places=12)

    def test_excursions_with_and_without_the_exit_bar(self):
        h = [101, 103, 106]; l = [99, 97.5, 100]
        mae, mfe, mae_x, mfe_x = L.excursions_r(h, l, 100.0, 2.0, "LONG", 2)
        self.assertEqual((mae, mfe), ((97.5 - 100) / 2, (106 - 100) / 2))
        self.assertEqual((mae_x, mfe_x), ((97.5 - 100) / 2, (103 - 100) / 2))
        self.assertEqual(L.excursions_r(h, l, 100.0, 2.0, "LONG", 0)[2:], (None, None))

    def test_mfe_is_negative_when_slippage_fills_beyond_the_bar_high(self):
        trade = {"direction": "LONG", "entry_price": 100.05, "sl": 98.0, "entry_index": 0, "exit_index": 0}
        m = L.post_trade_metrics(trade, 100.0, [100.0], [100.02], [99.0])
        self.assertLess(m["mfe_bar_1h_r"], 0)
        self.assertAlmostEqual(m["mfe_bar_1h_r"], (100.02 - 100.05) / 2.05)

    def test_post_trade_metrics(self):
        trade = {"direction": "LONG", "entry_price": 100.05, "sl": 98.0, "entry_index": 1, "exit_index": 2}
        o = [99.0, 100.0, 101.0]; h = [99.5, 101.0, 104.0]; l = [98.5, 99.0, 100.5]
        m = L.post_trade_metrics(trade, 99.8, o, h, l)
        self.assertAlmostEqual(m["implementation_shortfall_bps"], (100.05 - 99.8) / 99.8 * 1e4)
        self.assertAlmostEqual(m["fill_vs_rule_reference_bps"], (100.05 - 100.0) / 100.0 * 1e4)
        self.assertAlmostEqual(m["mae_bar_1h_r"], (99.0 - 100.05) / 2.05)
        self.assertEqual((m["bars_held"], m["bars_to_mae"], m["bars_to_mfe"]), (2, 1, 2))


class SealingKnownAnswers(SimpleTestCase):
    """OD-J4: a window that reaches F is SEALED with no value — even when the prices exist."""

    def test_forward_window_into_f_is_sealed_and_valueless(self):
        from types import SimpleNamespace
        idx = pd.date_range("2026-10-03 20:00", periods=10, freq="h", tz="UTC")
        o = np.linspace(100, 101, 10)
        zones = SimpleNamespace(F=pd.Timestamp("2026-10-04 00:00", tz="UTC"))
        st, why, end, val = L._forward_status(0, 4, o, np.full(10, 0.01), idx, pd.Timedelta("1h"), zones)
        self.assertEqual((st, val), ("SEALED", None))
        st2, _, _, val2 = L._forward_status(0, 1, o, np.full(10, 0.01), idx, pd.Timedelta("1h"), zones)
        self.assertEqual((st2, val2), ("OK", 0.01))                         # window ends 22:00, before F
        st3, _, _, _ = L._forward_status(0, 2, np.r_[o[:2], np.nan, o[3:]], np.full(10, 0.01), idx, pd.Timedelta("1h"), zones)
        self.assertEqual(st3, "UNTESTABLE")                                 # a missing candle inside the window


class ForwardWindowKnownAnswers(SimpleTestCase):
    """The window of an h-candle forward label is open(t+1) .. open(t+1+h): its end, its gap check and its
    censoring all hinge on that last index (an off-by-one here passed the value test — found by mutation P2-8)."""

    def setUp(self):
        from types import SimpleNamespace
        self.idx = pd.date_range("2023-01-01", periods=8, freq="h", tz="UTC")
        self.zones = SimpleNamespace(F=pd.Timestamp("2030-01-01", tz="UTC"))
        self.fwd = np.full(8, 0.02)

    def test_window_end_is_the_open_of_t_plus_1_plus_h(self):
        _, _, end, _ = L._forward_status(0, 4, np.arange(8.0) + 100, self.fwd, self.idx, pd.Timedelta("1h"), self.zones)
        self.assertEqual(pd.Timestamp(end), self.idx[5])

    def test_a_gap_at_the_last_candle_of_the_window_is_untestable(self):
        o = np.arange(8.0) + 100
        o[5] = np.nan                                                     # exactly open(t+1+h) for i=0, h=4
        self.assertEqual(L._forward_status(0, 4, o, self.fwd, self.idx, pd.Timedelta("1h"), self.zones)[0], "UNTESTABLE")

    def test_censored_exactly_when_open_of_t_plus_1_plus_h_does_not_exist(self):
        o = np.arange(8.0) + 100
        self.assertEqual(L._forward_status(2, 4, o, self.fwd, self.idx, pd.Timedelta("1h"), self.zones)[0], "OK")        # needs index 7
        self.assertEqual(L._forward_status(3, 4, o, self.fwd, self.idx, pd.Timedelta("1h"), self.zones)[0], "CENSORED")  # needs index 8


class SeparationTests(SimpleTestCase):

    def test_real_pnl_code_never_imports_labels(self):
        for f in ("bot/views/execution_monitor.py", "bot/engines/analytics_engine.py", "bot/journal/trade_logger.py",
                  "bot/views/portfolio_risk_data.py", "bot/backtesting/backtester.py"):
            src = Path(f).read_text(encoding="utf-8")
            names = {a.name for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
            mods = {n.module for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.module}
            self.assertNotIn("DecisionLabel", names, f)
            self.assertNotIn("bot.journal.labeler", mods, f)

    def test_label_has_no_link_to_trade_records(self):
        rel = {f.related_model.__name__ for f in DecisionLabel._meta.get_fields() if f.is_relation and f.related_model}
        self.assertNotIn("TradeRecord", rel)


@skipUnless(DATA.exists(), "data/BTC_USDT_1h.csv not present")
class LabelledZoneSRunTests(TestCase):
    """A run recorded inside zone S (ends 2023-06-01), then labelled."""

    @classmethod
    def setUpTestData(cls):
        call_command("record_journal_backtest", symbol="BTC_USDT", candles=800, end="2023-06-01",
                     allow_unvalidated=True, stdout=io.StringIO())
        cls.jrun = JournalRun.objects.get()
        out = io.StringIO()
        call_command("label_journal_run", run=cls.jrun.run_id, stdout=out)
        cls.out = out.getvalue()
        cls.labels = DecisionLabel.objects.filter(run=cls.jrun)
        cls.dec = DecisionEvent.objects.filter(run=cls.jrun)

    def test_every_decision_gets_three_forward_labels_and_directional_ones_a_rule_label(self):
        n = self.dec.count()
        self.assertEqual(self.labels.filter(label_type="FORWARD_RETURN").count(), 3 * n)
        directional = self.dec.exclude(direction="").count()
        rule_decisions = self.labels.filter(label_type="COUNTERFACTUAL_RULE").values("decision").distinct().count()
        self.assertEqual(rule_decisions, directional)
        amb = self.labels.filter(label_type="COUNTERFACTUAL_RULE").exclude(ambiguity="NONE")
        for a in amb.filter(variant="BASELINE_STOP_FIRST"):
            self.assertTrue(self.labels.filter(decision=a.decision, variant="BOUND_TARGET_FIRST").exists())

    def test_forward_label_equals_the_d1_timing_contract(self):
        from bot.quant_core.timing import research_forward_return
        grid = L.load_run_grid(self.jrun)
        fwd4 = research_forward_return(grid, 4)
        for lab in self.labels.filter(label_type="FORWARD_RETURN", horizon_candles=4, status="OK")[:200]:
            t = pd.Timestamp(lab.decision.candle_open_time)
            self.assertAlmostEqual(lab.gross_return, float(fwd4.loc[t]), places=12)
            o = grid["open"]
            self.assertAlmostEqual(lab.gross_return, o.iloc[o.index.get_loc(t) + 5] / o.iloc[o.index.get_loc(t) + 1] - 1, places=12)

    def test_causality_window_starts_at_the_decision(self):
        for lab in self.labels:
            self.assertEqual(lab.label_window_start, lab.information_time)
            if lab.fill_time is not None:
                self.assertEqual(lab.fill_time, lab.information_time)          # the open of t+1, never close of t
        rule = self.labels.filter(label_type="COUNTERFACTUAL_RULE", status="OK").first()
        grid = L.load_run_grid(self.jrun)
        self.assertEqual(rule.fill_price, float(grid["open"].loc[pd.Timestamp(rule.information_time)]))

    def test_zone_s_run_labels_are_ok_not_sealed(self):
        self.assertTrue(all(z == "SELECTION" for z in self.dec.values_list("research_zone", flat=True)))
        self.assertFalse(self.labels.filter(status="SEALED").exists())
        self.assertGreater(self.labels.filter(status="OK").count(), 0.9 * self.labels.count())

    def test_relabel_refused_insert_only_and_immutable(self):
        from bot.journal.labeler import LabelerRefused
        with self.assertRaises(LabelerRefused):
            L.label_run(self.jrun)
        lab = self.labels.first()
        lab.gross_return = 1.0
        with self.assertRaises(JournalRecordIsImmutableError):
            lab.save()

    def test_provenance_mismatch_refuses(self):
        from bot.journal.labeler import LabelerRefused
        df = pd.read_csv(DATA, float_precision="round_trip")
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp")
        k = df.index.get_loc(pd.Timestamp(self.jrun.data_version_detail["start"])) + 10
        df.iloc[k, df.columns.get_loc("close")] *= 1.0001                   # one changed price inside the run
        with self.assertRaises(LabelerRefused):
            L.load_run_grid(self.jrun, ohlcv=df)

    def test_post_trade_metrics_for_every_executed_decision(self):
        ex = self.dec.filter(status="EXECUTED")
        self.assertEqual(PostTradeMetrics.objects.filter(run=self.jrun).count(), ex.count())
        # MAE <= 0 always (the fill is at/after the open, so the bar's low/high is at least as adverse); MFE CAN be
        # negative: entry slippage can push the fill beyond the bar's best price (seen on real trades) — so only
        # MAE <= MFE holds, not MFE >= 0.
        self.assertTrue(all(p.status == "OK" and p.mae_bar_1h_r <= 0 and p.mae_bar_1h_r <= p.mfe_bar_1h_r
                            for p in PostTradeMetrics.objects.filter(run=self.jrun)))

    def test_ui_shows_zone_s_values(self):
        d = self.labels.filter(label_type="COUNTERFACTUAL_RULE", status="OK").first().decision
        html = self.client.get(f"/research/journal/{self.jrun.run_id}/{d.decision_id}/").content.decode()
        self.assertIn("hypothetical, NOT executed", html)
        self.assertNotIn("read-guarded", html)


@skipUnless(DATA.exists(), "data/BTC_USDT_1h.csv not present")
class ZoneGuardAndSealTests(TestCase):

    def test_non_zone_s_values_are_hidden_and_windows_into_f_are_sealed(self):
        call_command("record_journal_backtest", symbol="BTC_USDT", candles=300, allow_unvalidated=True, stdout=io.StringIO())
        run = JournalRun.objects.get()
        L.label_run(run)
        last = DecisionEvent.objects.filter(run=run).order_by("-candle_index").first()
        self.assertNotEqual(last.research_zone, "SELECTION")
        html = self.client.get(f"/research/journal/{run.run_id}/{last.decision_id}/").content.decode()
        self.assertIn("read-guarded", html)
        lab = DecisionLabel.objects.filter(decision=last, label_type="FORWARD_RETURN", horizon_candles=1).first()
        if lab.gross_return is not None:
            self.assertNotIn(f"{lab.gross_return:.5f}", html)
        from bot.quant_core.research_zones import ZONES
        for s in DecisionLabel.objects.filter(run=run, status="SEALED"):
            self.assertIsNone(s.gross_return); self.assertIsNone(s.net_r)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DecisionLabel.objects.create(**{**{f.attname: getattr(lab, f.attname) for f in DecisionLabel._meta.concrete_fields
                                               if f.attname != "id"}, "horizon_candles": 99, "status": "SEALED", "gross_return": 0.01})
