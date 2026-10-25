# bot/tests/test_lthp_hedge.py
"""claude code changed: 2026-10-25 — new. LTHP-1 (LTHP_1_PREREGISTRATION_2026-10-25.md) known-answer and state tests for
the optional hedge overlay in bot/research/fx_survival_replay.run_replay. Synthetic price paths, no mocks."""
import dataclasses
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import bot.research.fx_survival_replay as R
from bot.tests.test_fx_survival_replay import NO_COST, _pair, _strat


def _falling(depth=60, step=0.0004, tail=40, tail_step=-0.0004):
    """1.1000 then a steady fall that fills all 5 levels, then `tail` more bars moving by tail_step per bar."""
    down = [1.1 - step * k for k in range(depth)]
    rest = [down[-1] + tail_step * (k + 1) for k in range(tail)]
    return [1.1] + down + rest


def _run(path, policy=None, capital=100_000, cost=NO_COST, strategy=None, **pair_kw):
    p = _pair(path, **pair_kw)
    return R.run_replay([p], R.AccountConfig(capital), cost, strategy or _strat(max_cycle_candles=72), hedge_policy=policy), p


def _fill5_ts(r, cycle_id=1):
    f = r["fills"]
    return pd.Timestamp(f[(f.cycle_id == cycle_id) & (f.level == 5)]["timestamp"].iloc[0])


class TriggerAndIdentityTests(unittest.TestCase):
    def test_stop5_exits_at_the_open_after_the_fill5_bar(self):
        base, p = _run(_falling())
        t5 = _fill5_ts(base)
        r, _ = _run(_falling(), R.HedgePolicy("STOP5"))
        c = r["cycles"].iloc[0]
        self.assertEqual(c["exit_reason"], "FIFTH_FILL_STOP")
        self.assertEqual(pd.Timestamp(c["exit_ts"]), t5 + pd.Timedelta(hours=1))      # next open, never the same bar
        j = list(p.ts).index(t5 + pd.Timedelta(hours=1))
        self.assertAlmostEqual(c["net_pnl_usd"], (p.o[j] - c["wap"]) * c["units"], places=8)
        R.reconcile(r)

    def test_hedge_without_costs_equals_the_stop_exactly(self):
        # one price moves both legs: long (exit - wap)U + short (hedge - exit)U = (hedge - wap)U = the stop's P&L
        stop, _ = _run(_falling(), R.HedgePolicy("STOP5"))
        hedge, _ = _run(_falling(), R.HedgePolicy("HEDGE_HOLD"))
        c_s, c_h = stop["cycles"].iloc[0], hedge["cycles"].iloc[0]
        self.assertEqual(c_h["exit_reason"], "TIME_EXIT")                            # held until the existing time exit
        self.assertEqual(c_h["hedge_state"], "ACTIVE")
        self.assertAlmostEqual(c_h["net_pnl_usd"], c_s["net_pnl_usd"], places=8)
        self.assertAlmostEqual(c_h["hedge_lots"], c_h["lots"])                        # 100 % of the basket's lots
        R.reconcile(hedge)

    def test_hedge_costs_one_spread_more_than_the_stop(self):
        stop, _ = _run(_falling(), R.HedgePolicy("STOP5"), cost=R.FxCostModel(swap_source="NONE"), spread_points=20)
        hedge, _ = _run(_falling(), R.HedgePolicy("HEDGE_HOLD"), cost=R.FxCostModel(swap_source="NONE"), spread_points=20)
        c_s, c_h = stop["cycles"].iloc[0], hedge["cycles"].iloc[0]
        # the long sells at bid in both arms; only the hedge buys back at the ask -> exactly one extra spread on U units
        self.assertAlmostEqual(c_s["net_pnl_usd"] - c_h["net_pnl_usd"], 0.0002 * c_h["units"], places=8)
        self.assertAlmostEqual(c_h["hedge_spread_usd"], 0.0002 * c_h["units"], places=9)
        R.reconcile(hedge)

    def test_exit_pending_at_the_trigger_bar_wins_and_is_recorded(self):
        base, p = _run(_falling())
        t5 = _fill5_ts(base)
        entry = pd.Timestamp(base["cycles"].iloc[0]["entry_ts"])
        k = int((t5 - entry) / pd.Timedelta(hours=1))                               # candles from entry to the fill-5 bar
        r, _ = _run(_falling(), R.HedgePolicy("HEDGE_HOLD"), strategy=_strat(max_cycle_candles=k))
        ev = r["events"]
        ev1 = ev[ev.cycle_id == 1]                                                  # later cycles may legitimately hedge
        self.assertIn(R.TRIGGER_SKIPPED_EXIT, set(ev1["type"]))
        self.assertNotIn(R.HEDGE_OPENED, set(ev1["type"]))
        self.assertEqual(r["cycles"].iloc[0]["exit_reason"], "TIME_EXIT")


