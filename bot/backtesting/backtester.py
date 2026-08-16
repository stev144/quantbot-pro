# ============================================================
# bot/backtesting/backtester.py
#
# UPGRADED FROM PREVIOUS VERSION — NOW CLASS-BASED:
# 1. Backtester class added
#    — keeps all state in one place
#    — easier to extend for multi-coin and portfolio runs
#
# 2. Existing function interface preserved
#    — backtest(df, initial_balance=5000) still works
#    — other files depending on backtest() will not break
#
# 3. Structure context preserved
#    — structure, higher_highs, higher_lows, lower_highs, lower_lows
#    — last_swing_high, last_swing_low stored in every trade dict
#
# 4. Rejection analysis improved
#    — rejections are tracked inside the backtester class
#    — reasons are still passed into analytics for compatibility
#
# 5. Equity curve + drawdown curve retained
#    — dashboard chart compatibility preserved
# ============================================================

import logging                 # Gives us the logger — writes messages to terminal and file
import numpy as np             # Numerical library — used for mean, std, sqrt in ratio calculations
import pandas as pd            # DataFrame library — all OHLCV data lives in pandas DataFrames

# ── EXISTING IMPORTS — unchanged ──────────────────────────────

# Trade is the pure Python in-memory trade object used during backtesting
from bot.journal.models import Trade

# TradeAnalytics records every trade and rejection for the summary tables
from bot.engines.analytics_engine import TradeAnalytics

# claude code changed: new — the real position sizer, same one live
# trading uses (architecture audit finding H2, see _open_trade_from_signal)
from bot.risk.position_sizer import PositionSizer

# Three simulation helpers — these make the backtest realistic
from bot.engines.simulation import (
    apply_slippage,           # Adds realistic price difference between signal and actual fill
    calc_fees,                # Calculates the combined entry and exit fees for one trade
    calculate_pnl,            # Calculates gross profit before fees are subtracted
)

# claude code changed: new import — Kraken Multi-Venue Execution, Step 14.
from bot.config.execution_costs import get_venue_execution_costs

# ── REGIME-AWARE IMPORTS — unchanged ──────────────────────────

# RegimeDetector classifies the current market every single candle
from bot.engines.regime_detector import RegimeDetector

# StrategyRouter reads the regime and picks the right strategy for it
from bot.engines.strategy_router import StrategyRouter

# claude code changed: new import — vectorized, backtest-only replacement for calling
# RegimeDetector.detect() once per candle (RegimeDetector itself is untouched)
from bot.backtesting.regime_precomputer import precompute_regime_results

# TradeNarrativeGenerator writes a human-readable explanation for every trade
from bot.engines.trade_narrative import TradeNarrativeGenerator

# ── LOGGING ───────────────────────────────────────────────────
logger = logging.getLogger(__name__)    # Creates a logger named after this file
logging.basicConfig(                    # Configures the logging output format
    level=logging.INFO,                 # Only log INFO level and above
    format="%(asctime)s | %(levelname)s | %(message)s"  # Timestamp | Level | Message
)

# claude code changed: RISK_PER_TRADE module constant removed — the
# Backtester now uses a real PositionSizer instance (see __init__ and
# _open_trade_from_signal below) instead of an inline formula duplicating
# this value; PositionSizer's own risk_pct comes from the single-sourced
# bot.config.risk (architecture audit findings H2 and M3).

# claude code changed: new constant — bounds the per-candle indicator lookback (see run()) so the
# main loop is O(n) instead of O(n^2) on long backtests
# INDICATOR_LOOKBACK_CANDLES: how much trailing history each candle's regime/strategy
# detection actually needs. The longest period anything downstream uses is 50
# (RegimeDetector's ema_slow and atr_avg_period; RegimeDetector.min_candles=60 is its
# own floor). 300 candles gives 6x headroom on the longest EMA/ADX period, which is
# far more than enough for exponential-decay indicators (ewm with alpha=1/50) to be
# numerically indistinguishable from a "full history" seed — well beyond float64
# precision — while StructureAnalyser's O(lookback) swing scan and every rolling()/
# ewm() call downstream now run on a fixed-size slice instead of the whole,
# ever-growing history.
INDICATOR_LOOKBACK_CANDLES = 300


