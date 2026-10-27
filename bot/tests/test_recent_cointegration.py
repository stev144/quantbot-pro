# bot/tests/test_recent_cointegration.py  # claude code changed: 2026-10-27 — new: Stage 1 recent-window scan
import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research import recent_cointegration as rc


def _hourly(values, start="2026-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="h", tz="UTC"))


class WindowsAndSealsTest(SimpleTestCase):
    def test_windows_end_before_each_seal(self):
        from bot.quant_core.research_zones import ZONES
        from bot.research.fx_holdout_guard import FX_RESEARCH_DATA_CUTOFF
        w = rc.windows()
        self.assertLess(w["crypto"][1], ZONES.F)
        self.assertEqual(w["crypto"][1], ZONES.F - pd.Timedelta(hours=1))
        self.assertLess(w["forex"][1], pd.Timestamp(FX_RESEARCH_DATA_CUTOFF))
        for start, end in w.values():
            self.assertEqual(start + pd.DateOffset(months=12), end + pd.Timedelta(hours=1))


class PegFlagTest(SimpleTestCase):
    def test_known_answers(self):
        self.assertEqual(rc.peg_flag("EUR_USD", "USD_DKK"), "mechanical (currency peg)")   # USD/DKK ~ 1/(EUR/USD)
        self.assertEqual(rc.peg_flag("EUR_HKD", "EUR_USD"), "mechanical (currency peg)")
        self.assertEqual(rc.peg_flag("EUR_DKK", "GBP_USD"), "near-constant (currency peg)")
        self.assertIsNone(rc.peg_flag("EUR_USD", "GBP_USD"))
        self.assertIsNone(rc.peg_flag("BTC_USDT", "ETH_USDT"))


class SyntheticPairsTest(SimpleTestCase):
    def test_cointegrated_pair_is_found_with_its_hedge_ratio_and_half_life(self):
        rng = np.random.default_rng(1)
        n, phi = 3000, 0.5 ** (1 / 10)                       # spread half-life 10 h
        b = np.cumsum(rng.normal(0, 0.003, n)) + np.log(100)
        s = [0.0]
        for _ in range(n - 1):
            s.append(phi * s[-1] + rng.normal(0, 0.002))
        a = 0.2 + 1.5 * b + np.array(s)
        r = rc.test_pair("A", "B", _hourly(np.exp(a)), _hourly(np.exp(b)))
        self.assertEqual(r["status"], "OK")
        self.assertLess(r["p"], 0.001)
        self.assertAlmostEqual(r["beta"], 1.5, delta=0.05)
        self.assertLess(abs(r["half_life_h"] - 10), 4)

    def test_independent_random_walks_are_rarely_selected(self):
        rng = np.random.default_rng(2)
        closes = {f"S{i}": _hourly(100 * np.exp(np.cumsum(rng.normal(0, 0.003, 1500)))) for i in range(8)}
        out = rc.scan(closes, "crypto", n_workers=1)       # 28 pairs, no true relationship
        self.assertEqual(out["n_testable"], 28)
        self.assertLessEqual(out["n_bh05"], 1)              # BH keeps false selections rare

    def test_untestable_pair_is_recorded_not_dropped(self):
        short = _hourly(np.linspace(1, 2, 200))
        r = rc.test_pair("A", "B", short, short * 2)
        self.assertEqual(r["status"], "UNTESTABLE")
