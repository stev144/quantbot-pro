# ============================================================
# bot/engines/trade_data.py
# claude code changed: new file — Phase 2B (Crypto Market Data Foundation
# & Trade-Flow Research Layer), Steps 4-5. Aggregated trade-level data:
# the first genuinely new microstructure information source this platform
# ingests, per Phase 2A's audit ranking (highest research priority among
# genuinely new data, given funding/OI already existed).
#
# Deliberately mirrors bot/engines/derivatives_data.py's exact shape —
# module-level lazily-constructed ccxt exchange, bounded-retry wrapper,
# paginated history fetch with a hard page-count ceiling, typed DataFrame
# with fixed columns, empty (not None, not an exception) on a symbol with
# no data, dedup+sort+reset_index. Reusing this proven pattern rather than
# inventing a new one, per Phase 2B's own instruction to reuse existing
# boundaries where they are sound.
#
# WHAT THIS MODULE DOES NOT DO (scope discipline — Phase 2B Steps 13/14):
#   - No order-book data (L1/L2) — deferred to Phase 2C by design.
#   - No derived flow features (signed volume, CVD, delta, etc) — that is
#     bot/research/trade_flow_engine.py's job. This module's raw trade
#     records are the source of truth; every derived feature must be
#     reproducible from them, never computed here and thrown away.
#   - No live execution, no order placement, no credentials — this reads
#     Binance's PUBLIC spot aggTrades endpoint only (same trust boundary
#     as data_fetcher.py's public klines calls).
#
# ── VERIFIED, NOT ASSUMED ──────────────────────────────────────────────
# Binance's public spot aggTrades endpoint (GET /api/v3/aggTrades, ccxt's
# raw ex.publicGetAggTrades) was hit live against the real API while
# building this module (2026-08-24) to confirm the actual response shape,
# per Phase 2B Step 4's explicit "do not assume an API or schema from
# memory" instruction. Confirmed real fields:
#   a = aggregate trade ID (int, as a string in the raw response)
#   p = price (string, needs numeric coercion)
#   q = quantity (string, needs numeric coercion)
#   f = first individual trade ID in this aggregate (string)
#   l = last individual trade ID in this aggregate (string)
#   T = trade timestamp, milliseconds (int)
#   m = isBuyerMaker (bool) — Binance's aggressor-direction field. When
#       True, the BUY side was the resting/maker order, meaning the SELLER
#       crossed the spread — a SELL-initiated (sell-aggressor) trade. When
#       False, the buyer was the taker/aggressor — a BUY-initiated trade.
#       Getting this backwards silently would corrupt every downstream
#       signed-volume/CVD calculation without ever raising an error, so it
#       is called out explicitly here and stored under its own Binance
#       name (is_buyer_maker) rather than pre-flipped into an "is_buy"
#       column — translating the SIGN is trade_flow_engine.py's job, this
#       module only translates the CRYPTIC FIELD NAME (matching
#       derivatives_data.py's funding_rate/open_interest translation, not
#       adding a derived feature).
#   M = isBestMatch — confirmed present in the live response but is a
#       legacy/internal-matching-engine field with no documented research
#       meaning; not persisted (same "Binance sends it, it's not real
#       information" classification Phase 2B Step 1 gave OHLCV's own
#       "ignore" field).
#   Confirmed via a live call: startTime/endTime pagination works exactly
#   as Binance's docs describe.
# ============================================================

import logging
import time
from pathlib import Path
from typing import List, Optional

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)

# ── Real, confirmed constraints ──────────────────────────────────────────
AGG_TRADES_PAGE_LIMIT = 1000     # Binance's per-call max for aggTrades.
MAX_PAGINATION_PAGES = 200       # Same ceiling data_fetcher.py/derivatives_data.py use — safety, not a real API limit.
MAX_TRANSIENT_RETRIES = 3        # Same bounded-retry idea as data_fetcher.py/derivatives_data.py.

# claude code changed: new — the raw trade storage boundary (Step 5).
# Deliberately its OWN directory, not mixed into data/*.csv (OHLCV) or
# research_data/*.csv (engine output) — Step 5's explicit "the raw dataset
# must remain distinguishable from derived feature datasets" requirement.
# Smallest mechanism that fits the current project: one CSV per symbol,
# the exact same convention data/*.csv already uses for OHLCV — no
# database, no message queue, no parquet lake. Tradeoff, stated plainly:
# a single growing per-symbol CSV means an incremental update must read
# the file's current tail to find the resume point, and a very long
# history will eventually make that read non-trivial — acceptable for a
# research-scale, single-machine, batch-fetched dataset; would need to
# change if this ever became a live/streaming ingestion system (explicitly
# out of scope this phase, see module docstring).
TRADES_DIR = Path("data/trades")

