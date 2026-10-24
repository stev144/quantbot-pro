# claude code changed: new file — 2026-10-22, Futures Funding Intelligence Phase 1: known-answer, timing, data-
# completeness, seal and reproducibility tests for bot/research/funding_carry_monitor.py (G1/G2/G4/G5).
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research import funding_carry_monitor as fcm

SYM = "TST/USDT"


def write_data(d, start="2025-01-01", days=70, px=100.0, rate=0.0001, marks=None, spot_px=None,
               drop_settlements=(), drop_spot_opens=(), extra_fund_rows=None):
    hours = pd.date_range(start, periods=days * 24, freq="h", tz="UTC")
    close = np.full(len(hours), px) if spot_px is None else np.asarray(spot_px(hours), float)
    spot = pd.DataFrame({"timestamp": hours.strftime("%Y-%m-%d %H:%M:%S+00:00"), "open": close, "high": close,
                         "low": close, "close": close, "volume": 1.0})
    spot = spot[~hours.isin(pd.DatetimeIndex(list(drop_spot_opens), tz="UTC") if drop_spot_opens else [])]
    sett = pd.date_range(start, periods=days * 3, freq="8h", tz="UTC")
    sett = sett[~sett.isin(pd.DatetimeIndex(list(drop_settlements), tz="UTC") if drop_settlements else [])]
    fund = pd.DataFrame({"timestamp": sett.as_unit("ms").asi8, "funding_rate": rate,   # epoch ms, as recorded
                         "mark_price": (marks if marks is not None else np.nan)})
    if extra_fund_rows is not None:
        fund = pd.concat([fund, extra_fund_rows], ignore_index=True)
    spot.to_csv(Path(d) / "TST_USDT_1h.csv", index=False)
    fund.to_csv(Path(d) / "TST_USDT_funding.csv", index=False)


