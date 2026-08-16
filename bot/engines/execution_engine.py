# ============================================================
# engines/execution_engine.py
# Execution Engine — central coordinator of the live trading system
#
# This file is the bridge between every other component.
# It receives signals from the strategy and orchestrates:
#   - Position sizing (how much to buy)
#   - Order execution (via OrderManager)
#   - Position tracking (what's currently open)
#   - Exit management (stop loss / take profit monitoring)
#   - Trade journaling (permanent database record)
#
# KEY FIXES FROM PREVIOUS VERSION:
# 1. signal["sl"]  used throughout — NOT signal["stop"]
#    Your strategy returns "sl" as the stop loss key
# 2. signal["signal"] used for direction — NOT signal["side"]
#    Your strategy returns "BUY"/"SELL" under "signal" key
# 3. "BUY"/"SELL" normalised to "buy"/"sell" for ccxt
#    ccxt requires lowercase — your strategy returns uppercase
# 4. TradeLogger updated to use TradeRecord (Django model)
#    NOT the backtester Trade class
# 5. risk_amount passed to log_entry for R-Multiple tracking
# 6. claude code changed: stop loss is now placed as a REAL resting order
#    on the exchange at entry time (architecture audit finding C2), not
#    just a software price comparison in manage_positions(). Protection
#    now survives bot crashes/downtime. Take-profit is unchanged — still
#    software-polled (see STEP 7B in execute_signal() for why).
# ============================================================
import ccxt
import logging    # Structured logging throughout

# Import all connected system components
from bot.engines.order_manager    import (
    OrderManager,
    OrderStillOpenException,
    OrderUnconfirmedException,
    RateLimitException
)
from bot.engines.position_tracker import PositionTracker
from bot.engines.market_data      import MarketData
from bot.risk.position_sizer      import PositionSizer
from bot.risk.drawdown_guard      import DrawdownGuard
from bot.journal.trade_logger     import TradeLogger
from bot.journal.models           import TradeRecord    # claude code changed: new import — DB-backed duplicate-position guard
from bot.engines.trade_narrative  import TradeNarrativeGenerator, TradeNarrative   # claude code changed: new import — Phase 4 (DB persistence of trade provenance)
from typing import Dict   # claude code changed: new import

# Create logger specific to this module
logger = logging.getLogger(__name__)


