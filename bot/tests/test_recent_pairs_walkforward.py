# bot/tests/test_recent_pairs_walkforward.py  # claude code changed: 2026-10-27 — new: Stage 2 walk-forward engine
import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research import recent_pairs_walkforward as wf

T = pd.Timestamp("2025-06-01", tz="UTC")


def _pair(n_before=24 * 365, n_month=24 * 30, hl=10.0, shift_at=None, seed=1, beta=1.2):
    """Hourly closes of a planted cointegrated pair from T − 12 months to the end of the month starting at T.
    shift_at: hours into the trading month after which the spread jumps permanently by 15 standard deviations."""
    rng = np.random.default_rng(seed)
    n = n_before + n_month
    idx = pd.date_range(T - pd.Timedelta(hours=n_before), periods=n, freq="h", tz="UTC")
    lb = np.log(50) + np.cumsum(rng.normal(0, 0.004, n))
    phi, s = 0.5 ** (1 / hl), [0.0]
    for _ in range(n - 1):
        s.append(phi * s[-1] + rng.normal(0, 0.003))
    s = np.array(s)
    if shift_at is not None:
        s[n_before + shift_at:] += 15 * s[:n_before].std()
    la = 0.3 + beta * lb + s
    return pd.Series(np.exp(la), idx), pd.Series(np.exp(lb), idx)


def _sel(pa, pb):
    r, d = wf.eg_fixed(np.log(pa.loc[:T - wf.HOUR]), np.log(pb.loc[:T - wf.HOUR]))
    resid = d.iloc[:, 0] - r.alpha - r.beta * d.iloc[:, 1]
    return r, {"alpha": r.alpha, "beta": r.beta, "mu": float(resid.mean()), "sd": float(resid.std(ddof=1)),
               "half_life_h": 10.0}


class ZeroCost:
    def settle(self, a, b, e, t_exit, pa_x, pb_x, reason):
        da = e["dir"]
        db = -da * (1 if e["beta"] >= 0 else -1)
        gross = da * 1000 * (pa_x / e["pa"] - 1) + db * 1000 * abs(e["beta"]) * (pb_x / e["pb"] - 1)
        return wf._trade_row(a, b, e, t_exit, reason, gross, 0.0, 0.0, pa_x, pb_x)


class SelectionAndTradingTest(SimpleTestCase):
    def test_planted_pair_is_detected_and_static_variant_earns_gross(self):
        pa, pb = _pair()
        r, sel = _sel(pa, pb)
        self.assertLess(r.pvalue, 1e-6)
        self.assertAlmostEqual(r.beta, 1.2, delta=0.05)
        res = wf.trade_pair("A", "B", pa, pb, sel, T, "S", ZeroCost())
        self.assertGreaterEqual(len(res["trades"]), 2)                    # |z| >= 2 is rare (~1-5 % of candles) and
        self.assertTrue(all(t["reason"] == "z_cross" for t in res["trades"]))   # each trade waits for the cross
        self.assertTrue(all(t["gross"] > 0 for t in res["trades"]))       # a true mean-reverting spread pays gross
        self.assertFalse(res["dropped"])

    def test_break_rule_closes_and_drops_a_broken_pair(self):
        pa, pb = _pair(shift_at=24 * 5)
        _, sel = _sel(pa, pb)
        res = wf.trade_pair("A", "B", pa, pb, sel, T, "S", ZeroCost())
        self.assertTrue(res["dropped"])
        self.assertGreater(res["breaks"]["sigma_hl"] + res["breaks"]["eg_p"], 0)
        last_exit = max(pd.Timestamp(t["exit"]) for t in res["trades"]) if res["trades"] else T
        self.assertLess(last_exit, T + pd.Timedelta(days=8))              # nothing traded after the break

    def test_kalman_signal_has_no_look_ahead(self):
        pa, pb = _pair()
        _, sel = _sel(pa, pb)
        cut = T + pd.Timedelta(hours=200)
        pa2 = pa.copy()
        pa2.loc[cut:] *= 1.5                                             # change only the future
        r1 = wf.trade_pair("A", "B", pa, pb, sel, T, "K", ZeroCost())
        r2 = wf.trade_pair("A", "B", pa2, pb, sel, T, "K", ZeroCost())
        before = lambda r: [(t["entry"], t["dir"]) for t in r["trades"] if pd.Timestamp(t["entry"]) < cut]
        self.assertEqual(before(r1), before(r2))

    def test_weekly_check_runs_even_when_its_hour_is_a_weekend(self):
        pa, pb = _pair()
        keep = ~((pa.index >= T) & (pa.index.dayofweek >= 5))              # forex-like: no weekend candles in the month
        _, sel = _sel(pa, pb)
        res = wf.trade_pair("A", "B", pa[keep], pb[keep], sel, T, "S", ZeroCost())
        self.assertEqual(res["breaks"]["checks_run"], 4)


