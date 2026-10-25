# claude code changed: new file — 2026-10-25, Phase A recorder: aiohttp WebSocket clients for the three venues'
# PUBLIC order-book feeds. Read-only: the only outbound messages are subscribe/unsubscribe requests; the only REST
# calls are Binance's public depth snapshot (required by its official sync procedure), public symbol lookups and
# public server-time endpoints. No API key, signature or private endpoint exists in this module.
#
#   Binance   wss://stream.binance.com:9443/stream?streams=<sym>@depth@100ms  (+ REST /api/v3/depth snapshot)
#             limits honoured: 24 h connection life (reconnect), server ping every 20 s (aiohttp autopong),
#             ≤ 5 incoming messages/s (this client sends none after connecting), ≤ 1024 streams/connection.
#   Kraken    wss://ws.kraken.com/v2  {"method":"subscribe","params":{"channel":"book",...}}
#   Coinbase  wss://advanced-trade-ws.coinbase.com  level2 + heartbeats, both subscribed within 5 s of connecting
import asyncio
import json
import time
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Tuple

import aiohttp

from bot.research.cross_exchange.books import (DISCONNECTED, NOT_LISTED, SYNCED, SYNCING, BinanceSync, CoinbaseBookSync,
                                               KrakenSync, LocalBook, SequenceTracker)
from bot.research.cross_exchange.config import ENDPOINTS, RecorderConfig

Emit = Callable[..., None]
SILENCE_RECONNECT_S = 30.0          # no message at all for this long ⇒ treat the connection as dead and reconnect


def _now() -> Tuple[int, int]:
    return time.time_ns(), time.monotonic_ns()


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


class BaseFeed:
    venue = ""

    def __init__(self, cfg: RecorderConfig, books: Dict[str, LocalBook], http: aiohttp.ClientSession, emit: Emit,
                 stop: asyncio.Event):
        self.cfg, self.books, self.http, self.emit, self.stop = cfg, books, http, emit, stop
        self.connections = 0
        self.disconnects = 0

    async def run(self) -> None:
        attempt = 0
        while not self.stop.is_set():
            try:
                await self._session()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:                                   # reported, then a capped backoff
                self.emit("ERROR", self.venue, detail=f"{type(e).__name__}: {e}"[:300])
            finally:
                for b in self.books.values():
                    if b.status != NOT_LISTED:
                        b.reset(DISCONNECTED, "connection closed")
            if self.stop.is_set():
                break
            delay = min(self.cfg.reconnect_initial_s * (2 ** attempt), self.cfg.reconnect_max_s)
            attempt += 1
            self.disconnects += 1
            self.emit("RECONNECT_WAIT", self.venue, detail=f"retry in {delay:.0f}s (attempt {attempt})")
            await _wait(self.stop, delay)

    async def _connect(self, url: str):
        return await self.http.ws_connect(url, autoping=True, max_msg_size=0,
                                          timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=10.0),
                                          heartbeat=None)

    async def _receive_loop(self, ws, handle) -> None:
        while not self.stop.is_set():
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=SILENCE_RECONNECT_S)
            except asyncio.TimeoutError:
                self.emit("SILENCE", self.venue, detail=f"no message for {SILENCE_RECONNECT_S:.0f}s — reconnecting")
                return
            if msg.type == aiohttp.WSMsgType.TEXT:
                rt, rm = _now()
                for b in self.books.values():                          # claude code changed: connection liveness
                    b.feed_alive_mono_ns = rm
                await handle(msg.data, rt, rm, ws)
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                              aiohttp.WSMsgType.ERROR):
                self.emit("DISCONNECTED", self.venue, detail=f"{msg.type.name} {getattr(ws, 'close_code', '')}")
                return

    async def _session(self):  # pragma: no cover - overridden
        raise NotImplementedError


