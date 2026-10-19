# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/test_market_intelligence_failfast.py
# claude code changed: new file — 2026-10-07. Market Intelligence must fail fast when the internet is down
# (reported: ~155 symbols × ~19 s of retries ≈ 50 min for one page). No network: the probe and get_klines are
# replaced in-process; nothing here contacts an exchange.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from unittest import mock

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot import data_fetcher
from bot.views import market_intelligence_data as mid

SYMS = [f"S{i:02d}_USDT" for i in range(10)]


def _df(n=300, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 0.5, n))
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": c, "high": c + 0.6, "low": c - 0.6, "close": c, "volume": 1.0}, index=idx)


class ProbeTests(SimpleTestCase):

    def test_probe_is_one_attempt_with_a_short_timeout_and_never_raises(self):
        with mock.patch.object(data_fetcher.requests, "get", side_effect=OSError("offline")) as g:
            self.assertFalse(data_fetcher.binance_reachable())
        self.assertEqual(g.call_count, 1)                                   # no retries
        self.assertLessEqual(max(g.call_args.kwargs["timeout"]), 3.05)
        ok = mock.Mock(status_code=200)
        with mock.patch.object(data_fetcher.requests, "get", return_value=ok):
            self.assertTrue(data_fetcher.binance_reachable())
        with mock.patch.object(data_fetcher.requests, "get", return_value=mock.Mock(status_code=451)):
            self.assertFalse(data_fetcher.binance_reachable())


class FailFastTests(SimpleTestCase):

    def _run(self, reachable, klines_side_effect):
        with mock.patch.object(mid, "discover_symbol_universe", return_value=list(SYMS)), \
                mock.patch.object(mid, "binance_reachable", return_value=reachable), \
                mock.patch.object(mid, "get_klines", side_effect=klines_side_effect) as gk:
            return mid.get_market_regime_overview(), gk

    def test_offline_returns_unavailable_without_fetching_any_symbol(self):
        out, gk = self._run(False, lambda *a, **k: _df())
        self.assertFalse(out["available"])
        self.assertIn("unreachable", out["reason"])
        self.assertEqual(gk.call_count, 0)                                  # not one ~19 s fetch

    def test_connection_dropping_mid_page_stops_after_three_failures(self):
        out, gk = self._run(True, lambda *a, **k: pd.DataFrame())
        self.assertEqual(gk.call_count, mid.MAX_CONSECUTIVE_FETCH_FAILURES)   # 3 tries, not 10
        self.assertTrue(out["fetch_aborted"])
        self.assertIn("7 symbols not fetched", out["aborted_reason"])
        self.assertEqual(out["n_errors"], len(SYMS))                         # every symbol is accounted for
        self.assertFalse(out["available"])

    def test_isolated_failures_do_not_trip_the_breaker(self):
        seq = iter([pd.DataFrame(), pd.DataFrame(), _df(), pd.DataFrame(), RuntimeError("x"), _df(),
                    _df(), _df(), _df(), _df()])
        def side(*a, **k):
            v = next(seq)
            if isinstance(v, Exception):
                raise v
            return v
        out, gk = self._run(True, side)
        self.assertEqual(gk.call_count, len(SYMS))
        self.assertFalse(out["fetch_aborted"])
        self.assertEqual((out["n_symbols"], out["n_errors"]), (6, 4))

    def test_online_behaviour_unchanged(self):
        out, gk = self._run(True, lambda *a, **k: _df())
        self.assertEqual(gk.call_count, len(SYMS))
        self.assertTrue(out["available"])
        self.assertEqual(out["n_symbols"], len(SYMS))
        self.assertFalse(out["fetch_aborted"])
        self.assertEqual(gk.call_args.kwargs["total_candles"], mid.CANDLES_PER_SYMBOL)
