# claude code changed: new file — Research Agent architecture Phase 1
# (published blueprint, §04): feature_calculator.py had zero automated
# tests despite being a candidate AI-callable tool. These verify a few
# indicators against hand-computed reference values on small fixed
# series, not just "it doesn't crash" — plus the guard clauses and the
# feature/label separation the module's own docstring claims.

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.feature_calculator import FeatureCalculator  # claude code changed: module under test


class RsiHandComputedTest(SimpleTestCase):
    # claude code changed: period=3 (alpha=0.5) keeps the EWM recursion
    # tractable to verify by hand; RSI_PERIOD=14 in production is the
    # same formula, just a different alpha.

    def test_rsi_matches_hand_computed_ewm_recursion(self):
        closes = pd.Series([10.0, 11.0, 10.0, 12.0, 13.0])  # claude code changed: fixed series, deltas [nan,1,-1,2,1]
        df = pd.DataFrame({"close": closes})
        calc = FeatureCalculator(min_data_required=1)  # claude code changed: bypass min-data guard for direct private-method test

        rsi = calc._calculate_rsi(df, period=3)

        # claude code changed: hand-derived via ewm(span=3, adjust=False), alpha=0.5 —
        # see fork report for the full recursion; expected non-NaN values below.
        self.assertTrue(np.isnan(rsi.iloc[0]))  # claude code changed: avg_loss=0 at t=0 -> rs undefined
        self.assertTrue(np.isnan(rsi.iloc[1]))  # claude code changed: avg_loss still 0 at t=1
        self.assertAlmostEqual(rsi.iloc[2], 33.3333, places=3)  # claude code changed: rs=0.5 -> 100-100/1.5
        self.assertAlmostEqual(rsi.iloc[3], 81.8182, places=3)  # claude code changed: rs=4.5 -> 100-100/5.5
        self.assertAlmostEqual(rsi.iloc[4], 89.4737, places=3)  # claude code changed: rs=8.5 -> 100-100/9.5

    def test_rsi_clipped_to_0_100_range(self):
        closes = pd.Series([float(x) for x in range(1, 30)])  # claude code changed: monotonic uptrend -> RSI approaches 100
        df = pd.DataFrame({"close": closes})
        calc = FeatureCalculator(min_data_required=1)  # claude code changed: direct private-method test

        rsi = calc._calculate_rsi(df, period=14)

        self.assertTrue((rsi.dropna() <= 100).all())  # claude code changed: clip upper bound holds
        self.assertTrue((rsi.dropna() >= 0).all())  # claude code changed: clip lower bound holds


class AtrHandComputedTest(SimpleTestCase):

    def test_atr_matches_hand_computed_true_range(self):
        df = pd.DataFrame({  # claude code changed: fixed OHLC series, TR hand-derived in fork report
            "high":  [10.0, 12.0, 11.0, 13.0],
            "low":   [8.0, 9.0, 10.0, 11.0],
            "close": [9.0, 11.0, 10.0, 12.0],
        })
        calc = FeatureCalculator(min_data_required=1)  # claude code changed: direct private-method test

        atr = calc._calculate_atr(df, period=2)

        # claude code changed: TR = [2, 3, 1, 3] (row-wise max of the three TR
        # components, NaN-skipped on row 0); ATR = rolling(2).mean() of TR.
        self.assertTrue(np.isnan(atr.iloc[0]))  # claude code changed: rolling window not yet full
        self.assertAlmostEqual(atr.iloc[1], 2.5)  # claude code changed: mean(2, 3)
        self.assertAlmostEqual(atr.iloc[2], 2.0)  # claude code changed: mean(3, 1)
        self.assertAlmostEqual(atr.iloc[3], 2.0)  # claude code changed: mean(1, 3)


