# claude code changed: new file — 2026-10-25, Phase A recorder: PURE order-book state and each venue's OFFICIAL
# synchronisation procedure. No network here — feeds.py drives these objects; tests drive them with mocked messages.
#
# Sources (read 2026-10-25):
#   Binance  github.com/binance/binance-spot-api-docs web-socket-streams.md (last change 2026-10-15),
#            "How to manage a local order book correctly" — buffer diff events, REST snapshot, discard u <= lastUpdateId,
#            snapshot.lastUpdateId < first U ⇒ fetch again; per event: u < book id ⇒ ignore, U > id + 1 ⇒ gap ⇒ restart.
#   Kraken   docs.kraken.com websocket-v2/book + guides/spot-ws-book-v2 — snapshot then updates; qty 0 removes a level;
#            truncate to the subscribed depth; CRC32 over the top 10 asks (low→high) + top 10 bids (high→low), with "."
#            and leading zeros removed; mismatch handling is NOT specified by Kraken → this recorder invalidates the book
#            and resubscribes (its own, documented choice).
#   Coinbase docs.cdp.coinbase.com Advanced Trade WebSocket overview + level2 — l2_data events "snapshot"/"update",
#            new_quantity is absolute (0 removes); sequence_num greater than previous + 1 ⇒ a message was dropped. The
#            docs disagree on its scope ("per product" vs "per connection"); this recorder checks it per connection,
#            the stricter reading, and flags every gap.
#
# Prices and quantities are Decimal end to end (parsed from the exact wire text) and written as strings — no float
# rounding enters the raw record.
import binascii
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Tuple

SYNCING, SYNCED, INVALID, DISCONNECTED, NOT_LISTED = "SYNCING", "SYNCED", "INVALID", "DISCONNECTED", "NOT_LISTED"
ZERO = Decimal(0)

_RFC3339 = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$")


