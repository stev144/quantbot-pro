# ============================================================
# bot/strategies/mean_reversion_strategy.py
# Mean Reversion Strategy — FIXED AND UPGRADED
#
# FIXES FROM PREVIOUS VERSION:
# 1. "ranging" → "RANGING" — case mismatch was blocking ALL trades
#    This was the reason mean reversion never fired once
#
# UPGRADES:
# 2. rsi_oversold:   37 → 30  (true oversold — not middle of range)
# 3. rsi_overbought: 63 → 70  (true overbought — not middle of range)
# 4. atr_multiplier: 1.2 → 1.5 (wider stop — survives BTC noise)
# 5. BB width filter added — only trade genuinely compressed ranges
#    Wide ranging markets are choppy — mean reversion fails in them
# 6. Confirmation candle added — price must show reversal intent
#    Prevents entering the moment RSI hits extreme (can go further)
# 7. min_candles raised from 30 → 50 for better indicator warmup
# 8. strategy name added to signal dict — matches rest of system
#
# WHY THESE VALUES:
# RSI 30/70 are the classic oversold/overbought levels used
# by every professional mean reversion system. 37/63 is just
# the middle of the RSI range — not extreme enough to signal
# genuine price exhaustion.
# BTC requires wider stops — 1.2x ATR gets stopped out by
# normal BTC candle wicks constantly.
# ============================================================

import pandas as pd    # DataFrame operations
import numpy as np     # Numerical calculations
import logging         # Structured logging

# Create logger for this module
logger = logging.getLogger(__name__)


