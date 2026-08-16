#===========================================================
# bot/strategies/moving_average.py
# Moving Average Strategy — STRUCTURE-BASED VERSION
#
# KEY CHANGE FROM PREVIOUS VERSION:
# RSI filter REMOVED entirely — replaced with structure analysis
#
# NEW ENTRY LOGIC:
# LONG  requires: EMA tren__d UP + structure UPTREND (HH+HL) + pullback + momentum
# SHORT requires: EMA trend DOWN + structure DOWNTREND (LH+LL) + pullback + momentum
#
# WHY STRUCTURE INSTEAD OF RSI:
# RSI measures speed of price movement — lagging and interpretive
# Structure measures actual directional progress — objective and factual
# A market making HH+HL IS trending up by definition
# RSI at 47 in a trending market tells you nothing useful
# ============================================================

import pandas as pd    # DataFrame operations
import numpy as np     # Numerical operations
import logging         # Structured logging

# Import the new structure analyser
from bot.engines.structure_analyser import StructureAnalyser

# Create logger for this module
logger = logging.getLogger(__name__)


class MovingAverageStrategy:
    """
    EMA trend-following strategy with price structure confirmation.

    LONG entry requires ALL of:
    1. EMA20 above EMA50        — trend direction confirmed
    2. Structure = UPTREND      — price making HH + HL (replaces RSI)
    3. Pullback to EMA20        — entering on a dip not at the top
    4. Momentum candle          — last close above previous close

    SHORT entry requires ALL of:
    1. EMA20 below EMA50        — downtrend confirmed
    2. Structure = DOWNTREND    — price making LH + LL (replaces RSI)
    3. Pullback to EMA20        — entering on a bounce not at the bottom
    4. Momentum candle          — last close below previous close
    """

    def __init__(self, name="MovingAverageStrategy"):

        # Strategy name — used in logs and trade narrative
        self.name = name

        # ── EMA SETTINGS ─────────────────────────────────────
        self.ema_fast_period = 20    # Fast EMA — tracks recent price
        self.ema_slow_period = 50    # Slow EMA — defines trend direction

        # ── PULLBACK SETTINGS ─────────────────────────────────
        self.pullback_tolerance      = 1.005   # claude code changed: fixed comment — value is 0.5% above EMA20, not 2% as previously stated
        self.pullback_sell_tolerance = 0.995   # claude code changed: fixed comment — value is 0.5% below EMA20, not 2% as previously stated

        # ── SL/TP SETTINGS ────────────────────────────────────
        self.sl_range_factor = 0.15   # SL = 40% of recent 20-candle range
        self.tp_rr_ratio     = 3.0   # TP = 3x SL — 1:3 RRR

        # ── MINIMUM CANDLES ───────────────────────────────────
        # Raised to 100 — structure analyser needs swing points to form
        self.min_candles = 100

        # ── STRUCTURE ANALYSER ────────────────────────────────
        # Instantiated once — called every candle in check_signal()
        self.structure_analyser = StructureAnalyser(
            lookback=10,               # 5 candles each side for swings
            min_swing_points=2,       # Need 2 swings each type
            min_structure_change=0.003,  # 0.1% minimum between swings
        )

        logger.info(
            f"[MovingAverageStrategy] Initialised | "
            f"Structure-based entry | RRR: 1:{self.tp_rr_ratio}"
        )


    # ============================================================
    # CHECK SIGNAL — MAIN METHOD
    # ============================================================
    def check_signal(self, df: pd.DataFrame) -> dict:
        """
        Analyses price data and returns a trading signal.
        Replaces RSI filter with structure analysis.
        Never returns None.
        """

        df = df.copy()

        # ── VALIDATION ───────────────────────────────────────
        required_cols = ["close", "high", "low"]

        if not all(col in df.columns for col in required_cols):
            return self._no_signal("missing_data")

        df[required_cols] = df[required_cols].apply(
            pd.to_numeric, errors="coerce"
        )
        df.dropna(subset=required_cols, inplace=True)

        if len(df) < self.min_candles:
            return self._no_signal("not_enough_data")

        # ── CALCULATE EMAs ────────────────────────────────────
        df["ema_20"] = df["close"].ewm(
            span=self.ema_fast_period, adjust=False
        ).mean()

        df["ema_50"] = df["close"].ewm(
            span=self.ema_slow_period, adjust=False
        ).mean()

        ema20 = df["ema_20"].iloc[-1]
        ema50 = df["ema_50"].iloc[-1]

        if pd.isna(ema20) or pd.isna(ema50):
            return self._no_signal("indicator_warmup")

        # ── DETECT MARKET STRUCTURE ───────────────────────────
        # This is the core upgrade — replaces RSI entirely
        # Finds swing highs and lows and determines structural trend
        structure_result = self.structure_analyser.get_market_structure(df)

        if structure_result is None:
            return self._no_signal("structure_analysis_failed")

        logger.debug(
            f"[MovingAverageStrategy] {structure_result.summary}"
        )

        # ── NORMALISE STRUCTURE ───────────────────────────────
        structure_raw = getattr(structure_result, "structure", "UNKNOWN")
        structure_label = str(structure_raw or "UNKNOWN").upper().strip()

        if structure_label in ("", "NONE", "NAN"):
            structure_label = "UNKNOWN"

        # ── EXTRACT PRICE VALUES ──────────────────────────────
        latest_price = float(df["close"].iloc[-1])
        prev_price   = float(df["close"].iloc[-2])

        # ── CALCULATE DYNAMIC SL/TP ───────────────────────────
        recent      = df.iloc[-20:]
        recent_high = recent["high"].max()
        recent_low  = recent["low"].min()

        if recent_high == recent_low:
            return self._no_signal("flat_market", structure=structure_label)

        candle_range = recent_high - recent_low
        sl_distance  = candle_range * self.sl_range_factor
        tp_distance  = sl_distance * self.tp_rr_ratio

        # ── TREND DIRECTION ───────────────────────────────────
        bullish = ema20 > ema50
        bearish = ema20 < ema50

        # ── PULLBACK CONDITIONS ───────────────────────────────
        pullback_buy  = latest_price <= ema20 * self.pullback_tolerance
        pullback_sell = latest_price >= ema20 * self.pullback_sell_tolerance

        # ── MOMENTUM ─────────────────────────────────────────
        bullish_momentum = latest_price > prev_price
        bearish_momentum = latest_price < prev_price


        # ── BUY SIGNAL LOGIC ─────────────────────────────────
        if bullish:

            # STRUCTURE GATE — replaces RSI filter
            # EMA says uptrend but structure must confirm HH + HL
            # Without this we enter trends that are not real
            if structure_label != "UPTREND":
                return self._no_signal(
                    f"structure_not_uptrend_{structure_label.lower()}",
                    structure=structure_label
                )

            # Pullback to EMA20 required
            if not pullback_buy:
                return self._no_signal(
                    "no_pullback",
                    structure=structure_label
                )

            # Momentum — last candle closed up
            if not bullish_momentum:
                return self._no_signal(
                    "no_momentum",
                    structure=structure_label
                )

            # All conditions met — BUY signal
            logger.info(
                f"[MovingAverageStrategy] BUY | "
                f"UPTREND confirmed | "
                f"HH: {structure_result.higher_highs} | "
                f"HL: {structure_result.higher_lows} | "
                f"Swings: {structure_result.swing_high_count}H "
                f"{structure_result.swing_low_count}L"
            )

            return {
                "signal":           "BUY",
                "entry":            round(latest_price, 6),
                "sl":               round(latest_price - sl_distance, 6),
                "tp":               round(latest_price + tp_distance, 6),
                "rsi":              0,
                "reason":           "structure_uptrend_pullback",
                "strategy":         self.name,
                "structure":        structure_label,
                "higher_highs":     structure_result.higher_highs,
                "higher_lows":      structure_result.higher_lows,
                "lower_highs":      getattr(structure_result, "lower_highs", False),
                "lower_lows":       getattr(structure_result, "lower_lows", False),
                "last_swing_high":  structure_result.last_swing_high,
                "last_swing_low":   structure_result.last_swing_low,
                "swing_high_count": structure_result.swing_high_count,
                "swing_low_count":  structure_result.swing_low_count,
            }


        # ── SELL SIGNAL LOGIC ─────────────────────────────────
        if bearish:

            # STRUCTURE GATE for shorts
            # EMA says downtrend but structure must confirm LH + LL
            if structure_label != "DOWNTREND":
                return self._no_signal(
                    f"structure_not_downtrend_{structure_label.lower()}",
                    structure=structure_label
                )

            # Pullback to EMA20 required
            if not pullback_sell:
                return self._no_signal(
                    "no_pullback",
                    structure=structure_label
                )

            # Momentum — last candle closed down
            if not bearish_momentum:
                return self._no_signal(
                    "no_momentum",
                    structure=structure_label
                )

            # All conditions met — SELL signal
            logger.info(
                f"[MovingAverageStrategy] SELL | "
                f"DOWNTREND confirmn ed | "
                f"LH: {getattr(structure_result, 'lower_highs', False)} | "
                f"LL: {getattr(structure_result, 'lower_lows', False)} | "
                f"Swings: {structure_result.swing_high_count}H "
                f"{structure_result.swing_low_count}L"
            )

            return {
                "signal":           "SELL",
                "entry":            round(latest_price, 6),
                "sl":               round(latest_price + sl_distance, 6),
                "tp":               round(latest_price - tp_distance, 6),
                "rsi":              0,
                "reason":           "structure_downtrend_pullback",
                "strategy":         self.name,
                "structure":        structure_label,
                "higher_highs":     getattr(structure_result, "higher_highs", False),
                "higher_lows":      getattr(structure_result, "higher_lows", False),
                "lower_highs":      getattr(structure_result, "lower_highs", False),
                "lower_lows":       getattr(structure_result, "lower_lows", False),
                "last_swing_high":  structure_result.last_swing_high,
                "last_swing_low":   structure_result.last_swing_low,
                "swing_high_count": structure_result.swing_high_count,
                "swing_low_count":  structure_result.swing_low_count,
            }

        # EMAs flat or crossing — no trade
        return self._no_signal(
            "no_ema_trend",
            structure=structure_label
        )


    # ============================================================
    # NO SIGNAL HELPER
    # ============================================================
    def _no_signal(self, reason: str, structure: str = "UNKNOWN") -> dict:
        """Returns consistent NO_SIGNAL dict. Never returns None."""

        structure = str(structure or "UNKNOWN").upper().strip()

        return {
            "signal":    "NO_SIGNAL",
            "entry":     0,
            "sl":        0,
            "tp":        0,
            "rsi":       0,
            "reason":    reason,
            "strategy":  self.name,
            "structure": structure,
        }