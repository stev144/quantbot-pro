# ============================================================
# bot/engines/derivatives_data.py
# claude code changed: new file — funding rate + open interest data for
# this project's tracked symbols. OHLCV-only means every signal this
# project can produce is already priced in by every other participant
# reading the same candles; funding rate/OI are crypto-native, free via
# ccxt, and not derivable from spot OHLCV at all.
#
# This is deliberately its own standalone module rather than an
# ExchangeAdapter method: funding rate/OI only exist on Binance's USDT-
# margined perpetual futures market (a different API surface —
# fapi.binance.com, not api.binance.com — and a different, futures-only
# ccxt market type), not on the spot market BinanceAdapter/OrderManager/
# MarketData wrap. Kraken Futures is a genuinely separate product/API
# from Kraken spot and isn't covered here — this project's price data is
# Binance-sourced throughout (see CLAUDE.md's backtester venue-selection
# note), so Binance is also the natural, and only, source for this.
#
# Two real, verified constraints shape this module (confirmed against the
# live API before writing anything, not assumed):
#   - Funding rate history goes back years (confirmed 2023-2024 data
#     reachable in one paginated call) — plenty for the multi-year
#     STABILITY_WINDOWS feature_validator.py already tests against.
#   - Open interest history is capped at ~30 days by Binance itself
#     (confirmed empirically: 30 days back succeeds, 31 fails with a
#     -1130 "startTime is invalid" BadRequest) — not a bug in this module,
#     a real limit of Binance's /futures/data/openInterestHist endpoint.
#     fetch_open_interest_history() enforces this explicitly rather than
#     letting callers hit a confusing BadRequest.
# ============================================================

import logging
import time
from typing import Dict, List, Optional

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)

# ── Real, confirmed constraints ──────────────────────────────────────────
MAX_OPEN_INTEREST_HISTORY_DAYS = 30    # Binance's real cap on openInterestHist — see module docstring.
MAX_PAGINATION_PAGES = 200             # Same ceiling data_fetcher.py uses — safety, not a real API limit.
FUNDING_HISTORY_PAGE_LIMIT = 1000      # Binance's per-call max for fundingRateHistory.
OPEN_INTEREST_PAGE_LIMIT = 500         # Binance's per-call max for openInterestHist.

# claude code changed: real, confirmed divergences between this project's
# spot symbol list (bot/fetch_all_symbols.py's SYMBOLS) and Binance's
# USDT-margined perpetual listings — checked directly against
# ex.load_markets(), not assumed:
#   - MATIC/USDT's perpetual is listed as POL/USDT (Polygon's rebrand —
#     same real-world asset as the already-documented Kraken-side MATIC/
#     POL divergence in kraken_adapter.py's normalize_symbol()).
#   - SHIB/USDT's perpetual is listed as 1000SHIB/USDT (Binance sizes the
#     SHIB perpetual contract in units of 1000 SHIB, a common convention
#     for very low-priced coins — the funding rate/OI values themselves
#     are unaffected by this scaling, only the contract symbol is).
PERP_SYMBOL_OVERRIDES: Dict[str, str] = {
    "MATIC/USDT": "POL/USDT",
    "SHIB/USDT": "1000SHIB/USDT",
}

_exchange: Optional[ccxt.binance] = None

MAX_TRANSIENT_RETRIES = 3   # Same bounded-retry idea as data_fetcher.py's MAX_RATE_LIMIT_RETRIES.