# ── Binance ────────────────────────────────────────────────────────────────────────────────────────────────────────
class BinanceFeed(BaseFeed):
    venue = "binance"
    SNAPSHOT_LIMIT = 1000
    SNAPSHOT_RETRIES = 5

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.syncs = {sym: BinanceSync(b) for sym, b in self.books.items()}
        self.snapshot_tasks: Dict[str, asyncio.Task] = {}

    async def _session(self):
        live = [s for s, b in self.books.items() if b.status != NOT_LISTED]
        if not live:
            self.emit("IDLE", self.venue, detail="no listed instruments")
            await _wait(self.stop, 3600)
            return
        url = f"{ENDPOINTS['binance']['ws']}?streams=" + "/".join(f"{s.lower()}@depth@100ms" for s in live)
        ws = await asyncio.wait_for(self._connect(url), timeout=self.cfg.connect_timeout_s)
        self.connections += 1
        self.emit("CONNECTED", self.venue, detail=f"{len(live)} diff-depth streams")
        try:
            for s in live:
                self.syncs[s].restart("connected; buffering diff events for the REST snapshot")
                self._schedule_snapshot(s)
            await self._receive_loop(ws, self._handle)
        finally:
            for t in self.snapshot_tasks.values():
                t.cancel()
            self.snapshot_tasks.clear()
            await ws.close()

    async def _handle(self, text, rt, rm, ws):
        try:
            data = json.loads(text).get("data") or {}
            sym = data["s"]
        except (ValueError, KeyError, AttributeError, TypeError):
            self.emit("MALFORMED", self.venue, detail=str(text)[:200])
            return
        sync = self.syncs.get(sym)
        if sync is None:
            return
        result = sync.on_event(data, rt, rm)
        if result == "gap":
            self.emit("SEQUENCE_GAP", self.venue, symbol=sym, detail=sync.book.status_reason)
            self._schedule_snapshot(sym)
        elif result == "malformed":
            self.emit("MALFORMED", self.venue, symbol=sym, detail=str(text)[:200])

    def _schedule_snapshot(self, sym: str):
        t = self.snapshot_tasks.get(sym)
        if t is None or t.done():
            self.snapshot_tasks[sym] = asyncio.create_task(self._snapshot(sym))

    async def _snapshot(self, sym: str):
        sync = self.syncs[sym]
        for _ in range(100):                                         # step 2: wait for the first buffered event
            if sync.buffer or self.stop.is_set():
                break
            await asyncio.sleep(0.1)
        for attempt in range(1, self.SNAPSHOT_RETRIES + 1):
            if self.stop.is_set():
                return
            try:
                async with self.http.get(ENDPOINTS["binance"]["rest_depth"], params={"symbol": sym, "limit": self.SNAPSHOT_LIMIT},
                                         timeout=aiohttp.ClientTimeout(total=20)) as r:
                    if r.status != 200:
                        self.emit("SNAPSHOT_HTTP_ERROR", self.venue, symbol=sym, detail=f"HTTP {r.status}")
                        await asyncio.sleep(2 * attempt)
                        continue
                    snap = await r.json(content_type=None)
            except Exception as e:
                self.emit("SNAPSHOT_ERROR", self.venue, symbol=sym, detail=f"{type(e).__name__}: {e}"[:200])
                await asyncio.sleep(2 * attempt)
                continue
            result = sync.on_snapshot(snap)
            if result == "synced":
                sync.book.resyncs += 1
                self.emit("SYNCED", self.venue, symbol=sym, detail=f"snapshot lastUpdateId {sync.book.update_id}")
                return
            self.emit("SNAPSHOT_" + result.upper(), self.venue, symbol=sym, detail=f"attempt {attempt}")
            if result == "gap":
                return                                               # _handle schedules a fresh snapshot on the next event
            await asyncio.sleep(0.5)
        sync.book.invalidate(f"no usable depth snapshot after {self.SNAPSHOT_RETRIES} attempts")
        self.emit("SNAPSHOT_FAILED", self.venue, symbol=sym)


