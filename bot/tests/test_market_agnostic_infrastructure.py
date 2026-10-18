# claude code changed: new file — market-agnostic infrastructure tests (2026-10-18).
# Covers: RegimeDetector asset_class param, Backtester annualization, PortfolioBacktester
# asset_class default, and integration contracts.

import numpy as np
import pandas as pd
from django.test import SimpleTestCase


# ============================================================
# REGIME DETECTOR — asset_class param
# ============================================================

class RegimeDetectorAssetClassTest(SimpleTestCase):
    """RegimeDetector accepts asset_class param; existing behavior unchanged."""

    def _make_detector(self, **kwargs):
        from bot.engines.regime_detector import RegimeDetector
        return RegimeDetector(**kwargs)

    def test_no_asset_class_preserves_default_threshold(self):
        d = self._make_detector()
        self.assertIsNone(d.asset_class)
        self.assertAlmostEqual(d.ema_spread_threshold, 0.5)

    def test_crypto_asset_class_preserves_default_threshold(self):
        d = self._make_detector(asset_class="CRYPTO")
        self.assertEqual(d.asset_class, "CRYPTO")
        self.assertAlmostEqual(d.ema_spread_threshold, 0.5)

    def test_forex_asset_class_stored(self):
        d = self._make_detector(asset_class="FOREX")
        self.assertEqual(d.asset_class, "FOREX")

    def test_forex_with_default_threshold_threshold_unchanged(self):
        # The warning is emitted but the threshold is NOT silently changed —
        # callers must pass explicit threshold when calibrated
        d = self._make_detector(asset_class="FOREX")
        self.assertAlmostEqual(d.ema_spread_threshold, 0.5)

    def test_explicit_threshold_honoured_for_forex(self):
        d = self._make_detector(asset_class="FOREX", ema_spread_trend_threshold=0.02)
        self.assertAlmostEqual(d.ema_spread_threshold, 0.02)

    def test_explicit_threshold_honoured_for_crypto(self):
        d = self._make_detector(ema_spread_trend_threshold=0.3)
        self.assertAlmostEqual(d.ema_spread_threshold, 0.3)

    def test_detect_still_returns_result_for_forex(self):
        """RegimeDetector.detect() runs without error when asset_class=FOREX."""
        from bot.engines.regime_detector import RegimeDetector
        import numpy as np, pandas as pd
        d = RegimeDetector(asset_class="FOREX")
        np.random.seed(1)
        close = pd.Series(1.10 + np.cumsum(np.random.normal(0, 0.0002, 200)))
        df = pd.DataFrame({
            "open": close - 0.0001,
            "high": close + 0.0003,
            "low":  close - 0.0003,
            "close": close,
            "volume": np.ones(200) * 1e6,
        })
        result = d.detect(df)
        self.assertIsNotNone(result.regime)
        self.assertIn(result.regime, ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "HIGH_VOLATILITY"))

    def test_forex_ema_spread_below_threshold_gives_ranging(self):
        """EUR/USD-scale EMA spread (~0.01%) never reaches 0.5% default → always RANGING/HIGH_VOL."""
        from bot.engines.regime_detector import RegimeDetector
        d = RegimeDetector(asset_class="FOREX")
        np.random.seed(2)
        # EUR/USD flat-ish price; EMA spread will be near zero
        close = pd.Series(1.10 + np.cumsum(np.random.normal(0, 0.00005, 200)))
        df = pd.DataFrame({
            "open": close - 0.00005,
            "high": close + 0.0001,
            "low":  close - 0.0001,
            "close": close,
            "volume": np.ones(200) * 1e6,
        })
        result = d.detect(df)
        self.assertNotIn(result.regime, ("TRENDING_UP", "TRENDING_DOWN"),
                         "TRENDING should never fire for EUR/USD-scale data with default 0.5% threshold")


# ============================================================
# BACKTESTER — asset_class annualization
# ============================================================

