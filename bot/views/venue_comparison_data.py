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

import time

import ccxt

from bot.config.execution_costs import VENUE_FEE_PROVENANCE   # claude code changed: 2026-10-22
from bot.engines.binance_adapter import BinanceAdapter
from bot.engines.coinbase_adapter import CoinbaseAdapter   # claude code changed: 2026-10-22 — third venue
from bot.engines.execution_comparison import compare_venues
from bot.engines.kraken_adapter import KrakenAdapter
from bot.engines.venue_readiness import assess_venue_readiness

# claude code changed: 2026-10-22 — reuse each PUBLIC (credential-less) ccxt exchange for up to an hour so its loaded
# market list is not re-downloaded on every page view (Kraken's alone took several seconds). Order books, tickers and
# readiness checks are still fetched live on every request.
_EXCHANGE_TTL_S = 3600
_exchanges: dict = {}


def _exchange(name: str):
    ex, born = _exchanges.get(name, (None, 0.0))
    if ex is None or time.time() - born > _EXCHANGE_TTL_S:
        ex = getattr(ccxt, name)()
        _exchanges[name] = (ex, time.time())
    return ex


def _quote_ccy(symbol: str) -> str:
    return symbol.partition("/")[2]


def _plain(x, max_decimals: int = 8, min_decimals: int = 0):
    """claude code changed: 2026-10-22 — display string without exponent notation ("6.93e-06" → "0.00000693"),
    trailing zeros trimmed down to min_decimals; None stays None. Used for the animated card values."""
    if x is None:
        return None
    s = f"{float(x):.{max_decimals}f}"
    if "." in s:
        head, tail = s.split(".")
        tail = tail.rstrip("0")
        if len(tail) < min_decimals:
            tail = tail.ljust(min_decimals, "0")
        s = head + ("." + tail if tail else "")
    return s

def _price_decimals(rows) -> int:   # claude code changed: 2026-10-26 — one precision for every price on the page
    """Decimals needed by the most precise venue's best bid/ask (2–8). Each venue quotes on its own tick (Binance
    1.2910, Kraken 1.29113), and _plain() also dropped trailing zeros, so the cards showed 1.2909 next to 1.291 and an
    8-decimal average fill; one shared precision keeps the prices comparable digit for digit."""
    need = 2
    for r in rows:
        for x in (r.get("best_bid"), r.get("best_ask")):
            s = _plain(x)
            if s and "." in s:
                need = max(need, len(s.split(".")[1]))
    return min(need, 8)


def _fixed(x, decimals: int):   # claude code changed: 2026-10-26 — fixed decimals, trailing zeros kept; None stays None
    return None if x is None else f"{float(x):.{decimals}f}"


DEFAULT_SYMBOL = "XRP/USDT"
DEFAULT_QUANTITY = 100.0


def get_venue_comparison_table(symbol: str = DEFAULT_SYMBOL, quantity: float = DEFAULT_QUANTITY) -> dict:
    """
    Real-time comparison of executing `quantity` units of `symbol` (buy
    side) on Binance vs. Kraken vs. Coinbase right now — real order book, real fees,
    real readiness. Returns {"available": False, "reason": ...} only if
    both venues fail to produce any comparable data at all (e.g. a bad
    symbol) — a single venue being unavailable is reported per-row, not
    as a page-level failure, since "Kraken doesn't list this symbol" is
    itself real, useful information (Step 5's finding), not an error.
    """
    adapters = [   # claude code changed: 2026-10-22 — Coinbase added; dry_run=True, public exchanges, no credentials
        BinanceAdapter(_exchange("binance"), dry_run=True),
        KrakenAdapter(_exchange("kraken"), dry_run=True),
        CoinbaseAdapter(_exchange("coinbase"), dry_run=True),
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
        fee_info = VENUE_FEE_PROVENANCE.get(q.venue_id, {"verified": False, "basis": "no fee provenance recorded"})
        rows.append({
            "venue_id": q.venue_id,
            "market": q.symbol,                                                     # claude code changed: 2026-10-22 — the market actually quoted
            "quote_mismatch": _quote_ccy(q.symbol) != _quote_ccy(symbol),           # claude code changed: e.g. AAVE/USD vs AAVE/USDT
            "fee_verified": bool(fee_info.get("verified")),                         # claude code changed: 2026-10-22
            "fee_basis": fee_info.get("basis", ""),                                 # claude code changed: 2026-10-22
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

    # claude code changed: 2026-10-22 — rank by the ALL-IN amount actually paid, not by "fee % + slippage vs the
    # venue's OWN mid". The old percentage ignored price-level differences BETWEEN venues (a venue whose whole book sits
    # higher could rank cheapest). all-in = quantity × walked fill price × (1 + taker fee). Only rows quoted in the
    # requested currency are ranked — a USD-quoted row cannot be compared without a USDT/USD rate, so it is shown but
    # left unranked (never silently converted).
    for r in rows:
        if r["available"] and r["estimated_fill_price"] is not None and r["fee_rate_pct"] is not None:
            unit = r["estimated_fill_price"] * (1 + r["fee_rate_pct"] / 100.0)
            r["all_in_unit_price"] = unit
            r["all_in_total"] = unit * quantity
            r["ranked"] = not r["quote_mismatch"]
        else:
            r["all_in_unit_price"] = r["all_in_total"] = None
            r["ranked"] = False
    ranked = sorted((r for r in rows if r["ranked"]), key=lambda r: r["all_in_total"])
    best = ranked[0]["all_in_total"] if ranked else None
    for pos, r in enumerate(ranked, start=1):
        r["rank"] = pos
        r["extra_vs_cheapest"] = r["all_in_total"] - best
        r["extra_vs_cheapest_pct"] = (r["all_in_total"] / best - 1) * 100 if best else None
    for r in rows:
        r.setdefault("rank", None)
        r.setdefault("extra_vs_cheapest", None)
        r.setdefault("extra_vs_cheapest_pct", None)
        r["quote_currency"] = _quote_ccy(r["market"])
    rows = ranked + [r for r in rows if r["available"] and not r["ranked"]] + [r for r in rows if not r["available"]]
    best_price = min((r["estimated_fill_price"] for r in ranked), default=None)
    for r in ranked:   # explain the result: price advantage vs fee
        r["best_book_price"] = best_price is not None and r["estimated_fill_price"] == best_price
    cheapest = ranked[0]["venue_id"] if ranked else None
    px = _price_decimals([r for r in rows if r["available"]])   # claude code changed: 2026-10-26
    for r in rows:   # claude code changed: 2026-10-22 — display strings for the animated cards
        r["disp"] = {
            "best_bid": _fixed(r["best_bid"], px), "best_ask": _fixed(r["best_ask"], px),   # claude code changed: 2026-10-26 — shared precision (was _plain)
            "fill": _fixed(r["estimated_fill_price"], px), "spread": _plain(r["spread_pct"], 4),
            "fee": _plain(r["fee_rate_pct"], 3), "slippage": _plain(r["estimated_slippage_pct"], 4),
            "all_in": _plain(r["all_in_total"], 4, 2), "extra": _plain(r["extra_vs_cheapest"], 4, 2),
            "extra_pct": _plain(r["extra_vs_cheapest_pct"], 3),
        }

    return {
        "available": True,
        "symbol": symbol,
        "quantity": quantity,
        "rows": rows,
        "cheapest_venue": cheapest,
        "any_quote_mismatch": any(r["quote_mismatch"] for r in rows if r["available"]),   # claude code changed: 2026-10-22
        "requested_quote": _quote_ccy(symbol),   # claude code changed: 2026-10-22 — the currency the ranking is in
    }
