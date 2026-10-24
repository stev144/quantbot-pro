# claude code changed: new file — 2026-10-22, Leverage Reality Phase 2: known-answer liquidation maths, ladder
# feasibility, input validation, historical outcome definitions, seal, persistence and the Academy page.
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
from django.test import SimpleTestCase, TestCase

from bot.research import leverage_reality as lr

S = lambda **kw: lr.ScenarioInput(**{"account_equity": 2000.0, "entry_price": 100.0, "leverage": 20.0,
                                     "step_margins": (100.0, 200.0, 300.0, 400.0), "step_spacing_pct": 1.0, **kw})


class LiquidationFormulaTests(SimpleTestCase):
    def test_long_and_short_known_answers_satisfy_the_defining_identity(self):
        m = 0.005
        p_long = lr.liquidation_price(lr.LONG, 1.0, 100.0, 10.0, m)          # isolated 10× long, B = 10
        self.assertAlmostEqual(p_long, 90.0 / 0.995, places=10)
        self.assertAlmostEqual(10.0 + 1.0 * (p_long - 100.0), m * 1.0 * p_long, places=10)
        p_short = lr.liquidation_price(lr.SHORT, 2.0, 100.0, 20.0, m)       # isolated 10× short
        self.assertAlmostEqual(p_short, 220.0 / (2 * 1.005), places=10)
        self.assertAlmostEqual(20.0 - 2.0 * (p_short - 100.0), m * 2.0 * p_short, places=10)

    def test_fully_collateralised_long_has_no_liquidation_price(self):
        self.assertIsNone(lr.liquidation_price(lr.LONG, 1.0, 100.0, 100.0, 0.005))

    def test_single_isolated_step_matches_the_hold_formula(self):
        r = lr.ladder_scenario(S(step_margins=(100.0,), margin_mode=lr.ISOLATED, leverage=10.0, taker_fee_rate=0.0))
        self.assertAlmostEqual(r["steps"][0]["liquidation_price"], 100.0 * (1 - 1 / 10) / (1 - 0.005), places=10)

    def test_cross_backing_is_never_closer_than_isolated(self):
        cross = lr.ladder_scenario(S(margin_mode=lr.CROSS))["steps"][-1]["liquidation_price"]
        iso = lr.ladder_scenario(S(margin_mode=lr.ISOLATED))["steps"][-1]["liquidation_price"]
        self.assertLess(cross, iso)


class LadderTests(SimpleTestCase):
    def test_four_step_ladder_known_answer(self):
        r = lr.ladder_scenario(S())
        self.assertIsNone(r["stopped"])
        prices = [100.0, 99.0, 98.01, 97.0299]
        qty = sum(m * 20 / p for m, p in zip((100, 200, 300, 400), prices))
        last = r["steps"][-1]
        for row, p in zip(r["steps"], prices):
            self.assertAlmostEqual(row["price"], p, places=10)
        self.assertAlmostEqual(last["cumulative_qty"], qty, places=9)
        self.assertAlmostEqual(last["cumulative_notional_at_entry"], 20_000.0, places=9)
        self.assertAlmostEqual(last["weighted_avg_entry"], 20_000.0 / qty, places=10)
        self.assertAlmostEqual(last["fees_paid"], 10.0, places=10)
        self.assertAlmostEqual(last["liquidation_price"], (20_000.0 - 1990.0) / (qty * 0.995), places=9)
        self.assertAlmostEqual(last["effective_leverage_on_equity"], 10.0, places=10)

    def test_advertised_1000_dollar_ladder_cannot_fund_its_last_step(self):
        r = lr.ladder_scenario(S(account_equity=1000.0))
        self.assertEqual(len(r["steps"]), 3)
        self.assertEqual(r["stopped"]["step"], 4)
        self.assertIn("insufficient available balance", r["stopped"]["reason"])

    def test_liquidation_before_the_next_step_stops_the_ladder(self):
        r = lr.ladder_scenario(S(margin_mode=lr.ISOLATED, leverage=50.0, step_spacing_pct=5.0))
        self.assertEqual(len(r["steps"]), 1)
        self.assertIn("liquidated before step 2", r["stopped"]["reason"])

    def test_short_ladder_adds_at_higher_prices(self):
        r = lr.ladder_scenario(S(direction=lr.SHORT))
        prices = [row["price"] for row in r["steps"]]
        self.assertEqual(prices, sorted(prices))
        self.assertGreater(r["steps"][-1]["liquidation_price"], prices[-1])

    def test_sensitivity_flags_match_the_liquidation_price(self):
        r = lr.ladder_scenario(S())
        liq = r["steps"][-1]["liquidation_price"]
        for x in r["sensitivity"]:
            self.assertEqual(x["liquidated"], x["price"] <= liq)

    def test_invalid_inputs_are_refused_never_adjusted(self):
        bad = [dict(leverage=0.5), dict(leverage=126), dict(step_margins=tuple([10.0] * 11)), dict(step_margins=()),
               dict(step_margins=(100.0, -5.0)), dict(entry_price=0.0), dict(account_equity=float("nan")),
               dict(step_spacing_pct=60.0), dict(direction="UP"), dict(margin_mode="PORTFOLIO"),
               dict(contract_type="INVERSE"), dict(maintenance_rate=0.6), dict(taker_fee_rate=0.05)]
        for kw in bad:
            with self.assertRaises(lr.ScenarioError, msg=str(kw)):
                lr.ladder_scenario(S(**kw))

    def test_first_step_larger_than_the_account_is_an_error(self):
        with self.assertRaises(lr.ScenarioError):
            lr.ladder_scenario(S(account_equity=50.0, step_margins=(100.0,)))


