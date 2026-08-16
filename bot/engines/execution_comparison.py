# ============================================================
# bot/engines/execution_comparison.py
# claude code changed: new file — Kraken Multi-Venue Execution, Step 11.
# The "execution-comparison infrastructure" the spec's own non-negotiable
# constraints reference: "do NOT implement arbitrage before
# execution-comparison infrastructure works." This IS that infrastructure,
# and stops exactly there — no order routing, no automatic venue
# selection, no two-leg trade logic, no capturing a price differential.
# Purely: given a hypothetical order (symbol, side, quantity), compute a
# real, data-backed answer to "what would this actually cost on each venue
# right now." Nothing here places an order or makes a trading decision.
#
# Different from bot/engines/price_validator.py's PriceCrossChecker: that
# module compares raw last price only (fetch_ticker().get("last")) to flag
# a suspicious candle. This module uses real order-book depth (Step 9,
# get_order_book()/compute_liquidity_snapshot()) and real per-venue fees
# (Step 8, get_execution_costs()) to estimate what a SPECIFIC-SIZED order
# would actually cost to fill on each venue — a different, deeper question
# PriceCrossChecker was never designed to answer.
#
# NOT wired into execute_signal(), ExecutionCoordinator, bot_runner.py, or
# any dashboard view by this step — a standalone, tested capability, not
# consumed anywhere yet, matching every prior step's discipline.
# ============================================================

from dataclasses import dataclass
from typing import List, Optional

from bot.engines.exchange_adapter import ExchangeAdapter
from bot.engines.liquidity import compute_liquidity_snapshot


@dataclass(frozen=True)
class VenueQuote:
    venue_id: str
    symbol: str
    available: bool
    unavailable_reason: str
    best_bid: Optional[float]
    best_ask: Optional[float]
    spread_pct: Optional[float]
    fee_rate: Optional[float]
    estimated_fill_price: Optional[float]
    estimated_slippage_pct: Optional[float]
    estimated_total_cost_pct: Optional[float]


def _unavailable(venue_id: str, symbol: str, reason: str) -> VenueQuote:
    return VenueQuote(
        venue_id=venue_id, symbol=symbol, available=False, unavailable_reason=reason,
        best_bid=None, best_ask=None, spread_pct=None, fee_rate=None,
        estimated_fill_price=None, estimated_slippage_pct=None, estimated_total_cost_pct=None,
    )


def _walk_book(levels: list, quantity: float) -> Optional[float]:
    """
    Quantity-weighted average price to fill `quantity` by walking order
    book levels ([[price, amount], ...], best price first — already
    normalized by ExchangeAdapter.get_order_book(), Step 9). Returns None
    if the book's total depth across all given levels can't fill the
    requested quantity.
    """
    remaining = quantity
    notional = 0.0

    for price, amount in levels:
        take = min(remaining, amount)
        notional += take * price
        remaining -= take
        if remaining <= 0:
            return notional / quantity

    return None   # insufficient depth in the given levels


def get_venue_quote(adapter: ExchangeAdapter, symbol: str, side: str, quantity: float) -> VenueQuote:
    """
    Real-data quote for executing `quantity` of `symbol` as `side`
    ("buy"/"sell") on this venue right now.
    """
    if quantity <= 0:
        raise ValueError(f"quantity must be > 0, got {quantity}")

    # claude code changed: new — normalize_symbol() (Step 5) only does its
    # real availability/fallback check once exchange.markets is loaded;
    # without it, it gracefully passes the symbol through unchanged (by
    # design — callers are responsible for calling load_markets() first).
    # This function's whole point is an accurate quote, so it takes that
    # responsibility itself rather than silently degrading to a passthrough
    # if the caller forgot. load_markets() is cached by ccxt after the
    # first call, so this is a no-op on every call after the first.
    if not adapter.exchange.markets:
        adapter.exchange.load_markets()

    normalized = adapter.normalize_symbol(symbol)
    if normalized is None:
        return _unavailable(
            adapter.venue_id, symbol,
            f"{symbol} is not tradeable on {adapter.venue_id} (no direct or fallback listing)",
        )

    try:
        order_book = adapter.get_order_book(normalized)
    except Exception as e:
        return _unavailable(
            adapter.venue_id, normalized,
            f"order book fetch failed: {type(e).__name__}: {e}",
        )

    try:
        snapshot = compute_liquidity_snapshot(order_book)
    except ValueError as e:
        return _unavailable(adapter.venue_id, normalized, f"invalid order book: {e}")

    levels = order_book["asks"] if side == "buy" else order_book["bids"]
    fill_price = _walk_book(levels, quantity)

    if fill_price is None:
        return _unavailable(
            adapter.venue_id, normalized,
            f"insufficient order book depth to fill quantity {quantity}",
        )

    slippage_pct = abs(fill_price - snapshot.mid_price) / snapshot.mid_price
    costs = adapter.get_execution_costs(normalized)
    total_cost_pct = costs["fee_rate"] + slippage_pct

    return VenueQuote(
        venue_id=adapter.venue_id,
        symbol=normalized,
        available=True,
        unavailable_reason="",
        best_bid=snapshot.best_bid,
        best_ask=snapshot.best_ask,
        spread_pct=snapshot.spread_pct,
        fee_rate=costs["fee_rate"],
        estimated_fill_price=fill_price,
        estimated_slippage_pct=slippage_pct,
        estimated_total_cost_pct=total_cost_pct,
    )


def compare_venues(
    adapters: List[ExchangeAdapter], symbol: str, side: str, quantity: float
) -> List[VenueQuote]:
    """
    Returns one VenueQuote per adapter, sorted cheapest-total-cost-first
    (unavailable quotes sorted last). Pure comparison — returns data,
    makes no trading decision, places no order.
    """
    quotes = [get_venue_quote(adapter, symbol, side, quantity) for adapter in adapters]
    return sorted(
        quotes,
        key=lambda q: (not q.available, q.estimated_total_cost_pct if q.available else float("inf")),
    )
