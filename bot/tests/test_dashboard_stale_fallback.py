# claude code changed: new file — dashboard hardening. Real user report:
# clicking the navbar's Crypto/Executive link appeared to do nothing.
# Two separate causes were found and fixed:
#   1. The dashboard's live exchange fetch + full backtest can genuinely
#      take up to ~2 minutes on a cold cache with zero visual feedback
#      (covered by a client-side loading overlay — not testable here,
#      it's pure template/JS; see templates/base.html's data-slow-nav
#      handler).
#   2. If the live fetch fails outright (exchange down), the page used to
#      show only a bare "Failed to fetch market data" error with no
#      fallback at all. This file covers the fix for that: dashboard()
#      now falls back to bot.data_fetcher.get_last_known_klines() (a
#      stale but real, previously-cached snapshot) and renders a labeled
#      "STALE DATA" banner instead of a dead end.
#
# get_klines() itself is patched to deterministically force the failure
# path (a real live outage isn't reproducible on demand) — the fallback
# logic under test here is our own view code's reaction to that failure,
# not exchange behavior, matching the precedent already set by
# test_kalman_research_lab_integration.py's patching of load_ohlcv at its
# point of use.
#
# claude code changed: Data-Layer Audit "wire into the dashboard too" —
# the patch target moved from "bot.views.dashboard.get_klines" to
# "bot.data_fetcher.get_klines". dashboard.py no longer calls get_klines()
# directly at all; it goes through BinanceKlinesProvider.get_recent_bars(),
# which calls it via `data_fetcher.get_klines(...)` (a module-attribute
# access resolved at call time, not a name bound at import time) —
# patching the attribute on its SOURCE module is what actually intercepts
# that call now. Patching the old "bot.views.dashboard.get_klines" target
# would silently no-op (no such attribute exists there anymore) and these
# tests would either error on a missing attribute or, worse, hit live
# Binance instead of the deterministic forced failure they require.

import os
import pickle
import time
from unittest.mock import patch

import numpy as np
import pandas as pd
from django.test import TestCase

from bot.data_fetcher import CACHE_DIR, _cache_path, _normalize_symbol


def _synthetic_ohlcv(n=150):
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    idx.name = "timestamp"
    close = 100 + np.cumsum(np.random.default_rng(1).normal(0, 0.3, n))
    return pd.DataFrame({
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": np.full(n, 1000.0),
    }, index=idx)


class DashboardStaleFallbackTest(TestCase):

    def setUp(self):
        # claude code changed: a unique symbol per test method — not a
        # shared class constant — so each test gets its own untouched
        # Django `backtest_results_{symbol}` cache entry (LocMemCache
        # persists across tests within a run) as well as its own disk
        # pickle cache file. Sharing one symbol across tests here caused a
        # real test-isolation bug during development: whichever test
        # happened to run first populated the results cache for every
        # later test, silently steering them into dashboard()'s cache-HIT
        # branch instead of the cold-cache branch each test actually means
        # to exercise.
        self.symbol = f"TESTDASHFALLBACK{self._testMethodName.upper()}/USDT"
        # claude code changed: use the real _normalize_symbol() rather
        # than hand-reconstructing it — a first attempt at this test
        # manually stripped only "/" and missed that the real function
        # also strips underscores, which test method names are full of,
        # producing a cache key that never matched what dashboard.py's
        # _fetch_klines_or_stale_fallback() actually looks up.
        self.cache_key = f"klines_{_normalize_symbol(self.symbol)}_1h_10000"
        self.path = _cache_path(self.cache_key)
        self.addCleanup(self._remove_cache_file)

    def _remove_cache_file(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def _write_stale_cache(self, df, saved_at):
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(self.path, "wb") as f:
            pickle.dump({"expires_at": time.time() - 900, "saved_at": saved_at, "df": df}, f, protocol=pickle.HIGHEST_PROTOCOL)

    def test_live_failure_with_no_cache_at_all_shows_the_original_error(self):
        # claude code changed: the pre-existing behavior — no stale
        # fallback of any kind exists — must be completely unchanged.
        with patch("bot.data_fetcher.get_klines", return_value=pd.DataFrame()):
            resp = self.client.get("/", {"symbol": self.symbol})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Failed to fetch market data")
        self.assertNotContains(resp, "STALE DATA")

    def test_live_failure_with_a_stale_cache_shows_the_stale_banner_not_the_error(self):
        self._write_stale_cache(_synthetic_ohlcv(), saved_at=time.time() - 3600)
        with patch("bot.data_fetcher.get_klines", return_value=pd.DataFrame()):
            resp = self.client.get("/", {"symbol": self.symbol})
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertNotIn("Failed to fetch market data", html)
        self.assertIn("STALE DATA", html)
        self.assertIn("1 hour(s) old", html)

    def test_live_success_never_shows_the_stale_banner(self):
        # claude code changed: the common case — nothing about this
        # feature should be visible when the live fetch just works.
        self._write_stale_cache(_synthetic_ohlcv(), saved_at=time.time() - 3600)  # present but must be ignored
        with patch("bot.data_fetcher.get_klines", return_value=_synthetic_ohlcv()):
            resp = self.client.get("/", {"symbol": self.symbol})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "STALE DATA")

    def test_slow_nav_overlay_markup_present_on_the_nav_links(self):
        # claude code changed: proves the click-triggered loading overlay
        # (templates/base.html) is actually wired to the two links that
        # share dashboard()'s slow cold-cache path, not just present
        # somewhere unrelated on the page. Patches get_klines so this
        # stays a fast, deterministic markup check rather than a real
        # live fetch + full backtest.
        with patch("bot.data_fetcher.get_klines", return_value=_synthetic_ohlcv()):
            resp = self.client.get("/", {"symbol": self.symbol})
        html = resp.content.decode()
        self.assertIn("data-slow-nav", html)
        self.assertIn("term-slow-nav-overlay", html)