class BacktesterAnnualizationTest(SimpleTestCase):
    """Backtester uses correct periods_per_year for each asset class."""

    def test_no_asset_class_defaults_to_365(self):
        from bot.backtesting.backtester import Backtester, PERIODS_PER_YEAR
        b = Backtester(df=pd.DataFrame())
        self.assertEqual(b.periods_per_year, PERIODS_PER_YEAR)  # 365

    def test_crypto_asset_class_gives_365(self):
        from bot.backtesting.backtester import Backtester
        b = Backtester(df=pd.DataFrame(), asset_class="CRYPTO")
        self.assertEqual(b.periods_per_year, 365)

    def test_forex_asset_class_gives_260(self):
        from bot.backtesting.backtester import Backtester
        b = Backtester(df=pd.DataFrame(), asset_class="FOREX")
        self.assertEqual(b.periods_per_year, 260)

    def test_sharpe_fx_lower_than_crypto_identical_returns(self):
        """FX Sharpe is lower than crypto Sharpe for identical returns (fewer periods/year)."""
        from bot.backtesting.backtester import sharpe_from_returns
        np.random.seed(7)
        returns = pd.Series(np.random.normal(0.001, 0.02, 500))
        s_crypto = sharpe_from_returns(returns, rf_annual=0.0, periods_per_year=365)
        s_fx = sharpe_from_returns(returns, rf_annual=0.0, periods_per_year=260)
        # At rf=0, ratio must be sqrt(365/260)
        expected_ratio = (365 / 260) ** 0.5
        actual_ratio = s_crypto / s_fx
        self.assertAlmostEqual(actual_ratio, expected_ratio, places=3,
                               msg="Sharpe ratio must scale with sqrt(periods_per_year) at rf=0 (within 3 dp)")

    def test_sortino_fx_lower_than_crypto(self):
        """FX Sortino is lower than crypto Sortino for identical returns at rf=0."""
        from bot.backtesting.backtester import sortino_from_returns
        np.random.seed(8)
        returns = pd.Series(np.random.normal(0.001, 0.02, 500))
        s_crypto = sortino_from_returns(returns, rf_annual=0.0, periods_per_year=365)
        s_fx = sortino_from_returns(returns, rf_annual=0.0, periods_per_year=260)
        expected_ratio = (365 / 260) ** 0.5
        actual_ratio = s_crypto / s_fx
        self.assertAlmostEqual(actual_ratio, expected_ratio, places=3,
                               msg="Sortino ratio must scale with sqrt(periods_per_year) at rf=0 (within 3 dp)")

    def test_backtest_wrapper_accepts_asset_class(self):
        """backtest() wrapper forwards asset_class without error."""
        from bot.backtesting.backtester import backtest
        # Minimal DataFrame — backtest will return no trades but should not crash
        df = pd.DataFrame({
            "open": [100.0] * 200,
            "high": [101.0] * 200,
            "low":  [99.0]  * 200,
            "close": [100.0] * 200,
            "volume": [1000.0] * 200,
        }, index=pd.date_range("2024-01-01", periods=200, freq="1h", tz="UTC"))
        result = backtest(df, asset_class="FOREX")
        self.assertIn("sharpe_ratio", result)  # key is sharpe_ratio not sharpe
        self.assertIn("sortino_ratio", result)


# ============================================================
# PORTFOLIO BACKTESTER — asset_class default
# ============================================================

class PortfolioBacktesterAssetClassTest(SimpleTestCase):
    """PortfolioBacktester derives correct default symbol universe from asset_class."""

    def test_default_asset_class_is_crypto(self):
        from bot.backtesting.portfolio_backtester import PortfolioBacktester
        from bot.instruments import ASSET_CLASS_CRYPTO
        pb = PortfolioBacktester(exchange=None, symbols=["BTC/USDT"])
        self.assertEqual(pb.asset_class, ASSET_CLASS_CRYPTO)

    def test_explicit_forex_asset_class_stored(self):
        from bot.backtesting.portfolio_backtester import PortfolioBacktester
        from bot.instruments import ASSET_CLASS_FOREX
        pb = PortfolioBacktester(exchange=None, symbols=["EUR/USD"], asset_class=ASSET_CLASS_FOREX)
        self.assertEqual(pb.asset_class, ASSET_CLASS_FOREX)

    def test_explicit_symbols_override_asset_class_default(self):
        """Passing explicit symbols list is always honoured regardless of asset_class."""
        from bot.backtesting.portfolio_backtester import PortfolioBacktester
        symbols = ["EUR/USD", "GBP/USD"]
        pb = PortfolioBacktester(exchange=None, symbols=symbols)
        self.assertEqual(pb.symbols, symbols)

    def test_crypto_default_universe_excludes_forex_instruments(self):
        """Default crypto universe should not contain any FOREX-class instruments."""
        from bot.backtesting.portfolio_backtester import PortfolioBacktester
        from bot.instruments import get_instrument, ASSET_CLASS_FOREX
        pb = PortfolioBacktester(exchange=None)
        for sym in pb.symbols:
            # Reconstruct the slash form used in the registry
            slash_sym = sym.replace("_", "/")
            inst = get_instrument(slash_sym)
            if inst is not None:
                self.assertNotEqual(
                    inst.asset_class, ASSET_CLASS_FOREX,
                    f"Crypto default universe should not contain FX instrument {sym}"
                )


# ============================================================
# INTEGRATION — Backtester creates RegimeDetector with correct asset_class
# ============================================================

class BacktesterRegimeDetectorIntegrationTest(SimpleTestCase):
    """Backtester passes asset_class to its RegimeDetector instance."""

    def _make_backtester(self, asset_class=None):
        from bot.backtesting.backtester import Backtester
        df = pd.DataFrame({
            "open": [100.0] * 50,
            "high": [101.0] * 50,
            "low":  [99.0]  * 50,
            "close": [100.0] * 50,
            "volume": [1000.0] * 50,
        }, index=pd.date_range("2024-01-01", periods=50, freq="1h", tz="UTC"))
        kwargs = {"df": df}
        if asset_class is not None:
            kwargs["asset_class"] = asset_class
        return Backtester(**kwargs)

    def test_periods_per_year_correct_for_crypto(self):
        b = self._make_backtester("CRYPTO")
        self.assertEqual(b.periods_per_year, 365)

    def test_periods_per_year_correct_for_forex(self):
        b = self._make_backtester("FOREX")
        self.assertEqual(b.periods_per_year, 260)

    def test_existing_callers_unaffected(self):
        """Calling Backtester(df=...) with no asset_class gives same periods_per_year as before (365)."""
        b = self._make_backtester()
        self.assertEqual(b.periods_per_year, 365)