class HistoricalCycleTests(SimpleTestCase):
    """_ladder_cycle on hand-built bars: definitions of TAKE_PROFIT / WIPEOUT / UNRESOLVED and the two bounds."""
    margins, L, s, tp, m, fee, eq = (100.0, 200.0, 300.0, 400.0), 20.0, 0.01, 0.01, 0.005, 0.0, 2000.0

    def cycle(self, h, l, c, conservative=True, H=50):
        return lr._ladder_cycle(np.array(h, float), np.array(l, float), np.array(c, float), 0, H,
                                self.margins, self.L, self.s, self.tp, self.m, self.fee, self.eq, conservative)

    def test_take_profit_known_answer(self):
        out, pnl, nf = self.cycle([100, 101.5], [100, 100], [100, 101])
        self.assertEqual((out, nf), (lr.TAKE_PROFIT, 1))
        self.assertAlmostEqual(pnl, 20.0 * 101.0 - 2000.0, places=9)       # 20 units, TP exactly at 101

    def test_steady_fall_is_a_wipeout_of_the_whole_account(self):
        lows = [100] + list(np.linspace(99, 80, 30))
        highs = [100] + [x + 0.2 for x in lows[1:]]                     # every bar stays below any take-profit level
        out, pnl, nf = self.cycle(highs, lows, highs)
        self.assertEqual(out, lr.WIPEOUT)
        self.assertEqual(pnl, -self.eq)

    def test_sideways_is_unresolved_not_a_win_or_loss(self):
        out, pnl, _ = self.cycle([100] * 10, [99.5] * 10, [100] * 10, H=9)
        self.assertEqual(out, lr.UNRESOLVED)
        self.assertIsNone(pnl)

    def test_intrabar_bounds_differ_as_defined(self):
        # bar 1: low 98.9 fills step 2 at 99; high 100.4 is above the new-average TP (≈100.33) but below the old one (101)
        h, l, c = [100, 100.4], [100, 98.9], [100, 99.5]
        cons, _, _ = self.cycle(h, l, c, conservative=True, H=1)
        opt, _, _ = self.cycle(h, l, c, conservative=False, H=1)
        self.assertEqual(cons, lr.UNRESOLVED)        # old average 100 → TP 101 not reached; then the fall fills step 2
        self.assertEqual(opt, lr.TAKE_PROFIT)        # fill first → average ≈ 99.33 → TP ≈ 100.33 ≤ 100.4