class KalmanLevelTest(SimpleTestCase):   # claude code changed: 2026-10-28 — design v2 variant L
    def test_level_variant_equals_static_when_beta_cannot_move(self):
        pa, pb = _pair()
        _, sel = _sel(pa, pb)
        old = wf.LEVEL_SLOWDOWN
        try:
            wf.LEVEL_SLOWDOWN = float("inf")                              # g = 0 -> Q = 0 -> beta frozen at beta_hat
            rl = wf.trade_pair("A", "B", pa, pb, sel, T, "L", ZeroCost())
        finally:
            wf.LEVEL_SLOWDOWN = old
        rs = wf.trade_pair("A", "B", pa, pb, sel, T, "S", ZeroCost())
        key = lambda r: [(t["entry"], t["exit"], t["dir"], round(t["gross"], 9)) for t in r["trades"]]
        self.assertTrue(rs["trades"])
        self.assertEqual(key(rl), key(rs))

    def test_level_signal_is_not_absorbed_on_a_planted_pair(self):
        pa, pb = _pair()
        _, sel = _sel(pa, pb)
        res = wf.trade_pair("A", "B", pa, pb, sel, T, "L", ZeroCost())
        self.assertGreaterEqual(len(res["trades"]), 2)                    # stage 2's K made ~0 trades here
        self.assertGreater(res["frac_z2"]["L"], 0.5 * res["frac_z2"]["S"])
        self.assertLess(abs(res["beta_drift_L"]), 0.05)

    def test_level_signal_has_no_look_ahead(self):
        pa, pb = _pair()
        _, sel = _sel(pa, pb)
        cut = T + pd.Timedelta(hours=500)   # claude code changed: 2026-10-28 — late enough that trades precede it
        pa2 = pa.copy()
        pa2.loc[cut:] *= 1.5
        r1 = wf.trade_pair("A", "B", pa, pb, sel, T, "L", ZeroCost())
        r2 = wf.trade_pair("A", "B", pa2, pb, sel, T, "L", ZeroCost())
        before = lambda r: [(t["entry"], t["dir"], t["beta"]) for t in r["trades"] if pd.Timestamp(t["entry"]) < cut]
        self.assertTrue(before(r1))
        self.assertEqual(before(r1), before(r2))


class CostTest(SimpleTestCase):
    def test_crypto_costs_and_funding_known_answer(self):
        t0, t1 = pd.Timestamp("2025-06-02", tz="UTC"), pd.Timestamp("2025-06-03", tz="UTC")
        funding = {"A": pd.Series([0.0001, 0.0001, 0.0001], pd.date_range(t0 + pd.Timedelta(hours=8), periods=3, freq="8h")),
                   "B": pd.Series([0.0002], [t0 + pd.Timedelta(hours=8)])}
        e = {"t": t0, "pa": 100.0, "pb": 50.0, "dir": 1, "beta": 0.5, "variant": "S"}   # long A $1000, short B $500
        row = wf.CryptoCost(funding).settle("A", "B", e, t1, 101.0, 50.0, "z_cross")
        self.assertAlmostEqual(row["gross"], 10.0)
        self.assertAlmostEqual(row["fees"], 2 * 1500 * 0.001)                    # fee + slippage, both sides, both legs
        self.assertAlmostEqual(row["carry"], -1000 * 0.0003 + 500 * 0.0002)      # long pays A's rate, short receives B's
        self.assertAlmostEqual(row["net"], 10.0 - 3.0 - 0.2)

    def test_forex_spread_and_swap_known_answer(self):
        snap = {"pairs": {"A": {"point": 0.0001, "h1_spread_mean_pts_30d": 10, "swap_mode": 1, "swap_long": -2.0, "swap_short": 1.0},
                          "B": {"point": 0.01, "h1_spread_mean_pts_30d": 20, "swap_mode": 1, "swap_long": -5.0, "swap_short": -3.0}}}
        t0 = pd.Timestamp("2025-06-02", tz="UTC")
        e = {"t": t0, "pa": 1.0, "pb": 100.0, "dir": 1, "beta": 1.0, "variant": "S"}
        row = wf.ForexCost(snap).settle("A", "B", e, t0 + pd.Timedelta(days=2), 1.0, 100.0, "z_cross")
        self.assertAlmostEqual(row["fees"], 2 * 1000 * (5 * 0.0001 / 1.0) + 2 * 1000 * (10 * 0.01 / 100.0))
        self.assertAlmostEqual(row["carry"], 1000 * 2 * (-2.0 * 0.0001 / 1.0) + 1000 * 2 * (-3.0 * 0.01 / 100.0))
        self.assertTrue(row["cost_complete"])

    def test_monthly_tests_known_answer(self):
        out = wf._monthly_tests([1.0, 2.0, 3.0, -1.0])
        self.assertAlmostEqual(out["mean"], 1.25)
        self.assertEqual(out["months_positive"], 3)
        self.assertLess(out["t_p_one_sided"], 0.5)