class MeanReversionStrategy:
    """
    RSI mean reversion strategy for RANGING markets only.

    Logic:
    - Wait for price to reach a genuine RSI extreme (oversold/overbought)
    - Confirm the reversal with a momentum check (confirmation candle)
    - Only trade when Bollinger Bands confirm the range is tight
    - Use ATR-based SL/TP that adapts to current volatility

    This strategy is routed to by StrategyRouter ONLY when
    RegimeDetector classifies the market as RANGING.
    It should never fire in trending markets.
    """

    def __init__(
        self,
        rsi_oversold=35,        # UPGRADED from 37 — true oversold level
        rsi_overbought=65,      # UPGRADED from 63 — true overbought level
        atr_multiplier=1.0,     # UPGRADED from 1.2 — wider stop for BTC noise
        bb_width_max=6.0,       # NEW — max BB width % to confirm tight range
        min_candles=50,         # UPGRADED from 30 — better indicator warmup
        rr_ratio=1.5,           # Risk/Reward ratio for TP calculation
    ):
        # RSI extreme levels — price must reach these to signal exhaustion
        self.rsi_oversold   = rsi_oversold     # Below this = oversold = BUY zone
        self.rsi_overbought = rsi_overbought   # Above this = overbought = SELL zone

        # ATR multiplier for SL distance — wider = survives more noise
        self.atr_multiplier = atr_multiplier

        # Maximum Bollinger Band width allowed for entry
        # Above this = range is too wide = choppy = skip trade
        self.bb_width_max = bb_width_max

        # Minimum candles required before strategy can fire
        self.min_candles = min_candles

        # Risk/Reward — TP is this multiple of the SL distance
        self.rr_ratio = rr_ratio

        # Strategy name — used in signal dict and trade narrative
        self.name = "MeanReversionStrategy"

        logger.info(
            f"[MeanReversionStrategy] Initialised | "
            f"RSI oversold: {rsi_oversold} | "
            f"RSI overbought: {rsi_overbought} | "
            f"ATR multiplier: {atr_multiplier}x | "
            f"BB width max: {bb_width_max}%"
        )


    # ============================================================
    # EVALUATE — MAIN ENTRY POINT
    # Called by StrategyRouter when regime is RANGING
    # Returns signal dict or NO_SIGNAL dict — never None
    # ============================================================
    def evaluate(self, df: pd.DataFrame, regime_result) -> dict:
        """
        Evaluates current market for a mean reversion opportunity.

        df:            OHLCV DataFrame — must have enough candles
        regime_result: RegimeResult from RegimeDetector

        Returns signal dict with keys:
            signal   — "BUY", "SELL", or "NO_SIGNAL"
            entry    — intended entry price
            sl       — stop loss price
            tp       — take profit price
            rsi      — RSI value at signal time
            reason   — why signal fired or was rejected
            strategy — strategy name for journal
        """

        # ── GUARD: minimum candle count ───────────────────────
        # Need enough data for RSI, ATR, and BB to warm up properly
        if len(df) < self.min_candles:
            return self._no_signal("not_enough_data")

        # ── GUARD: RANGING regime check ───────────────────────
        # FIX: was "ranging" (lowercase) — RegimeDetector returns "RANGING"
        # This single character fix is what allows this strategy to fire
        if regime_result.regime != "RANGING":
            return self._no_signal("non_ranging_market")

        # ── STEP 1: CALCULATE RSI ─────────────────────────────
        close = df["close"]    # Close price series

        delta    = close.diff()              # Candle-to-candle price change
        gain     = delta.clip(lower=0)       # Isolate upward moves
        loss     = -delta.clip(upper=0)      # Isolate downward moves (positive)
        avg_gain = gain.rolling(14).mean()   # 14-period average gain
        avg_loss = loss.rolling(14).mean()   # 14-period average loss

        # RS ratio — relative strength of gains vs losses
        # 1e-9 prevents division by zero when avg_loss is 0
        rs = avg_gain / (avg_loss + 1e-9)

        # RSI formula — normalises RS to 0-100 scale
        rsi = 100 - (100 / (1 + rs))

        # Get latest RSI value
        latest_rsi = float(rsi.iloc[-1])

        # Guard: RSI must be a valid number
        if pd.isna(latest_rsi):
            return self._no_signal("rsi_not_ready")

        # ── STEP 2: CALCULATE ATR ─────────────────────────────
        # ATR measures current volatility — used for SL and TP sizing
        atr = self._calculate_atr(df)

        if atr is None or atr == 0:
            return self._no_signal("atr_unavailable")

        # ── STEP 3: BOLLINGER BAND WIDTH FILTER ──────────────
        # NEW: only trade when the range is genuinely tight
        # Wide BB = choppy market = mean reversion fails
        bb_width = self._calculate_bb_width(df)

        if bb_width is None:
            return self._no_signal("bb_unavailable")

        # If bands are too wide, the range is not clean enough
        if bb_width > self.bb_width_max:
            logger.debug(
                f"[MeanReversionStrategy] BB width {bb_width:.2f}% "
                f"exceeds max {self.bb_width_max}% — skipping"
            )
            return self._no_signal("range_too_wide")

        # ── STEP 4: CONFIRMATION CANDLE CHECK ─────────────────
        # NEW: price must show reversal intent before entry
        # Prevents entering as price continues deeper into extreme
        # Check last two closes to confirm direction is reversing
        if len(df) >= 2:
            prev_close   = float(df["close"].iloc[-2])   # Previous candle close
            latest_close = float(df["close"].iloc[-1])   # Current candle close
        else:
            return self._no_signal("not_enough_data")

        # Current price for entry calculation
        price = latest_close

        # ── STEP 5: BUY SIGNAL — RSI OVERSOLD ────────────────
        # RSI below 30 = genuine oversold = price has fallen too far
        # Confirmation: latest close must be ABOVE previous close
        # (price starting to bounce back up)
        if latest_rsi < self.rsi_oversold:

            # Confirmation candle: price must be turning up
            if latest_close <= prev_close:
                return self._no_signal("buy_no_confirmation")

            # Calculate SL and TP from ATR
            sl_distance = atr * self.atr_multiplier        # Stop below entry
            tp_distance = sl_distance * self.rr_ratio      # Target above entry

            sl = round(price - sl_distance, 6)    # Stop loss below entry
            tp = round(price + tp_distance, 6)    # Take profit above entry

            logger.debug(
                f"[MeanReversionStrategy] BUY signal | "
                f"RSI: {latest_rsi:.1f} | "
                f"BB width: {bb_width:.2f}% | "
                f"ATR: {atr:.4f}"
            )

            return {
                "signal":   "BUY",               # Direction
                "entry":    round(price, 6),      # Entry at current price
                "sl":       sl,                   # Stop below entry
                "tp":       tp,                   # Target above entry
                "rsi":      latest_rsi,           # RSI at signal
                "reason":   "mr_buy_oversold",    # Reason code
                "strategy": self.name, 
                "structure": "RANGING",
            }

        # ── STEP 6: SELL SIGNAL — RSI OVERBOUGHT ─────────────
        # RSI above 70 = genuine overbought = price has risen too far
        # Confirmation: latest close must be BELOW previous close
        # (price starting to fall back down)
        if latest_rsi > self.rsi_overbought:

            # Confirmation candle: price must be turning down
            if latest_close >= prev_close:
                return self._no_signal("sell_no_confirmation")

            # Calculate SL and TP from ATR
            sl_distance = atr * self.atr_multiplier        # Stop above entry
            tp_distance = sl_distance * self.rr_ratio      # Target below entry

            sl = round(price + sl_distance, 6)    # Stop loss above entry
            tp = round(price - tp_distance, 6)    # Take profit below entry

            logger.debug(
                f"[MeanReversionStrategy] SELL signal | "
                f"RSI: {latest_rsi:.1f} | "
                f"BB width: {bb_width:.2f}% | "
                f"ATR: {atr:.4f}"
            )

            return {
                "signal":   "SELL",               # Direction
                "entry":    round(price, 6),      # Entry at current price
                "sl":       sl,                   # Stop above entry (short)
                "tp":       tp,                   # Target below entry (short)
                "rsi":      latest_rsi,           # RSI at signal
                "reason":   "mr_sell_overbought", # Reason code
                "strategy": self.name, 
                "structure": "RANGING",
            }

        # RSI is between 30 and 70 — no extreme, no signal
        return self._no_signal("no_extreme")


    # ============================================================
    # CALCULATE ATR — INTERNAL
    # Average True Range — measures current candle volatility
    # Used to set SL and TP distances that adapt to market conditions
    # ============================================================
    def _calculate_atr(self, df: pd.DataFrame) -> float:
        """
        Calculates 14-period ATR from high, low, close.
        Returns float ATR value or None on failure.
        """

        try:
            high  = df["high"]      # Candle high prices
            low   = df["low"]       # Candle low prices
            close = df["close"]     # Candle close prices

            # True Range = largest of three measurements
            # Accounts for gaps between candles
            tr = np.maximum(
                high - low,                           # Current candle range
                np.maximum(
                    abs(high - close.shift(1)),       # Gap from prev close to high
                    abs(low  - close.shift(1))        # Gap from prev close to low
                )
            )

            # 14-period rolling average of True Range
            atr = tr.rolling(14).mean().iloc[-1]

            # Return float or None if NaN
            return float(atr) if not pd.isna(atr) else None

        except Exception as e:
            logger.warning(f"[MeanReversionStrategy] ATR calculation failed: {e}")
            return None    # Caller handles None safely


    # ============================================================
    # CALCULATE BB WIDTH — INTERNAL
    # Bollinger Band width as % of middle band
    # Narrow = tight range = good for mean reversion
    # Wide   = choppy range = bad for mean reversion
    # ============================================================
    def _calculate_bb_width(self, df: pd.DataFrame) -> float:
        """
        Calculates Bollinger Band width as % of the 20-period SMA.
        Returns float percentage or None on failure.
        """

        try:
            close = df["close"]

            # 20-period simple moving average — middle band
            middle = close.rolling(20).mean()

            # Standard deviation over 20 periods
            std = close.rolling(20).std()

            # Upper and lower bands at 2 standard deviations
            upper = middle + (2 * std)
            lower = middle - (2 * std)

            # Latest values
            latest_upper  = float(upper.iloc[-1])
            latest_lower  = float(lower.iloc[-1])
            latest_middle = float(middle.iloc[-1])

            # Guard against zero or NaN middle band
            if latest_middle == 0 or pd.isna(latest_middle):
                return None

            # Width as percentage of middle band
            # This normalises for different price levels (BTC vs NEAR)
            width_pct = (latest_upper - latest_lower) / latest_middle * 100

            return float(width_pct) if not pd.isna(width_pct) else None

        except Exception as e:
            logger.warning(f"[MeanReversionStrategy] BB width calculation failed: {e}")
            return None    # Caller handles None safely


    # ============================================================
    # NO SIGNAL — INTERNAL
    # Returns consistent NO_SIGNAL dict with reason
    # Never returns None — execution engine expects a dict always
    # ============================================================
    def _no_signal(self, reason: str) -> dict:
        """Returns a standardised NO_SIGNAL dict with reason code."""

        return {
            "signal":   "NO_SIGNAL",   # Tells router nothing fired
            "entry":    0,             # Zero — not used on NO_SIGNAL
            "sl":       0,             # Zero — not used on NO_SIGNAL
            "tp":       0,             # Zero — not used on NO_SIGNAL
            "rsi":      0,             # No RSI on rejection
            "reason":   reason,        # Why no signal was produced
            "strategy": self.name,      # which strategy rejected it
            "structure": "RANGING",
        }