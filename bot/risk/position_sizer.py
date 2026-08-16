# ============================================================
# risk/position_sizer.py
# Position Sizer — calculates how many units to buy or sell
#
# This file answers one question before every single trade:
# "Given my balance, entry price, and stop loss —
#  how many coins should I buy to risk exactly 1%?"
#
# Why does this deserve its own file?
# Because position sizing IS risk management.
# A strategy with a great edge can still blow an account
# if positions are sized incorrectly.
# Keeping this isolated means you can change risk % in one place
# and it automatically affects every trade the bot takes.
# ============================================================

import logging    # Structured logging — consistent with rest of system
import math       # For math.floor() — always round DOWN on position size

# claude code changed: new — single-sourced risk constants (architecture
# audit finding M3). Was risk_pct=0.01 / self.max_risk_pct=0.05 hardcoded
# here, independently duplicated in ExecutionEngine's PositionSizer(...)
# call and backtester.py's RISK_PER_TRADE constant, with nothing keeping
# the three in sync.
from bot.config.risk import DEFAULT_RISK_PCT, MAX_RISK_PCT

# Create logger for this module
logger = logging.getLogger(__name__)

class PositionSizer:
    """
    Calculates position size based on fixed fractional risk.

    Fixed fractional means you risk the same PERCENTAGE of your
    account on every trade — not the same dollar amount.

    Why percentage and not fixed dollars?
    - After a loss streak: smaller sizes protect remaining capital
    - After a win streak: larger sizes compound gains naturally
    - The account grows and shrinks in proportion — not linearly

    Example at 1% risk:
        Balance:       $5,000
        Risk amount:   $50     (1% of $5,000)
        Entry:         $5.00
        Stop:          $4.90
        Risk per unit: $0.10   (entry - stop)
        Quantity:      500     ($50 / $0.10)
    """

    def __init__(self, risk_pct=DEFAULT_RISK_PCT):
        # Risk percentage per trade — default is 1% (0.01), from bot.config.risk
        # 0.01 = 1%, 0.02 = 2%, 0.005 = 0.5%
        # Never set this above 0.02 (2%) on a live account
        self.risk_pct = risk_pct

        # Minimum quantity guard — some exchanges reject very small orders
        # Set to 0.0001 — adjust per exchange minimum lot size if needed
        self.min_quantity = 0.0001

        # Maximum risk percentage allowed — hard safety cap, from bot.config.risk
        # Prevents accidental misconfiguration (e.g. passing 10 instead of 0.1)
        self.max_risk_pct = MAX_RISK_PCT    # 5% absolute maximum per trade

        # Log confirmation that sizer is initialised with chosen risk level
        logger.info(
            f"[PositionSizer] Initialised | "
            f"Risk per trade: {self.risk_pct * 100:.2f}%"
        )


    # ============================================================
    # CALCULATE — MAIN METHOD
    # Called by ExecutionEngine before every entry order
    # Returns the quantity of base asset to buy or sell
    # ============================================================
    def calculate(self, balance, entry_price, stop_price):
        """
        Calculates the correct position size for a trade.

        balance:     current free account balance in USDT
        entry_price: intended entry price for the trade
        stop_price:  stop loss price (the "sl" value from signal dict)

        Returns float quantity (units of base asset) or 0.0 on failure.
        Never returns a negative number.
        Never returns a quantity that risks more than max_risk_pct.
        """

        # ----------------------------------------------------------
        # VALIDATION — check all inputs before calculating anything
        # Bad inputs produce bad sizes — validate first, always
        # ----------------------------------------------------------

        # Guard: balance must be a positive number
        if not balance or balance <= 0:
            logger.error(
                f"[PositionSizer] Invalid balance: {balance}. "
                f"Cannot calculate size."
            )
            return 0.0  # Return zero — execution engine will skip the trade

        # Guard: entry price must be positive
        if not entry_price or entry_price <= 0:
            logger.error(
                f"[PositionSizer] Invalid entry price: {entry_price}. "
                f"Cannot calculate size."
            )
            return 0.0  # Return zero — bad entry price means bad trade

        # Guard: stop price must be positive
        if not stop_price or stop_price <= 0:
            logger.error(
                f"[PositionSizer] Invalid stop price: {stop_price}. "
                f"Cannot calculate size."
            )
            return 0.0  # Return zero — cannot risk without a valid stop

        # Guard: entry and stop cannot be the same price
        # If they are equal, risk per unit is zero — causes division by zero
        if entry_price == stop_price:
            logger.error(
                f"[PositionSizer] Entry and stop are the same price: "
                f"{entry_price}. Stop must differ from entry."
            )
            return 0.0  # Return zero — zero-distance stop is invalid

        # ----------------------------------------------------------
        # SAFETY CAP — prevent misconfigured risk percentages
        # If someone accidentally sets risk_pct=10 instead of 0.1
        # this cap prevents a catastrophic oversized position
        # ----------------------------------------------------------
        effective_risk_pct = self.risk_pct    # Start with configured value

        if self.risk_pct > self.max_risk_pct:
            # Risk is set dangerously high — cap it and warn loudly
            logger.warning(
                f"[PositionSizer] Risk {self.risk_pct * 100:.1f}% exceeds "
                f"maximum {self.max_risk_pct * 100:.1f}%. "
                f"Capping to {self.max_risk_pct * 100:.1f}% for safety."
            )
            effective_risk_pct = self.max_risk_pct    # Apply the cap

        # ----------------------------------------------------------
        # STEP 1: Calculate dollar amount to risk on this trade
        # This is the maximum loss if stop loss is hit exactly
        # ----------------------------------------------------------
        risk_amount = balance * effective_risk_pct

        # Log the dollar risk for audit trail
        logger.debug(
            f"[PositionSizer] Balance: ${balance:.2f} | "
            f"Risk %: {effective_risk_pct * 100:.2f}% | "
            f"Risk $: ${risk_amount:.2f}"
        )

        # ----------------------------------------------------------
        # STEP 2: Calculate risk per unit (distance from entry to stop)
        # This is how much money you lose per coin if stop is hit
        # abs() handles both long (entry > stop) and short (entry < stop)
        # ----------------------------------------------------------
        risk_per_unit = abs(entry_price - stop_price)

        # Log the per-unit risk for debugging
        logger.debug(
            f"[PositionSizer] Entry: {entry_price} | "
            f"Stop: {stop_price} | "
            f"Risk per unit: {risk_per_unit:.6f}"
        )

        # ----------------------------------------------------------
        # STEP 3: Calculate raw quantity
        # quantity = how many units we can buy within our risk budget
        # ----------------------------------------------------------
        raw_quantity = risk_amount / risk_per_unit

        # ----------------------------------------------------------
        # STEP 4: Round DOWN to safe decimal precision
        # Always floor (round down) — never round up on position size
        # Rounding up could cause you to risk slightly more than intended
        # math.floor with 4 decimal places suits most crypto pairs
        # For BTC you might use 6 decimal places — adjust per asset
        # ----------------------------------------------------------
        quantity = math.floor(raw_quantity * 10000) / 10000    # 4 decimal places

        # ----------------------------------------------------------
        # STEP 5: Check quantity meets minimum threshold
        # Some exchanges reject orders below their minimum lot size
        # ----------------------------------------------------------
        if quantity < self.min_quantity:
            logger.warning(
                f"[PositionSizer] Calculated quantity {quantity} is below "
                f"minimum {self.min_quantity}. "
                f"Balance may be too low or stop too close to entry."
            )
            return 0.0  # Return zero — execution engine will skip the trade

        # ----------------------------------------------------------
        # STEP 6: Final sanity check
        # Verify the actual dollar risk of this quantity matches expectation
        # Catches any floating point drift that could cause overexposure
        # ----------------------------------------------------------
        actual_risk = quantity * risk_per_unit      # What we'll actually risk
        actual_risk_pct = actual_risk / balance     # As a % of balance

        # Log the final sizing summary — visible in trades.log
        logger.info(
            f"[PositionSizer] Size calculated | "
            f"Entry: {entry_price} | "
            f"Stop: {stop_price} | "
            f"Qty: {quantity} | "
            f"Actual risk: ${actual_risk:.2f} "
            f"({actual_risk_pct * 100:.3f}% of ${balance:.2f})"
        )

        # Return the final quantity — ready for OrderManager
        return quantity


    # ============================================================
    # UPDATE RISK — Allows runtime adjustment of risk percentage
    # Useful if you want to reduce risk after a drawdown period
    # ============================================================
    def set_risk_pct(self, new_risk_pct):
        """
        Updates the risk percentage at runtime.
        Useful for reducing size during drawdown or increasing
        size when strategy is performing strongly.

        new_risk_pct: float e.g. 0.005 for 0.5%, 0.01 for 1%
        """

        # Validate the new value before applying it
        if new_risk_pct <= 0:
            logger.error(
                f"[PositionSizer] Invalid risk percentage: {new_risk_pct}. "
                f"Must be greater than zero."
            )
            return  # Reject — keep existing value

        # Hard cap — never allow above maximum even via this method
        if new_risk_pct > self.max_risk_pct:
            logger.warning(
                f"[PositionSizer] Requested risk {new_risk_pct * 100:.1f}% "
                f"exceeds maximum. Capping at {self.max_risk_pct * 100:.1f}%."
            )
            new_risk_pct = self.max_risk_pct    # Cap to maximum

        # Store old value for the log message
        old_risk_pct = self.risk_pct

        # Apply the new risk percentage
        self.risk_pct = new_risk_pct

        # Log the change — important for post-trade analysis
        logger.info(
            f"[PositionSizer] Risk updated | "
            f"{old_risk_pct * 100:.2f}% → {self.risk_pct * 100:.2f}%"
        )