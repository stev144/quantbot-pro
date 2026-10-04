# claude code changed: new file — 2026-10-03, owner decision D-7 (verdict ladder replaces SUPPORTED).
import itertools

from django.test import SimpleTestCase

from bot.quant_core.verdict_ladder import LEGACY_POSITIVE_VERDICTS, LEVELS, LadderEvidence, ladder_level
from bot.research_lab import evidence_interface as ei
from bot.research_lab.verdict import compute_verdict, compute_verdict_conditional, compute_verdict_pairs

ALL_TRUE = {f: True for f in LadderEvidence.__dataclass_fields__}


class LadderTests(SimpleTestCase):
    def test_each_level_requires_every_level_below(self):
        self.assertEqual(ladder_level(LadderEvidence(**ALL_TRUE))[0], "CANONICAL")
        self.assertEqual(ladder_level(LadderEvidence())[0], None)
        # STATISTICAL failed ⇒ even perfect economics/holdout cannot lift the verdict above OBSERVATION
        e = dict(ALL_TRUE, family_confirmed=False)
        level, reasons = ladder_level(LadderEvidence(**e))
        self.assertEqual(level, "OBSERVATION")
        self.assertIn("failed: family_confirmed", reasons[0])

    def test_missing_input_caps_and_is_never_assumed(self):
        e = dict(ALL_TRUE, cost_complete=None)
        level, reasons = ladder_level(LadderEvidence(**e))
        self.assertEqual(level, "STATISTICAL")
        self.assertIn("missing input", reasons[0])
        self.assertIn("cost_complete", reasons[0])

    def test_levels_order(self):
        self.assertEqual(LEVELS, ("OBSERVATION", "STATISTICAL", "ECONOMIC", "TRADEABLE", "CANONICAL"))


class ResearchLabVerdictTests(SimpleTestCase):
    def test_strong_ic_that_survives_fdr_is_an_observation_not_supported(self):
        stat = {"sample_size": 5000, "ic": 0.09, "block_permutation_p_value": 0.001}
        v = compute_verdict(stat, {"passes_fdr": True})
        self.assertEqual(v.verdict, "OBSERVATION")
        self.assertTrue(any("family_confirmed" in line for line in v.explanation))

    def test_no_research_lab_path_can_produce_a_legacy_verdict(self):
        produced = set()
        for ic, p, fdr, d in itertools.product((0.0, 0.015, 0.03, 0.06, 0.09, -0.09), (0.001, 0.03, 0.2),
                                               (True, False, None), (None, "positive", "negative")):
            produced.add(compute_verdict({"sample_size": 5000, "ic": ic, "block_permutation_p_value": p},
                                         {"passes_fdr": fdr} if fdr is not None else None, d).verdict)
        for diff, p in itertools.product((-0.01, 0.0, 0.01), (0.001, 0.03, 0.2)):
            produced.add(compute_verdict_conditional({"metric": "conditional_event_test", "n_condition_true": 500,
                                                      "n_condition_false": 500, "observed_diff": diff,
                                                      "block_permutation_p_value": p, "mean_return_when_true": 0.001,
                                                      "mean_return_when_false": 0.0}).verdict)
        for coint, filt in itertools.product((True, False), (True, False)):
            produced.add(compute_verdict_pairs({"is_cointegrated": coint, "passes_filters": filt, "coint_pvalue": 0.01,
                                                "half_life_time": 20, "half_life_time_unit": "hours"}).verdict)
        self.assertFalse(produced & set(LEGACY_POSITIVE_VERDICTS), produced)
        self.assertIn("OBSERVATION", produced)
        self.assertFalse(produced & {"STATISTICAL", "ECONOMIC", "TRADEABLE", "CANONICAL"})   # unreachable without a D-3 family etc.


class EvidenceInterfaceTests(SimpleTestCase):
    def test_survived_means_statistical_or_higher(self):
        self.assertEqual(ei.SURVIVED_VERDICTS, ("STATISTICAL", "ECONOMIC", "TRADEABLE", "CANONICAL"))
        self.assertNotIn("OBSERVATION", ei.SURVIVED_VERDICTS)
        for legacy in LEGACY_POSITIVE_VERDICTS:
            self.assertNotIn(legacy, ei.SURVIVED_VERDICTS)
