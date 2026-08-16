# ============================================================
# bot/engines/order_manager.py
# claude code changed: fixed header — was "order_manager_final.py", a
# stale filename left over from a prior copy/rename (this file has
# always actually been order_manager.py; nothing was ever named
# order_manager_final.py in the repo)
# Written from a quantitative algorithmic trading perspective
#
# A quant understands that markets are dynamic and adversarial.
# Exchanges throttle, orders hang, fees vary, and latency spikes
# at exactly the worst moments (high volatility, news events).
# This file is built with that reality in mind.
#
# Patches applied from v2 review:
# 1. Silent None return scenarios replaced with typed exception
# 2. Rate limit handling separated with hard backoff
# 3. Fee normalised to clean float for P&L calculation
# ============================================================

import time        # For delays, backoff, and timeout tracking
import logging     # Structured logging — never use print in production

import ccxt        # Exchange library — provides typed exceptions per error class

# claude code changed: new import — fixes architecture audit finding C-2
# (issue 2). Reuses the exact same single-sourced cost constants the
# backtester's bot/engines/simulation.py already uses, so dry-run fills and
# backtest fills model execution cost identically.
from bot.config.execution_costs import SLIPPAGE_RATE, FEE_RATE


# ---------------------------------------------------------------
# Module-level logger — writes to whatever handler you configure
# In production this should write to a rotating file handler
# e.g. logging.handlers.RotatingFileHandler("trades.log")
# ---------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------
# Custom exception classes — typed exceptions are critical
# They let the strategy engine respond differently to each failure
# rather than catching a generic Exception and guessing what failed
# ---------------------------------------------------------------

class OrderUnconfirmedException(Exception):
    """
    Raised when an order's final state cannot be confirmed.
    The order may be live on the exchange — do NOT re-enter.
    Strategy engine must pause and reconcile before next trade.
    """
    pass


class OrderStillOpenException(Exception):
    """
    Raised when timeout recovery finds the order is still open.
    The order exists but has not filled — strategy engine must
    decide whether to wait, cancel, or re-price the order.
    This is common during low liquidity or wide spread conditions.
    """
    pass


class RateLimitException(Exception):
    """
    Raised when the exchange rate limit is hit and max backoff
    has been exhausted. Strategy engine should pause all activity
    and alert the operator — this is a systemic issue.
    """
    pass


