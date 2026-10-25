# claude code changed: new file — 2026-10-25, Phase A recorder: session lifecycle, sampler, storage and provenance.
#
# Layout (never overwrites: every session gets a fresh directory created with exist_ok=False):
#   <out>/raw/<session_id>/obs-0001.jsonl …   one JSON object per (sample instant × venue × instrument)
#   <out>/raw/<session_id>/events.jsonl        connections, snapshots, gaps, checksum failures, resyncs, errors
#   <out>/metadata/<session_id>.json           config, provenance, listing/comparability, clock checks, summary
# Metadata is written at start with status RUNNING and refreshed every minute; a session whose metadata still says
# RUNNING with an old heartbeat_utc ended abnormally (crash / power loss) and is reported as INCOMPLETE by read_session.
import asyncio
import hashlib
import json
import os
import secrets
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bot.research.cross_exchange import RECORDER_VERSION, SCHEMA_VERSION
from bot.research.cross_exchange.books import NOT_LISTED, LocalBook, sample_row
from bot.research.cross_exchange.config import RecorderConfig

RUNNING, COMPLETED, STOPPED_BY_USER, FAILED, INCOMPLETE = "RUNNING", "COMPLETED", "STOPPED_BY_USER", "FAILED", "INCOMPLETE"
CLASSIFICATION = ("RAW MARKET DATA — research-only measurement; carries no evidence status and implies no opportunity, "
                  "edge or permission to trade")
SEAL_NOTE = ("Recorded inside the sealed crypto forward-holdout window (ZONES.F, 2026-10-04 onward, owner decision D-8). "
             "Recording is permitted; ANY analysis of these rows (Phase B) needs an explicit owner decision on how this "
             "new research program relates to the seal before it starts.")


def _iso(ns: Optional[int]) -> Optional[str]:
    return None if ns is None else datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat()


def new_session_id(now_ns: Optional[int] = None) -> str:
    t = datetime.fromtimestamp((now_ns or time.time_ns()) / 1e9, tz=timezone.utc)
    return t.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


def package_fingerprint() -> Dict[str, str]:
    here = Path(__file__).resolve().parent
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(here.glob("*.py"))}


class SessionStore:
    """Append-only files for one session. Every line is flushed when written; rotation by size."""

    def __init__(self, out_dir: str, session_id: str, rotate_mb: float):
        self.root = Path(out_dir)
        self.raw = self.root / "raw" / session_id
        self.meta_dir = self.root / "metadata"
        self.raw.mkdir(parents=True, exist_ok=False)                 # a session never reuses a directory
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.meta_dir / f"{session_id}.json"
        if self.meta_path.exists():
            raise FileExistsError(f"metadata for {session_id} already exists")
        self.rotate_bytes = int(rotate_mb * 1024 * 1024)
        self.part = 0
        self.files_written: List[str] = []
        self.bytes_written = 0
        self._obs = None
        self._events = open(self.raw / "events.jsonl", "a", encoding="utf-8", newline="\n")
        self.files_written.append(str((self.raw / "events.jsonl").as_posix()))
        self._open_next()

    def _open_next(self):
        if self._obs:
            self._obs.flush(); os.fsync(self._obs.fileno()); self._obs.close()
        self.part += 1
        path = self.raw / f"obs-{self.part:04d}.jsonl"
        self._obs = open(path, "x", encoding="utf-8", newline="\n")  # "x": fail rather than overwrite
        self.files_written.append(str(path.as_posix()))

    def write_obs(self, rows: List[dict]) -> None:
        for r in rows:
            line = json.dumps(r, separators=(",", ":")) + "\n"
            self._obs.write(line)
            self.bytes_written += len(line.encode("utf-8"))
        self._obs.flush()
        if self._obs.tell() >= self.rotate_bytes:
            self._open_next()

    def write_event(self, ev: dict) -> None:
        line = json.dumps(ev, separators=(",", ":")) + "\n"
        self._events.write(line); self._events.flush()
        self.bytes_written += len(line.encode("utf-8"))

    def write_metadata(self, meta: dict) -> None:
        tmp = self.meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self.meta_path)                              # atomic: a reader never sees half a file

    def close(self) -> None:
        for f in (self._obs, self._events):
            if f and not f.closed:
                f.flush(); os.fsync(f.fileno()); f.close()


