# claude code changed: new file — 2026-10-04, step 3: legacy pairs paths moved onto run_canonical (decision lock
# §2.3–§2.6, D-8, D-3, D-4, borrow row, D-7).
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import pandas as pd
from bot.tests.synthetic_data import cointegrated_pair   # claude code changed: 2026-10-04 — shared generator (bot/tests/synthetic_data.py), identical draws
from django.test import SimpleTestCase

from bot.quant_core.research_zones import HoldoutSealedError, ZONES
from bot.research.pairs_step17_null import zone_v_label_complete
from bot.research.walk_forward_engine import canonical_walk_forward

H = pd.Timedelta(hours=1)


def _tl(decisions, pct):
    return pd.DataFrame({"entry_decision_timestamp": pd.to_datetime(decisions, utc=True),
                         "net_pnl_pct": pct, "net_pnl_usdt": [p * 1000 for p in pct]})


class ZoneVEndpointTests(SimpleTestCase):
    def test_label_complete_zone_v_boundaries(self):
        last_ok = ZONES.V1 - 25 * H                                     # t ≤ V_end − (d + H)
        tl = _tl([ZONES.S1, ZONES.V0 - H, ZONES.V0, last_ok, last_ok + H], [1, 2, 3, 4, 5])
        self.assertEqual(zone_v_label_complete(tl)["net_pnl_pct"].tolist(), [3, 4])


class CanonicalWalkForwardTests(SimpleTestCase):
    def test_known_answer_with_label_truncation(self):
        e0, e1, e2 = ZONES.V0, ZONES.V0 + 100 * H, ZONES.V0 + 200 * H
        tl = _tl([e0, e1 - 26 * H, e1 - 25 * H, e1, e2 - 25 * H], [0.01, 0.02, 0.99, -0.01, 0.99])
        wf = canonical_walk_forward(tl, [e0, e1, e2])
        f0, f1 = wf["folds"]
        self.assertEqual((f0["n_trades"], f1["n_trades"]), (2, 1))       # decisions within 25 candles of a fold end dropped
        self.assertAlmostEqual(f0["net_mean_return_per_trade"], 0.015)
        self.assertAlmostEqual(f1["net_mean_return_per_trade"], -0.01)
        self.assertEqual((wf["n_trades_in_folds"], wf["n_trades_excluded"]), (3, 2))
        self.assertEqual(wf["status"], "DEVELOPMENT_EVIDENCE_NOT_CLAIM_ELIGIBLE")

    def test_refusals(self):
        tl = _tl([ZONES.V0], [0.01])
        with self.assertRaisesRegex(ValueError, "owner parameter"):
            canonical_walk_forward(tl, None)
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            canonical_walk_forward(tl, [ZONES.V0 + 10 * H, ZONES.V0])
        with self.assertRaisesRegex(ValueError, "zone S or entirely inside zone V"):
            canonical_walk_forward(tl, [ZONES.S1 - 100 * H, ZONES.V0 + 100 * H])     # spans the embargo


class PermutationLabelTests(SimpleTestCase):
    def test_legacy_null_declares_it_cannot_test_a_pairs_edge(self):
        from bot.research.permutation_test_engine import PermutationTestEngine
        real = {"win_rate": 0.6, "sharpe_ratio": 1.0, "profit_factor": 1.5, "entry_ic": 0.1, "total_trades": 50}
        shuf = [dict(real, win_rate=0.5, sharpe_ratio=0.1, profit_factor=1.0) for _ in range(20)]
        v = PermutationTestEngine(n_permutations=20)._compare(real, shuf)
        self.assertIn("F-05", v["null_validity"])
        self.assertIn("pairs_step17_null", v["null_validity"])


def _pair_closes(end="2024-06-30 23:00", seed=3):
    idx = pd.date_range(ZONES.init_window()[0], pd.Timestamp(end, tz="UTC"), freq="h")
    return cointegrated_pair(idx, seed)   # claude code changed: 2026-10-04 — shared generator (bot/tests/synthetic_data.py), identical draws


class CanonicalPairLadderTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from bot.research.cointegration_pipeline_runner import run_pair_canonical
        cls.a, cls.b = _pair_closes()
        cls.d = Path(tempfile.mkdtemp())
        fund = pd.DataFrame({"timestamp": pd.date_range("2021-09-01", "2024-12-31", freq="8h", tz="UTC"),
                             "funding_rate": 0.0001})
        for sym in ("X_USDT", "Y_USDT"):
            fund.to_csv(cls.d / f"{sym}_funding.csv", index=False)
        cls.res = run_pair_canonical("X_USDT", "Y_USDT", cls.a, cls.b, output_dir=str(cls.d / "out"), funding_dir=str(cls.d))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)
        super().tearDownClass()

    def test_every_stage_is_canonical_and_gated(self):
        st = self.res["stages"]
        self.assertEqual(st["kalman"]["seed_timestamp"], ZONES.S0.isoformat())
        self.assertEqual((st["lifecycle"]["provenance_status"], st["lifecycle"]["borrow_status"]), ("CANONICAL", "FUNDING_KNOWN"))
        self.assertEqual(st["step17_null"]["status"], "BLOCKED")               # calibration not yet run
        self.assertEqual(st["walk_forward"]["status"], "BLOCKED")              # fold boundaries not set
        self.assertIn(self.res["verdict"]["level"], (None, "OBSERVATION"))    # never above without a D-3 family
        reasons = " ".join(self.res["verdict"]["reasons"])
        if self.res["verdict"]["level"] is None:      # zone-V endpoint not positive (this synthetic pair: −0.21 %/trade)
            self.assertIn("effect_observed", reasons)
        else:                                            # an observed effect is still capped by the missing D-3 family / null
            self.assertTrue("family_confirmed" in reasons or "dependence_aware_test" in reasons)

    def test_missing_funding_stops_fail_closed(self):
        from bot.research.cointegration_pipeline_runner import run_pair_canonical
        d = Path(tempfile.mkdtemp())
        try:
            r = run_pair_canonical("X_USDT", "Y_USDT", self.a, self.b, output_dir=str(d), funding_dir=str(d), run_null=False)
            self.assertEqual((r["stopped_at"], r["stages"]["financing"]["status"]), ("financing", "BORROW_UNAVAILABLE"))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_sealed_holdout_input_is_refused(self):
        from bot.research.cointegration_pipeline_runner import run_pair_canonical
        a = self.a.copy()
        a.loc[ZONES.F] = 1.0
        with self.assertRaises(HoldoutSealedError):
            run_pair_canonical("X_USDT", "Y_USDT", a, self.b, output_dir=str(self.d / "x"), funding_dir=str(self.d))


class ResearchLabToolTests(SimpleTestCase):
    def _fake_load(self):
        a, b = _pair_closes()
        frames = {"X/USDT": pd.DataFrame({"close": a}), "Y/USDT": pd.DataFrame({"close": b})}
        def load(sym):
            return frames[sym]
        return load

    def test_kalman_tool_uses_the_canonical_seed_and_stops_at_zone_s_end(self):
        from bot.research_lab.tools import research_tools as rt
        with mock.patch.object(rt, "load_ohlcv", self._fake_load()):
            r = rt.run_kalman_pairs_test("X/USDT", "Y/USDT")
        obs = r["observation"]
        self.assertEqual(obs["seed_timestamp"], ZONES.S0.isoformat())
        self.assertLessEqual(pd.Timestamp(obs["as_of"]), ZONES.S1)
        self.assertEqual(r["statistical_evidence"]["selection_basis"], "ZONE_S_D8")
        self.assertTrue(r["config"]["canonical_defaults"])
        self.assertIn(r["verdict"], ("OBSERVATION", "REJECTED", "INCONCLUSIVE"))

    def test_cointegration_tool_is_zone_s_and_untestable_is_insufficient_not_rejected(self):
        from bot.research_lab.tools import research_tools as rt
        from bot.research_lab.verdict import compute_verdict_pairs
        with mock.patch.object(rt, "load_ohlcv", self._fake_load()):
            r = rt.run_cointegration_test("X/USDT", "Y/USDT")
        self.assertEqual(r["selection_basis"], "ZONE_S_D8")
        short = self._fake_load()
        frames = {"X/USDT": short("X/USDT").loc[ZONES.S0 + pd.Timedelta(days=30):], "Y/USDT": short("Y/USDT")}
        with mock.patch.object(rt, "load_ohlcv", lambda s: frames[s]):
            u = rt.run_cointegration_test("X/USDT", "Y/USDT")
        self.assertEqual(compute_verdict_pairs(u).verdict, "INSUFFICIENT_DATA")