# ============================================================
# BACKTESTER CLASS
# ============================================================
class Backtester:
    """
    Institutional-grade backtester.
    Responsibilities:
    - Iterates over market data
    - Detects regime
    - Routes to strategy
    - Tracks trades
    - Tracks rejection reasons
    - Preserves structure context
    - Produces clean result dict for dashboard/scorer
    """

    def __init__(
        self,
        df: pd.DataFrame,
        initial_balance: float = 10000,
        allow_unvalidated_strategies: bool = False,
        venue_id: str = "binance",   # claude code changed: new param — Kraken Multi-Venue Execution, Step 14
    ):
        # Store the original DataFrame safely so we never mutate the caller's data
        self.original_df = df.copy() if df is not None else pd.DataFrame()

        # The starting account balance for this backtest run
        self.initial_balance = initial_balance
        # The running balance that changes as trades win and lose
        self.balance = initial_balance

        # claude code changed: new — Kraken Multi-Venue Execution, Step 14.
        # Selects which venue's REAL, already-verified cost model (Step 8's
        # get_venue_execution_costs()) is applied when simulating fills
        # against this DataFrame. Does NOT change the price data itself —
        # `df` is whatever the caller fetched (Binance-sourced in this
        # project via bot.data_fetcher) regardless of venue_id. A
        # venue_id="kraken" backtest means "this same Binance price history,
        # simulated with Kraken's real fee schedule" — an execution-cost
        # estimate, not a true historical Kraken backtest (this project
        # never fetches or fabricates Kraken OHLCV history). Default
        # "binance" is proven identical to this class's pre-Step-14
        # bare-constant behavior, since Step 8 confirmed Binance's table
        # row equals the global defaults exactly.
        self.venue_id = venue_id
        _costs = get_venue_execution_costs(venue_id)
        self.fee_rate = _costs["fee_rate"]
        self.slippage_rate = _costs["slippage_rate"]

        # ── SYSTEM COMPONENTS — each instantiated once before the loop ──
        # PositionSizer — claude code changed: new — the real sizer, same
        # class live trading uses, replacing an inline formula that had
        # none of its safeguards (flooring, 5% cap, min-quantity guard).
        # See _open_trade_from_signal() below for the call site.
        self.position_sizer = PositionSizer()
        # RegimeDetector uses ADX, ATR ratio, EMA spread, BB width to classify market
        self.detector = RegimeDetector()
        # StrategyRouter decides which strategy fires based on the detected regime
        self.router = StrategyRouter(
            allow_longs=True,               # Long (buy) trades are permitted
            allow_shorts=True,              # Short (sell) trades are permitted
            require_high_confidence=False,  # Accept MEDIUM confidence regimes too
            min_adx_for_trend=25,           # ADX must be at least 25 for trend trades
            allow_unvalidated_strategies=allow_unvalidated_strategies,   # claude code changed: new — see validated_feature_registry.py
        )
        # TradeNarrativeGenerator writes plain English explanations for each trade
        self.narrator = TradeNarrativeGenerator()
        # TradeAnalytics records every trade and rejection for the summary tables
        self.analytics = TradeAnalytics()
        

        # ── RUNTIME STATE — reset fresh for every new backtest run ──
        self.df = pd.DataFrame()        # Cleaned version of the data — built in _prepare_data
        self.trades = []                # Not used directly but kept for compatibility
        self.closed_trades = []         # Every completed trade dict — the main output list

        # Dictionary counting how many times each rejection reason occurred
        self.rejections = {
            "missing_data": 0,              # DataFrame was missing required columns
            "not_enough_data": 0,           # Not enough candles for indicators to warm up
            "indicator_warmup": 0,          # EMA or RSI returned NaN — still warming up
            "invalid_range": 0,             # Price range was invalid for SL/TP calculation
            "flat_market": 0,               # Market was completely flat — no range at all
            "no_trend": 0,                  # No EMA trend direction detected
            "no_pullback": 0,               # Price was not near EMA20 for entry
            "rsi_out_of_range": 0,          # RSI was outside the acceptable zone
            "no_momentum": 0,               # Last candle did not confirm direction
            "no_setup": 0,                  # No valid setup found this candle
            "no_valid_setup": 0,            # Alias for no_setup — kept for compatibility
            "trend_conflict": 0,            # Signal direction conflicts with 4H trend
            "structure_not_uptrend": 0,     # Structure was not UPTREND for a long entry
            "structure_not_downtrend": 0,   # Structure was not DOWNTREND for a short entry
            "invalid_signal": 0,            # Signal was not BUY or SELL
            "other": 0,                     # Any rejection reason not listed above
        }
        # Detailed list of every single rejection event with full context
        self.rejection_events = []
        # Counter for how many valid BUY/SELL signals were seen during the run
        self.signals_seen = 0

        # ── OPEN TRADE STATE — cleared after every close ──
        self.open_trade = None              # The Trade object currently open — None if flat
        self.open_trade_signal = None       # The signal dict that opened the current trade
        self.open_trade_narrative = None    # The entry narrative for the current trade

        # ── RISK AND PERFORMANCE TRACKING ──
        self.peak_balance = initial_balance  # Highest balance seen — used for drawdown calc
        self.max_drawdown = 0.0              # Largest peak-to-trough percentage seen so far
        self.total_fees = 0.0               # Cumulative fees paid across all trades
        self.total_slippage = 0.0           # Cumulative slippage cost across all trades
        self.consecutive_losses = 0          # Current losing streak length
        self.max_consec_losses = 0           # Worst losing streak seen during the run

        # ── CHART DATA — appended after every trade close ──
        self.equity_curve = [round(initial_balance, 2)]   # Balance over time — for equity chart
        self.drawdown_curve = [0.0]                        # Drawdown % over time — for DD chart


    # ============================================================
    # DATA PREPARATION
    # ============================================================
    def _prepare_data(self):
        """Cleans OHLCV data before running the backtest."""

        # Make a clean copy of the original data to work with
        self.df = self.original_df.copy()

        # Guard — if data is None or empty return immediately
        if self.df is None or self.df.empty:
            self.df = pd.DataFrame()   # Set to empty DataFrame so the loop skips cleanly
            return

        # Force all OHLC price columns to float — removes any string values from the data
        for col in ["open", "close", "high", "low"]:
            if col in self.df.columns:                                          # Only process columns that exist
                self.df[col] = pd.to_numeric(self.df[col], errors="coerce")    # Non-numeric becomes NaN

        # Remove any rows where close, high, or low is NaN — cannot trade on bad candles
        self.df.dropna(subset=["close", "high", "low"], inplace=True)

        # Ensure the index is a DatetimeIndex — RegimeDetector needs this for resampling
        if not isinstance(self.df.index, pd.DatetimeIndex):
            self.df.index = pd.to_datetime(self.df.index, errors="coerce")   # Convert index to datetime

        # Remove any rows where the datetime conversion produced NaT (not a time)
        self.df = self.df[~self.df.index.isna()]


    # ============================================================
    # REJECTION TRACKING
    # ============================================================
    def _record_rejection(self, reason: str, regime_result=None, signal=None):
        """Stores rejection reasons in both class state and analytics."""

        # Clean the reason string — lowercase, stripped of whitespace
        reason = (reason or "unknown").lower().strip()

        # Add to the local rejection counter — creates the key if it does not exist yet
        if reason not in self.rejections:
            self.rejections[reason] = 0    # Initialise new reason counter at zero
        self.rejections[reason] += 1       # Increment the counter for this reason

        # Also send to the analytics engine so it appears in the rejection table
        self.analytics.record_rejection(reason)

        # Make sure signal is always a dict — prevents KeyError on .get() calls below
        signal = signal if isinstance(signal, dict) else {}

        # Store a richer diagnostic event with full context for post-trade analysis
        self.rejection_events.append({
            "reason": reason,   # The specific rejection reason code

            # Regime context at the time of rejection
            "regime": getattr(regime_result, "regime", "UNKNOWN") if regime_result else "UNKNOWN",
            "confidence": getattr(regime_result, "confidence", "UNKNOWN") if regime_result else "UNKNOWN",
            "adx": getattr(regime_result, "adx", 0.0) if regime_result else 0.0,
            "atr_ratio": getattr(regime_result, "atr_ratio", 0.0) if regime_result else 0.0,

            # Structure context from the signal dict
            "structure": signal.get("structure", "UNKNOWN"),   # Market structure at rejection
            "trend": signal.get("trend", "UNKNOWN"),           # Trend direction at rejection

            # Indicator values at the time of rejection
            "rsi": signal.get("rsi", 0),   # RSI value — 0 for structure-based strategies

            # Strategy filter states — safe to access even if missing from signal
            "ema_alignment": signal.get("ema_alignment", None),   # Whether EMA20 was above EMA50
            "pullback": signal.get("pullback", None),              # Whether price was near EMA20
            "momentum": signal.get("momentum", None),             # Whether candle confirmed direction

            # claude code changed: new three keys — Phase 2 (trade provenance).
            # Populated by StrategyRouter._no_signal() only for blocks that
            # came from the production-validation gate (see
            # bot/research/validated_feature_registry.py); empty/False for
            # every other rejection reason, since those weren't decided by a
            # research verdict at all.
            "research_verdict": signal.get("research_verdict", ""),
            "production_eligible": signal.get("production_eligible", False),
            "verdict_rejection_reason": signal.get("verdict_rejection_reason", ""),
        })

    def rejection_summary(self):
        """Returns raw rejection counts."""
        return self.rejections   # Plain dict of reason → count

    def rejection_table(self):
        """Returns cleaned rejection table for UI rendering."""
        total = sum(self.rejections.values()) or 1   # Total rejections — avoid division by zero

        cleaned = {}   # Will hold the formatted table

        # claude code changed: new — Phase 3 (UI pipeline view). One
        # representative event per reason, so the dashboard's Rejection
        # Analysis table can show WHICH verdict blocked a
        # strategy_not_validated_* reason instead of just a bare count.
        # Every event for the same reason carries the same verdict fields
        # (the verdict doesn't vary candle-to-candle), so first-match is
        # sufficient — this is display sampling, not aggregation.
        verdict_by_reason = {}
        for event in self.rejection_events:
            reason_key = event.get("reason", "")
            if reason_key not in verdict_by_reason:
                verdict_by_reason[reason_key] = event

        for k, v in self.rejections.items():
            key = str(k).strip().lower().replace(" ", "_")   # Normalise key to snake_case
            sample = verdict_by_reason.get(key, {})

            cleaned[key] = {
                "count": v,                                        # Raw count for this reason
                "percentage": round((v / total) * 100, 2),        # Percentage of total rejections
                "research_verdict": sample.get("research_verdict", ""),        # claude code changed: new
                "production_eligible": sample.get("production_eligible", False),  # claude code changed: new
            }

        return cleaned   # Return formatted table ready for the dashboard template


    # ============================================================
    # TRADE ENTRY
    # ============================================================
    def _open_trade_from_signal(self, signal: dict, regime_result, i: int):
        """Creates an open Trade object and stores signal context."""

        sig = signal["signal"]               # "BUY" or "SELL"
        entry_p = float(signal["entry"])     # The intended entry price from the strategy
        sl_p = float(signal["sl"])           # The stop loss price level
        tp_p = float(signal["tp"])           # The take profit price level
        rsi_val = float(signal.get("rsi", 0))    # RSI at signal time — 0 for structure signals
        reason = signal.get("reason", "")        # The signal reason code from the strategy

        # Convert signal direction to the Trade object's internal format
        if sig == "BUY":
            direction = "LONG"     # Buy trade = long position
        elif sig == "SELL":
            direction = "SHORT"    # Sell trade = short position
        else:
            return None    # Unexpected signal value — cannot open a trade

        # Apply entry slippage — simulates paying slightly more/less than signal price
        # claude code changed: was apply_slippage(entry_p, direction, True) —
        # Step 14 threads through this run's venue-selected slippage_rate.
        entry = apply_slippage(entry_p, direction, True, slippage_rate=self.slippage_rate)

        # claude code changed: was an inline "risk = balance * risk_per_trade;
        # qty = risk / distance" formula with no flooring, no risk cap, and
        # no minimum-quantity guard — architecture audit finding H2. Now
        # uses the real PositionSizer, same class and logic live trading
        # uses, so backtest position sizes reflect what live trading would
        # actually produce. Sized on the signal's intended entry price
        # (entry_p, not the post-slippage `entry`) — matching live's real
        # sequence: size BEFORE the fill is known, apply slippage to the
        # actual fill afterward.
        qty = self.position_sizer.calculate(
            balance=self.balance,
            entry_price=entry_p,
            stop_price=sl_p,
        )

        # Guard — PositionSizer.calculate() returns 0.0 for any invalid or
        # degenerate input (zero/near-zero balance, zero-distance stop,
        # below its minimum quantity) — its own logging explains which
        if qty <= 0:
            return None    # Skip this trade

        distance = abs(entry - sl_p)   # Distance from the actual (post-slippage) fill to stop, in price units

        # Guard — kept from the original code: covers the (extremely
        # unlikely) case where slippage happens to move the actual fill
        # exactly onto the stop level even though entry_p != sl_p
        if distance == 0:
            return None    # Skip this trade — zero-distance stop is invalid

        risk_amount = distance * qty   # Actual dollar risk at the real fill — used for R-Multiple calculation later

        # Create the Trade object — the backtester's in-memory representation of a live trade
        open_trade = Trade(
            direction=direction,        # "LONG" or "SHORT"
            entry_price=entry,          # Actual fill price after slippage
            sl=sl_p,                    # Stop loss level
            tp=tp_p,                    # Take profit level
            quantity=qty,               # Units to hold
            trend=regime_result.regime, # Regime stored in the trend field for analytics
            rsi=rsi_val,                # RSI at entry — 0 for structure-based signals
            entry_index=i,              # Candle index when trade opened
            risk_amount=risk_amount,    # Dollar risk — used to calculate R-Multiple on close
            reason=reason,              # Why the trade was taken
        )

        # Store the full signal dict — needed to extract structure context when trade closes
        self.open_trade_signal = signal.copy()

        # Add symbol placeholder — narrator requires a symbol field
        signal["symbol"] = "BACKTEST"

        # Generate the entry narrative — documents why this trade was taken in plain English
        self.open_trade_narrative = self.narrator.generate_entry(
            signal=signal,
            symbol="BACKTEST",
        )

        # Log the trade opening for debugging
        logger.debug(
            f"Trade opened | {direction} | "
            f"Regime: {regime_result.regime} ({regime_result.confidence}) | "
            f"ADX: {regime_result.adx:.1f} | "
            f"Strategy: {signal.get('strategy', 'unknown')} | "
            f"Structure: {signal.get('structure', 'UNKNOWN')} | "
            f"Entry: {entry} | SL: {sl_p} | TP: {tp_p}"
        )

        return open_trade   # Return the Trade object — stored as self.open_trade in run()


    # ============================================================
    # TRADE EXIT
    # ============================================================
    def _process_exit(self, i: int, high: float, low: float):
        """Checks SL/TP and closes the open trade if needed."""

        # No open trade — nothing to check
        if not self.open_trade:
            return False

        # Extract the key values we need to check exits
        direction = self.open_trade.direction   # "LONG" or "SHORT"
        sl = self.open_trade.sl                 # Stop loss price level
        tp = self.open_trade.tp                 # Take profit price level

        # ── CHECK EXIT CONDITIONS ───────────────────────────────

        # Stop loss is hit if price falls to SL (long) or rises to SL (short)
        sl_hit = (
            low <= sl if direction == "LONG"    # Long SL: candle low touched the stop
            else high >= sl                     # Short SL: candle high touched the stop
        )

        # Take profit is hit if price rises to TP (long) or falls to TP (short)
        tp_hit = (
            high >= tp if direction == "LONG"   # Long TP: candle high touched the target
            else low <= tp                      # Short TP: candle low touched the target
        )

        # If neither SL nor TP was touched this candle — trade stays open
        if not (sl_hit or tp_hit):
            return False

        # ── DETERMINE EXIT PRICE ──────────────────────────────
        # SL takes priority when both are hit on the same candle — conservative / worst case
        raw_exit = sl if sl_hit else tp                         # Raw price before slippage
        exit_reason = "STOP LOSS" if sl_hit else "TAKE PROFIT"  # Label for the journal

        # ── APPLY SLIPPAGE ─────────────────────────────────────
        # We receive slightly worse price on exit — market impact of our sell/buy order
        # claude code changed: Step 14 — threads through venue-selected slippage_rate.
        exit_price = apply_slippage(raw_exit, direction, False, slippage_rate=self.slippage_rate)

        qty = self.open_trade.quantity      # Units we were holding
        entry = self.open_trade.entry_price  # The price we originally entered at

        # ── PNL CALCULATION ────────────────────────────────────

        # Gross profit is the price move multiplied by quantity — before any costs
        gross = calculate_pnl(direction, entry, exit_price, qty)

        # Exchange fees charged on this round trip (entry + exit)
        # claude code changed: Step 14 — threads through venue-selected fee_rate.
        fees = calc_fees(entry, exit_price, qty, fee_rate=self.fee_rate)
        self.total_fees += fees   # Add to the session running total

        # Slippage cost is the dollar difference between raw exit and actual fill
        slippage = abs(exit_price - raw_exit) * qty
        self.total_slippage += slippage   # Add to the session running total

        # Net profit is what actually hits the account after all costs
        net = gross - fees
        self.balance += net   # Update the running balance immediately

        # ── LOSS STREAK TRACKING ───────────────────────────────

        if net < 0:
            self.consecutive_losses += 1   # Extend the current losing streak
            self.max_consec_losses = max(self.max_consec_losses, self.consecutive_losses)   # Track worst streak
        else:
            self.consecutive_losses = 0    # Any win resets the streak to zero

        # ── DRAWDOWN TRACKING ──────────────────────────────────

        # Peak balance is the highest balance seen at any point during the run
        self.peak_balance = max(self.peak_balance, self.balance)

        # Drawdown is how far below the peak we currently are — expressed as a percentage
        dd = (self.peak_balance - self.balance) / self.peak_balance * 100
        self.max_drawdown = max(self.max_drawdown, dd)   # Track the worst drawdown seen

        # Append current balance and drawdown to the chart series
        self.equity_curve.append(round(self.balance, 2))    # Balance after this trade
        self.drawdown_curve.append(round(dd, 2))             # Drawdown % after this trade

        # ── CLOSE TRADE OBJECT ─────────────────────────────────

        # Formally close the Trade object — this calculates r_multiple and holding_candles
        self.open_trade.close(
            exit_price=exit_price,   # Actual fill price on exit
            reason=exit_reason,      # "STOP LOSS" or "TAKE PROFIT"
            exit_index=i,            # Candle index when trade closed
        )

        # Convert the Trade object into a plain dict for storage and dashboard display
        trade_dict = self.open_trade.to_dict()

        # Preserve the gross profit (before fees) under its own key
        trade_dict["gross_profit"] = trade_dict.get("profit", 0)

        # Replace the profit field with net profit — this is what the dashboard shows
        trade_dict["profit"] = round(net, 4)
        trade_dict["net_pnl"] = round(net, 4)   # Alias — some dashboard fields use this key

        # ── ATTACH NARRATIVE CONTEXT ───────────────────────────

        if self.open_trade_narrative:
            # Generate the exit narrative — documents how and why the trade ended
            exit_text = self.narrator.generate_exit(
                narrative=self.open_trade_narrative,
                exit_price=exit_price,
                exit_reason=exit_reason.lower().replace(" ", "_"),   # Norma    lise to snake_case
                net_pnl=net,
                r_multiple=trade_dict.get("r_multiple", 0),
                holding_candles=trade_dict.get("holding_candles", 0),
            )

            # Store both narratives in the trade dict for the journal tab
            trade_dict["entry_narrative"] = self.open_trade_narrative.entry_narrative
            trade_dict["exit_narrative"] = exit_text

            # Store regime context from the narrative object
            trade_dict["regime"] = self.open_trade_narrative.regime
            trade_dict["regime_confidence"] = self.open_trade_narrative.regime_confidence
            trade_dict["adx"] = self.open_trade_narrative.adx
            trade_dict["atr_ratio"] = self.open_trade_narrative.atr_ratio
            trade_dict["strategy_name"] = self.open_trade_narrative.strategy_name
            trade_dict["rsi_zone"] = self.open_trade_narrative.rsi_zone
            trade_dict["rr_ratio"] = self.open_trade_narrative.rr_ratio
            trade_dict["conditions"] = self.open_trade_narrative.conditions
            trade_dict["research_verdict"] = self.open_trade_narrative.research_verdict          # claude code changed: new — Phase 3 (UI pipeline view)
            trade_dict["production_eligible"] = self.open_trade_narrative.production_eligible    # claude code changed: new

        # ── ATTACH STRUCTURE CONTEXT ────────────────────────────

        structure_value = "UNKNOWN"   # Default if structure cannot be extracted

        # Try to get structure from the stored signal dict
        if self.open_trade_signal and isinstance(self.open_trade_signal, dict):
            structure_value = self.open_trade_signal.get("structure", "UNKNOWN")

        # Normalise structure to uppercase — removes any case inconsistencies
        structure_value = str(structure_value or "UNKNOWN").upper().strip()

        # Replace any blank or null-like values with UNKNOWN
        if structure_value in ("", "NONE", "NAN"):
            structure_value = "UNKNOWN"

        # If structure is still UNKNOWN but the regime was RANGING, remap it
        # This handles mean reversion trades that did not set a structure field
        regime_from_trade = getattr(self.open_trade, "trend", None)
        if structure_value == "UNKNOWN" and str(regime_from_trade).upper() == "RANGING":
            structure_value = "RANGING"    # Mean reversion trades belong in the RANGING bucket

        # Store the final normalised structure value in the trade dict
        trade_dict["structure"] = structure_value

        # Store detailed structure swing metadata from the signal dict
        if self.open_trade_signal and isinstance(self.open_trade_signal, dict):
            trade_dict["higher_highs"] = self.open_trade_signal.get("higher_highs", False)    # Was last SH above previous SH
            trade_dict["higher_lows"] = self.open_trade_signal.get("higher_lows", False)      # Was last SL above previous SL
            trade_dict["lower_highs"] = self.open_trade_signal.get("lower_highs", False)      # Was last SH below previous SH
            trade_dict["lower_lows"] = self.open_trade_signal.get("lower_lows", False)        # Was last SL below previous SL
            trade_dict["last_swing_high"] = self.open_trade_signal.get("last_swing_high", 0.0)   # Price of most recent swing high
            trade_dict["last_swing_low"] = self.open_trade_signal.get("last_swing_low", 0.0)     # Price of most recent swing low
            trade_dict["swing_high_count"] = self.open_trade_signal.get("swing_high_count", 0)   # How many swing highs were found
            trade_dict["swing_low_count"] = self.open_trade_signal.get("swing_low_count", 0)     # How many swing lows were found

        # ── STORE TRADE ────────────────────────────────────────

        # Add the completed trade dict to the closed trades list
        self.closed_trades.append(trade_dict)

        # Record the trade in the analytics engine for summary calculations
        self.analytics.record_trade({
            "pnl": net,                                         # Net P&L for analytics
            "profit": net,                                      # Alias used by some analytics methods
            "rsi": self.open_trade.rsi,                        # RSI at entry — 0 for structure trades
            "direction": direction,                             # "LONG" or "SHORT"
            "reason": exit_reason,                             # "STOP LOSS" or "TAKE PROFIT"
            "structure": trade_dict.get("structure", "UNKNOWN"),  # Structure at entry
        })

        # Debug log for the terminal
        logger.debug(
            f"Trade closed | {direction} | "
            f"PnL: ${round(net, 2)} | "
            f"{exit_reason} | "
            f"R: {trade_dict.get('r_multiple', 0):.2f}R | "
            f"Structure: {trade_dict.get('structure', 'UNKNOWN')}"
        )

        # ── RESET STATE ────────────────────────────────────────

        self.open_trade = None               # No trade is currently open
        self.open_trade_signal = None        # Clear stored signal context
        self.open_trade_narrative = None     # Clear stored narrative

        return True   # Signal to the main loop that a trade was closed this candle


    # ============================================================
    # FORCE CLOSE
    # ============================================================
    def _force_close(self):
        """Closes any position still open at the end of data."""

        # Guard — if no trade is open there is nothing to force close
        if not self.open_trade:
            return

        # Exit at the very last candle's closing price
        final_price = float(self.df["close"].iloc[-1])

        # Apply exit slippage to the final price — simulates realistic fill
        # claude code changed: Step 14 — threads through venue-selected slippage_rate.
        final_exit = apply_slippage(
            final_price, self.open_trade.direction, is_entry=False,
            slippage_rate=self.slippage_rate,
        )

        qty = self.open_trade.quantity       # Units we were holding
        entry = self.open_trade.entry_price  # Our original entry price

        # Calculate gross profit at the forced close price
        gross = calculate_pnl(self.open_trade.direction, entry, final_exit, qty)

        # Exchange fees for this final round trip
        # claude code changed: Step 14 — threads through venue-selected fee_rate.
        fees = calc_fees(entry, final_exit, qty, fee_rate=self.fee_rate)
        self.total_fees += fees   # Add to session total

        # Slippage cost for the force close
        slippage = abs(final_exit - final_price) * qty
        self.total_slippage += slippage   # Add to session total

        # Net profit after fees
        net = gross - fees
        self.balance += net   # Update running balance

        # Update loss streak after force close
        if net < 0:
            self.consecutive_losses += 1   # Extend streak
            self.max_consec_losses = max(self.max_consec_losses, self.consecutive_losses)
        else:
            self.consecutive_losses = 0    # Reset streak on any positive result

        # Update peak balance and drawdown after force close
        self.peak_balance = max(self.peak_balance, self.balance)
        dd = (self.peak_balance - self.balance) / self.peak_balance * 100
        self.max_drawdown = max(self.max_drawdown, dd)

        # Append force close point to chart series
        self.equity_curve.append(round(self.balance, 2))
        self.drawdown_curve.append(round(dd, 2))

        # Formally close the Trade object so r_multiple and holding_candles are calculated
        self.open_trade.close(
            exit_price=final_exit,
            reason="FORCE CLOSE",
            exit_index=len(self.df) - 1,   # Last candle index
        )

        # Convert Trade to dict for storage
        trade_dict = self.open_trade.to_dict()

        # Preserve gross profit before replacing with net
        trade_dict["gross_profit"] = trade_dict.get("profit", 0)
        # Replace profit with net P&L — this is what the dashboard shows
        trade_dict["profit"] = round(net, 4)
        trade_dict["net_pnl"] = round(net, 4)

        # Generate force close narrative if entry narrative exists
        if self.open_trade_narrative:
            exit_text = self.narrator.generate_exit(
                narrative=self.open_trade_narrative,
                exit_price=final_exit,
                exit_reason="force_close",    # Special reason code for data end close
                net_pnl=net,
                r_multiple=trade_dict.get("r_multiple", 0),
                holding_candles=trade_dict.get("holding_candles", 0),
            )
            # Attach narratives to trade dict
            trade_dict["entry_narrative"] = self.open_trade_narrative.entry_narrative
            trade_dict["exit_narrative"] = exit_text
            trade_dict["regime"] = self.open_trade_narrative.regime
            trade_dict["regime_confidence"] = self.open_trade_narrative.regime_confidence
            trade_dict["adx"] = self.open_trade_narrative.adx
            trade_dict["strategy_name"] = self.open_trade_narrative.strategy_name
            trade_dict["research_verdict"] = self.open_trade_narrative.research_verdict          # claude code changed: new — Phase 3 (UI pipeline view)
            trade_dict["production_eligible"] = self.open_trade_narrative.production_eligible    # claude code changed: new

        # Attach structure context from stored signal
        if self.open_trade_signal:
            trade_dict["structure"] = self.open_trade_signal.get("structure", "UNKNOWN")   # Structure at entry
            trade_dict["higher_highs"] = self.open_trade_signal.get("higher_highs", False)
            trade_dict["higher_lows"] = self.open_trade_signal.get("higher_lows", False)
            trade_dict["lower_highs"] = self.open_trade_signal.get("lower_highs", False)
            trade_dict["lower_lows"] = self.open_trade_signal.get("lower_lows", False)
            trade_dict["last_swing_high"] = self.open_trade_signal.get("last_swing_high", 0.0)
            trade_dict["last_swing_low"] = self.open_trade_signal.get("last_swing_low", 0.0)
            trade_dict["swing_high_count"] = self.open_trade_signal.get("swing_high_count", 0)
            trade_dict["swing_low_count"] = self.open_trade_signal.get("swing_low_count", 0)

        # Add the force-closed trade to the closed trades list
        self.closed_trades.append(trade_dict)

        # Record in analytics engine
        self.analytics.record_trade({
            "pnl": net,
            "profit": net,
            "rsi": self.open_trade.rsi,
            "direction": self.open_trade.direction,
            "reason": "FORCE CLOSE",
            "structure": trade_dict.get("structure", "UNKNOWN"),
        })

        logger.info(f"Force closed | PnL: ${round(net, 2)}")   # Log to terminal

        # Clear all open trade state — backtest is now fully complete
        self.open_trade = None
        self.open_trade_signal = None
        self.open_trade_narrative = None


    # ============================================================
    # RESULT BUILDERS
    # ============================================================
    def _empty_results(self):
        """Returns a safe empty result dict when data is insufficient."""

        return {
            "trades": [],                                       # No trades taken
            "final_balance": round(self.initial_balance, 2),  # Balance unchanged
            # claude code changed: new — Kraken Multi-Venue Execution, Step 14.
            # See Backtester.__init__'s venue_id docstring: this only tags
            # which cost model was applied, never which price data was used.
            "venue_id": self.venue_id,
            "fee_rate": self.fee_rate,
            "slippage_rate": self.slippage_rate,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "expectancy": 0,
            "expectancy_r": 0,
            "profit_factor": 0,
            "avg_r_multiple": 0,
            "sharpe_ratio": 0,
            "sortino_ratio": 0,
            "max_drawdown": 0,
            "max_consecutive_losses": 0,
            "total_fees_paid": 0,
            "total_slippage_cost": 0,
            "total_execution_cost": 0,
            "analysis": {
                "rsi_analysis": {},
                "regime_breakdown": {},
                "structure_breakdown": {},
            },
            "rsi_analysis": {},
            "analytics_summary": {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0,
                "total_pnl": 0,
            },
            "rejection_table": self.rejection_table(),   # Still return rejection counts
            "rejections": self.rejection_table(),
            "regime_breakdown": {},
            "route_stats": {},
            "structure_breakdown": {},
            "equity_curve": [round(self.initial_balance, 2)],   # Single point — starting balance
            "drawdown_curve": [0.0],                             # Single point — zero drawdown
            "signals_seen": 0,
            "execution_rate": 0,
            "rejection_events": [],
        }

    def _build_results(self):
        """Builds the final backtest result dict used by dashboard/scorer."""

        total = len(self.closed_trades)   # Total number of completed trades
        # Count trades where net profit was positive
        wins = sum(1 for t in self.closed_trades if t.get("profit", 0) > 0)
        losses = total - wins   # Everything that was not a win is a loss
        # Win rate as a percentage — zero if no trades
        win_rate = round((wins / total) * 100, 2) if total else 0

        # Build a list of every trade's profit for aggregate calculations
        profits = [t.get("profit", 0) for t in self.closed_trades]

        # Expectancy = average net profit per trade
        expectancy = round(sum(profits) / len(profits), 2) if profits else 0

        # Collect all R-Multiple values — skip trades where r_multiple is None
        r_values = [
            t.get("r_multiple", 0) for t in self.closed_trades
            if t.get("r_multiple") is not None
        ]
        # Average R across all trades — positive means strategy has edge
        avg_r = round(sum(r_values) / len(r_values), 4) if r_values else 0

        # Gross profit = sum of all winning trade profits
        gross_profit = sum(p for p in profits if p > 0)
        # Gross loss = absolute sum of all losing trade losses
        gross_loss = abs(sum(p for p in profits if p < 0))
        # Profit factor = gross wins divided by gross losses — above 1.5 is the target
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else 0

        max_drawdown = round(self.max_drawdown, 2)             # Round drawdown to 2 decimal places

        # claude code changed: new — real wall-clock span of the backtest,
        # used to annualise Sharpe/Sortino by the strategy's ACTUAL observed
        # trade frequency instead of a fixed 252 (see calculate_sharpe_ratio's
        # docstring, architecture audit finding H4). self.df is guaranteed
        # non-empty here — run() already returned _empty_results() earlier
        # if it wasn't.
        years_elapsed = (self.df.index[-1] - self.df.index[0]).days / 365.25

        sharpe = calculate_sharpe_ratio(self.closed_trades, years_elapsed=years_elapsed)    # Risk-adjusted return metric
        sortino = calculate_sortino_ratio(self.closed_trades, years_elapsed=years_elapsed)  # Downside-only risk metric

        # Get analytics engine summaries and rejection data
        summary = self.analytics.summary()
        rejection_table = self.rejection_table()
        rejection_insights = analyze_rejections(rejection_table)   # Plain English rejection analysis

        # RSI breakdown — useful for mean reversion trades, shows 0 for structure-based
        rsi_analysis = (
            self.analytics.rsi_breakdown()
            if hasattr(self.analytics, "rsi_breakdown")
            else {}
        )

        # Group closed trades by regime for the regime performance table
        regime_breakdown = analyze_regime_breakdown(self.closed_trades)

        # Group closed trades by structure for the structure performance table
        structure_breakdown = analyze_structure_breakdown(self.closed_trades)

        # Get route stats from the router — shows how many candles were each regime type
        route_stats = self.router.get_route_stats() if hasattr(self.router, "get_route_stats") else {}

        # ── LOG FINAL SUMMARY TO TERMINAL ──────────────────────

        logger.info("=" * 55)
        logger.info("BACKTEST COMPLETE — REGIME + STRUCTURE AWARE")
        logger.info(f"Balance       : ${round(self.balance, 2)}")
        logger.info(f"Trades        : {total}")
        logger.info(f"Wins          : {wins}")
        logger.info(f"Losses        : {losses}")
        logger.info(f"Win Rate      : {win_rate}%")
        logger.info(f"Expectancy    : ${expectancy}")
        logger.info(f"Profit Factor : {profit_factor}")
        logger.info(f"Avg R         : {avg_r}R")
        logger.info(f"Sharpe        : {sharpe}")
        logger.info(f"Sortino       : {sortino}")
        logger.info(f"Max Drawdown  : {max_drawdown}%")
        logger.info(f"Fees Paid     : ${round(self.total_fees, 2)}")
        logger.info(f"Slippage Cost : ${round(self.total_slippage, 2)}")
        logger.info(f"Rejections so far: {self.rejections}")   # Print all rejection counts
        # Log regime breakdown table
        logger.info("── Regime Breakdown ──────────────────────────")
        for regime, data in regime_breakdown.items():
            logger.info(
                f"  {regime:20s} | "
                f"Tra]des: {data['trades']:3d} | "   # Note: "Tra]des" typo preserved from original
                f"WR: {data['win_rate']:5.1f}% | "
                f"P&L: ${data['total_profit']}"
            )

        # Log structure breakdown table
        logger.info("── Structure Breakdown ───────────────────────")
        for structure, data in structure_breakdown.items():
            logger.info(
                f"  {structure:15s} | "
                f"Trades: {data['trades']:3d} | "
                f"WR: {data['win_rate']:5.1f}% | "
                f"P&L: ${data['total_profit']}"
            )

        logger.info("=" * 55)

        # Return the complete results dict — every key used by the dashboard is here
        return {
            "trades": self.closed_trades,   # Full list of trade dicts with all context

            # claude code changed: new — Kraken Multi-Venue Execution, Step 14.
            # Tags which cost model was applied — self.original_df's price
            # data is never venue-swapped, only the fee/slippage assumption.
            "venue_id": self.venue_id,
            "fee_rate": self.fee_rate,
            "slippage_rate": self.slippage_rate,

            # ── Core metrics — dashboard key names must not change ──
            "final_balance": round(self.balance, 2),   # Ending account balance
            "total_trades": total,                     # Number of completed trades
            "wins": wins,                              # Number of winning trades
            "losses": losses,                          # Number of losing trades
            "win_rate": win_rate,                      # Win rate percentage

            # ── Performance metrics ──
            "expectancy": expectancy,          # Average net profit per trade in dollars
            "expectancy_r": avg_r,             # Average R-Multiple per trade
            "profit_factor": profit_factor,    # Gross wins divided by gross losses
            "avg_r_multiple": avg_r,           # Alias for expectancy_r — used by scorer
            "sharpe_ratio": sharpe,            # Return per unit of total risk
            "sortino_ratio": sortino,          # Return per unit of downside risk only

            # ── Risk metrics ──
            "max_drawdown": max_drawdown,                      # Worst peak-to-trough percentage
            "max_consecutive_losses": self.max_consec_losses,  # Longest losing streak

            # ── Execution costs ──
            "total_fees_paid": round(self.total_fees, 2),                              # Total fees in dollars
            "total_slippage_cost": round(self.total_slippage, 2),                      # Total slippage in dollars
            "total_execution_cost": round(self.total_fees + self.total_slippage, 2),   # Combined execution cost

            # ── Analysis tables — nested and flat versions for compatibility ──
            "analysis": {
                "rsi_analysis": rsi_analysis,
                "regime_breakdown": regime_breakdown,
                "structure_breakdown": structure_breakdown,
            },
            "rsi_analysis": rsi_analysis,              # Flat alias for template access
            "analytics_summary": summary,              # Summary from analytics engine
            "rejection_table": rejection_table,        # Formatted rejection counts
            "rejections": rejection_table,             # Alias for rejection_table
            "rejection_insights": rejection_insights,  # Plain English rejection analysis

            # ── Regime and structure intelligence ──
            "regime_breakdown": regime_breakdown,      # Performance per regime
            "route_stats": route_stats,                # Candle classification counts from router
            "structure_breakdown": structure_breakdown, # Performance per structure type

            # ── Chart data ──
            "equity_curve": self.equity_curve,     # Balance at each trade close — for equity chart
            "drawdown_curve": self.drawdown_curve, # Drawdown % at each trade close — for DD chart

            # ── Execution diagnostics ──
            "signals_seen": self.signals_seen,   # How many valid signals were seen
            # Execution rate = trades taken as % of signals seen
            "execution_rate": round((total / self.signals_seen) * 100, 2) if self.signals_seen else 0,
            "rejection_events": self.rejection_events,   # Full list of rich rejection events
        }


    # ============================================================
    # MAIN RUN
    # ============================================================
    def run(self):
        """
        Executes the full backtest.

        Flow per candle:
        1. RegimeDetector classifies the market
        2. StrategyRouter selects correct strategy per regime
        3. Strategy returns structure-aware signal dict
        4. Trade is opened with risk sizing and slippage
        5. Exit checked every candle — SL or TP triggers close
        6. Trade context stored in every closed trade dict
        """

        # Clean and validate the data before doing anything else
        self._prepare_data()

        # Guard — if data is empty or too short, return safe empty results
        if self.df.empty or len(self.df) < 100:
            return self._empty_results()

        # claude code changed: new — one vectorized pass instead of one RegimeDetector.detect()
        # call per candle. RegimeDetector itself is untouched; see regime_precomputer.py's
        # module docstring for why this is exact, not an approximation, and verification notes.
        precomputed_regimes = precompute_regime_results(self.df, self.detector)

        # ── MAIN CANDLE LOOP ──────────────────────────────────────
        # Start at candle 100 — strategy requires 100 candles minimum for warmup
        for i in range(100, len(self.df)):

            # current_df is the slice of data visible at this candle
            # Prevents look-ahead bias — strategy cannot see future candles
            # claude code changed: was self.df.iloc[:i + 1] — an UNBOUNDED, ever-growing slice.
            # Every indicator downstream (RegimeDetector, StrategyRouter's strategies,
            # StructureAnalyser) only needs the last INDICATOR_LOOKBACK_CANDLES candles
            # (longest actual period used anywhere is 50) — recomputing over the full
            # history from candle 0 every single iteration made this loop O(n^2), which
            # is why a 10,000-candle backtest was taking 5+ minutes. Bounding the window
            # doesn't change look-ahead safety (still only sees up to candle i) and
            # doesn't change indicator OUTPUT (300 candles is 6x the longest period, so
            # ewm/rolling values are numerically identical to the full-history version
            # well beyond float64 precision) — it only changes how much irrelevant past
            # history gets re-scanned every candle.
            window_start = max(0, i + 1 - INDICATOR_LOOKBACK_CANDLES)   # claude code changed: new
            current_df = self.df.iloc[window_start:i + 1]               # claude code changed: was self.df.iloc[:i + 1]

            # Extract high and low of current candle for SL/TP checking
            candle = self.df.iloc[i]
            high = float(candle["high"])   # Candle high — used to check TP for longs
            low = float(candle["low"])     # Candle low — used to check SL for longs

            # ── EXIT LOGIC — always checked before entry ──────────
            if self.open_trade:
                closed = self._process_exit(i, high, low)   # Returns True if trade was closed
                if closed:
                    continue   # Move to next candle — do not look for entries on close candle

            # ── ENTRY LOGIC — only when no trade is currently open ──
            if not self.open_trade:

                # Step 1: Look up this candle's precomputed regime result
                # claude code changed: was self.detector.detect(current_df) — RegimeDetector
                # itself is unchanged, this is just an O(1) lookup into the vectorized
                # precompute done once above, instead of recomputing ADX/ATR/EMA/BB from
                # scratch on every candle.
                regime_result = precomputed_regimes[i]

                # Step 2: Route to the correct strategy based on regime
                signal = self.router.route(current_df, regime_result)

                # Guard — router must always return a dict
                if not isinstance(signal, dict):
                    self._record_rejection("invalid_signal", regime_result, {})
                    continue

                # Skip if no valid signal was produced this candle
                if signal.get("signal") == "NO_SIGNAL":
                    self._record_rejection(
                        signal.get("reason", "unknown"),   # Record the specific rejection reason
                        regime_result,
                        signal,
                    )
                    continue

                # Skip if signal is neither BUY nor SELL
                if signal.get("signal") not in ("BUY", "SELL"):
                    self._record_rejection("invalid_signal", regime_result, signal)
                    continue

                # Count this as a valid signal seen
                self.signals_seen += 1

                # Step 3: Open the trade
                self.open_trade = self._open_trade_from_signal(signal, regime_result, i)

                # Guard — if trade creation failed skip to next candle
                if self.open_trade is None:
                    continue

        # ── FORCE CLOSE — end of data ─────────────────────────────
        # If the data ended with a trade still open, close it at the last price
        self._force_close()

        # Ensure the chart series has at least two points if any trades were taken
        if len(self.equity_curve) == 1 and self.closed_trades:
            self.equity_curve.append(round(self.balance, 2))    # Add final balance point
            self.drawdown_curve.append(round(self.max_drawdown, 2))   # Add final drawdown point

        # Build and return the complete results dict
        return self._build_results()


