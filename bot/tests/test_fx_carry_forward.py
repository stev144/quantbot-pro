# bot/tests/test_fx_carry_forward.py  # claude code changed: 2026-10-28 — new: FXC-F1 forward paper position
"""Deterministic tests on synthetic prices and a temporary ledger (no MT5, no real ledger touched)."""
import math
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research import fx_carry_forward as F

TERMS = {"USD/JPY": {"point": 0.001, "swap_mode": 1, "swap_long": 19.0, "bid": 149.99, "ask": 150.01},
         "USD/CHF": {"point": 0.00001, "swap_mode": 1, "swap_long": 3.0, "bid": 0.79995, "ask": 0.80005}}


def _closes(n=200, start="2026-06-01", shock_at=None, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="D", tz="UTC")
    a = 150 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    b = 0.80 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    if shock_at is not None:
        a[shock_at:] *= 0.70                                          # a yen-unwind-sized drop
        b[shock_at:] *= 0.70
    return {"USD/JPY": pd.Series(a, idx), "USD/CHF": pd.Series(b, idx)}


class ForwardTest(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.tmp.name) / "ledger.json"
        self.design = Path(self.tmp.name) / "design.md"
        self.design.write_text("frozen rules", encoding="utf-8")
        self.t0 = pd.Timestamp("2026-09-15 10:00", tz="UTC")
        F.register("tester", now=self.t0, path=self.ledger, design_path=self.design)
        F.add_snapshot(TERMS, now=self.t0, path=self.ledger)

    def tearDown(self):
        self.tmp.cleanup()

    def test_second_registration_refused(self):
        with self.assertRaises(F.ForwardTestError):
            F.register("again", path=self.ledger, design_path=self.design)

    def test_first_fill_after_t0_lots_and_vol_target(self):
        st = F.replay(F.read_ledger(self.ledger), _closes(), through=pd.Timestamp("2026-12-01", tz="UTC"))
        first = st["rows"][0]
        self.assertEqual(first["day"], "2026-09-16")                  # first full UTC day after T0
        self.assertEqual(first["action"], "REBALANCE")
        for u in first["units"].values():
            self.assertEqual(u % F.LOT_UNITS, 0)                       # 0.01-lot steps, rounded down
        rebal = [r["day"] for r in st["rows"] if r["action"] == "REBALANCE"]
        self.assertEqual(rebal, ["2026-09-16", "2026-10-01", "2026-11-01", "2026-12-01"])   # start, then 1st of each month
        rep = F.report(st)
        self.assertLess(rep["realised_vol_pct"], 8.0)

    def test_carry_known_answer(self):
        st = F.replay(F.read_ledger(self.ledger), _closes(), through=pd.Timestamp("2026-09-18", tz="UTC"))
        u = st["rows"][0]["units"]
        c = _closes()
        d = pd.Timestamp("2026-09-17", tz="UTC")
        expect = (u["USD/JPY"] * 19.0 * 0.001 / c["USD/JPY"][d] + u["USD/CHF"] * 3.0 * 0.00001 / c["USD/CHF"][d])
        self.assertAlmostEqual(st["rows"][1]["carry_pnl"], expect)

    def test_price_pnl_uses_only_days_after_t0(self):
        a = _closes()
        b = {p: s.copy() for p, s in a.items()}
        for p in b:
            b[p].loc[:"2026-06-30"] *= 1.01                            # change history before the vol window only
        ra = F.replay(F.read_ledger(self.ledger), a, through=pd.Timestamp("2026-10-01", tz="UTC"))
        rb = F.replay(F.read_ledger(self.ledger), b, through=pd.Timestamp("2026-10-01", tz="UTC"))
        self.assertEqual([r["equity"] for r in ra["rows"]], [r["equity"] for r in rb["rows"]])

    def test_loss_limit_closes_everything_and_stops(self):
        st = F.replay(F.read_ledger(self.ledger), _closes(shock_at=120), through=pd.Timestamp("2026-12-15", tz="UTC"))
        self.assertIsNotNone(st["stopped"])
        last = st["rows"][-1]
        self.assertEqual(last["action"], "STOP")
        self.assertTrue(all(v == 0 for v in last["units"].values()))
        self.assertTrue(F.report(st)["checks"]["4_loss_limit_held"]["pass"])

    def test_non_points_swap_refused(self):
        bad = {p: {**t, "swap_mode": 2} for p, t in TERMS.items()}
        F.add_snapshot(bad, now=self.t0, path=self.ledger)
        with self.assertRaises(F.ForwardTestError):
            F.replay(F.read_ledger(self.ledger), _closes(), through=pd.Timestamp("2026-10-01", tz="UTC"))