class HedgeStateTests(unittest.TestCase):
    def test_margin_models_and_exposure_while_hedged(self):
        g, _ = _run(_falling(), R.HedgePolicy("HEDGE_HOLD", margin_model="GROSS"))
        o, _ = _run(_falling(), R.HedgePolicy("HEDGE_HOLD", margin_model="ONE_LEG"))
        rg, ro = g["rows"], o["rows"]
        h = rg["hedges_open"] == 1
        self.assertTrue(h.any())
        self.assertTrue(np.allclose(rg.loc[h, "margin_used"], 2 * ro.loc[h, "margin_used"]))   # both legs vs larger leg
        self.assertTrue(np.allclose(rg.loc[h, "gross_notional_usd"], 2 * rg.loc[h, "open_notional_usd"]))
        self.assertTrue(np.allclose(rg.loc[h, "net_notional_usd"], 0.0))

    def test_gross_margin_can_reject_the_hedge_one_leg_cannot(self):
        # capital chosen so the full basket fits but a second full leg does not
        base, _ = _run(_falling(), capital=100_000)
        peak_margin = base["cycles"].iloc[0]["peak_margin_usd"]
        cap = peak_margin * 1.5
        g, _ = _run(_falling(), R.HedgePolicy("HEDGE_HOLD", margin_model="GROSS"), capital=cap)
        o, _ = _run(_falling(), R.HedgePolicy("HEDGE_HOLD", margin_model="ONE_LEG"), capital=cap)
        self.assertIn(R.HEDGE_REJECTED, set(g["events"]["type"]))
        self.assertEqual(g["cycles"].iloc[0]["hedge_state"], "REJECTED")
        self.assertIn(R.HEDGE_OPENED, set(o["events"]["type"]))
        R.reconcile(g); R.reconcile(o)

    def test_hedge_swap_uses_the_short_rate_on_hedge_lots(self):
        p = dataclasses.replace(_pair(_falling(), start="2024-01-08 00:00"), swap_short_points=-20.0)
        r = R.run_replay([p], R.AccountConfig(100_000), R.FxCostModel(spread_source="NONE"), _strat(max_cycle_candles=72),
                         hedge_policy=R.HedgePolicy("HEDGE_HOLD"))
        led = r["ledger"]
        hs = led[led["note"].str.startswith("hedge rollover")]
        c = r["cycles"].iloc[0]
        self.assertGreater(len(hs), 0)
        days = sum(3 if "x3" in n else 1 for n in hs["note"])
        self.assertAlmostEqual(hs["amount_usd"].sum(), -20.0 * 1e-5 * 100_000 * c["hedge_lots"] * days, places=8)
        self.assertAlmostEqual(c["hedge_swap_usd"], hs["amount_usd"].sum(), places=9)
        R.reconcile(r)

    def test_hedged_basket_survives_a_crash_the_unhedged_one_does_not(self):
        crash = _falling(tail=30, tail_step=-0.004)                                # 400 pips per bar after the ladder
        base, _ = _run(crash, capital=3_000)
        hedge, _ = _run(crash, R.HedgePolicy("HEDGE_HOLD", margin_model="ONE_LEG"), capital=3_000)
        self.assertIn(R.LIQUIDATED, set(base["cycles"]["outcome"]))
        self.assertNotIn(R.LIQUIDATED, set(hedge["cycles"]["outcome"]))
        R.reconcile(hedge)


class RemovalTests(unittest.TestCase):
    def _v_path(self):
        """The fall stops two bars after the fill-5 bar; then +3 ATR up, back below the hedge, and +3 ATR up again
        (a repeated crossing that must NOT re-hedge)."""
        base, p = _run(_falling())
        k5 = list(p.ts).index(_fill5_ts(base))
        path = list(_falling()[:k5 + 3])
        last = path[-1]
        up = [last + 0.0006 * (k + 1) for k in range(5)]
        down = [up[-1] - 0.0008 * (k + 1) for k in range(6)]
        up2 = [down[-1] + 0.0008 * (k + 1) for k in range(6)]
        return path + up + down + up2 + [up2[-1]] * 5

    def test_removal_after_close_above_hedge_plus_one_atr_once_only(self):
        r, p = _run(self._v_path(), R.HedgePolicy("HEDGE_REMOVE"))
        ev = r["events"]
        c = r["cycles"].iloc[0]
        opened = ev[ev.type == R.HEDGE_OPENED]
        removed = ev[ev.type == R.HEDGE_REMOVED]
        self.assertEqual((len(opened[opened.cycle_id == 1]), len(removed[removed.cycle_id == 1])), (1, 1))   # never re-hedged
        t_rm = pd.Timestamp(removed.iloc[0]["timestamp"])
        j = list(p.ts).index(t_rm)
        self.assertGreaterEqual(p.c[j - 1], c["hedge_entry_bid"] + 1.0 * 0.001)                 # trigger on the previous close
        self.assertTrue(all(p.c[k] < c["hedge_entry_bid"] + 0.001 for k in range(list(p.ts).index(pd.Timestamp(c["hedge_open_ts"])), j - 1)))
        self.assertAlmostEqual(c["hedge_realised_usd"], (c["hedge_entry_bid"] - p.o[j]) * c["hedge_lots"] * 100_000, places=8)
        R.reconcile(r)

    def test_open_hedge_at_data_end_is_marked_and_reconciles(self):
        r, _ = _run(_falling(tail=5), R.HedgePolicy("HEDGE_HOLD"), strategy=_strat(max_cycle_candles=None))
        c = r["cycles"].iloc[-1]
        self.assertEqual(c["outcome"], R.OPEN_UNRESOLVED_AT_DATA_END)
        self.assertEqual(c["hedge_state"], "ACTIVE")
        self.assertIn("hedge_mtm_unrealised_usd", c.index)
        R.reconcile(r)


