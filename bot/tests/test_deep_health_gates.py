# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/test_deep_health_gates.py
# claude code changed: new file — 2026-10-07 Deep Health (DEEP_HEALTH_PLAN_2026-10-07.md §4). Proves the five-gate
# health checks judge correctness, not execution: correct known answers PASS; corrupted outputs, sign errors,
# timing errors and look-ahead mutations FAIL; missing prerequisites are BLOCKED/UNKNOWN, never a false PASS; the
# checks change no trading state, touch no network, are deterministic; the old shallow checks survive as L0.
# Corruptions are injected by patching the REAL function the check measures (mutation testing, not exchange mocks).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import dataclasses
import io
import json
import socket
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from bot.deep_health import core
from bot.deep_health.core import BLOCKED, FAIL, NA, PARTIAL, PASS, UNKNOWN, worst


def run(key):
    core._ensure_registered()
    return core.run_component(core.REGISTRY[key])


def failing(comp):
    return [(r["gate"], r["name"]) for r in comp["results"] if r["status"] == FAIL]


# Documented status of every L1 component on the current code. Anything not PASS carries a written reason.
EXPECTED_L1 = {
    "position_sizer": PASS, "pair_pnl": PASS, "timing_contract": PASS, "funding": PASS, "regime_detector": PASS,
    "kalman": PASS, "oos_validator": PASS, "research_zones": PASS, "provenance": PASS, "journal": PASS,
    "execution_costs": PARTIAL,          # existing silent fallbacks, reported not changed
    "dca": BLOCKED,                      # D-5
    "canonical_walk_forward": BLOCKED, "inference": BLOCKED, "cointegration_eg": BLOCKED,   # owner fold boundaries / OPEN-36
    "step17_null": BLOCKED, "pit_universe": BLOCKED, "fx_profile": BLOCKED,                # D-4 / D-6 / OD-FX
}


class KnownAnswersPassTests(TestCase):

    def test_every_l1_component_has_its_documented_status(self):
        for key, status in EXPECTED_L1.items():
            with self.subTest(key=key):
                comp = run(key)
                self.assertEqual(comp["status"], status, comp["results"])
                self.assertEqual(failing(comp), [])

    def test_no_component_is_unaccounted_for(self):
        core._ensure_registered()
        l1 = {k for k, s in core.REGISTRY.items() if s.level == "L1"}
        self.assertEqual(l1, set(EXPECTED_L1))

    def test_every_non_pass_result_states_a_reason(self):
        for key in EXPECTED_L1:
            for r in run(key)["results"]:
                if r["status"] != PASS:
                    self.assertTrue(r["reason"].strip(), (key, r))

    def test_blocked_components_name_their_owner_decision(self):
        self.assertIn("OPEN-36", json.dumps(run("inference")["results"]))
        self.assertIn("D-5", json.dumps(run("dca")["results"]))
        self.assertIn("D-4", json.dumps(run("step17_null")["results"]))
        self.assertIn("D-6", json.dumps(run("pit_universe")["results"]))

    def test_known_answers_are_the_hand_derived_values(self):
        pair = {r["name"]: r for r in run("pair_pnl")["results"]}
        self.assertIn("8.64897", pair["net = 8.64897"]["expected"])
        self.assertEqual(pair["net = 8.64897"]["status"], PASS)

    def test_l0_infrastructure_passes_on_the_test_database(self):
        self.assertEqual(run("postgres")["status"], PASS)


class CorruptionFailTests(SimpleTestCase):
    """A function that runs but returns a wrong known answer must be FAIL."""

    def test_sizer_returning_double_fails(self):
        from bot.risk.position_sizer import PositionSizer
        orig = PositionSizer.calculate
        with mock.patch.object(PositionSizer, "calculate", lambda self, *a: orig(self, *a) * 2):
            comp = run("position_sizer")
        self.assertEqual(comp["status"], FAIL)
        self.assertEqual(comp["gates"]["G2"], FAIL)

    def test_pair_net_pnl_off_by_one_fails(self):
        from bot.pairs import pnl
        orig = pnl.close_pair_slice

        def bad(*a, **k):
            sl, rest = orig(*a, **k)
            return dataclasses.replace(sl, net_pnl=sl.net_pnl + 1.0), rest
        with mock.patch.object(pnl, "close_pair_slice", bad):
            comp = run("pair_pnl")
        self.assertIn(("G2", "net = 8.64897"), failing(comp))

    def test_doubled_fees_fail(self):
        from bot.engines import simulation as sim
        orig = sim.calc_fees
        with mock.patch.object(sim, "calc_fees", lambda *a, **k: orig(*a, **k) * 2):
            self.assertEqual(run("execution_costs")["gates"]["G2"], FAIL)

    def test_dca_weighted_average_error_fails(self):
        from bot.research import dca_engine as de
        orig = de.simulate_dca_path

        def bad(*a, **k):
            trades, eq = orig(*a, **k)
            trades[0]["entry_price"] = sum(f["price"] for f in trades[0]["cycle_fills"]) / len(trades[0]["cycle_fills"])
            return trades, eq
        with mock.patch.object(de, "simulate_dca_path", bad):   # simple average instead of weighted
            self.assertEqual(run("dca")["gates"]["G2"], FAIL)