class Recorder:
    def __init__(self, cfg: RecorderConfig, session_id: Optional[str] = None, feeds: Optional[Dict] = None,
                 http_factory=None, clock_fn=None, listing_fn=None):
        self.cfg = cfg
        self.session_id = session_id or new_session_id()
        self.config_id = hashlib.sha256(json.dumps(cfg.as_dict(), sort_keys=True).encode()).hexdigest()[:16]
        self.books: Dict[str, Dict[str, LocalBook]] = defaultdict(dict)
        for inst in cfg.instruments:
            for vs in inst.venues:
                if vs.venue in cfg.venues:
                    self.books[vs.venue][vs.symbol] = LocalBook(vs.venue, inst.key, vs.symbol, vs.base, vs.quote)
        self.store = SessionStore(cfg.out_dir, self.session_id, cfg.rotate_mb)
        self._feed_classes = feeds                                   # injectable for tests (mock feeds)
        self._http_factory = http_factory
        self._clock_fn = clock_fn
        self._listing_fn = listing_fn
        self.stop: Optional[asyncio.Event] = None   # created inside run(), on the running loop
        self.seq = 0
        self.rows_by_book: Counter = Counter()
        self.usable_by_book: Counter = Counter()
        self.flags_by_book: Dict[str, Counter] = defaultdict(Counter)
        self.events: Counter = Counter()
        self.clock_checks: List[dict] = []
        self.listing: Dict[str, dict] = {}
        self.start_utc_ns = time.time_ns()
        self.start_mono_ns = time.monotonic_ns()
        self.end_utc_ns: Optional[int] = None
        self.status = RUNNING
        self.feeds = {}

    # ── events / metadata ───────────────────────────────────────────────────────────────────────────────────────
    def emit(self, kind: str, venue: str, symbol: Optional[str] = None, detail: str = "") -> None:
        now = time.time_ns()
        self.events[(venue, kind)] += 1
        self.store.write_event({"session_id": self.session_id, "utc_ns": now, "utc": _iso(now),
                                "mono_ns": time.monotonic_ns(), "venue": venue, "symbol": symbol, "kind": kind,
                                "detail": detail})

    def metadata(self) -> dict:
        from bot.quant_core.provenance import code_state
        code = code_state()
        now = time.time_ns()
        books = {}
        for v, bs in self.books.items():
            for s, b in bs.items():
                k = f"{v}:{s}"
                books[k] = {"instrument": b.instrument, "status": b.status, "status_reason": b.status_reason,
                            "rows": self.rows_by_book[k], "usable_rows": self.usable_by_book[k],
                            "flags": dict(self.flags_by_book[k]), "updates": b.updates_total, "resyncs": b.resyncs,
                            "gaps": b.gaps, "checksum_failures": b.checksum_failures, "malformed": b.malformed,
                            "rejected_levels": b.rejected_levels}
        dur = ((self.end_utc_ns or now) - self.start_utc_ns) / 1e9
        return {
            "kind": "XARB_RECORDER_SESSION", "session_id": self.session_id, "status": self.status,
            "classification": CLASSIFICATION, "governance": SEAL_NOTE,
            "research_only": True, "phase": "A — measurement only; Phase B/C/D not implemented",
            "recorder_version": RECORDER_VERSION, "schema_version": SCHEMA_VERSION, "config_id": self.config_id,
            "config": self.cfg.as_dict(), "comparability": [i.comparability() for i in self.cfg.instruments],
            "listing": self.listing, "code": {"commit": code.commit, "dirty": code.dirty, "package_sha256": package_fingerprint()},
            "start_utc": _iso(self.start_utc_ns), "end_utc": _iso(self.end_utc_ns), "heartbeat_utc": _iso(now),
            "duration_s": dur, "samples_written": self.seq, "books": books,
            "events": {f"{v}:{k}": n for (v, k), n in sorted(self.events.items())},
            "clock_checks": self.clock_checks,
            "connections": {v: {"connections": f.connections, "reconnect_waits": f.disconnects} for v, f in self.feeds.items()},
            "files_written": self.store.files_written, "bytes_written": self.store.bytes_written,
            "bytes_per_hour_observed": (self.store.bytes_written / dur * 3600) if dur > 0 else None,
            "clock_assumptions": ("Local timestamps come from this PC's clock (time.time_ns, Windows time service); "
                                  "exchange timestamps from each venue. clock_checks records server−local offsets with "
                                  "round-trip times; Kraken's public time has 1 s resolution. Cross-venue comparisons "
                                  "should use exchange timestamps where present and treat sub-RTT differences as unknown."),
            "timestamp_fields": {"exchange_ts_ns": "venue event time of the last applied change (Binance E; Kraken "
                                 "update timestamp; Coinbase max(event_time) in the message)",
                                 "receipt_utc_ns": "local wall-clock time the message was received",
                                 "receipt_mono_ns": "local monotonic clock — for durations only, not a calendar time",
                                 "sample_utc_ns": "when this row was sampled (sampling ≠ the feed's update rate)"},
        }

    # ── main loop ───────────────────────────────────────────────────────────────────────────────────────────────
    async def _sample_loop(self, stop: asyncio.Event):
        interval = self.cfg.sample_interval_s
        nxt = time.monotonic()
        while not stop.is_set():
            nxt += interval
            now_utc, now_mono = time.time_ns(), time.monotonic_ns()
            rows = []
            for v, bs in self.books.items():
                for s, b in bs.items():
                    self.seq += 1
                    row = sample_row(b, depth=self.cfg.depth, now_utc_ns=now_utc, now_mono_ns=now_mono,
                                     stale_after_s=self.cfg.stale_after_s, future_tol_s=self.cfg.future_ts_tolerance_s,
                                     session_id=self.session_id, config_id=self.config_id, seq=self.seq)
                    k = f"{v}:{s}"
                    self.rows_by_book[k] += 1
                    self.usable_by_book[k] += row["usable"]
                    for f in row["flags"]:
                        self.flags_by_book[k][f] += 1
                    if not row["usable"]:
                        self.flags_by_book[k]["UNUSABLE:" + row["status"]] += 1
                    rows.append(row)
            self.store.write_obs(rows)
            delay = nxt - time.monotonic()
            if delay < 0:
                nxt = time.monotonic()                               # fell behind: do not burst-catch-up
                delay = 0
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _heartbeat_loop(self, stop: asyncio.Event, http):
        last_clock = 0.0
        while not stop.is_set():
            if self._clock_fn and time.monotonic() - last_clock >= self.cfg.clock_check_every_s:
                try:
                    self.clock_checks.append({"utc": _iso(time.time_ns()), "offsets": await self._clock_fn(http, self.cfg.venues)})
                except Exception as e:
                    self.clock_checks.append({"utc": _iso(time.time_ns()), "error": f"{type(e).__name__}: {e}"[:200]})
                last_clock = time.monotonic()
            self.store.write_metadata(self.metadata())
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    async def run(self) -> dict:
        import aiohttp
        from bot.research.cross_exchange import feeds as feeds_mod
        feed_classes = self._feed_classes or feeds_mod.FEEDS
        stop = asyncio.Event()
        self.stop = stop
        tasks: List[asyncio.Task] = []
        # claude code changed: 2026-10-25 — the OS resolver (ThreadedResolver): aiohttp's optional aiodns/c-ares
        # resolver could not find this Windows machine's DNS servers ("Could not contact DNS servers") in the smoke run
        http = self._http_factory() if self._http_factory else aiohttp.ClientSession(
            headers={"User-Agent": "SQT-research-recorder/1"},
            connector=aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver()))
        try:
            if self._listing_fn:
                for v, bs in self.books.items():
                    for s, b in bs.items():
                        ok, why = await self._listing_fn(http, v, s)
                        self.listing[f"{v}:{s}"] = {"listed": ok, "detail": why}
                        if ok is False:
                            b.reset(NOT_LISTED, why)
                            self.emit("NOT_LISTED", v, s, why)
            self.store.write_metadata(self.metadata())
            for v in self.cfg.venues:
                feed = feed_classes[v](self.cfg, self.books[v], http, self.emit, stop)
                self.feeds[v] = feed
                tasks.append(asyncio.create_task(feed.run(), name=f"feed-{v}"))
            tasks.append(asyncio.create_task(self._sample_loop(stop), name="sampler"))
            tasks.append(asyncio.create_task(self._heartbeat_loop(stop, http), name="heartbeat"))
            if self.cfg.max_duration_s:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.cfg.max_duration_s)
                except asyncio.TimeoutError:
                    pass
                self.status = COMPLETED
            else:
                await stop.wait()
                self.status = COMPLETED
        except asyncio.CancelledError:
            self.status = STOPPED_BY_USER                            # Ctrl+C: asyncio cancels the main task
            raise
        except Exception as e:
            self.status = FAILED
            self.emit("RECORDER_FAILED", "recorder", detail=f"{type(e).__name__}: {e}"[:300])
            raise
        finally:
            stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await http.close()
            self.end_utc_ns = time.time_ns()
            meta = self.metadata()
            self.store.write_metadata(meta)
            self.store.close()
        return meta