class Base(SimpleTestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def analyse(self, **cfg):
        return fcm.analyse_asset(SYM, fcm.CarryConfig(**cfg), data_dir=self.d)


class FormulaTests(SimpleTestCase):
    def test_round_trip_costs_known_answer(self):
        c = fcm.round_trip_costs(2.0, 100.0, 101.0, 110.0, 111.0, 0.001, 0.0005, 0.0005)
        self.assertAlmostEqual(c["open"], 2 * 100 * 0.0015 + 2 * 101 * 0.001, places=12)    # 0.3 + 0.202
        self.assertAlmostEqual(c["close"], 2 * 110 * 0.0015 + 2 * 111 * 0.001, places=12)   # 0.33 + 0.222
        self.assertAlmostEqual(c["total"], 1.054, places=12)

    def test_basis_pnl_known_answer_and_invariant(self):
        self.assertAlmostEqual(fcm.basis_pnl(2.0, 100.0, 101.0, 110.0, 110.5), 2 * 10 - 2 * 9.5, places=12)
        # perfectly tracking legs: spot gain cancels the short's loss exactly
        self.assertEqual(fcm.basis_pnl(3.0, 100.0, 100.0, 150.0, 150.0), 0.0)

    def test_short_liquidation_price_derivation(self):
        # equity = Q P0/L − Q (P − P0) = m Q P  ⇒  P = P0 (1 + 1/L)/(1 + m)
        self.assertAlmostEqual(fcm.short_liquidation_price(100.0, 1.0, 0.005), 200.0 / 1.005, places=10)
        self.assertAlmostEqual(fcm.short_liquidation_price(100.0, 2.0, 0.005), 150.0 / 1.005, places=10)
        p = fcm.short_liquidation_price(100.0, 3.0, 0.005)
        q, L, m = 1.0, 3.0, 0.005
        self.assertAlmostEqual(q * 100 / L - q * (p - 100), m * q * p, places=10)       # the defining identity
        for bad in ((0, 2, 0.005), (100, 0.5, 0.005), (100, 2, 1.0)):
            with self.assertRaises(ValueError):
                fcm.short_liquidation_price(*bad)

    def test_annualise_boundaries(self):
        self.assertIsNone(fcm.annualise(10.0, 100.0, 364.9))
        self.assertIsNone(fcm.annualise(10.0, 0.0, 400))
        self.assertIsNone(fcm.annualise(10.0, None, 400))
        self.assertAlmostEqual(fcm.annualise(10.0, 100.0, 365.25), 0.10, places=12)
        self.assertAlmostEqual(fcm.annualise(10.0, 100.0, 730.5), 0.05, places=12)

    def test_config_validation(self):
        for kw in ({"spot_notional_usd": 0}, {"spot_fee_rate": -0.001}, {"perp_fee_rate": 0.2},
                   {"short_leverages": (0.5,)}, {"short_leverages": ()}, {"spot_notional_usd": float("nan")}):
            with self.assertRaises(ValueError):
                fcm.CarryConfig(**kw)


class CashFlowTests(Base):
    def test_known_answer_positive_funding_is_income_to_the_short(self):
        write_data(self.d)                                   # 70 days, rate 0.0001/settlement, flat price 100
        a = self.analyse()
        self.assertEqual(a["status"], fcm.OK)
        # settlements: 210; 00:00 Jan 1 precedes the first spot close (01:00); entry = 08:00 Jan 1 (exclusive)
        self.assertEqual(a["n_settlements"], 208)
        self.assertAlmostEqual(a["quantity_base_units"], 100.0, places=12)
        A = a["scope_A_funding_only"]
        self.assertAlmostEqual(A["gross_funding_usd"], 208 * 0.0001 * 100 * 100, places=9)   # $1 per settlement
        self.assertAlmostEqual(A["costs_usd"]["total"], 2 * (100 * 100 * 0.0015 + 100 * 100 * 0.001), places=9)
        self.assertAlmostEqual(A["net_usd"], 208 - 50, places=9)
        self.assertEqual(A["negative_month_pct"], 0.0)

    def test_negative_funding_is_paid_by_the_short(self):
        write_data(self.d, rate=-0.0002)
        A = self.analyse()["scope_A_funding_only"]
        self.assertAlmostEqual(A["gross_funding_usd"], -208 * 2.0, places=9)
        self.assertEqual(A["negative_month_pct"], 100.0)
        self.assertLess(A["worst_month"]["funding_usd"], 0)

    def test_spot_is_priced_at_candle_close_time_not_open_time(self):
        # close of the candle opened at hour h = 100 + h (h counted from the start): rising price
        start = pd.Timestamp("2025-01-01", tz="UTC")
        write_data(self.d, days=3, spot_px=lambda idx: 100.0 + (idx - start) / pd.Timedelta(hours=1))
        a = self.analyse()
        s_entry = 100 + 7                                     # settlement 08:00 → candle opened 07:00 closes then
        self.assertAlmostEqual(a["spot_entry"], s_entry, places=12)
        qty = 10_000 / s_entry
        setts = pd.date_range("2025-01-01 16:00", "2025-01-03 16:00", freq="8h", tz="UTC")
        expect = sum(0.0001 * qty * (100 + ((t - start) / pd.Timedelta(hours=1) - 1)) for t in setts)
        look_ahead = sum(0.0001 * qty * (100 + (t - start) / pd.Timedelta(hours=1)) for t in setts)
        got = a["scope_A_funding_only"]["gross_funding_usd"]
        self.assertAlmostEqual(got, expect, places=9)
        self.assertNotAlmostEqual(got, look_ahead, places=6)

    def test_margin_is_capital_never_a_cost(self):
        write_data(self.d)
        a = self.analyse(short_leverages=(1.0, 2.0, 3.0))
        net = a["scope_A_funding_only"]["net_usd"]
        rows = a["capital_and_liquidation"]["rows"]
        self.assertEqual([r["initial_margin_usd"] for r in rows], [10_000.0, 5_000.0, 10_000.0 / 3])
        for r in rows:
            self.assertAlmostEqual(r["total_capital_usd"], 10_000 + r["initial_margin_usd"], places=9)
            self.assertAlmostEqual(r["net_on_capital_pct"], net / r["total_capital_usd"] * 100, places=9)
        self.assertIsNone(rows[0]["annualised_pct"])                       # 70 days < 365: no extrapolation
        self.assertFalse(any(r["breached_without_topups"] for r in rows))  # flat price never reaches liquidation

    def test_full_position_scope_with_marks_and_its_absence(self):
        write_data(self.d, marks=100.0)
        B = self.analyse()["scope_B_full_position"]
        self.assertEqual(B["status"], fcm.OK)
        self.assertEqual(B["basis_pnl_usd"], 0.0)
        self.assertAlmostEqual(B["net_usd"], 208 - 50, places=9)
        shutil.rmtree(self.d); self.d = tempfile.mkdtemp()
        write_data(self.d)                                                  # no marks
        a = self.analyse()
        self.assertEqual(a["scope_B_full_position"]["status"], fcm.UNAVAILABLE)
        self.assertEqual(a["perp_price_proxy"]["source"], "SPOT_PROXY")
        self.assertIn("EXCLUDED", a["scope_A_funding_only"]["label"])

    def test_mark_in_another_contract_unit_is_refused(self):
        write_data(self.d, marks=100_000.0)                                  # a 1000-unit contract quote
        a = self.analyse()
        self.assertEqual(a["scope_B_full_position"]["status"], fcm.UNAVAILABLE)
        self.assertTrue(a["perp_price_proxy"]["mark_unit_mismatch"])
        self.assertAlmostEqual(a["scope_A_funding_only"]["costs_usd"]["total"], 50.0, places=9)   # spot proxy, not ×1000

    def test_cost_sensitivity_grid(self):
        write_data(self.d)
        a = self.analyse()
        grid = {(g["fee_multiplier"], g["slippage_rate"]): g["net_usd"] for g in a["cost_sensitivity"]}
        self.assertEqual(len(grid), 9)
        self.assertAlmostEqual(grid[(1.0, 0.0005)], a["scope_A_funding_only"]["net_usd"], places=9)
        self.assertGreater(grid[(0.5, 0.0)], grid[(2.0, 0.001)])


class CompletenessTests(Base):
    def test_funding_gap_makes_the_month_incomplete_never_zero(self):
        write_data(self.d, drop_settlements=["2025-02-10 00:00", "2025-02-10 08:00"])
        a = self.analyse()
        feb = [m for m in a["months"] if m["month"] == "2025-02"][0]
        self.assertFalse(feb["complete"])
        self.assertIsNone(feb["funding_usd"])
        self.assertIn("gap", feb["reason"])
        self.assertAlmostEqual(a["scope_A_funding_only"]["gross_funding_usd"], 91 + 33, places=9)   # Jan + Mar only
        self.assertEqual(a["months_complete"], 2)

    def test_stale_spot_price_makes_the_month_incomplete(self):
        write_data(self.d, drop_spot_opens=["2025-03-05 04:00", "2025-03-05 05:00", "2025-03-05 06:00", "2025-03-05 07:00"])
        a = self.analyse()
        mar = [m for m in a["months"] if m["month"] == "2025-03"][0]
        self.assertFalse(mar["complete"])
        self.assertIn("spot price", mar["reason"])

    def test_nan_rate_is_invalid_not_zero(self):
        write_data(self.d)
        f = Path(self.d) / "TST_USDT_funding.csv"
        df = pd.read_csv(f); df.loc[5, "funding_rate"] = np.nan; df.to_csv(f, index=False)
        a = self.analyse()
        self.assertEqual(a["status"], fcm.INVALID)
        self.assertIn("non-finite", a["reason"])

    def test_duplicates_identical_are_counted_conflicting_are_invalid(self):
        same = pd.DataFrame({"timestamp": [pd.Timestamp("2025-01-05 08:00", tz="UTC").value // 10**6],
                             "funding_rate": [0.0001], "mark_price": [np.nan]})
        write_data(self.d, extra_fund_rows=same)
        a = self.analyse()
        self.assertEqual(a["status"], fcm.OK)
        self.assertEqual(a["data"]["excluded"]["funding_duplicates"], 1)
        self.assertEqual(a["n_settlements"], 208)
        shutil.rmtree(self.d); self.d = tempfile.mkdtemp()
        write_data(self.d, extra_fund_rows=same.assign(funding_rate=0.005))
        self.assertEqual(self.analyse()["status"], fcm.INVALID)

    def test_missing_files_and_no_overlap_are_unavailable(self):
        a = self.analyse()
        self.assertEqual(a["status"], fcm.UNAVAILABLE)
        write_data(self.d)
        (Path(self.d) / "TST_USDT_funding.csv").write_text("timestamp,funding_rate,mark_price\n"
                                                         f"{pd.Timestamp('2020-01-01', tz='UTC').value // 10**6},0.0001,\n")
        b = self.analyse()
        self.assertEqual(b["status"], fcm.UNAVAILABLE)
        self.assertIn("overlap", b["reason"])


class SealAndProvenanceTests(Base):
    def test_rows_at_or_after_the_sealed_holdout_are_never_used(self):
        write_data(self.d, start="2026-09-10", days=40)                      # runs past F = 2026-10-04
        a = self.analyse()
        F = pd.Timestamp("2026-10-04", tz="UTC")
        self.assertEqual(a["status"], fcm.OK)
        self.assertLess(pd.Timestamp(a["period"]["exit_settlement"]), F)
        self.assertGreater(a["data"]["excluded"]["spot_sealed"], 0)
        self.assertGreater(a["data"]["excluded"]["funding_sealed"], 0)
        self.assertTrue(all(pd.Period(m["month"]) <= pd.Period("2026-10") for m in a["months"]))

    def test_reproducible_and_persisted_with_provenance(self):
        write_data(self.d)
        cfg = fcm.CarryConfig()
        r1 = fcm.run_monitor([SYM], cfg, data_dir=self.d, write=False)
        r2 = fcm.run_monitor([SYM], cfg, data_dir=self.d, write=False)
        self.assertEqual(r1["run_id"], r2["run_id"])
        self.assertEqual(json.dumps(r1["assets"], sort_keys=True, default=str), json.dumps(r2["assets"], sort_keys=True, default=str))
        out = Path(self.d) / "out"
        r3 = fcm.run_monitor([SYM], cfg, data_dir=self.d, out_dir=out, write=True)
        for name in (fcm.REPORT_NAME, fcm.MONTHLY_NAME):
            self.assertTrue((out / name).exists())
            self.assertTrue((out / (name + ".provenance.json")).exists())
        self.assertEqual(fcm.load_report(out)["run_id"], r3["run_id"])
        self.assertIsNone(fcm.load_report(Path(self.d) / "missing"))
        r4 = fcm.run_monitor([SYM], fcm.CarryConfig(spot_notional_usd=5_000), data_dir=self.d, write=False)
        self.assertNotEqual(r4["run_id"], r1["run_id"])                     # configuration is part of the identity
        self.assertEqual(r1["classification"], fcm.CLASSIFICATION)


from unittest import mock   # noqa: E402  # claude code changed: 2026-10-22 — view tests
from django.test import TestCase   # noqa: E402


class FundingCarryPageTests(TestCase):   # claude code changed: 2026-10-22 — the page only READS the persisted report
    def test_no_report_shows_the_explicit_command_and_never_computes(self):
        with mock.patch.object(fcm, "load_report", return_value=None), \
             mock.patch.object(fcm, "run_monitor", side_effect=AssertionError("page must not compute")):
            html = self.client.get("/research/funding-carry/").content.decode()
        self.assertIn("run_funding_carry_monitor", html)
        self.assertIn("not a live yield or trading recommendation", html)

    def test_report_rows_render_with_labels(self):
        d = tempfile.mkdtemp()
        try:
            write_data(d, marks=100.0)
            report = fcm.run_monitor([SYM], fcm.CarryConfig(), data_dir=d, write=False)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        with mock.patch.object(fcm, "load_report", return_value=json.loads(json.dumps(report, default=str))):
            html = self.client.get("/research/funding-carry/").content.decode()
        self.assertIn("TST/USDT", html)
        self.assertIn("$208.00", html)                 # gross funding known answer
        self.assertIn("$158.00", html)                 # net funding-only known answer
        self.assertIn("DESCRIPTIVE", html)
        self.assertIn(report["run_id"], html)