class ExecutionEngine:
    """
    Central coordinator of the live trading system.

    One instance of this class runs for the entire life of the bot.
    All entries, exits, and position checks flow through here.

    Dependencies injected on init:
    - OrderManager:    exchange communication
    - PositionTracker: open position memory
    - MarketData:      price and balance fetching
    - PositionSizer:   quantity calculation from risk %
    - TradeLogger:     database record keeping
    """

    def __init__(self, exchange, dry_run=False):
        # Store exchange reference — passed to components that need it
        self.exchange = exchange

        # claude code changed: new — fixes architecture audit finding C-2
        # (issue 2). Threaded into OrderManager/MarketData below so DRY_RUN
        # simulates fills and paper balance instead of bot_runner.py skipping
        # execute_signal()/manage_positions() entirely, which previously
        # meant DRY_RUN could never write a TradeRecord.
        self.dry_run = dry_run

        # OrderManager — handles all order placement and confirmation safely
        # Contains retry logic, slippage capture, and typed exceptions
        self.order_manager = OrderManager(exchange, dry_run=dry_run)    # claude code changed: pass through dry_run

        # PositionTracker — fast in-memory dict of all open positions
        # Checked every candle to decide if exits should fire
        self.position_tracker = PositionTracker()

        # MarketData — fetches live candles, price, and account balance
        # Balance is fetched fresh before every new entry
        self.market_data = MarketData(exchange, dry_run=dry_run)    # claude code changed: pass through dry_run

        # PositionSizer — calculates trade quantity from account risk %
        # claude code changed: was PositionSizer(risk_pct=0.01) — a hardcoded
        # literal duplicating PositionSizer's own default (architecture audit
        # finding M3). No args now, so it always follows PositionSizer's
        # default, which itself comes from the single-sourced bot.config.risk.
        self.position_sizer = PositionSizer()

        # DrawdownGuard — claude code changed: new — portfolio-level kill
        # switch. Blocks new entries once drawdown from peak balance
        # breaches bot.config.risk.MAX_DRAWDOWN_PCT; see that module for
        # the full rationale, including known scope limits.
        self.drawdown_guard = DrawdownGuard()

        # TradeLogger — writes entries and exits to TradeRecord database table
        # Read by dashboard and analytics views
        self.trade_logger = TradeLogger()

        # claude code changed: new — Phase 4 (DB persistence of trade
        # provenance). Mirrors Backtester's own self.narrator pattern
        # (backtester.py). Generates the entry-time TradeNarrative (which
        # carries research_verdict/production_eligible from
        # validated_feature_registry.py) so it can be persisted via
        # TradeLogger instead of being computed-and-discarded, which is
        # all bot_runner.py's own dry-run preview call did before this.
        self.narrator = TradeNarrativeGenerator()

        # claude code changed: new — per-symbol entry-time narrative,
        # retrieved at exit so log_exit() can complete it with outcome
        # text and persist both halves together. Kept as ExecutionEngine's
        # own dict, separate from PositionTracker.open_positions, so
        # PositionTracker's dict shape (relied on elsewhere) never has to
        # carry a non-JSON-serializable dataclass object.
        self._open_narratives: Dict[str, TradeNarrative] = {}

        # Slippage monitor — tracks execution quality over time
        # If average slippage climbs above threshold, log a warning
        self.total_slippage_pct = 0.0    # Running sum of all slippage values
        self.slippage_count     = 0       # Number of fills with slippage data


    # ============================================================
    # EXECUTE SIGNAL — ENTRY PIPELINE
    # Called by bot_runner.py when strategy returns BUY or SELL
    # ============================================================
    def execute_signal(self, signal):
        """
        Processes a BUY or SELL signal from MovingAverageStrategy.

        Signal dict received from strategy (via bot_runner.py):
        {
            "signal":   "BUY",            ← direction key
            "entry":    5.00,             ← intended entry price
            "sl":       4.90,             ← stop loss (NOT "stop")
            "tp":       5.20,             ← take profit
            "rsi":      46.2,             ← RSI at signal time
            "reason":   "ema_buy_setup",  ← why signal fired
            "strategy": "MovingAverageStrategy",
            "symbol":   "NEAR/USDT"       ← added by bot_runner.py
        }
        """

        # Extract symbol once for use throughout this method
        symbol = signal["symbol"]

        # ----------------------------------------------------------
        # GUARD 1: No duplicate positions
        # If a position is already open on this symbol, skip entry.
        # This prevents double-entering on the same pair.
        # ----------------------------------------------------------
        if self.position_tracker.has_position(symbol):
            logger.warning(
                f"[ExecutionEngine] Signal ignored — "
                f"position already open on {symbol}"
            )
            return  # Exit method — do nothing

        # claude code changed: new — GUARD 1 above only checks in-memory
        # state, which resets to empty on every process restart. A crash
        # with a real position still open would let this in-memory check
        # pass even though a TradeRecord for it already exists — the DB is
        # the durable source of truth PositionTracker's in-memory dict
        # isn't. Checked here too so a restart can't place a second real
        # order (and a second OPEN TradeRecord — see the unique constraint
        # in bot/journal/models.py) on top of one the bot has simply
        # forgotten about.
        if TradeRecord.objects.filter(symbol=symbol, status="OPEN").exists():
            logger.critical(
                f"[ExecutionEngine] Signal ignored for {symbol} — an OPEN "
                f"TradeRecord already exists in the database but "
                f"PositionTracker has no record of it in memory (likely a "
                f"restart after a crash). Reconcile manually before trading "
                f"this symbol again."
            )
            return  # Exit method — do nothing

        # ----------------------------------------------------------
        # GUARD 2: Reconciliation must complete before trading
        # Ensures bot knows real exchange state before placing orders.
        # Prevents entering trades while untracked positions may exist.
        # ----------------------------------------------------------
        if not self.position_tracker.is_reconciled:
            logger.critical(
                "[ExecutionEngine] Trading BLOCKED — "
                "reconciliation not complete. "
                "Ensure reconcile_with_exchange() ran on startup."
            )
            return  # Do not trade with unknown position state

        # ----------------------------------------------------------
        # STEP 1: Fetch live account balance
        # Never use a static balance — fees and P&L change it constantly.
        # Wrong balance = wrong position size = wrong risk per trade.
        # ----------------------------------------------------------
        current_balance = self.market_data.get_balance()

        # Guard: if balance fetch returned zero, do not size a trade
        if current_balance <= 0:
            logger.error(
                f"[ExecutionEngine] Balance unavailable (${current_balance:.2f}) "
                f"— cannot size position for {symbol}. Skipping signal."
            )
            return  # Skip — cannot calculate correct size without balance

        # ----------------------------------------------------------
        # STEP 1B: Portfolio drawdown kill switch
        # claude code changed: new — blocks NEW entries once drawdown from
        # peak balance breaches the configured limit (see
        # bot/risk/drawdown_guard.py for full rationale). Deliberately
        # placed after the balance guard above (needs a valid balance to
        # evaluate) and before sizing (no point sizing a trade that will
        # be blocked). manage_positions() is untouched by this — open
        # positions still close normally via stop-loss/take-profit even
        # while this is tripped.
        # ----------------------------------------------------------
        if self.drawdown_guard.update(current_balance):
            logger.critical(
                f"[ExecutionEngine] Signal ignored for {symbol} — "
                f"drawdown kill switch TRIPPED "
                f"({self.drawdown_guard.current_drawdown_pct(current_balance) * 100:.2f}% "
                f"from peak ${self.drawdown_guard.peak_balance:.2f}). "
                f"No new entries until drawdown recovers below "
                f"{self.drawdown_guard.max_drawdown_pct * 100:.1f}%."
            )
            return  # Skip — kill switch is active, no new risk allowed

        # ----------------------------------------------------------
        # STEP 2: Calculate position size
        # Uses "sl" key from signal — matches MovingAverageStrategy output.
        # PositionSizer returns quantity in base asset units (e.g. NEAR).
        # ----------------------------------------------------------
        quantity = self.position_sizer.calculate(
            balance=current_balance,
            entry_price=signal["entry"],
            stop_price=signal["sl"]         # ← "sl" not "stop"
        )

        # Guard: zero quantity means stop is too close or balance too low
        if quantity <= 0:
            logger.warning(
                f"[ExecutionEngine] Quantity is zero for {symbol} — "
                f"check stop distance vs balance. Skipping signal..."
            )
            return  # Skip — no point placing a zero-quantity order

        # ----------------------------------------------------------
        # STEP 3: Calculate dollar risk for this trade
        # Stored in the position so R-Multiple can be calculated on exit.
        # risk_amount = distance from entry to stop × quantity
        # ----------------------------------------------------------
        risk_amount = abs(signal["entry"] - signal["sl"]) * quantity

        # ----------------------------------------------------------
        # STEP 4: Normalise signal direction for ccxt
        # Your strategy returns "BUY" or "SELL" (uppercase).
        # ccxt requires "buy" or "sell" (lowercase).
        # Without this, create_order() raises InvalidOrder.
        # ----------------------------------------------------------
        order_side = signal["signal"].lower()   # "BUY" → "buy", "SELL" → "sell"

        logger.info(
            f"[ExecutionEngine] Entry signal | "
            f"{signal['signal']} {symbol} | "
            f"Balance: ${current_balance:.2f} | "
            f"Qty: {quantity} | "
            f"Risk: ${risk_amount:.2f} | "
            f"Entry: {signal['entry']} | "
            f"SL: {signal['sl']} | "
            f"TP: {signal['tp']}"
        )

        # ----------------------------------------------------------
        # STEP 5: Place entry order via OrderManager
        # OrderManager handles retries, confirmation, and slippage.
        # Returns a rich result dict on success.
        # Raises typed exceptions on failure — each handled separately.
        # ----------------------------------------------------------
        try:
            result = self.order_manager.place_order(
                symbol=symbol,
                side=order_side,            # lowercase "buy" or "sell"
                quantity=quantity,
                price=signal["entry"]       # limit order at signal entry price
            )

        except OrderStillOpenException as e:
            # Order was submitted but fill is unconfirmed.
            # A position may be open on the exchange already.
            # Do NOT register anything — state is unknown.
            logger.critical(
                f"[ExecutionEngine] ENTRY unconfirmed for {symbol}: {e}. "
                f"Check exchange manually before next signal."
            )
            return  # Halt — do not proceed with unknown fill state

        except OrderUnconfirmedException as e:
            # Order completely unknown after all recovery attempts.
            # Could be open, filled, or cancelled — impossible to tell.
            logger.critical(
                f"[ExecutionEngine] ENTRY unknown state for {symbol}: {e}. "
                f"Manual reconciliation required."
            )
            return  # Halt — do not assume fill happened

        except RateLimitException as e:
            # Rate limit exhausted — system-level issue, not just one order.
            # Bot should stop trading until operator resolves this.
            logger.critical(
                f"[ExecutionEngine] Rate limit exceeded on entry for {symbol}: {e}. "
                f"Bot pausing — check API key limits."
            )
            return  # Halt this signal — bot_runner will retry next candle

        except Exception as e:
            # Any other unexpected failure — log and abort cleanly.
            logger.error(
                f"[ExecutionEngine] Entry order failed for {symbol}: "
                f"{type(e).__name__}: {e}"
            )
            return  # Clean abort — no position registered

        # ----------------------------------------------------------
        # STEP 6: Slippage feedback loop
        # Tracks execution quality over all trades.
        # Warns if average slippage is eating into strategy edge.
        # ----------------------------------------------------------
        slippage = result.get("slippage_pct") or 0.0

        if slippage > 0:
            self.total_slippage_pct += slippage     # Add to running total
            self.slippage_count += 1                # Count this fill

            # Recalculate rolling average slippage across all fills
            avg_slippage = self.total_slippage_pct / self.slippage_count

            # Warn if average is exceeding acceptable threshold
            # For a strategy with 2R target, 0.3% avg slippage is significant
            if avg_slippage > 0.3:
                logger.warning(
                    f"[ExecutionEngine] Avg slippage HIGH: "
                    f"{avg_slippage:.4f}% over {self.slippage_count} fills. "
                    f"Consider reducing size on illiquid pairs."
                )

        # claude code changed: new — Phase 4 (DB persistence of trade
        # provenance). Generated here (not thrown away like bot_runner.py's
        # dry-run preview) so it can be persisted by log_entry() below and
        # completed by log_exit() when this position closes.
        narrative = self.narrator.generate_entry(signal, symbol)
        self._open_narratives[symbol] = narrative

        # ----------------------------------------------------------
        # STEP 7: Register position in tracker
        # Build position dict with all fields manage_positions() needs.
        # Uses "sl" and "tp" keys — consistent with signal dict naming.
        # ----------------------------------------------------------
        position = {
            "symbol":       symbol,                  # trading pair
            "side":         signal["signal"],        # "BUY" or "SELL" (uppercase)
            "entry_price":  result["fill_price"],    # actual fill — not signal price
            "sl":           signal["sl"],            # stop loss level
            "tp":           signal["tp"],            # take profit level
            "quantity":     result["filled_qty"],    # units actually filled
            "order_id":     result["order_id"],      # exchange order ID
            "fee_entry":    result["fee_usdt"],      # normalised entry fee
            "risk_amount":  risk_amount,             # dollar risk for R-Multiple calc
            "rsi":          signal.get("rsi", 0),    # RSI at entry — for journal
            "strategy":     signal.get("strategy", "MovingAverageStrategy"),
            "reason":       signal.get("reason", ""),
            "sl_order_id":  None,   # claude code changed: new — set below once the resting stop-loss order is placed; stays None if placement fails, which tells manage_positions() to fall back to software polling for this position
        }

        # Store in position tracker — bot now knows this trade is live
        self.position_tracker.open_positions[symbol] = position

        # ----------------------------------------------------------
        # STEP 7B: Place stop loss directly on the exchange
        # claude code changed: new — fixes architecture audit finding C2.
        # See order_manager.place_stop_loss()'s docstring for the full
        # rationale and the scope note on why take-profit is not placed
        # as a matching resting order here.
        # ----------------------------------------------------------
        exit_side_for_stop = "sell" if signal["signal"] == "BUY" else "buy"    # claude code changed: new — opposite side closes the position, same logic _close_position() already uses below

        try:
            stop_order = self.order_manager.place_stop_loss(
                symbol=symbol,
                side=exit_side_for_stop,
                quantity=result["filled_qty"],      # protect the quantity that actually filled, not the requested quantity
                stop_price=signal["sl"],
            )
            position["sl_order_id"] = stop_order["order_id"]    # claude code changed: new

        except Exception as e:
            # claude code changed: new — do not abort the trade over this; the
            # entry already filled and is real. Log loudly instead so the
            # operator knows this specific position is running without
            # exchange-side protection — manage_positions()'s software
            # fallback (see below) is the only thing watching it until
            # this is resolved.
            logger.critical(
                f"[ExecutionEngine] Stop-loss order FAILED to attach for {symbol}: "
                f"{type(e).__name__}: {e}. Position is OPEN with NO exchange-side "
                f"stop — relying on software polling only until this is resolved."
            )

        # ----------------------------------------------------------
        # STEP 8: Write entry to database
        # Creates a new TradeRecord with status="OPEN".
        # Updated later by log_exit() when trade closes.
        # ----------------------------------------------------------
        self.trade_logger.log_entry(signal, result, risk_amount, narrative=narrative)   # claude code changed: new arg — Phase 4

        logger.info(
            f"[ExecutionEngine] Entry complete | {symbol} | "
            f"Fill: {result['fill_price']} | "
            f"Slippage: {slippage:.4f}% | "
            f"Fee: ${result['fee_usdt']:.4f} | "
            f"Risk: ${risk_amount:.2f}"
        )


    # ============================================================
    # MANAGE POSITIONS — EXIT PIPELINE
    # Called every candle by bot_runner.py.
    # Checks all open positions against current market price.
    # Triggers _close_position() when SL or TP is breached.
    # ============================================================
    def manage_positions(self):
        """
        The exit pipeline — loops all open positions and checks
        whether stop loss or take profit has been hit.

        Must be called every candle loop in bot_runner.py.
        Without this, trades never close — ever.

        claude code changed: stop loss is now checked by asking the
        exchange whether the resting order placed in execute_signal()'s
        STEP 7B has filled — that order is the primary stop mechanism now,
        enforced by the exchange even between polls of this method. The
        original software price-comparison for the stop is kept as a
        fallback, used only when a position has no resting order (e.g.
        placement failed at entry — see the try/except around
        place_stop_loss() in execute_signal()). Take-profit is unchanged —
        still a software comparison every call, same as before this fix.
        """

        # Get a safe copy of positions for iteration.
        # We copy because _close_position() modifies open_positions.
        # Modifying a dict while iterating it raises RuntimeError.
        open_positions = self.position_tracker.get_all_positions()

        # Nothing to manage if no open positions
        if not open_positions:
            return  # Exit early — no API calls needed

        # Check each open position against the current price
        for symbol, position in open_positions.items():

            # --------------------------------------------------
            # CHECK 1: has the resting exchange-side stop already filled?
            # claude code changed: new — this is now the primary stop
            # mechanism; the exchange enforces it even when this loop
            # isn't running (bot crashed, network down, etc.).
            # --------------------------------------------------
            sl_order_id = position.get("sl_order_id")

            if sl_order_id:
                fill_result = self.order_manager.check_resting_order(sl_order_id, symbol)

                if fill_result is not None:
                    logger.info(
                        f"[ExecutionEngine] Exchange-side STOP LOSS filled | {symbol} | "
                        f"Fill: {fill_result['fill_price']}"
                    )
                    self._close_position_from_fill(symbol, position, fill_result, "stop_loss")
                    continue  # Position closed — move to next symbol

            # Fetch current live price for this symbol — needed for the
            # take-profit check below, and for the stop fallback when
            # sl_order_id is None
            current_price = self.market_data.get_price(symbol)

            # Guard: skip this symbol if price fetch failed
            # Never make exit decisions based on missing price data
            if current_price is None:
                logger.warning(
                    f"[ExecutionEngine] Price unavailable for {symbol} "
                    f"— skipping exit check this candle"
                )
                continue  # Move to next position in the loop

            # Extract the levels we need to compare against
            sl_price = position["sl"]       # Stop loss — close for a loss
            tp_price = position["tp"]       # Take profit — close for a win
            side     = position["side"]     # "BUY" or "SELL" (uppercase)

            # --------------------------------------------------
            # FALLBACK STOP CHECK
            # claude code changed: was the only stop mechanism; now only
            # acted on when there is no resting exchange-side order
            # (sl_order_id is None) — preserves protection for the case
            # where place_stop_loss() failed at entry time. If a resting
            # order exists, the exchange owns that decision — this bot-side
            # check is skipped so the two mechanisms can't disagree.
            # --------------------------------------------------
            if not sl_order_id:

                if side == "BUY" and current_price <= sl_price:
                    logger.info(
                        f"[ExecutionEngine] STOP LOSS hit (software fallback) | {symbol} | "
                        f"Price: {current_price} | SL: {sl_price}"
                    )
                    self._close_position(symbol, position, "stop_loss")
                    continue

                elif side == "SELL" and current_price >= sl_price:
                    logger.info(
                        f"[ExecutionEngine] STOP LOSS hit (software fallback, short) | {symbol} | "
                        f"Price: {current_price} | SL: {sl_price}"
                    )
                    self._close_position(symbol, position, "stop_loss")
                    continue

            # --------------------------------------------------
            # TAKE PROFIT — unchanged by this fix, still software-polled
            # every call (see order_manager.place_stop_loss()'s docstring
            # scope note for why this isn't a resting exchange order too)
            # --------------------------------------------------
            if side == "BUY" and current_price >= tp_price:
                logger.info(
                    f"[ExecutionEngine] TAKE PROFIT hit | {symbol} | "
                    f"Price: {current_price} | TP: {tp_price}"
                )
                self._close_position(symbol, position, "take_profit")

            elif side == "SELL" and current_price <= tp_price:
                logger.info(
                    f"[ExecutionEngine] TAKE PROFIT hit (short) | {symbol} | "
                    f"Price: {current_price} | TP: {tp_price}"
                )
                self._close_position(symbol, position, "take_profit")


    # ============================================================
    # CLOSE POSITION — INTERNAL
    # Places the exit order and writes the closed trade to database.
    # Only called from manage_positions() — not called directly.
    # ============================================================
    def _close_position(self, symbol, position, reason):
        """
        Executes a market exit order and records the completed trade.

        reason: "stop_loss" or "take_profit" — stored in journal
        Uses market order for exit — guarantees fill.
        Limit orders on exit risk non-fills at critical moments.

        claude code changed: now cancels any resting exchange-side stop
        order first. This path means the position is closing some OTHER
        way (take-profit, or the software fallback stop) — a stop order
        still resting on the exchange for this symbol would otherwise be
        left dangling, and would itself try to sell a quantity we no
        longer hold once this exit fills.
        """

        # ---- Cancel any resting stop-loss order before exiting ----
        sl_order_id = position.get("sl_order_id")
        if sl_order_id:
            cancel_result = self.order_manager.cancel_order(sl_order_id, symbol)
            if cancel_result is None:
                # cancel_order() already logs the specific reason (not found,
                # or an unexpected error) — just note it here for this exit's
                # own audit trail
                logger.warning(
                    f"[ExecutionEngine] Could not confirm cancellation of resting "
                    f"stop {sl_order_id} for {symbol} — it may have already filled "
                    f"or been cancelled; verify manually after this exit."
                )

        # Exit side is always opposite of entry side
        # Long (BUY) exits by selling back the asset
        # Short (SELL) exits by buying back the asset
        exit_side = "sell" if position["side"] == "BUY" else "buy"

        logger.info(
            f"[ExecutionEngine] Closing position | {symbol} | "
            f"Reason: {reason} | Exit side: {exit_side} | "
            f"Qty: {position['quantity']}"
        )

        # ---- Place market exit order ----
        try:
            exit_result = self.order_manager.place_order(
                symbol=symbol,
                side=exit_side,                  # "sell" to close long, "buy" to close short
                quantity=position["quantity"],   # exact quantity we entered with
                price=None                       # None = market order (guaranteed fill)
            )

        except OrderStillOpenException as e:
            # Exit order placed but fill unconfirmed — position may still be open
            logger.critical(
                f"[ExecutionEngine] EXIT unconfirmed for {symbol}: {e}. "
                f"Position left in tracker — will retry next candle."
            )
            return  # Do NOT remove from tracker — may still be open

        except OrderUnconfirmedException as e:
            # Exit order completely unknown — cannot confirm close
            logger.critical(
                f"[ExecutionEngine] EXIT unknown state for {symbol}: {e}. "
                f"Manual check required — position left in tracker."
            )
            return  # Leave in tracker — retry will happen next candle

        except RateLimitException as e:
            # Rate limited during exit — very serious
            # Leave position in tracker — exit will retry next candle
            logger.critical(
                f"[ExecutionEngine] Rate limit during EXIT for {symbol}: {e}. "
                f"Position left open — will retry."
            )
            return  # Retry next candle

        except Exception as e:
            # Unexpected exit failure — leave position in tracker
            # Better to have a stale tracked position than an untracked one
            logger.error(
                f"[ExecutionEngine] Exit order failed for {symbol}: "
                f"{type(e).__name__}: {e}. Retrying next candle."
            )
            return  # Position stays in tracker — retry next loop

        # ---- Remove from position tracker ----
        # Only remove AFTER confirmed exit fill — never before
        # Removing before confirmation risks losing track of live positions
        self.position_tracker.remove_position(symbol)

        # ---- Write exit to database ----
        # Updates the existing TradeRecord with exit price, P&L, R-Multiple
        # claude code changed: new — Phase 4. Pops the entry-time narrative
        # so log_exit() can complete it with outcome text and persist both.
        self.trade_logger.log_exit(
            symbol=symbol,
            exit_result=exit_result,
            risk_amount=position["risk_amount"],   # original dollar risk
            reason=reason,                           # "stop_loss" or "take_profit"
            narrative=self._open_narratives.pop(symbol, None),   # claude code changed: new
        )

        logger.info(
            f"[ExecutionEngine] Position CLOSED | {symbol} | "
            f"Reason: {reason} | "
            f"Exit fill: {exit_result['fill_price']} | "
            f"Fee: ${exit_result['fee_usdt']:.4f}"
        )


    # ============================================================
    # CLOSE POSITION FROM FILL — INTERNAL
    # claude code changed: new — handles the case where the exchange
    # already executed the exit (the resting stop-loss order filled)
    # rather than the bot needing to place a new exit order itself.
    # Called only from manage_positions()'s resting-stop check above.
    # ============================================================
    def _close_position_from_fill(self, symbol, position, fill_result, reason):
        """
        Records a position close that already happened on the exchange —
        no new order is placed here, unlike _close_position() which
        actively submits a market exit. fill_result is the normalised
        result OrderManager.check_resting_order() returned for the
        already-filled resting order.
        """

        # ---- Remove from position tracker ----
        # The exchange already closed this — just stop tracking it
        self.position_tracker.remove_position(symbol)

        # ---- Write exit to database ----
        # Same TradeLogger call _close_position() uses — the exit_result
        # shape is identical either way (both come from OrderManager's
        # _build_result()), so log_exit() needs no changes for this path
        self.trade_logger.log_exit(
            symbol=symbol,
            exit_result=fill_result,
            risk_amount=position["risk_amount"],
            reason=reason,
            narrative=self._open_narratives.pop(symbol, None),   # claude code changed: new — Phase 4
        )

        logger.info(
            f"[ExecutionEngine] Position CLOSED (exchange-side fill) | {symbol} | "
            f"Reason: {reason} | "
            f"Exit fill: {fill_result['fill_price']} | "
            f"Fee: ${fill_result['fee_usdt']:.4f}"
        )