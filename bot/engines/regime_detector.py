# ============================================================
# bot/engines/regime_detector.py
# Market Regime Detector — institutional grade classification
# PURPOSE:
# Classifies the current market into one of four regimes:
#   TRENDING_UP    — strong upward trend, EMAs expanding bullish
#   TRENDING_DOWN  — strong downward trend, EMAs expanding bearish
#   RANGING        — price oscillating, no clear direction
#   HIGH_VOLATILITY — abnormal price swings, too risky to trade
#
# WHY THIS MATTERS:
# Every strategy has a "home regime" where it performs best.
# Running a trend-following strategy in a ranging market is like
# using a hammer to cut wood — wrong tool, wrong conditions.
# This file acts as the gatekeeper before any strategy fires.
#
# METHODS USED (three must agree for high confidence):
#   1. ADX  — Average Directional Index (trend strength 0-100)
#   2. ATR Ratio — current volatility vs historical average
#   3. EMA Spread — distance between EMA20 and EMA50 as % of price
#   4. Bollinger Band Width — measures price compression/expansion
#
# INSTITUTIONAL STANDARD:
# Professional quant systems use ADX as the primary regime filter.
# ADX above 25 = trending. ADX below 20 = ranging.
# This is not opinion — it is the industry standard.
# ============================================================

import pandas as pd        # DataFrame operations for OHLCV data
import numpy as np         # Numerical calculations
import logging             # Structured logging
from dataclasses import dataclass   # Clean result object structure

# Create logger for this module
logger = logging.getLogger(__name__)


# ============================================================
# REGIME RESULT — DATA CLASS
# A clean structured object returned by RegimeDetector.detect()
# Using dataclass instead of a plain dict prevents KeyErrors
# and makes the result self-documenting
# ============================================================
@dataclass
class RegimeResult:
    """
    Holds the full classification result for the current market.

    Every field is populated on every detection — nothing is None.
    This guarantees that strategy_router.py can always read all fields
    without defensive checks.
    """

    # Primary regime classification — the most important field
    # One of: "TRENDING_UP", "TRENDING_DOWN", "RANGING", "HIGH_VOLATILITY"
    regime: str

    # Confidence level in the classification
    # "HIGH"   — 3 or more indicators agree
    # "MEDIUM" — 2 indicators agree
    # "LOW"    — only 1 indicator supports the classification
    confidence: str

    # ADX value — measures trend strength regardless of direction
    # 0-20  = weak or no trend (ranging)
    # 20-25 = developing trend
    # 25-40 = strong trend
    # 40+   = very strong trend (sometimes unsustainable)
    adx: float

    # ATR ratio — current ATR divided by 50-period average ATR
    # 1.0 = normal volatility
    # 2.0+ = volatility is double the normal level (dangerous)
    # 0.5  = very compressed market (often precedes a breakout)
    atr_ratio: float

    # EMA spread as a percentage of current price
    # Measures how far apart EMA20 and EMA50 are
    # High spread = trend is strong and established
    # Near zero   = EMAs converging = trend weakening or ranging
    ema_spread_pct: float

    # Bollinger Band width as a percentage of price
    # Narrow = market is compressed = ranging or pre-breakout
    # Wide   = market is expanding  = trending or high volatility
    bb_width_pct: float

    # Whether ADX confirms a trend (True if ADX > threshold)
    adx_trending: bool

    # Whether volatility is at abnormal levels (True = danger zone)
    volatility_extreme: bool

    # Human readable summary — used in trade narrative
    # Example: "TRENDING_UP | HIGH confidence | ADX 32.4 | ATR ratio 1.1"
    summary: str


