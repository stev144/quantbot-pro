# Cross-Exchange Statistical Arbitrage Research — Phase A: Synchronized Market-Data Recorder (2026-10-25)

**Status:** Phase A implemented and tested. **Research-only.** No orders, no signals, no profitability claim.
Phase B, Phase C and any execution are **not** implemented.

---

## 1. Repository findings (verified)

| Item | Finding |
|---|---|
| Project root | `C:\Users\HP\trading_bot_project` (Django 6; deps only in `venv/`) |
| Existing order-book code | Two existing modules, neither of which records anything: `bot/research/orderbook_depth_engine.py` reads Binance's *historical futures* `bookDepth` archive; `bot/engines/liquidity.py` / `execution_comparison.py` take single live snapshots for venue cost comparison. No duplication. |
| WebSocket libraries | `aiohttp` 3.13.5 is installed and already used by `bot/data_fetcher.py`. `ccxt.pro` (inside ccxt 4.5.51) is also installed. |
| Why not ccxt.pro | Its Kraken checksum check is **off by default** (`'checksum': False`, with the code comment "exchange checksum was not reliable"). It has **no Coinbase sequence-gap detection**. It also hides raw sequence numbers. The recorder therefore uses direct aiohttp clients that follow each official procedure. |
| Storage libraries | No `pyarrow` → **JSON Lines** (append-friendly, inspectable, crash-tolerant per line). |
| DNS | aiohttp's optional `aiodns` resolver fails on this PC ("Could not contact DNS servers"). The recorder uses the OS resolver (`aiohttp.ThreadedResolver`). |

### Five gates

The canonical source is `FULL_RESEARCH_PIPELINE_FIVE_GATE_AUDIT_2026-10-03.md` §4. The brief numbers the gates differently; this is the mapping:

| Brief's gate | Canonical gate | Official wording (§4) |
|---|---|---|
| Gate 1 Research integrity | **G5** | "Provenance (data, code, method, protocol, run ID), no look-ahead or selection leakage, reproducible, classified" |
| Gate 2 Quant/trading | **G4** | "Causal timing, next-bar execution, realistic costs/borrow/liquidity, liquidation-value risk, censoring" |
| Gate 3 Pure maths | **G2** | "Formulas, units, signs and indexing verified by known answers or invariants" |
| Gate 4 Statistics | **G3** | "An appropriate null, demonstrated size ≈ α and power, dependence handled, multiplicity controlled, honest OOS" |
| **Gate 5 ("not established")** | **G1 Software** | "Correct I/O, state and timestamps; edge cases handled; no silent fallbacks; tests that would fail on the defect" |

Gate 5 is therefore **resolved**: it is the Software gate.

---

## 2. Official interfaces consulted (2026-10-25)

| Venue | Source | Interface used | Procedure implemented |
|---|---|---|---|
| Binance | `github.com/binance/binance-spot-api-docs` `web-socket-streams.md` (last change 2026-10-15; the docs website truncated this section) | `wss://stream.binance.com:9443/stream?streams=<sym>@depth@100ms` plus REST `api/v3/depth` (limit 1000) | The official 7-step local-book procedure: buffer → snapshot → `lastUpdateId < first U` ⇒ refetch → discard `u ≤ lastUpdateId` → apply. Per event, `u < id` ⇒ ignore and `U > id+1` ⇒ **gap ⇒ restart**. Limits respected: 24 h connection life, ping every 20 s (aiohttp auto-pong), ≤ 5 incoming msg/s (none sent after connect), ≤ 1024 streams. Event time `E` (ms). |
| Kraken | `docs.kraken.com/api/docs/websocket-v2/book` and `guides/spot-ws-book-v2` | `wss://ws.kraken.com/v2`, `book` channel, depth ∈ {10, 25, 100, 500, 1000}; the subscribed depth is the smallest allowed ≥ max(requested, 10) | Snapshot then updates; qty 0 removes a level; truncate to depth; **CRC32 checksum on every message** over the top 10. Kraken does not say what to do on a mismatch, so the recorder **invalidates and resubscribes** (its own documented choice). RFC 3339 update timestamp. JSON parsed with `Decimal` as Kraken advises. |
| Coinbase | `docs.cdp.coinbase.com` Advanced Trade WebSocket overview, channels, `level2`, `heartbeats` | `wss://advanced-trade-ws.coinbase.com`; `level2` + `heartbeats`, both subscribed within the 5 s window; no JWT (doc: "A JWT is not required") | `l2_data` snapshot/update, absolute `new_quantity` (0 removes), `event_time` per update. `sequence_num` > previous + 1 ⇒ **gap ⇒ resubscribe** for a fresh snapshot. The docs disagree on whether the sequence is per product or per connection; the stricter **per-connection** check is used, and the live runs (≈4,000 messages, 0 gaps) are consistent with it. |