# ── Kraken ─────────────────────────────────────────────────────────────────────────────────────────────────────────
class KrakenFeed(BaseFeed):
    venue = "kraken"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.depth = self.cfg.kraken_subscription_depth()
        self.syncs = {sym: KrakenSync(b, self.depth) for sym, b in self.books.items()}
        self.req = 0

    def _sub(self, method: str, symbols: List[str]) -> str:
        self.req += 1
        params = {"channel": "book", "symbol": symbols, "depth": self.depth}
        if method == "subscribe":
            params["snapshot"] = True
        return json.dumps({"method": method, "params": params, "req_id": self.req})

    async def _session(self):
        live = [s for s, b in self.books.items() if b.status != NOT_LISTED]
        if not live:
            await _wait(self.stop, 3600)
            return
        ws = await asyncio.wait_for(self._connect(ENDPOINTS["kraken"]["ws"]), timeout=self.cfg.connect_timeout_s)
        self.connections += 1
        self.emit("CONNECTED", self.venue, detail=f"book depth {self.depth}")
        try:
            for s in live:
                self.books[s].reset(SYNCING, "subscribed; awaiting snapshot")
            await ws.send_str(self._sub("subscribe", live))
            await self._receive_loop(ws, self._handle)
        finally:
            await ws.close()

    async def _handle(self, text, rt, rm, ws):
        try:
            msg = json.loads(text, parse_float=Decimal)              # exact decimal text, as Kraken's guide advises
        except ValueError:
            self.emit("MALFORMED", self.venue, detail=str(text)[:200])
            return
        if msg.get("method") in ("subscribe", "unsubscribe"):
            sym = (msg.get("result") or {}).get("symbol")
            if msg.get("method") == "subscribe" and msg.get("success") is False:
                if sym in self.books:
                    self.books[sym].reset(NOT_LISTED, f"subscription rejected: {msg.get('error')}")
                self.emit("SUBSCRIBE_REJECTED", self.venue, symbol=sym, detail=str(msg.get("error"))[:200])
            return
        if msg.get("channel") != "book":
            return                                                    # heartbeat / status
        kind = msg.get("type")
        for data in msg.get("data") or []:
            sym = data.get("symbol")
            sync = self.syncs.get(sym)
            if sync is None:
                continue
            result = sync.on_message(kind, data, rt, rm)
            if result == "synced":
                sync.book.resyncs += 1
                self.emit("SYNCED", self.venue, symbol=sym, detail="snapshot checksum verified")
            elif result in ("checksum_mismatch", "malformed", "out_of_sync"):
                self.emit(result.upper(), self.venue, symbol=sym, detail=sync.book.status_reason)
                if result != "out_of_sync" or sync.book.status != SYNCING:
                    sync.book.reset(SYNCING, f"resubscribing after {result}")
                    await ws.send_str(self._sub("unsubscribe", [sym]))
                    await ws.send_str(self._sub("subscribe", [sym]))


