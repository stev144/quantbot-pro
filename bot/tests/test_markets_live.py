# bot/tests/test_markets_live.py
"""Markets live display prices (2026-10-21).   # claude code changed: new

Real endpoints, no mocks (project convention). Pins: the fast tail read equals a full read; the live endpoint
returns real Binance prices with timestamps; fetching live prices never writes anything into data/ (the D-8 crypto
holdout and the FX holdouts stay sealed); every Markets card carries its as-of time.
"""
import os
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, Client

from bot.views import markets_data as md


def _snapshot(directory: Path) -> dict:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in directory.glob("*.csv")}


class TailReadTests(SimpleTestCase):
    def test_tail_read_equals_full_read(self):
        for path in (Path(settings.BASE_DIR) / "data" / "BTC_USDT_1h.csv",
                     Path(settings.BASE_DIR) / "data" / "forex" / "EUR_USD_1h.csv"):
            if not path.exists():
                self.skipTest(f"{path.name} not on this machine")
            full = pd.read_csv(path, usecols=["timestamp", "close"])
            full["timestamp"] = pd.to_datetime(full["timestamp"], utc=True)
            full = full.sort_values("timestamp").tail(120).reset_index(drop=True)
            tail = md._read_csv_tail(path, 120).reset_index(drop=True)
            pd.testing.assert_frame_equal(full, tail)


class LiveQuotesTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_live_endpoint_returns_real_crypto_prices_and_never_writes_data(self):
        data_dir = Path(settings.BASE_DIR) / "data"
        before = (_snapshot(data_dir), _snapshot(data_dir / "forex"))
        r = Client(HTTP_HOST="127.0.0.1").get("/markets/live/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "no-store")
        j = r.json()
        self.assertIn("crypto", j["status"])
        self.assertTrue(j["status"]["crypto"].startswith("LIVE"), j["status"]["crypto"])
        btc = j["crypto"]["BTC_USDT"]
        self.assertGreater(btc["price"], 0)
        self.assertEqual(btc["source"], "BINANCE_LIVE")
        age = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(btc["as_of"])).total_seconds()
        self.assertLess(age, 3600)                                   # genuinely recent, not the sealed-boundary CSV close
        after = (_snapshot(data_dir), _snapshot(data_dir / "forex"))
        self.assertEqual(before, after, "live display prices must never be written into data/ (holdout seal)")
        sealed = data_dir / "_sealed_holdout_F2026-10-04"
        if sealed.exists():
            self.assertFalse(any(p.stat().st_mtime > pd.Timestamp.now().timestamp() - 5 for p in sealed.glob("*.csv")))

    def test_every_card_has_an_as_of_time_and_decimals(self):
        listing = md.get_markets_list(force_refresh=True)
        for cls in ("crypto", "forex"):
            for q in listing[cls]:
                if q.get("available"):
                    self.assertTrue(q["as_of"], q["symbol"])
                    self.assertIn(q["price_decimals"], (2, 5, 6))

    def test_forex_24h_change_uses_close_at_or_before_24h_earlier(self):
        now = pd.Timestamp("2026-10-21 12:00", tz="UTC")
        tail = [["2026-10-20 11:00:00+00:00", 1.0], ["2026-10-20 12:00:00+00:00", 2.0], ["2026-10-20 13:00:00+00:00", 4.0]]
        self.assertAlmostEqual(md._change_vs_24h(tail, 3.0, now), 50.0)          # base = 2.0 (exactly 24 h earlier)
        self.assertIsNone(md._change_vs_24h(tail[2:], 3.0, now))                  # tail does not reach back 24 h
