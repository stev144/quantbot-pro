# ============================================================
# bot/engines/trade_narrative.py
# Trade Narrative — generates human readable trade context
#
# PURPOSE:
# Every trade the bot takes gets a full written explanation.
# Not just "BUY at 5.00" — but WHY it was taken, WHAT the
# market conditions were, WHICH strategy fired, and HOW
# confident the regime detection was at that moment.
#
# This file answers the question every trader eventually asks:
# "Why did the bot take that trade?"
#
# WHAT IT RECORDS PER TRADE:
#   - Which regime the trade was triggered in
#   - Which strategy produced the signal
#   - All entry conditions and whether each passed
#   - ADX, ATR ratio, EMA spread, BB width at entry
#   - RSI value and which zone it was in
#   - Regime confidence level
#   - A full human-readable narrative paragraph
#   - Exit narrative when the trade closes
#
# WHERE IT CONNECTS:
#   Receives: enriched signal dict from strategy_router.py
#   Sends to: trade_logger.py (stored in TradeRecord)
#             dashboard views (displayed in journal tab)
#
# INSTITUTIONAL CONTEXT:
# Professional trading desks require trade rationale for every
# position. This is used for post-trade analysis, risk review,
# and identifying which conditions produce the best outcomes.
# ============================================================

import logging                        # Structured logging
from dataclasses import dataclass, field   # Clean structured objects
from typing import Dict, Any          # Type hints for clarity
from datetime import datetime         # Timestamp for each narrative

# Create logger for this module
logger = logging.getLogger(__name__)


# ============================================================
# TRADE NARRATIVE RESULT — DATA CLASS
# Holds the complete narrative for one trade entry or exit
# ============================================================
@dataclass
class TradeNarrative:
    """
    Complete narrative context for a single trade.

    Generated at entry — updated at exit.
    Stored in TradeRecord for dashboard display and analysis.
    """

    # ── IDENTITY ─────────────────────────────────────────────
    symbol: str              # Trading pair e.g. "NEAR/USDT"
    direction: str           # "BUY" or "SELL"
    timestamp: str           # When the narrative was generated

    # ── STRATEGY CONTEXT ─────────────────────────────────────
    strategy_name: str       # Which strategy fired e.g. "MovingAverageStrategy"
    strategy_reason: str     # Internal reason code e.g. "ema_buy_setup"

    # ── REGIME CONTEXT ───────────────────────────────────────
    regime: str              # "TRENDING_UP", "TRENDING_DOWN", "RANGING"
    regime_confidence: str   # "HIGH", "MEDIUM", "LOW"
    adx: float               # ADX value at signal time
    atr_ratio: float         # ATR ratio at signal time
    ema_spread_pct: float    # EMA spread percentage at signal time
    bb_width_pct: float      # Bollinger Band width percentage

    # ── PRICE LEVELS ─────────────────────────────────────────
    entry: float             # Intended entry price
    sl: float                # Stop loss level
    tp: float                # Take profit level
    rsi: float               # RSI at signal time

    # ── CONDITION CHECKLIST ──────────────────────────────────
    # Each condition is True if it passed, False if it was borderline
    # This allows post-trade analysis of which conditions correlate
    # with winning trades vs losing trades
    conditions: Dict[str, bool] = field(default_factory=dict)

    # ── NARRATIVE TEXT ───────────────────────────────────────
    # Full human-readable explanation of why this trade was taken
    entry_narrative: str = ""

    # Updated when trade closes — explains the outcome
    exit_narrative: str = ""

    # ── CALCULATED FIELDS ────────────────────────────────────
    rr_ratio: float = 0.0    # Risk/Reward ratio (tp_distance / sl_distance)
    sl_distance: float = 0.0  # Distance from entry to stop in price units
    tp_distance: float = 0.0  # Distance from entry to TP in price units

    # ── RSI ZONE ─────────────────────────────────────────────
    rsi_zone: str = ""       # e.g. "MOMENTUM_ZONE", "OVERBOUGHT", "OVERSOLD"


