# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/feature_calculator.py
#
# PHASE 1: OBSERVATION DATABASE FEATURE CALCULATOR
#
# Purpose:
#   Calculate ALL potential features (35+ pure math + indicators)
#   for EVERY candle in historical data
#
# Design Principles:
#   1. MODULAR: Easy to add new features without changing core
#   2. EFFICIENT: Fast enough to process 10 years of data
#   3. ROBUST: Error handling for missing/bad data
#   4. PROFESSIONAL: Full type hints, logging, documentation
#   5. TESTABLE: Each feature calculated independently

# Output:
#   observation_row = {
#       'timestamp': datetime,
#       'symbol': 'BTCUSDT',
#       'timeframe': '1h',
#       # PURE MATH FEATURES
#       'range_used': float,
#       'volatility_state': str,
#       'efficiency': float,
#       ... (30+ more features)
#       # INDICATOR FEATURES (to be tested)
#       'rsi': float,
#       'ema_12': float,
#       'ema_26': float,
#       'adx': float,
#       'atr': float,
#       ... (more indicators)
#       # LABEL (what happened next?)
#       'forward_return_4h': float,  # Return 4 hours later
#       'forward_return_24h': float, # Return 24 hours later
#       'win_4h': bool,              # Did price go up?
#   }
#
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import warnings

# claude code changed: new — Multi-Asset Foundation Refactor Phase 1B,
# Objective 3. The single source of "how many candles/year at this
# timeframe, under this asset class's trading calendar" — see
# bot/instruments.py's own module-level note for why this replaces the
# hardcoded sqrt(252) below rather than swapping in a second constant.
from bot.instruments import (
    UnsupportedAnnualizationError,
    UnsupportedTimeframeError,
    get_instrument,
    periods_per_year,
)

# ───────────────────────────────────────────────────────────────────────────────────────────────────────
# LOGGING CONFIGURATION
# ───────────────────────────────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# VOLATILITY STATE ENUM
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

class VolatilityState(Enum):
    """Classification of market volatility conditions"""
    COMPRESSED = "compressed"      # Vol < 80% of normal
    NORMAL = "normal"              # Vol 80-120% of normal
    EXPANDING = "expanding"        # Vol > 120% of normal


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

# Lookback periods for various calculations
ATR_PERIOD = 14                    # Average True Range period (standard)
RSI_PERIOD = 14                    # RSI period (standard)
EMA_FAST = 12                      # Fast EMA for MACD
EMA_SLOW = 26                      # Slow EMA for MACD
EMA_SIGNAL = 9                     # Signal line for MACD
ADX_PERIOD = 14                    # ADX period (standard)
BB_PERIOD = 20                     # Bollinger Bands period
BB_STD = 2                         # Bollinger Bands standard deviations

VOL_NORMAL_WINDOW = 20             # Calculate normal vol over 20 periods
RANGE_WINDOW = 20                  # Calculate average range over 20 periods


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# FEATURE CALCULATOR CLASS
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

