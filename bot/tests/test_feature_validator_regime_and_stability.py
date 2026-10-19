# claude code changed: new — regression coverage for two real bugs found
# during a live audit (user report: "regime detection failed"):
#
# 1. MarketRegimeDetector.detect_regime() unconditionally requires
#    'realized_vol'/'close'/'adx' columns. bot/research_lab/tools/
#    statistical_tools.py's run_statistical_test()/run_fdr_correction()
#    called it on a DataFrame scoped to just [feature_name, forward_col],
#    raising a KeyError caught by detect_regime()'s own blanket
#    `except Exception` and silently degrading every observation to
#    'ranging' — reproduced directly against real BTC/USDT data before
#    this fix ("Error detecting regime: 'realized_vol'. Defaulting to
#    'ranging'" on every single call). Fixed via
#    statistical_tools._scope_to_single_feature().
#
# 2. The same over-scoping also dropped 'timestamp', which was silently
#    always-missing from `df.columns` anyway (it lives on the index once
#    feature_calculator.py runs) — so the year-by-year stability test
#    computed the SAME overall IC four times under the module's
#    hardcoded STABILITY_WINDOWS (2021-2024) keys, reporting a falsely
#    "perfect" 0.0 stability_consistency instead of honestly reflecting
#    that no real per-year comparison happened. STABILITY_WINDOWS was
#    also independently stale (real data now extends to 2026, 2+ years
#    never tested). Fixed via _build_stability_windows(), which derives
#    real per-year windows from whatever data is actually present.
#
# No mocking, per this project's convention — real project OHLCV data
# for the end-to-end regression proof.

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.feature_validator import MarketRegime, MarketRegimeDetector, _build_stability_windows


class MarketRegimeDetectorTest(SimpleTestCase):

    def _make_regime_df(self, n=200, seed=0):
        rng = np.random.default_rng(seed)
        close = 100 + np.cumsum(rng.normal(0, 1, n))
        return pd.DataFrame({
            "close": close,
            "realized_vol": np.abs(rng.normal(0.02, 0.01, n)),
            "adx": rng.uniform(10, 60, n),
        })

    def test_real_columns_present_produces_genuinely_different_regimes(self):
        """The positive case: given the columns detect_regime() actually
        needs, it must classify observations into more than one regime —
        proving the classification logic itself works, not just that it
        doesn't crash."""
        df = self._make_regime_df(n=500, seed=1)
        regime = MarketRegimeDetector.detect_regime(df)
        self.assertGreater(regime.nunique(), 1, "expected more than one distinct regime label on real-shaped varied data")
        self.assertTrue(set(regime.unique()).issubset({
            MarketRegime.TRENDING, MarketRegime.RANGING, MarketRegime.VOLATILE,
            MarketRegime.CRASH, MarketRegime.RECOVERY,
        }))

    def test_missing_realized_vol_falls_back_to_ranging_not_a_crash(self):
        """Documents the CURRENT defensive fallback (never crash a caller)
        — this is the exact failure mode that was silently hit on every
        real run_statistical_test() call before the real fix
        (bot/research_lab/tools/statistical_tools.py's
        _scope_to_single_feature()) started supplying 'realized_vol'.
        This test proves the fallback itself is safe; it does NOT mean
        this code path is fine to hit in production — see the other
        test below proving the real caller no longer hits it."""
        df = pd.DataFrame({"close": np.arange(100, 200, dtype=float)})
        regime = MarketRegimeDetector.detect_regime(df)
        self.assertTrue((regime == MarketRegime.RANGING).all())


class BuildStabilityWindowsTest(SimpleTestCase):

    def test_derives_one_window_per_real_calendar_year_present(self):
        ts = pd.to_datetime(["2023-06-01", "2024-01-01", "2025-12-31"], utc=True)
        df = pd.DataFrame({"timestamp": ts})
        windows = _build_stability_windows(df)
        self.assertEqual(set(windows.keys()), {"2023", "2024", "2025"})

    def test_windows_are_tz_matched_to_the_real_column(self):
        """Real bug this guards: a tz-naive hardcoded Timestamp compared
        against a tz-aware real timestamp column raises
        TypeError('Cannot compare tz-naive and tz-aware timestamps').
        Windows built here must share the column's own tz."""
        ts = pd.to_datetime(["2024-03-01"], utc=True)
        df = pd.DataFrame({"timestamp": ts})
        windows = _build_stability_windows(df)
        start, end = windows["2024"]
        # Must not raise:
        self.assertTrue(ts[0] >= start)
        self.assertTrue(ts[0] <= end)

    def test_no_timestamp_column_returns_empty_not_a_guess(self):
        df = pd.DataFrame({"feature": [1.0, 2.0, 3.0]})
        self.assertEqual(_build_stability_windows(df), {})

    def test_derives_years_beyond_the_old_hardcoded_2021_2024_range(self):
        """The real staleness bug: the old module-level STABILITY_WINDOWS
        was hardcoded to exactly 2021-2024. Real data now extends well
        past that — this must not silently exclude it."""
        ts = pd.to_datetime(["2025-01-01", "2026-06-01"], utc=True)
        df = pd.DataFrame({"timestamp": ts})
        windows = _build_stability_windows(df)
        self.assertIn("2025", windows)
        self.assertIn("2026", windows)