class SignErrorFailTests(SimpleTestCase):

    def test_flipped_slippage_direction_fails(self):
        from bot.engines import simulation as sim
        orig = sim.apply_slippage
        flip = {"LONG": "SHORT", "SHORT": "LONG"}
        with mock.patch.object(sim, "apply_slippage", lambda p, d, e, r=None: orig(p, flip.get(d, d), e, r)):
            self.assertEqual(run("execution_costs")["gates"]["G4"], FAIL)

    def test_flipped_funding_sign_fails(self):
        from bot.quant_core import funding as fu
        orig = fu.funding_cost

        def flipped(*a, **k):
            r = orig(*a, **k)
            return dataclasses.replace(r, cost=None if r.cost is None else -r.cost)
        with mock.patch.object(fu, "funding_cost", flipped):
            comp = run("funding")
        self.assertEqual(comp["status"], FAIL)
        self.assertEqual(comp["gates"]["G4"], FAIL)

    def test_flipped_short_spread_sign_fails(self):
        from bot.pairs import pnl
        orig = pnl.close_pair_slice

        def bad(position, *a, **k):
            sl, rest = orig(position, *a, **k)
            if position.direction == "SHORT_SPREAD":
                sl = dataclasses.replace(sl, mark_pnl=-sl.mark_pnl)
            return sl, rest
        with mock.patch.object(pnl, "close_pair_slice", bad):
            self.assertIn(("G2", "SHORT_SPREAD mirrors the sign: mark P&L = −20"), failing(run("pair_pnl")))


class TimingErrorFailTests(SimpleTestCase):

    def test_same_bar_fill_label_fails(self):
        from bot.quant_core import timing
        with mock.patch.object(timing, "forward_label",
                               lambda df, h, c: (df[c.execution_price].shift(-h) / df[c.execution_price] - 1.0)):
            comp = run("timing_contract")              # d = 0 (fills at the decision bar's own price)
        self.assertEqual(comp["gates"]["G4"], FAIL)

    def test_dca_fill_at_the_signal_close_fails(self):
        from bot.research import dca_engine as de
        from bot.deep_health.l1_quant import dca_fixture
        orig = de.simulate_dca_path
        df = dca_fixture()

        def same_close(*a, **k):
            trades, eq = orig(*a, **k)
            f = trades[0]["cycle_fills"][1]
            f["timestamp"], f["price"] = df.index[2], float(df["close"].iloc[2])   # filled at the confirming close
            return trades, eq
        with mock.patch.object(de, "simulate_dca_path", same_close):
            self.assertEqual(run("dca")["gates"]["G4"], FAIL)

    def test_disabled_zero_delay_guard_fails(self):
        from bot.quant_core import timing

        class Lax(timing.TimingContract):
            def __post_init__(self):
                pass
        with mock.patch.object(timing, "TimingContract", Lax):
            self.assertEqual(run("timing_contract")["gates"]["G4"], FAIL)