def summary_text(meta: dict) -> str:
    lines = [f"Session {meta['session_id']}  status {meta['status']}",
             f"  {meta['start_utc']} → {meta['end_utc']}  ({meta['duration_s']:.0f} s), {meta['samples_written']} rows"]
    for k, b in sorted(meta["books"].items()):
        lines.append(f"  {k:24s} rows {b['rows']:6d}  usable {b['usable_rows']:6d}  updates {b['updates']:8d}  "
                     f"resyncs {b['resyncs']}  gaps {b['gaps']}  checksum fails {b['checksum_failures']}  "
                     f"status {b['status']}  flags {b['flags']}")
    for v, c in (meta.get("connections") or {}).items():
        lines.append(f"  {v:10s} connections {c['connections']}  reconnect waits {c['reconnect_waits']}")
    lines.append(f"  files: {len(meta['files_written'])}  bytes: {meta['bytes_written']:,}  "
                 f"≈ {(meta.get('bytes_per_hour_observed') or 0) / 1e6:,.1f} MB/hour at this configuration")
    return "\n".join(lines)


def read_session(out_dir: str, session_id: str, stale_heartbeat_s: float = 180) -> Tuple[dict, List[dict], List[dict]]:
    """Read a session back: (metadata, observations, events). RUNNING with an old heartbeat ⇒ reported INCOMPLETE."""
    root = Path(out_dir)
    meta = _effective_status(json.loads((root / "metadata" / f"{session_id}.json").read_text(encoding="utf-8")),
                             stale_heartbeat_s)   # claude code changed: 2026-10-25 — shared with latest_books()
    obs = []
    for p in sorted((root / "raw" / session_id).glob("obs-*.jsonl")):
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        obs.append(json.loads(line))
                    except ValueError:
                        obs.append({"_unreadable_line": True, "file": p.name})   # a torn last line after a crash
    events = []
    ev = root / "raw" / session_id / "events.jsonl"
    if ev.exists():
        events = [json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines() if l.strip()]
    return meta, obs, events