def _with_retry(fn, *args, **kwargs):
    """
    Retries `fn` on ccxt.NetworkError (which covers RequestTimeout —
    confirmed the recurring, environment-level transient failure mode
    while building this module against the real API) with a short linear
    backoff. Re-raises after MAX_TRANSIENT_RETRIES — never retries forever.
    """
    last_error = None
    for attempt in range(1, MAX_TRANSIENT_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except ccxt.NetworkError as e:
            last_error = e
            if attempt < MAX_TRANSIENT_RETRIES:
                logger.warning(f"Transient network error (attempt {attempt}/{MAX_TRANSIENT_RETRIES}): {e}")
                time.sleep(1.5 * attempt)
    raise last_error


def _get_futures_exchange() -> ccxt.binance:
    """
    Lazily-constructed, module-level ccxt Binance instance scoped to
    USDT-margined linear perpetuals only (defaultSubType='linear').

    claude code changed: without defaultSubType='linear', ccxt's
    load_markets() for defaultType='future' also loads Binance's COIN-M
    inverse contracts from dapi.binance.com — confirmed by hitting a real
    RequestTimeout against dapi.binance.com during development. This
    project has no use for inverse contracts, so linear-only avoids that
    endpoint entirely rather than working around its failures.
    """
    global _exchange
    if _exchange is None:
        ex = ccxt.binance({
            "options": {"defaultType": "swap", "defaultSubType": "linear"},
            "timeout": 20000,
            "enableRateLimit": True,
        })
        _with_retry(ex.load_markets)
        _exchange = ex
    return _exchange


def to_perp_symbol(symbol: str) -> Optional[str]:
    """
    Maps this project's spot symbol format (e.g. "BTC/USDT") to ccxt's
    unified USDT-margined perpetual symbol (e.g. "BTC/USDT:USDT").

    Returns None for a symbol with no real Binance USDT-margined
    perpetual — fails closed rather than guessing, matching
    KrakenAdapter.normalize_symbol()'s precedent elsewhere in this project.
    """
    base_quote = PERP_SYMBOL_OVERRIDES.get(symbol, symbol)
    perp_symbol = f"{base_quote}:USDT"

    ex = _get_futures_exchange()
    if perp_symbol not in ex.markets:
        return None
    return perp_symbol


# ── Live snapshots ───────────────────────────────────────────────────────

def get_funding_rate_snapshot(symbol: str) -> Optional[dict]:
    """
    Current funding rate for `symbol` (e.g. "BTC/USDT"). Returns None if
    this symbol has no Binance USDT-margined perpetual.
    """
    perp_symbol = to_perp_symbol(symbol)
    if perp_symbol is None:
        logger.warning(f"get_funding_rate_snapshot: {symbol} has no Binance USDT-margined perpetual")
        return None

    ex = _get_futures_exchange()
    raw = _with_retry(ex.fetch_funding_rate, perp_symbol)
    return {
        "symbol": symbol,
        "perp_symbol": perp_symbol,
        "funding_rate": raw["fundingRate"],
        "mark_price": raw.get("markPrice"),
        "next_funding_timestamp": raw.get("nextFundingTimestamp"),
        "timestamp": raw.get("timestamp"),
        "datetime": raw.get("datetime"),
    }


def get_open_interest_snapshot(symbol: str) -> Optional[dict]:
    """
    Current open interest for `symbol`. Returns None if this symbol has
    no Binance USDT-margined perpetual.
    """
    perp_symbol = to_perp_symbol(symbol)
    if perp_symbol is None:
        logger.warning(f"get_open_interest_snapshot: {symbol} has no Binance USDT-margined perpetual")
        return None

    ex = _get_futures_exchange()
    raw = _with_retry(ex.fetch_open_interest, perp_symbol)
    return {
        "symbol": symbol,
        "perp_symbol": perp_symbol,
        "open_interest_amount": raw.get("openInterestAmount"),
        "open_interest_value": raw.get("openInterestValue"),
        "timestamp": raw.get("timestamp"),
        "datetime": raw.get("datetime"),
    }


# ── Historical series (research pipeline) ────────────────────────────────

def fetch_funding_rate_history(symbol: str, since_ms: int, until_ms: Optional[int] = None) -> pd.DataFrame:
    """
    Paginated funding rate history from `since_ms` to `until_ms` (defaults
    to now). Funding prints every 8h, so even a multi-year range is a
    handful of pages under FUNDING_HISTORY_PAGE_LIMIT=1000/call.

    Returns columns: timestamp, funding_rate, mark_price. Empty DataFrame
    (not None, not a raised exception) if the symbol has no perpetual —
    matches contagion_engine.py's "skip, don't crash" convention for a
    missing/unavailable symbol.
    """
    perp_symbol = to_perp_symbol(symbol)
    if perp_symbol is None:
        logger.warning(f"fetch_funding_rate_history: {symbol} has no Binance USDT-margined perpetual")
        return pd.DataFrame(columns=["timestamp", "funding_rate", "mark_price"])

    ex = _get_futures_exchange()
    until_ms = until_ms if until_ms is not None else int(time.time() * 1000)

    rows: List[dict] = []
    cursor = since_ms
    page_count = 0

    while cursor < until_ms:
        page_count += 1
        if page_count > MAX_PAGINATION_PAGES:
            logger.warning(
                f"fetch_funding_rate_history({symbol}): reached page limit "
                f"({MAX_PAGINATION_PAGES}). Returning what was collected so far."
            )
            break

        page = _with_retry(ex.fetch_funding_rate_history, perp_symbol, since=cursor, limit=FUNDING_HISTORY_PAGE_LIMIT)
        if not page:
            break

        for entry in page:
            ts = entry["timestamp"]
            if ts is None or ts > until_ms:
                continue
            rows.append({
                "timestamp": ts,
                "funding_rate": entry["fundingRate"],
                "mark_price": entry.get("info", {}).get("markPrice"),
            })

        last_ts = page[-1]["timestamp"]
        if last_ts is None or last_ts <= cursor:
            break  # No forward progress — stop rather than loop forever.
        cursor = last_ts + 1

    df = pd.DataFrame(rows, columns=["timestamp", "funding_rate", "mark_price"])
    if not df.empty:
        df["mark_price"] = pd.to_numeric(df["mark_price"], errors="coerce")
        df.drop_duplicates(subset=["timestamp"], inplace=True)
        df.sort_values("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
    return df


def fetch_open_interest_history(symbol: str, days: int = MAX_OPEN_INTEREST_HISTORY_DAYS, timeframe: str = "1h") -> pd.DataFrame:
    """
    Open interest history for `symbol` over the last `days` days.
    `days` is clamped to MAX_OPEN_INTEREST_HISTORY_DAYS — Binance's real,
    server-enforced limit for this endpoint (see module docstring); a
    caller asking for more gets the honest maximum, not a BadRequest.

    Returns columns: timestamp, open_interest_amount, open_interest_value.
    Empty DataFrame if the symbol has no perpetual.
    """
    if days > MAX_OPEN_INTEREST_HISTORY_DAYS:
        logger.info(
            f"fetch_open_interest_history({symbol}): requested {days}d, "
            f"clamping to Binance's real {MAX_OPEN_INTEREST_HISTORY_DAYS}d limit."
        )
        days = MAX_OPEN_INTEREST_HISTORY_DAYS

    perp_symbol = to_perp_symbol(symbol)
    if perp_symbol is None:
        logger.warning(f"fetch_open_interest_history: {symbol} has no Binance USDT-margined perpetual")
        return pd.DataFrame(columns=["timestamp", "open_interest_amount", "open_interest_value"])

    ex = _get_futures_exchange()
    since_ms = int((time.time() - days * 86400) * 1000)
    until_ms = int(time.time() * 1000)

    rows: List[dict] = []
    cursor = since_ms
    page_count = 0

    while cursor < until_ms:
        page_count += 1
        if page_count > MAX_PAGINATION_PAGES:
            logger.warning(
                f"fetch_open_interest_history({symbol}): reached page limit "
                f"({MAX_PAGINATION_PAGES}). Returning what was collected so far."
            )
            break

        page = _with_retry(
            ex.fetch_open_interest_history,
            perp_symbol, timeframe=timeframe, since=cursor, limit=OPEN_INTEREST_PAGE_LIMIT
        )
        if not page:
            break

        for entry in page:
            ts = entry["timestamp"]
            if ts is None or ts > until_ms:
                continue
            rows.append({
                "timestamp": ts,
                "open_interest_amount": entry.get("openInterestAmount"),
                "open_interest_value": entry.get("openInterestValue"),
            })

        last_ts = page[-1]["timestamp"]
        if last_ts is None or last_ts <= cursor:
            break
        cursor = last_ts + 1

    df = pd.DataFrame(rows, columns=["timestamp", "open_interest_amount", "open_interest_value"])
    if not df.empty:
        df.drop_duplicates(subset=["timestamp"], inplace=True)
        df.sort_values("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
    return df
