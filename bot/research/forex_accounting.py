# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/forex_accounting.py
# Workstream B — Forex position sizing and account-currency accounting
#
# claude code changed: new file — 2026-10-16. Pure accounting functions for forex DCA research.
# All formulas are independently verifiable against known-answer references (see test_forex_dca_instruments.py).
#
# Design decisions:
#   - These are PURE FUNCTIONS — no state, no I/O, no exchange connections.
#   - Units are EXPLICIT throughout. Every function name says what unit its result is in.
#   - No crypto percentage-fee reuse. FX costs are spread + commission + financing (see ForexCostProfile).
#   - Division-by-zero is a hard error (ValueError), never silently returned as 0 or NaN.
#   - Causal constraint: quote_to_account_rate must be the CONTEMPORANEOUS rate available at fill time,
#     never a look-ahead rate. Callers are responsible for this; this module cannot enforce it, but the
#     parameter name and docstring make the requirement explicit.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from bot.research.forex_instrument_spec import ForexInstrumentSpec  # claude code changed: reuse, not re-derive


def lots_from_allocation(
    allocated_capital: float,
    entry_price: float,
    spec: ForexInstrumentSpec,
    leverage: float,
) -> float:
    """
    Convert allocated capital (in account currency) to a tradeable lot size.

    Formula (standard retail FX lot sizing):
        notional_exposure = allocated_capital * leverage
        base_units        = notional_exposure / entry_price        [for USD-quoted pairs: USD / (quote/base) = base]
        lots              = base_units / contract_size

    For EUR/USD: allocated=$1000, leverage=30, entry=1.1000, contract_size=100_000
        notional = $30,000
        base_units = $30,000 / 1.1000 = 27,272.7... EUR
        lots = 27,272.7 / 100,000 = 0.2727... → rounds DOWN to nearest volume_increment

    Returns the lot size rounded DOWN to the nearest spec.volume_increment (never rounds up —
    conservative sizing is the right default when capital is scarce).

    Raises ValueError for non-positive inputs, missing spec fields, or if the result falls
    below spec.min_volume (position would be unexecutable at this broker).
    """  # claude code changed: new — Workstream B independent reference calculation
    if allocated_capital <= 0:
        raise ValueError(f"allocated_capital must be > 0, got {allocated_capital}")
    if entry_price <= 0:
        raise ValueError(f"entry_price must be > 0, got {entry_price}")
    if leverage <= 0:
        raise ValueError(f"leverage must be > 0, got {leverage}")
    if spec.contract_size is None:
        raise ValueError(f"ForexInstrumentSpec for {spec.canonical_symbol} has no contract_size — cannot size a position")
    if spec.contract_size <= 0:
        raise ValueError(f"contract_size must be > 0, got {spec.contract_size}")

    notional = allocated_capital * leverage
    base_units = notional / entry_price
    raw_lots = base_units / spec.contract_size

    # Round DOWN to volume_increment (conservative — never over-size)
    increment = spec.volume_increment if spec.volume_increment is not None else 0.01
    lots = math.floor(raw_lots / increment) * increment
    lots = round(lots, 10)  # floating-point cleanup

    min_vol = spec.min_volume if spec.min_volume is not None else 0.01
    if lots < min_vol:
        raise ValueError(
            f"lots_from_allocation: computed {lots:.4f} lots for {spec.canonical_symbol} is below "
            f"min_volume={min_vol}. allocated_capital={allocated_capital}, leverage={leverage}, "
            f"entry_price={entry_price}. Increase allocation or leverage."
        )

    return lots


def pnl_quote_currency(
    side: str,
    base_qty: float,
    entry_price: float,
    exit_price: float,
    spec: Optional[ForexInstrumentSpec] = None,
) -> float:
    """
    Gross P&L in the quote currency (before costs and currency conversion).

    Standard formula (independent of whether the account currency is USD):
        LONG:  P&L_quote = (exit_price − entry_price) × base_qty
        SHORT: P&L_quote = (entry_price − exit_price) × base_qty

    base_qty is in BASE currency units (lots × contract_size).
    result is in QUOTE currency units.

    EUR/USD example (1 lot LONG, entry 1.1000, exit 1.1010):
        base_qty = 100_000 EUR
        P&L_quote = (1.1010 - 1.1000) × 100_000 = 0.0010 × 100_000 = $100 USD

    USD/JPY example (1 lot SHORT, entry 150.00, exit 149.50):
        base_qty = 100_000 USD
        P&L_quote = (150.00 - 149.50) × 100_000 = 50 × 100_000 = ¥5,000,000 JPY
        (This must be converted to account currency separately with pnl_account_currency)

    Raises ValueError for invalid side or non-positive base_qty.
    spec is accepted but currently unused (reserved for future pair-specific overrides).
    """  # claude code changed: new — Workstream B core P&L formula
    if side not in ("LONG", "SHORT"):
        raise ValueError(f"side must be 'LONG' or 'SHORT', got {side!r}")
    if base_qty <= 0:
        raise ValueError(f"base_qty must be > 0, got {base_qty}")
    if entry_price <= 0:
        raise ValueError(f"entry_price must be > 0, got {entry_price}")
    if exit_price <= 0:
        raise ValueError(f"exit_price must be > 0, got {exit_price}")

    if side == "LONG":
        return (exit_price - entry_price) * base_qty
    else:  # SHORT
        return (entry_price - exit_price) * base_qty


