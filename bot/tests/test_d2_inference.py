# claude code changed: new file — 2026-10-03, owner decisions D-2 / D-10 (block permutation is the confirmatory
# test; block = max(4h, 24); HAC lag 2h descriptive; IID never decides; B = 999 per D-3).
import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.quant_core import inference as inf
from bot.research.inference_calibration import _sample


class RuleTests(SimpleTestCase):
    def test_d10_block_and_lag_rules(self):
        self.assertEqual([inf.d10_block_length(h) for h in (1, 4, 6, 7, 24)], [24, 24, 24, 28, 96])
        self.assertEqual([inf.d10_hac_lag(h) for h in (1, 4, 24)], [2, 8, 48])
        self.assertEqual(inf.d10_sensitivity_blocks(4), (8, 32))
        self.assertEqual(inf.CONFIRMATORY_B, 999)
        for bad in (0, -1, 2.5):
            with self.assertRaises(ValueError):
                inf.d10_block_length(bad)

    def test_horizon_parser_fails_closed(self):
        self.assertEqual(inf.horizon_from_label("forward_return_24h"), 24)
        with self.assertRaises(ValueError):
            inf.horizon_from_label("forward_return")


class ConfirmatoryTestTests(SimpleTestCase):
    def test_decision_is_block_permutation_and_iid_hac_are_descriptive(self):
        x, y = _sample(4, 0.9, np.random.default_rng(3), 0.0)
        r = inf.confirmatory_ic_test(x, y, 4, seed=11, n_perm=199)
        self.assertEqual((r["status"], r["basis"], r["block"], r["n_perm"]), ("OK", "BLOCK_PERMUTATION_D2", 24, 199))
        self.assertEqual(r["iid_basis"], inf.IID_PVALUE_BASIS)
        self.assertEqual(r["hac_basis"], inf.HAC_BASIS)
        self.assertEqual(r["p_value"], inf.block_permutation_p(x, y, 24, 199, np.random.default_rng(11)))
        self.assertEqual(r, inf.confirmatory_ic_test(x, y, 4, seed=11, n_perm=199))     # reproducible from the seed

    def test_untestable_is_never_given_a_substitute_p(self):
        r = inf.confirmatory_ic_test(np.arange(40.0), np.arange(40.0), 24, seed=0)      # < 2 blocks of 96
        self.assertEqual(r["status"], "UNTESTABLE")
        self.assertIsNone(r["p_value"])

    def test_iid_p_can_scream_while_the_dependence_aware_p_does_not(self):
        # TRUE NULL (ic = 0), h = 24 overlapping labels, persistent feature: the IID p is ~1e-5, the block p is not.
        x, y = _sample(24, 0.99, np.random.default_rng(1), 0.0)
        r = inf.confirmatory_ic_test(x, y, 24, seed=1, n_perm=199)
        self.assertLess(r["p_iid"], 1e-3)
        self.assertGreater(r["p_value"], 0.2)


class RegimeWiringTests(SimpleTestCase):
    def _panel(self):
        x, y = _sample(24, 0.99, np.random.default_rng(1), 0.0)
        idx = pd.date_range("2024-01-01", periods=len(x), freq="h", tz="UTC")
        reg = np.where((np.arange(len(x)) // 500) % 2 == 0, "A", "B")
        return pd.DataFrame({"f": x, "forward_return_24h": y, "reg": reg}, index=idx)

    def test_family_correction_runs_on_block_p_not_iid_p(self):
        from bot.research.regime_conditional_ic import compute_regime_conditional_ic
        rep = compute_regime_conditional_ic(self._panel(), "f", "forward_return_24h", ["reg"], n_perm=199, seed=0)
        overall = rep.cell("OVERALL", "ALL")
        self.assertLess(overall.ic_pvalue, 1e-3)                  # IID would have called it significant
        self.assertGreater(overall.ic_pvalue_block, 0.05)        # the decision p does not
        self.assertFalse(overall.passes_fdr)
        self.assertIn("block-permutation", rep.fdr_family_definition)
        self.assertEqual(overall.to_dict()["decision_pvalue_basis"], "BLOCK_PERMUTATION_D2")

    def test_regime_ic_refuses_an_unparseable_horizon(self):
        from bot.research.regime_conditional_ic import compute_regime_conditional_ic
        df = self._panel().rename(columns={"forward_return_24h": "label"})
        with self.assertRaises(ValueError):
            compute_regime_conditional_ic(df, "f", "label", ["reg"], n_perm=19)

    def test_interaction_decision_is_freedman_lane_block(self):
        from bot.research.regime_conditional_ic import test_regime_interaction
        r = test_regime_interaction(self._panel(), "f", "forward_return_24h", "reg", n_perm=99, seed=4)
        self.assertEqual((r["status"], r["pvalue_basis"], r["block"], r["n_perm"]), ("OK", "FREEDMAN_LANE_BLOCK_PERMUTATION_D2", 96, 99))
        self.assertIn("p_value_iid_anova", r)
        self.assertEqual(r["interaction_significant"], r["p_value"] < 0.05)
        self.assertEqual(r, test_regime_interaction(self._panel(), "f", "forward_return_24h", "reg", n_perm=99, seed=4))


class EngineWiringTests(SimpleTestCase):
    def test_feature_validator_uses_d10_block_and_b999(self):
        from unittest import mock
        from bot.research import feature_validator as fv
        self.assertEqual(fv.BLOCK_PERMUTATION_N, 999)
        seen = {}
        orig = fv.FeatureValidator._block_permutation_pvalue
        def spy(self, *a, **k):
            seen["block"] = k.get("block_size"); return 0.5
        rng = np.random.default_rng(0)
        n = 600
        df = pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"),
                           "feat": rng.standard_normal(n), "forward_return_4h": rng.standard_normal(n)})
        with mock.patch.object(fv.FeatureValidator, "_block_permutation_pvalue", spy):
            try:
                fv.FeatureValidator(min_observations=30).validate_all_features_raw(df, forward_return_col="forward_return_4h")
            except Exception:
                pass
        self.assertEqual(seen.get("block"), 24)                   # max(4·4, 24)

    def test_research_lab_conditional_tool_constants(self):
        from bot.research_lab.tools import conditional_tools as ct
        self.assertEqual(ct.BLOCK_PERMUTATION_N, 999)
