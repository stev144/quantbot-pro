# ============================================================
# bot/engines/simulation.py
# Simulation Helpers — used exclusively by the backtester
#
# These three functions simulate realistic trade execution
# costs during backtesting. They do NOT touch the live exchange.
#
# Why a separate file?
# execution_engine.py handles live trading — it talks to Binance.
# These functions simulate what execution WOULD have cost.
# Mixing simulation code into live trading code causes confusion
# and import errors — so they live here instead.
#
# Used by:
#   bot/backtesting/backtester.py
# NOT used by:
#   bot/engines/execution_engine.py (live trading)
# ============================================================

import logging    # Consistent logging across all modules

# claude code changed: SLIPPAGE_RATE/FEE_RATE now single-sourced from
# bot.config.execution_costs instead of defined locally here (architecture
# audit finding M3) — see that module's comments for the full rationale.
from bot.config.execution_costs import SLIPPAGE_RATE, FEE_RATE

# Create logger for this module
logger = logging.getLogger(__name__)


# ============================================================
# APPLY SLIPPAGE
# Simulates the price difference between signal and actual fill
#
# On entry:  you pay slightly MORE than the signal price
# On exit:   you receive slightly LESS than the signal price
# This models the market impact of placing a real order
# ============================================================
def apply_slippage(price: float, direction: str, is_entry: bool, slippage_rate: float = None) -> float:
    """
    Adjusts a price to simulate realistic slippage.

    price:      the raw signal price (entry or exit level)
    direction:  "LONG" or "SHORT" — determines which way slippage hurts
    is_entry:   True if entering the trade, False if exiting
    slippage_rate: claude code changed: new — Kraken Multi-Venue Execution,
        Step 14. Optional override; None (the default) falls back to the
        module-level SLIPPAGE_RATE exactly as before this param existed —
        every existing caller that doesn't pass it is unaffected. Lets
        Backtester simulate a different venue's real slippage assumption
        (bot.config.execution_costs.get_venue_execution_costs(), Step 8)
        against the SAME price series, not a different one.

    Returns the slippage-adjusted price as a float.

    How slippage works in practice:
    - Entering a LONG:  you buy slightly above signal price (market moves up)
    - Exiting a LONG:   you sell slightly below signal price (market moves down)
    - Entering a SHORT: you sell slightly below signal price
    - Exiting a SHORT:  you buy slightly above signal price
    In all cases, slippage costs you money — never helps you.
    """

    # Guard: price must be a valid positive number
    if not price or price <= 0:
        logger.warning(f"[Simulation] Invalid price for slippage: {price}")
        return price    # Return as-is — cannot apply slippage to bad price

    # claude code changed: was bare SLIPPAGE_RATE — Step 14.
    rate = slippage_rate if slippage_rate is not None else SLIPPAGE_RATE

    if direction == "LONG":

        if is_entry:
            # Entering long — we buy at a slightly higher price
            # Market moves against us as our order fills
            adjusted = price * (1 + rate)

        else:
            # Exiting long — we sell at a slightly lower price
            # Market moves against us as our sell order fills
            adjusted = price * (1 - rate)

    elif direction == "SHORT":

        if is_entry:
            # Entering short — we sell at a slightly lower price
            # Market moves against us as our sell order fills
            adjusted = price * (1 - rate)

        else:
            # Exiting short — we buy back at a slightly higher price
            # Market moves against us as our buy order fills
            adjusted = price * (1 + rate)

    else:
        # Unknown direction — return price unchanged and log warning
        logger.warning(f"[Simulation] Unknown direction: {direction}. No slippage applied.")
        adjusted = price    # Return unchanged

    # Round to 8 decimal places — matches exchange precision
    return round(adjusted, 8)


