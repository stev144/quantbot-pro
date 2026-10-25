# claude code changed: new file — 2026-10-25, cross-exchange recorder Phase A. OFFLINE tests with mocked market-data
# messages (no network, no credentials, no orders). The live public-data smoke test is the explicit command
# `manage.py research_record_cross_exchange_books --minutes 2 --out <dir>` and is NOT part of this suite.
import asyncio
import json
import re
import shutil
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase

from bot.research.cross_exchange import books as bk
from bot.research.cross_exchange import config as cf
from bot.research.cross_exchange import feeds as fd
from bot.research.cross_exchange import recorder as rc

D = Decimal
PKG = Path(bk.__file__).resolve().parent


def book(venue="kraken", symbol="BTC/USDT"):
    return bk.LocalBook(venue, "BTC-USDT", symbol, "BTC", "USDT")


# Kraken's own worked example (docs.kraken.com guides/spot-ws-book-v2): expected checksum 3310070434
KRAKEN_EXAMPLE = {
    "bids": [("45283.5", "0.10000000"), ("45283.4", "1.54582015"), ("45282.1", "0.10000000"), ("45281.0", "0.10000000"),
             ("45280.3", "1.54592586"), ("45279.0", "0.07990000"), ("45277.6", "0.03310103"), ("45277.5", "0.30000000"),
             ("45277.3", "1.54602737"), ("45276.6", "0.15445238")],
    "asks": [("45285.2", "0.00100000"), ("45286.4", "1.54571953"), ("45286.6", "1.54571109"), ("45289.6", "1.54560911"),
             ("45290.2", "0.15890660"), ("45291.8", "1.54553491"), ("45294.7", "0.04454749"), ("45296.1", "0.35380000"),
             ("45297.5", "0.09945542"), ("45299.5", "0.18772827")],
}


def kraken_msg(bids, asks, checksum, ts="2026-10-25T10:00:00.123456Z"):
    return {"symbol": "BTC/USDT", "bids": [{"price": D(p), "qty": D(q)} for p, q in bids],
            "asks": [{"price": D(p), "qty": D(q)} for p, q in asks], "checksum": checksum, "timestamp": ts}


class TimestampAndNumberTests(SimpleTestCase):
    def test_rfc3339_nanoseconds_and_offsets(self):
        self.assertEqual(bk.parse_rfc3339_ns("2023-02-09T20:32:50.714964855Z"), 1675974770714964855)
        self.assertEqual(bk.parse_rfc3339_ns("2023-02-09T22:32:50.5+02:00"), 1675974770500000000)
        self.assertEqual(bk.parse_rfc3339_ns("2023-02-09T20:32:50Z"), 1675974770000000000)
        for bad in ("2023-02-09 20:32:50", "2023-13-09T20:32:50Z", "", None, 1675974770, "2023-02-09T25:00:00Z"):
            self.assertIsNone(bk.parse_rfc3339_ns(bad), bad)

    def test_decimals_are_exact_and_floats_refused(self):
        self.assertEqual(str(bk.to_decimal("0.10000000")), "0.10000000")
        for bad in (0.1, float("nan"), "abc", None, True, "Infinity"):
            self.assertIsNone(bk.to_decimal(bad), bad)

    def test_invalid_levels_are_rejected_and_counted(self):
        b = book()
        self.assertFalse(b.set_level("bid", "-1", "1"))
        self.assertFalse(b.set_level("bid", "100", "-0.5"))
        self.assertFalse(b.set_level("ask", 100.0, "1"))       # float: exact text already lost
        self.assertEqual(b.rejected_levels, 3)
        self.assertTrue(b.set_level("bid", "100", "2")); self.assertTrue(b.set_level("bid", "100", "0"))
        self.assertEqual(b.bids, {})                            # qty 0 removes the level


class KrakenTests(SimpleTestCase):
    def test_checksum_matches_krakens_published_example(self):
        bids = [(D(p), D(q)) for p, q in KRAKEN_EXAMPLE["bids"]]
        asks = [(D(p), D(q)) for p, q in KRAKEN_EXAMPLE["asks"]]
        self.assertEqual(bk.kraken_checksum(bids, asks), 3310070434)

    def test_snapshot_update_and_mismatch(self):
        b = book(); s = bk.KrakenSync(b, depth=10)
        snap = kraken_msg(KRAKEN_EXAMPLE["bids"], KRAKEN_EXAMPLE["asks"], 3310070434)
        self.assertEqual(s.on_message("snapshot", snap, 1, 1), "synced")
        self.assertEqual(b.status, bk.SYNCED)
        self.assertEqual(b.exchange_ts_ns, bk.parse_rfc3339_ns("2026-10-25T10:00:00.123456Z"))
        # a valid update: remove the best bid, checksum recomputed on the new top 10
        new_bids = [(p, q) for p, q in KRAKEN_EXAMPLE["bids"][1:]]
        good = bk.kraken_checksum([(D(p), D(q)) for p, q in new_bids], [(D(p), D(q)) for p, q in KRAKEN_EXAMPLE["asks"]])
        upd = {"symbol": "BTC/USDT", "bids": [{"price": D("45283.5"), "qty": D("0")}], "asks": [], "checksum": good,
               "timestamp": "2026-10-25T10:00:01Z"}
        self.assertEqual(s.on_message("update", upd, 2, 2), "applied")
        self.assertNotIn(D("45283.5"), b.bids)
        bad = dict(upd, bids=[{"price": D("45283.4"), "qty": D("9")}], checksum=1)
        self.assertEqual(s.on_message("update", bad, 3, 3), "checksum_mismatch")
        self.assertEqual((b.status, b.bids, b.asks, b.checksum_failures), (bk.INVALID, {}, {}, 1))   # nothing stale kept

    def test_update_before_snapshot_is_not_applied_and_depth_is_truncated(self):
        b = book(); s = bk.KrakenSync(b, depth=10)
        self.assertEqual(s.on_message("update", kraken_msg([("1", "1")], [("2", "1")], 0), 1, 1), "out_of_sync")
        self.assertEqual(b.bids, {})
        many = [(str(45000 - i), "1") for i in range(15)]
        asks = [(str(46000 + i), "1") for i in range(15)]
        cs = bk.kraken_checksum([(D(p), D(q)) for p, q in many], [(D(p), D(q)) for p, q in asks])
        s.on_message("snapshot", kraken_msg(many, asks, cs), 1, 1)
        self.assertEqual((len(b.bids), len(b.asks)), (10, 10))

    def test_subscription_depth_mapping(self):
        self.assertEqual(cf.RecorderConfig(depth=5).kraken_subscription_depth(), 10)    # checksum needs the top 10
        self.assertEqual(cf.RecorderConfig(depth=20).kraken_subscription_depth(), 25)


