# claude code changed: new file — dashboard hardening. Covers
# bot.data_fetcher.get_last_known_klines(), the new read-only "last known
# good" cache peek added so the dashboard can show a labeled stale
# snapshot instead of a bare error when the live exchange fetch fails.
# Pure disk I/O against real bot/cache/*.pkl files (no network, no
# mocking of the exchange itself) — matches this project's own "no
# mocking, real endpoints" convention, since the thing under test here is
# a filesystem cache, not an exchange response.

import os
import pickle
import time

import pandas as pd
from django.test import SimpleTestCase

from bot.data_fetcher import CACHE_DIR, _cache_path, get_last_known_klines


def _make_df(n=5):
    return pd.DataFrame({
        "open": range(n), "high": range(n), "low": range(n),
        "close": range(n), "volume": range(n),
    })


class GetLastKnownKlinesTest(SimpleTestCase):

    def setUp(self):
        self.symbol = "TESTFALLBACK/USDT"
        self.interval = "1h"
        self.total_candles = 5
        self.cache_key = f"klines_TESTFALLBACKUSDT_{self.interval}_{self.total_candles}"
        self.path = _cache_path(self.cache_key)
        self.addCleanup(self._remove_cache_file)

    def _remove_cache_file(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def _write_payload(self, payload):
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(self.path, "wb") as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)

    def test_no_cache_file_returns_none_none(self):
        df, saved_at = get_last_known_klines(self.symbol, self.interval, self.total_candles)
        self.assertIsNone(df)
        self.assertIsNone(saved_at)

    def test_expired_entry_is_still_returned_unlike_get_klines_own_cache(self):
        # claude code changed: the whole point of this function — an entry
        # whose expires_at is long past (which _load_cache would delete
        # and refuse to return) must still come back here, since this is
        # an explicit last-resort fallback, never the default read path.
        saved_at = time.time() - 999_999
        self._write_payload({"expires_at": time.time() - 900, "saved_at": saved_at, "df": _make_df()})
        df, returned_saved_at = get_last_known_klines(self.symbol, self.interval, self.total_candles)
        self.assertIsNotNone(df)
        self.assertEqual(len(df), 5)
        self.assertAlmostEqual(returned_saved_at, saved_at, delta=1)
        # claude code changed: never deletes the file it just read — a
        # second real live failure must be able to fall back again.
        self.assertTrue(os.path.exists(self.path))

    def test_old_cache_format_without_dict_wrapper_still_works(self):
        self._write_payload(_make_df())  # pre-saved_at cache format: a bare DataFrame
        df, saved_at = get_last_known_klines(self.symbol, self.interval, self.total_candles)
        self.assertIsNotNone(df)
        self.assertIsNone(saved_at)  # honestly unknown for this old format, never guessed

    def test_corrupted_file_fails_closed_not_crashes(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(self.path, "wb") as f:
            f.write(b"not a real pickle")
        df, saved_at = get_last_known_klines(self.symbol, self.interval, self.total_candles)
        self.assertIsNone(df)
        self.assertIsNone(saved_at)

    def test_empty_dataframe_is_treated_as_no_data(self):
        self._write_payload({"expires_at": time.time() - 900, "saved_at": time.time(), "df": pd.DataFrame()})
        df, saved_at = get_last_known_klines(self.symbol, self.interval, self.total_candles)
        self.assertIsNone(df)
        self.assertIsNone(saved_at)