# ============================================================
# RSI ANALYSIS GROUPING — UNCHANGED
# ============================================================
def analyze_trade_groups(trades: list) -> dict:
    """Groups trades by RSI zone for dashboard RSI analysis table."""

    if not trades:
        return {}   # Nothing to analyse — return empty dict

    df = pd.DataFrame(trades)   # Convert list of trade dicts to DataFrame

    # Drop rows where RSI, profit, or direction is missing
    df = df.dropna(subset=["rsi", "profit", "direction"])

    if df.empty:
        return {}   # All rows were dropped — nothing to group

    # Bin RSI values into named zones for grouping
    df["rsi_zone"] = pd.cut(
        df["rsi"],
        bins=[0, 30, 40, 50, 60, 70, 100],
        labels=["0-30", "30-40", "40-50", "50-60", "60-70", "70+"]
    )

    analysis = {}   # Will hold results per RSI zone

    for zone, data in df.groupby("rsi_zone"):
        total = len(data)                            # Total trades in this RSI zone
        wins = len(data[data["profit"] > 0])         # Winning trades in this zone
        losses = len(data[data["profit"] <= 0])      # Losing trades in this zone
        long_data = data[data["direction"] == "LONG"]    # Long trades only
        short_data = data[data["direction"] == "SHORT"]  # Short trades only

        analysis[str(zone)] = {
            "trades": int(total),
            "wins": int(wins),
            "losses": int(losses),
            "win_rate": round((wins / total) * 100, 2) if total else 0,
            "avg_profit": round(data["profit"].mean(), 4),
            "long_trades": len(long_data),
            "short_trades": len(short_data),
            "long_win_rate": round(
                len(long_data[long_data["profit"] > 0]) / len(long_data) * 100, 2
            ) if len(long_data) else 0,
            "short_win_rate": round(
                len(short_data[short_data["profit"] > 0]) / len(short_data) * 100, 2
            ) if len(short_data) else 0,
        }

    return {"rsi_analysis": analysis}   # Wrapped in key for dashboard compatibility