# ============================================================
# CALCULATE FEES
# Simulates the trading fees charged by the exchange
# Binance charges on both the entry and exit of every trade
# ============================================================
def calc_fees(entry_price: float, exit_price: float, quantity: float, fee_rate: float = None) -> float:
    """
    Calculates total fees for a complete trade (entry + exit).

    entry_price: the actual fill price on entry (after slippage)
    exit_price:  the actual fill price on exit (after slippage)
    quantity:    number of units traded
    fee_rate: claude code changed: new — Kraken Multi-Venue Execution,
        Step 14. Optional override; None (the default) falls back to the
        module-level FEE_RATE exactly as before this param existed. Same
        venue-cost-model-swap purpose as apply_slippage()'s slippage_rate.

    Returns total fees in USDT as a float.

    Fee calculation:
    - Entry fee = entry_price × quantity × FEE_RATE
    - Exit fee  = exit_price  × quantity × FEE_RATE
    - Total     = entry fee + exit fee

    Example:
    - Entry: 500 NEAR @ $5.00 = $2,500 × 0.001 = $2.50 fee
    - Exit:  500 NEAR @ $5.20 = $2,600 × 0.001 = $2.60 fee
    - Total fees: $5.10
    """

    # Guard: all inputs must be valid positive numbers
    if not all([entry_price, exit_price, quantity]):
        logger.warning("[Simulation] Invalid inputs for fee calculation")
        return 0.0    # Return zero — cannot calculate fees from bad data

    if entry_price <= 0 or exit_price <= 0 or quantity <= 0:
        logger.warning(
            f"[Simulation] Non-positive values in fee calc: "
            f"entry={entry_price} exit={exit_price} qty={quantity}"
        )
        return 0.0    # Return zero — protect against bad data

    # claude code changed: was bare FEE_RATE — Step 14.
    rate = fee_rate if fee_rate is not None else FEE_RATE

    # Entry fee — charged when opening the position
    entry_fee = entry_price * quantity * rate

    # Exit fee — charged when closing the position
    exit_fee = exit_price * quantity * rate

    # Total fees for the complete round trip
    total_fees = entry_fee + exit_fee

    # Return rounded to 6 decimal places — sufficient precision
    return round(total_fees, 6)


# ============================================================
# CALCULATE PNL
# Calculates gross profit or loss for a trade
# Gross = before fees are subtracted
# Net   = gross - fees (calculated separately in backtester)
# ============================================================
def calculate_pnl(
    direction:   str,
    entry_price: float,
    exit_price:  float,
    quantity:    float
) -> float:
    """
    Calculates gross P&L for a trade before fees.

    direction:   "LONG" or "SHORT"
    entry_price: price the trade was opened at (after slippage)
    exit_price:  price the trade was closed at (after slippage)
    quantity:    number of units traded

    Returns gross profit or loss as a float (can be negative).

    P&L logic:
    - LONG:  profit = (exit - entry) × qty  [positive if price went up]
    - SHORT: profit = (entry - exit) × qty  [positive if price went down]
    """

    # Guard: all inputs must be valid
    if not all([direction, entry_price, exit_price, quantity]):
        logger.warning("[Simulation] Invalid inputs for P&L calculation")
        return 0.0    # Return zero — cannot calculate from bad data

    if entry_price <= 0 or exit_price <= 0 or quantity <= 0:
        logger.warning(
            f"[Simulation] Non-positive values in P&L calc: "
            f"entry={entry_price} exit={exit_price} qty={quantity}"
        )
        return 0.0    # Protect against bad data

    if direction == "LONG":
        # Long trade: profit when exit price is higher than entry
        # Loss when exit price is lower than entry
        pnl = (exit_price - entry_price) * quantity

    elif direction == "SHORT":
        # Short trade: profit when exit price is lower than entry
        # Loss when exit price is higher than entry
        pnl = (entry_price - exit_price) * quantity

    else:
        # Unknown direction — return zero and log
        logger.warning(f"[Simulation] Unknown direction for P&L: {direction}")
        return 0.0    # Cannot calculate P&L without valid direction

    # Return rounded to 6 decimal places
    return round(pnl, 6)