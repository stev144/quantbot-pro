# ============================================================
# bot/engines/structure_analyser.py
# Price Structure Analyser — institutional grade HH/HL/LH/LL detection
#
# PURPOSE:
# Identifies the current market structure by finding significant
# swing points (peaks and troughs) and comparing them sequentially.
#
# This replaces RSI as the trend confirmation filter because:
# - RSI measures speed of price movement (lagging, interpretive)
# - Structure measures actual directional progress (objective, factual)
#
# WHAT IT DETECTS:
#   UPTREND     — Higher Highs + Higher Lows   → buy setups valid
#   DOWNTREND   — Lower Highs  + Lower Lows    → sell setups valid
#   EXPANDING   — Higher Highs + Lower Lows    → volatility expanding, no trade
#   CONTRACTING — Lower Highs  + Higher Lows   → coiling, breakout pending
#   UNCLEAR     — Not enough swing points yet  → wait for more data
#
# HOW SWING POINTS ARE FOUND:
# A swing high at candle i is confirmed when the high at i is
# greater than the highs of N candles on both sides of it.
# N is the lookback — larger N = fewer but more significant swings.
# Default lookback of 5 works well on 1H BTC data.
#
# INSTITUTIONAL CONTEXT:
# This is the same logic used in Smart Money Concepts (SMC),
# Wyckoff methodology, and institutional order flow analysis.
# All of them start with the same question:
# "Is price making progress in one direction?"
# ============================================================

import pandas as pd        # DataFrame operations
import numpy as np         # Numerical operations
import logging             # Structured logging
from dataclasses import dataclass, field    # Clean result objects
from typing import List, Tuple, Optional    # Type hints


# Create logger for this module
logger = logging.getLogger(__name__)


# ============================================================
# SWING POINT — represents a single peak or trough
# ============================================================
@dataclass
class SwingPoint:
    """
    Represents a single confirmed swing high or swing low.

    index: candle index in the DataFrame where swing occurred
    price: the high (for swing highs) or low (for swing lows)
    candle_time: timestamp of the swing candle (for logging)
    swing_type: "HIGH" or "LOW"
    """
    index:       int          # Position in DataFrame
    price:       float        # Price level of the swing
    candle_time: str          # Timestamp string for logging
    swing_type:  str          # "HIGH" or "LOW"


# ============================================================
# STRUCTURE RESULT — returned by get_market_structure()
# ============================================================
@dataclass
class StructureResult:
    """
    Complete market structure classification for the current candle.

    This is what MovingAverageStrategy reads to decide if entry is valid.
    """

    # Primary structure classification
    # "UPTREND", "DOWNTREND", "EXPANDING", "CONTRACTING", "UNCLEAR"
    structure: str

    # Whether the last two swing highs confirm the trend direction
    # True for UPTREND = last swing high > previous swing high
    higher_highs: bool

    # Whether the last two swing lows confirm the trend direction
    # True for UPTREND = last swing low > previous swing low
    higher_lows: bool

    # Mirror conditions for downtrend
    lower_highs: bool
    lower_lows:  bool

    # The last confirmed swing high price — used for stop placement
    last_swing_high: float

    # The last confirmed swing low price — used for stop placement
    last_swing_low: float

    # Previous swing high — compared against last to confirm structure
    prev_swing_high: float

    # Previous swing low — compared against last to confirm structure
    prev_swing_low: float

    # How many swing points were found — low count = less reliable
    swing_high_count: int
    swing_low_count:  int

    # Human readable summary for trade narrative
    summary: str