# ── Coinbase ───────────────────────────────────────────────────────────────────────────────────────────────────────
class CoinbaseFeed(BaseFeed):
    venue = "coinbase"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.syncs = {sym: CoinbaseBookSync(b) for sym, b in self.books.items()}
        self.seq = SequenceTracker()
        self.gaps = 0

    def _sub(self, kind: str, channel: str, products: Optional[List[str]] = None) -> str:
        m = {"type": kind, "channel": channel}
        if products:
            m["product_ids"] = products
        return json.dumps(m)

    async def _session(self):
        live = [s for s, b in self.books.items() if b.status != NOT_LISTED]
        if not live:
            await _wait(self.stop, 3600)
            return
        ws = await asyncio.wait_for(self._connect(ENDPOINTS["coinbase"]["ws"]), timeout=self.cfg.connect_timeout_s)
        self.connections += 1
        self.seq.reset()
        self.emit("CONNECTED", self.venue, detail="level2 + heartbeats")
        try:
            for s in live:
                self.books[s].reset(SYNCING, "subscribed; awaiting level2 snapshot")
            await ws.send_str(self._sub("subscribe", "heartbeats"))           # both within the 5 s subscribe window
            await ws.send_str(self._sub("subscribe", "level2", live))
            self._live = live
            await self._receive_loop(ws, self._handle)
        finally:
            await ws.close()

    async def _handle(self, text, rt, rm, ws):
        try:
            msg = json.loads(text)
        except ValueError:
            self.emit("MALFORMED", self.venue, detail=str(text)[:200])
            return
        if msg.get("type") == "error":
            self.emit("SERVER_ERROR", self.venue, detail=str(msg.get("message"))[:300])
            return
        seq_state = self.seq.check(msg.get("sequence_num"))
        if seq_state == "gap":
            self.gaps += 1
            self.emit("SEQUENCE_GAP", self.venue, detail=f"sequence_num jumped to {msg.get('sequence_num')}")
            for b in self.books.values():
                if b.status != NOT_LISTED:
                    b.gaps += 1
                    b.reset(SYNCING, "sequence gap — resubscribing level2 for a fresh snapshot")
            await ws.send_str(self._sub("unsubscribe", "level2", self._live))
            await ws.send_str(self._sub("subscribe", "level2", self._live))
            return
        if seq_state == "old":
            self.emit("OUT_OF_ORDER", self.venue, detail=f"sequence_num {msg.get('sequence_num')} ignored")
            return
        if msg.get("channel") != "l2_data":
            return                                                    # heartbeats / subscriptions
        for event in msg.get("events") or []:
            sym = event.get("product_id")
            sync = self.syncs.get(sym)
            if sync is None:
                continue
            result = sync.on_event(event, rt, rm)
            if result == "synced":
                sync.book.resyncs += 1
                self.emit("SYNCED", self.venue, symbol=sym, detail="level2 snapshot")
            elif result == "malformed":
                self.emit("MALFORMED", self.venue, symbol=sym, detail=sync.book.status_reason)


FEEDS = {"binance": BinanceFeed, "kraken": KrakenFeed, "coinbase": CoinbaseFeed}


# ── public REST helpers (listing check, server clock) ─────────────────────────────────────────────────────────────
async def check_listing(http: aiohttp.ClientSession, venue: str, symbol: str) -> Tuple[Optional[bool], str]:
    """True/False when the venue's public metadata answers; None when it cannot be checked (Kraken: decided by the
    subscription acknowledgement instead)."""
    try:
        if venue == "binance":
            async with http.get("https://api.binance.com/api/v3/exchangeInfo", params={"symbol": symbol},
                                timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    return False, f"exchangeInfo HTTP {r.status}"
                j = await r.json(content_type=None)
                st = (j.get("symbols") or [{}])[0].get("status")
                return st == "TRADING", f"status {st}"
        if venue == "coinbase":
            async with http.get(f"https://api.coinbase.com/api/v3/brokerage/market/products/{symbol}",
                                timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    return False, f"products HTTP {r.status}"
                j = await r.json(content_type=None)
                ok = not j.get("is_disabled") and not j.get("trading_disabled") and j.get("status", "online") == "online"
                return ok, f"status {j.get('status')}"
    except Exception as e:
        return None, f"listing check failed: {type(e).__name__}"
    return None, "checked by the subscription acknowledgement"


async def clock_offsets(http: aiohttp.ClientSession, venues) -> Dict[str, dict]:
    """server_time − local midpoint, with round-trip time. Kraken's public time has 1 s resolution (flagged)."""
    out = {}
    for v in venues:
        t0 = time.time_ns()
        try:
            async with http.get(ENDPOINTS[v]["rest_time"], timeout=aiohttp.ClientTimeout(total=15)) as r:
                j = await r.json(content_type=None)
            t1 = time.time_ns()
            if v == "binance":
                server_ns, res = int(j["serverTime"]) * 1_000_000, "1 ms"
            elif v == "kraken":
                server_ns, res = int(j["result"]["unixtime"]) * 1_000_000_000, "1 s (coarse)"
            else:
                server_ns, res = int(j["epochMillis"]) * 1_000_000, "1 ms"
            out[v] = {"offset_ms": (server_ns - (t0 + t1) / 2) / 1e6, "rtt_ms": (t1 - t0) / 1e6, "resolution": res,
                      "measured_utc_ns": t1}
        except Exception as e:
            out[v] = {"error": f"{type(e).__name__}: {e}"[:200]}
    return out
