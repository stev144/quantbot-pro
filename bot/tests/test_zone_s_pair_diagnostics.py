# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/test_zone_s_pair_diagnostics.py
# claude code changed: new file — 2026-10-07 zone-S-only pair diagnostics: the zone cut can never let a zone-V row
# through, folds lie inside S, and the signed 3× pre-screen rule has known answers.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.quant_core.research_zones import SELECTION, ZONES
from bot.research import zone_s_pair_diagnostics as z


class ZoneDisciplineTests(SimpleTestCase):

    def test_cut_drops_every_row_after_s1(self):
        idx = pd.date_range(ZONES.S1 - pd.Timedelta(hours=3), ZONES.V0 + pd.Timedelta(days=30), freq="h", tz="UTC")
        s = z.cut_to_zone_s(pd.Series(1.0, index=idx), "x")
        self.assertEqual(s.index.max(), ZONES.S1)
        self.assertEqual(len(s), 4)

    def test_real_loader_never_returns_zone_v(self):
        s = z.load_close("FIL_USDT")
        self.assertLessEqual(s.index.max(), ZONES.S1)
        f = z.load_funding("FIL_USDT")
        self.assertLessEqual(f.index.max(), ZONES.S1)

    def test_quarter_folds_lie_inside_zone_s(self):
        e = z.quarter_edges()
        self.assertEqual((e[0], e[-1]), (ZONES.S0, ZONES.S1 + pd.Timedelta(hours=1)))
        for a, b in zip(e, e[1:]):
            self.assertEqual({ZONES.classify(a), ZONES.classify(b - pd.Timedelta(hours=1))}, {SELECTION})


class PrescreenRuleTests(SimpleTestCase):

    def test_three_times_cost_known_answers(self):
        self.assertEqual(z.prescreen(0.0091, 0.003), "MEETS_3X")       # just above 3× (an exact-3× case is a float
        #                                                                artifact: 3 * 0.003 = 0.009000000000000001)
        self.assertEqual(z.prescreen(0.008, 0.002), "MEETS_3X")
        self.assertEqual(z.prescreen(0.0089, 0.003), "FAILS_3X")
        self.assertEqual(z.prescreen(-0.001, 0.003), "FAILS_3X")
        self.assertEqual(z.prescreen(0.01, float("nan")), "COST_UNAVAILABLE")
        self.assertEqual(z.prescreen(None, 0.003), "COST_UNAVAILABLE")

    def test_unfinanced_trades_are_never_priced_at_zero(self):
        d = pd.DataFrame({"gross": [0.01, 0.02], "trading_cost": [0.001, 0.001], "financed": [True, False],
                          "financing": [0.0005, np.nan], "net_pnl_pct": [0.0085, 0.019]})
        s = z._summ(d)
        self.assertEqual((s["n_financing_priced"], s["n_financing_unavailable"]), (1, 1))
        self.assertAlmostEqual(s["mean_full_cost_priced"], 0.0015)     # only the financed trade carries full cost