Public time endpoints, used for clock checks only:
- Binance `api/v3/time` (1 ms)
- Kraken `0/public/Time` (**1 s resolution**)
- Coinbase `api/v3/brokerage/time` (1 ms)

Listing checks:
- Binance `api/v3/exchangeInfo?symbol=`
- Coinbase `api/v3/brokerage/market/products/{id}`
- Kraken: the subscription acknowledgement

---

## 3. Architecture

```
bot/research/cross_exchange/
  __init__.py    RECORDER_VERSION SQT-XARB-REC-1, SCHEMA_VERSION xarb-obs-v1
  config.py      Instrument/VenueSymbol mapping (each venue's own identifier), comparability classes, validated config
  books.py       PURE: LocalBook, BinanceSync, KrakenSync (+ kraken_checksum), CoinbaseBookSync, SequenceTracker,
                 RFC 3339 → ns, exact Decimal handling, sample_row (quality flags)
  feeds.py       aiohttp WebSocket clients; reconnect with capped exponential backoff (2 s → 60 s); silence watchdog 30 s;
                 books marked DISCONNECTED the moment a connection drops; listing + clock helpers
  recorder.py    session ID, SessionStore (JSONL, rotation, flush/fsync, atomic metadata), sampler, heartbeat, summary,
                 read_session (round trip; RUNNING + old heartbeat ⇒ INCOMPLETE; torn last line tolerated)
bot/management/commands/research_record_cross_exchange_books.py   manual start, banner, Ctrl+C stop, summary
bot/tests/test_cross_exchange_recorder.py                         28 offline tests (mocked messages)
```

### Comparability
Each instrument keeps its base and quote currency per venue:
- **DIRECT:** same quote everywhere. This covers the defaults: BTC, ETH, SOL and XRP against USDT on all three venues.
- **CONVERSION_REQUIRED:** quotes differ, e.g. USD vs USDT. These are stored, and **never converted**.
- **NOT_COMPARABLE:** fewer than two venues carry the instrument.

### Observation row
One row per (sample instant × venue × instrument):
- **Identity and provenance:** `schema`, `session_id`, `config_id`, `seq`, `venue`, `instrument`, `venue_symbol`, `base`, `quote`.
- **Status:** `status` (SYNCED / SYNCING / INVALID / DISCONNECTED / NOT_LISTED), `status_reason`, `usable`, `flags`.
- **Timestamps:**
  - `exchange_ts_ns`: venue event time.
  - `receipt_utc_ns`: local wall clock.
  - `receipt_mono_ns`: local monotonic clock, durations only.
  - `sample_utc_ns` / `sample_mono_ns`: when the row was sampled.
- **Ages and delay:** `book_age_s`, `feed_age_s`, `feed_delay_ms`.
- **Sequence:** `update_id`, and `updates_since_last_sample`, which separates sampling from the feed's update rate.
- **Book:** `best_bid`/`best_ask` plus their quantities, and `bids`/`asks` (top N as exact decimal strings).
- **Units:** `units`.