class BinanceTests(SimpleTestCase):
    def ev(self, U, u, E=1_700_000_000_000, b=(), a=()):
        return {"e": "depthUpdate", "E": E, "s": "BTCUSDT", "U": U, "u": u, "b": [list(x) for x in b], "a": [list(x) for x in a]}

    def test_official_procedure_buffer_snapshot_discard_apply(self):
        b = book("binance", "BTCUSDT"); s = bk.BinanceSync(b)
        for U, u in ((100, 105), (106, 110), (111, 115)):
            self.assertEqual(s.on_event(self.ev(U, u, b=[("50000", str(u))]), 1, 1), "buffered")
        self.assertEqual(s.on_snapshot({"lastUpdateId": 99, "bids": [], "asks": []}), "retry")   # older than first U
        snap = {"lastUpdateId": 108, "bids": [["49999", "1"]], "asks": [["50001", "1"]]}
        self.assertEqual(s.on_snapshot(snap), "synced")
        self.assertEqual(b.update_id, 115)                      # (100,105) discarded; (106,110) and (111,115) applied
        self.assertEqual(b.bids[D("50000")], D("115"))
        self.assertEqual(b.exchange_ts_ns, 1_700_000_000_000 * 1_000_000)

    def test_gap_restarts_and_old_events_are_ignored(self):
        b = book("binance", "BTCUSDT"); s = bk.BinanceSync(b)
        s.on_event(self.ev(1, 5), 1, 1)
        s.on_snapshot({"lastUpdateId": 3, "bids": [["1", "1"]], "asks": [["2", "1"]]})
        self.assertEqual(s.on_event(self.ev(2, 4), 1, 1), "ignored")       # u < book id
        self.assertEqual(s.on_event(self.ev(9, 12), 1, 1), "gap")          # U > id + 1
        self.assertEqual((b.status, b.gaps, b.bids), (bk.SYNCING, 1, {}))
        self.assertEqual(s.on_event({"U": "x"}, 1, 1), "malformed")


class CoinbaseTests(SimpleTestCase):
    def test_sequence_tracker(self):
        t = bk.SequenceTracker()
        self.assertEqual([t.check(x) for x in (0, 1, 2, 5, 4, 6)], ["first", "ok", "ok", "gap", "old", "ok"])
        self.assertEqual(t.check("7"), "malformed")

    def test_snapshot_updates_and_bad_side(self):
        b = book("coinbase", "BTC-USDT"); s = bk.CoinbaseBookSync(b)
        upd = lambda side, p, q, t="2026-10-25T10:00:00.5Z": {"side": side, "price_level": p, "new_quantity": q, "event_time": t}
        self.assertEqual(s.on_event({"type": "update", "updates": [upd("bid", "1", "1")]}, 1, 1), "out_of_sync")
        self.assertEqual(s.on_event({"type": "snapshot", "updates": [upd("bid", "100", "2"), upd("offer", "101", "3")]}, 1, 1), "synced")
        self.assertEqual(s.on_event({"type": "update", "updates": [upd("bid", "100", "0", "2026-10-25T10:00:01Z")]}, 2, 2), "applied")
        self.assertEqual(b.bids, {})
        self.assertEqual(b.exchange_ts_ns, bk.parse_rfc3339_ns("2026-10-25T10:00:01Z"))
        self.assertEqual(s.on_event({"type": "update", "updates": [upd("sideways", "1", "1")]}, 3, 3), "malformed")
        self.assertEqual(b.status, bk.INVALID)


class SampleRowTests(SimpleTestCase):
    def synced(self, now_mono=10_000_000_000):
        b = book(); b.set_level("bid", "100.50", "0.10000000"); b.set_level("ask", "100.60", "2")
        b.mark_synced(); b.touch(now_mono, now_mono, now_mono); b.feed_alive_mono_ns = now_mono
        return b

    def row(self, b, now_mono, **kw):
        args = dict(depth=5, now_utc_ns=now_mono, now_mono_ns=now_mono, stale_after_s=5, future_tol_s=2,
                    session_id="S", config_id="C", seq=1)
        args.update(kw)
        return bk.sample_row(b, **args)

    def test_usable_row_keeps_exact_strings_units_and_resets_counter(self):
        b = self.synced(); r = self.row(b, 10_500_000_000)
        self.assertTrue(r["usable"])
        self.assertEqual((r["best_bid"], r["best_bid_qty"], r["best_ask"]), ("100.50", "0.10000000", "100.60"))
        self.assertEqual(r["units"]["price"], "USDT per BTC")
        self.assertEqual(r["updates_since_last_sample"], 1)
        self.assertEqual(b.updates_since_sample, 0)

    def test_stale_connection_vs_quiet_book(self):
        b = self.synced(); later = 10_000_000_000 + 9_000_000_000
        b.feed_alive_mono_ns = later - 1_000_000_000              # connection alive, book unchanged for 9 s
        r = self.row(b, later)
        self.assertTrue(r["usable"]); self.assertIn("QUIET_BOOK", r["flags"])
        b.feed_alive_mono_ns = 10_000_000_000                     # connection silent for 9 s
        r = self.row(b, later)
        self.assertFalse(r["usable"]); self.assertIn("STALE", r["flags"]); self.assertIsNone(r["bids"])

    def test_no_forward_fill_for_invalid_or_disconnected_books(self):
        for status in (bk.INVALID, bk.DISCONNECTED, bk.SYNCING, bk.NOT_LISTED):
            b = self.synced(); b.status = status
            r = self.row(b, 10_100_000_000)
            self.assertFalse(r["usable"]); self.assertIsNone(r["best_bid"]); self.assertIsNone(r["asks"])

    def test_crossed_empty_and_clock_anomaly_flags(self):
        b = self.synced(); b.set_level("bid", "101", "1")       # bid above ask
        self.assertIn("CROSSED", self.row(b, 10_100_000_000)["flags"])
        b = self.synced(); b.asks.clear()
        self.assertIn("EMPTY_SIDE", self.row(b, 10_100_000_000)["flags"])
        b = self.synced(); b.exchange_ts_ns = b.receipt_utc_ns + 5_000_000_000   # exchange 5 s "in the future"
        self.assertIn("CLOCK_ANOMALY", self.row(b, 10_100_000_000)["flags"])
        b = self.synced(); b.exchange_ts_ns = None
        self.assertIn("NO_EXCHANGE_TIMESTAMP", self.row(b, 10_100_000_000)["flags"])