class HistoricalRunTests(SimpleTestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write(self, start="2026-07-01", days=95, crash_day=None):
        hours = pd.date_range(start, periods=days * 24, freq="h", tz="UTC")
        px = np.full(len(hours), 100.0)
        if crash_day is not None:
            px[crash_day * 24:] = 50.0
        pd.DataFrame({"timestamp": hours.strftime("%Y-%m-%d %H:%M:%S+00:00"), "open": px, "high": px, "low": px,
                      "close": px, "volume": 1.0}).to_csv(Path(self.d) / "TST_USDT_1h.csv", index=False)
        sett = pd.date_range(start, periods=days * 3, freq="8h", tz="UTC")
        pd.DataFrame({"timestamp": sett.as_unit("ms").asi8, "funding_rate": 0.0, "mark_price": np.nan}).to_csv(
            Path(self.d) / "TST_USDT_funding.csv", index=False)

    def test_sealed_rows_are_never_read(self):
        self.write()                                                   # runs past F = 2026-10-04
        opens, a = lr._load_spot(Path(self.d) / "TST_USDT_1h.csv", lr.ZONES)
        self.assertTrue((opens < lr.ZONES.F).all())
        self.assertEqual(len(opens), len(a["close"]))

    def test_hold_liquidation_counts_and_persistence(self):
        self.write(start="2025-01-01", days=70, crash_day=40)
        res = lr.run_historical(data_dir=self.d, out_dir=Path(self.d) / "out", write=True,
                                files=[Path(self.d) / "TST_USDT_1h.csv"])
        hold = {h["leverage"]: h for h in res["hold"]}
        self.assertEqual(hold[1]["liquidated_share"], 0.0)
        self.assertGreater(hold[3]["liquidated_share"], 0.0)            # a 50 % crash liquidates 3× (needs −33 %)
        self.assertGreaterEqual(hold[50]["liquidated_share"], hold[3]["liquidated_share"])
        self.assertEqual(lr.load_historical(Path(self.d) / "out")["run_id"], res["run_id"])
        self.assertTrue((Path(self.d) / "out" / (lr.SUMMARY_NAME + ".provenance.json")).exists())
        self.assertEqual(res["classification"], lr.CLASSIFICATION)


class LeverageRealityPageTests(TestCase):
    def test_default_calculator_shows_the_honest_stop(self):
        with mock.patch.object(lr, "load_historical", return_value=None):
            html = self.client.get("/academy/leverage-reality/").content.decode()
        self.assertIn("The ladder stops at step 4", html)
        self.assertIn("run_leverage_reality", html)                     # historical: explicit command, not computed
        self.assertIn("not advice", html)

    def test_invalid_input_is_reported(self):
        with mock.patch.object(lr, "load_historical", return_value=None):
            html = self.client.get("/academy/leverage-reality/?leverage=500").content.decode()
        self.assertIn("Cannot calculate: leverage must be between", html)
        with mock.patch.object(lr, "load_historical", return_value=None):
            html = self.client.get("/academy/leverage-reality/?steps=x").content.decode()
        self.assertIn("Cannot calculate", html)

    def test_historical_tables_read_the_persisted_summary(self):
        fake = {"run_id": "abc123def456", "classification": lr.CLASSIFICATION,
                "data": {"before": "2026-10-04T00:00:00+00:00", "n_files": 3}, "warnings": ["Past shares are not future probabilities."],
                "hold": [{"leverage": 10, "windows": 100, "liquidated_share": 0.72, "liquidated_share_interval": {"ok": True, "lo": 0.6, "hi": 0.8},
                          "median_return_on_margin_pct": -100.0, "share_positive": 0.24}],
                "ladder": [{"leverage": 20.0, "spacing_pct": 1.0, "bound": "conservative", "cycles": 10, "unresolved_at_30d": 1,
                            "wipeout_share_of_resolved": 0.05, "wipeout_share_interval": {"ok": False}, "cycles_per_wipeout": 20.0,
                            "avg_take_profit_usd": 50.0, "mean_result_per_resolved_cycle_usd": -2.5}]}
        with mock.patch.object(lr, "load_historical", return_value=json.loads(json.dumps(fake))):
            html = self.client.get("/academy/leverage-reality/").content.decode()
        self.assertIn("72%", html)
        self.assertIn("60–80%", html)
        self.assertIn("Past shares are not future probabilities", html)
        self.assertIn("20 cycles", html)