# claude code changed: new — Step 5 "schema versioning" without inventing
# a metadata sidecar file: the expected column set is a single, explicit,
# testable module constant. load_stored_trades() below validates a loaded
# file's columns against this and fails loud (not a silent misread) on a
# mismatch — the smallest mechanism that gives real auditability.
TRADE_SCHEMA_VERSION = 1
TRADE_COLUMNS: List[str] = [
    "trade_id", "timestamp", "price", "quantity", "is_buyer_maker", "symbol", "source",
]

_exchange: Optional[ccxt.binance] = None


def _with_retry(fn, *args, **kwargs):
    """Same bounded-retry idea as derivatives_data.py's _with_retry — retries
    on ccxt.NetworkError with short linear backoff, re-raises after
    MAX_TRANSIENT_RETRIES. Never retries forever."""
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


def _get_spot_exchange() -> ccxt.binance:
    """
    Lazily-constructed, module-level ccxt Binance SPOT instance — the
    aggTrades endpoint used here is Binance's public spot market data API
    (api.binance.com), the same trust boundary bot/data_fetcher.py's
    klines calls already use, not the futures API derivatives_data.py
    needs for funding/OI. Deliberately a SEPARATE exchange instance from
    derivatives_data.py's futures-scoped one (different defaultType), so
    neither module's configuration can accidentally affect the other's.
    """
    global _exchange
    if _exchange is None:
        ex = ccxt.binance({"timeout": 20000, "enableRateLimit": True})
        _exchange = ex
    return _exchange


def trade_csv_path(symbol: str) -> Path:
    """
    claude code changed: new — the one place symbol -> raw-trade-file path
    is decided, mirroring bot.fetch_all_symbols.symbol_to_filename()'s
    convention (BTC/USDT -> BTC_USDT) but under TRADES_DIR and with a
    "_trades.csv" suffix instead of "_1h.csv" — this is trade-level data,
    not an OHLCV file, and must never collide with one on disk.
    """
    safe = symbol.replace("/", "_")
    return TRADES_DIR / f"{safe}_trades.csv"


# ── Fetch: pure, no disk I/O ─────────────────────────────────────────────

