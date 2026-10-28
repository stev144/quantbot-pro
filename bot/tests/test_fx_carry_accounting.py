# bot/tests/test_fx_carry_accounting.py  # claude code changed: 2026-10-28 — new
"""Known-answer tests for the FX carry accounting (no MT5 needed: terms are constructed)."""
import math

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research import fx_carry_accounting as fca


def _terms(**over):
    base = dict(symbol="USD/JPY", mt5_symbol="USDJPY", point=0.001, digits=3, contract_size=100000.0, swap_mode=1,
                swap_long=19.0, swap_short=-24.0, triple_day=3, bid=149.990, ask=150.010, as_of="2026-10-28T00:00:00+00:00")
    base.update(over)
    return fca.BrokerTerms(**base)


class CarryRowTest(SimpleTestCase):
    def test_known_answer(self):
        r = fca.carry_row(_terms(), vol_ann_pct=10.0)
        mid = 150.0
        self.assertAlmostEqual(r["carry_long_pct_yr"], 19 * 0.001 / mid * 365 * 100)       # ≈ +4.62 %/yr
        self.assertAlmostEqual(r["carry_short_pct_yr"], -24 * 0.001 / mid * 365 * 100)
        self.assertAlmostEqual(r["round_trip_pct"], 0.02 / mid * 100)                       # one full spread
        self.assertEqual(r["paid_side"], "LONG")
        self.assertAlmostEqual(r["carry_to_vol_long"], r["carry_long_pct_yr"] / 10.0)
        self.assertAlmostEqual(r["days_to_3x_cost_long"], 3 * r["round_trip_pct"] / (r["carry_long_pct_yr"] / 365))
        self.assertIsNone(r["days_to_3x_cost_short"])                                       # negative carry never pays
        self.assertAlmostEqual(r["broker_markup_pct_yr"], -(r["carry_long_pct_yr"] + r["carry_short_pct_yr"]) / 2)

    def test_both_sides_charged(self):
        r = fca.carry_row(_terms(swap_long=-3.0, swap_short=-5.0), vol_ann_pct=8.0)
        self.assertEqual(r["paid_side"], "NONE")
        self.assertGreater(r["broker_markup_pct_yr"], 0)
        self.assertIsNone(r["best_carry_to_vol"])

    def test_unsupported_swap_unit_is_refused_not_converted(self):
        r = fca.carry_row(_terms(swap_mode=2), vol_ann_pct=10.0)
        self.assertIn("not POINTS", r["problem"])
        self.assertNotIn("carry_long_pct_yr", r)

    def test_bad_quote(self):
        self.assertIn("bad quote", fca.carry_row(_terms(bid=0.0), None)["problem"])

    def test_realised_vol_uses_daily_returns(self):
        idx = pd.date_range("2026-01-01", periods=24 * 90, freq="h", tz="UTC")
        daily_r = np.tile([0.01, -0.01], 45)
        close = pd.Series(np.repeat(np.exp(np.cumsum(daily_r)), 24), index=idx)
        v = fca.realised_vol_ann_pct(close)
        expect = np.std(np.tile([0.01, -0.01], 30), ddof=1) * math.sqrt(260) * 100
        self.assertAlmostEqual(v, expect, places=6)

    def test_hourly_closes_stop_at_the_fx_cutoff(self):
        from bot.research.fx_holdout_guard import FX_RESEARCH_DATA_CUTOFF
        c = fca.load_hourly_close("EUR/USD")
        self.assertLess(c.index[-1], pd.Timestamp(FX_RESEARCH_DATA_CUTOFF))