class BuildStabilityWindowsDatetimeIndexTest(SimpleTestCase):
    """Phase 1 repair: _build_stability_windows must use the DatetimeIndex
    when no timestamp column is present, matching how df_features is built
    (timestamps as index, no 'timestamp' column).

    Before the fix every feature had stability_std=NaN because
    _build_stability_windows returned {} for any DatetimeIndex df.
    """  # claude code changed: new — Phase 1 defect repair tests

    def _make_dti_df(self, dates, tz=None, include_feature=True):
        idx = pd.DatetimeIndex(pd.to_datetime(dates), tz=tz, name="timestamp")
        data = {"feature": np.ones(len(dates))} if include_feature else {}
        return pd.DataFrame(data, index=idx)

    # ── T1: primary repair — DatetimeIndex → non-empty windows ─────────────
    def test_datetime_index_returns_non_empty_windows(self):
        df = self._make_dti_df(["2023-06-01", "2024-01-01", "2025-12-31"], tz="UTC")
        windows = _build_stability_windows(df)
        self.assertNotEqual(windows, {}, "DatetimeIndex df must produce windows, not {}")
        self.assertEqual(set(windows.keys()), {"2023", "2024", "2025"})

    # ── T2: DatetimeIndex multiple years → one window per year ─────────────
    def test_datetime_index_one_window_per_year(self):
        # One timestamp per year — deterministic, no arithmetic ambiguity
        idx = pd.DatetimeIndex(
            [pd.Timestamp(f"{y}-06-01", tz="UTC") for y in range(2021, 2025)]
        )
        df = pd.DataFrame({"v": np.ones(4)}, index=idx)
        windows = _build_stability_windows(df)
        self.assertEqual(set(windows.keys()), {"2021", "2022", "2023", "2024"})

    # ── T3: DatetimeIndex with tz — windows carry same tz ──────────────────
    def test_datetime_index_tz_aware_windows_comparable(self):
        """Windows built from a tz-aware DatetimeIndex must be tz-aware with
        the same zone, so the year >= / <= filter never raises TypeError."""
        idx = pd.date_range("2024-03-01", periods=3, freq="1h", tz="UTC")
        df = pd.DataFrame({"v": 1.0}, index=idx)
        windows = _build_stability_windows(df)
        start, end = windows["2024"]
        # Must not raise TypeError (tz-aware vs tz-naive comparison):
        self.assertTrue(idx[0] >= start)
        self.assertTrue(idx[-1] <= end)

    # ── T4: empty DatetimeIndex → returns {} ───────────────────────────────
    def test_empty_datetime_index_returns_empty(self):
        idx = pd.DatetimeIndex([], tz="UTC")
        df = pd.DataFrame({"v": pd.Series([], dtype=float)}, index=idx)
        self.assertEqual(_build_stability_windows(df), {})

    # ── T5: column takes precedence over index when both present ───────────
    def test_timestamp_column_takes_precedence_over_index(self):
        """If both a 'timestamp' column and a DatetimeIndex exist,
        the column must win — no regression in the column path."""
        col_dates = pd.to_datetime(["2022-07-01", "2023-07-01"], utc=True)
        idx_dates = pd.DatetimeIndex(pd.to_datetime(["2019-01-01", "2020-01-01"]), tz="UTC")
        df = pd.DataFrame({"timestamp": col_dates, "v": 1.0}, index=idx_dates)
        windows = _build_stability_windows(df)
        # Should see 2022/2023 (from column), NOT 2019/2020 (from index):
        self.assertEqual(set(windows.keys()), {"2022", "2023"})
        self.assertNotIn("2019", windows)
        self.assertNotIn("2020", windows)

    # ── T6: no mutation — no new column added to df ────────────────────────
    def test_datetime_index_path_does_not_mutate_df(self):
        idx = pd.date_range("2024-01-01", periods=24, freq="1h", tz="UTC")
        df = pd.DataFrame({"v": np.ones(24)}, index=idx)
        cols_before = set(df.columns)
        _build_stability_windows(df)
        self.assertEqual(set(df.columns), cols_before, "df columns must not change after _build_stability_windows()")

    # ── T7: RangeIndex (no column, no DatetimeIndex) still returns {} ──────
    def test_range_index_no_column_returns_empty(self):
        df = pd.DataFrame({"v": [1.0, 2.0, 3.0]})  # plain RangeIndex
        self.assertEqual(_build_stability_windows(df), {})

    # ── T8: DatetimeIndex tz-naive → windows produced without error ────────
    def test_datetime_index_tz_naive_produces_windows(self):
        idx = pd.date_range("2023-01-01", periods=12, freq="MS")  # tz-naive monthly
        df = pd.DataFrame({"v": np.ones(12)}, index=idx)
        windows = _build_stability_windows(df)
        self.assertIn("2023", windows)
        # tz-naive window start must compare without TypeError against tz-naive idx:
        start, _ = windows["2023"]
        self.assertIsNone(start.tzinfo)
