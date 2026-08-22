# claude code changed: new file — Research Agent architecture Phase 1
# (published blueprint, §04): permutation_test_engine.py had zero
# automated tests despite its own comments documenting a real,
# already-fixed bug — independently shuffling kalman_zscore_lag1 by the
# same block-index map as every other column fed the entry-confirmation
# filter a stale, non-adjacent lag value at every block boundary (a full
# 25% of rows at a 4-candle block), biasing every shuffle's entry IC
# consistently negative and producing a misleadingly suspicious-looking
# permutation-test result. The fix recomputes lag1 from the
# already-shuffled zscore series instead of shuffling it independently.
#
# SCOPE NOTE: covers _block_shuffle() (the structural fix above) and
# _compare() (the percentile/p-value/edge_appears_real significance
# logic) — both pure, self-contained, and the two places most directly
# tied to this module's documented statistical-integrity purpose. Does
# NOT cover run()/_run_engine_on_df() end-to-end, since those require
# bot.research.entry_exit_engine.EntryExitEngine — a separate, large
# trade-simulation module outside this pass's scope (same scoping
# decision as test_walk_forward_engine.py).

import shutil
import tempfile

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.permutation_test_engine import (  # claude code changed: module under test
    PermutationTestEngine, REQUIRED_KALMAN_COLUMNS, SIGNIFICANCE_PERCENTILE,
)


def _make_marked_df(n=24):
    """Every REQUIRED_KALMAN_COLUMNS column set to the row's own integer
    position (0..n-1) — a distinctive marker that makes it easy to prove
    block-internal contiguity survives shuffling, and that every required
    column moves together as a unit."""
    idx = pd.date_range("2020-01-01", periods=n, freq="1h")
    data = {col: np.arange(n, dtype=float) for col in REQUIRED_KALMAN_COLUMNS}
    return pd.DataFrame(data, index=idx)


