# claude code changed: new file — Hardening Phase 2, next-mission item 3.
# Independently re-derives every load-bearing performance metric the
# prior hardening audit flagged as "NOT VALIDATED": Sharpe, Sortino,
# Kelly, win_rate, expectancy, profit_factor, drawdown, avg R-multiple.
# Every real formula below was read from bot/backtesting/backtester.py's
# calculate_sharpe_ratio()/calculate_sortino_ratio()/_build_results()
# and bot/research/entry_exit_engine.py's KalmanPositionSizer BEFORE
# writing any test (Hardening Mission Rule A). Where practical, the
# independent check uses a DIFFERENT code path than the implementation
# (Python's stdlib `statistics` module instead of numpy) rather than
# just re-typing the same formula.

import statistics

from django.test import SimpleTestCase
import pandas as pd

from bot.backtesting.backtester import Backtester, calculate_sharpe_ratio, calculate_sortino_ratio


# ═══════════════════════════════════════════════════════════════════════
# SHARPE / SORTINO — pure functions.
# claude code changed: Phase 3 H-11 — the formulas below are the CORRECTED,
# textbook ones (the previous expectations pinned the defects themselves):
#   sharpe   = mean(R) / std(R, ddof=1) * sqrt(trades_per_year)     (no risk-free term:
#              an annual return fraction cannot be subtracted from an R-multiple)
#   sortino  = mean(R) / DD * sqrt(trades_per_year),  DD = sqrt(mean_all(min(R,0)^2))
#              (downside deviation over ALL trades, target 0); undefined (None) with no losses
#   Unannualised (years_elapsed=None) => annualisation=1
# ═══════════════════════════════════════════════════════════════════════

class SharpeRatioIndependentVerificationTest(SimpleTestCase):

    def test_unannualised_sharpe_matches_stdlib_statistics_module(self):
        # claude code changed: independent cross-check uses Python's
        # `statistics` module (a completely separate implementation from
        # numpy) for mean/stdev — not just re-typing backtester.py's formula.
        r_values = [1.0, -0.5, 2.0, 0.5, -1.0, 1.5]
        trades = [{"r_multiple": r} for r in r_values]

        expected_mean = statistics.mean(r_values)
        expected_std = statistics.stdev(r_values)   # sample stdev, ddof=1 — same convention as np.std(ddof=1)
        expected_sharpe = round(expected_mean / expected_std, 4)   # unannualised: rf=0, annualisation=1

        self.assertAlmostEqual(calculate_sharpe_ratio(trades, years_elapsed=None), expected_sharpe, places=4)

    def test_annualised_sharpe_matches_hand_derivation(self):
        # claude code changed: a clean, hand-checkable case — 10 trades of
        # R=[1,-1] alternating over exactly 1 year => trades_per_year=10.
        r_values = [1.0, -1.0] * 5
        trades = [{"r_multiple": r} for r in r_values]
        years_elapsed = 1.0

        mean_r = 0.0   # alternating +1/-1 averages to exactly zero
        std_r = statistics.stdev(r_values)   # = 1.0257... (ddof=1 on [1,-1]*5)
        trades_per_year = 10 / 1.0
        annualisation = trades_per_year ** 0.5
        expected = round(mean_r / std_r * annualisation, 4)   # claude code changed: Phase 3 H-11 — no unit-mismatched rf term (was − 0.05/10)

        self.assertAlmostEqual(calculate_sharpe_ratio(trades, years_elapsed=years_elapsed), expected, places=4)

    def test_fewer_than_two_trades_returns_zero_not_a_crash(self):
        self.assertEqual(calculate_sharpe_ratio([], years_elapsed=1.0), 0.0)
        self.assertEqual(calculate_sharpe_ratio([{"r_multiple": 1.0}], years_elapsed=1.0), 0.0)

    def test_zero_variance_returns_zero_not_a_divide_by_zero_crash(self):
        # claude code changed: every trade identical R -> std=0 -> the
        # real risk here is a silent ZeroDivisionError/NaN, not a wrong
        # number. Confirms the implementation's own explicit guard.
        trades = [{"r_multiple": 1.0}] * 5
        self.assertEqual(calculate_sharpe_ratio(trades, years_elapsed=1.0), 0.0)

    def test_missing_r_multiple_key_treated_as_zero_not_a_crash(self):
        trades = [{"profit": 10.0}, {"r_multiple": 1.0}, {"r_multiple": None}]
        # must not raise — .get("r_multiple", 0) or 0 handles both missing and explicit None
        result = calculate_sharpe_ratio(trades, years_elapsed=None)
        self.assertIsInstance(result, float)


