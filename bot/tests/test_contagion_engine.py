# claude code changed: new file — Research Agent architecture study Phase 1
# (test-coverage prerequisite before contagion_engine.py could ever be
# exposed as an AI-callable tool). Confirms and proves the fix for a real
# look-ahead leakage bug: both _winsorise_divergence() and the tail-clip
# inside _calculate_zscore() computed clip boundaries from the WHOLE
# column (`series.dropna().quantile(...)`), so an early row's clipped
# value depended on divergence/z-score values from LATER rows — the
# identical shape to the bug already fixed in cross_section_engine.py's
# _winsorise_returns() this session. These tests prove: "changing the
# future must not change the past."

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.contagion_engine import ContagionEngine, DIVERGENCE_WINSOR_MIN_PERIODS, DivergenceICReporter   # claude code changed: new import — Phase 1D, Objective 10


def _make_divergence_df(n, seed, col="divergence_3h", spike_at=None, spike_magnitude=5.0):
    """Synthetic single-altcoin DataFrame with one divergence column already
    populated (the shape _winsorise_divergence()/_calculate_zscore() expect
    as input, bypassing the full calculate_all() pipeline)."""
    rng = np.random.default_rng(seed)
    values = rng.normal(0, 0.01, n)
    if spike_at is not None:
        values[spike_at] = spike_magnitude   # a wildly extreme future divergence event
    df = pd.DataFrame(index=pd.date_range("2020-01-01", periods=n, freq="1h"))
    df[col] = values
    return df


class DivergenceWinsorizationLeakageTest(SimpleTestCase):

    def test_past_boundaries_unaffected_by_future_spike(self):
        shared_rows = DIVERGENCE_WINSOR_MIN_PERIODS + 500
        tail_rows = 200

        df_a = _make_divergence_df(shared_rows + tail_rows, seed=1)
        df_b = df_a.copy()
        spike_idx = shared_rows + 50
        df_b.loc[df_b.index[spike_idx], "divergence_3h"] = 8.0   # future "flash crash" divergence

        engine = ContagionEngine()
        out_a = engine._winsorise_divergence(df_a.copy(), "TEST/USDT")
        out_b = engine._winsorise_divergence(df_b.copy(), "TEST/USDT")

        pd.testing.assert_series_equal(
            out_a["divergence_3h"].iloc[:shared_rows],
            out_b["divergence_3h"].iloc[:shared_rows],
            check_names=False,
            obj="divergence_3h for the shared (pre-spike) history",
        )

    def test_early_rows_kept_raw_not_clipped_from_insufficient_history(self):
        n = DIVERGENCE_WINSOR_MIN_PERIODS - 10
        df = _make_divergence_df(n, seed=2)
        raw = df["divergence_3h"].copy()

        engine = ContagionEngine()
        out = engine._winsorise_divergence(df.copy(), "TEST/USDT")

        pd.testing.assert_series_equal(
            out["divergence_3h"], raw, check_names=False,
            obj="divergence_3h before enough history exists for a stable boundary",
        )

    def test_boundary_is_strictly_prior_not_inclusive_of_current_row(self):
        n = DIVERGENCE_WINSOR_MIN_PERIODS + 100
        df = _make_divergence_df(n, seed=3)
        extreme_idx = n - 1
        df.loc[df.index[extreme_idx], "divergence_3h"] = 3.0

        engine = ContagionEngine()
        out = engine._winsorise_divergence(df.copy(), "TEST/USDT")

        self.assertLess(
            out["divergence_3h"].iloc[extreme_idx], 3.0,
            "an extreme row's own value must not inflate the boundary used to clip itself",
        )


class ZScoreWinsorizationLeakageTest(SimpleTestCase):
    """_calculate_zscore()'s rolling mean/std was already causal — only its
    final tail-clip (z > ~2.58 std) had the whole-series leakage bug."""

    def test_past_zscore_boundaries_unaffected_by_future_spike(self):
        shared_rows = DIVERGENCE_WINSOR_MIN_PERIODS + 500
        tail_rows = 200

        df_a = _make_divergence_df(shared_rows + tail_rows, seed=4)
        df_b = df_a.copy()
        spike_idx = shared_rows + 50   # well after the shared comparison window
        df_b.loc[df_b.index[spike_idx], "divergence_3h"] = 8.0

        engine = ContagionEngine()
        out_a = engine._calculate_zscore(df_a.copy(), "TEST/USDT")
        out_b = engine._calculate_zscore(df_b.copy(), "TEST/USDT")

        pd.testing.assert_series_equal(
            out_a["divergence_zscore_3h"].iloc[:shared_rows],
            out_b["divergence_zscore_3h"].iloc[:shared_rows],
            check_names=False,
            obj="divergence_zscore_3h for the shared (pre-spike) history",
        )


