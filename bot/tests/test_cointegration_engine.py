# claude code changed: new file — Phase B of the controlled remediation
# program (forensic-audit finding P1-5: cointegration_engine.py tested
# C(20,2)=190 pairs at a flat p<0.05 with no multiple-testing correction
# across them — roughly 9-10 pairs are expected to look "cointegrated" by
# chance alone at that rate). Tests _apply_fdr_correction() directly
# against synthetic PairResult objects, avoiding the cost of real
# OLS/ADF computation over actual price data for this unit-level check.

import numpy as np
import pandas as pd
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
        # claude code changed: was a hardcoded len(engine.universe)==20 /
        # expected_pairs==190 assertion — the universe is now dynamically
        # sized (bot/universe_selector.py, expansion to 50 symbols), so
        # this checks the general C(n, 2) relationship instead of a fixed
        # historical count. See test_universe_selection.py for the actual
        # "exactly 50 symbols" assertion against the persisted selection.
        engine = self._engine()
        n = len(engine.universe)
        self.assertGreater(n, 0)
        expected_pairs = (n * (n - 1)) // 2   # C(n, 2)
        self.assertEqual(len(engine.all_pairs), expected_pairs)

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


class CorrelationPrefilterTest(SimpleTestCase):
    """
    claude code changed: new — universe expansion mission, Step 4's
    optional correlation pre-filter. Uses real run_all() with small
    synthetic price series (independent random walks — no real relationship,
    so returns correlation should be near zero) rather than mocking, per
    this project's own testing convention; MIN_CANDLES=1000 is respected
    with training_window/zscore params shrunk to fit a fast unit test.
    """

    def _synthetic_data(self, n=1200, seed=0):
        rng = np.random.default_rng(seed)
        idx = pd.date_range("2020-01-01", periods=n, freq="1h", tz="UTC")

        # Independent random walks in log-price space -> near-zero returns
        # correlation between the two, by construction.
        log_price_a = np.cumsum(rng.normal(0, 0.001, n)) + 4.0   # ~exp(4) ≈ 55
        log_price_b = np.cumsum(rng.normal(0, 0.001, n)) + 2.0   # ~exp(2) ≈ 7.4

        df_a = pd.DataFrame({"close": np.exp(log_price_a)}, index=idx)
        df_b = pd.DataFrame({"close": np.exp(log_price_b)}, index=idx)
        return {"SYM_A_USDT": df_a, "SYM_B_USDT": df_b}

    def _engine(self, **overrides):
        defaults = dict(
            universe=["SYM_A_USDT", "SYM_B_USDT"],
            training_window=800,
            zscore_window=100,
            zscore_min_periods=50,
        )
        defaults.update(overrides)
        return CointegrationEngine(**defaults)

    def test_off_by_default(self):
        engine = self._engine()
        self.assertFalse(engine.enable_correlation_prefilter)

    def test_disabled_prefilter_still_tests_the_low_correlation_pair(self):
        data = self._synthetic_data()
        engine = self._engine(enable_correlation_prefilter=False)
        engine.run_all(data)
        self.assertEqual(len(engine.pair_results), 1)

    def test_enabled_prefilter_skips_the_low_correlation_pair(self):
        data = self._synthetic_data()
        engine = self._engine(enable_correlation_prefilter=True, correlation_prefilter_threshold=0.5)
        engine.run_all(data)
        self.assertEqual(len(engine.pair_results), 0)

    def test_threshold_of_zero_never_skips_anything(self):
        # claude code changed: a sanity bound — any real correlation value
        # is >= 0 in absolute terms, so a threshold of exactly 0 must never
        # discard a pair purely from the prefilter.
        data = self._synthetic_data()
        engine = self._engine(enable_correlation_prefilter=True, correlation_prefilter_threshold=0.0)
        engine.run_all(data)
        self.assertEqual(len(engine.pair_results), 1)


