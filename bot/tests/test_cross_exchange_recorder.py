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
        files = list(PKG.glob("*.py")) + [PKG.parents[1] / "management" / "commands" / "research_record_cross_exchange_books.py"]
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
