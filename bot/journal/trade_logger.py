# ============================================================
# journal/trade_logger.py
# Trade Logger — writes every trade permanently to the database
#
# Called by ExecutionEngine at two points in every trade:
# 1. log_entry() — when a position is opened (status = OPEN)
# 2. log_exit()  — when a position is closed (status = WIN/LOSS)
#
# Uses TradeRecord (Django model) — NOT the backtester Trade class.
# TradeRecord is the database table.
# Trade is the in-memory backtester object.
# They are different things that live in the same models.py file.
#
# Import prefix uses bot. — matching your project structure:
# trading_bot_project/bot/journal/models.py
# ============================================================

import logging    # Structured logging — consistent with rest of system

# Import TradeRecord — the Django database model
# NOT Trade — that is the backtester's in-memory object
from bot.journal.models import TradeRecord

# claude code changed: new import — Phase 4 (DB persistence of trade
# provenance). Needed because generate_exit() requires net_pnl/r_multiple/
# holding_candles, which this class computes internally in log_exit() —
# those values don't exist yet at ExecutionEngine's call site, so
# generate_exit() must be invoked from here, after that computation.
from bot.engines.trade_narrative import TradeNarrativeGenerator

# Create logger specific to this module
logger = logging.getLogger(__name__)


class TradeLogger:
    """
    Permanent record keeper for all live bot trades.

    Every trade that ExecutionEngine opens gets a TradeRecord row
    created with status OPEN. When the trade closes, that same row
    is updated with exit data, P&L, and R-Multiple.

    The dashboard and analytics views read from these records.
    """

    def __init__(self):
        # claude code changed: new — Phase 4 (DB persistence of trade
        # provenance). See log_exit()'s docstring for why generate_exit()
        # is called from inside this class rather than by ExecutionEngine.
        self.narrator = TradeNarrativeGenerator()

    # ============================================================
    # LOG ENTRY
    # Called by ExecutionEngine after a confirmed entry fill.
    # Creates a new TradeRecord row in the database.
    # ============================================================
    def log_entry(self, signal, fill_result, risk_amount, narrative=None):
        """
        Records the opening of a live trade to the database.

        signal:      the signal dict from MovingAverageStrategy
        fill_result: the result dict returned by OrderManager
        risk_amount: dollar amount at risk — calculated by ExecutionEngine
        narrative:   claude code changed: new — Optional[TradeNarrative],
                     the entry-time narrative from
                     TradeNarrativeGenerator.generate_entry(). When given,
                     its research_verdict/production_eligible/
                     verdict_rejection_reason/entry_narrative fields are
                     persisted onto the new row. None keeps every new
                     field at its model default — kept optional so this
                     signature stays backward-compatible rather than a
                     hard requirement.
        """

        try:
            # Create a new row in the TradeRecord database table
            record = TradeRecord.objects.create(

                # ---- Identity ----
                symbol   = signal["symbol"],            # e.g. "NEAR/USDT"
                side     = signal["signal"],            # "BUY" or "SELL"
                strategy = signal.get(                  # which strategy fired
                    "strategy",
                    "MovingAverageStrategy"
                ),

                # ---- Entry data ----
                # Use actual fill price — not signal entry price
                # These differ by slippage amount
                entry_price  = fill_result["fill_price"],       # actual exchange fill
                sl           = signal["sl"],                    # stop loss level
                tp           = signal["tp"],                    # take profit level
                quantity     = fill_result["filled_qty"],       # units actually filled
                fee_entry    = fill_result["fee_usdt"],         # entry fee in USDT
                slippage_in  = fill_result.get(                 # entry slippage %
                    "slippage_pct", 0.0
                ) or 0.0,
                order_id     = fill_result["order_id"],         # exchange order ID
                rsi_at_entry = signal.get("rsi", 0.0),         # RSI at signal time
                risk_amount  = risk_amount,                     # dollar risk for R calc

                # ---- Provenance ---- claude code changed: new block — Phase 4
                research_verdict          = narrative.research_verdict if narrative else "",
                production_eligible       = narrative.production_eligible if narrative else False,
                verdict_rejection_reason  = narrative.verdict_rejection_reason if narrative else "",
                entry_narrative           = narrative.entry_narrative if narrative else "",

                # ---- Status ----
                # Trade is now live — exit fields are all null at this point
                status = "OPEN"
            )

            # Log the database record ID for full traceability
            logger.info(
                f"[TradeLogger] Entry logged | "
                f"DB ID: {record.id} | "
                f"{signal['symbol']} {signal['signal']} | "
                f"Fill: {fill_result['fill_price']} | "
                f"Qty: {fill_result['filled_qty']} | "
                f"Risk: ${risk_amount:.2f}"
            )

        except Exception as e:
            # Log failure as critical — trade is live but unrecorded
            # Operator must manually record this trade if this fires
            logger.critical(
                f"[TradeLogger] FAILED to log entry for "
                f"{signal.get('symbol', 'UNKNOWN')}: {e}. "
                f"Trade is live but has no database record."
            )


    # ============================================================
    # LOG EXIT
    # Called by ExecutionEngine after a confirmed exit fill.
    # Finds the open TradeRecord and updates it with exit data.
    # ============================================================
    def log_exit(self, symbol, exit_result, risk_amount, reason="unknown", narrative=None):
        """
        Updates the open TradeRecord with exit data and calculated P&L.

        symbol:      trading pair e.g. "NEAR/USDT"
        exit_result: result dict returned by OrderManager on exit
        risk_amount: original dollar risk — used to calculate R-Multiple
        reason:      "stop_loss", "take_profit", or "manual"
        narrative:   claude code changed: new — Optional[TradeNarrative],
                     the SAME entry-time narrative object log_entry() was
                     given for this trade. generate_exit() is called here,
                     not by ExecutionEngine, because it needs net_pnl/
                     r_multiple/holding_candles — computed below, not yet
                     available at ExecutionEngine's call site. None skips
                     exit-narrative completion (record.exit_narrative stays
                     at its model default).
        """

        try:
            # claude code changed: was TradeRecord.objects.get(...), which
            # raises MultipleObjectsReturned if more than one OPEN row
            # exists for this symbol — that's now prevented going forward
            # by the DB-level unique constraint on TradeRecord (see
            # bot/journal/models.py's Meta.constraints), but this stays
            # defensive against any row that predates that constraint, or
            # any other future gap that lets one through. Picks the most
            # recently created OPEN row as the one actually being closed —
            # the exit came from ExecutionEngine acting on a real, single
            # position, so the newest OPEN record is the correct match —
            # and logs critically so the operator knows duplicate OPEN
            # rows existed for this symbol and should investigate.
            candidates = list(
                TradeRecord.objects.filter(symbol=symbol, status="OPEN").order_by("-id")
            )

            if not candidates:
                logger.error(
                    f"[TradeLogger] No OPEN TradeRecord found for {symbol}. "
                    f"Cannot log exit. Entry may have failed to record. "
                    f"Check database manually."
                )
                return

            if len(candidates) > 1:
                logger.critical(
                    f"[TradeLogger] {len(candidates)} OPEN TradeRecords found for "
                    f"{symbol} (IDs: {[r.id for r in candidates]}) — expected at most "
                    f"one. Closing the most recent (ID {candidates[0].id}); the "
                    f"others are left OPEN and need manual investigation."
                )

            record = candidates[0]

            # ---- Calculate Gross P&L ----
            # Gross = before fees are subtracted
            # Long:  profit when exit price > entry price
            # Short: profit when exit price < entry price
            if record.side == "BUY":
                # Long trade — bought low, selling high
                gross_pnl = (
                    exit_result["fill_price"] - record.entry_price
                ) * record.quantity

            else:
                # Short trade — sold high, buying back low
                gross_pnl = (
                    record.entry_price - exit_result["fill_price"]
                ) * record.quantity

            # ---- Calculate Net P&L ----
            # Net = gross minus both entry and exit fees
            # This is the real number that hits your account balance
            net_pnl = (
                gross_pnl           # what the price move made/lost
                - record.fee_entry  # fee paid when we entered
                - exit_result["fee_usdt"]   # fee paid when we exited
            )

            # ---- Calculate R-Multiple ----
            # R-Multiple = net P&L divided by initial dollar risk
            # +1.8R means you made 1.8 times what you risked
            # -1.0R means you lost exactly what you risked (full stop)
            if risk_amount and risk_amount > 0:
                # Normal case — divide net P&L by risk amount
                r_multiple = net_pnl / risk_amount
            else:
                # Risk amount missing or zero — cannot calculate R
                # Default to 0 rather than crash
                r_multiple = 0.0
                logger.warning(
                    f"[TradeLogger] risk_amount is zero for {symbol} — "
                    f"R-Multiple defaulted to 0. Check position sizing."
                )

            # ---- Calculate holding candles ----
            # How long the trade was open in candle count
            # Useful for analytics — do longer trades perform better?
            # We don't have candle index here so we leave as None
            # The backtester Trade class calculates this — not needed in live
            holding_candles = None   # Live bot tracks time not candle count

            # ---- Determine outcome ----
            # WIN if net P&L is positive, LOSS if negative or zero
            outcome = "WIN" if net_pnl > 0 else "LOSS"

            # ---- Update the database record ----
            # All exit fields are updated in one save() call
            record.exit_price     = exit_result["fill_price"]          # actual exit fill
            record.fee_exit       = exit_result["fee_usdt"]            # exit fee in USDT
            record.slippage_out   = exit_result.get(                   # exit slippage %
                "slippage_pct", 0.0
            ) or 0.0
            record.exit_reason    = reason                              # why it closed
            record.gross_pnl      = round(gross_pnl, 4)               # before fees
            record.net_pnl        = round(net_pnl, 4)                 # after fees
            record.r_multiple     = round(r_multiple, 4)              # R-Multiple
            record.holding_candles = holding_candles                   # duration
            record.status         = outcome                            # WIN or LOSS

            # claude code changed: new block — Phase 4 (DB persistence of
            # trade provenance). Completes the entry-time narrative with
            # outcome text now that net_pnl/r_multiple/holding_candles are
            # known, and persists it. narrative already carries
            # research_verdict/production_eligible/verdict_rejection_reason
            # from entry time (set by log_entry() above), so only
            # exit_narrative needs updating here.
            if narrative is not None:
                record.exit_narrative = self.narrator.generate_exit(
                    narrative,
                    exit_result["fill_price"],
                    reason,
                    net_pnl,
                    r_multiple,
                    holding_candles=holding_candles,
                )

            # Save all changes to database in a single operation
            record.save()

            # Log the completed trade summary for the audit trail
            logger.info(
                f"[TradeLogger] Exit logged | "
                f"{symbol} | "
                f"Outcome: {outcome} | "
                f"Gross: ${gross_pnl:.2f} | "
                f"Net: ${net_pnl:.2f} | "
                f"R: {r_multiple:.2f}R | "
                f"Reason: {reason}"
            )

        except Exception as e:
            # Any other database error — log critically
            # P&L will be unrecorded — requires manual entry
            logger.critical(
                f"[TradeLogger] FAILED to log exit for {symbol}: "
                f"{type(e).__name__}: {e}. "
                f"P&L unrecorded — check database."
            )