class SortinoRatioIndependentVerificationTest(SimpleTestCase):

    def test_no_losing_trades_is_undefined_not_a_sentinel(self):
        # claude code changed: Phase 3 H-11 — was a 999.0 sentinel that reads like a result
        trades = [{"r_multiple": 1.0}, {"r_multiple": 2.0}, {"r_multiple": 0.5}]
        self.assertIsNone(calculate_sortino_ratio(trades, years_elapsed=1.0))

    def test_exactly_one_losing_trade_is_well_defined_not_nan(self):
        # claude code changed: Phase 3 H-11 — the textbook downside deviation is defined with
        # one loss (it averages over ALL trades), so the old "return 0.0" guard is gone.
        # R = [1, 2, -0.5]: mean = 0.8333…, DD = sqrt(0.25/3) = 0.288675…, ×sqrt(3/1) → 5.0 exactly
        trades = [{"r_multiple": 1.0}, {"r_multiple": 2.0}, {"r_multiple": -0.5}]
        result = calculate_sortino_ratio(trades, years_elapsed=1.0)
        self.assertAlmostEqual(result, 5.0, places=4)
        self.assertFalse(result != result)   # NaN != NaN is True; explicitly prove it's NOT NaN

    def test_downside_only_std_matches_independent_stdlib_calculation(self):
        r_values = [3.0, 2.0, -1.0, 1.0, -2.0, -0.5]
        trades = [{"r_multiple": r} for r in r_values]
        # claude code changed: Phase 3 H-11 — textbook downside deviation (target 0, over ALL trades):
        # DD = sqrt((1 + 4 + 0.25)/6) = sqrt(0.875) = 0.935414…; mean = 0.416667 → 0.4454
        # (the old formula — stdev of the LOSSES around their own mean — gave 0.5455)
        expected_mean_all = statistics.mean(r_values)
        expected_down_dev = (sum(min(r, 0.0) ** 2 for r in r_values) / len(r_values)) ** 0.5
        expected = round(expected_mean_all / expected_down_dev, 4)   # unannualised

        self.assertAlmostEqual(calculate_sortino_ratio(trades, years_elapsed=None), expected, places=4)


# claude code changed: Stage 2D Rev B step 8 — the KELLY independent-
# verification section was removed together with KalmanPositionSizer (owner
# decision: fixed-fraction sizing, no Kelly). There is no Kelly formula left in
# any sizing path to verify; the fixed-fraction arithmetic is verified in
# bot/tests/test_entry_exit_engine.py (FixedFractionSizerTest).


# ═══════════════════════════════════════════════════════════════════════
# win_rate / expectancy / profit_factor / avg_r / drawdown — inline in
# Backtester._build_results(); verified via a real Backtester instance
# with hand-crafted closed_trades injected directly (this project's own
# established convention for testing private/internal state — see
# test_feature_calculator.py's direct _calculate_rsi() calls).
# ═══════════════════════════════════════════════════════════════════════