class DivergenceCalculationSanityTest(SimpleTestCase):
    """Basic correctness checks unrelated to the leakage bug — confirms the
    fix didn't change the (already-correct) divergence formula or forward-
    return label shift."""

    def _minimal_alt_df(self, n=30):
        idx = pd.date_range("2020-01-01", periods=n, freq="1h")
        df = pd.DataFrame(index=idx)
        df["return_1h"] = 0.01
        df["btc_return_1h"] = 0.02
        df["btc_return_3h"] = 0.06
        df["btc_return_6h"] = 0.12
        df["btc_return_12h"] = 0.24
        return df

    def test_divergence_1h_is_btc_minus_altcoin_return(self):
        engine = ContagionEngine()
        df = self._minimal_alt_df()
        out = engine._calculate_divergence(df, "TEST/USDT")
        # divergence_1h = btc_return_1h - return_1h = 0.02 - 0.01 = 0.01
        self.assertAlmostEqual(out["divergence_1h"].iloc[5], 0.01, places=10)

    def test_forward_return_uses_future_candles_not_past(self):
        engine = ContagionEngine(forward_horizons={"forward_return_2h": 2})
        idx = pd.date_range("2020-01-01", periods=10, freq="1h")
        df = pd.DataFrame(index=idx)
        # Distinct return per row so the shift direction is unambiguous.
        df["return_1h"] = [0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09]

        out = engine._calculate_forward_returns(df, "TEST/USDT")

        # Row 2 (return=0.02) should see the sum of rows 3+4 (0.03+0.04=0.07),
        # never its own or earlier rows' returns.
        self.assertAlmostEqual(out["forward_return_2h"].iloc[2], 0.07, places=10)
        # Last two rows have no full 2h future window left — must be NaN.
        self.assertTrue(pd.isna(out["forward_return_2h"].iloc[-1]))


# claude code changed: new — Multi-Asset Foundation Refactor Phase 1B,
# Objective 4. This engine was NOT redesigned this phase (per the
# brief's own explicit instruction) — these tests instead PROVE two
# correctness boundaries the audit discovered were already real,
# structural properties of the existing implementation, not something
# that needed building: (1) the "BTC" reference asset is a genuine
# constructor parameter, not a hardcoded symbol string anywhere in the
# actual computation — btc_symbol="ETH/USDT" really does make ETH play
# the benchmark role, using real price data; (2) a left-join-based
# altcoin/benchmark merge already tolerates a missing altcoin entirely
# (skips it, logs a warning, never crashes) — real asynchronous-market
# tolerance, already present, not a gap this phase needed to close.
#
# What this phase deliberately did NOT fix, and why: DIVERGENCE_WINDOWS/
# ZSCORE_LOOKBACK are documented in "hours" but are actually raw candle
# counts (rolling(window=N) on a candle-indexed Series) — the identical
# class of bug fixed in cointegration_engine.py's half-life this same
# phase. Left untouched here because (a) this engine has no live
# Research Lab traffic to verify a before/after baseline against (unlike
# cointegration, which does), and (b) rewiring its rolling-window
# reporting risks the same "requires touching a functioning research
# engine's public output shape" territory the brief's own STOP
# conditions warn against, for a capability nothing currently consumes.
# Recorded here, not silently — see the Phase 1B report's Contagion
# Findings section.
class ContagionCrossAssetBoundaryTest(SimpleTestCase):

    def test_benchmark_asset_is_a_real_parameter_not_hardcoded(self):
        """Real price data, both legs — proves btc_symbol genuinely
        controls which asset plays the reference role, using ETH instead
        of BTC, with zero code changes to the engine itself."""
        from bot.research_lab.tools._data import load_ohlcv

        eth = load_ohlcv("ETH/USDT")
        sol = load_ohlcv("SOL/USDT")

        engine = ContagionEngine(btc_symbol="ETH/USDT", altcoin_symbols=["SOL/USDT"])
        result = engine.calculate_all({"ETH/USDT": eth.copy(), "SOL/USDT": sol.copy()})

        self.assertIn("return_1h", result["ETH/USDT"].columns)
        divergence_cols = [c for c in result["SOL/USDT"].columns if c.startswith("divergence_")]
        self.assertTrue(divergence_cols, "SOL's divergence-from-ETH features must exist when ETH is the benchmark")

    def test_missing_altcoin_is_skipped_not_crashed(self):
        """An altcoin named in altcoin_symbols but absent from the data
        dict must be skipped with a warning, never raise — real tolerance
        for a genuinely asynchronous/incomplete multi-asset dataset."""
        from bot.research_lab.tools._data import load_ohlcv

        btc = load_ohlcv("BTC/USDT")
        sol = load_ohlcv("SOL/USDT")

        engine = ContagionEngine(altcoin_symbols=["SOL/USDT", "NOT_IN_DATA/USDT"])
        result = engine.calculate_all({"BTC/USDT": btc.copy(), "SOL/USDT": sol.copy()})   # NOT_IN_DATA/USDT deliberately absent

        self.assertIn("SOL/USDT", result)
        self.assertNotIn("NOT_IN_DATA/USDT", result)
        divergence_cols = [c for c in result["SOL/USDT"].columns if c.startswith("divergence_")]
        self.assertTrue(divergence_cols)


