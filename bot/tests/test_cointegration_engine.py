# claude code changed: new file — Phase B of the controlled remediation
# program (forensic-audit finding P1-5: cointegration_engine.py tested
# C(20,2)=190 pairs at a flat p<0.05 with no multiple-testing correction
# across them — roughly 9-10 pairs are expected to look "cointegrated" by
# chance alone at that rate). Tests _apply_fdr_correction() directly
# against synthetic PairResult objects, avoiding the cost of real
# OLS/ADF computation over actual price data for this unit-level check.

from django.test import SimpleTestCase

from bot.instruments import UnsupportedTimeframeError
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


# claude code changed: new — Multi-Asset Foundation Refactor Phase 1B,
# Objective 2. _estimate_half_life() has only ever returned a raw AR(1)
# decay CANDLE COUNT — never hours — silently correct-by-coincidence only
# because every dataset this engine has ever run on happened to be 1h
# candles. These tests lock the real fix: half_life_candles is the honest
# raw quantity; half_life_time/half_life_time_unit is the real,
# timeframe-derived wall-clock conversion (bot.instruments.
# candles_to_wall_clock()); half_life_hours is kept, unchanged in value,
# only for backward compatibility with pre-Phase-1B consumers that have
# only ever seen 1h data (kalman_filter_engine.py's CSV loader,
# templates/pairs_performance.html, templates/research_lab.html).
class HalfLifeTimeframeTest(SimpleTestCase):

    def test_default_timeframe_is_1h_and_matches_old_behavior_exactly(self):
        """No timeframe passed at all — every pre-Phase-1B call site."""
        result = _pair_result("BTC_USDT", "ETH_USDT", adf_pvalue=0.01)
        d = result.to_dict()
        self.assertEqual(d["timeframe"], "1h")
        self.assertEqual(d["half_life_hours"], d["half_life_candles"])
        self.assertEqual(d["half_life_time"], d["half_life_candles"])
        self.assertEqual(d["half_life_time_unit"], "hours")

    def test_4h_timeframe_reports_half_life_in_hours_scaled_by_4(self):
        """The refactor brief's own required example: 4h data + half-life
        = 12 candles -> 48 hours, not 12 hours and not 2 days."""
        result = PairResult(
            symbol_a="BTC_USDT", symbol_b="ETH_USDT",
            coint_pvalue=0.01, hedge_ratio=1.0, intercept=0.0,
            half_life=12.0, adf_pvalue=0.01,
            is_cointegrated=True, passes_filters=True,
            timeframe="4h",
        )
        d = result.to_dict()
        self.assertEqual(d["half_life_candles"], 12.0)
        self.assertEqual(d["half_life_time"], 48.0)
        self.assertEqual(d["half_life_time_unit"], "hours")
        # claude code changed: half_life_hours stays the OLD (wrong for
        # non-1h data) value — this is the deliberate backward-compat
        # carve-out, not an oversight. Real Research Lab traffic can never
        # actually reach this branch with today's data (every supported
        # instrument is 1h), so this test exists purely to document the
        # boundary, not to certify the old field as correct.
        self.assertEqual(d["half_life_hours"], 12.0)

    def test_1d_timeframe_reports_half_life_in_days(self):
        """The refactor brief's own required example: 1d data + half-life
        = 12 candles -> 12 days."""
        result = PairResult(
            symbol_a="BTC_USDT", symbol_b="ETH_USDT",
            coint_pvalue=0.01, hedge_ratio=1.0, intercept=0.0,
            half_life=12.0, adf_pvalue=0.01,
            is_cointegrated=True, passes_filters=True,
            timeframe="1d",
        )
        d = result.to_dict()
        self.assertEqual(d["half_life_time"], 12.0)
        self.assertEqual(d["half_life_time_unit"], "days")

    def test_infinite_half_life_converts_cleanly_without_crashing(self):
        """A non-cointegrated/failed pair's half_life is np.inf — must
        still produce a usable (inf, unit) pair, never raise."""
        result = PairResult(
            symbol_a="BTC_USDT", symbol_b="ETH_USDT",
            coint_pvalue=1.0, hedge_ratio=0.0, intercept=0.0,
            half_life=float("inf"), adf_pvalue=1.0,
            is_cointegrated=False, passes_filters=False,
            reject_reason="not cointegrated",
        )
        d = result.to_dict()
        self.assertEqual(d["half_life_time"], float("inf"))
        self.assertEqual(d["half_life_time_unit"], "hours")

    def test_unsupported_timeframe_fails_closed(self):
        """No known candle width for this timeframe — must raise, never
        silently assume 1h (the exact bug this phase removes)."""
        with self.assertRaises(UnsupportedTimeframeError):
            PairResult(
                symbol_a="BTC_USDT", symbol_b="ETH_USDT",
                coint_pvalue=0.01, hedge_ratio=1.0, intercept=0.0,
                half_life=12.0, adf_pvalue=0.01,
                is_cointegrated=True, passes_filters=True,
                timeframe="2h",  # not a real timeframe this platform has ever used
            )

    def test_engine_threads_its_timeframe_into_every_pair_result(self):
        """CointegrationEngine(timeframe=...) must reach PairResult via
        _test_pair() — checked at the engine-construction level (cheap,
        no I/O) since _test_pair() itself needs real OLS/ADF-worthy price
        data, already covered end-to-end by
        test_research_lab_pairs_research.py."""
        engine = CointegrationEngine(timeframe="4h")
        self.assertEqual(engine.timeframe, "4h")

    def test_max_min_half_life_filter_bounds_are_unchanged_numerically(self):
        """Objective 2 deliberately does NOT rescale the filter thresholds
        by timeframe (see cointegration_engine.py's own module-level note)
        — only how half-life is REPORTED changed. Locks that the filter
        bounds a 1h experiment sees today are byte-identical to before
        this phase."""
        engine = CointegrationEngine()
        self.assertEqual(engine.max_half_life, 120)
        self.assertEqual(engine.min_half_life, 2.0)