class LookAheadFailTests(SimpleTestCase):

    def test_regime_precompute_reading_future_rows_fails(self):
        from bot.backtesting import regime_precomputer as rp
        orig = rp.precompute_regime_results

        def leaky(df, det):
            peek = df.copy()
            peek["close"] = df["close"].shift(-3).fillna(df["close"])   # uses candles not yet closed
            return orig(peek, det)
        with mock.patch.object(rp, "precompute_regime_results", leaky):
            comp = run("regime_causal")
        self.assertEqual(comp["gates"]["G5"], FAIL)

    def test_regime_causal_passes_on_the_real_code(self):
        self.assertEqual(run("regime_causal")["status"], PASS)
        self.assertEqual(run("kalman_causal")["status"], PASS)

    def test_journal_rule_reading_bars_after_the_exit_fails(self):
        from bot.journal import labeler as lb
        orig = lb.simulate_rule_path

        def leaky(o, h, l, c, direction, stop, target, max_hold=24):
            st, why, out = orig(o, h, l, c, direction, stop, target, max_hold=max_hold)
            if out:
                out = [dataclasses.replace(out[0], exit_price=float(np.max(h[:max_hold])))] + out[1:]
            return st, why, out
        with mock.patch.object(lb, "simulate_rule_path", leaky):
            self.assertEqual(run("journal")["gates"]["G5"], FAIL)

    def test_purge_mutation_is_detected(self):
        from bot.research import oos_validator as ov
        orig = ov.build_folds
        with mock.patch.object(ov, "build_folds", lambda ix, cfg: orig(ix, dataclasses.replace(cfg, purge_periods=0, embargo_periods=0))):
            self.assertEqual(run("oos_validator")["gates"]["G5"], FAIL)


class PrerequisiteTests(SimpleTestCase):
    """Missing prerequisites are BLOCKED/UNKNOWN — never a false PASS."""

    def test_missing_l2_report_is_unknown(self):
        from bot.deep_health.dashboard import l2_state
        status, reason, comps = l2_state({"code_commit": "a" * 40, "code_dirty_digest": None}, None)
        self.assertEqual((status, comps), (UNKNOWN, []))
        self.assertIn("never been run", reason)

    def test_l2_report_from_other_code_is_unknown(self):
        from bot.deep_health.dashboard import l2_state
        old = {"code_commit": "b" * 40, "code_dirty_digest": None, "started_at": "x", "components": [{"status": PASS}]}
        self.assertEqual(l2_state({"code_commit": "a" * 40, "code_dirty_digest": None}, old)[0], UNKNOWN)
        dirty = dict(old, code_commit="a" * 40, code_dirty_digest="d1")
        self.assertEqual(l2_state({"code_commit": "a" * 40, "code_dirty_digest": "d2"}, dirty)[0], UNKNOWN)
        same = dict(old, code_commit="a" * 40)
        self.assertEqual(l2_state({"code_commit": "a" * 40, "code_dirty_digest": None}, same)[0], PASS)

    def test_calibration_gates_are_blocked_until_open_36(self):
        for key in ("inference", "cointegration_eg", "inference_size", "bootstrap_coverage"):
            with self.subTest(key=key):
                self.assertEqual(run(key)["gates"]["G3"], BLOCKED)

    def test_git_unavailable_is_partial_not_pass(self):
        from bot.quant_core import provenance as pv
        with mock.patch.object(pv, "code_state", lambda *a, **k: pv.CodeState(None, None, None, error="git unavailable")):
            self.assertEqual(run("provenance")["gates"]["G5"], PARTIAL)

    def test_declared_gate_without_a_check_is_unknown(self):
        spec = core.ComponentSpec("t", "t", core.QUANT, "L1", ("G1", "G2"),
                                  lambda: [core.CheckResult("G1", "x", PASS)])
        comp = core.run_component(spec)
        self.assertEqual((comp["gates"]["G2"], comp["status"]), (UNKNOWN, UNKNOWN))

    def test_a_crashing_check_is_fail_not_skip(self):
        def boom():
            raise ImportError("module missing")
        comp = core.run_component(core.ComponentSpec("t", "t", core.QUANT, "L1", ("G2",), boom))
        self.assertEqual((comp["status"], comp["gates"]["G1"]), (FAIL, FAIL))


class AggregationTests(SimpleTestCase):

    def test_worst_of_never_averaged(self):
        self.assertEqual(worst([PASS] * 50 + [FAIL]), FAIL)
        self.assertEqual(worst([PASS, PARTIAL, BLOCKED]), BLOCKED)
        self.assertEqual(worst([PASS, PARTIAL, UNKNOWN]), UNKNOWN)
        self.assertEqual(worst([BLOCKED, FAIL]), FAIL)
        self.assertEqual(worst([PASS, NA]), PASS)
        self.assertEqual(worst([NA]), NA)