# ---------------------------------------------------------------
# OrderManager — all execution passes through this class
# Designed to be instantiated once and reused across all trades
# ---------------------------------------------------------------
class OrderManager:
    """
    Production-grade order execution manager.
    A quant perspective on why this class exists:
    The gap between a signal firing and a fill being confirmed
    is where most live trading systems break. Slippage, partial
    fills, rate limits, and network failures all live in this gap.
    This class owns that gap entirely so strategy logic stays clean.
    """

    def __init__(
        self,
        exchange,
        max_retries=3,
        retry_delay=2,
        confirm_timeout=30,
        rate_limit_backoff=60,
        dry_run=False
    ):
        # The ccxt exchange instance — already authenticated
        self.exchange = exchange

        # claude code changed: new — when True, place_order()/place_stop_loss()
        # never call the authenticated create_order() endpoint. Instead they
        # synthesize a fill so the rest of the pipeline (TradeLogger, position
        # tracking) still runs end-to-end. Fixes architecture audit finding
        # C-2 (issue 2) — DRY_RUN previously skipped this whole class.
        self.dry_run = dry_run

        # Max attempts before declaring an order as failed
        # 3 is sufficient — more than this suggests a systemic issue
        self.max_retries = max_retries

        # Base delay in seconds — used as multiplier for exponential backoff
        # Exponential backoff prevents hammering a degraded exchange
        self.retry_delay = retry_delay

        # Maximum seconds to wait for a fill confirmation
        # 30s is generous — most fills confirm within 3-5s in normal markets
        # During high volatility or illiquid alts, fills can hang longer
        self.confirm_timeout = confirm_timeout

        # Accept fills at 95%+ — common on illiquid pairs like NEAR
        # where the full quantity may not be available at one price level
        self.min_fill_pct = 0.95
        
        # Seconds between status polling — 1.5s respects most exchange limits
        # Binance allows ~1200 requests/minute — polling every 1.5s is safe
        self.poll_interval = 1.5

        # Hard backoff when rate limited — 60s minimum before resuming
        # Retrying faster when rate-limited escalates the ban risk
        self.rate_limit_backoff = rate_limit_backoff

        # Maximum number of rate limit retries before raising hard exception
        # After 3 rate limit hits in a row, something is structurally wrong
        self.rate_limit_max_retries = 3

    # ============================================================
    # PLACE ORDER — PUBLIC ENTRY POINT
    # All order placement flows through here regardless of type
    # ============================================================
    def place_order(self, symbol, side, quantity, price=None):
        """
        Places an order and returns a normalised result dict.
        Raises typed exceptions on failure — never returns None.
        From a quant perspective:
        Market orders fill faster but guarantee worse slippage.
        Limit orders control entry price but risk non-fills.
        This method handles both — strategy logic decides which.
        """

        # Log intent with full context before touching the exchange
        logger.info(
            f"[OrderManager] {side.upper()} | {symbol} | "
            f"Qty: {quantity} | Price: {price or 'MARKET'}"
        )

        # claude code changed: new — dry run never calls create_order(), which
        # is a private authenticated endpoint that would either fail without
        # real API keys or place a real order with them. Returns a simulated
        # fill instead. Fixes architecture audit finding C-2 (issue 2).
        if self.dry_run:
            return self._simulate_fill(symbol, side, quantity, price)

        # Preserve intended price before any mutation
        # Used downstream for slippage calculation
        intended_price = price

        # Track consecutive rate limit hits across attempts
        rate_limit_hits = 0

        # Attempt the order up to max_retries times
        for attempt in range(self.max_retries):

            try:
                # Determine order type from whether a price was given
                order_type = "market" if price is None else "limit"

                # Submit order to the exchange via ccxt unified API
                order = self.exchange.create_order(
                    symbol=symbol,        # e.g. "NEAR/USDT"
                    type=order_type,      # "market" or "limit"
                    side=side.lower(),    # normalise to lowercase for ccxt
                    amount=quantity,      # units calculated by position sizer
                    price=price           # None = market, float = limit
                )

                # Log the exchange-assigned order ID immediately
                # This ID is the key to all subsequent status checks
                logger.info(
                    f"[OrderManager] Submitted | ID: {order.get('id')} | "
                    f"Attempt: {attempt + 1}/{self.max_retries}"
                )

                # Hand off to confirmation logic and return its result
                return self._confirm_order(order, intended_price)


            # --------------------------------------------------------
            # HARD FAILURES — abort immediately, never retry
            # These errors mean the order is fundamentally invalid.
            # Retrying would waste time or cause duplicate orders.
            # --------------------------------------------------------

            except ccxt.InsufficientFunds as e:
                # Account balance too low — retrying won't create funds
                # From a risk perspective: this should never happen if
                # position sizing is correctly implemented upstream
                logger.error(f"[OrderManager] Insufficient funds — aborting: {e}")
                raise  # Propagate as-is to strategy engine


            except ccxt.InvalidOrder as e:
                # Order parameters rejected by exchange
                # Common causes: quantity below minimum, price precision wrong
                # These are configuration bugs — fix the strategy, not the retry
                logger.error(f"[OrderManager] Invalid order parameters — aborting: {e}")
                raise  # Propagate as-is


            # --------------------------------------------------------
            # RATE LIMIT — separate from network errors by design
            # A quant insight: rate limits often spike during high
            # volatility when every bot on the exchange fires at once.
            # Hard backoff here is the only correct response.
            # --------------------------------------------------------

            except ccxt.RateLimitExceeded as e:
                # Increment the rate limit counter for this order attempt
                rate_limit_hits += 1

                # Log with severity — rate limits in live trading are serious
                logger.warning(
                    f"[OrderManager] Rate limit hit ({rate_limit_hits}/"
                    f"{self.rate_limit_max_retries}): {e}"
                )

                # If we've hit the rate limit too many times, raise hard
                # Something is structurally wrong — halt all trading activity
                if rate_limit_hits >= self.rate_limit_max_retries:
                    logger.critical(
                        "[OrderManager] Rate limit max retries exceeded — "
                        "halting. Check API key permissions and request frequency."
                    )
                    raise RateLimitException(
                        f"Rate limit exceeded {rate_limit_hits} times. "
                        f"System requires manual intervention."
                    )

                # Otherwise back off hard — 60 seconds minimum
                # This is not exponential because rate limits need a full reset
                logger.info(f"[OrderManager] Backing off {self.rate_limit_backoff}s for rate limit...")
                time.sleep(self.rate_limit_backoff)


            # --------------------------------------------------------
            # RETRYABLE FAILURES — network and exchange errors
            # These are transient — worth retrying with backoff
            # Exponential backoff: 2s → 4s → 8s
            # Gives the exchange time to recover between attempts
            # --------------------------------------------------------

            except (ccxt.NetworkError, ccxt.ExchangeError) as e:
                # Calculate exponential backoff based on attempt number
                sleep_time = self.retry_delay * (2 ** attempt)

                # Log with attempt context for post-trade review
                logger.warning(
                    f"[OrderManager] Retryable error on attempt "
                    f"{attempt + 1}/{self.max_retries}: {e}"
                )
                logger.info(f"[OrderManager] Retrying in {sleep_time}s...")

                # Wait the calculated backoff before next attempt
                time.sleep(sleep_time)

            # --------------------------------------------------------
            # UNKNOWN ERRORS — catch-all defensive layer
            # In live markets, unexpected exceptions do occur.
            # Log thoroughly and retry once with base delay.
            # --------------------------------------------------------

            except Exception as e:
                logger.error(
                    f"[OrderManager] Unexpected error on attempt "
                    f"{attempt + 1}: {type(e).__name__}: {e}"
                )
                # Use base delay — we don't know the cause so don't escalate
                time.sleep(self.retry_delay)

        # All retry attempts exhausted — order has definitively failed
        logger.critical(
            f"[OrderManager] Order FAILED after {self.max_retries} attempts | "
            f"{symbol} {side} {quantity}"
        )

        # Raise a clear exception — strategy engine must handle this explicitly
        raise Exception(
            f"Order failed after {self.max_retries} attempts: "
            f"{symbol} {side} {quantity}"
        )


    # ============================================================
    # CONFIRM ORDER — INTERNAL
    # Polls the exchange until the order is filled, cancelled,
    # or the timeout is reached. Quantitative note: in normal
    # liquid markets (BTC, ETH) this resolves in 1-3 polls.
    # On illiquid alts (NEAR, smaller caps) allow more time.
    # ============================================================
    def _confirm_order(self, order, intended_price=None):

        # Extract order ID — this is our primary tracking key
        order_id = order.get("id")

        # Extract symbol — required by most exchanges for fetch_order
        symbol = order.get("symbol")

        # Log the start of confirmation with full identifiers
        logger.info(f"[OrderManager] Confirming | ID: {order_id} | Symbol: {symbol}")

        # Record start time for timeout enforcement
        start_time = time.time()

        # Poll until timeout
        while time.time() - start_time < self.confirm_timeout:

            try:
                # Fetch current order state from exchange
                # Passing symbol is required on most exchanges — don't omit it
                status = self.exchange.fetch_order(order_id, symbol)

                # Extract status string for branching logic
                order_status = status.get("status")


                # ------------------------------------------------
                # FULLY FILLED — the ideal outcome
                # Build and return the normalised result object
                # ------------------------------------------------
                if order_status == "closed":
                    logger.info(f"[OrderManager] Order {order_id} confirmed FILLED")
                    return self._build_result(status, intended_price)


                # ------------------------------------------------
                # CANCELLED — exchange rejected or cancelled it
                # Common during high volatility or market halts
                # Do not re-enter until cause is understood
                # ------------------------------------------------
                if order_status == "canceled":
                    logger.error(f"[OrderManager] Order {order_id} CANCELLED by exchange")

                    # Raise typed exception — strategy engine should not
                    # automatically re-enter when an order is cancelled
                    raise OrderUnconfirmedException(
                        f"Order {order_id} was cancelled by the exchange. "
                        f"Investigate before re-entering."
                    )


                # ------------------------------------------------
                # PARTIAL FILL — order is open but partially filled
                # On illiquid pairs this is common at larger sizes
                # Accept if above threshold, otherwise keep polling
                # ------------------------------------------------
                if order_status == "open":

                    # Calculate what percentage of the order has filled
                    filled = status.get("filled", 0)       # Units filled so far
                    total = status.get("amount", 1)        # Total units requested
                    fill_pct = filled / total if total > 0 else 0  # Fill ratio

                    # If fill is close enough to complete, accept and move on
                    # Holding out for the last 5% on illiquid pairs can cost more
                    # in time and opportunity than the missing fraction is worth
                    if fill_pct >= self.min_fill_pct:
                        logger.warning(
                            f"[OrderManager] Accepting partial fill: "
                            f"{fill_pct * 100:.1f}% of order {order_id}"
                        )
                        return self._build_result(status, intended_price)

                    # Below threshold — log progress and continue polling
                    logger.info(
                        f"[OrderManager] Partial fill {fill_pct * 100:.1f}% "
                        f"— continuing to poll..."
                    )

                # Wait before next poll — respects exchange rate limits
                time.sleep(self.poll_interval)

            except (OrderUnconfirmedException, OrderStillOpenException):
                # Re-raise our own typed exceptions without catching them here
                raise

            except Exception as e:
                # Exchange status check failed — log and retry the poll
                logger.warning(f"[OrderManager] Status check error: {e}")
                time.sleep(self.poll_interval)

        # --------------------------------------------------------
        # TIMEOUT REACHED — order state is unknown
        # This is the most dangerous scenario in live trading.
        # An unknown order means a position may be open that the
        # strategy engine doesn't know about. Recovery is critical.
        # --------------------------------------------------------
        logger.critical(
            f"[OrderManager] TIMEOUT reached for order {order_id} "
            f"after {self.confirm_timeout}s — initiating recovery"
        )

        # Attempt recovery before declaring failure
        return self._recover_order(order_id, symbol, intended_price)


    # ============================================================
    # BUILD RESULT — INTERNAL
    # Constructs a clean, normalised result dict from raw exchange data.
    # Every consumer of this class gets the same structure — no
    # parsing raw ccxt responses scattered across the codebase.
    # ============================================================
    def _build_result(self, status, intended_price):

        # Extract actual average fill price from exchange response
        # Use 0.0 as fallback — never let a None propagate to P&L calc
        actual_fill = status.get("average") or 0.0

        # Extract filled quantity — may differ from requested on partial fills
        filled_qty = status.get("filled", 0)

        # --------------------------------------------------------
        # FEE NORMALISATION — patch #3
        # ccxt returns fees as a nested dict: {"cost": 0.0025, "currency": "USDT"}
        # Your P&L calculator needs a clean float, not a dict.
        # Passing raw fee objects causes silent bugs in accounting.
        # --------------------------------------------------------

        # Safely extract the fee object — default to empty dict if missing
        fee_data = status.get("fee") or {}

        # Extract the cost value as a float — this is the actual fee in quote currency
        fee_usdt = float(fee_data.get("cost", 0.0))

        # Extract the currency for logging and audit purposes
        fee_currency = fee_data.get("currency", "USDT")

        # --------------------------------------------------------
        # SLIPPAGE CALCULATION
        # Slippage is the difference between intended and actual price.
        # In quantitative terms this is a transaction cost that erodes
        # your theoretical edge. Track it per trade to monitor execution quality.
        # High slippage on a strategy that backtested well is a red flag.
        # --------------------------------------------------------

        # Only calculate if both prices are valid and non-zero
        if intended_price and actual_fill > 0:
            # Express as percentage of intended price
            slippage_pct = abs(actual_fill - intended_price) / intended_price * 100
        else:
            # Cannot calculate without intended price (e.g. pure market order)
            slippage_pct = 0.0

        # Log the full execution summary for post-trade review
        logger.info(
            f"[OrderManager] Execution summary | "
            f"Fill: {actual_fill} | Intended: {intended_price} | "
            f"Slippage: {slippage_pct:.4f}% | "
            f"Fee: {fee_usdt} {fee_currency} | "
            f"Qty filled: {filled_qty}"
        )

        # Return a clean, typed result dict — every field has a defined type
        # This is the contract between OrderManager and the rest of the system
        return {
            "order":          status,           # Full raw exchange response (for audit)
            "order_id":       status.get("id"), # Exchange order ID
            "fill_price":     actual_fill,      # float — actual execution price
            "intended_price": intended_price,   # float — target price at signal time
            "slippage_pct":   slippage_pct,     # float — % deviation from intended
            "filled_qty":     filled_qty,       # float — actual units filled
            "fee_usdt":       fee_usdt,         # float — normalised fee in quote currency
            "fee_currency":   fee_currency,     # str   — fee currency (usually USDT)
            "symbol":         status.get("symbol"),  # str — trading pair
            "side":           status.get("side"),    # str — "buy" or "sell"
            "timestamp":      status.get("timestamp"),  # i/nt — exchange timestamp (ms)
        }

    # ============================================================
    # SIMULATE FILL — INTERNAL
    # claude code changed: new — fixes architecture audit finding C-2
    # (issue 2). Only called when self.dry_run is True (see place_order()).
    # Builds the same normalised result dict _build_result() returns, so
    # every downstream consumer (ExecutionEngine, TradeLogger) treats a
    # simulated fill identically to a real one. Reuses SLIPPAGE_RATE/
    # FEE_RATE from bot.config.execution_costs — the same single-sourced
    # constants the backtester uses, so dry-run cost modelling matches
    # backtest cost modelling.
    # ============================================================
    def _simulate_fill(self, symbol, side, quantity, price):
        """
        Synthesizes an immediate full fill without touching the exchange.

        price: the limit price for entries, or None for market exit orders —
               when None, a live ticker price is fetched (fetch_ticker is a
               public endpoint, safe to call without real API keys) so the
               simulated fill still reflects real market conditions.
        """

        # Market order (exits use price=None) — use a real live reference
        # price so the simulated fill isn't detached from actual market data
        if price is None:
            try:
                price = self.exchange.fetch_ticker(symbol).get("last")
            except Exception as e:
                logger.warning(
                    f"[OrderManager] DRY RUN — ticker fetch failed for {symbol}, "
                    f"cannot simulate market fill: {e}"
                )
                price = None

        if not price or price <= 0:
            logger.error(
                f"[OrderManager] DRY RUN — no valid reference price for {symbol}, "
                f"cannot simulate fill"
            )
            raise Exception(f"DRY RUN fill simulation failed: no price for {symbol}")

        # Adverse slippage: buys fill slightly above reference, sells slightly
        # below — same direction real slippage always costs the trader
        if side.lower() == "buy":
            fill_price = round(price * (1 + SLIPPAGE_RATE), 8)
        else:
            fill_price = round(price * (1 - SLIPPAGE_RATE), 8)

        fee_usdt = round(fill_price * quantity * FEE_RATE, 6)

        slippage_pct = abs(fill_price - price) / price * 100

        order_id = f"DRYRUN-{symbol.replace('/', '')}-{int(time.time() * 1000)}"

        logger.info(
            f"[OrderManager] DRY RUN fill simulated | {side.upper()} | {symbol} | "
            f"Fill: {fill_price} | Qty: {quantity} | Fee: {fee_usdt} | ID: {order_id}"
        )

        return {
            "order":          None,           # no raw exchange response — nothing was sent
            "order_id":       order_id,
            "fill_price":     fill_price,
            "intended_price": price,
            "slippage_pct":   slippage_pct,
            "filled_qty":     quantity,        # dry run assumes full fill
            "fee_usdt":       fee_usdt,
            "fee_currency":   "USDT",
            "symbol":         symbol,
            "side":           side.lower(),
            "timestamp":      int(time.time() * 1000),
        }


    # ============================================================
    # RECOVER ORDER — INTERNAL
    # Called when confirmation times out.
    # A quant's perspective: timeout recovery is not optional.
    # An untracked open position is one of the most dangerous states
    # a live trading system can be in — especially on leveraged accounts.
    # ============================================================
    def _recover_order(self, order_id, symbol, intended_price):

        # ---- Attempt 1: Direct fetch ----
        # The order may have filled just after the timeout was triggered
        try:
            logger.info(f"[OrderManager] Recovery attempt 1: direct fetch {order_id}")

            # Fetch the current state one more time
            status = self.exchange.fetch_order(order_id, symbol)

            # If it filled during/after timeout — return it normally
            if status.get("status") == "closed":
                logger.warning(
                    f"[OrderManager] Order {order_id} was filled — "
                    f"recovered successfully after timeout"
                )
                return self._build_result(status, intended_price)

            # If the order is still sitting open on the exchange
            if status.get("status") == "open":
                logger.warning(
                    f"[OrderManager] Order {order_id} still OPEN after timeout. "
                    f"Strategy engine must decide: wait, cancel, or reprice."
                )

                # ---- PATCH: raise typed exception instead of returning None ----
                # Returning None here caused a silent failure in v2.
                # The strategy engine would receive None, not know an order is live,
                # and potentially fire another entry signal — doubling the position.
                # Raising OrderStillOpenException forces explicit handling upstream.
                raise OrderStillOpenException(
                    f"Order {order_id} on {symbol} is still open after "
                    f"{self.confirm_timeout}s timeout. "
                    f"Do not re-enter. Cancel or monitor manually."
                )

        except (OrderStillOpenException, OrderUnconfirmedException):
            # Re-raise our own typed exceptions — don't swallow them here
            raise

        except Exception as e:
            logger.error(f"[OrderManager] Recovery attempt 1 failed: {e}")


        # ---- Attempt 2: Scan open orders ----
        # If direct fetch failed, check the open orders list as a fallback
        try:
            logger.info(f"[OrderManager] Recovery attempt 2: scanning open orders")

            # Fetch all currently open orders for this symbol
            open_orders = self.exchange.fetch_open_orders(symbol)

            # Search for our specific order ID in the open orders list
            for o in open_orders:
                if o.get("id") == order_id:
                    # Found it — still open, raise typed exception
                    logger.warning(
                        f"[OrderManager] Order {order_id} found in open orders — "
                        f"still not filled"
                    )
                    raise OrderStillOpenException(
                        f"Order {order_id} confirmed open via open_orders scan. "
                        f"Manual intervention required."
                    )

        except (OrderStillOpenException, OrderUnconfirmedException):
            # Re-raise our typed exceptions
            raise

        except Exception as e:
            logger.error(f"[OrderManager] Recovery attempt 2 failed: {e}")


        # ---- Final fallback: truly unknown state ----
        # We cannot determine what happened to this order.
        # This is the most dangerous state. Log as critical.
        # The operator must check the exchange dashboard manually.
        logger.critical(
            f"[OrderManager] Order {order_id} state UNKNOWN after all recovery attempts. "
            f"Check exchange dashboard immediately. Do not place new orders on {symbol}."
        )

        # Raise as unconfirmed — strategy engine must halt activity on this symbol
        raise OrderUnconfirmedException(
            f"Order {order_id} final state unknown. "
            f"Manual reconciliation required before resuming."
        )


    # ============================================================
    # PLACE STOP LOSS — PUBLIC
    # claude code changed: new method — fixes architecture audit finding C2.
    # Unlike place_order(), this places a RESTING order meant to sit
    # unfilled on the exchange until price reaches it — the opposite of
    # place_order()'s assumption that an order should fill within
    # confirm_timeout. Calling place_order() for a stop would incorrectly
    # treat "still open" as a timeout and raise OrderStillOpenException on
    # every stop that simply hasn't triggered yet.
    #
    # Why this matters: previously stop-loss protection existed only as a
    # software comparison inside ExecutionEngine.manage_positions(), polled
    # once per candle. If the bot process crashed, lost network, or was
    # simply between polls when price moved against an open position,
    # nothing protected that position on the exchange itself. A resting
    # STOP_LOSS_LIMIT order fixes that — the exchange enforces it
    # independently of whether the bot is even running.
    #
    # Scope note: take-profit is deliberately NOT placed as a matching
    # resting order by this fix. Binance spot reserves the position's full
    # quantity for each resting sell order independently — there is no
    # shared "either one fills, the other cancels" reservation without
    # using Binance's separate OCO/order-list endpoint, which ccxt only
    # exposes as a raw, exchange-specific implicit binding (no unified
    # createOrder support) that could not be validated without live/testnet
    # credentials. Placing both a resting stop AND a resting take-profit
    # for the same quantity would fail with insufficient balance on the
    # second order. Take-profit remains software-polled in
    # ExecutionEngine.manage_positions(), same as before this fix — missing
    # a take-profit costs upside, missing a stop-loss risks capital, so the
    # stop is the side that actually needed exchange-side enforcement.
    # ============================================================
    def place_stop_loss(self, symbol, side, quantity, stop_price):
        """
        Places a resting stop-loss order directly on the exchange.

        symbol:     e.g. "NEAR/USDT"
        side:       "sell" to protect a long position, "buy" to protect a short
        quantity:   units to close if triggered — should match the position size
        stop_price: trigger price. The order also executes as a limit at this
                    same price once triggered — a deliberate simplification.
                    A fast gap through this level could leave the limit leg
                    unfilled, the same risk any stop-limit order carries on
                    any exchange (vs. a stop-market, which always fills but
                    at an unknown, potentially much worse, price).

        Returns a normalised dict describing the resting order (NOT a fill —
        this order is not expected to fill immediately). Raises the same
        typed exceptions place_order() uses on hard failures, since the
        caller must know loudly if a position was left without exchange-side
        protection.
        """

        logger.info(
            f"[OrderManager] Placing STOP LOSS | {side.upper()} | {symbol} | "
            f"Qty: {quantity} | Stop: {stop_price}"
        )

        # claude code changed: new — dry run never places a real resting
        # order. Returns order_id=None (not an exception — nothing failed,
        # this is expected dry-run behaviour), which ExecutionEngine stores
        # as position["sl_order_id"] = None. That's the exact same value
        # already used when a real place_stop_loss() call fails, so
        # manage_positions()'s existing software-polled stop-loss fallback
        # protects the simulated position with zero changes to that method.
        # Fixes architecture audit finding C-2 (issue 2).
        if self.dry_run:
            logger.info(
                f"[OrderManager] DRY RUN — no resting stop placed for {symbol}; "
                f"software-polled fallback in manage_positions() will protect it"
            )
            return {
                "order_id":   None,
                "stop_price": stop_price,
                "status":     "dry_run_skipped",
                "symbol":     symbol,
                "side":       side.lower(),
            }

        try:
            # claude code changed: ccxt maps type="limit" + a stopLossPrice
            # param onto Binance's native STOP_LOSS_LIMIT order type — verified
            # against the installed ccxt 4.5.51 binance.py createOrder()
            # implementation directly, not assumed from documentation.
            order = self.exchange.create_order(
                symbol=symbol,
                type="limit",
                side=side.lower(),
                amount=quantity,
                price=stop_price,
                params={"stopLossPrice": stop_price},
            )

            logger.info(
                f"[OrderManager] Stop loss resting on exchange | "
                f"ID: {order.get('id')} | Trigger: {stop_price}"
            )

            return {
                "order_id":   order.get("id"),
                "stop_price": stop_price,
                "status":     order.get("status", "open"),
                "symbol":     symbol,
                "side":       side.lower(),
            }

        except ccxt.InsufficientFunds as e:
            # Balance won't change by retrying — abort immediately
            logger.error(f"[OrderManager] Insufficient funds placing stop loss — aborting: {e}")
            raise

        except ccxt.InvalidOrder as e:
            # Bad params won't fix themselves on retry — abort immediately
            logger.error(f"[OrderManager] Invalid stop-loss order parameters — aborting: {e}")
            raise

        except Exception as e:
            # Any other failure — caller must know protection failed to attach
            logger.critical(
                f"[OrderManager] Stop-loss placement FAILED for {symbol}: "
                f"{type(e).__name__}: {e}. Position has NO exchange-side protection."
            )
            raise


    # ============================================================
    # CHECK RESTING ORDER — PUBLIC
    # claude code changed: new method — lets ExecutionEngine ask "has this
    # resting stop-loss filled yet?" without going through place_order()'s
    # fill-polling/timeout machinery, which does not apply to an order that
    # is *supposed* to stay open indefinitely until triggered.
    # ============================================================
    def check_resting_order(self, order_id, symbol, intended_price=None):
        """
        Checks a resting order's current status on the exchange.

        Returns a normalised fill result dict (same shape as place_order()'s
        return value) if the order has filled. Returns None if it is still
        open/untriggered — the normal, expected state for a stop that
        hasn't been hit — or if it was cancelled (expected when the other
        exit path fires and cleans this order up via cancel_order()).
        """

        try:
            status = self.exchange.fetch_order(order_id, symbol)

            if status.get("status") == "closed":
                logger.info(f"[OrderManager] Resting order {order_id} has FILLED")
                return self._build_result(status, intended_price)

            if status.get("status") == "canceled":
                logger.info(f"[OrderManager] Resting order {order_id} is cancelled")
                return None

            # Still open/untriggered — the normal, expected state
            return None

        except Exception as e:
            logger.warning(
                f"[OrderManager] Could not check resting order {order_id}: {e}"
            )
            return None


    # ============================================================
    # CANCEL ORDER — PUBLIC
    # Used when strategy logic decides to abandon an unfilled order.
    # Common scenario: limit order placed, price moved away,
    # strategy wants to cancel and re-evaluate the setup.
    # ============================================================
    def cancel_order(self, order_id, symbol):

        # Log the cancellation intent
        logger.info(f"[OrderManager] Cancelling order...! {order_id} on {symbol}")

        try:
            # Send cancellation to exchange
            result = self.exchange.cancel_order(order_id, symbol)

            # Log success with order ID for audit trail
            logger.info(f"[OrderManager] Order {order_id} cancelled successfully")

            # Return exchange response — may contain final fill info
            return result

        except ccxt.OrderNotFound:
            # Order doesn't exist — likely already filled or previously cancelled
            # From a quant perspective: verify position state after this scenario
            logger.warning(
                f"[OrderManager] Order {order_id} not found — "
                f"may have already filled. Verify position state."
            )
            return None

        except Exception as e:
            # Cancellation failed for an unknown reason — log and return None
            # Caller must check open orders to confirm cancellation status
            logger.error(
                f"[OrderManager] Cancel failed for {order_id}: {e}. "
                f"Verify order state manually."
            )
            return None