def pnl_account_currency(
    pnl_quote: float,
    quote_to_account_rate: float,
    spec: Optional[ForexInstrumentSpec] = None,
) -> float:
    """
    Convert quote-currency P&L to account currency.

    For a USD-account:
        EUR/USD: quote IS USD → rate = 1.0, result = pnl_quote unchanged
        USD/JPY: quote is JPY → rate = USD/JPY at fill time (e.g. 150.0)
                 result = pnl_quote_jpy / 150.0 (JPY → USD)
        GBP/JPY: quote is JPY → rate = GBP/JPY at fill time... NO.
                 The cross P&L is GBP/JPY P&L in JPY; to convert JPY to USD, need USD/JPY rate.
                 Caller MUST supply the correct contemporaneous rate.

    CAUSAL REQUIREMENT: quote_to_account_rate must be from the SAME candle as the fill,
    never a future rate. This function has no way to enforce this — it is the caller's
    responsibility. The parameter name makes the requirement explicit.

    Raises ValueError for a non-positive rate.
    """  # claude code changed: new — Workstream B currency conversion
    if quote_to_account_rate <= 0:
        raise ValueError(f"quote_to_account_rate must be > 0, got {quote_to_account_rate}")
    return pnl_quote / quote_to_account_rate


def gross_to_net_pnl(
    gross_pnl: float,
    commission_paid: float,
    spread_cost: float,
    financing_cost: float,
) -> float:
    """
    Net P&L from gross P&L by deducting all known costs.

    All inputs must be in the same currency (account currency).
    spread_cost and commission_paid are always positive (costs, not P&L adjustments).
    financing_cost is always positive (a cost; overnight swap is a cost for a held position).

    Accounting identity: net_pnl = gross_pnl - commission_paid - spread_cost - financing_cost

    Raises ValueError if any cost is negative (a negative cost would be P&L, not a cost;
    this catches sign-convention errors).
    """  # claude code changed: new — Workstream B accounting identity
    if commission_paid < 0:
        raise ValueError(f"commission_paid must be >= 0 (a cost), got {commission_paid}. Use gross_pnl for positive adjustments.")
    if spread_cost < 0:
        raise ValueError(f"spread_cost must be >= 0 (a cost), got {spread_cost}. Use gross_pnl for positive adjustments.")
    if financing_cost < 0:
        raise ValueError(f"financing_cost must be >= 0 (a cost), got {financing_cost}. Use gross_pnl for positive adjustments.")
    return gross_pnl - commission_paid - spread_cost - financing_cost


def weighted_average_entry(fills: List[Tuple[float, float]]) -> float:
    """
    Weighted-average entry price from a list of (price, quantity) fills.

    The correct formula — same as dca_engine._OpenCycle.weighted_avg_price:
        WAE = total_cost / total_quantity
            = sum(price_i × qty_i) / sum(qty_i)

    NOT the dollar-weighted average of prices (sum(cost_i × price_i) / total_cost),
    which is a known incorrect formula this project previously fixed in
    dca_engine.compute_theoretical_ladder() — see that function's own docstring.

    Example: 0.5 lots at 1.1000, 0.5 lots at 1.0980
        WAE = (1.1000 × 0.5 + 1.0980 × 0.5) / (0.5 + 0.5)
            = (0.5500 + 0.5490) / 1.0
            = 1.0990

    Raises ValueError for empty fills or non-positive prices/quantities.
    """  # claude code changed: new — correct formula, referenced against dca_engine fixed version
    if not fills:
        raise ValueError("fills must be non-empty")
    total_cost = 0.0
    total_qty = 0.0
    for i, (price, qty) in enumerate(fills):
        if price <= 0:
            raise ValueError(f"fill[{i}].price must be > 0, got {price}")
        if qty <= 0:
            raise ValueError(f"fill[{i}].qty must be > 0, got {qty}")
        total_cost += price * qty
        total_qty += qty
    if total_qty == 0:
        raise ValueError("total quantity is zero — cannot compute weighted average")
    return total_cost / total_qty


def spread_cost_account_currency(
    lots: float,
    spec: ForexInstrumentSpec,
    quote_to_account_rate: float = 1.0,
) -> Optional[float]:
    """
    Approximate one-way spread cost in account currency for a given lot size.

    spread_cost = spread_typical_pips × pip_size × contract_size × lots / quote_to_account_rate

    Returns None when spec.spread_typical_pips or spec.pip_size or spec.contract_size is unavailable.
    This represents the cost of crossing the spread once (e.g. on entry OR exit, not both).
    A round-trip trade crosses the spread twice; multiply by 2 for total spread cost.
    """  # claude code changed: new — Workstream C/B spread cost helper
    if spec.spread_typical_pips is None or spec.pip_size is None or spec.contract_size is None:
        return None
    pip_value = spec.pip_value_in_quote(lots)
    if pip_value is None:
        return None
    spread_in_quote = spec.spread_typical_pips * pip_value  # spread_pips × pip_value_per_lot
    if quote_to_account_rate <= 0:
        raise ValueError(f"quote_to_account_rate must be > 0, got {quote_to_account_rate}")
    return spread_in_quote / quote_to_account_rate
