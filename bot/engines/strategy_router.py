
    # ============================================================
# bot/engines/strategy_router.py
# Strategy Router — selects and executes the correct strategy
# based on the current market regime
#
# PURPOSE:
# This file is the decision layer between regime detection
# and actual strategy execution. It answers one question:
# "Given the current market regime, which strategy should fire?"
#
# ROUTING LOGIC:
#   TRENDING_UP    → MovingAverageStrategy (BUY signals only)
#   TRENDING_DOWN  → MovingAverageStrategy (SELL signals only)
#   RANGING        → MeanReversionStrategy (both directions) — claude code
#                     changed: gated behind validated_feature_registry.py;
#                     blocked by default (RSI/BB not research-validated),
#                     see BLOCK 3 in route() below
#   HIGH_VOLATILITY → No strategy fires — sit out
#
# WHY THIS MATTERS:
# Before this file existed, MovingAverageStrategy ran in ALL
# market conditions. In ranging markets it was constantly
# entering trend trades that immediately reversed.
# This router ensures each strategy only runs in its
# natural home environment — the conditions it was designed for.
#
# CONNECTIONS:
#   Receives from: regime_detector.py (RegimeResult)
#   Sends to:      execution_engine.py (signal dict)
#   Uses:          moving_average.py, mean_reversion_strategy.py
# ============================================================

import logging    # Structured logging throughout

# Import both strategy classes
from bot.strategies.moving_average import MovingAverageStrategy
from bot.strategies.mean_reversion_strategy import MeanReversionStrategy

# Import the RegimeResult dataclass for type hints and field access
from bot.engines.regime_detector import RegimeResult

# claude code changed: new import — gates strategies behind research validation
from bot.research.validated_feature_registry import get_strategy_verdict

# Create logger for this module
logger = logging.getLogger(__name__)


# ============================================================
# ROUTER RESULT — what the router returns after evaluation
# A plain dict — keeps things simple for execution_engine.py
# which already expects a signal dict
# ============================================================

# The router returns a standard signal dict with extra fields:
# {
#     "signal":          "BUY" / "SELL" / "NO_SIGNAL"
#     "entry":           float
#     "sl":              float
#     "tp":              float
#     "rsi":             float
#     "reason":          str
#     "strategy":        str  — which strategy produced this
#     "regime":          str  — regime at time of signal
#     "regime_confidence": str  — HIGH / MEDIUM / LOW
#     "blocked_reason":  str  — why signal was blocked (if applicable)
# }