### Quality flags
| Flag | Meaning | Row usable? |
|---|---|---|
| `STALE` | The connection has been silent longer than `stale_after_s` | No |
| `QUIET_BOOK` | The connection is alive but this book hasn't changed | Yes |
| `CROSSED` | Best bid ≥ best ask | No |
| `EMPTY_SIDE` | One side of the book is empty | No |
| `CLOCK_ANOMALY` | The exchange timestamp is later than local receipt by more than the tolerance | — (flagged) |
| `NO_EXCHANGE_TIMESTAMP` | The venue supplied no event time | — (flagged) |

A row that isn't usable carries **no prices**. There is no forward fill and no interpolation.

### Files
- `research_data/cross_exchange_arb/raw/<session>/obs-NNNN.jsonl` (rotating, 64 MB default) and `events.jsonl`:
  - connects, snapshots, gaps, checksum failures, resyncs;
  - silences, malformed messages, errors.
- `research_data/cross_exchange_arb/metadata/<session>.json`:
  - config, `config_id`, recorder and schema versions, package sha256 per file, git commit/dirty;
  - listing, comparability, clock checks, per-book counters, files, bytes;
  - status, governance note.
- Session directories are created with `exist_ok=False` and observation files with mode `"x"`, so nothing is ever overwritten.

---

## 4. How to run and stop

```
venv\Scripts\python.exe manage.py research_record_cross_exchange_books                 # until Ctrl+C
venv\Scripts\python.exe manage.py research_record_cross_exchange_books --minutes 60 --interval 2 --depth 10
venv\Scripts\python.exe manage.py research_record_cross_exchange_books --assets BTC ETH --venues binance kraken
```

- **Ctrl+C:** cancels cleanly, flushes and fsyncs files, writes status `STOPPED_BY_USER` and prints the summary.
- **`--minutes`:** ends with `COMPLETED`.
- **A crash or power loss** leaves `RUNNING` with an old heartbeat, which `read_session` reports as `INCOMPLETE`.

Nothing starts automatically: there is no scheduler, no daemon and no import from the trading bot.

---

## 5. Tests and evidence

| Command | Result |
|---|---|
| `manage.py test --keepdb bot.tests.test_cross_exchange_recorder` | **28 OK** (offline, mocked messages) |
| Live smoke 1: `research_record_cross_exchange_books --minutes 2 --out <scratch>` | **Failed honestly**: DNS error (aiodns). Every row was DISCONNECTED and unusable, with no prices. This led to the resolver fix. |
| Live smoke 2 (after the fix) | All 3 venues connected and synced. Kraken: 0 checksum failures in 1,825–5,190 updates per book. Coinbase: 0 sequence gaps. Binance: snapshot sync OK, 0 gaps. |
| Live smoke 3 (after the quiet-book fix) | Same results; Coinbase XRP quiet periods recorded as usable + `QUIET_BOOK` |

What the offline tests cover:
- RFC 3339 nanosecond and offset parsing;
- exact Decimal handling, with floats refused;
- Kraken's **published checksum example → 3310070434**;
- Kraken snapshot, update, mismatch (invalidate and clear), update-before-snapshot, depth truncation and depth mapping;
- Binance buffer / retry / discard / apply / gap / ignore / malformed;
- Coinbase sequence tracking (first, ok, gap, old, malformed) and snapshot / update / bad side;
- sample rows: exact strings, units, counter reset, STALE vs QUIET_BOOK, no forward fill for each non-synced status, CROSSED / EMPTY_SIDE / CLOCK_ANOMALY / NO_EXCHANGE_TIMESTAMP;
- config validation and comparability classes;
- the recorder with mock feeds: round trip, simulated disconnect, events, governance note;
- unique sessions, rotation, a torn last line, INCOMPLETE detection, user stop (cancellation) → STOPPED_BY_USER, NOT_LISTED;
- feed handlers: Kraken rejection → NOT_LISTED, Coinbase gap → resubscribe, Binance malformed message;
- **safety (code parsed with `ast`, not text):**
  - no order, cancel, withdraw, transfer, balance, key, secret or signing identifiers;
  - no imports of `bot.engines`, `bot.core`, `bot.risk`, `bot.journal`, `bot.pairs`, ccxt or schedulers;
  - no environment-variable reads;
  - the live bot, execution engine and order manager don't reference the package;
  - the only hosts are the six public market-data hosts.