class FeatureCalculator:
    """
    Calculates all features for a given OHLCV dataframe.
    
    Design:
    - Each feature calculation is independent
    - Can process entire dataframe at once (efficient)
    - Handles edge cases (insufficient data, NaN, etc.)
    - Returns complete observation with all features
    """
    
    def __init__(self, min_data_required: int = 100):
        """
        Initialize feature calculator.
        
        Args:
            min_data_required: Minimum candles needed before calculating features
        """
        self.min_data_required = min_data_required
        logger.info(f"FeatureCalculator initialized (min_data: {min_data_required})")
    
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # MAIN METHOD: Calculate all features for a dataframe
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    
    def calculate_all_features(self, df: pd.DataFrame, symbol: str = "UNKNOWN", 
                              timeframe: str = "1h") -> pd.DataFrame:
        """
        Calculate all features for every candle in dataframe.
        
        Args:
            df (pd.DataFrame): OHLCV data with columns: open, high, low, close, volume
            symbol (str): Coin symbol (e.g., 'BTCUSDT')
            timeframe (str): Candle timeframe (e.g., '1h', '4h')
        
        Returns:
            pd.DataFrame: Original DF + all feature columns
        """
        
        # Guard: validate input
        if df is None or len(df) < self.min_data_required:
            logger.warning(f"Insufficient data for {symbol}: {len(df) if df is not None else 0} candles")
            return df
        
        if not all(col in df.columns for col in ['open', 'high', 'low', 'close', 'volume']):
            logger.error("Missing required OHLCV columns")
            return df
        
        # claude code changed: new — Multi-Asset Foundation Refactor Phase
        # 1B, Objective 3. Resolve asset_class from `symbol` via the
        # instrument registry (bot/instruments.py) so realized_vol's
        # annualization factor is derived from real market structure
        # instead of a hardcoded sqrt(252). `symbol="UNKNOWN"` (the
        # default, and any symbol this platform doesn't recognize) leaves
        # asset_class=None — _calculate_realized_vol() falls back to the
        # legacy constant in that case, loudly logged, never silent.
        instrument = get_instrument(symbol)
        asset_class = instrument.asset_class if instrument else None

        try:
            # Make a copy to avoid modifying original
            result_df = df.copy()

            # ── PURE MATH FEATURES ──────────────────────────────────────────────────────────
            logger.info(f"Calculating pure math features for {symbol}...")

            result_df['atr'] = self._calculate_atr(result_df)
            result_df['adr'] = self._calculate_adr(result_df)
            result_df['range_used'] = self._calculate_range_used(result_df)
            result_df['volatility_state'] = self._calculate_volatility_state(result_df, timeframe=timeframe, asset_class=asset_class)
            result_df['efficiency'] = self._calculate_efficiency(result_df)
            result_df['atr_ratio'] = self._calculate_atr_ratio(result_df)
            result_df['realized_vol'] = self._calculate_realized_vol(result_df, timeframe=timeframe, asset_class=asset_class)
            result_df['volume_ratio'] = self._calculate_volume_ratio(result_df)
            
            # ── INDICATOR FEATURES (To be statistically tested) ─────────────────────────────
            logger.info(f"Calculating indicator features for {symbol}...")
            
            result_df['rsi'] = self._calculate_rsi(result_df, RSI_PERIOD)
            result_df['ema_12'] = self._calculate_ema(result_df['close'], EMA_FAST)
            result_df['ema_26'] = self._calculate_ema(result_df['close'], EMA_SLOW)
            result_df['adx'] = self._calculate_adx(result_df, ADX_PERIOD)
            result_df['atr_indicator'] = self._calculate_atr(result_df)
            
            # Bollinger Bands
            bb_upper, bb_middle, bb_lower = self._calculate_bollinger_bands(result_df, BB_PERIOD, BB_STD)
            result_df['bb_upper'] = bb_upper
            result_df['bb_middle'] = bb_middle
            result_df['bb_lower'] = bb_lower
            result_df['bb_width'] = bb_upper - bb_lower
            
            # MACD
            macd, signal, histogram = self._calculate_macd(result_df)
            result_df['macd'] = macd
            result_df['macd_signal'] = signal
            result_df['macd_histogram'] = histogram
            
            # ── LABELS (Forward returns - what happened next?) ─────────────────────────────
            logger.info(f"Calculating labels for {symbol}...")
            
            result_df['forward_return_4h'] = self._calculate_forward_return(result_df, periods=4)
            result_df['forward_return_24h'] = self._calculate_forward_return(result_df, periods=24)
            result_df['forward_return_1h'] = self._calculate_forward_return(result_df, periods=1)
            
            result_df['win_4h'] = result_df['forward_return_4h'] > 0
            result_df['win_24h'] = result_df['forward_return_24h'] > 0
            result_df['win_1h'] = result_df['forward_return_1h'] > 0
            
            logger.info(f"✓ All features calculated for {symbol} ({len(result_df)} candles)")
            return result_df
        
        except Exception as e:
            logger.error(f"Error calculating features: {e}", exc_info=True)
            return df
    
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # PURE MATH FEATURES
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
        """
        Calculate Average True Range (ATR).
        
        True Range = max(high - low, abs(high - prev_close), abs(low - prev_close))
        ATR = SMA of True Range
        """
        try:
            high = df['high']
            low = df['low']
            close = df['close']
            
            # Calculate True Range
            tr1 = high - low
            tr2 = abs(high - close.shift())
            tr3 = abs(low - close.shift())
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            
            # Calculate ATR (SMA of TR)
            atr = tr.rolling(window=period).mean()
            return atr
        except Exception as e:
            logger.debug(f"Error calculating ATR: {e}")
            return pd.Series(np.nan, index=df.index)
    
    def _calculate_adr(self, df: pd.DataFrame, period: int = RANGE_WINDOW) -> pd.Series:
        """
        Calculate Average Daily Range (ADR).
        
        ADR = SMA of (High - Low)
        """
        try:
            adr = (df['high'] - df['low']).rolling(window=period).mean()
            return adr
        except Exception as e:
            logger.debug(f"Error calculating ADR: {e}")
            return pd.Series(np.nan, index=df.index)
    
    def _calculate_range_used(self, df: pd.DataFrame) -> pd.Series:
        """
        Calculate percentage of daily range used.
        
        range_used = (current_high - current_low) / ADR
        """
        try:
            adr = self._calculate_adr(df)
            current_range = df['high'] - df['low']
            
            # Avoid division by zero
            range_used = current_range / adr.replace(0, np.nan)
            range_used = range_used.clip(0, 1)  # Cap at 100%
            
            return range_used
        except Exception as e:
            logger.debug(f"Error calculating range_used: {e}")
            return pd.Series(np.nan, index=df.index)
    
    def _calculate_volatility_state(self, df: pd.DataFrame, timeframe: str = "1h", asset_class: Optional[str] = None) -> pd.Series:
        """
        Classify volatility state: compressed/normal/expanding.

        Normal = 20-day average
        Compressed = < 80% of normal
        Expanding = > 120% of normal

        claude code changed: Multi-Asset Foundation Refactor Phase 1B —
        timeframe/asset_class are threaded through purely for consistency
        with realized_vol's own signature and to avoid a spurious
        "falling back to legacy annualization" warning on every call. The
        classification here is mathematically UNAFFECTED by the
        annualization factor either way: it's a ratio of realized_vol to
        its own rolling mean, and the constant sqrt(factor) term cancels
        out of that ratio exactly. Verified, not assumed — see
        test_feature_calculator.py's AnnualizationTest.
        """
        try:
            realized_vol = self._calculate_realized_vol(df, timeframe=timeframe, asset_class=asset_class)
            normal_vol = realized_vol.rolling(window=VOL_NORMAL_WINDOW).mean()
            
            ratio = realized_vol / normal_vol.replace(0, np.nan)
            
            # Classify
            state = pd.Series(index=df.index, dtype='object')
            state[ratio < 0.8] = VolatilityState.COMPRESSED.value
            state[(ratio >= 0.8) & (ratio <= 1.2)] = VolatilityState.NORMAL.value
            state[ratio > 1.2] = VolatilityState.EXPANDING.value
            
            return state
        except Exception as e:
            logger.debug(f"Error calculating volatility_state: {e}")
            return pd.Series(np.nan, index=df.index)
    
    def _calculate_efficiency(self, df: pd.DataFrame) -> pd.Series:
        """
        Calculate directional efficiency.
        
        efficiency = abs(close - open) / (high - low)
        Range 0-1: How much of range was directional vs choppy?
        """
        try:
            directional = abs(df['close'] - df['open'])
            range_size = df['high'] - df['low']
            
            efficiency = directional / range_size.replace(0, np.nan)
            efficiency = efficiency.clip(0, 1)
            
            return efficiency
        except Exception as e:
            logger.debug(f"Error calculating efficiency: {e}")
            return pd.Series(np.nan, index=df.index)
    
    def _calculate_atr_ratio(self, df: pd.DataFrame) -> pd.Series:
        """
        Calculate ATR ratio (wicks vs direction).
        
        atr_ratio = (high - low) / abs(close - open)
        High ratio = exhaustion (wicks rejected)
        Low ratio = conviction (directional)
        """
        try:
            range_size = df['high'] - df['low']
            directional = abs(df['close'] - df['open'])
            
            ratio = range_size / directional.replace(0, np.nan)
            ratio = ratio.clip(0, 10)  # Cap at 10
            
            return ratio
        except Exception as e:
            logger.debug(f"Error calculating atr_ratio: {e}")
            return pd.Series(np.nan, index=df.index)
    
    def _calculate_realized_vol(self, df: pd.DataFrame, period: int = 20, timeframe: str = "1h", asset_class: Optional[str] = None) -> pd.Series:
        """
        Calculate realized volatility (actual observed), annualized.

        Std dev of returns x sqrt(periods/year)

        claude code changed: Multi-Asset Foundation Refactor Phase 1B,
        Objective 3. Was `np.sqrt(252)` unconditionally — the US-equity
        daily-bar annualization convention, applied regardless of
        timeframe or asset class. Wrong even for this platform's own only
        real dataset (crypto, 1h candles): 252 assumes ~252 DAILY
        observations/year, but 1h crypto candles produce ~8,760
        observations/year on a 24/7/365 market — the old value
        under-annualized realized_vol by a factor of ~5.9x
        (sqrt(8760/252) ~= 5.9) for every crypto/1h experiment that has
        ever computed this feature. Now derived from
        bot.instruments.periods_per_year(timeframe, asset_class) — the
        correct factor for whatever data this actually is, not a constant
        borrowed from a different market's convention.

        Falls back to the legacy 252 constant — loudly logged, never
        silent — only when asset_class can't be resolved at all (no real
        symbol was passed) or when the (timeframe, asset_class) pair has
        no known trading-calendar convention yet (e.g. intraday
        US_EQUITY/FOREX — see UnsupportedAnnualizationError). This keeps
        every existing caller that doesn't pass a real symbol (unit tests
        using symbol="TEST", the "UNKNOWN" default) working exactly as
        before; every REAL Research Lab call, which always names a real
        instrument, now gets the correct factor.
        """
        try:
            returns = df['close'].pct_change()

            if asset_class is None:
                logger.warning(
                    "_calculate_realized_vol: no asset_class resolved (symbol not in the instrument "
                    "registry) — falling back to the legacy 252 (US-equity daily-bar) annualization constant"
                )
                annualization_factor = 252.0
            else:
                try:
                    annualization_factor = periods_per_year(timeframe, asset_class)
                except (UnsupportedTimeframeError, UnsupportedAnnualizationError) as exc:
                    logger.warning(
                        f"_calculate_realized_vol: cannot derive a correct annualization factor for "
                        f"timeframe={timeframe!r}, asset_class={asset_class!r} ({exc}) — falling back to "
                        f"the legacy 252 (US-equity daily-bar) annualization constant"
                    )
                    annualization_factor = 252.0

            realized_vol = returns.rolling(window=period).std() * np.sqrt(annualization_factor)

            return realized_vol
        except Exception as e:
            logger.debug(f"Error calculating realized_vol: {e}")
            return pd.Series(np.nan, index=df.index)
    
    def _calculate_volume_ratio(self, df: pd.DataFrame, period: int = 20) -> pd.Series:
        """
        Calculate volume ratio (today vs normal).
        
        volume_ratio = current_volume / avg_volume
        """
        try:
            avg_volume = df['volume'].rolling(window=period).mean()
            ratio = df['volume'] / avg_volume.replace(0, np.nan)
            
            return ratio
        except Exception as e:
            logger.debug(f"Error calculating volume_ratio: {e}")
            return pd.Series(np.nan, index=df.index)
    
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # INDICATOR FEATURES (To be statistically validated)
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    
    def _calculate_rsi(self, df: pd.DataFrame, period: int = RSI_PERIOD) -> pd.Series:
        """
        Calculate Relative Strength Index (RSI).
        
        RSI = 100 - (100 / (1 + RS))
        RS = avg_gain / avg_loss
        """
        try:
            delta = df['close'].diff()
            
            # Separate gains and losses
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            
            # Exponential moving average
            avg_gain = gain.ewm(span=period, adjust=False).mean()
            avg_loss = loss.ewm(span=period, adjust=False).mean()
            
            # Calculate RS and RSI
            rs = avg_gain / avg_loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))
            
            return rsi.clip(0, 100)
        except Exception as e:
            logger.debug(f"Error calculating RSI: {e}")
            return pd.Series(np.nan, index=df.index)
    
    def _calculate_ema(self, series: pd.Series, period: int) -> pd.Series:
        """
        Calculate Exponential Moving Average (EMA).
        
        EMA = price * multiplier + EMA_prev * (1 - multiplier)
        multiplier = 2 / (period + 1)
        """
        try:
            return series.ewm(span=period, adjust=False).mean()
        except Exception as e:
            logger.debug(f"Error calculating EMA: {e}")
            return pd.Series(np.nan, index=series.index)
    
    def _calculate_adx(self, df: pd.DataFrame, period: int = ADX_PERIOD) -> pd.Series:
        """
        Calculate Average Directional Index (ADX).
        
        ADX = measure of trend strength (0-100)
        High ADX = strong trend
        Low ADX = range/weak trend
        """
        try:
            high = df['high']
            low = df['low']
            close = df['close']
            
            # Calculate directional movements
            up = high.diff()
            down = -low.diff()
            
            plus_dm = pd.Series(0, index=df.index)
            minus_dm = pd.Series(0, index=df.index)
            
            plus_dm[up > down] = up[up > down]
            minus_dm[down > up] = down[down > up]
            
            # True Range
            tr = pd.concat([
                high - low,
                abs(high - close.shift()),
                abs(low - close.shift())
            ], axis=1).max(axis=1)
            
            # Smooth with RSI-like method
            atr = tr.rolling(window=period).mean()
            plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr.replace(0, np.nan))
            minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr.replace(0, np.nan))
            
            # ADX
            di_diff = abs(plus_di - minus_di)
            di_sum = plus_di + minus_di
            dx = 100 * (di_diff / di_sum.replace(0, np.nan))
            adx = dx.rolling(window=period).mean()
            
            return adx.clip(0, 100)
        except Exception as e:
            logger.debug(f"Error calculating ADX: {e}")
            return pd.Series(np.nan, index=df.index)
    
    def _calculate_bollinger_bands(self, df: pd.DataFrame, period: int = BB_PERIOD, 
                                   std_dev: float = BB_STD) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Calculate Bollinger Bands.
        
        Middle = SMA
        Upper = SMA + (std * std_dev)
        Lower = SMA - (std * std_dev)
        """
        try:
            close = df['close']
            middle = close.rolling(window=period).mean()
            std = close.rolling(window=period).std()
            
            upper = middle + (std * std_dev)
            lower = middle - (std * std_dev)
            
            return upper, middle, lower
        except Exception as e:
            logger.debug(f"Error calculating Bollinger Bands: {e}")
            return (pd.Series(np.nan, index=df.index), 
                   pd.Series(np.nan, index=df.index),
                   pd.Series(np.nan, index=df.index))
    
    def _calculate_macd(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Calculate MACD (Moving Average Convergence Divergence).
        
        MACD = EMA12 - EMA26
        Signal = EMA9 of MACD
        Histogram = MACD - Signal
        """
        try:
            close = df['close']
            ema12 = self._calculate_ema(close, EMA_FAST)
            ema26 = self._calculate_ema(close, EMA_SLOW)
            
            macd = ema12 - ema26
            signal = self._calculate_ema(macd, EMA_SIGNAL)
            histogram = macd - signal
            
            return macd, signal, histogram
        except Exception as e:
            logger.debug(f"Error calculating MACD: {e}")
            return (pd.Series(np.nan, index=df.index),
                   pd.Series(np.nan, index=df.index),
                   pd.Series(np.nan, index=df.index))
    
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # LABELS (Forward returns - what actually happened?)
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    
    def _calculate_forward_return(self, df: pd.DataFrame, periods: int = 4) -> pd.Series:
        """
        Calculate forward return (what happened next?).
        
        This is the LABEL we're trying to predict.
        
        forward_return = (price_N_periods_later / current_price) - 1
        """
        try:
            close = df['close']
            future_close = close.shift(-periods)
            
            forward_return = (future_close / close) - 1
            
            return forward_return
        except Exception as e:
            logger.debug(f"Error calculating forward return: {e}")
            return pd.Series(np.nan, index=df.index)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# EXAMPLE USAGE
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """
    Example: Calculate features for Bitcoin
    """
    
    # Create sample data (replace with real Binance data)
    dates = pd.date_range('2023-01-01', periods=1000, freq='1h')
    np.random.seed(42)
    
    sample_df = pd.DataFrame({
        'open': 40000 + np.random.randn(1000).cumsum() * 50,
        'high': 40100 + np.random.randn(1000).cumsum() * 50,
        'low': 39900 + np.random.randn(1000).cumsum() * 50,
        'close': 40000 + np.random.randn(1000).cumsum() * 50,
        'volume': np.random.uniform(1000, 10000, 1000),
    }, index=dates)
    
    # Initialize calculator
    calculator = FeatureCalculator(min_data_required=100)
    
    # Calculate all features
    result_df = calculator.calculate_all_features(sample_df, symbol='BTCUSDT', timeframe='1h')
    
    # Show results
    print(f"✓ Calculated {len(result_df.columns)} columns")
    print(f"✓ {result_df.notna().sum().sum()} non-null values")
    print(f"\nFeature columns:\n{result_df.columns.tolist()}")
    print(f"\nFirst observation:\n{result_df.iloc[100]}")