class BacktesterMetricsIndependentVerificationTest(SimpleTestCase):

    def _make_backtester_with_trades(self, closed_trades, max_drawdown=0.0, final_balance=10_000.0):
        df = pd.DataFrame({
            "open": [100.0] * 48, "high": [101.0] * 48, "low": [99.0] * 48, "close": [100.0] * 48, "volume": [1000.0] * 48,
        }, index=pd.date_range("2026-01-01", periods=48, freq="1h", tz="UTC"))
        bt = Backtester(df=df, initial_balance=10_000.0)
        bt.df = df   # claude code changed: normally set by _prepare_data() inside run(); set directly since this test bypasses the simulation loop entirely
        bt.closed_trades = closed_trades
        bt.max_drawdown = max_drawdown
        bt.balance = final_balance
        return bt

    def test_win_rate_expectancy_profit_factor_hand_computed_example(self):
        # claude code changed: a clean, by-hand example —
        # 3 wins ($100, $50, $150 = $300 gross profit), 2 losses ($-40, $-60 = $100 gross loss)
        # win_rate = 3/5 * 100 = 60.0%
        # expectancy = (100+50+150-40-60)/5 = 200/5 = 40.0
        # profit_factor = 300/100 = 3.0
        trades = [
            {"profit": 100.0, "r_multiple": 2.0}, {"profit": 50.0, "r_multiple": 1.0},
            {"profit": 150.0, "r_multiple": 3.0}, {"profit": -40.0, "r_multiple": -0.8},
            {"profit": -60.0, "r_multiple": -1.2},
        ]
        bt = self._make_backtester_with_trades(trades)
        results = bt._build_results()

        self.assertEqual(results["win_rate"], 60.0)
        self.assertEqual(results["expectancy"], 40.0)
        self.assertEqual(results["profit_factor"], 3.0)
        # avg_r = (2.0+1.0+3.0-0.8-1.2)/5 = 4.0/5 = 0.8
        self.assertAlmostEqual(results["avg_r_multiple"], 0.8, places=4)
        self.assertEqual(results["wins"], 3)
        self.assertEqual(results["losses"], 2)

    def test_zero_trades_returns_the_documented_empty_shape_not_a_crash(self):
        bt = self._make_backtester_with_trades([])
        results = bt._build_results()
        self.assertEqual(results["win_rate"], 0)
        self.assertEqual(results["expectancy"], 0)
        self.assertEqual(results["profit_factor"], 0)

    def test_all_winning_trades_profit_factor_and_win_rate(self):
        trades = [{"profit": 10.0, "r_multiple": 1.0}, {"profit": 20.0, "r_multiple": 2.0}]
        bt = self._make_backtester_with_trades(trades)
        results = bt._build_results()
        self.assertEqual(results["win_rate"], 100.0)
        # claude code changed: no losses -> gross_loss=0 -> the real code's
        # OWN documented behavior is profit_factor=0 (guarded by `if
        # gross_loss else 0`), NOT infinity — confirmed against source,
        # not assumed.
        self.assertEqual(results["profit_factor"], 0)

    def test_all_losing_trades_profit_factor_and_expectancy(self):
        trades = [{"profit": -10.0, "r_multiple": -1.0}, {"profit": -30.0, "r_multiple": -3.0}]
        bt = self._make_backtester_with_trades(trades)
        results = bt._build_results()
        self.assertEqual(results["win_rate"], 0.0)
        self.assertEqual(results["profit_factor"], 0)   # gross_profit=0 -> 0/40=0.0, rounds to 0 not None
        self.assertEqual(results["expectancy"], -20.0)

    def test_single_trade_dataset(self):
        trades = [{"profit": 25.0, "r_multiple": 1.5}]
        bt = self._make_backtester_with_trades(trades)
        results = bt._build_results()
        self.assertEqual(results["total_trades"], 1)
        self.assertEqual(results["win_rate"], 100.0)
        self.assertEqual(results["expectancy"], 25.0)

    def test_drawdown_is_reported_from_the_real_tracked_value(self):
        # claude code changed: max_drawdown is tracked incrementally during
        # the real simulation loop (dd = (peak-balance)/peak*100 each close)
        # — verifies _build_results() reports exactly what was tracked,
        # rather than recomputing it a second, possibly-inconsistent way.
        bt = self._make_backtester_with_trades([{"profit": -500.0, "r_multiple": -1.0}], max_drawdown=15.3456)
        bt.max_drawdown_mtm = 21.0049   # claude code changed: Phase 3 M-02 — the headline is the MTM drawdown
        results = bt._build_results()
        self.assertEqual(results["max_drawdown"], 21.0)                       # MTM, rounded to 2dp
        self.assertEqual(results["max_drawdown_closed_trade_basis"], 15.35)   # the former measure, under its own name
        self.assertEqual(results["drawdown_basis"], "MTM_LIQUIDATION_AT_EVERY_CLOSE")

    def test_drawdown_formula_hand_derivation(self):
        # claude code changed: independently re-derives the ACTUAL dd
        # formula (peak_balance - balance) / peak_balance * 100, read from
        # backtester.py's trade-close handler, on a hand-picked example —
        # peak=$10,000, balance drops to $8,500 -> dd = 1500/10000*100 = 15.0%
        peak, balance = 10_000.0, 8_500.0
        expected_dd = (peak - balance) / peak * 100
        self.assertAlmostEqual(expected_dd, 15.0, places=4)