class BlockShuffleTest(SimpleTestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep engine output out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp output dir

    def test_shape_and_chronological_index_preserved(self):
        df = _make_marked_df(24)
        engine = PermutationTestEngine(  # claude code changed: module under test
            block_size_candles=4, random_seed=1, output_dir=self.tmp_dir,
        )
        shuffled = engine._block_shuffle(df)

        self.assertEqual(len(shuffled), len(df))  # claude code changed: no rows gained/lost
        pd.testing.assert_index_equal(shuffled.index, df.index)  # claude code changed: timestamps stay in original chronological order

    def test_single_block_is_a_no_op(self):
        df = _make_marked_df(20)
        engine = PermutationTestEngine(block_size_candles=20, random_seed=1, output_dir=self.tmp_dir)  # claude code changed: block_size == n -> only one possible "shuffle"

        shuffled = engine._block_shuffle(df)

        pd.testing.assert_series_equal(shuffled["kalman_beta"], df["kalman_beta"], check_names=False)  # claude code changed: a single block has nowhere else to go

    def test_block_internal_contiguity_preserved(self):
        """Even though blocks get relocated, the VALUES WITHIN a block must
        stay in their original relative order — proving the shuffle moves
        whole blocks, not individual rows, at this block size."""
        n, block_size = 24, 4
        df = _make_marked_df(n)
        engine = PermutationTestEngine(block_size_candles=block_size, random_seed=7, output_dir=self.tmp_dir)  # claude code changed: module under test

        shuffled = engine._block_shuffle(df)
        marker = shuffled["kalman_beta"].to_numpy()

        for block_start in range(0, n, block_size):
            block = marker[block_start:block_start + block_size]
            diffs = np.diff(block)
            self.assertTrue(np.all(diffs == 1), f"block at {block_start} is not a contiguous original run: {block}")  # claude code changed: consecutive original integers = untouched internal order

    def test_lag1_recomputed_from_shuffled_zscore_not_independently_shuffled(self):
        """Direct regression test for the historical bug: kalman_zscore_lag1
        must exactly equal the shifted, ALREADY-SHUFFLED kalman_zscore at
        every row — never a value carried over from the pre-shuffle
        block-index map, which would create boundary mismatches."""
        n, block_size = 40, 3  # claude code changed: small block relative to n -> many boundaries, exactly where the historical bug showed up
        df = _make_marked_df(n)
        df["kalman_zscore"] = np.arange(n, dtype=float) * 1.1  # claude code changed: distinct, non-integer values so accidental matches are implausible
        engine = PermutationTestEngine(block_size_candles=block_size, random_seed=3, output_dir=self.tmp_dir)  # claude code changed: module under test

        shuffled = engine._block_shuffle(df)
        expected_lag1 = shuffled["kalman_zscore"].shift(1).bfill()

        pd.testing.assert_series_equal(
            shuffled["kalman_zscore_lag1"], expected_lag1, check_names=False,
            obj="kalman_zscore_lag1 must exactly track the shuffled kalman_zscore's own lag, at every row",
        )

    def test_required_columns_move_together_as_a_unit(self):
        """kalman_beta and kalman_spread both carry the same row-position
        marker — after shuffling, a given row's values for both columns
        must still match each other, proving they were relocated in
        lockstep rather than independently."""
        df = _make_marked_df(30)
        engine = PermutationTestEngine(block_size_candles=5, random_seed=9, output_dir=self.tmp_dir)  # claude code changed: module under test

        shuffled = engine._block_shuffle(df)

        pd.testing.assert_series_equal(
            shuffled["kalman_beta"], shuffled["kalman_spread"], check_names=False,
            obj="all non-lag1 required columns must be relocated together, not independently",
        )


class CompareSignificanceTest(SimpleTestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep engine output out of research_data/
        self.engine = PermutationTestEngine(output_dir=self.tmp_dir)  # claude code changed: module under test

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp output dir

    def _shuffled_dicts(self, win_rates, sharpes, profit_factors):
        return [
            {"win_rate": w, "sharpe_ratio": s, "profit_factor": p}
            for w, s, p in zip(win_rates, sharpes, profit_factors)
        ]

    def test_real_result_dominating_shuffles_is_significant_and_edge_appears_real(self):
        shuffled = self._shuffled_dicts(
            win_rates=list(np.linspace(0.30, 0.50, 100)),  # claude code changed: real result (0.90) beats every single shuffle
            sharpes=list(np.linspace(-0.5, 0.5, 100)),
            profit_factors=list(np.linspace(0.8, 1.2, 100)),
        )
        real = {"win_rate": 0.90, "sharpe_ratio": 3.0, "profit_factor": 3.0, "entry_ic": 0.25}  # claude code changed: dominates the shuffled distribution on every metric, real IC clearly positive

        verdict = self.engine._compare(real, shuffled)

        self.assertAlmostEqual(verdict["win_rate_percentile"], 1.0)  # claude code changed: beats every shuffle
        self.assertLess(verdict["win_rate_p_value"], 1 - SIGNIFICANCE_PERCENTILE)  # claude code changed: p < 0.05
        self.assertTrue(verdict["win_rate_significant"])
        self.assertTrue(verdict["sharpe_significant"])
        self.assertTrue(verdict["real_entry_ic_meaningfully_positive"])
        self.assertTrue(verdict["edge_appears_real"])  # claude code changed: all three conditions hold

    def test_real_result_typical_of_shuffles_is_not_significant(self):
        rng = np.random.default_rng(5)  # claude code changed: reproducible synthetic distribution
        shuffled = self._shuffled_dicts(
            win_rates=list(rng.normal(0.55, 0.05, 100)),
            sharpes=list(rng.normal(1.0, 0.3, 100)),
            profit_factors=list(rng.normal(1.4, 0.2, 100)),
        )
        real = {"win_rate": 0.55, "sharpe_ratio": 1.0, "profit_factor": 1.4, "entry_ic": 0.3}  # claude code changed: sits right in the middle of the shuffled distribution — not distinguishable from noise

        verdict = self.engine._compare(real, shuffled)

        self.assertFalse(verdict["edge_appears_real"])  # claude code changed: typical-of-shuffles result must not be called real

    def test_weak_entry_ic_fails_even_with_significant_win_rate_and_sharpe(self):
        """entry_ic <= 0.1 must veto edge_appears_real regardless of how
        strong the shuffled-distribution comparison looks — the module's
        own design treats real IC near/below zero as disqualifying on its
        own terms."""
        shuffled = self._shuffled_dicts(
            win_rates=list(np.linspace(0.30, 0.50, 100)),
            sharpes=list(np.linspace(-0.5, 0.5, 100)),
            profit_factors=list(np.linspace(0.8, 1.2, 100)),
        )
        real = {"win_rate": 0.90, "sharpe_ratio": 3.0, "profit_factor": 3.0, "entry_ic": 0.05}  # claude code changed: dominates shuffles but IC is weak

        verdict = self.engine._compare(real, shuffled)

        self.assertTrue(verdict["win_rate_significant"])  # claude code changed: shuffled comparison alone looks strong
        self.assertTrue(verdict["sharpe_significant"])
        self.assertFalse(verdict["real_entry_ic_meaningfully_positive"])  # claude code changed: 0.05 is not > 0.1
        self.assertFalse(verdict["edge_appears_real"])  # claude code changed: IC veto wins regardless of the other two

    def test_empty_shuffled_values_do_not_crash(self):
        real = {"win_rate": 0.90, "sharpe_ratio": 3.0, "profit_factor": 3.0, "entry_ic": 0.3}
        verdict = self.engine._compare(real, [])  # claude code changed: zero shuffles produced any usable result

        self.assertTrue(pd.isna(verdict["win_rate_percentile"]))  # claude code changed: fail closed to NaN, not a crash
        self.assertFalse(verdict["win_rate_significant"])
        self.assertFalse(verdict["edge_appears_real"])  # claude code changed: can't claim a real edge with nothing to compare against
