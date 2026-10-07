# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/test_decision_journal_phase4.py
# claude code changed: new file — 2026-10-07, Decision Journal Phase 4 (descriptive analytics). G3: the time-block
# bootstrap (known answers, determinism, fail-closed, coverage sanity), episodes (OD-J14 iii, both counts), unit =
# episode; G5: the zone read guard (OD-J16 + V-1 + legacy + F). Offline.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import io
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless

import numpy as np
import pandas as pd
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from bot.journal import analytics as A
from bot.journal.models import DecisionEvent, DecisionLabel, JournalRun
from bot.quant_core.inference import time_block_bootstrap_mean_ci

DATA = Path("data/BTC_USDT_1h.csv")
H = pd.Timedelta(hours=1)


class TimeBlockBootstrapTests(SimpleTestCase):

    def times(self, n, every_h=30):
        return pd.date_range("2022-01-01", periods=n, freq=f"{every_h}h", tz="UTC")

    def test_mean_exact_deterministic_and_constant_series_degenerate(self):
        v = np.arange(50, dtype=float)
        a = time_block_bootstrap_mean_ci(v, self.times(50), block_hours=96, seed=7)
        b = time_block_bootstrap_mean_ci(v, self.times(50), block_hours=96, seed=7)
        self.assertEqual(a, b)
        self.assertEqual(a["mean"], 24.5)
        self.assertTrue(a["ok"] and a["lo"] < 24.5 < a["hi"])
        c = time_block_bootstrap_mean_ci(np.full(40, 0.3), self.times(40), block_hours=96, seed=1)
        self.assertAlmostEqual(c["lo"], 0.3); self.assertAlmostEqual(c["hi"], 0.3)

    def test_blocks_keep_close_observations_together(self):
        t = self.times(60, every_h=1)                                      # 60 hourly points
        self.assertEqual(time_block_bootstrap_mean_ci(np.ones(60), t, block_hours=24, seed=1)["n_blocks"], 3)

    def test_fails_closed_with_too_few_blocks_or_bad_input(self):
        r = time_block_bootstrap_mean_ci(np.ones(30), self.times(30, every_h=1), block_hours=96, seed=1)
        self.assertFalse(r["ok"]); self.assertNotIn("lo", r)
        self.assertFalse(time_block_bootstrap_mean_ci([1.0, np.nan], self.times(2), block_hours=24, seed=1)["ok"])
        self.assertFalse(time_block_bootstrap_mean_ci([], [], block_hours=24, seed=1)["ok"])

    def test_coverage_sanity_on_independent_data(self):
        rng = np.random.default_rng(11)
        hits = 0
        reps = 200
        for r in range(reps):
            ci = time_block_bootstrap_mean_ci(rng.normal(0, 1, 60), self.times(60), block_hours=96, seed=r, n_boot=499)
            hits += ci["lo"] <= 0.0 <= ci["hi"]
        self.assertGreater(hits / reps, 0.85)                               # percentile CI, n=60: near-nominal, a bit under
        self.assertLess(hits / reps, 0.99)


def dec(i, direction, t0=pd.Timestamp("2022-01-01", tz="UTC")):
    return SimpleNamespace(decision_id=i, candle_index=i, candle_open_time=t0 + i * H, direction=direction)


class EpisodeTests(SimpleTestCase):

    def test_consecutive_same_direction_is_one_episode(self):
        b, n = A.assign_episodes([dec(1, "LONG"), dec(2, "LONG"), dec(3, "LONG")], {}, H)
        self.assertEqual(set(b.values()), {1}); self.assertEqual(set(n.values()), {1})

    def test_direction_change_and_no_signal_break_both(self):
        b, n = A.assign_episodes([dec(1, "LONG"), dec(2, "SHORT"), dec(3, ""), dec(4, "SHORT")], {}, H)
        self.assertEqual([b[1], b[2], b[4]], [1, 2, 3]); self.assertEqual([n[1], n[2], n[4]], [1, 2, 3])
        self.assertNotIn(3, b)                                              # no candidate -> no episode

    def test_trade_exit_breaks_only_the_break_rule(self):
        cov = {5: "NOT_EVALUATED_IN_POSITION", 6: "NOT_EVALUATED_IN_POSITION", 7: "NOT_EVALUATED_EXIT_CANDLE"}
        b, n = A.assign_episodes([dec(4, "LONG"), dec(8, "LONG")], cov, H)
        self.assertEqual((b[4], b[8]), (1, 2))                              # (iii): break at exit ...
        self.assertEqual((n[4], n[8]), (1, 1))                              # ... and the never-break count differs

    def test_warmup_or_missing_data_breaks_both(self):
        b, n = A.assign_episodes([dec(4, "LONG"), dec(6, "LONG")], {5: "WARMUP"}, H)
        self.assertEqual((n[4], n[6]), (1, 2))
        gap = [dec(1, "LONG"), SimpleNamespace(decision_id=2, candle_index=2, candle_open_time=dec(1, "").candle_open_time + 5 * H, direction="LONG")]
        b2, n2 = A.assign_episodes(gap, {}, H)
        self.assertEqual((n2[1], n2[2]), (1, 2))                            # rows adjacent, time is not: a data gap


