# claude code changed: new file — Phase 2B, Steps 4-5. Coverage for
# bot/engines/trade_data.py, the first genuinely new microstructure data
# source this platform ingests (aggregated trade-level data).
#
# Real network calls for the actual fetch behavior — no mocking, per this
# project's established convention (see test_derivatives_data.py's own
# header) — a mocked ccxt response can't catch a real pagination-boundary
# bug the way the module's own construction already caught one (see
# fetch_agg_trades's docstring: verified live against the real API before
# writing the pagination logic). Windows are kept small (minutes, not
# hours) so these stay fast.
#
# Storage-layer tests (dedup, incremental update, schema versioning) use
# small, deterministic, hand-built trade fixtures instead — those are pure
# logic tests that don't need the real market, per Phase 2B Step 16's
# "prefer small, deterministic fixtures" instruction.

import shutil
import tempfile
import time
from pathlib import Path

import pandas as pd
from django.test import SimpleTestCase

import bot.engines.trade_data as trade_data
from bot.engines.trade_data import (
    TRADE_COLUMNS, fetch_agg_trades, load_stored_trades,
    trade_csv_path, update_stored_trades,
)


class FetchAggTradesLiveTest(SimpleTestCase):
    """Real network calls against Binance's public aggTrades endpoint."""

    def test_recent_window_has_real_schema_and_is_causally_ordered(self):
        now_ms = int(time.time() * 1000)
        since_ms = now_ms - 2 * 60 * 1000   # last 2 minutes — small, fast
        df = fetch_agg_trades("BTC/USDT", since_ms=since_ms, until_ms=now_ms)

        self.assertGreater(len(df), 0)
        self.assertEqual(list(df.columns), TRADE_COLUMNS)
        self.assertTrue(df["trade_id"].is_unique)
        self.assertTrue(df["timestamp"].is_monotonic_increasing)
        self.assertTrue((df["timestamp"] >= since_ms).all())
        self.assertTrue((df["timestamp"] <= now_ms).all())
        self.assertTrue((df["price"] > 0).all())
        self.assertTrue((df["quantity"] > 0).all())
        self.assertEqual(set(df["symbol"].unique()), {"BTC/USDT"})
        self.assertEqual(set(df["source"].unique()), {"binance"})
        self.assertEqual(df["is_buyer_maker"].dtype, bool)

    def test_no_trades_in_an_impossible_future_window_returns_empty_not_none(self):
        # claude code changed: a since_ms in the far future should yield
        # zero trades, not a crash — matches derivatives_data.py's
        # "skip, don't crash" convention.
        far_future_ms = int(time.time() * 1000) + 365 * 86400 * 1000
        df = fetch_agg_trades("BTC/USDT", since_ms=far_future_ms, until_ms=far_future_ms + 60_000)
        self.assertEqual(list(df.columns), TRADE_COLUMNS)
        self.assertEqual(len(df), 0)

    def test_pagination_handles_a_dense_window_beyond_one_page(self):
        # claude code changed: a slightly longer window on a liquid symbol
        # (BTC/USDT) reliably exceeds AGG_TRADES_PAGE_LIMIT=1000 in a few
        # minutes — exercises the fromId-based second-page path, not just
        # the single-page path the first test above may hit.
        now_ms = int(time.time() * 1000)
        since_ms = now_ms - 5 * 60 * 1000
        df = fetch_agg_trades("BTC/USDT", since_ms=since_ms, until_ms=now_ms)
        self.assertTrue(df["trade_id"].is_unique)
        self.assertTrue(df["timestamp"].is_monotonic_increasing)


class TradeCsvPathTest(SimpleTestCase):

    def test_path_uses_underscore_form_distinct_from_ohlcv_and_research_data(self):
        path = trade_csv_path("BTC/USDT")
        self.assertEqual(path.name, "BTC_USDT_trades.csv")
        self.assertIn("trades", str(path.parent))  # claude code changed: Step 5 — must be distinguishable from data/*.csv and research_data/*.csv


class StorageLayerTest(SimpleTestCase):
    """Deterministic, hand-built fixtures — no network required."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self._orig_trades_dir = trade_data.TRADES_DIR
        trade_data.TRADES_DIR = Path(self.tmp_dir)

    def tearDown(self):
        trade_data.TRADES_DIR = self._orig_trades_dir
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_load_stored_trades_returns_empty_shape_when_no_file_exists(self):
        df = load_stored_trades("BTC/USDT")
        self.assertEqual(list(df.columns), TRADE_COLUMNS)
        self.assertEqual(len(df), 0)

    def test_load_stored_trades_raises_on_schema_mismatch(self):
        # claude code changed: Step 5 schema-versioning guard — a file with
        # the wrong columns must fail loud, never be silently misread.
        trade_data.TRADES_DIR.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"unexpected": [1, 2]}).to_csv(trade_csv_path("ETH/USDT"), index=False)
        with self.assertRaises(ValueError):
            load_stored_trades("ETH/USDT")

    def test_update_without_existing_store_requires_explicit_backfill_since(self):
        with self.assertRaises(ValueError):
            update_stored_trades("BTC/USDT")   # claude code changed: no backfill_since_ms, no existing store — must not guess

    def test_incremental_update_deduplicates_by_trade_id_not_timestamp(self):
        # claude code changed: proves dedup is by trade_id (the real
        # primary key — see trade_data.py's module docstring on why two
        # trades can legitimately share a millisecond timestamp), not by
        # timestamp, which would wrongly collapse two distinct real trades.
        existing = pd.DataFrame({
            "trade_id": [1, 2], "timestamp": [1000, 1000],   # claude code changed: same timestamp, different trade_id — both real, must both survive
            "price": [100.0, 100.1], "quantity": [1.0, 2.0],
            "is_buyer_maker": [False, True], "symbol": ["BTC/USDT"] * 2, "source": ["binance"] * 2,
        })
        trade_data.TRADES_DIR.mkdir(parents=True, exist_ok=True)
        existing.to_csv(trade_csv_path("BTC/USDT"), index=False)

        loaded = load_stored_trades("BTC/USDT")
        self.assertEqual(len(loaded), 2)
        self.assertTrue(loaded["trade_id"].is_unique)
        self.assertEqual(sorted(loaded["timestamp"].tolist()), [1000, 1000])   # claude code changed: both rows preserved despite equal timestamps

    def test_update_stored_trades_writes_nothing_when_backfill_finds_no_trades(self):
        # claude code changed: an empty backfill result on a symbol with no
        # prior store must not create a spurious empty file.
        far_future_ms = int(time.time() * 1000) + 365 * 86400 * 1000
        result = update_stored_trades("BTC/USDT", until_ms=far_future_ms + 60_000, backfill_since_ms=far_future_ms)
        self.assertTrue(result.empty)
        self.assertFalse(trade_csv_path("BTC/USDT").exists())
