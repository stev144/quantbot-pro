# ============================================================
# bot/engines/execution_result.py
# claude code changed: new file — Kraken Multi-Venue Execution, Step 7.
# Defines ExecutionResult/RestingOrderResult, the normalized "what
# happened" response objects parsed from ExchangeAdapter.place_order()/
# place_stop_loss()'s raw dict returns. The mirror image of Step 6's
# order_intent.py — that module goes typed-request -> dict-kwargs (to
# CALL an adapter); this one goes dict-result -> typed-response (from
# what an adapter RETURNS).
#
# Built directly from OrderManager._build_result()/_simulate_fill()/
# place_stop_loss() (bot/engines/order_manager.py) — confirmed identical
# shapes returned by both BinanceAdapter and KrakenAdapter (Step 4 built
# KrakenAdapter's own place_stop_loss() to deliberately match
# OrderManager's shape, since it isn't delegated like place_order() is).
#
# NOT wired into execution_engine.py/trade_logger.py yet — both still
# read dict keys directly, exactly as today. Wiring is Step 10's job
# (Execution Coordinator), same deferral reasoning as Step 6.
# ============================================================

from dataclasses import dataclass
from typing import Optional

from bot.engines.order_intent import OrderSide


@dataclass(frozen=True)
class ExecutionResult:
    """
    Normalized response from ExchangeAdapter.place_order() — a completed
    (or dry-run simulated) fill. Field names match OrderManager._build_result()/
    _simulate_fill()'s dict keys exactly, confirmed identical across both
    BinanceAdapter and KrakenAdapter since both delegate to the shared
    OrderManager for order placement.
    """

    order_id: str
    fill_price: float
    filled_qty: float
    fee_usdt: float
    fee_currency: str
    slippage_pct: float
    intended_price: Optional[float]
    symbol: str
    side: OrderSide
    timestamp: Optional[int]
    raw_order: Optional[dict] = None

    def __post_init__(self):
        # claude code changed: light shape-sanity checks, not business-rule
        # validation — this parses already-executed exchange output (unlike
        # OrderIntent, which validates a not-yet-submitted request), so it
        # only catches an obviously malformed/corrupt result, not
        # strategy-level invariants.
        if self.filled_qty < 0:
            raise ValueError(f"ExecutionResult filled_qty must be >= 0, got {self.filled_qty}")
        if self.fee_usdt < 0:
            raise ValueError(f"ExecutionResult fee_usdt must be >= 0, got {self.fee_usdt}")

    @classmethod
    def from_adapter_result(cls, result: dict) -> "ExecutionResult":
        """Parses the dict returned by ExchangeAdapter.place_order()."""
        return cls(
            order_id=result["order_id"],
            fill_price=result["fill_price"],
            filled_qty=result["filled_qty"],
            fee_usdt=result["fee_usdt"],
            fee_currency=result.get("fee_currency", "USDT"),
            slippage_pct=result.get("slippage_pct", 0.0) or 0.0,
            intended_price=result.get("intended_price"),
            symbol=result["symbol"],
            side=OrderSide(result["side"]),
            timestamp=result.get("timestamp"),
            raw_order=result.get("order"),
        )


@dataclass(frozen=True)
class RestingOrderResult:
    """
    Normalized response from ExchangeAdapter.place_stop_loss() — a resting
    order that is NOT expected to have filled yet (unlike ExecutionResult).
    Kept as a separate type from ExecutionResult for the same reason Step 6
    kept OrderIntent/StopLossIntent separate: place_stop_loss() is a
    genuinely distinct adapter method with its own, smaller response shape.
    """

    order_id: Optional[str]
    stop_price: float
    status: str
    symbol: str
    side: OrderSide

    @classmethod
    def from_adapter_result(cls, result: dict) -> "RestingOrderResult":
        """Parses the dict returned by ExchangeAdapter.place_stop_loss()."""
        return cls(
            order_id=result.get("order_id"),
            stop_price=result["stop_price"],
            status=result["status"],
            symbol=result["symbol"],
            side=OrderSide(result["side"]),
        )