class SafetyAndDeterminismTests(TestCase):

    def _trading_fingerprint(self):
        from bot.config import execution_costs, risk
        from bot.deep_health.l1_quant import dca_fixture
        from bot.research import dca_engine as de
        trades, _ = de.simulate_dca_path(dca_fixture(), de.DCAConfig(initial_usdt=100.0, levels=(de.DCALevel(0.1, 2.0),)))
        return (risk.DEFAULT_RISK_PCT, risk.MAX_RISK_PCT, execution_costs.FEE_RATE, execution_costs.SLIPPAGE_RATE,
                json.dumps(trades, default=str))

    def test_health_run_changes_no_state(self):
        from bot.journal.models import DecisionEvent, JournalRun, TradeRecord
        from bot.quant_core.research_zones import DEFAULT_LEDGER
        before_rows = (TradeRecord.objects.count(), JournalRun.objects.count(), DecisionEvent.objects.count())
        ledger_before = DEFAULT_LEDGER.read_bytes() if DEFAULT_LEDGER.exists() else None
        trading_before = self._trading_fingerprint()
        core.run_health(("L0", "L1"))
        self.assertEqual(before_rows, (TradeRecord.objects.count(), JournalRun.objects.count(), DecisionEvent.objects.count()))
        self.assertEqual(ledger_before, DEFAULT_LEDGER.read_bytes() if DEFAULT_LEDGER.exists() else None)
        self.assertEqual(trading_before, self._trading_fingerprint())

    def test_network_is_refused_inside_and_restored_after(self):
        def tries_network():
            socket.create_connection(("example.com", 443), timeout=1)
            return [core.CheckResult("G1", "reached the network", PASS)]
        core.REGISTRY["_net_probe"] = core.ComponentSpec("_net_probe", "net", core.INFRA, "L0", ("G1",), tries_network)
        try:
            rep = core.run_health(("L0",), only=["_net_probe"])
        finally:
            del core.REGISTRY["_net_probe"]
        comp = rep["components"][0]
        self.assertEqual(comp["status"], FAIL)
        self.assertIn("HealthNetworkBlocked", comp["results"][0]["observed"])
        self.assertIsNot(socket.create_connection, None)
        self.assertNotIn("refuse", getattr(socket.create_connection, "__name__", ""))

    def test_deterministic(self):
        a, b = core.run_health(("L0", "L1")), core.run_health(("L0", "L1"))
        self.assertNotEqual(a["run_id"], b["run_id"])
        self.assertEqual(core.comparable(a), core.comparable(b))

    def test_shallow_checks_remain_as_l0_rows(self):
        from bot.views import terminal_data
        names = {c["name"] for c in terminal_data.get_system_health()["checks"]}
        rows = {r["name"] for r in run("infra_shallow")["results"]}
        self.assertEqual(names, rows)
        self.assertEqual(run("infra_shallow")["level"], "L0")

    def test_cli_level_writes_a_report_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(core, "REPORT_DIR", Path(d)):
            out = io.StringIO()
            call_command("deep_health_check", level="L0", stdout=out)
            files = list(Path(d).glob("L0_*.json"))
            self.assertEqual(len(files), 1)
            rep = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(rep["levels"], ["L0"])
            self.assertIn("OVERALL", out.getvalue())
            with self.assertRaises(FileExistsError):
                core.save_report(rep, Path(d))

    def test_existing_module_walk_mode_is_untouched(self):
        from bot.management.commands.deep_health_check import Command
        parser = Command().create_parser("manage.py", "deep_health_check")
        self.assertEqual(parser.parse_args([]).mode, "standard")
        self.assertIsNone(parser.parse_args([]).level)


class PageTests(TestCase):

    def test_page_shows_areas_gates_and_run_identity(self):
        with mock.patch("bot.deep_health.dashboard.load_latest_report", lambda level: None):
            html = self.client.get("/system/health/").content.decode()
        for text in ("Deep Health", "Infrastructure Health", "Quant Engine Health", "Research Infrastructure Health",
                     "G1", "G5", "methodology", "commit", "L2 has never been run", "OPEN-36"):
            self.assertIn(text, html)

    def test_a_failure_is_not_hidden_behind_green(self):
        from bot.risk.position_sizer import PositionSizer
        orig = PositionSizer.calculate
        with mock.patch.object(PositionSizer, "calculate", lambda self, *a: orig(self, *a) * 2), \
                mock.patch("bot.deep_health.dashboard.load_latest_report", lambda level: None):
            resp = self.client.get("/system/health/")
        html = resp.content.decode()
        self.assertEqual(resp.context["dh"]["overall"], FAIL)
        self.assertIn("FAILING", html)
        self.assertIn("long: 10,000 × 1 % / |100 − 98| = 50", html)          # the failing check is listed…
        self.assertIn("100.0", html)                                          # …with its observed value