# ============================================================
# SHARPE RATIO
# claude code changed: fixes architecture audit finding H4. Previously
# this used each trade's raw DOLLAR profit as if it were a periodic
# return, and annualised with a fixed periods_per_year=252 (a stock
# trading-days assumption) regardless of how many trades actually
# occurred or over what span — dimensionally unsound (subtracting an
# annual rate fraction from a dollar figure) and disconnected from the
# strategy's real trading frequency (a strategy taking 40 trades over
# 5 years is not "252 periods/year"). Now uses each trade's r_multiple
# (profit normalised by risk taken — already dimensionless, the
# standard unit for this comparison in systematic trading, equivalent
# to Van Tharp's System Quality Number) and annualises using the
# backtest's ACTUAL observed trade frequency instead of a fixed constant.
# ============================================================
def calculate_sharpe_ratio(
    trades: list,
    years_elapsed: float = None,     # Wall-clock span of the backtest, in years — see docstring
    risk_free_rate: float = 0.05,    # Annual risk-free rate — US Treasury approximation
) -> float:
    """
    Risk-adjusted return per unit of R-multiple dispersion. Above 1.0 is good.

    years_elapsed: wall-clock span the trades were taken over (e.g. the
    full backtest DataFrame's first-to-last timestamp, in years). If None
    or non-positive, the ratio is reported UNANNUALISED (raw per-trade
    risk-adjusted return) rather than silently applying a wrong assumption
    about trading frequency.
    """

    if len(trades) < 2:
        return 0.0   # Cannot calculate a ratio from fewer than 2 trades

    # r_multiple is already risk-normalised (profit / dollar risk taken),
    # so it's comparable across trades regardless of position size —
    # unlike raw dollar profit, which isn't a "return" in any comparable unit
    r_values = [t.get("r_multiple", 0) or 0 for t in trades]
    avg_r = np.mean(r_values)                  # Average R across all trades
    std_r = np.std(r_values, ddof=1)           # Standard deviation — ddof=1 for sample std

    if std_r == 0:
        return 0.0   # All trades had identical R — no variance to measure

    if years_elapsed and years_elapsed > 0:
        trades_per_year = len(trades) / years_elapsed
        rf_per_trade = risk_free_rate / trades_per_year   # risk-free hurdle, per trade, at this trading frequency
        annualisation = np.sqrt(trades_per_year)
    else:
        # No timeframe given — report unannualised rather than guess
        rf_per_trade = 0.0
        annualisation = 1.0

    return round(float(
        (avg_r - rf_per_trade) / std_r * annualisation
    ), 4)


