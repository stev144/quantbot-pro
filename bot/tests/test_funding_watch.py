# bot/tests/test_funding_watch.py  # claude code changed: 2026-10-28 — new: live funding watch (five gates)
"""The watch's rules on injected funding (the fetcher and the ping are passed in — no network)."""
import pandas as pd
from django.test import SimpleTestCase

from bot.research import funding_watch as W
from bot.research.funding_carry_rule import RT

NOW = pd.Timestamp("2026-10-28 12:00", tz="UTC")


def _df(rate, hours=8, days=14, end=NOW - pd.Timedelta(hours=4)):
    idx = pd.date_range(end=end, periods=int(days * 24 / hours), freq=f"{hours}h", tz="UTC")
    return pd.DataFrame({"timestamp": idx.as_unit("ms").asi8, "funding_rate": rate, "mark_price": None})   # Binance: epoch ms


def _fetch(table):
    def f(sym, since_ms):
        v = table[sym]
        if isinstance(v, Exception):
            raise v
        return v
    return f


class ClassifyTest(SimpleTestCase):
    def _rates(self, df):
        return pd.Series(df["funding_rate"].to_numpy(float), index=pd.to_datetime(df["timestamp"], unit="ms", utc=True))

    def test_on_below_negative_with_the_study_hurdle(self):
        self.assertEqual(W.classify(self._rates(_df(0.0003)), NOW)["status"], W.ON)          # 21 × 0.03 % = 0.63 % ≥ 0.5 %
        self.assertEqual(W.classify(self._rates(_df(0.0001)), NOW)["status"], W.BELOW)       # 0.21 %
        self.assertEqual(W.classify(self._rates(_df(-0.0001)), NOW)["status"], W.NEGATIVE)
        c = W.classify(self._rates(_df(0.0003)), NOW)
        self.assertAlmostEqual(c["f7_pct"], 21 * 0.03)
        self.assertAlmostEqual(c["times_hurdle"], 21 * 0.0003 / RT)
        self.assertAlmostEqual(c["annual_equiv_pct"], 21 * 0.03 * 365 / 7)

    def test_four_hour_schedule(self):
        c = W.classify(self._rates(_df(0.0002, hours=4)), NOW)
        self.assertEqual(c["n_settlements_7d"], 42)
        self.assertAlmostEqual(c["interval_hours"], 4.0)
        self.assertEqual(c["status"], W.ON)                                                    # 42 × 0.02 % = 0.84 %

    def test_stale_and_insufficient(self):
        stale = _df(0.0003, end=NOW - pd.Timedelta(days=2))
        self.assertEqual(W.classify(self._rates(stale), NOW)["status"], W.STALE)
        short = _df(0.0003, days=5)
        self.assertEqual(W.classify(self._rates(short), NOW)["status"], W.INSUFFICIENT)      # no full 7-day window


class ComputeTest(SimpleTestCase):
    def test_offline_fails_fast_without_fetching(self):
        called = []
        out = W.compute_watch(fetch=lambda s, m: called.append(s), now=NOW, probe=lambda: False)
        self.assertFalse(out["available"])
        self.assertEqual(called, [])

    def test_circuit_breaker_and_per_coin_errors(self):
        coins = ("A/USDT", "B/USDT", "C/USDT", "D/USDT")
        table = {"A/USDT": _df(0.0003), "B/USDT": RuntimeError("boom"), "C/USDT": pd.DataFrame(), "D/USDT": _df(0.0003)}
        out = W.compute_watch(fetch=_fetch(table), now=NOW, probe=lambda: True, coins=coins)
        st = {c["symbol"]: c["status"] for c in out["coins"]}
        self.assertEqual(st["A/USDT"], W.ON)
        self.assertEqual(st["B/USDT"], W.UNAVAILABLE)
        self.assertEqual(st["C/USDT"], W.UNAVAILABLE)
        self.assertEqual(st["D/USDT"], W.UNAVAILABLE)                                         # stopped after 2 failures in a row
        self.assertIn("not fetched", [c for c in out["coins"] if c["symbol"] == "D/USDT"][0]["reason"])
        self.assertTrue(out["available"])
        self.assertEqual(out["n_on"], 1)

    def test_future_settlements_are_ignored(self):
        df = _df(0.0003, end=NOW + pd.Timedelta(hours=16))                                     # rows after "now"
        out = W.compute_watch(fetch=_fetch({"A/USDT": df}), now=NOW, probe=lambda: True, coins=("A/USDT",))
        last = pd.Timestamp(out["coins"][0]["last_settlement"])
        self.assertLessEqual(last, NOW)

    def test_writes_nothing_to_research_data(self):
        import os
        before = set(os.listdir("research_data"))
        W.compute_watch(fetch=_fetch({"A/USDT": _df(0.0003)}), now=NOW, probe=lambda: True, coins=("A/USDT",))
        self.assertEqual(set(os.listdir("research_data")), before)