# ============================================================
# TRADE NARRATIVE GENERATOR — MAIN CLASS
# ============================================================
class TradeNarrativeGenerator:
    """
    Generates complete trade narratives from enriched signal dicts.

    Instantiated once in execution_engine.py and backtester.py.
    Called at entry and exit for every trade.

    The narrative is stored in the database alongside the trade
    so it can be reviewed on the dashboard journal tab.
    """

    def __init__(self):
        # RSI zone definitions — used to describe RSI context in plain English
        # These describe what the RSI value means for the trade
        self.rsi_zones = {
            "OVERSOLD":        (0,  30),    # Extreme low — mean reversion buy zone
            "WEAK_OVERSOLD":   (30, 40),    # Approaching oversold — cautious
            "MOMENTUM_ZONE":   (40, 60),    # Neutral momentum — trend entry zone
            "WEAK_OVERBOUGHT": (60, 70),    # Approaching overbought — cautious
            "OVERBOUGHT":      (70, 100),   # Extreme high — mean reversion sell zone
        }

        # Regime descriptions — plain English for the narrative
        self.regime_descriptions = {
            "TRENDING_UP":     "a confirmed bullish trend",
            "TRENDING_DOWN":   "a confirmed bearish trend",
            "RANGING":         "a ranging / mean-reverting market",
            "HIGH_VOLATILITY": "an abnormally volatile market",
        }

        # Strategy descriptions — plain English for the narrative
        self.strategy_descriptions = {
            "MovingAverageStrategy": "EMA trend-following with pullback confirmation",
            "MeanReversionStrategy": "RSI mean reversion with ATR-based levels",
        }

        # Log initialisation
        logger.info("[TradeNarrativeGenerator] Initialised")


    # ============================================================
    # GENERATE ENTRY NARRATIVE — MAIN PUBLIC METHOD
    # Called by execution_engine.py after a signal is approved
    # by the strategy router but before the order is placed
    # ============================================================
    def generate_entry(self, signal: dict, symbol: str) -> TradeNarrative:
        """
        Generates a complete entry narrative from an enriched signal dict.

        signal: the enriched signal dict from strategy_router.py
                Must contain regime context fields added by the router
        symbol: trading pair e.g. "NEAR/USDT"

        Returns a TradeNarrative dataclass with all fields populated.
        """

        try:
            # ── EXTRACT SIGNAL FIELDS ─────────────────────────

            direction   = signal.get("signal", "UNKNOWN")     # "BUY" or "SELL"
            entry       = float(signal.get("entry", 0))       # Entry price
            sl          = float(signal.get("sl", 0))          # Stop loss
            tp          = float(signal.get("tp", 0))          # Take profit
            rsi         = float(signal.get("rsi", 0))         # RSI value
            reason      = signal.get("reason", "unknown")     # Signal reason code
            strategy    = signal.get("strategy", "unknown")   # Strategy name

            # ── EXTRACT REGIME FIELDS ─────────────────────────
            # These were attached by strategy_router._attach_regime_context()

            regime      = signal.get("regime", "UNKNOWN")             # Regime name
            confidence  = signal.get("regime_confidence", "UNKNOWN")  # Confidence
            adx         = float(signal.get("adx", 0))                 # ADX value
            atr_ratio   = float(signal.get("atr_ratio", 1.0))         # ATR ratio
            ema_spread  = float(signal.get("ema_spread_pct", 0))      # EMA spread %
            bb_width    = float(signal.get("bb_width_pct", 0))        # BB width %

            # ── CALCULATE TRADE GEOMETRY ──────────────────────

            # Distance from entry to stop loss in price units
            sl_distance = abs(entry - sl) if entry and sl else 0

            # Distance from entry to take profit in price units
            tp_distance = abs(tp - entry) if tp and entry else 0

            # Risk/Reward ratio — how many R is the target
            rr_ratio = round(tp_distance / sl_distance, 2) if sl_distance > 0 else 0

            # ── CLASSIFY RSI ZONE ─────────────────────────────
            rsi_zone = self._classify_rsi(rsi)

            # ── BUILD CONDITION CHECKLIST ─────────────────────
            # Documents which conditions were present at trade entry
            # Used in post-trade analysis to find best setups
            conditions = self._build_condition_checklist(
                signal, direction, adx, rsi, rsi_zone, regime, confidence
            )

            # ── GENERATE NARRATIVE TEXT ───────────────────────
            entry_text = self._write_entry_narrative(
                direction   = direction,
                symbol      = symbol,
                strategy    = strategy,
                reason      = reason,
                regime      = regime,
                confidence  = confidence,
                adx         = adx,
                atr_ratio   = atr_ratio,
                ema_spread  = ema_spread,
                rsi         = rsi,
                rsi_zone    = rsi_zone,
                entry       = entry,
                sl          = sl,
                tp          = tp,
                sl_distance = sl_distance,
                tp_distance = tp_distance,
                rr_ratio    = rr_ratio,
                conditions  = conditions,
            )

            # ── BUILD AND RETURN NARRATIVE OBJECT ─────────────
            narrative = TradeNarrative(
                symbol            = symbol,
                direction         = direction,
                timestamp         = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                strategy_name     = strategy,
                strategy_reason   = reason,
                regime            = regime,
                regime_confidence = confidence,
                adx               = round(adx, 2),
                atr_ratio         = round(atr_ratio, 2),
                ema_spread_pct    = round(ema_spread, 4),
                bb_width_pct      = round(bb_width, 4),
                entry             = entry,
                sl                = sl,
                tp                = tp,
                rsi               = round(rsi, 2),
                conditions        = conditions,
                entry_narrative   = entry_text,
                exit_narrative    = "",            # Filled later by generate_exit()
                rr_ratio          = rr_ratio,
                sl_distance       = round(sl_distance, 6),
                tp_distance       = round(tp_distance, 6),
                rsi_zone          = rsi_zone,
            )

            # Log confirmation that narrative was generated
            logger.info(
                f"[TradeNarrative] Entry narrative generated | "
                f"{direction} {symbol} | "
                f"Regime: {regime} ({confidence}) | "
                f"Strategy: {strategy} | "
                f"R:R = 1:{rr_ratio}"
            )

            return narrative   # Return the complete narrative object

        except Exception as e:
            # Never crash the bot because of narrative generation
            logger.error(
                f"[TradeNarrative] Entry generation failed: "
                f"{type(e).__name__}: {e}"
            )
            # Return a minimal fallback narrative
            return self._fallback_narrative(signal, symbol)


    # ============================================================
    # GENERATE EXIT NARRATIVE — CALLED WHEN TRADE CLOSES
    # Updates the narrative with exit context and outcome
    # ============================================================
    def generate_exit(
        self,
        narrative: TradeNarrative,  # The original entry narrative
        exit_price: float,          # Actual exit fill price
        exit_reason: str,           # "stop_loss", "take_profit", "force_close"
        net_pnl: float,             # Net P&L after fees
        r_multiple: float,          # R-Multiple achieved
        holding_candles: int = None # How many candles position was held
    ) -> str:
        """
        Generates the exit portion of the trade narrative.
        Returns a string that updates narrative.exit_narrative.
        """

        try:
            # Determine outcome word for the narrative
            if net_pnl > 0:
                outcome_word = "WIN"           # Positive P&L
                outcome_emoji = "✓"
            elif net_pnl < 0:
                outcome_word = "LOSS"          # Negative P&L
                outcome_emoji = "✗"
            else:
                outcome_word = "BREAKEVEN"     # Zero P&L
                outcome_emoji = "~"

            # Format exit reason into readable text
            exit_reason_text = {
                "stop_loss":   "Stop loss was hit",
                "take_profit": "Take profit target was reached",
                "force_close": "Position was force-closed at end of data",
                "manual":      "Position was manually closed",
            }.get(exit_reason, f"Closed: {exit_reason}")

            # Format holding time
            if holding_candles is not None:
                holding_text = f"after {holding_candles} candles"
            else:
                holding_text = "at market close"

            # Write the exit narrative
            exit_text = (
                f"EXIT {outcome_emoji} {outcome_word} | "
                f"{exit_reason_text} {holding_text}. "
                f"Exit price: {exit_price}. "
                f"Net P&L: ${net_pnl:.2f}. "
                f"R-Multiple: {r_multiple:.2f}R. "
            )

            # Add performance commentary based on outcome
            if r_multiple >= 2.0:
                # Excellent outcome — trade ran well beyond expectations
                exit_text += (
                    f"Excellent execution — trade achieved {r_multiple:.1f}R, "
                    f"well above the 1:1 threshold."
                )
            elif r_multiple >= 1.0:
                # Good outcome — trade hit target
                exit_text += (
                    f"Good execution — trade reached target with {r_multiple:.1f}R."
                )
            elif r_multiple >= 0:
                # Marginal win — better than loss
                exit_text += (
                    f"Marginal win — {r_multiple:.2f}R. "
                    f"Consider reviewing entry timing."
                )
            elif r_multiple >= -0.5:
                # Partial loss — stopped out but didn't lose full R
                exit_text += (
                    f"Partial loss — stopped out at {r_multiple:.2f}R. "
                    f"Stop may have been too tight."
                )
            else:
                # Full stop loss hit
                exit_text += (
                    f"Full stop loss — {r_multiple:.2f}R. "
                    f"Review regime conditions at entry for lessons."
                )

            # Log the exit narrative generation
            logger.info(
                f"[TradeNarrative] Exit narrative generated | "
                f"{outcome_word} | "
                f"R: {r_multiple:.2f} | "
                f"P&L: ${net_pnl:.2f}"
            )

            return exit_text   # Return the exit narrative string

        except Exception as e:
            # Never crash on narrative generation
            logger.error(
                f"[TradeNarrative] Exit generation failed: {e}"
            )
            return f"Exit at {exit_price} | P&L: ${net_pnl:.2f} | R: {r_multiple:.2f}R"


    # ============================================================
    # WRITE ENTRY NARRATIVE — INTERNAL
    # Assembles the full human-readable entry explanation
    # ============================================================
    def _write_entry_narrative(
        self,
        direction,
        symbol,
        strategy,
        reason,
        regime,
        confidence,
        adx,
        atr_ratio,
        ema_spread,
        rsi,
        rsi_zone,
        entry,
        sl,
        tp,
        sl_distance,
        tp_distance,
        rr_ratio,
        conditions,
    ) -> str:
        """
        Writes the full entry narrative paragraph.
        Plain English — readable by any trader reviewing the journal.
        """

        # Get regime description in plain English
        regime_desc = self.regime_descriptions.get(
            regime, f"a {regime.lower()} market"
        )

        # Get strategy description in plain English
        strategy_desc = self.strategy_descriptions.get(
            strategy, strategy
        )

        # Count how many conditions passed
        conditions_passed = sum(1 for v in conditions.values() if v)
        conditions_total  = len(conditions)

        # Build the narrative paragraph
        narrative = (
            # Opening line — what happened
            f"{direction} signal triggered on {symbol} "
            f"at {entry}. "

            # Market context
            f"Market was in {regime_desc} "
            f"with {confidence.lower()} confidence. "

            # Trend strength
            f"ADX was {adx:.1f} "
            f"({'strong trend' if adx >= 25 else 'weak trend' if adx >= 20 else 'no clear trend'}). "

            # Volatility context
            f"Volatility was "
            f"{'elevated' if atr_ratio > 1.5 else 'normal' if atr_ratio <= 1.2 else 'slightly elevated'} "
            f"(ATR ratio: {atr_ratio:.2f}). "

            # EMA structure
            f"EMA spread was {ema_spread:.2f}% "
            f"({'wide — strong trend structure' if ema_spread >= 0.5 else 'narrow — weak trend structure'}). "

            # RSI context
            f"RSI was {rsi:.1f} "
            f"({rsi_zone.lower().replace('_', ' ')}). "

            # Strategy used
            f"Signal was generated by {strategy} "
            f"({strategy_desc}). "
            f"Internal reason code: {reason}. "

            # Trade levels
            f"Entry: {entry} | "
            f"Stop: {sl} | "
            f"Target: {tp} | "
            f"Risk/Reward: 1:{rr_ratio}. "

            # Stop distance context
            f"Stop distance: {sl_distance:.4f} "
            f"({'tight' if sl_distance < 0.01 * entry else 'moderate' if sl_distance < 0.03 * entry else 'wide'}). "

            # Condition summary
            f"{conditions_passed} of {conditions_total} "
            f"conditions confirmed at entry."
        )

        return narrative   # Return the full paragraph


    # ============================================================
    # BUILD CONDITION CHECKLIST — INTERNAL
    # Documents which conditions were present at entry
    # ============================================================
    def _build_condition_checklist(
        self,
        signal: dict,
        direction: str,
        adx: float,
        rsi: float,
        rsi_zone: str,
        regime: str,
        confidence: str,
    ) -> Dict[str, bool]:
        """
        Builds a True/False checklist of entry conditions.
        Stored in TradeRecord for post-trade pattern analysis.
        """

        conditions = {}

        # Regime conditions
        conditions["regime_is_trending"]  = regime in ("TRENDING_UP", "TRENDING_DOWN")
        conditions["regime_is_ranging"]   = regime == "RANGING"
        conditions["confidence_high"]     = confidence == "HIGH"
        conditions["confidence_medium_or_higher"] = confidence in ("HIGH", "MEDIUM")

        # ADX conditions
        conditions["adx_above_20"] = adx >= 20    # Developing trend
        conditions["adx_above_25"] = adx >= 25    # Strong trend (institutional threshold)
        conditions["adx_above_35"] = adx >= 35    # Very strong trend

        # RSI conditions
        conditions["rsi_in_momentum_zone"]  = rsi_zone == "MOMENTUM_ZONE"   # 40-60
        conditions["rsi_not_overbought"]    = rsi < 70                       # Below overbought
        conditions["rsi_not_oversold"]      = rsi > 30                       # Above oversold
        conditions["rsi_buy_optimal"]       = 40 <= rsi <= 55 and direction == "BUY"
        conditions["rsi_sell_optimal"]      = 45 <= rsi <= 65 and direction == "SELL"

        # Direction alignment conditions
        conditions["direction_matches_regime"] = (
            (direction == "BUY"  and regime == "TRENDING_UP")   or
            (direction == "SELL" and regime == "TRENDING_DOWN") or
            (regime == "RANGING")   # Both directions valid in ranging
        )

        # Volatility conditions
        atr_ratio = float(signal.get("atr_ratio", 1.0))
        conditions["volatility_normal"]   = atr_ratio <= 1.5    # Not elevated
        conditions["volatility_not_extreme"] = atr_ratio < 2.0  # Not dangerous

        # EMA structure conditions
        ema_spread = float(signal.get("ema_spread_pct", 0))
        conditions["ema_spread_wide"]   = ema_spread >= 0.5     # Strong trend structure
        conditions["ema_spread_narrow"] = ema_spread < 0.3      # Ranging structure

        return conditions   # Return the complete checklist


    # ============================================================
    # CLASSIFY RSI — INTERNAL
    # Puts the RSI value into a named zone for the narrative
    # ============================================================
    def _classify_rsi(self, rsi: float) -> str:
        """
        Returns the RSI zone name for a given RSI value.
        Used in narrative text and condition checklist.
        """

        # Check each zone range and return the label
        for zone_name, (low, high) in self.rsi_zones.items():
            if low <= rsi < high:
                return zone_name   # e.g. "MOMENTUM_ZONE"

        # Fallback for edge cases (exactly 100 or below 0)
        return "UNKNOWN_ZONE"


    # ============================================================
    # CONVERT TO DICT — PUBLIC UTILITY
    # Converts a TradeNarrative to a dict for database storage
    # ============================================================
    def to_dict(self, narrative: TradeNarrative) -> dict:
        """
        Converts a TradeNarrative dataclass to a plain dict.
        Used by trade_logger.py to store in TradeRecord.
        """

        return {
            # Identity
            "symbol":             narrative.symbol,
            "direction":          narrative.direction,
            "timestamp":          narrative.timestamp,

            # Strategy
            "strategy_name":      narrative.strategy_name,
            "strategy_reason":    narrative.strategy_reason,

            # Regime
            "regime":             narrative.regime,
            "regime_confidence":  narrative.regime_confidence,
            "adx":                narrative.adx,
            "atr_ratio":          narrative.atr_ratio,
            "ema_spread_pct":     narrative.ema_spread_pct,
            "bb_width_pct":       narrative.bb_width_pct,

            # Levels
            "entry":              narrative.entry,
            "sl":                 narrative.sl,
            "tp":                 narrative.tp,
            "rsi":                narrative.rsi,
            "rsi_zone":           narrative.rsi_zone,

            # Geometry
            "rr_ratio":           narrative.rr_ratio,
            "sl_distance":        narrative.sl_distance,
            "tp_distance":        narrative.tp_distance,

            # Conditions checklist
            "conditions":         narrative.conditions,

            # Narrative text
            "entry_narrative":    narrative.entry_narrative,
            "exit_narrative":     narrative.exit_narrative,
        }


    # ============================================================
    # FALLBACK NARRATIVE — INTERNAL
    # Returns a minimal narrative if generation fails
    # Ensures the bot never crashes on a narrative error
    # ============================================================
    def _fallback_narrative(self, signal: dict, symbol: str) -> TradeNarrative:
        """Returns a minimal TradeNarrative when full generation fails."""

        return TradeNarrative(
            symbol            = symbol,
            direction         = signal.get("signal", "UNKNOWN"),
            timestamp         = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            strategy_name     = signal.get("strategy", "unknown"),
            strategy_reason   = signal.get("reason", "unknown"),
            regime            = signal.get("regime", "UNKNOWN"),
            regime_confidence = signal.get("regime_confidence", "UNKNOWN"),
            adx               = float(signal.get("adx", 0)),
            atr_ratio         = float(signal.get("atr_ratio", 1.0)),
            ema_spread_pct    = float(signal.get("ema_spread_pct", 0)),
            bb_width_pct      = float(signal.get("bb_width_pct", 0)),
            entry             = float(signal.get("entry", 0)),
            sl                = float(signal.get("sl", 0)),
            tp                = float(signal.get("tp", 0)),
            rsi               = float(signal.get("rsi", 0)),
            entry_narrative   = "Narrative generation failed — minimal record.",
            exit_narrative    = "",
            rr_ratio          = 0.0,
            sl_distance       = 0.0,
            tp_distance       = 0.0,
            rsi_zone          = "UNKNOWN_ZONE",
            conditions        = {},
        )