class ConfigTests(SimpleTestCase):
    def test_validation(self):
        for kw in ({"sample_interval_s": 0}, {"depth": 0}, {"depth": 500}, {"venues": ("ftx",)}, {"venues": ()},
                   {"stale_after_s": 0}, {"max_duration_s": -1}, {"rotate_mb": 0}):
            with self.assertRaises(ValueError, msg=str(kw)):
                cf.RecorderConfig(**kw)
        with self.assertRaises(ValueError):
            cf.instruments_from_bases(["BTC;rm"])

    def test_comparability_is_explicit(self):
        self.assertEqual(cf.DEFAULT_INSTRUMENTS[0].comparability()["class"], cf.DIRECT)
        mixed = cf.Instrument("BTC-X", (cf.VenueSymbol("binance", "BTCUSDT", "BTC", "USDT"), cf.VenueSymbol("kraken", "BTC/USD", "BTC", "USD")))
        self.assertEqual(mixed.comparability()["class"], cf.CONVERSION_REQUIRED)
        single = cf.Instrument("BTC-Y", (cf.VenueSymbol("binance", "BTCUSDT", "BTC", "USDT"),))
        self.assertEqual(single.comparability()["class"], cf.NOT_COMPARABLE)
        self.assertEqual([vs.symbol for vs in cf.DEFAULT_INSTRUMENTS[0].venues], ["BTCUSDT", "BTC/USDT", "BTC-USDT"])