# ============================================================
# REGIME DETECTOR — MAIN CLASS
# ============================================================
class RegimeDetector:
    """
    Classifies market regime using four institutional indicators.

    Designed to be instantiated once and called every candle.
    All thresholds are configurable via __init__ parameters.

    Usage:
        detector = RegimeDetector()
        result = detector.detect(df)
        print(result.regime)       # "TRENDING_UP"
        print(result.confidence)   # "HIGH"
    """

    def __init__(
        self,
        adx_period=14,                  # ADX lookback period (14 = standard)
        adx_trend_threshold=25,         # ADX above this = trending market
        adx_strong_threshold=40,        # ADX above this = very strong trend
        atr_period=14,                  # ATR lookback period
        atr_avg_period=50,              # Period for average ATR comparison
        atr_extreme_multiplier=1.7,     # ATR ratio above this = high volatility
        ema_fast=20,                    # Fast EMA period
        ema_slow=50,                    # Slow EMA period
        ema_spread_trend_threshold=0.5, # EMA spread % above this = trending
        bb_period=20,                   # Bollinger Band period
        bb_std=2.0,                     # Bollinger Band standard deviations
        bb_narrow_threshold=2.0,        # BB width % below this = ranging/compressed
        min_candles=60,                 # Minimum candles before detection runs
    ):

        # Store ADX settings
        self.adx_period            = adx_period              # Lookback for ADX calc
        self.adx_trend_threshold   = adx_trend_threshold     # 25 = institutional standard
        self.adx_strong_threshold  = adx_strong_threshold    # 40 = very strong trend

        # Store ATR settings
        self.atr_period            = atr_period              # ATR smoothing period
        self.atr_avg_period        = atr_avg_period          # Baseline ATR comparison period
        self.atr_extreme_multiplier = atr_extreme_multiplier # 2x = danger zone

        # Store EMA settings
        self.ema_fast              = ema_fast                 # Fast EMA (20)
        self.ema_slow              = ema_slow                 # Slow EMA (50)
        self.ema_spread_threshold  = ema_spread_trend_threshold  # 0.5% spread = trending

        # Store Bollinger Band settings
        self.bb_period             = bb_period               # BB calculation period
        self.bb_std                = bb_std                  # Standard deviations
        self.bb_narrow_threshold   = bb_narrow_threshold     # Narrow = ranging signal

        # Minimum data required before any detection can run
        self.min_candles = min_candles

        # Log initialisation with key thresholds
        logger.info(
            f"[RegimeDetector] Initialised | "
            f"ADX threshold: {adx_trend_threshold} | "
            f"ATR extreme: {atr_extreme_multiplier}x | "
            f"EMA spread: {ema_spread_trend_threshold}%"
        )


    # ============================================================
    # DETECT — MAIN PUBLIC METHOD
    # Called every candle by strategy_router.py
    # Returns a RegimeResult dataclass with full classification
    # ============================================================
    def detect(self, df: pd.DataFrame) -> RegimeResult:
        """
        Analyses the current DataFrame and classifies the market regime.

        df: pandas DataFrame with columns [open, high, low, close, volume]
            Must have at least min_candles rows for valid detection

        Returns RegimeResult with regime, confidence, and all indicator values.
        Never raises an exception — always returns a valid RegimeResult.
        """

        # Guard: not enough data to calculate indicators reliably
        if df is None or len(df) < self.min_candles:
            logger.warning(
                f"[RegimeDetector] Insufficient data: {len(df) if df is not None else 0} candles. "
                f"Minimum required: {self.min_candles}. Returning RANGING."
            )
            # Default to RANGING — safest fallback (no directional trades)
            return self._default_result("not_enough_data")

        # Work on a copy — never mutate the caller's DataFrame
        df = df.copy()

        # Force all price columns to numeric — removes string artifacts
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Drop rows with any NaN price data — cannot calculate on bad candles
        df.dropna(subset=["high", "low", "close"], inplace=True)

        # Recheck after dropping NaN rows
        if len(df) < self.min_candles:
            return self._default_result("data_after_cleaning")

        try:
            # ── CALCULATE ALL FOUR INDICATORS ────────────────────

            # Calculate ADX — primary trend strength indicator
            adx_value = self._calculate_adx(df)

            # Calculate ATR ratio — volatility level vs historical norm
            atr_ratio = self._calculate_atr_ratio(df)

            # Calculate EMA spread — distance between fast and slow EMA
            ema_spread_pct = self._calculate_ema_spread(df)

            # Calculate Bollinger Band width — price compression/expansion
            bb_width_pct = self._calculate_bb_width(df)

            # Get current EMA direction for trend up/down determination
            ema_direction = self._get_ema_direction(df)

            # ── CLASSIFY SIGNALS FROM EACH INDICATOR ─────────────

            # ADX says trending if above the threshold (25 standard)
            adx_trending = adx_value >= self.adx_trend_threshold

            # EMA spread says trending if spread is above threshold
            ema_trending = ema_spread_pct >= self.ema_spread_threshold

            # Bollinger Bands say ranging if width is narrow
            bb_ranging = bb_width_pct <= self.bb_narrow_threshold

            # Volatility is extreme if ATR is more than 2x the average
            volatility_extreme = atr_ratio >= self.atr_extreme_multiplier

            # ── REGIME CLASSIFICATION LOGIC ──────────────────────
            # Priority order: HIGH_VOLATILITY > TRENDING > RANGING
            # High volatility is checked FIRST — it overrides everything
            # A trending market that suddenly spikes volatility is dangerous

            # COUNT how many indicators agree on trending
            # This gives us a confidence score
            trending_signals = sum([
                adx_trending,    # ADX says trend exists
                ema_trending,    # EMA spread confirms trend
                not bb_ranging,  # BB width is not narrow (expanding)
            ])

            # COUNT how many indicators agree on ranging
            ranging_signals = sum([
                not adx_trending,   # ADX says no trend
                not ema_trending,   # EMA spread is tight
                bb_ranging,         # BB width is narrow
            ])

            # ── DETERMINE FINAL REGIME ───────────────────────────

            if volatility_extreme:
                # Volatility spike overrides everything
                # Do not trade — conditions are unpredictable
                regime     = "HIGH_VOLATILITY"
                confidence = "HIGH"   # ATR ratio is a hard number — high confidence

            elif trending_signals >= 2:
                # Two or more indicators agree on a trend
                # Determine direction from EMA relationship
                if ema_direction == "UP":
                    regime = "TRENDING_UP"     # Bullish trend — longs only
                elif ema_direction == "DOWN":
                    regime = "TRENDING_DOWN"   # Bearish trend — shorts only
                else:
                    # EMAs crossing or equal — transitional state
                    regime = "RANGING"

                # Confidence based on how many signals agreed
                confidence = "HIGH" if trending_signals == 3 else "MEDIUM"

            elif ranging_signals >= 2:
                # Two or more indicators agree on ranging
                regime     = "RANGING"
                confidence = "HIGH" if ranging_signals == 3 else "MEDIUM"

            else:
                # Mixed signals — cannot classify with confidence
                # Default to RANGING (safe]r than forcing a trend trade)
                regime     = "RANGING"
                confidence = "LOW"

            # ── BUILD SUMMARY STRING ─────────────────────────────
            # Human readable — stored in trade narrative
            summary = (
                f"{regime} | {confidence} confidence | "
                f"ADX: {adx_value:.1f} | "
                f"ATR ratio: {atr_ratio:.2f} | "
                f"EMA spread: {ema_spread_pct:.2f}% | "
                f"BB width: {bb_width_pct:.2f}%"
            )

            # Log the detection result
            logger.info(f"[RegimeDetector] {summary}")

            # Return the full RegimeResult dataclass
            return RegimeResult(
                regime             = regime,           # Primary classification
                confidence         = confidence,       # How certain we are
                adx                = round(adx_value, 2),
                atr_ratio          = round(atr_ratio, 2),
                ema_spread_pct     = round(ema_spread_pct, 4),
                bb_width_pct       = round(bb_width_pct, 4),
                adx_trending       = adx_trending,
                volatility_extreme = volatility_extreme,
                summary            = summary,
            )

        except Exception as e:
            # Never let a detection error crash the bot
            # Log the full error and return safe default
            logger.error(
                f"[RegimeDetector] Detection failed: {type(e).__name__}: {e}. "
                f"Returning safe default."
            )
            return self._default_result("detection_error")


    # ============================================================
    # ADX CALCULATION
    # Average Directional Index — measures trend STRENGTH
    # NOT direction — ADX is always 0 to 100
    # High ADX = strong trend (up or down)
    # Low ADX  = weak trend or ranging market
    # ============================================================
    def _calculate_adx(self, df: pd.DataFrame) -> float:
        """
        Calculates ADX using the Wilder smoothing method.
        Returns float ADX value (0-100). Returns 0.0 on failure.
        """

        try:
            # Extract price series
            high  = df["high"]    # Candle high prices
            low   = df["low"]     # Candle low prices
            close = df["close"]   # Candle close prices

            # ── STEP 1: TRUE RANGE ────────────────────────────
            # True Range is the largest of three measurements:
            # 1. Current high minus current low
            # 2. Absolute distance from previous close to current high
            # 3. Absolute distance from previous close to current low
            # This accounts for overnight gaps in price
            prev_close = close.shift(1)   # Previous candle's close

            tr = pd.concat([
                high - low,                        # Current candle range
                (high - prev_close).abs(),         # Gap up measurement
                (low  - prev_close).abs(),         # Gap down measurement
            ], axis=1).max(axis=1)                 # Take the largest of the three

            # ── STEP 2: DIRECTIONAL MOVEMENT ─────────────────
            # +DM: how much price moved UP compared to previous candle
            # -DM: how much price moved DOWN compared to previous candle

            up_move   = high - high.shift(1)     # How much higher than last high
            down_move = low.shift(1) - low       # How much lower than last low

            # +DM: upward move only counts if it exceeded the downward move
            plus_dm  = pd.Series(
                np.where((up_move > down_move) & (up_move > 0), up_move, 0),
                index=df.index
            )

            # -DM: downward move only counts if it exceeded the upward move
            minus_dm = pd.Series(
                np.where((down_move > up_move) & (down_move > 0), down_move, 0),
                index=df.index
            )

            # ── STEP 3: WILDER SMOOTHING ──────────────────────
            # Wilder uses a specific EMA formula: 1/period weight
            # This is different from standard EMA
            period = self.adx_period   # Typically 14

            # Smooth TR, +DM, and -DM with Wilder's method
            atr_smooth    = tr.ewm(alpha=1/period, adjust=False).mean()
            plus_dm_smooth  = plus_dm.ewm(alpha=1/period, adjust=False).mean()
            minus_dm_smooth = minus_dm.ewm(alpha=1/period, adjust=False).mean()

            # ── STEP 4: DIRECTIONAL INDICATORS ───────────────
            # +DI and -DI show direction — ADX combines them for strength

            # +DI: percentage of ATR that was upward movement
            plus_di  = 100 * (plus_dm_smooth  / (atr_smooth + 1e-9))

            # -DI: percentage of ATR that was downward movement
            minus_di = 100 * (minus_dm_smooth / (atr_smooth + 1e-9))

            # ── STEP 5: ADX FROM DX ───────────────────────────
            # DX = difference between +DI and -DI as fraction of their sum
            # ADX = smoothed DX (removes noise from DX oscillations)

            dx = 100 * (
                (plus_di - minus_di).abs() /
                (plus_di + minus_di + 1e-9)
            )

            # Smooth DX to get ADX
            adx = dx.ewm(alpha=1/period, adjust=False).mean()

            # Return the latest ADX value
            latest_adx = float(adx.iloc[-1])

            # Guard: return 0 if calculation produced NaN
            return latest_adx if not np.isnan(latest_adx) else 0.0

        except Exception as e:
            logger.warning(f"[RegimeDetector] ADX calculation failed: {e}")
            return 0.0   # Return 0 — treated as no trend detected


    # ============================================================
    # ATR RATIO CALCULATION
    # Compares current volatility to historical average
    # Ratio > 2.0 = abnormal volatility = HIGH_VOLATILITY regime
    # ============================================================
    def _calculate_atr_ratio(self, df: pd.DataFrame) -> float:
        """
        Returns current ATR divided by the long-period average ATR.
        Ratio of 1.0 = normal. Above 2.0 = dangerous volatility.
        """

        try:
            # Calculate True Range (same method as ADX)
            high      = df["high"] 
            low       = df["low"]
            prev_close = df["close"].shift(1)

            tr = pd.concat([
                high - low,
                (high - prev_close).abs(),
                (low  - prev_close).abs(),
            ], axis=1).max(axis=1)

            # Short-period ATR — current volatility (14 periods)
            current_atr = tr.rolling(self.atr_period).mean().iloc[-1]

            # Long-period ATR — baseline "normal" volatility (50 periods)
            avg_atr = tr.rolling(self.atr_avg_period).mean().iloc[-1]

            # Guard against division by zero
            if avg_atr == 0 or np.isnan(avg_atr):
                return 1.0   # Return neutral ratio if no baseline available

            # Ratio > 1.0 means volatility is above average
            ratio = current_atr / avg_atr

            return float(ratio) if not np.isnan(ratio) else 1.0

        except Exception as e:
            logger.warning(f"[RegimeDetector] ATR ratio calculation failed: {e}")
            return 1.0   # Return neutral — assume normal volatility


    # ============================================================
    # EMA SPREAD CALCULATION
    # Measures how far apart EMA20 and EMA50 are
    # Wide spread = strong established trend
    # Tight spread = EMAs converging = ranging or trend ending
    # ============================================================
    def _calculate_ema_spread(self, df: pd.DataFrame) -> float:
        """
        Returns the spread between EMA20 and EMA50 as % of current price.
        High % = strong trend. Near 0% = ranging or transitioning.
        """

        try:
            close = df["close"]

            # Calculate EMA20 and EMA50
            ema_fast = close.ewm(span=self.ema_fast, adjust=False).mean()
            ema_slow = close.ewm(span=self.ema_slow, adjust=False).mean()

            # Get latest values
            latest_fast  = float(ema_fast.iloc[-1])   # EMA20 value
            latest_slow  = float(ema_slow.iloc[-1])   # EMA50 value
            latest_price = float(close.iloc[-1])       # Current price

            # Guard against zero price
            if latest_price == 0:
                return 0.0

            # Spread as percentage of price
            # abs() because we care about distance, not direction
            # Direction is captured separately by _get_ema_direction()
            spread_pct = abs(latest_fast - latest_slow) / latest_price * 100

            return round(float(spread_pct), 4)

        except Exception as e:
            logger.warning(f"[RegimeDetector] EMA spread calculation failed: {e}")
            return 0.0   # Return 0 — treated as no spread (ranging signal)


    # ============================================================
    # BOLLINGER BAND WIDTH CALCULATION
    # Narrow bands = price compression = ranging market
    # Wide bands   = price expansion   = trending or volatile
    # ============================================================
    def _calculate_bb_width(self, df: pd.DataFrame) -> float:
        """
        Returns Bollinger Band width as % of the middle band (SMA20).
        Below 2% = narrow = ranging. Above 4% = wide = trending/volatile.
        """

        try:
            close = df["close"]

            # Middle band = simple moving average
            middle = close.rolling(self.bb_period).mean()

            # Standard deviation of close prices over the BB period
            std = close.rolling(self.bb_period).std()

            # Upper and lower bands
            upper = middle + (self.bb_std * std)   # Middle + 2 std devs
            lower = middle - (self.bb_std * std)   # Middle - 2 std devs

            # Get latest values
            latest_upper  = float(upper.iloc[-1])   # Latest upper band
            latest_lower  = float(lower.iloc[-1])   # Latest lower band
            latest_middle = float(middle.iloc[-1])  # Latest middle band

            # Guard against zero middle band
            if latest_middle == 0 or np.isnan(latest_middle):
                return 0.0

            # Band width as percentage of middle band
            # This normalises for different price levels (BTC vs NEAR)
            width_pct = (latest_upper - latest_lower) / latest_middle * 100

            return round(float(width_pct), 4) if not np.isnan(width_pct) else 0.0

        except Exception as e:
            logger.warning(f"[RegimeDetector] BB width calculation failed: {e}")
            return 0.0   # Return 0 — treated as narrow (ranging signal)


    # ============================================================
    # EMA DIRECTION
    # Determines whether the trend is UP or DOWN
    # Used to set TRENDING_UP vs TRENDING_DOWN
    # ============================================================
    def _get_ema_direction(self, df: pd.DataFrame) -> str:
        """
        Returns "UP", "DOWN", or "NEUTRAL" based on EMA20 vs EMA50.
        Used only when trend is confirmed — gives the direction.
        """

        try:
            close = df["close"]

            # Calculate both EMAs
            ema_fast = close.ewm(span=self.ema_fast, adjust=False).mean()
            ema_slow = close.ewm(span=self.ema_slow, adjust=False).mean()

            # Compare latest values
            latest_fast = float(ema_fast.iloc[-1])   # Current EMA20
            latest_slow = float(ema_slow.iloc[-1])   # Current EMA50

            if latest_fast > latest_slow:
                return "UP"     # EMA20 above EMA50 = bullish

            elif latest_fast < latest_slow:
                return "DOWN"   # EMA20 below EMA50 = bearish

            else:
                return "NEUTRAL"   # EMAs equal = crossing point

        except Exception as e:
            logger.warning(f"[RegimeDetector] EMA direction failed: {e}")
            return "NEUTRAL"   # Default to neutral on error


    # ============================================================
    # DEFAULT RESULT
    # Returns a safe RegimeResult when detection cannot run
    # Defaults to RANGING — the safest fallback
    # RANGING prevents directional trades without blocking all trading
    # ===========n =================================================
    def _default_result(self, reason: str) -> RegimeResult:
        """
        Returns a safe default RegimeResult when detection fails.
        RANGING is the default because it's the safest non-trade state.
        """

        # Log why we're returning the default
        logger.warning(
            f"[RegimeDetector] Returning default RANGING result. Reason: {reason}"
        )

        return RegimeResult(
            regime             = "RANGING",     # Safest default — no trend trades
            confidence         = "LOW",          # Low confidence — data was insufficient
            adx                = 0.0,            # No ADX available
            atr_ratio          = 1.0,            # Assume normal volatility
            ema_spread_pct     = 0.0,            # No spread data
            bb_width_pct       = 0.0,            # No BB data
            adx_trending       = False,          # ADX does not confirm trend
            volatility_extreme = False,          # Assume volatility is normal
            summary            = f"RANGING | LOW confidence | reason: {reason}",
        )