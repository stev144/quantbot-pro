# ============================================================
# engines/position_tracker.py
# Position Tracker — knows every open trade at all times
#
# This module answers one critical question at all times:
# "What positions does the bot currently have open?"
#
# KEY UPGRADE from v1: Position Reconciliation
# If the bot crashes and restarts, it syncs with the exchange
# so it never loses track of live positions.
# Without this, a crash = ghost positions = real money at risk.
# ============================================================

import logging    # Structured logging
import ccxt       # Needed for exchange error types in reconciliation

# Create logger for this module
logger = logging.getLogger(__name__)


class PositionTracker:
    """
    Tracks all currently open positions in memory.

    Why in memory and not just the database?
    Speed. Checking a dict takes microseconds.
    A database query takes milliseconds — too slow for the main loop.
    The database (via TradeLogger) is for permanent records.
    This tracker is the fast, live view.
    """

    def __init__(self):
        # Main storage — keyed by symbol for O(1) lookup
        # Format: { "NEAR/USDT": { position dict } }
        self.open_positions = {}

        # Flag to track whether reconciliation has been run
        # Prevents trading before the bot knows its real state
        self.is_reconciled = False


    # claude code changed: removed add_position() and get_position() —
    # architecture audit finding H1. Neither had any call site anywhere
    # in the repo (confirmed via grep). ExecutionEngine.execute_signal()
    # has always written directly to self.open_positions[symbol] instead
    # of calling add_position(), and add_position()'s own signature
    # (signal["side"], signal["stop"]) never matched the real signal
    # schema used everywhere else (signal["signal"], signal["sl"]) — it
    # would have raised KeyError immediately if anything had ever called
    # it. get_position() was likewise unused; ExecutionEngine and
    # manage_positions() both go through get_all_positions() or direct
    # dict access instead.

    # ============================================================
    # CHECK IF POSITION EXISTS
    # Used by strategy to avoid double-entering a trade
    # ============================================================
    def has_position(self, symbol):
        # Returns True if we have an open position on this symbol
        return symbol in self.open_positions


    # ============================================================
    # REMOVE POSITION
    # Called by ExecutionEngine after exit order is confirmed
    # ============================================================
    def remove_position(self, symbol):
        """Removes a position after it has been closed."""

        # Check it exists before trying to remove
        if symbol in self.open_positions:
            # Delete from dict
            del self.open_positions[symbol]
            logger.info(f"[PositionTracker] Position removed | {symbol}")
        else:
            # Log a warning if we tried to remove something that wasn't there
            logger.warning(f"[PositionTracker] Tried to remove non-existent position: {symbol}")


    # ============================================================
    # RECONCILE WITH EXCHANGE — CRITICAL UPGRADE
    # Called ONCE on bot startup before any trading begins.
    #
    # Problem it solves:
    # Bot crashes at 2am. NEAR position is live on Binance.
    # Bot restarts at 6am. open_positions dict is empty.
    # Strategy fires a new BUY signal. Bot buys again.
    # Now you have double the position you intended. Real loss.
    #
    # This method prevents that by syncing with exchange on startup.
    # ============================================================
    def reconcile_with_exchange(self, exchange, tracked_symbols):
        """
        Syncs open_positions with actual exchange state on startup.
        Must be called before the main bot loop begins.

        tracked_symbols: list of symbols the bot trades
        e.g. ["NEAR/USDT", "BTC/USDT"]
        """

        logger.info("[PositionTracker] Starting position reconciliation...")

        # Check each symbol the bot is configured to trade
        for symbol in tracked_symbols:

            try:
                # Fetch all open orders for this symbol from exchange
                open_orders = exchange.fetch_open_orders(symbol)

                # If there are open orders, we may have a live position
                if open_orders:
                    logger.warning(
                        f"[PositionTracker] Found {len(open_orders)} open order(s) "
                        f"for {symbol} on exchange"
                    )

                    # Log each open order so operator can review
                    for order in open_orders:
                        logger.warning(
                            f"[PositionTracker] Open order | "
                            f"ID: {order.get('id')} | "
                            f"Side: {order.get('side')} | "
                            f"Qty: {order.get('amount')} | "
                            f"Price: {order.get('price')}"
                        )

                # Fetch recent closed orders to find filled positions
                # limit=5 is enough to catch recent fills without heavy API use
                recent_orders = exchange.fetch_orders(symbol, limit=5)

                # Loop through recent orders looking for filled positions
                for order in recent_orders:

                    # We only care about orders that actually filled
                    if order.get("status") != "closed":
                        continue  # Skip unfilled or cancelled orders

                    # Check if this filled order is already tracked
                    # If not, it may be an untracked position from before crash
                    order_id = order.get("id")

                    # Check if any tracked position uses this order ID
                    already_tracked = any(
                        p.get("order_id") == order_id
                        for p in self.open_positions.values()
                    )

                    # If not tracked, warn the operator to investigate
                    if not already_tracked:
                        logger.warning(
                            f"[PositionTracker] Untracked filled order found | "
                            f"Symbol: {symbol} | "
                            f"ID: {order_id} | "
                            f"Side: {order.get('side')} | "
                            f"Filled: {order.get('filled')} @ {order.get('average')}"
                        )

            except ccxt.NetworkError as e:
                # Network issue during reconciliation — log and continue
                logger.error(f"[PositionTracker] Network error reconciling {symbol}: {e}")

            except Exception as e:
                # Any other error — log but don't crash startup
                logger.error(f"[PositionTracker] Reconciliation error for {symbol}: {e}")

        # Mark reconciliation as complete — bot loop can now start
        self.is_reconciled = True

        # Log summary of what we found
        logger.info(
            f"[PositionTracker] Reconciliation complete | "
            f"Tracked positions: {list(self.open_positions.keys())}"
        )


    # ============================================================
    # GET ALL OPEN POSITIONS
    # Returns a copy of the positions dict for safe iteration
    # ============================================================
    def get_all_positions(self):
        # Return a copy — prevents modification during iteration
        # Iterating over the live dict while modifying it causes RuntimeError
        return dict(self.open_positions)