class OutOfSamplePersistenceTest(SimpleTestCase):
    """
    claude code changed: new — real bug fix. A pair being stationary WITHIN
    cointegration_engine.py's fixed TRAINING_WINDOW never proved the
    relationship persists: empirically, 76 of 84 pairs (90.5%) that passed
    the old training-window-only ADF test failed a second ADF test on the
    untouched out-of-sample holdout using the SAME frozen hedge ratio — a
    genuine, permanent structural break in most cases, not noise. These
    tests build synthetic series where the true generating process is
    known and controlled, so the pass/fail outcome can be asserted exactly
    rather than relied on to "look right" against real market data.
    """

    def _engine(self, **overrides):
        defaults = dict(training_window=600, min_oos_candles=200, zscore_window=100, zscore_min_periods=50)
        defaults.update(overrides)
        return CointegrationEngine(**defaults)

    def _ou_spread(self, rng, n, phi=0.85, noise_std=0.01):
        """A genuinely mean-reverting AR(1) (Ornstein-Uhlenbeck-style) series — stationary by construction."""
        spread = np.zeros(n)
        for t in range(1, n):
            spread[t] = phi * spread[t - 1] + rng.normal(0, noise_std)
        return spread

    def test_relationship_persisting_through_both_windows_passes(self):
        rng = np.random.default_rng(1)
        n_train, n_oos = 600, 500
        n = n_train + n_oos
        log_b = np.cumsum(rng.normal(0, 0.002, n)) + 3.0
        spread = self._ou_spread(rng, n)   # Mean-reverting for the ENTIRE series — train AND oos
        intercept, beta = 1.0, 0.5
        log_a = intercept + beta * log_b + spread

        price_a = pd.Series(np.exp(log_a))
        price_b = pd.Series(np.exp(log_b))
        engine = self._engine()
        result = engine._test_pair("SYM_A", "SYM_B", np.log(price_a), np.log(price_b))

        self.assertTrue(result.is_cointegrated)
        self.assertIsNotNone(result.oos_adf_pvalue)
        self.assertLess(result.oos_adf_pvalue, 0.05)
        self.assertTrue(result.passes_filters)

    def test_training_window_only_artifact_is_rejected(self):
        # Same OU-mean-reverting construction for TRAIN, but the spread
        # becomes a pure random walk (unit root — genuinely non-stationary)
        # for the OOS portion, simulating exactly the real-world failure
        # mode this fix was built to catch: looks cointegrated in-sample,
        # breaks down permanently right after.
        rng = np.random.default_rng(2)
        n_train, n_oos = 600, 500
        n = n_train + n_oos
        log_b = np.cumsum(rng.normal(0, 0.002, n)) + 3.0

        train_spread = self._ou_spread(rng, n_train)
        oos_random_walk = np.cumsum(rng.normal(0, 0.01, n_oos))   # Unit root — no reversion at all
        spread = np.concatenate([train_spread, train_spread[-1] + oos_random_walk])

        intercept, beta = 1.0, 0.5
        log_a = intercept + beta * log_b + spread

        price_a = pd.Series(np.exp(log_a))
        price_b = pd.Series(np.exp(log_b))
        engine = self._engine()
        result = engine._test_pair("SYM_A", "SYM_B", np.log(price_a), np.log(price_b))

        self.assertTrue(result.is_cointegrated)   # Training window alone still looks cointegrated
        self.assertIsNotNone(result.oos_adf_pvalue)
        self.assertGreaterEqual(result.oos_adf_pvalue, 0.05)   # But the OOS holdout is genuinely non-stationary
        self.assertFalse(result.passes_filters)   # And the pair must be rejected overall
        self.assertIn("out-of-sample", result.reject_reason)

    def test_insufficient_oos_data_is_rejected_not_silently_skipped(self):
        # Total data comfortably clears MIN_CANDLES (1000), but leaves less
        # than min_oos_candles (200) after the training window — must fail
        # loudly with an honest reason, never silently pass on the
        # in-sample result alone.
        rng = np.random.default_rng(3)
        n_train, n_oos = 900, 150   # 1050 total clears MIN_CANDLES; oos=150 < min_oos_candles=200
        n = n_train + n_oos
        log_b = np.cumsum(rng.normal(0, 0.002, n)) + 3.0
        spread = self._ou_spread(rng, n)
        intercept, beta = 1.0, 0.5
        log_a = intercept + beta * log_b + spread

        price_a = pd.Series(np.exp(log_a))
        price_b = pd.Series(np.exp(log_b))
        engine = self._engine(training_window=n_train)   # claude code changed: override the class default (600) so train_end lands exactly at n_train, leaving exactly n_oos=150 held out
        result = engine._test_pair("SYM_A", "SYM_B", np.log(price_a), np.log(price_b))

        self.assertFalse(result.passes_filters)
        self.assertIn("insufficient out-of-sample data", result.reject_reason)

    def test_pair_rejected_at_in_sample_stage_never_gets_an_oos_pvalue(self):
        # A pair that fails the training-window ADF test outright must
        # never reach the OOS section at all — oos_adf_pvalue stays None,
        # not a default 1.0 that could be mistaken for "a real test ran and
        # failed."
        rng = np.random.default_rng(4)
        n = 1100
        log_a = np.cumsum(rng.normal(0, 0.01, n)) + 4.0   # Independent random walk
        log_b = np.cumsum(rng.normal(0, 0.01, n)) + 2.0   # Independent random walk — no real relationship at all
        engine = self._engine()
        result = engine._test_pair("SYM_A", "SYM_B", pd.Series(log_a), pd.Series(log_b))

        self.assertFalse(result.is_cointegrated)
        self.assertIsNone(result.oos_adf_pvalue)
        self.assertFalse(result.passes_filters)

    def test_apply_oos_persistence_filter_downgrades_via_fdr(self):
        # Direct unit test of _apply_oos_persistence_filter()'s FDR logic,
        # mirroring FdrCorrectionTest's pattern for _apply_fdr_correction()
        # — synthetic PairResult objects, no real OLS/ADF computation needed.
        engine = self._engine()

        def _result(oos_p, passes_so_far=True):
            r = PairResult(
                symbol_a="A", symbol_b="B", coint_pvalue=0.01, hedge_ratio=1.0,
                intercept=0.0, half_life=24.0, adf_pvalue=0.01,
                is_cointegrated=True, passes_filters=passes_so_far,
                oos_adf_pvalue=oos_p,
            )
            return r

        # One borderline-real pair (oos_p=0.03) pooled with 30 pairs whose
        # OOS test clearly failed (p in [0.3, 0.9]) — the real scale this
        # fix addresses (most candidate pairs fail persistence).
        borderline = _result(0.03)
        engine.pair_results = [borderline] + [
            _result(0.3 + (i % 6) * 0.1) for i in range(30)
        ]

        engine._apply_oos_persistence_filter()

        self.assertFalse(borderline.passes_oos_persistence)
        self.assertFalse(borderline.passes_filters)
        self.assertIn("out-of-sample persistence", borderline.reject_reason)

    def test_pair_never_reaching_oos_stage_is_untouched_by_correction(self):
        engine = self._engine()
        never_tested = PairResult(
            symbol_a="A", symbol_b="B", coint_pvalue=0.9, hedge_ratio=0.0,
            intercept=0.0, half_life=float("inf"), adf_pvalue=0.9,
            is_cointegrated=False, passes_filters=False,
            oos_adf_pvalue=None,
        )
        engine.pair_results = [never_tested]
        engine._apply_oos_persistence_filter()

        self.assertIsNone(never_tested.passes_oos_persistence)
        self.assertIsNone(never_tested.oos_adf_pvalue_fdr)
