# ============================================================
# bot/engines/order_intent.py
# claude code changed: new file — Kraken Multi-Venue Execution, Step 6.
# Defines OrderIntent/StopLossIntent, the normalized "what to order"
# request objects every venue's ExchangeAdapter.place_order()/
# place_stop_loss() (bot/engines/exchange_adapter.py) can consume
# identically. Kept as two separate types, not one with a discriminator,
# because the adapter contract itself already treats them as two genuinely
# distinct methods — Step 4 found place_stop_loss() cannot be a shared
# param-passthrough across venues the way place_order() can.
#
# Built directly from the real call sites in
# bot/engines/execution_engine.py — execute_signal()'s STEP 4-5 (entry),
# STEP 7B (stop-loss), and _close_position() (exit) — not a speculative
# generic design. The for_entry()/for_exit()/for_position() factories
# mirror that inline kwarg-building logic exactly.
#
# NOT wired into execution_engine.py yet. That file still builds raw
# kwargs directly to OrderManager, unchanged — exactly as Steps 2-4 left
# BinanceAdapter/KrakenAdapter unwired until a second venue existed to
# justify it. Here, wiring is deferred to Step 10 (Execution Coordinator),
# once there's an actual multi-venue dispatcher to route these into.
# ============================================================

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass(frozen=True)
class OrderIntent:
    """
    Normalized request to enter or exit a position — the venue-agnostic
    input to ExchangeAdapter.place_order(). Field names and types match
    that contract exactly (see to_place_order_kwargs()).
    """

    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None

    def __post_init__(self):
        # claude code changed: formalizes invariants that already had to
        # hold for correct behavior but were only enforced implicitly.
        # execute_signal() itself guards `if quantity <= 0: skip` before
        # ever reaching OrderManager — this makes that a hard construction
        # error instead of relying on every future caller to remember it.
        if self.quantity <= 0:
            raise ValueError(f"OrderIntent quantity must be > 0, got {self.quantity}")

        if self.order_type == OrderType.LIMIT and self.price is None:
            raise ValueError("OrderIntent order_type=LIMIT requires a price")

        # claude code changed: BinanceAdapter's existing test
        # (test_market_order_type_ignores_a_stray_price) proves a market
        # order's price is currently silently DROPPED by the adapter, not
        # rejected. Rejecting it here at construction time is a genuine
        # correctness improvement — a caller passing a price to a market
        # order almost certainly has a logic bug, not a valid intent.
        if self.order_type == OrderType.MARKET and self.price is not None:
            raise ValueError("OrderIntent order_type=MARKET must not carry a price")

    @classmethod
    def for_entry(cls, signal: dict, quantity: float) -> "OrderIntent":
        """
        Mirrors execute_signal()'s STEP 4-5 exactly: signal["signal"]
        ("BUY"/"SELL") lowercased into OrderSide, always a LIMIT order at
        signal["entry"] — matches this codebase's current entry behavior,
        where entries are always limit orders at the signal price.
        """
        return cls(
            symbol=signal["symbol"],
            side=OrderSide(signal["signal"].lower()),
            order_type=OrderType.LIMIT,
            quantity=quantity,
            price=signal["entry"],
        )

    @classmethod
    def for_exit(cls, symbol: str, position: dict) -> "OrderIntent":
        """
        Mirrors _close_position()'s exit-side inversion exactly: opposite
        side of the position's entry, always a MARKET order (guarantees
        fill — matches _close_position()'s own documented reasoning for
        why exits don't use limit orders).
        """
        exit_side = OrderSide.SELL if position["side"] == "BUY" else OrderSide.BUY
        return cls(
            symbol=symbol,
            side=exit_side,
            order_type=OrderType.MARKET,
            quantity=position["quantity"],
            price=None,
        )

    def to_place_order_kwargs(self) -> dict:
        """Exact kwarg match for ExchangeAdapter.place_order()."""
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "price": self.price,
        }


@dataclass(frozen=True)
class StopLossIntent:
    """
    Normalized request to place a resting stop-loss order — the
    venue-agnostic input to ExchangeAdapter.place_stop_loss(). Kept
    separate from OrderIntent; see module docstring for why.
    """

    symbol: str
    side: OrderSide
    quantity: float
    stop_price: float

    def __post_init__(self):
        if self.quantity <= 0:
            raise ValueError(f"StopLossIntent quantity must be > 0, got {self.quantity}")
        if self.stop_price <= 0:
            raise ValueError(f"StopLossIntent stop_price must be > 0, got {self.stop_price}")

    @classmethod
    def for_position(cls, symbol: str, signal: dict, filled_qty: float) -> "StopLossIntent":
        """
        Mirrors execute_signal()'s STEP 7B exactly: exit_side_for_stop is
        the opposite side of the entry signal, protecting the quantity
        that actually filled (not the requested quantity), at signal["sl"].
        """
        stop_side = OrderSide.SELL if signal["signal"] == "BUY" else OrderSide.BUY
        return cls(
            symbol=symbol,
            side=stop_side,
            quantity=filled_qty,
            stop_price=signal["sl"],
        )

    def to_place_stop_loss_kwargs(self) -> dict:
        """Exact kwarg match for ExchangeAdapter.place_stop_loss()."""
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "stop_price": self.stop_price,
        }
