"""
bot/tests/test_fx_holdout_guard.py
Tests for the FX holdout guard (F2 remediation).

Verifies:
  1. Calibration-only DataFrames pass the guard
  2. Any holdout row raises FxHoldoutSealedError
  3. FxSplits.calibration passes the guard
  4. FxSplits.holdout raises when passed to assert_no_fx_holdout
  5. FxDataRole enum has the expected members
  6. FxHoldoutGuard correctly rejects duplicate timestamps in make_fx_splits (F7)
  7. make_fx_holdout_guard derives the earliest holdout start across pairs
  8. FxSplits.holdout_start property is consistent with the guard boundary

These tests do NOT run DCA simulations, access holdout performance,
or examine any strategy results.
"""
# claude code changed: new file — 2026-10-16, F2 holdout guard tests

from __future__ import annotations

from datetime import timezone
from unittest import TestCase

import pandas as pd

from bot.research.fx_holdout_guard import (
    FxDataRole,
    FxHoldoutGuard,
    FxHoldoutSealedError,
    make_fx_holdout_guard,
)
from bot.research.fx_fold_splitter import make_fx_splits, FxSplits


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_df(start: str, periods: int, freq: str = "1h") -> pd.DataFrame:
    """Minimal OHLC DataFrame with UTC timestamps."""  # claude code changed: test helper
    ts = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    price = 1.1000 + (pd.RangeIndex(periods) * 0.0001)
    return pd.DataFrame({
        "timestamp": ts,
        "open": price,
        "high": price + 0.0002,
        "low": price - 0.0002,
        "close": price + 0.0001,
        "volume": 100,
    })


def _make_large_df(n: int = 38_000) -> pd.DataFrame:
    """Large enough DataFrame for make_fx_splits."""  # claude code changed: test helper
    return _make_df("2020-01-01", n)


# ── FxDataRole tests ──────────────────────────────────────────────────────────

class FxDataRoleTests(TestCase):  # claude code changed: new test class — FxDataRole enum
    def test_expected_members_exist(self):
        self.assertIn("CALIBRATION", FxDataRole.__members__)
        self.assertIn("CALIBRATION_EMBARGO", FxDataRole.__members__)
        self.assertIn("HOLDOUT", FxDataRole.__members__)

    def test_values_are_strings(self):
        for member in FxDataRole:
            self.assertIsInstance(member.value, str)


# ── FxHoldoutGuard unit tests ─────────────────────────────────────────────────

class FxHoldoutGuardTests(TestCase):  # claude code changed: new test class — guard unit tests
    HOLDOUT_START = pd.Timestamp("2024-12-24 00:00:00", tz="UTC")

    def setUp(self):
        self.guard = FxHoldoutGuard(self.HOLDOUT_START)

    def test_calibration_only_df_passes(self):
        """DataFrame entirely before holdout_start must pass."""
        df = _make_df("2024-12-23", periods=20)  # ends before holdout
        self.guard.assert_no_fx_holdout(df, purpose="test_calibration_only")

    def test_single_holdout_row_raises(self):
        """One row at the holdout boundary must raise."""
        df = _make_df("2024-12-24", periods=1)
        with self.assertRaises(FxHoldoutSealedError):
            self.guard.assert_no_fx_holdout(df, purpose="test_single_holdout_row")

    def test_mixed_df_raises(self):
        """DataFrame spanning calibration and holdout must raise."""
        cal = _make_df("2024-12-23 00:00", periods=1)
        hld = _make_df("2024-12-24 00:00", periods=1)
        df = pd.concat([cal, hld], ignore_index=True)
        with self.assertRaises(FxHoldoutSealedError):
            self.guard.assert_no_fx_holdout(df, purpose="test_mixed_df")

    def test_empty_df_passes(self):
        df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])
        self.guard.assert_no_fx_holdout(df, purpose="test_empty")

    def test_none_df_passes(self):
        self.guard.assert_no_fx_holdout(None, purpose="test_none")

    def test_is_calibration_safe_true_for_pre_holdout(self):
        df = _make_df("2024-12-01", periods=10)
        self.assertTrue(self.guard.is_calibration_safe(df))

    def test_is_calibration_safe_false_for_holdout(self):
        df = _make_df("2024-12-24", periods=5)
        self.assertFalse(self.guard.is_calibration_safe(df))

    def test_error_message_contains_holdout_start(self):
        df = _make_df("2024-12-24", periods=1)
        with self.assertRaises(FxHoldoutSealedError) as cm:
            self.guard.assert_no_fx_holdout(df, purpose="msg_test")
        self.assertIn("2024-12-24", str(cm.exception))

    def test_error_message_contains_purpose(self):
        df = _make_df("2024-12-24", periods=1)
        with self.assertRaises(FxHoldoutSealedError) as cm:
            self.guard.assert_no_fx_holdout(df, purpose="MY_UNIQUE_PURPOSE")
        self.assertIn("MY_UNIQUE_PURPOSE", str(cm.exception))

    def test_naive_timestamps_treated_as_utc(self):
        """Naive timestamps should be coerced to UTC, not rejected."""
        df = _make_df("2024-12-23", periods=5)
        df["timestamp"] = df["timestamp"].dt.tz_localize(None)  # strip timezone
        # Should pass (entirely before holdout)
        self.guard.assert_no_fx_holdout(df, purpose="test_naive_utc")

    def test_guard_holdout_start_property(self):
        self.assertEqual(self.guard.holdout_start, self.HOLDOUT_START)


