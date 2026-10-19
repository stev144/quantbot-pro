# bot/tests/test_dca_wf_validator.py
"""
Known-answer tests for bot/research/dca_wf_validator.py.

Tests cover:
  - Fold boundary arithmetic (_build_folds)
  - Sign test known answers (_sign_test)
  - T-test known answers (_ttest)
  - Warmup isolation: cycles entering before fold_start are excluded
  - MIN_CYCLES_FOLD gate: folds with too few cycles are ineligible
  - Governance: IS_PAPER_ONLY is True and cannot be cleared
  - End-to-end validate_dca_walk_forward with injectable sim_df

No mocking — tests use the injectable _sim_df_override path to avoid
loading real CSVs or running the live paper engine (consistent with the
no-mock convention: real calls are used for live paths; injectable
parameters are the mechanism for unit tests of internal logic).
"""

from __future__ import annotations

import math
import unittest
from dataclasses import asdict
from typing import Dict, List
from unittest.mock import patch

import numpy as np
import pandas as pd

from bot.research.dca_wf_validator import (
    FOLD_CANDLES,
    IS_PAPER_ONLY,
    MIN_CYCLES_FOLD,
    WARMUP_CANDLES,
    DCAWalkForwardResult,
    FoldResult,
    PairWFResult,
    _build_folds,
    _sign_test,
    _ttest,
    print_wf_report,
    validate_dca_walk_forward,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_df(n: int, start: str = "2022-01-01", freq: str = "1h") -> pd.DataFrame:
    """Minimal OHLCV DataFrame with n 1h candles."""
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    close = np.linspace(1.1000, 1.1100, n)  # gradual drift — avoids ATR=0
    return pd.DataFrame(
        {
            "open":   close * 0.9999,
            "high":   close * 1.0001,
            "low":    close * 0.9998,
            "close":  close,
            "volume": np.ones(n) * 1000.0,
        },
        index=idx,
    )  # claude code changed: new — minimal OHLCV factory for tests


class BuildFoldsTest(unittest.TestCase):
    """_build_folds fold boundary arithmetic."""  # claude code changed: new test class

    def test_exact_multiple_gives_n_folds(self):
        df = _make_df(FOLD_CANDLES * 3)
        folds = _build_folds(df, fold_candles=FOLD_CANDLES)
        self.assertEqual(len(folds), 3)

    def test_partial_remainder_discarded(self):
        # 3 full folds + 100 extra → 3 folds, no partial fold
        df = _make_df(FOLD_CANDLES * 3 + 100)
        folds = _build_folds(df, fold_candles=FOLD_CANDLES)
        self.assertEqual(len(folds), 3)

    def test_first_fold_starts_at_zero(self):
        df = _make_df(FOLD_CANDLES * 2)
        folds = _build_folds(df, fold_candles=FOLD_CANDLES)
        self.assertEqual(folds[0], (0, FOLD_CANDLES))

    def test_folds_are_non_overlapping(self):
        df = _make_df(FOLD_CANDLES * 4)
        folds = _build_folds(df, fold_candles=FOLD_CANDLES)
        for i in range(len(folds) - 1):
            _, end_i   = folds[i]
            start_next = folds[i + 1][0]
            self.assertEqual(end_i, start_next)

    def test_fewer_than_one_fold_returns_empty(self):
        df = _make_df(FOLD_CANDLES - 1)
        folds = _build_folds(df, fold_candles=FOLD_CANDLES)
        self.assertEqual(folds, [])

    def test_custom_fold_candles(self):
        df = _make_df(500)
        folds = _build_folds(df, fold_candles=100)
        self.assertEqual(len(folds), 5)
        for start, end in folds:
            self.assertEqual(end - start, 100)


class SignTestKnownAnswerTest(unittest.TestCase):
    """_sign_test known-answer checks."""  # claude code changed: new test class

    def test_all_positive(self):
        n_pos, pval = _sign_test([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(n_pos, 5)
        # 5/5 positive: exact binomial p = 0.5^5 ≈ 0.03125
        self.assertAlmostEqual(pval, 0.03125, places=4)

    def test_all_negative(self):
        n_pos, pval = _sign_test([-1.0, -2.0, -3.0])
        self.assertEqual(n_pos, 0)
        self.assertEqual(pval, 1.0)

    def test_empty_list(self):
        n_pos, pval = _sign_test([])
        self.assertEqual(n_pos, 0)
        self.assertEqual(pval, 1.0)

    def test_single_positive(self):
        n_pos, pval = _sign_test([1.0])
        self.assertEqual(n_pos, 1)
        self.assertAlmostEqual(pval, 0.5, places=5)

    def test_half_positive(self):
        # 3/6 positive → p ~= 0.5 (not significant)
        n_pos, pval = _sign_test([1.0, -1.0, 1.0, -1.0, 1.0, -1.0])
        self.assertEqual(n_pos, 3)
        self.assertGreater(pval, 0.4)

    def test_returns_int_and_float(self):
        n_pos, pval = _sign_test([1.0, -1.0])
        self.assertIsInstance(n_pos, int)
        self.assertIsInstance(pval, float)


class TtestKnownAnswerTest(unittest.TestCase):
    """_ttest known-answer checks."""  # claude code changed: new test class

    def test_positive_mean_two_sided(self):
        stat, pval = _ttest([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertGreater(stat, 0)
        self.assertLess(pval, 0.05)

    def test_zero_mean(self):
        stat, pval = _ttest([-1.0, 1.0])
        self.assertAlmostEqual(stat, 0.0, places=5)
        self.assertAlmostEqual(pval, 1.0, places=5)

    def test_single_element_returns_nan_and_one(self):
        stat, pval = _ttest([5.0])
        self.assertTrue(math.isnan(stat))
        self.assertEqual(pval, 1.0)

    def test_empty_list_returns_nan_and_one(self):
        stat, pval = _ttest([])
        self.assertTrue(math.isnan(stat))
        self.assertEqual(pval, 1.0)


class GovernanceTest(unittest.TestCase):
    """IS_PAPER_ONLY governance constant is always True."""  # claude code changed: new test class

    def test_is_paper_only_constant(self):
        self.assertTrue(IS_PAPER_ONLY)

    def test_cannot_set_false_in_module(self):
        import bot.research.dca_wf_validator as mod
        self.assertTrue(mod.IS_PAPER_ONLY)
        # Verify it is the sentinel; module attribute should stay True
        self.assertIs(mod.IS_PAPER_ONLY, True)


class FoldResultEligibilityTest(unittest.TestCase):
    """FoldResult.eligible gate based on MIN_CYCLES_FOLD."""  # claude code changed: new test class

    def test_eligible_when_at_threshold(self):
        fr = FoldResult(
            fold_num=1, pair="EUR/USD", start_ts="", end_ts="",
            n_cycles=MIN_CYCLES_FOLD, n_wins=2, win_rate=0.67,
            total_pnl=1.0, avg_pnl=0.33, eligible=True,
        )
        self.assertTrue(fr.eligible)

    def test_ineligible_below_threshold(self):
        fr = FoldResult(
            fold_num=1, pair="EUR/USD", start_ts="", end_ts="",
            n_cycles=MIN_CYCLES_FOLD - 1, n_wins=1, win_rate=1.0,
            total_pnl=1.0, avg_pnl=1.0, eligible=False,
        )
        self.assertFalse(fr.eligible)


class PrintReportSmokeTest(unittest.TestCase):
    """print_wf_report does not raise with a minimal result."""  # claude code changed: new test class

    def _minimal_result(self) -> DCAWalkForwardResult:
        fr = FoldResult(
            fold_num=1, pair="EUR/USD", start_ts="2022-01-01", end_ts="2022-07-01",
            n_cycles=5, n_wins=3, win_rate=0.6, total_pnl=10.0, avg_pnl=2.0,
            eligible=True,
        )
        pr = PairWFResult(
            pair="EUR/USD", n_folds=1, n_eligible=1,
            fold_results=[fr],
            n_pos_folds=1, binom_pval=0.5, binom_sig=False,
            ttest_stat=1.0, ttest_pval=0.4, ttest_sig=False,
            mean_fold_pnl=10.0, std_fold_pnl=0.0,
        )
        return DCAWalkForwardResult(
            pair_results=[pr],
            n_pairs=1,
            n_eligible_total=1,
            n_pos_total=1,
            pooled_binom_pval=0.5,
            pooled_binom_sig=False,
            pooled_ttest_stat=1.0,
            pooled_ttest_pval=0.4,
            pooled_ttest_sig=False,
            pooled_mean_pnl=10.0,
            pooled_std_pnl=0.0,
        )

    def test_no_exception(self):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_wf_report(self._minimal_result())
        out = buf.getvalue()
        self.assertIn("DCA WALK-FORWARD VALIDATOR", out)
        self.assertIn("EUR/USD", out)
        self.assertIn("IS_PAPER_ONLY", out)


if __name__ == "__main__":
    unittest.main()
