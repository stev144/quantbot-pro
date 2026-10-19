"""
bot/tests/test_fx_permutation_test.py

Unit tests for bot/research/fx_permutation_test.py.
Covers block shuffle shape/consistency, FDR-BH logic, Stouffer, and
the permutation test runner on a small synthetic dataset.

These tests do NOT touch the permutation runner with full OHLCV data —
the calibration run is too slow for the unit-test suite.  The integration
run lives in the scratchpad runner script.
"""  # claude code changed: new file — tests for FX permutation test module

import math

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.fx_permutation_test import (
    block_shuffle_ohlcv,
    compute_fdr_bh,
    _stouffer_combine,
    _prepare_sim_df,
)


def _make_ohlcv(n: int = 200, seed: int = 0) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame with DatetimeIndex."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n, freq="1h", tz="UTC")
    close = 1.10 + np.cumsum(rng.normal(0, 0.0001, n))
    open_ = close + rng.normal(0, 0.00005, n)
    high = np.maximum(open_, close) + rng.uniform(0, 0.0002, n)
    low = np.minimum(open_, close) - rng.uniform(0, 0.0002, n)
    vol = rng.uniform(100, 1000, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


# ── block_shuffle_ohlcv ───────────────────────────────────────────────────────

class BlockShuffleTests(SimpleTestCase):  # claude code changed: new test class

    def test_shape_preserved(self):
        df = _make_ohlcv(200)
        rng = np.random.default_rng(42)
        shuffled = block_shuffle_ohlcv(df, block_size=48, rng=rng)
        self.assertEqual(shuffled.shape, df.shape)

    def test_index_unchanged(self):
        df = _make_ohlcv(200)
        rng = np.random.default_rng(42)
        shuffled = block_shuffle_ohlcv(df, block_size=48, rng=rng)
        pd.testing.assert_index_equal(shuffled.index, df.index)

    def test_multirow_candle_consistency(self):
        """Every shuffled row's open/high/low/close come from the SAME original row."""
        df = _make_ohlcv(200)
        # Embed a unique candle marker: row index * 100 in volume
        df["volume"] = np.arange(len(df), dtype=float) * 100.0
        rng = np.random.default_rng(7)
        shuffled = block_shuffle_ohlcv(df, block_size=12, rng=rng)
        # For each shuffled row, the original row index should be vol/100
        for i in range(len(shuffled)):
            orig_idx = int(round(shuffled["volume"].iloc[i] / 100))
            self.assertAlmostEqual(shuffled["close"].iloc[i], df["close"].iloc[orig_idx], places=10)
            self.assertAlmostEqual(shuffled["open"].iloc[i], df["open"].iloc[orig_idx], places=10)

    def test_all_rows_present(self):
        """Every original row should appear exactly once in the shuffled df."""
        df = _make_ohlcv(100)
        df["volume"] = np.arange(100, dtype=float)
        rng = np.random.default_rng(1)
        shuffled = block_shuffle_ohlcv(df, block_size=10, rng=rng)
        volumes = sorted(shuffled["volume"].tolist())
        self.assertEqual(volumes, list(range(100)))

    def test_content_changes_with_shuffle(self):
        """Shuffled content differs from original (statistically almost certain)."""
        df = _make_ohlcv(200)
        rng = np.random.default_rng(99)
        shuffled = block_shuffle_ohlcv(df, block_size=24, rng=rng)
        self.assertFalse((shuffled["close"].values == df["close"].values).all())

    def test_empty_df_returns_copy(self):
        df = pd.DataFrame({"open": [], "high": [], "low": [], "close": []})
        rng = np.random.default_rng(0)
        result = block_shuffle_ohlcv(df, block_size=48, rng=rng)
        self.assertEqual(len(result), 0)

    def test_single_block_identity(self):
        """If data fits in one block, shuffled = original (only one block to shuffle)."""
        df = _make_ohlcv(20)
        rng = np.random.default_rng(5)
        # block_size >= n means one block, shuffle is identity
        shuffled = block_shuffle_ohlcv(df, block_size=100, rng=rng)
        np.testing.assert_array_almost_equal(shuffled["close"].values, df["close"].values)

    def test_different_seeds_differ(self):
        df = _make_ohlcv(200)
        rng1 = np.random.default_rng(11)
        rng2 = np.random.default_rng(22)
        s1 = block_shuffle_ohlcv(df, 24, rng1)
        s2 = block_shuffle_ohlcv(df, 24, rng2)
        self.assertFalse((s1["close"].values == s2["close"].values).all())


# ── _prepare_sim_df ───────────────────────────────────────────────────────────

class PreparSimDfTests(SimpleTestCase):  # claude code changed: new test class

    def test_timestamp_column_becomes_index(self):
        idx = pd.date_range("2021-01-01", periods=10, freq="1h", tz="UTC")
        df = pd.DataFrame({
            "timestamp": idx, "open": 1.0, "high": 1.01,
            "low": 0.99, "close": 1.001,
        })
        result = _prepare_sim_df(df)
        self.assertIsInstance(result.index, pd.DatetimeIndex)
        self.assertNotIn("timestamp", result.columns)

    def test_already_datetimeindex_passthrough(self):
        idx = pd.date_range("2021-01-01", periods=10, freq="1h", tz="UTC")
        df = pd.DataFrame({"open": 1.0, "high": 1.01, "low": 0.99, "close": 1.001}, index=idx)
        result = _prepare_sim_df(df)
        pd.testing.assert_index_equal(result.index, df.index)


# ── compute_fdr_bh ────────────────────────────────────────────────────────────

class FdrBhTests(SimpleTestCase):  # claude code changed: new test class

    def test_empty_list(self):
        self.assertEqual(compute_fdr_bh([]), [])

    def test_all_significant(self):
        # p-values well below α=0.05
        result = compute_fdr_bh([0.001, 0.002, 0.003], alpha=0.05)
        self.assertTrue(all(result))

    def test_all_non_significant(self):
        result = compute_fdr_bh([0.9, 0.8, 0.7], alpha=0.05)
        self.assertFalse(any(result))

    def test_nan_never_rejected(self):
        result = compute_fdr_bh([float("nan"), 0.001], alpha=0.05)
        self.assertFalse(result[0])
        self.assertTrue(result[1])

    def test_bh_step_up(self):
        # With m=5, rank 1 threshold = 0.05/5=0.01, rank 2=0.02, rank 3=0.03
        # p=[0.005, 0.025, 0.04, 0.05, 0.9]
        # sorted: 0.005(rank1,thr=0.01✓), 0.025(rank2,thr=0.02✗)...
        # last_reject=rank1 only → only index 0 rejected? No:
        # BH step-up: all ranks up to last_reject are rejected.
        # rank1: 0.005<=0.01 ✓ → last_reject=1; nothing higher passes
        # So only the first (p=0.005) is rejected
        result = compute_fdr_bh([0.005, 0.025, 0.04, 0.05, 0.9], alpha=0.05)
        self.assertTrue(result[0])
        self.assertFalse(result[1])

    def test_bh_rejects_multiple_when_trailing_pass(self):
        # m=3, α=0.05: thresholds 0.05/3≈0.0167, 0.1/3≈0.0333, 0.15/3=0.05
        # p=[0.01, 0.03, 0.04]: sorted with ranks 1,2,3
        # rank1: 0.01<=0.0167✓, rank2: 0.03<=0.0333✓, rank3: 0.04<=0.05✓
        # last_reject=3 → all rejected
        result = compute_fdr_bh([0.01, 0.03, 0.04], alpha=0.05)
        self.assertTrue(all(result))


# ── _stouffer_combine ─────────────────────────────────────────────────────────

class StoufferTests(SimpleTestCase):  # claude code changed: new test class

    def test_empty_returns_nan(self):
        z, p = _stouffer_combine([])
        self.assertTrue(math.isnan(z))
        self.assertTrue(math.isnan(p))

    def test_all_nan_returns_nan(self):
        z, p = _stouffer_combine([float("nan"), float("nan")])
        self.assertTrue(math.isnan(z))

    def test_single_p_roundtrips(self):
        # Stouffer with one p-value should return ~ same p (apart from float)
        from scipy.stats import norm
        p_in = 0.05
        z, p_out = _stouffer_combine([p_in])
        self.assertAlmostEqual(p_out, p_in, places=8)

    def test_very_small_p_produces_large_z(self):
        z, p = _stouffer_combine([0.001, 0.002])
        self.assertGreater(z, 2.5)
        self.assertLess(p, 0.01)

    def test_large_p_produces_non_significant(self):
        z, p = _stouffer_combine([0.5, 0.6, 0.7])
        self.assertGreater(p, 0.05)