class StrategyRouter:
    """
    Routes market data to the correct strategy based on regime.

    Instantiated once in bot_runner.py and backtester.py.
    The route() method is called every candle with fresh data
    and the current RegimeResult.

    Design principle:
    The router does NOT modify signals — it only decides
    which strategy produces them and whether they are allowed.
    Signal quality is the strategy's responsibility.
    Regime suitability is the router's responsibility.
    """

    def __init__(
        self,
        allow_longs=True,           # Set False to disable all long trades
        allow_shorts=True,          # Set False to disable all short trades
        require_high_confidence=False,  # If True, only trade HIGH confidence regimes
        min_adx_for_trend=25,       # Override ADX threshold for trend trades
        allow_unvalidated_strategies=False,  # claude code changed: new param — see validated_feature_registry.py; default False is the safe state, mirrors entry_exit_engine.py's require_passes_filters=True-safe-default convention
    ):

        # ── STRATEGY INSTANCES ────────────────────────────────
        # Instantiated once here — not recreated every candle
        # This preserves any state the strategies might hold

        # Trend-following strategy — used in TRENDING regimes
        self.trend_strategy = MovingAverageStrategy()

        # Mean reversion strategy — used in RANGING regime
        self.reversion_strategy = MeanReversionStrategy()

        # ── ROUTER SETTINGS ───────────────────────────────────

        # Whether long trades are allowed at all
        self.allow_longs = allow_longs

        # Whether short trades are allowed at all
        self.allow_shorts = allow_shorts

        # If True, LOW and MEDIUM confidence regimes are skipped
        # Reduces trade count further but improves quality
        self.require_high_confidence = require_high_confidence

        # Minimum ADX value before trend trades are allowed
        # Can override the detector's threshold for extra caution
        self.min_adx_for_trend = min_adx_for_trend

        # claude code changed: new — if False (default), strategies without a
        # SUPPORTED research verdict are blocked at routing time regardless of
        # what the strategy itself would have returned. See
        # bot/research/validated_feature_registry.py.
        self.allow_unvalidated_strategies = allow_unvalidated_strategies

        # ── PERFORMANCE TRACKING ──────────────────────────────
        # Track how many times each route was taken
        # Useful for understanding bot behaviour over time

        self.route_counts = {
            "TRENDING_UP":     0,   # Times routed to trend strategy for longs
            "TRENDING_DOWN":   0,   # Times routed to trend strategy for shorts
            "RANGING":         0,   # Times routed to mean reversion strategy
            "HIGH_VOLATILITY": 0,   # Times all trading was blocked
            "BLOCKED_CONFIDENCE": 0,  # Times blocked due to low confidence
            "BLOCKED_DIRECTION":  0,  # Times blocked due to long/short settings
            "BLOCKED_UNVALIDATED": 0,  # claude code changed: new — times a strategy was blocked for lacking a SUPPORTED research verdict
        }

        # Log initialisation
        logger.info(
            f"[StrategyRouter] Initialised | "
            f"Longs: {allow_longs} | "
            f"Shorts: {allow_shorts} | "
            f"Require high confidence: {require_high_confidence} | "
            f"Allow unvalidated strategies: {allow_unvalidated_strategies}"   # claude code changed: new
        )


    # ============================================================
    # ROUTE — MAIN PUBLIC METHOD
    # Called every candle by bot_runner.py and backtester.py
    # Returns a signal dict ready for execution_engine.py
    # ============================================================
    def route(self, df, regime_result: RegimeResult) -> dict:
        """
        Evaluates the current market and returns a signal dict.

        df:            pandas DataFrame with OHLCV data
        regime_result: RegimeResult from RegimeDetector.detect()

        Returns signal dict with regime context attached.
        Never returns None — always returns a valid dict.
        """

        # Extract regime fields for readability
        regime     = regime_result.regime       # e.g. "TRENDING_UP"
        confidence = regime_result.confidence   # "HIGH", "MEDIUM", "LOW"
        adx        = regime_result.adx          # ADX value

        # Log the routing decision being made
        logger.debug(
            f"[StrategyRouter] Routing | "
            f"Regime: {regime} | "
            f"Confidence: {confidence} | "
            f"ADX: {adx:.1f}"
        )

        # ── BLOCK 1: HIGH VOLATILITY ──────────────────────────
        # Volatility extreme means conditions are unpredictable
        # No strategy should fire in this environment
        # Waiting is a valid and profitable decision
        if regime == "HIGH_VOLATILITY":

            # Increment the tracking counter
            self.route_counts["HIGH_VOLATILITY"] += 1

            # Log clearly — operator should know volatility blocked trades
            logger.info(
                f"[StrategyRouter] BLOCKED — HIGH_VOLATILITY regime. "
                f"ATR ratio: {regime_result.atr_ratio:.2f}. Sitting out."
            )

            # Return a NO_SIGNAL with regime context attached
            return self._no_signal(
                reason="high_volatility_blocked",
                regime=regime,
                confidence=confidence
            )

        # ── BLOCK 2: LOW CONFIDENCE FILTER ───────────────────
        # If require_high_confidence is enabled, only trade when
        # three indicators agree — reduces quantity, raises quality
        if self.require_high_confidence and confidence != "HIGH":

            # Increment tracking counter
            self.route_counts["BLOCKED_CONFIDENCE"] += 1

            logger.info(
                f"[StrategyRouter] BLOCKED — confidence is {confidence}. "
                f"Require HIGH confidence is enabled."
            )

            return self._no_signal(
                reason=f"confidence_too_low_{confidence.lower()}",
                regime=regime,
                confidence=confidence
            )

        # ── ROUTE: TRENDING UP ───────────────────────────────
        # Regime is bullish trend — run MovingAverageStrategy
        # Only BUY signals are valid in an uptrend
        # SELL signals from this strategy are ignored in this regime
        if regime == "TRENDING_UP":

            # Check ADX meets minimum threshold for trend trades
            # This adds an extra layer beyond the detector's classification
            if adx < self.min_adx_for_trend:
                logger.info(
                    f"[StrategyRouter] TRENDING_UP but ADX {adx:.1f} below "
                    f"minimum {self.min_adx_for_trend}. Skipping."
                )
                return self._no_signal(
                    reason="adx_below_minimum_for_trend",
                    regime=regime,
                    confidence=confidence
                )

            # Check longs are allowed in router settings
            if not self.allow_longs:
                self.route_counts["BLOCKED_DIRECTION"] += 1
                return self._no_signal(
                    reason="longs_disabled",
                    regime=regime,
                    confidence=confidence
                )

            # Increment routing counter
            self.route_counts["TRENDING_UP"] += 1

            # Get signal from MovingAverageStrategy
            signal = self.trend_strategy.check_signal(df)

            # Only allow BUY signals in an uptrend
            # Reject any SELL signals the strategy might return
            if signal.get("signal") == "SELL":
                logger.debug(
                    "[StrategyRouter] SELL signal rejected in TRENDING_UP regime"
                )
                return self._no_signal(
                    reason="sell_rejected_in_uptrend",
                    regime=regime,
                    confidence=confidence
                )

            # Attach regime context to the signal before returning
            return self._attach_regime_context(signal, regime_result)


        # ── ROUTE: TRENDING DOWN ─────────────────────────────
        # Regime is bearish trend — run MovingAverageStrategy
        # Only SELL signals are valid in a downtrend
        # BUY signals from this strategy are ignored in this regime
        if regime == "TRENDING_DOWN":

            # Check ADX meets minimum threshold
            if adx < self.min_adx_for_trend:
                logger.info(
                    f"[StrategyRouter] TRENDING_DOWN but ADX {adx:.1f} below "
                    f"minimum {self.min_adx_for_trend}. Skipping."
                )
                return self._no_signal(
                    reason="adx_below_minimum_for_trend",
                    regime=regime,
                    confidence=confidence
                )

            # Check shorts are allowed in router settings
            if not self.allow_shorts:
                self.route_counts["BLOCKED_DIRECTION"] += 1
                return self._no_signal(
                    reason="shorts_disabled",
                    regime=regime,
                    confidence=confidence
                )

            # Increment routing counter
            self.route_counts["TRENDING_DOWN"] += 1

            # Get signal from MovingAverageStrategy
            signal = self.trend_strategy.check_signal(df)

            # Only allow SELL signals in a downtrend
            # Reject any BUY signals the strategy might return
            if signal.get("signal") == "BUY":
                logger.debug(
                    "[StrategyRouter] BUY signal rejected in TRENDING_DOWN regime"
                )
                return self._no_signal(
                    reason="buy_rejected_in_downtrend",
                    regime=regime,
                    confidence=confidence
                )

            # Attach regime context and return
            return self._attach_regime_context(signal, regime_result)


        # ── ROUTE: RANGING ───────────────────────────────────
        # Regime is ranging — run MeanReversionStrategy
        # Mean reversion profits from oscillations around a midpoint
        # MovingAverageStrategy would bleed in this environment
        if regime == "RANGING":

            # claude code changed: new block — production-validation gate.
            # MeanReversionStrategy fires directly off RSI/Bollinger Band
            # thresholds, neither of which has cleared this project's own
            # research validation bar (see validated_feature_registry.py for
            # the cited evidence). Checked here, not inside
            # MeanReversionStrategy.evaluate() itself, so the strategy's raw
            # mechanics remain directly testable/inspectable in isolation —
            # this is specifically about whether the ROUTER is allowed to
            # dispatch to it for live/backtest trades.
            verdict = get_strategy_verdict("MeanReversionStrategy")
            if not verdict.production_eligible:
                if not self.allow_unvalidated_strategies:
                    self.route_counts["BLOCKED_UNVALIDATED"] += 1
                    logger.warning(
                        f"[StrategyRouter] BLOCKED — MeanReversionStrategy is not "
                        f"production_eligible (verdict={verdict.research_verdict}). "
                        f"{verdict.rejection_reason} "
                        f"Set allow_unvalidated_strategies=True to override (test-only)."
                    )
                    return self._no_signal(
                        reason=f"strategy_not_validated_{verdict.research_verdict.lower()}",
                        regime=regime,
                        confidence=confidence,
                        verdict=verdict,   # claude code changed: new — Phase 2 (trade provenance)
                    )
                else:
                    # claude code changed: new — make the override visible, not
                    # silent, same convention as entry_exit_engine.py's
                    # require_passes_filters=False warning. Fires only when the
                    # override is actually bypassing a real block, not just
                    # because the flag is set.
                    logger.warning(
                        f"[StrategyRouter] allow_unvalidated_strategies=True — "
                        f"dispatching to MeanReversionStrategy despite verdict="
                        f"{verdict.research_verdict}. {verdict.rejection_reason} "
                        f"Treat any resulting trades as a deliberate test of an "
                        f"unvalidated strategy, not a production decision."
                    )

            # Increment ranging counter
            self.route_counts["RANGING"] += 1

            # MeanReversionStrategy uses evaluate() not check_signal()
            # Pass regime_result so it can verify the regime itself
            signal = self.reversion_strategy.evaluate(df, regime_result)

            # Guard: if evaluate() returned None, convert to NO_SIGNAL
            if signal is None:
                return self._no_signal(
                    reason="mean_reversion_returned_none",
                    regime=regime,
                    confidence=confidence
                )

            # Check direction filters
            if signal.get("signal") == "BUY" and not self.allow_longs:
                self.route_counts["BLOCKED_DIRECTION"] += 1
                return self._no_signal(
                    reason="longs_disabled",
                    regime=regime,
                    confidence=confidence
                )

            if signal.get("signal") == "SELL" and not self.allow_shorts:
                self.route_counts["BLOCKED_DIRECTION"] += 1
                return self._no_signal(
                    reason="shorts_disabled",
                    regime=regime,
                    confidence=confidence
                )

            # Attach regime context and return
            return self._attach_regime_context(signal, regime_result)


        # ── FALLBACK ─────────────────────────────────────────
        # Should never reach here — all regimes are handled above
        # If it does, log a warning and return NO_SIGNAL safely
        logger.warning(
            f"[StrategyRouter] Unhandled regime: {regime}. "
            f"This should not happen. Returning NO_SIGNAL."
        )
        return self._no_signal(
            reason=f"unhandled_regime_{regime}",
            regime=regime,
            confidence=confidence
        )


    # ============================================================
    # ATTACH REGIME CONTEXT — INTERNAL
    # Adds regime fields to a strategy signal dict
    # This is how regime information flows into the trade narrative
    # ============================================================
    def _attach_regime_context(
        self,
        signal: dict,
        regime_result: RegimeResult
    ) -> dict:
        """
        Attaches regime detection context to a strategy signal.

        Takes the raw signal from a strategy and enriches it with
        regime data. This enriched dict is what execution_engine.py
        receives — and what trade_narrative.py will read from.
        """

        # Make a copy — never mutate the original signal
        enriched = signal.copy()

        # Add regime fields to the signal
        enriched["regime"]             = regime_result.regime
        enriched["regime_confidence"]  = regime_result.confidence
        enriched["adx"]                = regime_result.adx
        enriched["atr_ratio"]          = regime_result.atr_ratio
        enriched["ema_spread_pct"]     = regime_result.ema_spread_pct
        enriched["bb_width_pct"]       = regime_result.bb_width_pct
        enriched["regime_summary"]     = regime_result.summary
        enriched["volatility_extreme"] = regime_result.volatility_extreme

        # Log the enriched signal being passed forward
        if enriched.get("signal") in ("BUY", "SELL"):
            logger.info(
                f"[StrategyRouter] Signal approved | "
                f"{enriched['signal']} | "
                f"Strategy: {enriched.get('strategy', 'unknown')} | "
                f"Regime: {regime_result.regime} | "
                f"Confidence: {regime_result.confidence} | "
                f"ADX: {regime_result.adx:.1f}"
            )
            
        structure = enriched.get("structure")

        # If structure missing or invalid → derive from regime
        if not structure or structure in ("UNKNOWN", None, ""):
            if regime_result.regime == "TRENDING_UP":
                structure = "UPTREND"
            elif regime_result.regime == "TRENDING_DOWN":
                structure = "DOWNTREND"
            elif regime_result.regime == "RANGING":
                structure = "RANGING"
            else:
                structure = "UNKNOWN"

        enriched["structure"] = structure

        return enriched   # Return the enriched signal dict


    # ============================================================
    # NO SIGNAL — INTERNAL
    # Returns a consistent NO_SIGNAL dict with regime context
    # Always includes regime fields even when no trade fires
    # ============================================================
    def _no_signal(
        self,
        reason: str,
        regime: str = "UNKNOWN",
        confidence: str = "LOW",
        verdict=None,   # claude code changed: new — Optional[StrategyVerdict], Phase 2 (trade provenance)
    ) -> dict:
        """
        Returns a standardised NO_SIGNAL dict with regime context.
        Consistent structure means execution_engine.py never gets None.

        verdict : Optional[StrategyVerdict]
            claude code changed: new param. Only passed by call sites where
            the block itself came from validated_feature_registry.py (the
            production-validation gate) — most NO_SIGNAL reasons (ADX too
            low, high volatility, direction disabled) aren't about a
            strategy's research verdict at all, so they leave this None
            rather than fabricating a verdict that didn't decide the block.
        """

        return {
            "signal":            "NO_SIGNAL",   # Execution engine checks this first
            "entry":             0,             # Zero — not used on NO_SIGNAL
            "sl":                0,             # Zero — not used on NO_SIGNAL
            "tp":                0,             # Zero — not used on NO_SIGNAL
            "rsi":               0,             # No RSI available on block
            "reason":            reason,        # Why no signal was produced
            "strategy":          "none",        # No strategy fired
            "regime":            regime,        # Regime at time of rejection
            "regime_confidence": confidence,    # Confidence at rejection
            "blocked_reason":    reason,        # Alias — used by trade narrative
            # claude code changed: new three keys — Phase 2 (trade provenance)
            "research_verdict":         verdict.research_verdict if verdict else "",
            "production_eligible":      verdict.production_eligible if verdict else False,
            "verdict_rejection_reason": verdict.rejection_reason if verdict else "",
        }


    # ============================================================
    # GET ROUTE STATS — PUBLIC UTILITY
    # Returns a summary of how many times each route was taken
    # Useful for understanding bot behaviour over a backtest run
    # ============================================================
    def get_route_stats(self) -> dict:
        """
        Returns routing statistics for the current session.
        Call this after a backtest to see regime distribution.
        """

        # Calculate total routed candles
        total = sum(self.route_counts.values())

        # Build percentage breakdown
        stats = {
            "total_candles_evaluated": total,
            "route_breakdown": {}
        }

        for route, count in self.route_counts.items():
            # Calculate percentage of total for each route
            pct = round((count / total * 100), 1) if total > 0 else 0
            stats["route_breakdown"][route] = {
                "count":      count,        # Raw count
                "percentage": pct,          # Percentage of total
            }

        return stats   # Return full stats dict for logging or display