# ============================================================
# SORTINO RATIO
# claude code changed: same fixes as calculate_sharpe_ratio above — see
# its docstring/comments for the full rationale. Downside deviation is
# now computed on r_multiple, not raw dollar profit.
# ============================================================
def calculate_sortino_ratio(
    trades: list,
    years_elapsed: float = None,     # Wall-clock span of the backtest, in years — see calculate_sharpe_ratio
    risk_free_rate: float = 0.05,    # Annual risk-free rate
) -> float:
    """Return per unit of downside risk only. More relevant than Sharpe for trading."""

    if len(trades) < 2:
        return 0.0   # Cannot calculate from fewer than 2 trades

    r_values = [t.get("r_multiple", 0) or 0 for t in trades]   # All trade R-multiples
    avg_r = np.mean(r_values)                                  # Average R
    downside = [r for r in r_values if r < 0]                 # Only negative R — ignores wins

    if not downside:
        return 999.0   # No losing trades at all — perfect downside protection

    if len(downside) < 2:
        # claude code changed: new guard — np.std(ddof=1) on a single value
        # divides by zero and silently returns NaN (no exception), which
        # would have propagated into the dashboard as a NaN Sortino with
        # no indication anything was wrong. Two losses minimum needed for
        # a sample standard deviation to mean anything.
        return 0.0

    down_std = np.std(downside, ddof=1)   # Standard deviation of losses only

    if down_std == 0:
        return 0.0   # All losses were identical — cannot calculate

    if years_elapsed and years_elapsed > 0:
        trades_per_year = len(trades) / years_elapsed
        rf_per_trade = risk_free_rate / trades_per_year
        annualisation = np.sqrt(trades_per_year)
    else:
        rf_per_trade = 0.0
        annualisation = 1.0

    return round(float(
        (avg_r - rf_per_trade) / down_std * annualisation
    ), 4)