class ForwardReturnTest(SimpleTestCase):

    def test_forward_return_matches_hand_computed_ratio(self):
        df = pd.DataFrame({"close": [100.0, 110.0, 121.0]})  # claude code changed: +10% each step, exact ratios
        calc = FeatureCalculator(min_data_required=1)  # claude code changed: direct private-method test

        fwd = calc._calculate_forward_return(df, periods=1)

        self.assertAlmostEqual(fwd.iloc[0], 0.10)  # claude code changed: 110/100 - 1
        self.assertAlmostEqual(fwd.iloc[1], 0.10)  # claude code changed: 121/110 - 1
        self.assertTrue(np.isnan(fwd.iloc[2]))  # claude code changed: no future close available

    def test_forward_return_is_the_only_feature_using_future_data(self):
        """
        Behavioral leakage guard: every OTHER feature column produced by
        calculate_all_features must be computable from data available up
        to and including the current row — i.e. truncating the input
        DataFrame at any row t must not change that same row's value for
        any column except the explicitly forward-looking labels.
        """
        rng = np.random.default_rng(11)  # claude code changed: reproducible synthetic OHLCV
        n = 150
        returns = rng.normal(0, 0.01, n)
        close = 100.0 * np.exp(np.cumsum(returns))
        df = pd.DataFrame({
            "open": close * (1 + rng.normal(0, 0.001, n)),  # claude code changed: synthetic OHLCV
            "high": close * (1 + np.abs(rng.normal(0, 0.003, n))),
            "low": close * (1 - np.abs(rng.normal(0, 0.003, n))),
            "close": close,
            "volume": rng.uniform(1000, 5000, n),
        })

        calc = FeatureCalculator(min_data_required=100)  # claude code changed: matches production default
        full = calc.calculate_all_features(df, symbol="TEST")

        label_cols = {  # claude code changed: forward-looking by design and documented as such
            "forward_return_1h", "forward_return_4h", "forward_return_24h",
            "win_1h", "win_4h", "win_24h",
        }
        cutoff = 120  # claude code changed: arbitrary truncation point with enough trailing history
        truncated = calc.calculate_all_features(df.iloc[:cutoff + 1], symbol="TEST")

        for col in full.columns:
            if col in label_cols:
                continue  # claude code changed: labels are allowed to differ/be NaN near a truncated end
            full_val = full[col].iloc[cutoff]
            trunc_val = truncated[col].iloc[cutoff]
            if pd.isna(full_val) and pd.isna(trunc_val):
                continue  # claude code changed: both NaN is consistent, not a leakage signal
            self.assertAlmostEqual(
                full_val, trunc_val, places=8,
                msg=f"column '{col}' at row {cutoff} changed when future rows were removed — look-ahead leak",
            )  # claude code changed: core leakage assertion


class GuardClauseTest(SimpleTestCase):

    def test_insufficient_data_returns_input_unchanged(self):
        df = pd.DataFrame({"open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0], "close": [1.0, 2.0], "volume": [1.0, 2.0]})  # claude code changed: 2 rows, below default min_data_required
        calc = FeatureCalculator(min_data_required=100)  # claude code changed: production default

        result = calc.calculate_all_features(df, symbol="TEST")

        pd.testing.assert_frame_equal(result, df)  # claude code changed: guard clause returns df unchanged, not a partial/crashed frame

    def test_missing_ohlcv_columns_returns_input_unchanged(self):
        df = pd.DataFrame({"close": list(range(200))})  # claude code changed: enough rows but missing open/high/low/volume
        calc = FeatureCalculator(min_data_required=100)  # claude code changed: production default

        result = calc.calculate_all_features(df, symbol="TEST")

        pd.testing.assert_frame_equal(result, df)  # claude code changed: guard clause returns df unchanged


class WinLabelConsistencyTest(SimpleTestCase):

    def test_win_flags_exactly_match_sign_of_forward_return(self):
        rng = np.random.default_rng(3)  # claude code changed: reproducible synthetic OHLCV
        n = 200
        returns = rng.normal(0, 0.01, n)
        close = 100.0 * np.exp(np.cumsum(returns))
        df = pd.DataFrame({
            "open": close, "high": close * 1.001, "low": close * 0.999,
            "close": close, "volume": rng.uniform(1000, 5000, n),
        })
        calc = FeatureCalculator(min_data_required=100)  # claude code changed: production default

        result = calc.calculate_all_features(df, symbol="TEST")

        for horizon in ("1h", "4h", "24h"):
            fwd_col, win_col = f"forward_return_{horizon}", f"win_{horizon}"
            valid = result[fwd_col].notna()
            expected = result.loc[valid, fwd_col] > 0
            pd.testing.assert_series_equal(
                result.loc[valid, win_col], expected, check_names=False,
                obj=f"{win_col} must exactly equal ({fwd_col} > 0)",
            )  # claude code changed: real behavioral property, not shape/type only