# claude code changed: 2026-10-25 — read-only viewer support (Venue Comparison "Recorded order books" panel).
def _effective_status(meta: dict, stale_heartbeat_s: float = 180) -> dict:
    """RUNNING with a heartbeat older than stale_heartbeat_s ⇒ INCOMPLETE (the process died without closing)."""
    if meta.get("status") == RUNNING and meta.get("heartbeat_utc"):
        age = time.time() - datetime.fromisoformat(meta["heartbeat_utc"]).timestamp()
        if age > stale_heartbeat_s:
            meta = {**meta, "status": INCOMPLETE, "status_note": f"no heartbeat for {age:.0f}s — ended abnormally"}
    return meta


def latest_session_id(out_dir: str) -> Optional[str]:
    """Newest session by id (ids start with a UTC timestamp, so they sort chronologically); None if none recorded."""
    d = Path(out_dir) / "metadata"
    ids = sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []
    return ids[-1] if ids else None


def latest_books(out_dir: str, session_id: Optional[str] = None, tail_bytes: int = 512 * 1024,
                 max_files: int = 2, stale_heartbeat_s: float = 180) -> Tuple[Optional[dict], Dict[str, dict]]:
    """The most recent sample row of every book in a session, read from the END of the newest observation file(s)
    only — a multi-GB session costs one small read. Returns (metadata, {"venue:symbol": row}); (None, {}) when no
    session exists. Rows are returned exactly as recorded (a STALE / DISCONNECTED book has no levels — no fill-in).
    Torn or partial lines (the recorder may be mid-write) are skipped, never guessed."""
    session_id = session_id or latest_session_id(out_dir)
    if not session_id:
        return None, {}
    root = Path(out_dir)
    meta_path = root / "metadata" / f"{session_id}.json"
    if not meta_path.exists():
        return None, {}
    meta = _effective_status(json.loads(meta_path.read_text(encoding="utf-8")), stale_heartbeat_s)
    wanted = set((meta.get("books") or {}).keys())
    found: Dict[str, dict] = {}
    for p in sorted((root / "raw" / session_id).glob("obs-*.jsonl"), reverse=True)[:max_files]:
        with open(p, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            start = max(0, size - tail_bytes)
            fh.seek(start)
            lines = fh.read().split(b"\n")
        if start > 0:
            lines = lines[1:]                                          # first line is cut by the seek
        for raw in reversed(lines):
            try:
                row = json.loads(raw)
            except ValueError:
                continue                                               # empty / torn line
            key = f"{row.get('venue')}:{row.get('venue_symbol')}"
            if key not in found:
                found[key] = row
        if wanted and wanted <= found.keys():
            break
    return meta, found