# ============================================================
# REGIME BREAKDOWN
# ============================================================
def analyze_regime_breakdown(trades: list) -> dict:
    """Groups closed trades by regime. Shows where the edge lives."""

    if not trades:
        return {}   # Nothing to analyse

    # Initialise counters for all four possible regimes plus UNKNOWN
    regimes = {
        "TRENDING_UP": {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0},
        "TRENDING_DOWN": {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0},
        "RANGING": {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0},
        "HIGH_VOLATILITY": {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0},
        "UNKNOWN": {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0},
    }

    for trade in trades:
        regime = trade.get("regime", "UNKNOWN")   # Get regime from the trade dict

        if regime not in regimes:
            regime = "UNKNOWN"   # Handle any unexpected regime values safely

        profit = trade.get("profit", 0)   # Get the trade profit

        regimes[regime]["trades"] += 1        # Count this trade
        regimes[regime]["profit"] += profit   # Add to total profit for this regime

        if profit > 0:
            regimes[regime]["wins"] += 1    # Winning trade
        else:
            regimes[regime]["losses"] += 1   # Losing trade

    result = {}   # Build the output dict — skip regimes with no trades

    for regime, data in regimes.items():
        total = data["trades"]

        if total == 0:
            continue   # Skip unused regimes — keeps output clean

        result[regime] = {
            "trades": total,
            "wins": data["wins"],
            "losses": data["losses"],
            "win_rate": round((data["wins"] / total) * 100, 2),   # Win rate as percentage
            "total_profit": round(data["profit"], 2),              # Total P&L for this regime
            "avg_profit": round(data["profit"] / total, 2),        # Average P&L per trade
        }

    return result   # Dict keyed by regime name


