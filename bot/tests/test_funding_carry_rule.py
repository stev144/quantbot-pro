# bot/tests/test_funding_carry_rule.py  # claude code changed: 2026-10-28 — new: funding carry cost-hurdle rule
"""Known-answer tests on synthetic funding (no files read)."""
import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research import funding_carry_rule as R


def _series(rates, price=100.0, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(rates), freq="8h", tz="UTC")
    r = pd.Series(rates, idx, dtype=float)
    p = pd.Series(price if np.ndim(price) else [price] * len(rates), idx, dtype=float)
    return r, p


class RuleTest(SimpleTestCase):
    def test_round_trip_cost_at_base_tier(self):
        self.assertAlmostEqual(R.RT, 0.005)                      # 2 × (0.10 % + 0.05 % + 2 × 0.05 %)

    def test_trailing_sum_is_time_based(self):
        r, _ = _series([0.001] * 30)
        self.assertAlmostEqual(R.trailing_sum(r).iloc[-1], 0.021)  # 21 settlements of 8 h in 7 days

    def test_enter_after_hurdle_exit_after_negative_known_answer(self):
        rates = [0.001] * 21 + [0.0005] * 10 + [-0.05] + [0.0] * 5
        r, p = _series(rates)
        res = R.run_rule(r, p)
        self.assertEqual(len(res["trades"]), 1)
        tr = res["trades"][0]
        self.assertEqual(tr["entry"], r.index[22])                # first FULL 7-day window at k = 21 (F7 = 0.021) → in from 22
        # funding received at k = 23..30 (0.0005 each) and k = 31 (−0.05, the decision to exit is taken at k = 31)
        expect = -R.RT / 2 + 8 * 0.0005 - 0.05 - R.RT / 2
        self.assertAlmostEqual(tr["net"], expect)
        self.assertEqual(tr["exit"], r.index[32])

    def test_no_entry_below_hurdle(self):
        r, p = _series([0.0001] * 60)                             # F7 = 0.0021 < 0.005
        self.assertEqual(R.run_rule(r, p)["trades"], [])

    def test_reset_rehedges_and_pays_a_round_trip(self):
        price = [100.0] * 25 + [160.0] * 10
        r, p = _series([0.001] * 35, price=price)
        res = R.run_rule(r, p, reset_runup=0.5)
        self.assertTrue(any(t.get("reset") for t in res["trades"]))
        self.assertEqual(len(res["trades"]), 2)                    # reset leg + leg still open at the end

    def test_liquidation_breach_flag(self):
        price = [100.0] * 25 + [210.0] * 10
        r, p = _series([0.001] * 35, price=price)
        tr = R.run_rule(r, p, high=p, leverage=1.0)["trades"][0]
        self.assertTrue(tr["liquidation_breach"])                  # 1× short liquidates near +99 %