class ReadGuardTests(TestCase):

    def make_run(self, **kw):
        base = dict(run_id=f"R-{uuid.uuid4().hex[:8]}", environment="BACKTEST", execution_mode="RESEARCH", symbol="X",
                    asset_class="CRYPTO", venue="binance", timeframe="1h", status="CLOSED",
                    started_at=pd.Timestamp("2026-01-01", tz="UTC"), methodology_version="m", config_hash="c",
                    data_version="d", zones_version="z", zones_hash="h", strategy_set="OtherStrategy",
                    strategy_version="v", taxonomy_version="QBP-JRNL-REASON-1", schema_version="s",
                    gate_order_version="g", feature_set_version="f", coverage_version="c", evidence_status="e")
        base.update(kw)
        return JournalRun.objects.create(**base)

    def test_zone_s_default_and_forbidden_zones(self):
        run = self.make_run()
        self.assertEqual(A.read_labels(run).count(), 0)
        for z in (("FORWARD_HOLDOUT_SEALED",), ("UNASSIGNED_EXPOSED",), ("SELECTION", "EMBARGO")):
            with self.assertRaises(A.ZoneReadRefused):
                A.read_labels(run, zones=z)

    def test_legacy_runs_are_never_analysed(self):
        with self.assertRaises(A.ZoneReadRefused):
            A.read_labels(self.make_run(taxonomy_version="QBP-JRNL-LEGACY-1"))

    def test_zone_v_needs_a_registered_open_family_and_is_logged(self):
        from bot.quant_core.hypothesis_registry import Hypothesis, HypothesisRegistry
        run = self.make_run()
        with tempfile.TemporaryDirectory() as d:
            reg = HypothesisRegistry(Path(d) / "reg.json")
            with self.assertRaises(A.ZoneReadRefused):
                A.read_labels(run, zones=("VALIDATION",))                   # no family context
            ctx = {"program": "decision_journal", "quarter": "2026Q4", "purpose": "test"}
            with self.assertRaises(A.ZoneReadRefused):
                A.read_labels(run, zones=("VALIDATION",), confirmatory=ctx, registry=reg)   # nothing registered
            reg.register(Hypothesis("J1", "decision_journal", "2026Q4", "rejections save money",
                                    "a filter that blocks low-quality entries should avoid negative-expectancy trades",
                                    "JOURNAL_RULE_NET_R", "rr_filter", 24))
            A.read_labels(run, zones=("VALIDATION",), confirmatory=ctx, registry=reg)
            events = [e for e in reg._load()["events"] if e["type"] == "JOURNAL_V_READ"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["run_id"], run.run_id)

    def test_v1_zone_v_refused_for_the_backtester_strategies_even_with_a_family(self):
        run = self.make_run(strategy_set=A.V_NOT_INDEPENDENT_STRATEGY_SETS[0])
        with self.assertRaises(A.ZoneReadRefused) as cm:
            A.read_labels(run, zones=("VALIDATION",), confirmatory={"program": "p", "quarter": "2026Q4"})
        self.assertIn("V-1", str(cm.exception))


