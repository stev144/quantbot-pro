# claude code changed: new file — Research Agent architecture Phase 1
# (published blueprint, §04): walk_forward_engine.py had zero automated
# tests despite its own comments documenting a real, already-fixed bug —
# a lag-1 OU half-life regression on noisy hourly spread data reporting
# implausible ~2.5h half-lives (against a 119.9h full-sample reference),
# silently collapsing the derived time-stop and forcing trades to exit
# before genuine reversion could happen. The sanity band
# (HALF_LIFE_SANITY_MIN_HOURS / HALF_LIFE_SANITY_MAX_RATIO) is the fix.
#
# SCOPE NOTE: this file covers every pure, self-contained piece of the
# module — pair discovery, the Missing-Piece-4 gate, anchored fold
# construction, the OU half-life re-calibration (including its sanity
# band regression), and the OOS equity/verdict aggregation math. It does
# NOT cover _run_single_fold()/run() end-to-end, since those require
# bot.research.entry_exit_engine.EntryExitEngine — a separate, large
# trade-simulation module outside this pass's scope. That integration
# path is a known coverage gap, not an oversight.

import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.walk_forward_engine import (  # claude code changed: module under test
    WalkForwardEngine, discover_pairs, pair_deserves_testing,
    HALF_LIFE_SANITY_MIN_HOURS, VALIDATED_HALF_LIFE, HALF_LIFE_SANITY_MAX_RATIO,
)


