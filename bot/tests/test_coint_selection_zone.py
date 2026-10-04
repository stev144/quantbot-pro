# claude code changed: new file — 2026-10-04, canonical cointegration selection in zone S (D-8 + D-3).
import numpy as np
import pandas as pd
from bot.tests.synthetic_data import cointegrated_log_pair   # claude code changed: 2026-10-04 — shared generator (bot/tests/synthetic_data.py), identical draws
from django.test import SimpleTestCase

from bot.quant_core.research_zones import HoldoutSealedError, ZONES
from bot.research.cointegration_engine import pair_test_in_selection_zone, run_selection_zone_screen

START = ZONES.init_window()[0] - pd.Timedelta(hours=48)       # a little pre-init history
END = pd.Timestamp("2024-06-30 23:00", tz="UTC")                 # zone S plus part of zone V


def _universe(seed=0):
    idx = pd.date_range(START, END, freq="h")
    n = len(idx)
    rng = np.random.default_rng(seed)
    la, lb = cointegrated_log_pair(n, rng, phi=0.9)     # half-life ≈ 6.6 candles (inside 2–120); rng keeps drawing below   # claude code changed: 2026-10-04 — shared generator (bot/tests/synthetic_data.py), identical draws
    s = lambda x: pd.Series(np.exp(x), index=idx)
    return {"A_USDT": s(la), "B_USDT": s(lb),
            "C_USDT": s(np.cumsum(0.01 * rng.standard_normal(n)) + 3.0),
            "D_USDT": s(np.cumsum(0.01 * rng.standard_normal(n)) + 3.0)}


class ZoneSOnlyTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.u = _universe()

    def test_validation_zone_and_pre_init_prices_cannot_change_the_selection_result(self):
        a, b = self.u["A_USDT"], self.u["B_USDT"]
        base = pair_test_in_selection_zone("A_USDT", "B_USDT", a, b)
        a2 = a.copy()
        a2[a2.index > ZONES.S1] *= np.linspace(1, 5, int((a2.index > ZONES.S1).sum()))   # wreck zone V
        a2[a2.index < ZONES.init_window()[0]] *= 3.0                                      # and pre-init history
        moved = pair_test_in_selection_zone("A_USDT", "B_USDT", a2, b)
        for k in ("coint_pvalue", "hedge_ratio", "intercept", "half_life_candles", "passes_filters", "n_zone_s_candles"):
            self.assertEqual(base[k], moved[k], k)
        self.assertEqual((base["status"], base["selection_basis"]), ("OK", "ZONE_S_D8"))

    def test_sealed_holdout_candles_are_refused(self):
        a = self.u["A_USDT"].copy()
        a.loc[ZONES.F] = 1.0
        with self.assertRaises(HoldoutSealedError):
            pair_test_in_selection_zone("A_USDT", "B_USDT", a, self.u["B_USDT"])

    def test_partial_zone_coverage_and_large_gaps_are_untestable_never_filled(self):
        a, b = self.u["A_USDT"], self.u["B_USDT"]
        late = pair_test_in_selection_zone("A_USDT", "B_USDT", a[a.index >= ZONES.S0 + pd.Timedelta(days=10)], b)
        self.assertEqual(late["status"], "UNTESTABLE")
        self.assertIn("does not cover zone S", late["reject_reason"])
        hole = a.drop(a.index[(a.index > ZONES.S0 + pd.Timedelta(days=100)) & (a.index <= ZONES.S0 + pd.Timedelta(days=100, hours=5))])
        gap = pair_test_in_selection_zone("A_USDT", "B_USDT", hole, b)
        self.assertEqual(gap["status"], "UNTESTABLE")
        self.assertIn("gap policy", gap["reject_reason"])

    def test_short_history_pair_is_not_seed_eligible(self):
        a, b = self.u["A_USDT"], self.u["B_USDT"]
        short = a[a.index >= ZONES.S0 - pd.Timedelta(hours=500)]          # < L = 1000 candles before S
        r = pair_test_in_selection_zone("A_USDT", "B_USDT", short, b)
        self.assertFalse(r["seed_eligible"])
        self.assertIn("no canonical seed", r["seed_reason"])


class ScreenTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.res = run_selection_zone_screen(_universe())

    def test_screen_is_exploratory_and_nominates_the_true_pair_only(self):
        r = self.res
        self.assertFalse(r["claimable"])
        self.assertIn("NOT CLAIMABLE", r["label"])
        self.assertEqual(r["n_requested"], 6)
        self.assertEqual(r["n_testable"], 6)                             # one family = every testable pair
        self.assertEqual(r["nominated"], ["A_USDT/B_USDT"])
        self.assertLessEqual(len(r["nominated"]), 5)
        unrelated = [p for p in r["pairs"] if p["pair_name"] in ("C_USDT/D_USDT",)]
        self.assertFalse(unrelated[0]["nominated"])
        self.assertTrue(all(p["selection_basis"] == "ZONE_S_D8" for p in r["pairs"]))

    def test_short_history_true_pair_is_never_nominated(self):
        u = _universe()
        u["A_USDT"] = u["A_USDT"][u["A_USDT"].index >= ZONES.S0 - pd.Timedelta(hours=500)]
        r = run_selection_zone_screen(u, pairs=[("A_USDT", "B_USDT"), ("C_USDT", "D_USDT")])
        self.assertEqual(r["nominated"], [])