# ── FxSplits integration tests ────────────────────────────────────────────────

class FxSplitsHoldoutGuardIntegrationTests(TestCase):  # claude code changed: new — integration with FxSplits
    def _get_splits(self) -> FxSplits:
        df = _make_large_df(38_000)
        return make_fx_splits(df)

    def test_calibration_passes_guard(self):
        """FxSplits.calibration must pass an FxHoldoutGuard derived from splits."""
        splits = self._get_splits()
        guard = make_fx_holdout_guard({"TEST": splits})
        guard.assert_no_fx_holdout(splits.calibration, purpose="test_calibration_passes")

    def test_holdout_raises_guard(self):
        """FxSplits.holdout must raise FxHoldoutSealedError via the guard."""
        splits = self._get_splits()
        guard = make_fx_holdout_guard({"TEST": splits})
        with self.assertRaises(FxHoldoutSealedError):
            guard.assert_no_fx_holdout(splits.holdout, purpose="test_holdout_raises")

    def test_calibration_role_is_correct(self):
        splits = self._get_splits()
        self.assertEqual(splits.calibration_role, FxDataRole.CALIBRATION)

    def test_embargo_role_is_correct(self):
        splits = self._get_splits()
        self.assertEqual(splits.embargo_role, FxDataRole.CALIBRATION_EMBARGO)

    def test_holdout_role_is_correct(self):
        splits = self._get_splits()
        self.assertEqual(splits.holdout_role, FxDataRole.HOLDOUT)

    def test_holdout_start_property_matches_guard(self):
        splits = self._get_splits()
        guard = make_fx_holdout_guard({"TEST": splits})
        self.assertEqual(splits.holdout_start, guard.holdout_start)


# ── make_fx_holdout_guard tests ───────────────────────────────────────────────

class MakeFxHoldoutGuardTests(TestCase):  # claude code changed: new — guard factory tests
    def test_uses_earliest_holdout_across_pairs(self):
        """Guard must use the EARLIEST holdout start when pairs differ."""
        # Pair A: smaller dataset → earlier holdout start
        df_a = _make_large_df(37_460)
        df_b = _make_large_df(37_462)
        splits_a = make_fx_splits(df_a)
        splits_b = make_fx_splits(df_b)
        guard = make_fx_holdout_guard({"A": splits_a, "B": splits_b})
        # Guard must be at or before the earliest holdout start of either pair
        self.assertLessEqual(guard.holdout_start, splits_a.holdout_start)
        self.assertLessEqual(guard.holdout_start, splits_b.holdout_start)

    def test_empty_splits_raises(self):
        with self.assertRaises(ValueError):
            make_fx_holdout_guard({})

    def test_single_pair(self):
        df = _make_large_df(37_461)
        splits = make_fx_splits(df)
        guard = make_fx_holdout_guard({"EURUSD": splits})
        self.assertEqual(guard.holdout_start, splits.holdout_start)


# ── F7: duplicate timestamp rejection in make_fx_splits ──────────────────────

class FxFoldSplitterDuplicateTimestampTests(TestCase):  # claude code changed: new — F7 duplicate rejection
    def test_duplicate_timestamps_raise(self):
        df = _make_large_df(38_000)
        # Introduce a duplicate by appending the first row
        df_duped = pd.concat([df, df.iloc[:1]], ignore_index=True)
        with self.assertRaises(ValueError) as cm:
            make_fx_splits(df_duped)
        self.assertIn("duplicate", str(cm.exception).lower())

    def test_clean_data_does_not_raise(self):
        df = _make_large_df(38_000)
        # Should complete without error
        splits = make_fx_splits(df)
        self.assertEqual(splits.total_candles, 38_000)