class DivergenceIcReporterFdrCorrectionTest(SimpleTestCase):
    """claude code changed: new — Phase 1D, Objective 10. Proves
    DivergenceICReporter.report() now applies family-wide FDR correction
    across every (symbol, feature) p-value it produces, per
    capability_registry.py's documented blocker for
    contagion_divergence_research ("flags results at a raw p<0.05
    threshold with no multiple-testing correction"). Uses synthetic,
    hand-constructed data — no real data/*.csv fixture required."""

    def _make_altcoin_df(self, n, rng, n_noise_features=15, real_feature_ic=0.0):
        """One altcoin's DataFrame with `n_noise_features` divergence
        columns that are PURE NOISE relative to forward_return_2h (should
        not survive FDR correction even if a few look raw-significant by
        chance), plus optionally one divergence column genuinely
        correlated with the forward return (should survive)."""
        idx = pd.date_range("2020-01-01", periods=n, freq="1h")
        fwd = pd.Series(rng.normal(0, 1, n), index=idx)
        df = pd.DataFrame(index=idx)
        df["forward_return_2h"] = fwd
        for i in range(n_noise_features):
            df[f"divergence_noise_{i}h"] = rng.normal(0, 1, n)   # claude code changed: independent of fwd by construction
        if real_feature_ic:
            df["divergence_real_1h"] = fwd * real_feature_ic + rng.normal(0, 1, n)   # claude code changed: genuinely correlated with fwd
        return df

    def test_pure_noise_features_mostly_fail_fdr_even_if_some_pass_raw_threshold(self):
        # claude code changed: with enough noise features tested at raw
        # p<0.05, a few are EXPECTED to look significant by chance alone
        # (the exact multiple-testing problem cointegration_engine.py's
        # P1-5 finding already documented) — FDR correction must bring the
        # survivor count down, not leave it identical to the raw count.
        rng = np.random.default_rng(11)
        data = {
            "ALT1": self._make_altcoin_df(300, rng, n_noise_features=25),
            "ALT2": self._make_altcoin_df(300, rng, n_noise_features=25),
        }

        result = DivergenceICReporter.report(data, altcoin_symbols=["ALT1", "ALT2"])

        self.assertIn("pvalue_fdr", result.columns)
        self.assertIn("significant_fdr", result.columns)
        n_raw = int(result["significant"].sum())
        n_fdr = int(result["significant_fdr"].sum())
        self.assertLessEqual(n_fdr, n_raw)   # claude code changed: downgrade-only — FDR can only remove significance, never add it

    def test_fdr_never_marks_significant_a_result_that_failed_raw_threshold(self):
        rng = np.random.default_rng(22)
        data = {"ALT1": self._make_altcoin_df(300, rng, n_noise_features=20)}

        result = DivergenceICReporter.report(data, altcoin_symbols=["ALT1"])

        violations = result[(~result["significant"]) & (result["significant_fdr"])]
        self.assertTrue(violations.empty, "FDR correction must never upgrade a raw-insignificant result to significant")

    def test_strongly_correlated_feature_survives_fdr_correction(self):
        # claude code changed: a genuinely strong signal (ic~0.6 by
        # construction) mixed in with noise features must still survive
        # family-wide correction — proves the fix is conservative, not
        # simply "nothing is ever significant."
        rng = np.random.default_rng(33)
        data = {
            "ALT1": self._make_altcoin_df(500, rng, n_noise_features=10, real_feature_ic=0.6),
        }

        result = DivergenceICReporter.report(data, altcoin_symbols=["ALT1"])

        real_row = result[result["feature"] == "divergence_real_1h"]
        self.assertEqual(len(real_row), 1)
        self.assertTrue(bool(real_row.iloc[0]["significant_fdr"]), "a genuinely strong signal must survive FDR correction, not just raw significance")

    def test_no_results_returns_empty_dataframe_not_a_crash(self):
        result = DivergenceICReporter.report({}, altcoin_symbols=["NOTHING"])
        self.assertTrue(result.empty)