def parse_rfc3339_ns(value) -> Optional[int]:
    """'2023-02-09T20:32:50.714964855Z' → epoch nanoseconds (UTC). None for anything that is not valid RFC 3339 —
    a malformed timestamp is reported, never guessed."""
    import calendar
    if not isinstance(value, str):
        return None
    m = _RFC3339.match(value.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, frac, tz = m.groups()
    try:
        secs = calendar.timegm((int(y), int(mo), int(d), int(h), int(mi), int(s), 0, 0, 0))
    except (ValueError, OverflowError):
        return None
    if not (1 <= int(mo) <= 12 and 1 <= int(d) <= 31 and int(h) < 24 and int(mi) < 60 and int(s) < 61):
        return None
    ns = int((frac or "").ljust(9, "0")) if frac else 0
    if tz != "Z":
        tz_sign = 1 if tz[0] == "+" else -1   # timezone offset direction
        secs -= tz_sign * (int(tz[1:3]) * 3600 + int(tz[4:6]) * 60)
    return secs * 1_000_000_000 + ns


def to_decimal(x) -> Optional[Decimal]:
    """Exact Decimal from a wire value (str/Decimal/int). Floats are rejected: they have already lost the exact text."""
    if isinstance(x, bool) or x is None or isinstance(x, float):
        return None
    try:
        d = Decimal(str(x)) if not isinstance(x, Decimal) else x
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


@dataclass
class LocalBook:
    venue: str
    instrument: str             # research key, e.g. BTC-USDT
    symbol: str                 # the venue's own identifier
    base: str
    quote: str
    status: str = SYNCING
    status_reason: str = "awaiting first snapshot"
    bids: Dict[Decimal, Decimal] = field(default_factory=dict)
    asks: Dict[Decimal, Decimal] = field(default_factory=dict)
    update_id: Optional[int] = None           # venue sequence/update id of the last applied change
    exchange_ts_ns: Optional[int] = None      # venue event time of the last applied change (None if the venue omits it)
    receipt_utc_ns: Optional[int] = None      # local wall-clock receipt time of the last applied change
    receipt_mono_ns: Optional[int] = None     # local monotonic receipt time (for elapsed durations only)
    feed_alive_mono_ns: Optional[int] = None  # last message of ANY kind on this book's connection (liveness)
    updates_since_sample: int = 0
    updates_total: int = 0
    resyncs: int = 0
    gaps: int = 0
    checksum_failures: int = 0
    malformed: int = 0
    rejected_levels: int = 0

    # ── state changes ────────────────────────────────────────────────────────────────────────────────────────────
    def reset(self, status: str, reason: str) -> None:
        self.bids.clear(); self.asks.clear()
        self.update_id = None
        self.status, self.status_reason = status, reason

    def invalidate(self, reason: str) -> None:
        """Mark the book unusable until it is resynchronised; its levels are cleared so nothing stale is ever written."""
        self.reset(INVALID, reason)

    def mark_synced(self, reason: str = "synchronised") -> None:
        self.status, self.status_reason = SYNCED, reason

    def touch(self, receipt_utc_ns: int, receipt_mono_ns: int, exchange_ts_ns: Optional[int]) -> None:
        self.receipt_utc_ns, self.receipt_mono_ns = receipt_utc_ns, receipt_mono_ns
        if exchange_ts_ns is not None:
            self.exchange_ts_ns = exchange_ts_ns
        self.updates_since_sample += 1
        self.updates_total += 1

    # ── levels ───────────────────────────────────────────────────────────────────────────────────────────────────
    def set_level(self, side: str, price, qty) -> bool:
        p, q = to_decimal(price), to_decimal(qty)
        if p is None or q is None or p <= ZERO or q < ZERO:
            self.rejected_levels += 1
            return False
        book = self.bids if side == "bid" else self.asks
        if q == ZERO:
            book.pop(p, None)
        else:
            book[p] = q
        return True

    def top(self, n: int) -> Tuple[List[Tuple[Decimal, Decimal]], List[Tuple[Decimal, Decimal]]]:
        bids = sorted(self.bids.items(), key=lambda kv: kv[0], reverse=True)[:n]
        asks = sorted(self.asks.items(), key=lambda kv: kv[0])[:n]
        return bids, asks

    def truncate(self, depth: int) -> None:
        bids, asks = self.top(depth)
        self.bids, self.asks = dict(bids), dict(asks)


# ── Binance: official local-order-book procedure ───────────────────────────────────────────────────────────────────
class BinanceSync:
    """Diff-depth events are buffered until a REST snapshot arrives; then the official update procedure applies."""
    MAX_BUFFER = 5000

    def __init__(self, book: LocalBook):
        self.book = book
        self.buffer: List[dict] = []

    def restart(self, reason: str) -> None:
        self.buffer.clear()
        self.book.reset(SYNCING, reason)

    def first_buffered_U(self) -> Optional[int]:
        return self.buffer[0]["U"] if self.buffer else None

    def on_event(self, evt: dict, receipt_utc_ns: int, receipt_mono_ns: int) -> str:
        """Returns 'buffered' | 'applied' | 'ignored' | 'gap' | 'malformed'."""
        try:
            U, u = int(evt["U"]), int(evt["u"])
            E = int(evt["E"])
            bids, asks = evt["b"], evt["a"]
        except (KeyError, TypeError, ValueError):
            self.book.malformed += 1
            return "malformed"
        item = {"U": U, "u": u, "E": E, "b": bids, "a": asks, "rt": receipt_utc_ns, "rm": receipt_mono_ns}
        if self.book.status != SYNCED:
            if len(self.buffer) >= self.MAX_BUFFER:
                self.restart("buffer overflow while waiting for the depth snapshot")
            self.buffer.append(item)
            return "buffered"
        return self._apply(item)

    def on_snapshot(self, snapshot: dict) -> str:
        """Returns 'synced' | 'retry' (snapshot older than the first buffered event) | 'gap' | 'malformed'."""
        try:
            last_id = int(snapshot["lastUpdateId"])
            bids, asks = snapshot["bids"], snapshot["asks"]
        except (KeyError, TypeError, ValueError):
            self.book.malformed += 1
            return "malformed"
        first_U = self.first_buffered_U()
        if first_U is None or last_id < first_U:
            return "retry"                                        # step 4: fetch the snapshot again
        self.book.bids.clear(); self.book.asks.clear()
        for p, q in bids:
            self.book.set_level("bid", p, q)
        for p, q in asks:
            self.book.set_level("ask", p, q)
        self.book.update_id = last_id
        pending = [e for e in self.buffer if e["u"] > last_id]     # step 5: discard u <= lastUpdateId
        self.buffer.clear()
        self.book.mark_synced("snapshot + buffered events applied")
        for e in pending:                                          # step 7: apply buffered events in order
            if self._apply(e) == "gap":
                return "gap"
        return "synced"

    def _apply(self, e: dict) -> str:
        book = self.book
        if e["u"] < book.update_id:
            return "ignored"
        if e["U"] > book.update_id + 1:
            book.gaps += 1
            self.restart(f"sequence gap: U={e['U']} > book id {book.update_id} + 1")
            return "gap"
        for p, q in e["b"]:
            book.set_level("bid", p, q)
        for p, q in e["a"]:
            book.set_level("ask", p, q)
        book.update_id = e["u"]
        book.touch(e["rt"], e["rm"], e["E"] * 1_000_000)
        return "applied"


# ── Kraken: snapshot/update + CRC32 checksum ───────────────────────────────────────────────────────────────────────
def _kraken_token(x: Decimal) -> str:
    """Kraken guide: remove the '.' and the leading zeros from the exact decimal text."""
    s = format(x, "f") if isinstance(x, Decimal) else str(x)
    return s.replace(".", "").lstrip("0")


def kraken_checksum(bids: Iterable[Tuple[Decimal, Decimal]], asks: Iterable[Tuple[Decimal, Decimal]]) -> int:
    """CRC32 over the top 10 asks (low→high) then the top 10 bids (high→low), as an unsigned 32-bit integer."""
    a = sorted(asks, key=lambda kv: kv[0])[:10]
    b = sorted(bids, key=lambda kv: kv[0], reverse=True)[:10]
    text = "".join(_kraken_token(p) + _kraken_token(q) for p, q in a) + "".join(_kraken_token(p) + _kraken_token(q) for p, q in b)
    return binascii.crc32(text.encode("ascii")) & 0xFFFFFFFF


class KrakenSync:
    def __init__(self, book: LocalBook, depth: int):
        self.book, self.depth = book, depth

    def on_message(self, kind: str, data: dict, receipt_utc_ns: int, receipt_mono_ns: int) -> str:
        """kind 'snapshot' | 'update'. Returns 'synced' | 'applied' | 'checksum_mismatch' | 'out_of_sync' | 'malformed'."""
        book = self.book
        try:
            bids, asks, checksum = data["bids"], data["asks"], int(data["checksum"])
        except (KeyError, TypeError, ValueError):
            book.malformed += 1
            return "malformed"
        if kind == "snapshot":
            book.bids.clear(); book.asks.clear()
        elif book.status != SYNCED:
            return "out_of_sync"                                   # an update without a valid snapshot is not applied
        try:
            for lvl in bids:
                book.set_level("bid", lvl["price"], lvl["qty"])
            for lvl in asks:
                book.set_level("ask", lvl["price"], lvl["qty"])
        except (KeyError, TypeError):
            book.malformed += 1
            book.invalidate("malformed level in book message")
            return "malformed"
        book.truncate(self.depth)
        if kraken_checksum(book.bids.items(), book.asks.items()) != checksum:
            book.checksum_failures += 1
            book.invalidate(f"checksum mismatch (expected {checksum})")
            return "checksum_mismatch"
        ts = parse_rfc3339_ns(data.get("timestamp"))
        book.touch(receipt_utc_ns, receipt_mono_ns, ts)
        if kind == "snapshot":
            book.mark_synced("snapshot checksum verified")
            return "synced"
        return "applied"


# ── Coinbase: per-connection sequence check + l2 events ────────────────────────────────────────────────────────────
class SequenceTracker:
    """Coinbase sequence_num: a value greater than previous + 1 means a dropped message; a lower one is ignored."""
    def __init__(self):
        self.last: Optional[int] = None

    def check(self, seq) -> str:
        """'first' | 'ok' | 'gap' | 'old' | 'malformed'."""
        if isinstance(seq, bool) or not isinstance(seq, int):
            return "malformed"
        if self.last is None:
            self.last = seq
            return "first"
        if seq <= self.last:
            return "old"
        status = "ok" if seq == self.last + 1 else "gap"
        self.last = seq
        return status

    def reset(self):
        self.last = None


class CoinbaseBookSync:
    def __init__(self, book: LocalBook):
        self.book = book

    def on_event(self, event: dict, receipt_utc_ns: int, receipt_mono_ns: int) -> str:
        """One l2_data event: 'synced' (snapshot) | 'applied' | 'out_of_sync' | 'malformed'."""
        book = self.book
        kind, updates = event.get("type"), event.get("updates")
        if kind not in ("snapshot", "update") or not isinstance(updates, list):
            book.malformed += 1
            return "malformed"
        if kind == "snapshot":
            book.bids.clear(); book.asks.clear()
        elif book.status != SYNCED:
            return "out_of_sync"
        latest = None
        for u in updates:
            side = {"bid": "bid", "offer": "ask"}.get(u.get("side"))
            if side is None:
                book.malformed += 1
                book.invalidate("unknown side in level2 update")
                return "malformed"
            book.set_level(side, u.get("price_level"), u.get("new_quantity"))
            ts = parse_rfc3339_ns(u.get("event_time"))
            if ts is not None and (latest is None or ts > latest):
                latest = ts
        book.touch(receipt_utc_ns, receipt_mono_ns, latest)
        if kind == "snapshot":
            book.mark_synced("level2 snapshot received")
            return "synced"
        return "applied"


# ── sampling ───────────────────────────────────────────────────────────────────────────────────────────────────────
def sample_row(book: LocalBook, *, depth: int, now_utc_ns: int, now_mono_ns: int, stale_after_s: float,
               future_tol_s: float, session_id: str, config_id: str, seq: int) -> dict:
    """One observation of one book at one sampling instant. Levels are written ONLY for a SYNCED, non-stale book —
    an invalid, disconnected or stale venue is recorded as such, never as a current price (no forward fill)."""
    flags: List[str] = []
    age_s = None if book.receipt_mono_ns is None else (now_mono_ns - book.receipt_mono_ns) / 1e9
    feed_delay_ms = None
    if book.exchange_ts_ns is not None and book.receipt_utc_ns is not None:
        feed_delay_ms = (book.receipt_utc_ns - book.exchange_ts_ns) / 1e6
        if feed_delay_ms < -future_tol_s * 1000:
            flags.append("CLOCK_ANOMALY")                          # exchange time later than local receipt
    if book.exchange_ts_ns is None:
        flags.append("NO_EXCHANGE_TIMESTAMP")
    usable = book.status == SYNCED
    # claude code changed: 2026-10-25 — liveness comes from the CONNECTION (any message, incl. heartbeats), not from
    # the book changing: a quiet market is not a dead feed (smoke run: Coinbase XRP-USDT went >5 s without a change).
    feed_age_s = None if book.feed_alive_mono_ns is None else (now_mono_ns - book.feed_alive_mono_ns) / 1e9
    if usable and (feed_age_s is None or feed_age_s > stale_after_s):
        flags.append("STALE")                                     # connection silent: the book cannot be trusted
        usable = False
    elif usable and (age_s is None or age_s > stale_after_s):
        flags.append("QUIET_BOOK")                                # feed alive, this book simply has not changed
    bids, asks = book.top(depth) if usable else ([], [])
    if usable:
        if not bids or not asks:
            flags.append("EMPTY_SIDE")
            usable = False
        elif bids[0][0] >= asks[0][0]:
            flags.append("CROSSED")
            usable = False
    row = {
        "schema": "xarb-obs-v1", "session_id": session_id, "config_id": config_id, "seq": seq,
        "sample_utc_ns": now_utc_ns, "sample_mono_ns": now_mono_ns,
        "venue": book.venue, "instrument": book.instrument, "venue_symbol": book.symbol, "base": book.base, "quote": book.quote,
        "status": book.status, "status_reason": book.status_reason, "usable": usable, "flags": flags,
        "exchange_ts_ns": book.exchange_ts_ns, "receipt_utc_ns": book.receipt_utc_ns, "receipt_mono_ns": book.receipt_mono_ns,
        "book_age_s": age_s, "feed_age_s": feed_age_s, "feed_delay_ms": feed_delay_ms, "update_id": book.update_id,
        "updates_since_last_sample": book.updates_since_sample,
        "best_bid": str(bids[0][0]) if usable else None, "best_bid_qty": str(bids[0][1]) if usable else None,
        "best_ask": str(asks[0][0]) if usable else None, "best_ask_qty": str(asks[0][1]) if usable else None,
        "bids": [[str(p), str(q)] for p, q in bids] if usable else None,
        "asks": [[str(p), str(q)] for p, q in asks] if usable else None,
        "units": {"price": f"{book.quote} per {book.base}", "qty": book.base, "time": "ns since Unix epoch, UTC"},
    }
    book.updates_since_sample = 0
    return row
