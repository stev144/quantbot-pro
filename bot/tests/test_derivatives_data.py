# claude code changed: new file — coverage for bot/engines/derivatives_data.py
# (funding rate + open interest data, added to move this project beyond
# OHLCV-only signals). Real network calls throughout — no mocking, per
# this project's established convention — since a mocked ccxt exchange
# can't catch the two real API quirks this module exists to handle:
# Binance's ~30-day hard cap on open interest history, and the MATIC/POL,
# SHIB/1000SHIB perpetual-symbol divergences from this project's spot
# symbol list (both confirmed directly against the live API, not assumed,
# while building this module).

import time

from django.test import SimpleTestCase

from bot.engines.derivatives_data import (
    MAX_OPEN_INTEREST_HISTORY_DAYS,
    fetch_funding_rate_history,
    fetch_open_interest_history,
    get_funding_rate_snapshot,
    get_open_interest_snapshot,
    to_perp_symbol,
)


class ToPerpSymbolTest(SimpleTestCase):

    def test_plain_symbol_maps_directly(self):
        self.assertEqual(to_perp_symbol("BTC/USDT"), "BTC/USDT:USDT")

    def test_matic_maps_to_pol_override(self):
        # claude code changed: real, confirmed divergence — Binance lists
        # MATIC's perpetual under POL (Polygon's rebrand), same real-world
        # asset as the already-documented Kraken-side MATIC/POL divergence
        # in kraken_adapter.py's normalize_symbol().
        self.assertEqual(to_perp_symbol("MATIC/USDT"), "POL/USDT:USDT")

    def test_shib_maps_to_1000shib_override(self):
        # claude code changed: real, confirmed divergence — Binance sizes
        # the SHIB perpetual contract in units of 1000 SHIB.
        self.assertEqual(to_perp_symbol("SHIB/USDT"), "1000SHIB/USDT:USDT")

    def test_unlisted_symbol_returns_none_rather_than_guessing(self):
        self.assertIsNone(to_perp_symbol("NOT_A_REAL_SYMBOL/USDT"))


class LiveSnapshotTest(SimpleTestCase):

    def test_funding_rate_snapshot_has_real_shape(self):
        snapshot = get_funding_rate_snapshot("BTC/USDT")
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["symbol"], "BTC/USDT")
        self.assertEqual(snapshot["perp_symbol"], "BTC/USDT:USDT")
        self.assertIsInstance(snapshot["funding_rate"], float)
        # A real BTC perpetual funding rate is always a small fraction —
        # sanity bound generous enough to never flake on a real value,
        # tight enough to catch a badly wrong field mapping.
        self.assertLess(abs(snapshot["funding_rate"]), 0.05)

    def test_open_interest_snapshot_has_real_shape(self):
        snapshot = get_open_interest_snapshot("BTC/USDT")
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["symbol"], "BTC/USDT")
        self.assertGreater(snapshot["open_interest_amount"], 0)

    def test_snapshot_returns_none_for_symbol_with_no_perpetual(self):
        self.assertIsNone(get_funding_rate_snapshot("NOT_A_REAL_SYMBOL/USDT"))
        self.assertIsNone(get_open_interest_snapshot("NOT_A_REAL_SYMBOL/USDT"))


class FundingRateHistoryTest(SimpleTestCase):

    def test_returns_real_rows_within_requested_window(self):
        since_ms = int((time.time() - 10 * 86400) * 1000)
        df = fetch_funding_rate_history("BTC/USDT", since_ms=since_ms)

        self.assertFalse(df.empty)
        for col in ("timestamp", "funding_rate", "mark_price"):
            self.assertIn(col, df.columns)
        # Funding prints every 8h — 10 days should yield roughly 30 rows,
        # generous bounds to avoid flaking on the exact print schedule.
        self.assertGreater(len(df), 15)
        self.assertLess(len(df), 45)
        self.assertGreaterEqual(df["timestamp"].min(), since_ms)

    def test_rows_are_sorted_and_deduplicated(self):
        since_ms = int((time.time() - 10 * 86400) * 1000)
        df = fetch_funding_rate_history("BTC/USDT", since_ms=since_ms)

        self.assertTrue(df["timestamp"].is_monotonic_increasing)
        self.assertEqual(df["timestamp"].nunique(), len(df))

    def test_empty_for_symbol_with_no_perpetual(self):
        since_ms = int((time.time() - 10 * 86400) * 1000)
        df = fetch_funding_rate_history("NOT_A_REAL_SYMBOL/USDT", since_ms=since_ms)
        self.assertTrue(df.empty)
        self.assertEqual(list(df.columns), ["timestamp", "funding_rate", "mark_price"])


class OpenInterestHistoryTest(SimpleTestCase):

    def test_returns_real_rows(self):
        df = fetch_open_interest_history("BTC/USDT", days=5, timeframe="1h")
        self.assertFalse(df.empty)
        for col in ("timestamp", "open_interest_amount", "open_interest_value"):
            self.assertIn(col, df.columns)
        # ~5 days @ 1h candles — generous bounds around 120.
        self.assertGreater(len(df), 80)

    def test_clamps_to_real_binance_limit_instead_of_raising(self):
        # claude code changed: direct regression test for the exact
        # failure hit during development — requesting more than Binance's
        # real ~30-day cap used to raise ccxt.BadRequest (-1130,
        # "startTime is invalid"). Confirmed this by hand against the live
        # API before writing fetch_open_interest_history()'s clamp.
        df = fetch_open_interest_history("BTC/USDT", days=90, timeframe="1h")
        self.assertFalse(df.empty)

        span_days = (df["timestamp"].max() - df["timestamp"].min()) / 86400000
        self.assertLessEqual(span_days, MAX_OPEN_INTEREST_HISTORY_DAYS + 1)  # +1 slack for boundary rounding

    def test_empty_for_symbol_with_no_perpetual(self):
        df = fetch_open_interest_history("NOT_A_REAL_SYMBOL/USDT", days=5)
        self.assertTrue(df.empty)
        self.assertEqual(list(df.columns), ["timestamp", "open_interest_amount", "open_interest_value"])