@skipUnless(DATA.exists(), "data/BTC_USDT_1h.csv not present")
class OutcomeTableTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        call_command("record_journal_backtest", symbol="BTC_USDT", candles=2500, end="2023-12-01",
                     allow_unvalidated=True, stdout=io.StringIO())
        cls.jrun = JournalRun.objects.get()
        call_command("label_journal_run", run=cls.jrun.run_id, stdout=io.StringIO())

    def test_rows_account_for_every_ok_label_and_units_are_episodes(self):
        self.assertEqual(A.DISPLAY_MIN_EPISODES, 30)                          # OD-J12 (owner-delegated) value
        t = A.outcome_table(self.jrun, by="status", label="rule")
        ok = DecisionLabel.objects.filter(run=self.jrun, label_type="COUNTERFACTUAL_RULE", variant="BASELINE_STOP_FIRST",
                                          status="OK", research_zone="SELECTION").count()
        self.assertEqual(sum(r["n_decisions"] for r in t["rows"]), ok)
        for r in t["rows"]:
            self.assertLessEqual(r["n_units"], r["n_decisions"])
            self.assertLessEqual(r["n_episodes_never_break"], r["n_units"])
            self.assertEqual(r["suppressed"], r["n_units"] < A.DISPLAY_MIN_EPISODES)

    def test_mean_is_the_mean_of_episode_first_values(self):
        t = A.outcome_table(self.jrun, by="direction", label="rule")
        decs = list(DecisionEvent.objects.filter(run=self.jrun).order_by("candle_index"))
        brk, _ = A.assign_episodes(decs, A.coverage_by_index(self.jrun), H)
        labs = {l.decision_id: l for l in DecisionLabel.objects.filter(run=self.jrun, label_type="COUNTERFACTUAL_RULE",
                                                                       variant="BASELINE_STOP_FIRST", status="OK")}
        for r in t["rows"]:
            if r["suppressed"]:
                continue
            seen, vals = set(), []
            for d in decs:
                if (d.direction or "(none)") != r["group"] or d.decision_id not in labs or labs[d.decision_id].net_r is None:
                    continue
                if brk.get(d.decision_id) not in seen:
                    seen.add(brk.get(d.decision_id)); vals.append(labs[d.decision_id].net_r)
            self.assertAlmostEqual(r["mean"], float(np.mean(vals)), places=12)

    def test_forward_tables_and_page(self):
        t = A.outcome_table(self.jrun, by="hour_utc", label="fwd4")
        self.assertEqual(t["block_hours"], 24)
        self.assertTrue(all(r["n_units"] >= 1 for r in t["rows"]))
        html = self.client.get(f"/research/journal/{self.jrun.run_id}/analytics/?by=regime&label=rule").content.decode()
        self.assertIn("not a test, not a claim", html)
        self.assertEqual(self.client.get(f"/research/journal/{self.jrun.run_id}/analytics/?by=bogus").status_code, 404)


class EpisodeUnitTests(ReadGuardTests):
    """Real backtests rarely produce multi-decision episodes (a candidate fills on the next candle), so the
    'unit = episode, not decision' rule needs a constructed run (found by mutation P4-3)."""

    def test_table_counts_episodes_and_averages_their_first_decision(self):
        run = self.make_run(coverage={"intervals": []})
        t0 = pd.Timestamp("2022-06-01", tz="UTC")
        i = 0
        for ep in range(40):                                               # 40 episodes x 3 consecutive decisions
            direction = "LONG" if ep % 2 == 0 else "SHORT"                 # alternate -> each run of 3 is one episode
            for k in range(3):
                d = DecisionEvent.objects.create(
                    decision_id=uuid.uuid4(), run=run, environment="BACKTEST", execution_mode="RESEARCH", symbol="X",
                    asset_class="CRYPTO", venue="binance", timeframe="1h", candle_index=i, candle_open_time=t0 + i * H,
                    information_time=t0 + (i + 1) * H, research_zone="SELECTION", zones_hash="h", strategy="s",
                    strategy_version="v", direction=direction, status="REJECTED", reason_code="DIRECTION_CONFLICTS_REGIME",
                    reason_stage="REGIME", gate_order_version="g", methodology_version="m", config_hash="c",
                    data_version="d", taxonomy_version="t", lifecycle_stages=["EVALUATED", "SIGNAL_GENERATED", "GATES_EVALUATED"])
                DecisionLabel.objects.create(
                    decision=d, run=run, outcome_kind="COUNTERFACTUAL_NOT_EXECUTED", label_type="COUNTERFACTUAL_RULE",
                    label_version="L", variant="BASELINE_STOP_FIRST", horizon_candles=24, information_time=d.information_time,
                    label_window_start=d.information_time, status="OK", research_zone="SELECTION",
                    net_r=float(ep) + 100.0 * k, gross_r=0.0, data_version="d")    # only the first decision carries ep
                i += 1
        row = A.outcome_table(run, by="status", label="rule")["rows"][0]
        self.assertEqual((row["n_decisions"], row["n_units"], row["n_episodes_never_break"]), (120, 40, 40))
        self.assertFalse(row["suppressed"])
        self.assertAlmostEqual(row["mean"], float(np.mean(range(40))))     # 19.5, not the decision-level mean