# ============================================================
# STRUCTURE BREAKDOWN
# ============================================================
def analyze_structure_breakdown(trades: list) -> dict:
    """
    Groups closed trades by market structure at entry time.
    Reveals whether HH+HL entries outperform LH+LL entries.
    """

    if not trades:
        return {}   # Nothing to analyse

    # Initialise counters for all possible structure classifications
    structures = {
        "UPTREND": {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0},      # HH + HL confirmed
        "DOWNTREND": {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0},    # LH + LL confirmed
        "EXPANDING": {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0},    # HH + LL — widening range
        "CONTRACTING": {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0},  # LH + HL — coiling range
        "UNCLEAR": {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0},      # Mixed swing signals
        "RANGING": {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0},      # Mean reversion regime
        "UNKNOWN": {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0},      # Structure not available
    }

    for trade in trades:
        # Get structure from the trade dict — normalise to uppercase
        structure = trade.get("structure", "UNKNOWN").upper().strip()

        if structure not in structures:  
            structure = "RANGING"   # Default unmapped structures to RANGING

        profit = trade.get("profit", 0)   # Trade profit

        structures[structure]["trades"] += 1        # Count trade
        structures[structure]["profit"] += profit   # Accumulate profit

        if profit > 0:
            structures[structure]["wins"] += 1    # Win
        else:
            structures[structure]["losses"] += 1   # Loss

    result = {}   # Build output dict

    for structure, data in structures.items():
        total = data["trades"]

        if total == 0:
            continue   # Skip empty structure types

        result[structure] = {
            "trades": total,
            "wins": data["wins"],
            "losses": data["losses"],
            "win_rate": round((data["wins"] / total) * 100, 2),
            "total_profit": round(data["profit"], 2),
            "avg_profit": round(data["profit"] / total, 2),
        }

    return result   # Dict keyed by structure type