# ============================================================
# STRUCTURE ANALYSER — MAIN CLASS
# ============================================================
class StructureAnalyser:
    """
    Detects market structure from OHLCV price data.

    Instantiated once in MovingAverageStrategy.__init__()
    Called every candle via get_market_structure(df)

    Usage:
        analyser = StructureAnalyser()
        result = analyser.get_market_structure(df)

        if result.structure == "UPTREND":
            # Price is making HH + HL — trend entry valid
    """

    def __init__(
        self,
        lookback: int = 5,           # Candles each side to confirm a swing
        min_swing_points: int = 2,   # Minimum swings needed for structure
        min_structure_change: float = 0.001,  # Minimum % move to count as new swing
    ):
        # How many candles on each side must be exceeded for a swing
        # 5 works well on 1H BTC — catches significant turns, ignores noise
        # Lower = more swings found (more noise)
        # Higher = fewer swings found (only major turns)
        self.lookback = lookback

        # Minimum swing points required before classifying structure
        # Need at least 2 swing highs and 2 swing lows for comparison
        self.min_swing_points = min_swing_points

        # Minimum percentage price change between swings
        # Filters out tiny swings that are just noise
        # 0.001 = 0.1% minimum — very small, most real swings exceed this
        self.min_structure_change = min_structure_change

        logger.info(
            f"[StructureAnalyser] Initialised | "
            f"Lookback: {lookback} candles | "
            f"Min swings: {min_swing_points} | "
            f"Min change: {min_structure_change * 100:.2f}%"
        )


    # ============================================================
    # GET MARKET STRUCTURE — MAIN PUBLIC METHOD
    # Called every candle by MovingAverageStrategy
    # Returns StructureResult with full classification
    # ============================================================
    def get_market_structure(self, df: pd.DataFrame) -> StructureResult:
        df = df.iloc[-200:].copy()
        
        """
        Analyses the DataFrame and returns the current market structure.

        df: OHLCV DataFrame with at least (lookback * 2 + min_swing_points * 10) candles

        Returns StructureResult — never raises an exception.
        """

        # Minimum candles needed = lookback on each side * enough candles for swings
        min_candles = (self.lookback * 2) + (self.min_swing_points * 10)

        # Guard: not enough data
        if df is None or len(df) < min_candles:
            return self._unclear_result(
                f"not_enough_data ({len(df) if df is not None else 0} candles)"
            )

        try:
            # ── STEP 1: FIND ALL SWING HIGHS ─────────────────
            swing_highs = self._find_swing_highs(df)

            # ── STEP 2: FIND ALL SWING LOWS ──────────────────
            swing_lows = self._find_swing_lows(df)

            # ── STEP 3: CHECK MINIMUM SWING COUNT ────────────
            # Need at least 2 of each to compare and find structure
            if len(swing_highs) < self.min_swing_points:
                return self._unclear_result(
                    f"insufficient_swing_highs ({len(swing_highs)} found)"
                )

            if len(swing_lows) < self.min_swing_points:
                return self._unclear_result(
                    f"insufficient_swing_lows ({len(swing_lows)} found)"
                )

            # ── STEP 4: GET LAST TWO OF EACH ─────────────────
            # We compare the most recent swing to the one before it
            # This is the core of structure analysis

            last_sh  = swing_highs[-1]    # Most recent swing high
            prev_sh  = swing_highs[-2]    # Previous swing high

            last_sl  = swing_lows[-1]     # Most recent swing low
            prev_sl  = swing_lows[-2]     # Previous swing low

            # ── STEP 5: COMPARE SWING POINTS ─────────────────

            # Higher High: most recent swing high is above previous
            # This confirms buyers are pushing higher
            higher_highs = last_sh.price > prev_sh.price * (1 + self.min_structure_change)

            # Higher Low: most recent swing low is above previous
            # This confirms buyers are defending higher levels on dips
            higher_lows = last_sl.price > prev_sl.price * (1 + self.min_structure_change)

            # Lower High: most recent swing high is below previous
            # This confirms sellers are pushing down from lower levels
            lower_highs = last_sh.price < prev_sh.price * (1 - self.min_structure_change)

            # Lower Low: most recent swing low is below previous
            # This confirms sellers are driving price to new lows
            lower_lows = last_sl.price < prev_sl.price * (1 - self.min_structure_change)

            # ── STEP 6: CLASSIFY STRUCTURE ────────────────────

            if higher_highs and higher_lows:
                # Both highs and lows are rising — confirmed uptrend
                # This is the strongest buy signal from structure
                structure = "UPTREND"

            elif lower_highs and lower_lows:
                # Both highs and lows are falling — confirmed downtrend
                # This is the strongest sell signal from structure
                structure = "DOWNTREND"

            elif higher_highs and lower_lows:
                # Highs rising but lows also falling — range expanding
                # Market is becoming more volatile — no clear direction
                # Do not trade — wait for one side to take control
                structure = "EXPANDING"

            elif lower_highs and higher_lows:
                # Highs falling and lows rising — range contracting
                # Market is coiling — breakout is coming
                # Wait for the breakout direction to be confirmed
                structure = "CONTRACTING"

            else:
                # Mixed signals — structure is transitioning
                structure = "UNCLEAR"

            # ── STEP 7: BUILD SUMMARY STRING ─────────────────
            summary = (
                f"{structure} | "
                f"HH: {higher_highs} | HL: {higher_lows} | "
                f"LH: {lower_highs} | LL: {lower_lows} | "
                f"Last SH: {last_sh.price:.2f} | "
                f"Last SL: {last_sl.price:.2f} | "
                f"Swings found: {len(swing_highs)}H {len(swing_lows)}L"
            )

            logger.debug(f"[StructureAnalyser] {summary}")

            # Return the complete result
            return StructureResult(
                structure        = structure,
                higher_highs     = higher_highs,
                higher_lows      = higher_lows,
                lower_highs      = lower_highs,
                lower_lows       = lower_lows,
                last_swing_high  = last_sh.price,
                last_swing_low   = last_sl.price,
                prev_swing_high  = prev_sh.price,
                prev_swing_low   = prev_sl.price,
                swing_high_count = len(swing_highs),
                swing_low_count  = len(swing_lows),
                summary          = summary,
            )

        except Exception as e:
            logger.error(
                f"[StructureAnalyser] Structure detection failed: "
                f"{type(e).__name__}: {e}"
            )
            return self._unclear_result("detection_error")


    # ============================================================
    # IS STRUCTURE BROKEN — used for early exit signal
    # Called by execution engine every candle while in a trade
    # Returns True if the trend structure has been invalidated
    # ============================================================
    def is_structure_broken(
        self,
        df: pd.DataFrame,
        trade_direction: str,    # "BUY" or "SELL" (from position dict)
    ) -> bool:
        """
        Returns True if the market structure has broken against
        the direction of the open trade.

        Used as an early exit signal — get out before the full
        stop loss is hit when structure breaks down.

        For a BUY trade: structure breaks if price makes a Lower Low
        For a SELL trade: structure breaks if price makes a Higher High

        This is called "Change of Character" (CHoCH) in institutional
        trading — the market has changed its behaviour.
        """

        try:
            # Get current structure
            result = self.get_market_structure(df)

            if result.structure == "UNCLEAR":
                return False    # Cannot confirm break — do not exit

            if trade_direction == "BUY":
                # In a long trade — structure breaks if lower low forms
                # A lower low means buyers are no longer defending lows
                # The uptrend assumption is invalidated
                return result.lower_lows    # True = structure broken = exit

            elif trade_direction == "SELL":
                # In a short trade — structure breaks if higher high forms
                # A higher high means sellers are no longer controlling highs
                # The downtrend assumption is invalidated
                return result.higher_highs    # True = structure broken = exit

            return False    # Unknown direction — do not force exit

        except Exception as e:
            logger.error(f"[StructureAnalyser] Structure break check failed: {e}")
            return False    # On error, do not force exit


    # ============================================================
    # FIND SWING HIGHS — INTERNAL
    # Finds all local peaks in the high price series
    # ============================================================
    def _find_swing_highs(self, df: pd.DataFrame) -> List[SwingPoint]:
        """
        Finds all confirmed swing highs in the DataFrame.

        A swing high at candle i is confirmed when:
        df["high"].iloc[i] is greater than the highs of
        the previous N candles AND the next N candles
        (where N = self.lookback)

        Returns list of SwingPoint objects, oldest first.
        """

        swing_highs = []    # Will hold all confirmed swing highs
        # claude code changed: was "highs = df['high']" (a pandas Series). Profiling a real
        # backtest showed this loop spending ~200 of ~245 total seconds here — not from the
        # O(n) scan itself, but from pandas per-call overhead (Series.__init__,
        # _construct_result, __finalize__, isinstance checks) on every .iloc[] slice and
        # comparison, done twice per iteration. Switching to a plain numpy array keeps the
        # exact same loop/comparison logic (same boundary conditions, same "<"/".all()"
        # semantics) but removes that per-call pandas overhead entirely.
        highs = df["high"].values  # claude code changed: was df["high"] (pandas Series) — now numpy array

        # Loop through candles — skip first and last N (need N candles each side)
        for i in range(self.lookback, len(df) - self.lookback):

            current_high = highs[i]    # claude code changed: was highs.iloc[i]

            # Check N candles to the LEFT — all must be lower
            left_candles = highs[i - self.lookback : i]        # claude code changed: was highs.iloc[i - self.lookback : i]
            all_left_lower = (left_candles < current_high).all()

            # Check N candles to the RIGHT — all must be lower
            right_candles = highs[i + 1 : i + self.lookback + 1]  # claude code changed: was highs.iloc[i + 1 : i + self.lookback + 1]
            all_right_lower = (right_candles < current_high).all()

            # Both sides must be lower — this is a local peak
            if all_left_lower and all_right_lower:

                # Get timestamp for logging
                try:
                    candle_time = str(df.index[i])
                except Exception:
                    candle_time = f"candle_{i}"

                # Create SwingPoint object
                swing = SwingPoint(
                    index       = i,
                    price       = float(current_high),
                    candle_time = candle_time,
                    swing_type  = "HIGH",
                )

                swing_highs.append(swing)    # Add to list

        logger.debug(
            f"[StructureAnalyser] Found {len(swing_highs)} swing highs"
        )

        return swing_highs    # List of all swing highs, oldest first


    # ============================================================
    # FIND SWING LOWS — INTERNAL
    # Finds all local troughs in the low price series
    # ============================================================
    def _find_swing_lows(self, df: pd.DataFrame) -> List[SwingPoint]:
        """
        Finds all confirmed swing lows in the DataFrame.

        A swing low at candle i is confirmed when:
        df["low"].iloc[i] is less than the lows of
        the previous N candles AND the next N candles

        Returns list of SwingPoint objects, oldest first.
        """

        swing_lows = []    # Will hold all confirmed swing lows
        lows = df["low"].values   # claude code changed: was df["low"] (pandas Series) — see _find_swing_highs for why

        # Loop through candles — skip first and last N
        for i in range(self.lookback, len(df) - self.lookback):

            current_low = lows[i]    # claude code changed: was lows.iloc[i]

            # Check N candles to the LEFT — all must be higher
            left_candles = lows[i - self.lookback : i]           # claude code changed: was lows.iloc[i - self.lookback : i]
            all_left_higher = (left_candles > current_low).all()

            # Check N candles to the RIGHT — all must be higher
            right_candles = lows[i + 1 : i + self.lookback + 1]  # claude code changed: was lows.iloc[i + 1 : i + self.lookback + 1]
            all_right_higher = (right_candles > current_low).all()

            # Both sides must be higher — this is a local trough
            if all_left_higher and all_right_higher:

                # Get timestamp for logging
                try:
                    candle_time = str(df.index[i])
                except Exception:
                    candle_time = f"candle_{i}"

                # Create SwingPoint object
                swing = SwingPoint(
                    index       = i,
                    price       = float(current_low),
                    candle_time = candle_time,
                    swing_type  = "LOW",
                )

                swing_lows.append(swing)    # Add to list

        logger.debug(
            f"[StructureAnalyser] Found {len(swing_lows)} swing lows"
        )

        return swing_lows    # List of all swing lows, oldest first


    # ============================================================
    # GET STRUCTURE FOR NARRATIVE — PUBLIC UTILITY
    # Returns a simple string summary for trade narrative storage
    # ============================================================
    def get_structure_label(self, df: pd.DataFrame) -> str:
        """
        Returns a short string label of the current structure.
        Used by TradeNarrativeGenerator for trade context.
        """

        result = self.get_market_structure(df)
        return result.structure    # "UPTREND", "DOWNTREND", etc.


    # ============================================================
    # UNCLEAR RESULT — INTERNAL
    # Returns a safe default when structure cannot be determined
    # ============================================================
    def _unclear_result(self, reason: str) -> StructureResult:
        """Returns a safe UNCLEAR StructureResult with all fields populated."""

        return StructureResult(
            structure        = "UNCLEAR",
            higher_highs     = False,
            higher_lows      = False,
            lower_highs      = False,
            lower_lows       = False,
            last_swing_high  = 0.0,
            last_swing_low   = 0.0,
            prev_swing_high  = 0.0,
            prev_swing_low   = 0.0,
            swing_high_count = 0,
            swing_low_count  = 0,
            summary          = f"UNCLEAR | reason: {reason}",
        )