class GuardTests(unittest.TestCase):
    def test_invalid_policies_and_short_strategy_are_refused(self):
        with self.assertRaises(ValueError):
            R.HedgePolicy("HEDGE_FOREVER")
        with self.assertRaises(ValueError):
            R.HedgePolicy("HEDGE_HOLD", margin_model="NET")
        with self.assertRaises(ValueError):
            R.run_replay([_pair([1.1] * 10)], R.AccountConfig(5000), NO_COST, _strat(direction="SHORT"),
                         hedge_policy=R.HedgePolicy("STOP5"))

    def test_no_policy_is_bit_identical_to_the_baseline_engine(self):
        a, _ = _run(_falling(tail=80, tail_step=0.0006))
        b, _ = _run(_falling(tail=80, tail_step=0.0006), None)
        pd.testing.assert_frame_equal(a["cycles"], b["cycles"])
        self.assertNotIn("hedge_state", a["cycles"].columns)                     # baseline table shape unchanged
        self.assertNotIn("gross_notional_usd", a["rows"].columns)

    def test_saved_gbpjpy_baseline_reproduced_when_available(self):
        saved = Path("research_data/dca_survival/2026-10-16_0800/runs/gbpjpy_10000_cycles.csv")
        if not saved.exists():
            self.skipTest("saved baseline run not present")
        try:
            pair = R.build_pair_data("GBP/JPY")
        except FileNotFoundError as exc:
            self.skipTest(str(exc))
        r = R.run_replay([pair], R.AccountConfig(10_000))
        s = pd.read_csv(saved)
        self.assertEqual(len(r["cycles"]), len(s))
        self.assertLess(float(np.abs(r["cycles"]["net_pnl_usd"].to_numpy() - s["net_pnl_usd"].to_numpy()).max()), 1e-6)


class StudyGuardTests(unittest.TestCase):   # claude code changed: 2026-10-25 — runner governance + statistics known answers
    def test_altered_protocol_is_refused(self):
        import tempfile
        from bot.research import lthp_study as S
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / S.PROTOCOL_FILE).write_text("edited after registration", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                S.check_protocol(Path(d))
        self.assertEqual(S.check_protocol(Path(".")), S.PROTOCOL_SHA256)          # the registered file is intact
        self.assertTrue(S.IS_PAPER_ONLY)
        with tempfile.TemporaryDirectory() as d:   # claude code changed: 2026-10-25 — a CRLF checkout is the same text
            lf = (Path(".") / S.PROTOCOL_FILE).read_bytes().replace(b"\r\n", b"\n")
            (Path(d) / S.PROTOCOL_FILE).write_bytes(lf.replace(b"\n", b"\r\n"))
            self.assertEqual(S.check_protocol(Path(d)), S.PROTOCOL_SHA256)

    def test_sign_flip_holm_and_monthly_change_known_answers(self):
        from bot.research import lthp_study as S
        self.assertLess(S.sign_flip_p([1.0] * 20), 0.001)                        # all months better
        self.assertGreater(S.sign_flip_p([-1.0] * 20), 0.99)                     # all months worse
        self.assertEqual(S.holm({"a": 0.01, "b": 0.04, "c": 0.03}), {"a": 0.03, "c": 0.06, "b": 0.06})
        rows = pd.DataFrame({"timestamp": ["2024-01-01 00:00", "2024-01-31 23:00", "2024-02-15 00:00", "2024-02-29 23:00"],
                             "equity": [100.0, 110.0, 105.0, 120.0]})
        self.assertEqual(list(S.monthly_equity_change(rows)), [10.0, 10.0])      # Jan 100→110, Feb 110→120