def fetch_agg_trades(symbol: str, since_ms: int, until_ms: Optional[int] = None) -> pd.DataFrame:
    """
    Paginated aggregated-trade history for `symbol` from `since_ms` to
    `until_ms` (defaults to now). Binance symbol format for this endpoint
    is the concatenated form (e.g. "BTCUSDT") — translated here from this
    project's canonical "BTC/USDT" form; no ccxt unified-symbol lookup is
    needed since aggTrades is called via the raw public endpoint.

    Pagination strategy (verified live against the real API before writing
    this, per Phase 2B Step 4's "do not assume an API or schema from
    memory" instruction): Binance's 1-hour startTime/endTime window limit
    only applies when BOTH are supplied together. The FIRST call uses
    startTime-only (no endTime, no 1h restriction) to establish a
    position; every subsequent call pages purely by fromId=last_id+1 (also
    no 1h restriction) — simpler and more robust than re-windowing by
    time, and avoids ever needing to reconcile two different pagination
    cursors.

    Returns TRADE_COLUMNS-shaped DataFrame, sorted by timestamp,
    deduplicated by trade_id. Empty DataFrame (not None, not a raised
    exception) if no trades are found in range — matches
    derivatives_data.py's "skip, don't crash" convention for missing data.
    """
    market_symbol = symbol.replace("/", "")
    ex = _get_spot_exchange()
    until_ms = until_ms if until_ms is not None else int(time.time() * 1000)

    rows: List[dict] = []
    page_count = 0
    from_id: Optional[int] = None   # None = first call, use startTime; set = subsequent calls, use fromId

    while True:
        page_count += 1
        if page_count > MAX_PAGINATION_PAGES:
            logger.warning(
                f"fetch_agg_trades({symbol}): reached page limit "
                f"({MAX_PAGINATION_PAGES}). Returning what was collected so far."
            )
            break

        params = {"symbol": market_symbol, "limit": AGG_TRADES_PAGE_LIMIT}
        if from_id is None:
            params["startTime"] = since_ms
        else:
            params["fromId"] = from_id

        page = _with_retry(ex.publicGetAggTrades, params)
        if not page:
            break

        page_had_in_range_trade = False
        for entry in page:
            ts = int(entry["T"])
            if ts > until_ms:
                continue   # claude code changed: keep paging past an out-of-range trade rather than stopping mid-page — a page can span the until_ms boundary
            page_had_in_range_trade = True
            rows.append({
                "trade_id": int(entry["a"]),
                "timestamp": ts,
                "price": entry["p"],
                "quantity": entry["q"],
                "is_buyer_maker": bool(entry["m"]),
                "symbol": symbol,
                "source": "binance",
            })

        last_entry_ts = int(page[-1]["T"])
        last_entry_id = int(page[-1]["a"])

        if last_entry_ts > until_ms:
            break   # claude code changed: this page already reached/passed until_ms — nothing further to fetch
        if len(page) < AGG_TRADES_PAGE_LIMIT:
            break   # claude code changed: short page — caught up to the most recent trade, no more pages exist
        if not page_had_in_range_trade:
            break   # claude code changed: defensive — should be unreachable given the two checks above, but never loop forever on a page that added nothing

        from_id = last_entry_id + 1   # claude code changed: advance by trade ID, never by time — two trades can share a millisecond timestamp, never a trade_id

    df = pd.DataFrame(rows, columns=TRADE_COLUMNS)
    if not df.empty:
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
        df.drop_duplicates(subset=["trade_id"], inplace=True)
        df.sort_values("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
    return df


# ── Storage: incremental, deduplicated, per-symbol ───────────────────────

def load_stored_trades(symbol: str) -> pd.DataFrame:
    """
    Reads this symbol's local raw-trade CSV, if any. Returns an empty,
    correctly-shaped DataFrame (never None, never raises) if no file
    exists yet — same convention as _ohlcv_check()'s file-not-found path.

    Fails loud (ValueError), not a silent misread, if a stored file's
    columns don't match TRADE_COLUMNS — the schema-versioning guard Step 5
    asked for.
    """
    path = trade_csv_path(symbol)
    if not path.exists():
        return pd.DataFrame(columns=TRADE_COLUMNS)

    df = pd.read_csv(path)
    if list(df.columns) != TRADE_COLUMNS:
        raise ValueError(
            f"{path} has columns {list(df.columns)}, expected {TRADE_COLUMNS} "
            f"(TRADE_SCHEMA_VERSION={TRADE_SCHEMA_VERSION}). Refusing to silently "
            f"reinterpret a file that may be from an older schema."
        )
    return df


def update_stored_trades(symbol: str, until_ms: Optional[int] = None, backfill_since_ms: Optional[int] = None) -> pd.DataFrame:
    """
    claude code changed: new — the incremental-update orchestration Step 5
    asked for. Reads what's already stored, fetches only trades AFTER the
    last stored trade_id's timestamp (or from `backfill_since_ms` if
    nothing is stored yet and a caller supplies one — required for a true
    first-time backfill, since there is no "last stored trade" to resume
    from), appends, deduplicates by trade_id, re-sorts, and writes back.

    Deduplication is by trade_id, not by timestamp — multiple trades can
    share a millisecond timestamp (confirmed in the live schema check
    above), so trade_id is the only real primary key here.

    Raises ValueError if nothing is stored yet and backfill_since_ms is
    not given — never silently guesses a start point for a symbol with no
    history.
    """
    existing = load_stored_trades(symbol)

    if existing.empty:
        if backfill_since_ms is None:
            raise ValueError(
                f"No stored trades for '{symbol}' yet and backfill_since_ms was not "
                f"given — cannot silently guess how far back to fetch."
            )
        since_ms = backfill_since_ms
    else:
        since_ms = int(existing["timestamp"].max()) + 1   # claude code changed: +1ms, never re-request a candle we might already have (trade_id dedup would catch it anyway, but avoids the wasted call)

    new_trades = fetch_agg_trades(symbol, since_ms=since_ms, until_ms=until_ms)

    if new_trades.empty and existing.empty:
        return existing   # claude code changed: nothing to write — don't create an empty file

    combined = pd.concat([existing, new_trades], ignore_index=True)
    combined["timestamp"] = pd.to_numeric(combined["timestamp"], errors="coerce").astype("int64")
    combined["trade_id"] = pd.to_numeric(combined["trade_id"], errors="coerce").astype("int64")
    combined.drop_duplicates(subset=["trade_id"], inplace=True)
    combined.sort_values("timestamp", inplace=True)
    combined.reset_index(drop=True, inplace=True)

    TRADES_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_csv(trade_csv_path(symbol), index=False)

    return combined
