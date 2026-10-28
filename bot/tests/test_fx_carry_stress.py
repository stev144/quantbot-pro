# bot/tests/test_fx_carry_stress.py  # claude code changed: 2026-10-28 — new
"""Known-answer tests for the carry stress test (synthetic data, no network)."""
import math

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research import fx_carry_stress as S


class StressMathTest(SimpleTestCase):
    def test_usd_notional_return(self):
        c = pd.Series([100.0, 110.0], index=pd.date_range("2024-01-01", periods=2, tz="UTC"))
        self.assertAlmostEqual(S.price_return_usd_notional(c).iloc[0], 1 - 100 / 110)   # USD/XXX up 10% → +9.09% in USD

    def test_daily_carry_uses_rate_known_the_day_before_and_calendar_days(self):
        idx = pd.DatetimeIndex(["2024-01-05", "2024-01-08"], tz="UTC")                   # Fri → Mon: 3 calendar days
        usd = pd.Series([5.0], index=pd.DatetimeIndex(["2024-01-01"], tz="UTC"))
        q = pd.Series([0.0], index=pd.DatetimeIndex(["2024-01-01"], tz="UTC"))
        c = S.daily_carry(idx, usd, q, markup_pct_yr=1.0)
        self.assertEqual(c.iloc[0], 0.0)
        self.assertAlmostEqual(c.iloc[1], 0.04 * 3 / 365)

    def test_sizing_hits_target_vol_and_is_causal(self):
        rng = np.random.default_rng(0)
        idx = pd.bdate_range("2023-01-02", periods=400, tz="UTC")
        closes = {p: pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.005, 400))), index=idx) for p in S.PAIRS}
        flat = pd.Series([0.0], index=pd.DatetimeIndex(["2020-01-01"], tz="UTC"))
        rates = {S.USD_RATE_SERIES: flat, **{v: flat for v in S.QUOTE_RATE_SERIES.values()}}
        cfg = S.StressConfig(target_vol=0.10, markups_pct_yr={p: 0.0 for p in S.PAIRS}, spreads_pct={p: 0.0 for p in S.PAIRS})
        res = S.run_stress(closes, rates, cfg)
        realised = res["daily"].std(ddof=1) * math.sqrt(S.TRADING_DAYS)
        self.assertAlmostEqual(realised, 0.10, delta=0.02)
        # changing prices AFTER a rebalance date cannot change that date's weights
        d = res["weights"].index[100]
        closes2 = {p: s.copy() for p, s in closes.items()}
        for p in S.PAIRS:
            closes2[p].loc[d:] *= 1.3
        w2 = S.run_stress(closes2, rates, cfg)["weights"]
        self.assertTrue(np.allclose(res["weights"].loc[d].to_numpy(), w2.loc[d].to_numpy()))

    def test_max_drawdown(self):
        eq = pd.Series([1.0, 1.2, 0.9, 1.3], index=pd.date_range("2024-01-01", periods=4, tz="UTC"))
        m = S.max_drawdown(eq)
        self.assertAlmostEqual(m["max_dd_pct"], -25.0)
        self.assertEqual(m["recovered"], "2024-01-04")