# ============================================================
# REJECTION INSIGHTS
# ============================================================
def analyze_rejections(rejection_table: dict) -> dict:
    if not rejection_table:
        return {}   # Nothing to analyse

    # Sort rejection reasons by percentage descending — worst offenders first
    sorted_reasons = sorted(
        rejection_table.items(),
        key=lambda x: x[1]["percentage"],
        reverse=True
    )

    top_3 = sorted_reasons[:3]   # Take the three most common rejection reasons

    insights = []   # Will hold plain English insight strings

    for reason, data in top_3:
        pct = data["percentage"]   # Percentage of total rejections for this reason

        if "no_extreme" in reason:
            # Mean reversion is not seeing RSI extremes — thresholds may be too tight
            insights.append(
                f"{pct}% of trades blocked by lack of extreme conditions → entry too strict"
            )

        elif "structure" in reason:
            # Structure filter is blocking too many trades — may be too restrictive
            insights.append(
                f"{pct}% rejected by structure filter → structure logic too restrictive or misaligned"
            )

        elif "momentum" in reason:
            # Momentum candle check is blocking entries — threshold may need loosening
            insights.append(
                f"{pct}% blocked by momentum → consider lowering momentum threshold"
            )

        elif "adx" in reason:
            # ADX minimum is blocking trend trades — threshold may be too high
            insights.append(
                f"{pct}% blocked by ADX → trend filter too strict"
            )

        else:
            # Generic fallback for any rejection reason not specifically handled
            insights.append(
                f"{pct}% blocked by {reason}"
            )

    return {
        "top_reasons": top_3,    # The three most common rejection reasons with counts
        "insights": insights     # Plain English improvement suggestions
    }


# ============================================================
# FUNCTION WRAPPER — PRESERVES OLD API
# ============================================================
def backtest(
    df: pd.DataFrame,
    initial_balance: float = 5000,
    allow_unvalidated_strategies: bool = False,
    venue_id: str = "binance",   # claude code changed: new param — Kraken Multi-Venue Execution, Step 14
) -> dict:
    """
    Backward-compatible wrapper.

    Other files can keep calling:
        backtest(df, initial_balance=5000)

    while the real work is now done by the Backtester class.

    venue_id: claude code changed: new — see Backtester.__init__'s venue_id
        docstring. Selects which venue's real cost model is applied; never
        changes df's price data. Every existing caller in this codebase
        calls backtest(df) positionally-only, so the "binance" default
        (proven identical to pre-Step-14 behavior) leaves all of them
        unaffected.
    """
    # Create a Backtester instance and run it — returns the full results dict
    return Backtester(
        df=df, initial_balance=initial_balance,
        allow_unvalidated_strategies=allow_unvalidated_strategies,
        venue_id=venue_id,
    ).run()