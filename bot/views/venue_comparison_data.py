# ============================================================
# bot/views/venue_comparison_data.py
# claude code changed: new file — real-time Binance vs. Kraken execution
# comparison, using only already-built, already-tested capability: Step 11's
# compare_venues() (real order-book-depth cost estimate) and Step 17's
# assess_venue_readiness() (real dry-run/connectivity/auth classification).
# No new venue-data logic lives here — this module only presents what
# already exists.
#
# Deliberately DOES make live exchange calls (order book + ticker fetches)
# inside this view — unlike bot/views/terminal_data.py's functions, which
# avoid live calls in a synchronous page load on purpose. That's the right
# choice there (a fast page loaded on every visit); this page's entire
# purpose is a real-time cross-venue comparison, so real latency for real
# numbers is the correct trade-off here, not something to avoid — same
# precedent as the existing Portfolio Backtester view, which already takes
# real time (~30s) for the same reason.
# ============================================================

import ccxt

from bot.engines.binance_adapter import BinanceAdapter
from bot.engines.execution_comparison import compare_venues
from bot.engines.kraken_adapter import KrakenAdapter
from bot.engines.venue_readiness import assess_venue_readiness

DEFAULT_SYMBOL = "XRP/USDT"
DEFAULT_QUANTITY = 100.0


def get_venue_comparison_table(symbol: str = DEFAULT_SYMBOL, quantity: float = DEFAULT_QUANTITY) -> dict:
    """
    Real-time comparison of executing `quantity` units of `symbol` (buy
    side) on Binance vs. Kraken right now — real order book, real fees,
    real readiness. Returns {"available": False, "reason": ...} only if
    both venues fail to produce any comparable data at all (e.g. a bad
    symbol) — a single venue being unavailable is reported per-row, not
    as a page-level failure, since "Kraken doesn't list this symbol" is
    itself real, useful information (Step 5's finding), not an error.
    """
    adapters = [
        BinanceAdapter(ccxt.binance(), dry_run=True),
        KrakenAdapter(ccxt.kraken(), dry_run=True),
    ]

    try:
        quotes = compare_venues(adapters, symbol, "buy", quantity)
    except Exception as e:
        return {
            "available": False,
            "reason": f"Comparison failed for both venues: {type(e).__name__}: {e}",
            "symbol": symbol,
            "quantity": quantity,
        }

    readiness_by_venue = {}
    for adapter in adapters:
        try:
            readiness_by_venue[adapter.venue_id] = assess_venue_readiness(adapter).classification.value
        except Exception:
            readiness_by_venue[adapter.venue_id] = "UNKNOWN"

    rows = []
    for q in quotes:
        rows.append({
            "venue_id": q.venue_id,
            "available": q.available,
            "unavailable_reason": q.unavailable_reason,
            "readiness": readiness_by_venue.get(q.venue_id, "UNKNOWN"),
            "best_bid": q.best_bid,
            "best_ask": q.best_ask,
            "spread_pct": round(q.spread_pct * 100, 4) if q.spread_pct is not None else None,
            "fee_rate_pct": round(q.fee_rate * 100, 3) if q.fee_rate is not None else None,
            "estimated_fill_price": q.estimated_fill_price,
            "estimated_slippage_pct": round(q.estimated_slippage_pct * 100, 4) if q.estimated_slippage_pct is not None else None,
            "estimated_total_cost_pct": round(q.estimated_total_cost_pct * 100, 4) if q.estimated_total_cost_pct is not None else None,
        })

    cheapest = next((r["venue_id"] for r in rows if r["available"]), None)

    return {
        "available": True,
        "symbol": symbol,
        "quantity": quantity,
        "rows": rows,
        "cheapest_venue": cheapest,
    }
