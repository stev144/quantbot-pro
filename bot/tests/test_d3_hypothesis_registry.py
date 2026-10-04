# claude code changed: new file — 2026-10-03, owner decision D-3 (families, pre-registration, Holm, two-stage scans).
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from django.test import SimpleTestCase
from statsmodels.stats.multitest import multipletests

from bot.quant_core.hypothesis_registry import (
    EXPLORATORY_LABEL, Hypothesis, HypothesisRegistry, RegistryError, exploratory_screen, holm_adjust,
)

V_TS = pd.date_range("2024-05-01", periods=200, freq="h", tz="UTC")       # zone V
S_TS = pd.date_range("2022-01-01", periods=200, freq="h", tz="UTC")       # zone S
WHY = "Funding-rate extremes reflect crowded leverage that tends to unwind."


def H(i, feature="f{}", program="crypto_carry", quarter="2026Q4", horizon=24):
    return Hypothesis(f"H{i}", program, quarter, f"statement {i}", WHY, "NET_MEAN_RETURN_PER_TRADE",
                      feature.format(i), horizon)


class HolmTests(SimpleTestCase):
    def test_matches_statsmodels_holm(self):
        for p in ([0.01, 0.04, 0.03, 0.005, 0.2], [0.5], [0.0001, 0.0001, 0.9]):
            np.testing.assert_allclose(holm_adjust(p), multipletests(p, method="holm")[1], atol=1e-15)

    def test_refuses_invalid_p(self):
        for bad in ([], [1.2], [np.nan]):
            with self.assertRaises(RegistryError):
                holm_adjust(bad)


class RegistryTests(SimpleTestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.reg = HypothesisRegistry(self.d / "reg.json")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_validation_of_a_hypothesis(self):
        with self.assertRaisesRegex(RegistryError, "economic rationale"):
            Hypothesis("X", "p", "2026Q4", "s", "because", "IC", "f", 4)
        with self.assertRaisesRegex(RegistryError, "quarter"):
            Hypothesis("X", "p", "Q4-2026", "s", WHY, "IC", "f", 4)
        with self.assertRaisesRegex(RegistryError, "endpoint"):
            Hypothesis("X", "p", "2026Q4", "s", WHY, "SHARPE", "f", 4)

    def test_at_most_five_primary_hypotheses_per_family(self):
        for i in range(5):
            self.reg.register(H(i))
        with self.assertRaisesRegex(RegistryError, "5 primary"):
            self.reg.register(H(5))
        self.reg.register(H(6, quarter="2027Q1"))                   # a new quarter is a new family

    def test_duplicate_atr_counts_once(self):
        self.reg.register(H(1, feature="atr"))
        with self.assertRaisesRegex(RegistryError, "duplicates"):
            self.reg.register(H(2, feature="atr_indicator"))

    def _results(self, ids, p=0.001, n_perm=999, ts=V_TS):
        return {i: {"p_value": p, "n_perm": n_perm, "timestamps": ts} for i in ids}

    def test_confirm_applies_holm_records_and_closes_the_family(self):
        for i in range(3):
            self.reg.register(H(i))
        res = {"H0": {"p_value": 0.004, "n_perm": 999, "timestamps": V_TS},
               "H1": {"p_value": 0.03, "n_perm": 999, "timestamps": V_TS},
               "H2": {"p_value": 0.2, "n_perm": 999, "timestamps": V_TS}}
        ev = self.reg.confirm("crypto_carry", "2026Q4", res, run_id="run-1")
        self.assertEqual({k: v["reject_h0"] for k, v in ev["decision"].items()}, {"H0": True, "H1": False, "H2": False})
        self.assertAlmostEqual(ev["decision"]["H0"]["p_holm"], 0.012)
        with self.assertRaisesRegex(RegistryError, "CLOSED"):
            self.reg.register(H(9))
        with self.assertRaisesRegex(RegistryError, "tested once"):
            self.reg.confirm("crypto_carry", "2026Q4", res, run_id="run-2")

    def test_confirm_refusals(self):
        for i in range(2):
            self.reg.register(H(i))
        with self.assertRaisesRegex(RegistryError, "exactly the registered family"):
            self.reg.confirm("crypto_carry", "2026Q4", self._results(["H0"]), run_id="r")          # dropped a hypothesis
        with self.assertRaisesRegex(RegistryError, "B = 999"):
            self.reg.confirm("crypto_carry", "2026Q4", self._results(["H0", "H1"], n_perm=100), run_id="r")
        with self.assertRaisesRegex(RegistryError, "zone V only"):
            self.reg.confirm("crypto_carry", "2026Q4", self._results(["H0", "H1"], ts=S_TS), run_id="r")
        holdout = pd.date_range("2026-10-04", periods=5, freq="h", tz="UTC")
        with self.assertRaisesRegex(RegistryError, "zone V only"):
            self.reg.confirm("crypto_carry", "2026Q4", self._results(["H0", "H1"], ts=holdout), run_id="r")
        with self.assertRaisesRegex(RegistryError, "register before testing"):
            self.reg.confirm("other", "2026Q4", {}, run_id="r")


class ExploratoryScreenTests(SimpleTestCase):
    def test_zone_s_only_and_never_claimable(self):
        p = {f"x{i}": v for i, v in enumerate([0.0001, 0.0002, 0.0003, 0.0004, 0.0005, 0.0006, 0.5])}
        out = exploratory_screen(p, S_TS)
        self.assertEqual(out["label"], EXPLORATORY_LABEL)
        self.assertFalse(out["claimable"])
        self.assertEqual(len(out["nominated"]), 5)                  # at most 5 go on to registration
        self.assertNotIn("x6", out["survivors"])
        with self.assertRaisesRegex(RegistryError, "zone S only"):
            exploratory_screen(p, V_TS)