class DiscoverPairsTest(SimpleTestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep fixtures out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp fixture dir

    def test_discovers_only_kalman_suffixed_files_and_strips_pair_label(self):
        Path(self.tmp_dir, "AVAX_USDT_ATOM_USDT_kalman.csv").write_text("x")  # claude code changed: matching file
        Path(self.tmp_dir, "SOL_USDT_LINK_USDT_kalman.csv").write_text("x")  # claude code changed: second matching file
        Path(self.tmp_dir, "AVAX_USDT_ATOM_USDT_cointegration.csv").write_text("x")  # claude code changed: unrelated file, must be ignored

        pairs = discover_pairs(research_data_dir=self.tmp_dir)

        self.assertEqual(set(pairs.keys()), {"AVAX_USDT_ATOM_USDT", "SOL_USDT_LINK_USDT"})  # claude code changed: suffix stripped, unrelated file excluded

    def test_missing_directory_returns_empty_dict_not_crash(self):
        pairs = discover_pairs(research_data_dir=str(Path(self.tmp_dir) / "does_not_exist"))
        self.assertEqual(pairs, {})  # claude code changed: fail-closed, not a crash


class PairDeservesTestingTest(SimpleTestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep fixtures out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp fixture dir

    def _write_summary(self, pair_label, win_rate, sharpe, profit_factor, max_dd):
        pd.DataFrame([{
            "win_rate": win_rate, "sharpe_ratio": sharpe,
            "profit_factor": profit_factor, "max_drawdown_pct": max_dd,
        }]).to_csv(Path(self.tmp_dir) / f"{pair_label}_strategy_summary.csv", index=False)

    def test_no_summary_file_allows_testing_with_warning_reason(self):
        deserves, reason = pair_deserves_testing("NEW_PAIR_USDT_X_USDT", research_data_dir=self.tmp_dir)
        self.assertTrue(deserves)  # claude code changed: absence of a gate file isn't disqualifying
        self.assertIn("No entry_exit_engine.py summary found", reason)

    def test_passing_summary_allows_testing(self):
        self._write_summary("PAIR_A", win_rate=0.65, sharpe=1.2, profit_factor=1.8, max_dd=-0.10)  # claude code changed: clears every threshold
        deserves, reason = pair_deserves_testing("PAIR_A", research_data_dir=self.tmp_dir)
        self.assertTrue(deserves)

    def test_failing_summary_blocks_testing(self):
        self._write_summary("PAIR_B", win_rate=0.40, sharpe=0.5, profit_factor=1.0, max_dd=-0.35)  # claude code changed: fails every threshold
        deserves, reason = pair_deserves_testing("PAIR_B", research_data_dir=self.tmp_dir)
        self.assertFalse(deserves)
        self.assertIn("failed", reason)


class GenerateFoldsTest(SimpleTestCase):
    """Anchored, expanding walk-forward fold construction — train always
    starts at data_start, test window is fixed-length, and a final
    too-short trailing window is skipped rather than padded."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep engine output out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp output dir

    def test_anchored_expansion_with_final_partial_window_skipped(self):
        idx = pd.date_range("2020-01-01", "2024-06-01", freq="1D")  # claude code changed: ~4.4 years, daily freq keeps the fixture small
        df_full = pd.DataFrame({"kalman_spread": np.zeros(len(idx))}, index=idx)

        engine = WalkForwardEngine(  # claude code changed: module under test
            min_train_years=2, test_window_years=1, min_test_candles=300, output_dir=self.tmp_dir,
        )
        folds = engine._generate_folds(df_full)

        # claude code changed: 2020-01-01+2y=2022-01-01 (fold1 test 2022->2023, ~365
        # days, kept) -> train_end=2023-01-01 (fold2 test 2023->2024, ~365 days,
        # kept) -> train_end=2024-01-01 (fold3 test 2024-01-01->2024-06-01, ~152
        # days < 300, skipped) -> loop ends since train_end==data_end.
        self.assertEqual(len(folds), 2)

        train_start_1, train_end_1, test_start_1, test_end_1 = folds[0]
        self.assertEqual(train_start_1, idx[0])  # claude code changed: train always anchored at data_start
        self.assertEqual(train_end_1, pd.Timestamp("2022-01-01"))
        self.assertEqual(test_start_1, train_end_1)  # claude code changed: test begins exactly where train ends

        train_start_2, train_end_2, test_start_2, test_end_2 = folds[1]
        self.assertEqual(train_start_2, idx[0])  # claude code changed: still anchored at data_start, not fold1's train_end
        self.assertEqual(train_end_2, pd.Timestamp("2023-01-01"))  # claude code changed: expanded to cover fold1's test window

        self.assertLessEqual(folds[-1][3], idx[-1])  # claude code changed: no fold's test_end ever exceeds data_end

    def test_too_little_data_for_even_one_fold_returns_empty(self):
        idx = pd.date_range("2020-01-01", "2020-06-01", freq="1D")  # claude code changed: 5 months, below min_train_years=2
        df_full = pd.DataFrame({"kalman_spread": np.zeros(len(idx))}, index=idx)

        engine = WalkForwardEngine(min_train_years=2, test_window_years=1, output_dir=self.tmp_dir)  # claude code changed: module under test
        folds = engine._generate_folds(df_full)

        self.assertEqual(folds, [])  # claude code changed: train_end computed from data_start would exceed data_end -> loop body never runs


class EstimateHalfLifeTest(SimpleTestCase):
    """Regression coverage for the documented, already-fixed sanity-band
    bug: a statistically significant negative slope is not, by itself,
    sufficient — an implausibly fast or slow half-life must be rejected."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep engine output out of research_data/
        self.engine = WalkForwardEngine(output_dir=self.tmp_dir)  # claude code changed: module under test

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp output dir

    def _simulate_ou_spread(self, n, phi, seed, initial_value=50.0, sigma=1e-4):
        """AR(1) spread_t = phi * spread_{t-1} + eps_t, started from a large
        nonzero deviation with tiny noise — an essentially deterministic
        exponential decay toward zero. True half-life (in candles) =
        ln(2) / -ln(phi) is then recoverable precisely by the regression
        regardless of random seed, avoiding sampling-noise flakiness for
        slow (long half-life) cases where a noisy AR(1) is hard to
        distinguish from a random walk over a finite sample."""
        rng = np.random.default_rng(seed)  # claude code changed: reproducible tiny-noise perturbation
        eps = rng.normal(0, sigma, n)
        spread = np.zeros(n)
        spread[0] = initial_value  # claude code changed: large starting deviation drives a clean, precisely-fit decay
        for t in range(1, n):
            spread[t] = phi * spread[t - 1] + eps[t]
        idx = pd.date_range("2020-01-01", periods=n, freq="1h")
        return pd.Series(spread, index=idx)

    def test_genuine_mean_reversion_recovered_within_sane_band(self):
        true_half_life_candles = 100  # claude code changed: comfortably inside [24h, 119.9*5=599.5h]
        phi = np.exp(-np.log(2) / true_half_life_candles)  # claude code changed: AR(1) coefficient implied by the target half-life
        spread = self._simulate_ou_spread(n=1000, phi=phi, seed=1)

        half_life = self.engine._estimate_half_life_hours(spread)

        self.assertFalse(pd.isna(half_life))  # claude code changed: must not be rejected — this is a genuine, plausible reversion
        self.assertAlmostEqual(half_life, true_half_life_candles, delta=2)  # claude code changed: near-deterministic decay recovers precisely

    def test_implausibly_fast_decay_rejected_by_sanity_band(self):
        """Direct regression test for the historical bug: a genuinely fast-
        decaying AR(1) series (analogous to the real ~2.5h noise-decay
        case) is statistically significant but must still be rejected as
        implausible for this pair's spread."""
        phi = np.exp(-np.log(2) / 2)  # claude code changed: true half-life ~2 candles, well below HALF_LIFE_SANITY_MIN_HOURS
        spread = self._simulate_ou_spread(n=200, phi=phi, seed=2)

        half_life = self.engine._estimate_half_life_hours(spread)

        self.assertTrue(pd.isna(half_life))  # claude code changed: must be rejected, not silently accepted

    def test_implausibly_slow_decay_rejected_by_sanity_band(self):
        phi = np.exp(-np.log(2) / 700)  # claude code changed: true half-life ~700h, beyond VALIDATED_HALF_LIFE * 5 = 599.5h
        spread = self._simulate_ou_spread(n=3000, phi=phi, seed=3)

        half_life = self.engine._estimate_half_life_hours(spread)

        self.assertTrue(pd.isna(half_life))  # claude code changed: must be rejected as implausibly slow

    def test_non_mean_reverting_random_walk_returns_nan(self):
        rng = np.random.default_rng(4)  # claude code changed: reproducible synthetic spread
        spread = pd.Series(
            np.cumsum(rng.normal(0, 1, 1000)),  # claude code changed: pure random walk, no reversion at all
            index=pd.date_range("2020-01-01", periods=1000, freq="1h"),
        )

        half_life = self.engine._estimate_half_life_hours(spread)

        self.assertTrue(pd.isna(half_life))  # claude code changed: b_coefficient should not be significantly negative

    def test_too_few_points_returns_nan(self):
        spread = pd.Series(np.arange(10, dtype=float), index=pd.date_range("2020-01-01", periods=10, freq="1h"))  # claude code changed: below the 30-point minimum
        half_life = self.engine._estimate_half_life_hours(spread)
        self.assertTrue(pd.isna(half_life))  # claude code changed: guard clause, not a crash


class BuildOosEquityCurveTest(SimpleTestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep engine output out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp output dir

    def test_cumulative_equity_and_drawdown_hand_computed(self):
        idx = pd.date_range("2020-01-01", periods=4, freq="1D")
        oos_trades = pd.DataFrame({
            "exit_timestamp": idx,
            "fold_id":         [1, 1, 2, 2],
            "trade_id":         [1, 2, 1, 2],
            "net_pnl_usdt":      [100.0, -50.0, 200.0, -300.0],  # claude code changed: deliberately dips below the running peak on the last trade
        })

        engine = WalkForwardEngine(capital_usdt=1000.0, output_dir=self.tmp_dir)  # claude code changed: module under test
        equity = engine._build_oos_equity_curve(oos_trades)

        # claude code changed: cumulative_usdt = 1000 +100 -50 +200 -300 = [1100, 1050, 1250, 950]
        expected_cumulative = [1100.0, 1050.0, 1250.0, 950.0]
        self.assertEqual(list(equity["cumulative_usdt"]), expected_cumulative)

        # claude code changed: rolling peak = [1100, 1100, 1250, 1250]; last row drawdown = (950-1250)/1250
        self.assertAlmostEqual(equity["drawdown_pct"].iloc[-1], (950.0 - 1250.0) / 1250.0, places=10)
        self.assertEqual(equity["drawdown_pct"].iloc[0], 0.0)  # claude code changed: first row is its own peak, zero drawdown

    def test_empty_trades_returns_empty_dataframe(self):
        engine = WalkForwardEngine(output_dir=self.tmp_dir)  # claude code changed: module under test
        equity = engine._build_oos_equity_curve(pd.DataFrame())
        self.assertTrue(equity.empty)  # claude code changed: guard clause, not a crash on empty input


class PrintVerdictTest(SimpleTestCase):
    """Pure aggregation/threshold logic over already-computed fold and OOS
    DataFrames — exercised directly, without needing a real fold run."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep engine output out of research_data/
        self.engine = WalkForwardEngine(output_dir=self.tmp_dir)  # claude code changed: module under test

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp output dir

    def test_zero_oos_trades_fails_immediately(self):
        fold_df = pd.DataFrame({"skipped": [True]})
        verdict = self.engine._print_verdict("TEST_PAIR", fold_df, pd.DataFrame(), pd.DataFrame())
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["reason"], "zero OOS trades")

    def test_strong_oos_performance_passes_every_threshold(self):
        fold_df = pd.DataFrame({"skipped": [False, False], "train_sharpe": [1.5, 1.6]})
        entries = pd.date_range("2022-01-01", periods=20, freq="10D")
        exits = entries + pd.Timedelta(hours=4)
        # claude code changed: 15 winners / 5 losers = 75% win rate, comfortably
        # clears every OOS threshold (win rate, Sharpe, profit factor, drawdown).
        pnl_pct = [0.03] * 15 + [-0.01] * 5
        oos_trades = pd.DataFrame({
            "entry_timestamp": entries, "exit_timestamp": exits, "net_pnl_pct": pnl_pct,
        })
        oos_equity = pd.DataFrame({"drawdown_pct": [0.0, -0.02, -0.01]})  # claude code changed: shallow drawdown, clears OOS_MAX_DRAWDOWN_PCT

        verdict = self.engine._print_verdict("TEST_PAIR", fold_df, oos_trades, oos_equity)

        self.assertTrue(verdict["passed"])
        self.assertAlmostEqual(verdict["oos_win_rate"], 0.75)

    def test_weak_oos_performance_fails(self):
        fold_df = pd.DataFrame({"skipped": [False], "train_sharpe": [1.5]})
        entries = pd.date_range("2022-01-01", periods=10, freq="10D")
        exits = entries + pd.Timedelta(hours=4)
        # claude code changed: 3 winners / 7 losers -> 30% win rate, well below OOS_WIN_RATE_MIN
        pnl_pct = [0.01] * 3 + [-0.02] * 7
        oos_trades = pd.DataFrame({
            "entry_timestamp": entries, "exit_timestamp": exits, "net_pnl_pct": pnl_pct,
        })
        oos_equity = pd.DataFrame({"drawdown_pct": [0.0, -0.30]})  # claude code changed: breaches OOS_MAX_DRAWDOWN_PCT too

        verdict = self.engine._print_verdict("TEST_PAIR", fold_df, oos_trades, oos_equity)

        self.assertFalse(verdict["passed"])