Regression: no existing file was changed by Phase A. Only new files were added; the shared changes in this working tree are the separate exchange-logo work. A full-suite run is still required before committing.

---

## 6. Measured limitations (from the smoke sessions)

- **Local clock offset:** the server − local midpoint was −131 ms for Binance and Coinbase, so **this PC's clock runs ≈ 131 ms ahead**. Kraken read −745 ms, but its 1 s resolution makes that uninformative.
- **Effect on feed delay:** `feed_delay_ms` (receipt − exchange time) includes that clock error. Measured medians: Binance 238 ms, Kraken 219 ms, Coinbase 358 ms. Binance's 95th percentile was 2.1 s on the current, bandwidth-limited connection.
- **Alignment:** cross-venue alignment is reliable only to roughly the sum of the round-trip times (≈ 200–375 ms) plus clock error. Sub-second "gaps" between venues must not be treated as real. The data records this; it doesn't hide it.
- **Sampling:** `--interval 1` samples the latest book each second. Changes between samples are counted (`updates_since_last_sample`) but not stored individually.
- **Binance:** the 1,000-level REST snapshot means levels beyond it are unknown until they change (as Binance's own docs note). Only the top N are recorded anyway.
- **Coinbase sequence scope:** the docs are inconsistent; the stricter per-connection reading is applied.
- **Storage:** observed **≈ 55 MB/hour** at 12 books, 1 s and 10 levels (about 1,275 bytes per row), so **≈ 1.3 GB/day**. Scale it linearly by books ÷ interval × depth; `--interval 5 --depth 5` is about one-eighth of that.

---

## 7. Five-gate assessment (Phase A only)

| Canonical gate | Applies? | Verification and evidence | Status | Remaining limitations |
|---|---|---|---|---|
| **G1 Software** (brief's "Gate 5") | Yes | Correct I/O and state machine, fail-closed books, no forward fill, no silent fallback (a failed connection was recorded as DISCONNECTED, not as prices); 28 tests that fail on each defect; 3 live runs | **PASS** | Long-session behaviour (24 h reconnect, multi-day rotation) not yet observed live |
| **G2 Mathematics** | Yes | Kraken checksum known answer 3310070434 plus 0 live failures; ns timestamp conversion; exact Decimal; units recorded per row | **PASS** | — |
| **G3 Statistics** | No statistical claim is made | — | **NOT APPLICABLE** (not a pass) | Any Phase B claim needs a dependence-aware design |
| **G4 Quant / trading** | Partly | The executable sides (bid/ask/depth per venue) are preserved; gaps are never treated as opportunities; comparability is explicit; no fees embedded; feed age and delay recorded | **PARTIAL** | Clock offset (≈ 131 ms), feed-delay tail, 1 s sampling, Kraken 1 s time resolution: sub-second alignment can't be established |
| **G5 Research integrity** | Yes | Session, config, schema, package sha, code state, immutable raw files, atomic metadata, classification "RAW MARKET DATA" | **PASS for recording** · **BLOCKED for Phase B** | All recorded rows fall **inside the sealed crypto forward-holdout window ZONES.F (≥ 2026-10-04, decision D-8)**. Analysing them needs an explicit owner decision first (written into every session's metadata). |

## 8. Not implemented (by design)
- **Phase B (gap study):** no gross or net gap calculation, no profitability page, no fee model.
- **Phase C (preregistration).**
- **Execution:** no order path. It would also conflict with the owner rule "each venue must fetch its own data/signal, never share another venue's signal to execute".

A working recorder is **not** evidence of an opportunity, and a recorded price difference is **not** a profit.
