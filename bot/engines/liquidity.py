# ============================================================
# bot/engines/liquidity.py
# claude code changed: new file — Kraken Multi-Venue Execution, Step 9.
# Pure, venue-agnostic liquidity math computed from the normalized order
# book shape ExchangeAdapter.get_order_book() returns (bot/engines/
# exchange_adapter.py). Kept separate from the adapters themselves,
# mirroring this codebase's existing split between raw execution
# (OrderManager) and derived calculation (bot/engines/simulation.py's
# pure fee/slippage/PnL functions) — this is the same kind of pure
# function, just for liquidity instead of cost.
#
# Directly closes a real, previously-honest gap: bot/views/terminal_data.py
# currently reports {"available": False, "reason": "No volume/order-book-
# based liquidity metric exists in the codebase"} rather than fake a
# number. This module makes a real one computable. NOT wired into
# terminal_data.py by this step — that's dashboard/UI work (Step 18),
# not this step's job, which only builds the capability.
# ============================================================

from dataclasses import dataclass


@dataclass(frozen=True)
class LiquiditySnapshot:
    symbol: str
    best_bid: float
    best_ask: float
    mid_price: float
    spread_abs: float
    spread_pct: float
    bid_depth_notional: float
    ask_depth_notional: float
    depth_band_pct: float


def compute_liquidity_snapshot(order_book: dict, depth_band_pct: float = 0.005) -> LiquiditySnapshot:
    """
    Computes spread and depth-within-band from a normalized order book
    (the dict shape ExchangeAdapter.get_order_book() returns — bids/asks
    already trimmed to [price, amount] pairs regardless of venue).

    depth_band_pct: fraction of mid price defining the depth window on
    each side (default 0.005 = 0.5%). bid_depth_notional sums price*amount
    for bids at or above mid*(1-depth_band_pct); ask_depth_notional sums
    price*amount for asks at or below mid*(1+depth_band_pct).
    """
    bids = order_book["bids"]
    asks = order_book["asks"]

    if not bids or not asks:
        raise ValueError(
            f"compute_liquidity_snapshot requires a non-empty order book on "
            f"both sides — got {len(bids)} bids, {len(asks)} asks"
        )

    best_bid = bids[0][0]
    best_ask = asks[0][0]

    if best_ask <= best_bid:
        raise ValueError(
            f"crossed or invalid order book: best_bid={best_bid} >= best_ask={best_ask}"
        )

    mid_price = (best_bid + best_ask) / 2
    spread_abs = best_ask - best_bid
    spread_pct = spread_abs / mid_price

    bid_floor = mid_price * (1 - depth_band_pct)
    ask_ceiling = mid_price * (1 + depth_band_pct)

    bid_depth_notional = sum(price * amount for price, amount in bids if price >= bid_floor)
    ask_depth_notional = sum(price * amount for price, amount in asks if price <= ask_ceiling)

    return LiquiditySnapshot(
        symbol=order_book["symbol"],
        best_bid=best_bid,
        best_ask=best_ask,
        mid_price=mid_price,
        spread_abs=spread_abs,
        spread_pct=spread_pct,
        bid_depth_notional=bid_depth_notional,
        ask_depth_notional=ask_depth_notional,
        depth_band_pct=depth_band_pct,
    )
