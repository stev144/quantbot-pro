# claude code changed: new file — Phase B of the controlled remediation
# program (forensic-audit finding P1-5: cointegration_engine.py tested
# C(20,2)=190 pairs at a flat p<0.05 with no multiple-testing correction
# across them — roughly 9-10 pairs are expected to look "cointegrated" by
# chance alone at that rate). Tests _apply_fdr_correction() directly
# against synthetic PairResult objects, avoiding the cost of real
# OLS/ADF computation over actual price data for this unit-level check.

from django.test import SimpleTestCase

from bot.research.cointegration_engine import CointegrationEngine, PairResult


def _pair_result(symbol_a, symbol_b, adf_pvalue, coint_threshold=0.05):
    is_cointegrated = adf_pvalue < coint_threshold
    return PairResult(
        symbol_a=symbol_a, symbol_b=symbol_b,
        coint_pvalue=adf_pvalue, hedge_ratio=1.0, intercept=0.0,
        half_life=24.0, adf_pvalue=adf_pvalue,
        is_cointegrated=is_cointegrated, passes_filters=is_cointegrated,
        reject_reason="" if is_cointegrated else f"ADF p={adf_pvalue:.4f} > {coint_threshold}",
    )


class FdrCorrectionTest(SimpleTestCase):

    def _engine(self):
        # No real data needed — __init__ only builds config + all_pairs
        # from the UNIVERSE constant, no I/O.
        return CointegrationEngine()

    def test_downgrades_a_borderline_pair_once_pooled_with_many_noise_pairs(self):
        engine = self._engine()
        # One borderline pair (would pass alone: p=0.045 < 0.05) plus 30
        # clearly-not-cointegrated pairs (p in [0.3, 0.9]) — the real
        # scale of multiple testing this fix addresses (190 pairs today).
        engine.pair_results = [_pair_result("BTC_USDT", "ETH_USDT", adf_pvalue=0.045)]
        for i in range(30):
            engine.pair_results.append(
                _pair_result(f"SYM{i}_A", f"SYM{i}_B", adf_pvalue=0.3 + (i % 6) * 0.1)
            )

        engine._apply_fdr_correction()

        borderline = engine.pair_results[0]
        self.assertTrue(borderline.is_cointegrated, "raw-threshold flag must be preserved as historical record")
        self.assertFalse(borderline.passes_fdr)
        self.assertFalse(borderline.passes_filters, "must be downgraded once corrected against the full pair family")
        self.assertIn("FDR", borderline.reject_reason)

    def test_strongly_significant_pair_survives_correction(self):
        engine = self._engine()
        engine.pair_results = [_pair_result("BTC_USDT", "ETH_USDT", adf_pvalue=1e-20)]
        for i in range(30):
            engine.pair_results.append(
                _pair_result(f"SYM{i}_A", f"SYM{i}_B", adf_pvalue=0.3 + (i % 6) * 0.1)
            )

        engine._apply_fdr_correction()

        strong = engine.pair_results[0]
        self.assertTrue(strong.passes_fdr)
        self.assertTrue(strong.passes_filters)

    def test_never_upgrades_a_pair_that_failed_the_raw_threshold(self):
        engine = self._engine()
        engine.pair_results = [
            _pair_result("BTC_USDT", "ETH_USDT", adf_pvalue=0.8),   # clearly not cointegrated
            _pair_result("SOL_USDT", "ADA_USDT", adf_pvalue=1e-20),  # strong, drags others' FDR threshold up
        ]

        engine._apply_fdr_correction()

        failed_raw = engine.pair_results[0]
        self.assertFalse(failed_raw.is_cointegrated)
        self.assertFalse(failed_raw.passes_filters)

    def test_every_pair_gets_fdr_fields_populated(self):
        engine = self._engine()
        engine.pair_results = [
            _pair_result("BTC_USDT", "ETH_USDT", adf_pvalue=0.01),
            _pair_result("SOL_USDT", "ADA_USDT", adf_pvalue=0.5),
        ]

        engine._apply_fdr_correction()

        for r in engine.pair_results:
            self.assertIsNotNone(r.adf_pvalue_fdr)
            self.assertIsNotNone(r.passes_fdr)

    def test_universe_matches_pair_count_documented_in_comments(self):
        # Direct regression check for the stale "7 symbols -> 21 pairs"
        # comment this fix also corrected — the real universe/pair count
        # must match what the corrected comments now claim.
        engine = self._engine()
        self.assertEqual(len(engine.universe), 20)
        expected_pairs = (20 * 19) // 2   # C(20, 2)
        self.assertEqual(len(engine.all_pairs), expected_pairs)
        self.assertEqual(expected_pairs, 190)

    def test_empty_results_is_a_no_op(self):
        engine = self._engine()
        engine.pair_results = []
        engine._apply_fdr_correction()   # must not raise
        self.assertEqual(engine.pair_results, [])
