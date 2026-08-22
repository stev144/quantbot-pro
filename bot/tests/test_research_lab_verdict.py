# claude code changed: new file — Research Lab MVP, section 21: "Test
# every verdict state that is implemented." REGIME_DEPENDENT and
# SUPERSEDED_BY_EXISTING_RESEARCH are real taxonomy states but this MVP
# pass has no code path that ever reaches them (see verdict.py's own
# docstring + the final engineering report) — not tested here since
# nothing implements them yet.

from django.test import SimpleTestCase

from bot.research_lab.verdict import compute_verdict, MIN_SAMPLE_SIZE


def _stat(ic=0.08, block_p=0.01, sample_size=50000):
    return {"ic": ic, "block_permutation_p_value": block_p, "sample_size": sample_size}


class SupportedVerdictTest(SimpleTestCase):

    def test_strong_ic_significant_passes_fdr_is_supported(self):
        result = compute_verdict(_stat(ic=0.08, block_p=0.001), {"passes_fdr": True})
        self.assertEqual(result.verdict, "SUPPORTED")

    def test_matching_hypothesized_direction_still_supported(self):
        result = compute_verdict(_stat(ic=0.08, block_p=0.001), {"passes_fdr": True}, hypothesized_direction="positive")
        self.assertEqual(result.verdict, "SUPPORTED")


class PartiallySupportedVerdictTest(SimpleTestCase):

    def test_moderate_ic_significant_passes_fdr_is_partially_supported(self):
        result = compute_verdict(_stat(ic=0.035, block_p=0.01), {"passes_fdr": True})
        self.assertEqual(result.verdict, "PARTIALLY_SUPPORTED")


class InconclusiveVerdictTest(SimpleTestCase):

    def test_no_statistical_test_run_is_inconclusive(self):
        result = compute_verdict(None, None)
        self.assertEqual(result.verdict, "INCONCLUSIVE")

    def test_weak_ic_survives_fdr_but_below_moderate_is_inconclusive(self):
        result = compute_verdict(_stat(ic=0.015, block_p=0.01), {"passes_fdr": True})
        self.assertEqual(result.verdict, "INCONCLUSIVE")

    def test_fails_fdr_after_raw_significance_is_inconclusive(self):
        result = compute_verdict(_stat(ic=0.06, block_p=0.01), {"passes_fdr": False})
        self.assertEqual(result.verdict, "INCONCLUSIVE")

    def test_not_significant_but_nonzero_ic_is_inconclusive_not_rejected(self):
        result = compute_verdict(_stat(ic=0.02, block_p=0.5), None)
        self.assertEqual(result.verdict, "INCONCLUSIVE")


class RejectedVerdictTest(SimpleTestCase):

    def test_not_significant_and_near_zero_ic_is_rejected(self):
        result = compute_verdict(_stat(ic=0.001, block_p=0.8), None)
        self.assertEqual(result.verdict, "REJECTED")

    def test_direction_contradiction_is_rejected_even_if_significant(self):
        result = compute_verdict(_stat(ic=0.08, block_p=0.001), {"passes_fdr": True}, hypothesized_direction="negative")
        self.assertEqual(result.verdict, "REJECTED")
        self.assertTrue(any("claimed direction" in e for e in result.explanation))


class InvalidResearchVerdictTest(SimpleTestCase):

    def test_undersized_sample_is_invalid_research(self):
        result = compute_verdict(_stat(sample_size=MIN_SAMPLE_SIZE - 1), {"passes_fdr": True})
        self.assertEqual(result.verdict, "INVALID_RESEARCH")

    def test_missing_ic_value_is_invalid_research(self):
        result = compute_verdict({"ic": None, "sample_size": 50000}, None)
        self.assertEqual(result.verdict, "INVALID_RESEARCH")


class RequiresReviewVerdictTest(SimpleTestCase):

    def test_significant_but_no_fdr_correction_run_requires_review(self):
        result = compute_verdict(_stat(ic=0.08, block_p=0.001), None)
        self.assertEqual(result.verdict, "REQUIRES_REVIEW")


class DeterminismTest(SimpleTestCase):

    def test_same_evidence_always_produces_same_verdict(self):
        stat = _stat(ic=0.08, block_p=0.001)
        r1 = compute_verdict(stat, {"passes_fdr": True})
        r2 = compute_verdict(stat, {"passes_fdr": True})
        self.assertEqual(r1.verdict, r2.verdict)  # claude code changed: no randomness anywhere in this module — must be exactly reproducible
        self.assertEqual(r1.criteria_version, r2.criteria_version)
