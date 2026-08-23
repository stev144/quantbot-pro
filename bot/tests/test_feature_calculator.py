# claude code changed: new file — Research Agent architecture Phase 1
# (published blueprint, §04): feature_calculator.py had zero automated
# tests despite being a candidate AI-callable tool. These verify a few
# indicators against hand-computed reference values on small fixed
# series, not just "it doesn't crash" — plus the guard clauses and the
# feature/label separation the module's own docstring claims.

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.instruments import ASSET_CLASS_CRYPTO, ASSET_CLASS_US_EQUITY
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


# claude code changed: new — Multi-Asset Foundation Refactor Phase 1B,
# Objective 3. _calculate_realized_vol() used a hardcoded sqrt(252)
# regardless of timeframe or asset class — wrong even for this platform's
# own crypto/1h data (crypto trades 24/7/365; 1h candles need x24
# scaling, not x1). These tests hand-verify the exact annualization
# factor for the brief's own required cases (1h crypto, daily
# equity-style, another timeframe) against deterministic synthetic data,
# by asserting the RESULT equals the raw (unannualized) std times the
# exact expected sqrt(periods_per_year) — never just "it doesn't crash."
class RealizedVolAnnualizationTest(SimpleTestCase):

    def _synthetic_df(self, n=60, seed=7):
        rng = np.random.default_rng(seed)  # claude code changed: reproducible
        close = 100 + np.cumsum(rng.normal(0, 1, n))
        return pd.DataFrame({"close": close})

    def _raw_std(self, df, period=20):
        return df["close"].pct_change().rolling(window=period).std()

    def test_1h_crypto_annualizes_by_sqrt_24x365(self):
        """The brief's own required example: 1h crypto ~= 24 x 365
        observations/year."""
        df = self._synthetic_df()
        calc = FeatureCalculator(min_data_required=1)

        result = calc._calculate_realized_vol(df, timeframe="1h", asset_class=ASSET_CLASS_CRYPTO)
        expected = self._raw_std(df) * np.sqrt(24 * 365)

        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_daily_equity_style_data_annualizes_by_sqrt_252(self):
        """The brief's own required example: US equities ~= 252 trading
        sessions/year for daily observations."""
        df = self._synthetic_df()
        calc = FeatureCalculator(min_data_required=1)

        result = calc._calculate_realized_vol(df, timeframe="1d", asset_class=ASSET_CLASS_US_EQUITY)
        expected = self._raw_std(df) * np.sqrt(252)

        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_daily_crypto_annualizes_by_sqrt_365_not_252(self):
        """A second, distinct timeframe (daily crypto) — 365 days/year,
        never silently reusing the equity 252-day convention just because
        both are 'daily' bars."""
        df = self._synthetic_df()
        calc = FeatureCalculator(min_data_required=1)

        result = calc._calculate_realized_vol(df, timeframe="1d", asset_class=ASSET_CLASS_CRYPTO)
        expected = self._raw_std(df) * np.sqrt(365)

        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_4h_crypto_annualizes_by_sqrt_6x365(self):
        """A third timeframe: 4h candles -> 6 candles/day on a 24/7 market."""
        df = self._synthetic_df()
        calc = FeatureCalculator(min_data_required=1)

        result = calc._calculate_realized_vol(df, timeframe="4h", asset_class=ASSET_CLASS_CRYPTO)
        expected = self._raw_std(df) * np.sqrt(6 * 365)

        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_no_asset_class_falls_back_to_legacy_252_not_a_crash(self):
        """Backward compatibility: every pre-Phase-1B caller (unit tests
        using symbol='TEST', any code not passing a real symbol) keeps
        getting exactly the old numeric behavior."""
        df = self._synthetic_df()
        calc = FeatureCalculator(min_data_required=1)

        result = calc._calculate_realized_vol(df, timeframe="1h", asset_class=None)
        expected = self._raw_std(df) * np.sqrt(252)

        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_intraday_equity_has_no_session_model_yet_falls_back_honestly(self):
        """No market-session-length model exists for intraday equities —
        must fall back to the legacy constant (logged), never silently
        apply crypto's 24/7 assumption to a market that isn't one."""
        df = self._synthetic_df()
        calc = FeatureCalculator(min_data_required=1)

        result = calc._calculate_realized_vol(df, timeframe="1h", asset_class=ASSET_CLASS_US_EQUITY)
        expected = self._raw_std(df) * np.sqrt(252)

        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_volatility_state_classification_is_unaffected_by_annualization_factor(self):
        """The one feature that CONSUMES realized_vol internally
        (volatility_state) must classify identically regardless of
        annualization factor — it's a ratio of realized_vol to its own
        rolling mean, and the constant sqrt(factor) term cancels out of
        that ratio exactly. Proves the annualization fix changes
        realized_vol's absolute value without silently changing this
        derived classification's behavior."""
        rng = np.random.default_rng(11)
        n = 200
        close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        df = pd.DataFrame({
            "open": close, "high": close * 1.001, "low": close * 0.999,
            "close": close, "volume": rng.uniform(1000, 5000, n),
        })
        calc = FeatureCalculator(min_data_required=100)

        crypto_1h = calc.calculate_all_features(df.copy(), symbol="BTC/USDT", timeframe="1h")
        unresolved = calc.calculate_all_features(df.copy(), symbol="TEST")

        self.assertGreater(crypto_1h["realized_vol"].dropna().iloc[-1], unresolved["realized_vol"].dropna().iloc[-1] * 2)  # genuinely different absolute values
        valid = crypto_1h["volatility_state"].notna() & unresolved["volatility_state"].notna()
        pd.testing.assert_series_equal(
            crypto_1h.loc[valid, "volatility_state"], unresolved.loc[valid, "volatility_state"],
            check_names=False, obj="volatility_state must classify identically regardless of annualization factor",
        )