# ── recorder with mock feeds (no network) ──────────────────────────────────────────────────────────────────────────
class MockFeed(fd.BaseFeed):
    """Synchronises every book from a fake snapshot, keeps it alive, then simulates one disconnect."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)

    async def run(self):
        self.connections += 1
        self.emit("CONNECTED", self.venue, detail="mock")
        for b in self.books.values():
            b.set_level("bid", "100.0", "1.5"); b.set_level("ask", "100.1", "2.0")
            now = time.time_ns(); mono = time.monotonic_ns()
            b.mark_synced("mock snapshot"); b.touch(now, mono, now - 50_000_000)
        t0 = time.monotonic()
        while not self.stop.is_set():
            for b in self.books.values():
                b.feed_alive_mono_ns = time.monotonic_ns()
            if time.monotonic() - t0 > 0.6 and self.venue == "kraken" and self.disconnects == 0:
                self.disconnects += 1
                self.emit("DISCONNECTED", self.venue, detail="mock drop")
                for b in self.books.values():
                    b.reset(bk.DISCONNECTED, "connection closed")
            await asyncio.sleep(0.05)


class FakeHttp:
    async def close(self):
        pass


class RecorderTests(SimpleTestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def cfg(self, **kw):
        base = dict(instruments=cf.DEFAULT_INSTRUMENTS[:2], sample_interval_s=0.2, depth=3, out_dir=self.d,
                    max_duration_s=1.2, stale_after_s=5)
        base.update(kw)
        return cf.RecorderConfig(**base)

    def feeds(self):
        return {v: type(f"Mock{v}", (MockFeed,), {"venue": v}) for v in cf.VENUES}

    def test_session_round_trip_metadata_and_flags(self):
        rec = rc.Recorder(self.cfg(), feeds=self.feeds(), http_factory=FakeHttp)
        meta = asyncio.run(rec.run())
        self.assertEqual(meta["status"], rc.COMPLETED)
        self.assertTrue(meta["research_only"])
        m2, obs, events = rc.read_session(self.d, rec.session_id)
        self.assertEqual(m2["session_id"], rec.session_id)
        self.assertEqual(len(obs), meta["samples_written"])
        self.assertTrue(all(o["schema"] == "xarb-obs-v1" and o["session_id"] == rec.session_id for o in obs))
        kr = [o for o in obs if o["venue"] == "kraken"]
        self.assertTrue(any(o["usable"] for o in kr) and any(o["status"] == bk.DISCONNECTED and not o["usable"] for o in kr))
        self.assertTrue(all(o["best_bid"] is None for o in kr if not o["usable"]))     # disconnected ⇒ no price
        self.assertIn("kraken:DISCONNECTED", m2["events"])
        self.assertEqual({c["class"] for c in m2["comparability"]}, {cf.DIRECT})
        self.assertIn("ZONES.F", m2["governance"])
        self.assertTrue(Path(m2["files_written"][0]).exists())

    def test_session_directories_are_never_reused(self):
        rc.SessionStore(self.d, "SAME", 1).close()
        with self.assertRaises(FileExistsError):
            rc.SessionStore(self.d, "SAME", 1)
        self.assertNotEqual(rc.new_session_id(), rc.new_session_id())

    def test_rotation_and_torn_last_line(self):
        st = rc.SessionStore(self.d, "ROT", 0.0001)        # ~100-byte rotation threshold
        for i in range(5):
            st.write_obs([{"seq": i, "pad": "x" * 200}])
        st.close()
        files = sorted((Path(self.d) / "raw" / "ROT").glob("obs-*.jsonl"))
        self.assertGreater(len(files), 1)
        with open(files[-1], "a", encoding="utf-8") as fh:
            fh.write('{"seq": 99, "torn')                    # crash mid-line
        (Path(self.d) / "metadata" / "ROT.json").write_text(json.dumps({"status": rc.RUNNING, "heartbeat_utc": "2026-01-01T00:00:00+00:00"}))
        meta, obs, _ = rc.read_session(self.d, "ROT")
        self.assertEqual(meta["status"], rc.INCOMPLETE)
        self.assertEqual(sum(1 for o in obs if "seq" in o), 5)
        self.assertTrue(obs[-1].get("_unreadable_line"))

    def test_user_stop_flushes_and_records_status(self):
        rec = rc.Recorder(self.cfg(max_duration_s=None), feeds=self.feeds(), http_factory=FakeHttp)

        async def main():
            t = asyncio.create_task(rec.run())
            await asyncio.sleep(0.7)
            t.cancel()                                      # what Ctrl+C does to asyncio.run's main task
            try:
                await t
            except asyncio.CancelledError:
                pass
        asyncio.run(main())
        meta, obs, _ = rc.read_session(self.d, rec.session_id)
        self.assertEqual(meta["status"], rc.STOPPED_BY_USER)
        self.assertIsNotNone(meta["end_utc"])
        self.assertGreater(len(obs), 0)

    def test_not_listed_books_are_excluded_honestly(self):
        async def listing(http, venue, symbol):
            return (False, "status BREAK") if symbol == "ETH-USDT" else (True, "ok")
        rec = rc.Recorder(self.cfg(max_duration_s=0.5), feeds=self.feeds(), http_factory=FakeHttp, listing_fn=listing)
        meta = asyncio.run(rec.run())
        self.assertEqual(meta["listing"]["coinbase:ETH-USDT"]["listed"], False)


# ── feed handlers, offline ─────────────────────────────────────────────────────────────────────────────────────────
class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_str(self, s):
        self.sent.append(json.loads(s))


class FeedHandlerTests(SimpleTestCase):
    def mk(self, cls, symbols):
        events = []
        bs = {s: bk.LocalBook(cls.venue, "X", s, "BTC", "USDT") for s in symbols}
        feed = cls(cf.RecorderConfig(), bs, None, lambda *a, **k: events.append((a, k)), asyncio.Event())
        return feed, bs, events

    def test_kraken_rejected_subscription_marks_not_listed(self):
        feed, bs, ev = self.mk(fd.KrakenFeed, ["BTC/USDT"])
        msg = json.dumps({"method": "subscribe", "success": False, "error": "Currency pair not supported", "result": {"symbol": "BTC/USDT"}})
        asyncio.run(feed._handle(msg, 1, 1, FakeWS()))
        self.assertEqual(bs["BTC/USDT"].status, bk.NOT_LISTED)

    def test_coinbase_sequence_gap_resubscribes_for_a_fresh_snapshot(self):
        feed, bs, ev = self.mk(fd.CoinbaseFeed, ["BTC-USDT"])
        feed._live = ["BTC-USDT"]
        ws = FakeWS()
        bs["BTC-USDT"].mark_synced()
        for seq in (0, 1, 5):
            asyncio.run(feed._handle(json.dumps({"channel": "heartbeats", "sequence_num": seq}), 1, 1, ws))
        self.assertEqual(bs["BTC-USDT"].status, bk.SYNCING)
        self.assertEqual([m["type"] for m in ws.sent], ["unsubscribe", "subscribe"])
        self.assertTrue(all(m["channel"] == "level2" for m in ws.sent))

    def test_binance_malformed_message_is_reported_not_applied(self):
        feed, bs, ev = self.mk(fd.BinanceFeed, ["BTCUSDT"])
        asyncio.run(feed._handle("not json", 1, 1, FakeWS()))
        self.assertTrue(any(a[0] == "MALFORMED" for a, k in ev))
        self.assertEqual(bs["BTCUSDT"].bids, {})


class SafetyTests(SimpleTestCase):
    """Checks the CODE (parsed with ast: identifiers, attribute calls, imports, string constants), not its comments —
    the warnings in the banner and docstrings legitimately mention orders and withdrawals."""
    FORBIDDEN_NAMES = re.compile(r"(create|cancel|edit|place)_?order|withdraw|transfer|fetch_balance|fetch_my_trades|"
                                 r"apiKey|api_key|api_secret|secret_key|^secret$|listenKey|user_data_stream|^sign$|signature", re.I)
    FORBIDDEN_IMPORTS = ("bot.engines", "bot.core", "bot.risk", "bot.journal", "bot.pairs", "ccxt", "celery", "schedule",
                         "apscheduler", "crontab")

    def trees(self):
        import ast
        cmds = PKG.parents[1] / "management" / "commands"   # claude code changed: 2026-10-25 — Phase B command too
        files = list(PKG.glob("*.py")) + [cmds / "research_record_cross_exchange_books.py", cmds / "research_cross_exchange_gap_study.py",
                                            cmds / "research_cross_exchange_phase_c.py"]   # claude code changed: 2026-10-25 — Phase C command too
        return {f.name: ast.parse(f.read_text(encoding="utf-8")) for f in files}

    def test_no_order_account_or_credential_path_exists(self):
        import ast
        for name, tree in self.trees().items():
            idents = ({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
                      | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
                      | {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))})
            hits = sorted(i for i in idents if self.FORBIDDEN_NAMES.search(i))
            self.assertEqual(hits, [], f"forbidden identifiers in {name}")

    def test_isolated_from_execution_live_loop_and_environment(self):
        import ast
        for name, tree in self.trees().items():
            mods = ({a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
                    | {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)})
            self.assertEqual(sorted(m for m in mods if m.startswith(self.FORBIDDEN_IMPORTS)), [], name)
            attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
            self.assertNotIn("environ", attrs, f"{name} reads environment variables")
            self.assertNotIn("getenv", attrs, f"{name} reads environment variables")
        # and nothing in the live trading path imports the recorder
        for live in ("bot/core/bot_runner.py", "bot/engines/execution_engine.py", "bot/engines/order_manager.py"):
            self.assertNotIn("cross_exchange", Path(live).read_text(encoding="utf-8"), live)

    def test_only_public_market_data_hosts(self):
        import ast
        consts = [n.value for t in self.trees().values() for n in ast.walk(t) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        hosts = set(re.findall(r"(?:wss|https)://([a-z0-9.\-]+)", " ".join(consts)))
        self.assertEqual(hosts, {"stream.binance.com", "api.binance.com", "ws.kraken.com", "api.kraken.com",
                                 "advanced-trade-ws.coinbase.com", "api.coinbase.com"})


# claude code changed: 2026-10-25 — read-only viewer (Venue Comparison "Recorded order books" panel)
class LatestBooksViewerTests(SimpleTestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.sid = "20261025T000000Z-abcdef"
        (self.d / "metadata").mkdir()
        self.raw = self.d / "raw" / self.sid
        self.raw.mkdir(parents=True)
        self.meta = {"session_id": self.sid, "status": rc.RUNNING, "heartbeat_utc": rc._iso(time.time_ns()),
                     "samples_written": 5, "config": {"depth": 2, "sample_interval_s": 1.0},
                     "books": {"binance:BTCUSDT": {}, "kraken:BTC/USDT": {}}}
        self.write_meta()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write_meta(self):
        (self.d / "metadata" / f"{self.sid}.json").write_text(json.dumps(self.meta), encoding="utf-8")

    @staticmethod
    def row(venue, sym, seq, bid="100.10", ask="100.20", usable=True, status="SYNCED", flags=()):
        return {"venue": venue, "venue_symbol": sym, "instrument": "BTC-USDT", "base": "BTC", "quote": "USDT", "seq": seq,
                "sample_utc_ns": time.time_ns(), "status": status, "usable": usable, "flags": list(flags),
                "best_bid": bid if usable else None, "best_ask": ask if usable else None,
                "bids": [[bid, "1.50000000"], ["100.00", "2"]] if usable else None,
                "asks": [[ask, "0.25000000"], ["100.30", "3"]] if usable else None}

    def write_obs(self, name, rows, torn_tail=False):
        text = "".join(json.dumps(r) + "\n" for r in rows) + ('{"venue":"binance","venue_sym' if torn_tail else "")
        (self.raw / name).write_text(text, encoding="utf-8")

    def test_newest_row_per_book_across_rotated_files_and_torn_line_skipped(self):
        self.write_obs("obs-0001.jsonl", [self.row("binance", "BTCUSDT", 1), self.row("kraken", "BTC/USDT", 1, bid="99")])
        self.write_obs("obs-0002.jsonl", [self.row("binance", "BTCUSDT", 2), self.row("binance", "BTCUSDT", 3, bid="101")],
                       torn_tail=True)
        meta, found = rc.latest_books(str(self.d))
        self.assertEqual(meta["status"], rc.RUNNING)
        self.assertEqual(found["binance:BTCUSDT"]["seq"], 3)            # newest row in the newest file
        self.assertEqual(found["binance:BTCUSDT"]["best_bid"], "101")
        self.assertEqual(found["kraken:BTC/USDT"]["seq"], 1)            # only in the previous file
        # a small tail window still finds the newest complete row (the cut first line is discarded)
        _, small = rc.latest_books(str(self.d), tail_bytes=900)
        self.assertEqual(small["binance:BTCUSDT"]["seq"], 3)

    def test_old_heartbeat_is_incomplete_and_no_session_is_none(self):
        self.meta["heartbeat_utc"] = rc._iso(time.time_ns() - 3600 * 10**9)
        self.write_meta()
        self.write_obs("obs-0001.jsonl", [self.row("binance", "BTCUSDT", 1)])
        self.assertEqual(rc.latest_books(str(self.d))[0]["status"], rc.INCOMPLETE)
        empty = Path(tempfile.mkdtemp())
        try:
            self.assertEqual(rc.latest_books(str(empty)), (None, {}))
            from bot.views.recorded_books import recorded_books_payload
            self.assertFalse(recorded_books_payload(empty)["available"])
        finally:
            shutil.rmtree(empty, ignore_errors=True)

    def test_payload_shows_rows_as_recorded_and_no_cross_venue_figure(self):
        from bot.views.recorded_books import recorded_books_payload
        self.write_obs("obs-0001.jsonl", [self.row("kraken", "BTC/USDT", 1, usable=False, status="DISCONNECTED"),
                                          self.row("binance", "BTCUSDT", 1)])
        p = recorded_books_payload(self.d)
        self.assertTrue(p["available"] and p["live"])
        books = p["instruments"][0]["books"]
        self.assertEqual([b["venue"] for b in books], ["binance", "kraken"])         # fixed venue order
        bn, kr = books
        self.assertEqual(bn["best_bid"], "100.1")                                    # exact text, zeros trimmed
        self.assertEqual(bn["bids"][0], ["100.1", "1.5"])
        self.assertAlmostEqual(bn["spread_pct"], (100.2 - 100.1) / 100.15 * 100)    # the venue's OWN spread
        self.assertFalse(kr["usable"])
        self.assertEqual((kr["bids"], kr["asks"], kr["best_bid"]), ([], [], None))   # no forward fill
        flat = json.dumps(p).lower()
        for word in ("gap", "arb", "opportun", "edge", "profit"):
            self.assertNotIn(f'"{word}', flat)                                       # no cross-venue figure keys

    def test_json_endpoint_and_page_panel(self):
        from unittest import mock
        from bot.views import recorded_books as rb
        self.write_obs("obs-0001.jsonl", [self.row("binance", "BTCUSDT", 1), self.row("kraken", "BTC/USDT", 1)])
        with mock.patch.object(rb, "recorder_dir", return_value=self.d):
            r = self.client.get("/execution/venues/books.json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["session_id"], self.sid)
        from bot.views import venue_comparison_data as vcd
        with mock.patch.object(rb, "recorder_dir", return_value=self.d), \
             mock.patch.object(vcd, "compare_venues", return_value=[]), \
             mock.patch.object(vcd, "assess_venue_readiness", side_effect=Exception("offline")):
            html = self.client.get("/execution/venues/").content.decode()
        self.assertIn('id="recorded-books"', html)
        self.assertIn('id="rb-initial"', html)
        self.assertIn('data-inst="BTC-USDT"', html)

    def test_viewer_is_read_only(self):
        import ast
        src = (PKG.parents[1] / "views" / "recorded_books.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        mods = ({a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
                | {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)})
        for bad in ("aiohttp", "ccxt", "requests", "bot.research.cross_exchange.feeds", "bot.engines", "subprocess"):
            self.assertFalse(any(m.startswith(bad) for m in mods), bad)
        calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertFalse(calls & {"write_text", "write", "unlink", "mkdir", "run"})


# claude code changed: 2026-10-25 — PHASE B descriptive gap study: known answers + a synthetic session with known episodes
from bot.research.cross_exchange import gap_study as gs


class GapStudyMathTests(SimpleTestCase):
    def test_walk_known_answers_exact_decimal(self):
        lv = [["100", "1"], ["101", "1"]]
        self.assertEqual(gs.walk(lv, D("1")), D("100"))
        self.assertEqual(gs.walk(lv, D("1.5")), (D("100") + D("50.5")) / D("1.5"))   # 100.333…
        self.assertEqual(gs.walk(lv, D("2")), D("100.5"))
        self.assertIsNone(gs.walk(lv, D("2.0001")))                                # never extrapolated

    def test_pair_measure_known_answers(self):
        buy = {"best_ask": "100", "asks": [["100", "1"], ["101", "1"]]}
        sell = {"best_bid": "100.5", "bids": [["100.5", "1"], ["100", "1"]]}
        m = gs.pair_measure(buy, sell, (150, 1000), 0.001, 0.002)
        self.assertAlmostEqual(m["top_gross_bps"], 50.0)                              # (100.5-100)/100
        self.assertAlmostEqual(m["top_net_bps"], (100.5 * 0.998 - 100.1) / 100.1 * 1e4)   # ≈ 19.88 bps
        r = m["sizes"][150]                                                           # 1.5 coins on both legs
        self.assertAlmostEqual(r["gross_bps"], 0.0, places=9)                        # 100.333… both sides
        avg = 150.5 / 1.5
        self.assertAlmostEqual(r["net_bps"], (avg * 0.998 - avg * 1.001) / (avg * 1.001) * 1e4)
        self.assertIsNone(m["sizes"][1000])                                           # 10 coins: depth exhausted

    def test_row_exclusion(self):
        self.assertEqual(gs.row_exclusion(None, 1000), "missing_row")
        self.assertEqual(gs.row_exclusion({"usable": False, "flags": ["STALE"], "status": "SYNCED"}, 1000), "stale")
        self.assertEqual(gs.row_exclusion({"usable": False, "flags": [], "status": "DISCONNECTED"}, 1000),
                         "unusable:DISCONNECTED")
        self.assertEqual(gs.row_exclusion({"usable": True, "feed_delay_ms": 1500}, 1000), "slow_feed")
        self.assertIsNone(gs.row_exclusion({"usable": True, "feed_delay_ms": -800}, 1000))   # clock offset, not slow
        self.assertIsNone(gs.row_exclusion({"usable": True, "feed_delay_ms": None}, 1000))


class GapStudySessionTests(SimpleTestCase):
    """6 instants, binance vs kraken, BTC. Kraken's bid is above Binance's ask (buy Binance → sell Kraken gap > 0) at
    instants 1, 2, 5, 15; below at 3; Kraken unusable at 4; a 10 s recorder pause sits between 5 and 15."""
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.sid = "20261025T010000Z-aaaaaa"
        (self.d / "metadata").mkdir()
        raw = self.d / "raw" / self.sid
        raw.mkdir(parents=True)
        meta = {"session_id": self.sid, "status": rc.COMPLETED,
                "config": {"sample_interval_s": 1.0, "depth": 2, "venues": ["binance", "kraken"]},
                "books": {"binance:BTCUSDT": {"instrument": "BTC-USDT"}, "kraken:BTC/USDT": {"instrument": "BTC-USDT"}}}
        (self.d / "metadata" / f"{self.sid}.json").write_text(json.dumps(meta), encoding="utf-8")
        mono = [1, 2, 3, 4, 5, 15]
        kr_bid = ["100.2", "100.3", "99.8", None, "100.2", "100.4"]
        lines = []
        for t, kb in zip(mono, kr_bid):
            common = {"sample_mono_ns": t * 10**9, "sample_utc_ns": (1792890000 + t) * 10**9, "instrument": "BTC-USDT",
                      "feed_delay_ms": 80.0, "flags": []}
            lines.append({**common, "venue": "binance", "venue_symbol": "BTCUSDT", "usable": True, "status": "SYNCED",
                          "best_bid": "99.9", "best_ask": "100", "bids": [["99.9", "5"]], "asks": [["100", "5"]]})
            if kb is None:
                lines.append({**common, "venue": "kraken", "venue_symbol": "BTC/USDT", "usable": False,
                              "status": "DISCONNECTED", "best_bid": None, "best_ask": None, "bids": None, "asks": None})
            else:
                ka = str(D(kb) + D("0.1"))
                lines.append({**common, "venue": "kraken", "venue_symbol": "BTC/USDT", "usable": True, "status": "SYNCED",
                              "best_bid": kb, "best_ask": ka, "bids": [[kb, "0.5"]], "asks": [[ka, "0.5"]]})
        text = "".join(json.dumps(r) + "\n" for r in lines) + '{"torn'
        (raw / "obs-0001.jsonl").write_text(text, encoding="utf-8")
        self.cfg = gs.StudyConfig(sizes_quote=(10, 100), fees={"binance": 0.001, "kraken": 0.008},
                                  fee_basis={"binance": "test", "kraken": "test"})

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_known_shares_episodes_depth_and_exclusions(self):
        r = gs.run_study(str(self.d), cfg=self.cfg)
        self.assertEqual(r["input"]["instants"], 6)
        self.assertEqual(r["input"]["pauses_over_max_gap"], 1)
        pairs = {(p["buy_venue"], p["sell_venue"]): p for p in r["instruments"][0]["pairs"]}
        top = pairs[("binance", "kraken")]["by_size"]["top"]
        self.assertEqual(top["eligible_instants"], 5)                                  # instant 4 excluded
        self.assertAlmostEqual(top["gross_bps"]["pct_positive"], 80.0)                 # 4 of 5
        self.assertAlmostEqual(top["gross_bps"]["max"], 40.0)                          # (100.4-100)/100
        ep = top["episodes_gross_positive"]
        self.assertEqual(ep["count"], 3)                                               # [1,2] · [5] · [15]
        self.assertEqual(ep["max_s"], 2.0)
        self.assertEqual(top["net_bps"]["pct_positive"], 0.0)                          # 90 bps of fees > 40 bps gap
        self.assertEqual(top["episodes_net_positive"]["count"], 0)
        # 100 USDT = 1 coin: Kraken's 0.5-coin bid cannot absorb it → insufficient depth, never extrapolated
        self.assertEqual(pairs[("binance", "kraken")]["by_size"]["100"]["insufficient_depth"], 5)
        self.assertIsNone(pairs[("binance", "kraken")]["by_size"]["100"]["gross_bps"])
        self.assertEqual(r["data_quality"]["kraken:BTC/USDT"]["excluded"], {"unusable:DISCONNECTED": 1})
        self.assertEqual(r["config"]["fees"], {"binance": 0.001, "kraken": 0.008})
        self.assertIn("DESCRIPTIVE", r["classification"])
        r2 = gs.run_study(str(self.d), cfg=self.cfg)                                 # reproducible
        self.assertEqual((r["run_id"], r["input"]["sha256"]), (r2["run_id"], r2["input"]["sha256"]))

    def test_saved_reports_are_never_overwritten_and_missing_fee_fails_closed(self):
        r = gs.run_study(str(self.d), cfg=self.cfg)
        path = gs.save_report(r, str(self.d))
        with self.assertRaises(FileExistsError):
            gs.save_report(r, str(self.d))
        self.assertEqual(gs.load_latest_report(str(self.d))["run_id"], r["run_id"])
        with self.assertRaises(ValueError):
            gs.run_study(str(self.d), cfg=gs.StudyConfig(fees={"binance": 0.001}, fee_basis={}))
        self.assertTrue(path.name.startswith(f"gap_study_{self.sid}_"))


class GapStudyPageTests(GapStudySessionTests):   # claude code changed: 2026-10-25 — Phase B page (reuses the synthetic session)
    def test_page_without_report_explains_how_to_run(self):
        from unittest import mock
        from bot.views import cross_exchange_gaps as xg
        with mock.patch.object(xg, "_out_dir", return_value=self.d):
            html = self.client.get("/research/cross-exchange-gaps/").content.decode()
        self.assertIn("No gap study has been run yet", html)
        self.assertIn("research_cross_exchange_gap_study", html)

    def test_page_renders_saved_report_with_classification_and_limits(self):
        from unittest import mock
        from bot.views import cross_exchange_gaps as xg
        gs.save_report(gs.run_study(str(self.d), cfg=self.cfg), str(self.d))
        with mock.patch.object(xg, "_out_dir", return_value=self.d):
            r = self.client.get("/research/cross-exchange-gaps/")
        html = r.content.decode()
        self.assertEqual(r.status_code, 200)
        self.assertIn("not profit, not a trading signal", html)
        self.assertIn("BTC-USDT", html)
        self.assertIn('src="/static/img/exchanges/kraken.svg"', html)
        self.assertIn("0 / 2", html)                       # no direction ever net-positive in the synthetic session
        self.assertIn("Pre-funded inventory", html)        # limitations are shown, not hidden
        self.assertIn("Governance:", html)

    def test_venue_comparison_shows_the_gap_research_after_the_books(self):   # claude code changed: 2026-10-25
        from unittest import mock
        from bot.views import cross_exchange_gaps as xg, recorded_books as rb, venue_comparison_data as vcd
        gs.save_report(gs.run_study(str(self.d), cfg=self.cfg), str(self.d))
        with mock.patch.object(xg, "_out_dir", return_value=self.d), \
             mock.patch.object(rb, "recorder_dir", return_value=self.d), \
             mock.patch.object(vcd, "compare_venues", return_value=[]), \
             mock.patch.object(vcd, "assess_venue_readiness", side_effect=Exception("offline")):
            html = self.client.get("/execution/venues/").content.decode()
        self.assertIn("Cross-Exchange Gaps — research (Phase B, descriptive)", html)
        self.assertLess(html.index('id="recorded-books"'), html.index("Cross-Exchange Gaps — research"))   # after the books
        self.assertIn("0 / 2", html)
        self.assertIn("not profit, not a trading signal", html)

    def test_view_is_read_only(self):
        import ast
        src = (PKG.parents[1] / "views" / "cross_exchange_gaps.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        mods = ({a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
                | {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)})
        for bad in ("aiohttp", "ccxt", "requests", "bot.research.cross_exchange.feeds", "bot.engines", "subprocess"):
            self.assertFalse(any(m.startswith(bad) for m in mods), bad)
        self.assertNotIn("run_study", src)                 # the page never computes; it only loads a saved report


# claude code changed: 2026-10-25 — PHASE C (preregistered XARB-C1): known answers, guards, one-shot evaluation, calibration
from datetime import datetime as _dt, timezone as _tz
import numpy as _np
from dataclasses import replace as _replace
from bot.research.cross_exchange import phase_c as pc

_FEES0 = {"binance": 0.0, "kraken": 0.0, "coinbase": 0.0}


class PhaseCDecisionTests(SimpleTestCase):
    reg = pc.Registration(fees=_FEES0, fee_basis={v: "test" for v in _FEES0})

    def test_known_answers(self):
        n = self.reg.n_hours
        self.assertEqual(pc.decide(_np.zeros(n), self.reg)["verdict"], "FAIL")
        self.assertEqual(pc.decide(_np.full(n, 3 * self.reg.theta), self.reg)["verdict"], "PASS")
        self.assertEqual(pc.decide(_np.full(n, self.reg.theta), self.reg)["verdict"], "FAIL")     # must EXCEED θ
        ramp = _np.linspace(2 * self.reg.theta, 6 * self.reg.theta, n)                           # lag-1 ≈ 1
        r = pc.decide(ramp, self.reg)
        self.assertGreater(r["lag1_autocorrelation"], 0.9)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")                                           # beyond calibration
        self.assertEqual(r["batches"], 7)

    def test_batch_means_t_known_answer(self):
        y = _np.repeat([1.0, 2.0, 3.0], 2)                         # batches of 2 → means 1, 2, 3
        r = pc.batch_means_test(y, 2, theta=0.0, alpha=0.05)
        from scipy import stats
        se = _np.std([1, 2, 3], ddof=1) / _np.sqrt(3)
        self.assertAlmostEqual(r["lower_bound"], 2.0 - stats.t.ppf(0.95, 2) * se)
        self.assertAlmostEqual(r["t_stat"], 2.0 / se)
        with self.assertRaises(ValueError):
            pc.batch_means_test(_np.ones(5), 2, 0.0, 0.05)        # fewer than 3 batches

    def test_false_pass_rate_at_the_null_boundary_is_controlled(self):
        self.assertLessEqual(pc.false_pass_rate(self.reg, n_sims=600, rho=0.5), 0.05)


class PhaseCRegistrationTests(SimpleTestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "metadata").mkdir()
        self.reg = pc.Registration(fees=_FEES0, fee_basis={v: "test" for v in _FEES0}, n_hours=3, batch_hours=1,
                                   min_eligible_share=0.0005, recorder={"recorder_version": "SQT-XARB-REC-1",
                                   "schema_version": "xarb-obs-v1", "sample_interval_s": 1.0, "depth": 10,
                                   "venues": ["binance", "kraken"]})

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def session(self, sid, start_iso, hours, kraken_bids):
        """One instant per (hour, kraken_bid); Binance ask fixed at 100. Instants are 1 s apart within an hour."""
        meta = {"session_id": sid, "status": rc.COMPLETED, "start_utc": start_iso, "recorder_version": "SQT-XARB-REC-1",
                "schema_version": "xarb-obs-v1", "config": {"sample_interval_s": 1.0, "depth": 10, "venues": ["binance", "kraken"]},
                "books": {"binance:BTCUSDT": {"instrument": "BTC-USDT"}, "kraken:BTC/USDT": {"instrument": "BTC-USDT"}}}
        (self.d / "metadata" / f"{sid}.json").write_text(json.dumps(meta), encoding="utf-8")
        raw = self.d / "raw" / sid
        raw.mkdir(parents=True)
        lines, mono = [], 0
        for h in hours:
            for kb in kraken_bids:
                mono += 1
                utc = (h * 3600 + mono) * 10**9
                c = {"sample_mono_ns": (h * 3600 + mono) * 10**9, "sample_utc_ns": utc, "instrument": "BTC-USDT",
                     "feed_delay_ms": 50.0, "flags": [], "usable": True, "status": "SYNCED"}
                lines.append({**c, "venue": "binance", "venue_symbol": "BTCUSDT", "best_bid": "99.9", "best_ask": "100",
                              "bids": [["99.9", "100"]], "asks": [["100", "100"]]})
                lines.append({**c, "venue": "kraken", "venue_symbol": "BTC/USDT", "best_bid": kb,
                              "best_ask": str(D(kb) + D("0.1")), "bids": [[kb, "100"]], "asks": [[str(D(kb) + D("0.1")), "100"]]})
        (raw / "obs-0001.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")

    def test_hourly_outcome_counts_only_runs_of_l_min(self):
        # bids: + + + − + −  → a run of 3 qualifies (≥ 2); the lone + does not → 3 qualifying of 6 eligible
        self.session("20261026T000000Z-bbbbbb", "2026-10-26T00:00:00+00:00", [493000],
                     ["100.2", "100.2", "100.2", "99.5", "100.2", "99.5"])
        hours, sha = pc.hourly_outcomes(str(self.d), "20261026T000000Z-bbbbbb", self.reg)
        self.assertEqual(len(hours), 1)
        self.assertEqual((hours[0]["eligible"], hours[0]["qualifying"]), (6, 3))
        self.assertAlmostEqual(hours[0]["y"], 0.5)
        self.assertEqual(len(sha), 64)

    def test_full_lifecycle_guards_and_single_evaluation(self):
        pc.write_draft(str(self.d), self.reg)
        with self.assertRaises(FileExistsError):
            pc.write_draft(str(self.d), self.reg)                          # a draft is never replaced
        with self.assertRaises(PermissionError):
            pc.evaluate(str(self.d))                                        # unsigned → refused
        pc.owner_sign(str(self.d), "owner (test)", now=_dt(2026, 10, 26, tzinfo=_tz.utc))
        with self.assertRaises(ValueError):
            pc.owner_sign(str(self.d), "again")                                   # only a draft can be signed
        # pre-registration session (Phase B-style) is refused; post-registration session is used
        self.session("20261025T000000Z-old000", "2026-10-25T00:00:00+00:00", [492990, 492991, 492992], ["100.2", "100.2"])
        self.session("20261027T000000Z-new000", "2026-10-27T00:00:00+00:00", [493030], ["100.2", "100.2"])
        r = pc.evaluate(str(self.d))
        self.assertEqual(r["status"], "INSUFFICIENT_DATA")                  # 1 of 3 hours: nothing written
        self.assertFalse((pc.prereg_dir(str(self.d)) / "XARB-C1_RESULT.json").exists())
        self.assertEqual([s["reason"] for s in r["sessions_refused"]], ["started before registration"])
        shutil.rmtree(self.d / "raw" / "20261027T000000Z-new000")
        (self.d / "metadata" / "20261027T000000Z-new000.json").unlink()
        self.session("20261027T000000Z-new000", "2026-10-27T00:00:00+00:00", [493030, 493031, 493032], ["100.2", "100.2"])
        r = pc.evaluate(str(self.d))
        self.assertEqual((r["status"], r["verdict"], r["n_hours"]), ("EVALUATED", "PASS", 3))
        with self.assertRaises(FileExistsError):
            pc.evaluate(str(self.d))                                        # one shot only

    def test_edited_registration_is_refused(self):
        path = pc.write_draft(str(self.d), self.reg)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["theta"] = 0.0                                                  # someone loosens the threshold
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(ValueError):
            pc.owner_sign(str(self.d), "owner")
