# bot/tests/test_home_asset_metrics.py  # claude code changed: 2026-10-27 — new: home page risk & cost metrics
"""Known answers for bot/market_intel/asset_metrics.py and the home page cell that shows them."""
import math

import numpy as np
import pandas as pd
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from bot.market_intel import asset_metrics as am


def _hourly(values, start="2026-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="h", tz="UTC"), dtype=float)


class RealizedVolTest(SimpleTestCase):
    def test_alternating_returns_known_answer(self):
        # log returns alternate +r, −r: sample std over 168 returns = r·sqrt(168/167)
        r = 0.001
        logp = np.cumsum([0.0] + [r if i % 2 == 0 else -r for i in range(168)])
        vol = am.realized_vol_pct(_hourly(np.exp(logp)), 8760)
        self.assertAlmostEqual(vol, r * math.sqrt(168 / 167) * math.sqrt(8760) * 100, places=9)

    def test_too_few_candles(self):
        self.assertIsNone(am.realized_vol_pct(_hourly([1.0, 1.01, 1.02]), 8760))


class VolatilityRatioTest(SimpleTestCase):
    def _days(self, ranges):
        rows = []
        for d, rng in enumerate(ranges):
            for h in range(24):
                rows.append((100 + rng / 2, 100 - rng / 2))
        idx = pd.date_range("2026-01-01", periods=len(rows), freq="h", tz="UTC")
        return pd.DataFrame(rows, index=idx, columns=["high", "low"])

    def test_engine_definition_and_states(self):
        # 31 days (the last is treated as incomplete and dropped): 23 days of range 2, then 7 days of range 1
        ratio, state = am.volatility_ratio(self._days([2] * 23 + [1] * 7 + [9]))
        self.assertAlmostEqual(ratio, 1 / ((23 * 2 + 7) / 30))
        self.assertEqual(state, "COMPRESSED")          # 0.566 < 0.75 (RangeAnalytics threshold)
        ratio, state = am.volatility_ratio(self._days([1] * 23 + [3] * 7 + [9]))
        self.assertEqual(state, "EXPANDING")           # 3 / (44/30) = 2.05 > 1.35

    def test_fewer_than_30_days(self):
        self.assertEqual(am.volatility_ratio(self._days([1] * 20)), (None, None))


class HalfLifeTest(SimpleTestCase):
    def test_dickey_fuller_p_matches_statsmodels(self):
        from statsmodels.tsa.stattools import adfuller
        x = _hourly(np.cumsum(np.random.default_rng(3).normal(0, 0.001, 168)))
        expected = adfuller(x.to_numpy(), maxlag=0, regression="c", autolag=None)[1]
        self.assertAlmostEqual(am.dickey_fuller_p(x), expected, places=10)

    def test_random_walks_rarely_get_a_half_life(self):
        rng = np.random.default_rng(11)
        shown = sum(am.half_life(_hourly(100 * np.exp(np.cumsum(rng.normal(0, 0.002, 168)))))["hours"] is not None
                    for _ in range(400))
        self.assertLess(shown / 400, 0.09)   # ~5% expected at alpha = 0.05

    def test_mean_reverting_series_usually_get_one(self):
        # true half-life 5 h: the 5% Dickey-Fuller gate detects it in ~91% of 7-day paths (it is a test, not a certainty)
        rng, phi, shown, hours = np.random.default_rng(5), 0.5 ** (1 / 5), 0, []
        for _ in range(200):
            y = [0.0]
            for _ in range(167):
                y.append(phi * y[-1] + rng.normal(0, 0.002))
            hl = am.half_life(_hourly(100 * np.exp(y)))
            if hl["hours"] is not None:
                shown += 1
                hours.append(hl["hours"])
        self.assertGreater(shown / 200, 0.8)
        self.assertLess(abs(np.median(hours) - 5), 2)   # short-window AR(1) is biased low, but near the truth

    def test_weekend_gap_is_not_bridged(self):
        x = _hourly(100 * np.exp(np.cumsum(np.random.default_rng(2).normal(0, 0.002, 168))))
        x = x.drop(x.index[60:108])                     # 48 missing hours, like a forex weekend
        grid = x.reindex(pd.date_range(x.index[0], x.index[-1], freq="h"))
        from bot.quant_core.time_grid import lag_pairs
        self.assertEqual(len(lag_pairs(grid)), 118)     # 120 candles, one gap: 59 + 59 adjacent pairs, none across it
        self.assertIn("text", am.half_life(x))          # and the half-life runs on it without a grid error


class RelativeVolumeAndSpreadTest(SimpleTestCase):
    def test_rvol_known_answer(self):
        v = _hourly([10.0] * am.RVOL_BASE_BARS + [25.0] * am.RVOL_RECENT_BARS)
        self.assertAlmostEqual(am.relative_volume(v), 2.5)

    def test_spread_known_answer(self):
        out = am.spread(99.99, 100.01, 10.0, 8760)       # 2 bp; day σ = 10%/√8760·√24
        self.assertAlmostEqual(out["spread_bps"], 2.0)
        self.assertAlmostEqual(out["spread_pct_of_day"], 0.0002 / (0.10 / math.sqrt(8760) * math.sqrt(24)) * 100)

    def test_invalid_quotes(self):
        self.assertEqual(am.spread(None, 1.0, 5.0, 8760), {})
        self.assertEqual(am.spread(1.01, 1.00, 5.0, 8760), {})


class MetricsCellTest(SimpleTestCase):
    def test_cell_shows_every_metric_and_the_forex_rvol_reason(self):
        m = {"vol_7d_pct": 12.4, "vol_ratio": 0.92, "vol_state": "NORMAL", "rvol": None, "pair": "EUR/USD",
             "rvol_note": "n/a: mixed broker feed", "half_life": {"text": "none", "title": "no evidence"},
             "spread_bps": 1.9, "spread_pct_of_day": 5.1}
        html = render_to_string("partials/_hq_metrics_cell.html", {"m": m})
        for text in ("VOL 7D <b>12.4%", '0.92 <i class="hq-st">normal</i>', "HALF-LIFE <b>none", "RVOL <b>n/a",
                     "n/a: mixed broker feed", "1.9 bp · 5.1% of 1-day σ"):   # claude code changed: 2026-10-27 — state word in its own span
            self.assertIn(text, html)

    def test_missing_metrics_say_computing(self):
        self.assertIn("computing", render_to_string("partials/_hq_metrics_cell.html", {"m": None}))


class SharedMetricsTest(SimpleTestCase):   # claude code changed: 2026-10-27 — home + Markets use the same helper
    SNAP = {"available": True,
            "metrics": {"BTC_USDT": {"vol_7d_pct": 30.0, "bars_per_year": 8760, "half_life": {"text": "none", "title": ""}}},
            "fx_metrics": {"EUR_USD": {"vol_7d_pct": 6.0, "bars_per_year": 6240, "pair": "EUR/USD",
                                       "half_life": {"text": "none", "title": ""}}}}
    LIVE = {"crypto": {"BTC_USDT": {"bid": "99.99", "ask": "100.01"}},
            "forex": {"EUR_USD": {"bid": 1.1000, "ask": 1.1002, "change_pct": 0.2},
                      "GBP_USD": {"bid": 1.3, "ask": 1.3002, "change_pct": -0.1},
                      "USD_JPY": {"bid": 150.0, "ask": 150.02, "change_pct": 0.05}}}

    def test_quote_metrics_adds_the_live_spread(self):
        from bot.views.home_intelligence import quote_metrics
        m = quote_metrics("BTC_USDT", "crypto", self.SNAP, self.LIVE)
        self.assertAlmostEqual(m["spread_bps"], 2.0)
        self.assertEqual(quote_metrics("EUR_USD", "forex", self.SNAP, self.LIVE)["pair"], "EUR/USD")
        self.assertIsNone(quote_metrics("ETH_USDT", "crypto", self.SNAP, self.LIVE))           # not computed yet
        self.assertIsNone(quote_metrics("BTC_USDT", "crypto", {"available": False}, self.LIVE))

    def test_forex_pairs_section_ranks_real_pairs(self):
        from bot.research.cross_section_engine import MIN_ASSETS_FOR_CROSS_SECTION
        from bot.views.home_intelligence import forex_pairs_section
        live = {"forex": dict(self.LIVE["forex"])}
        for i in range(MIN_ASSETS_FOR_CROSS_SECTION):   # enough pairs for a cross-section; all below the three above
            live["forex"][f"X{i}_Y{i}"] = {"bid": 1.0, "ask": 1.0001, "change_pct": -0.5 - i / 100}
        out = forex_pairs_section(live, self.SNAP)
        self.assertTrue(out["available"], out.get("reason"))
        self.assertEqual([r["symbol"] for r in out["rows"][:3]], ["EUR_USD", "USD_JPY", "GBP_USD"])
        self.assertEqual(out["rows"][0]["display"], "EUR/USD")
        self.assertIsNotNone(out["rows"][0]["metrics"])

    def test_fx_row_and_markets_card_show_the_chips(self):
        from bot.views.home_intelligence import quote_metrics
        m = quote_metrics("EUR_USD", "forex", self.SNAP, self.LIVE)
        row = render_to_string("partials/_hq_fx_row.html",
                               {"r": {"symbol": "EUR_USD", "display": "EUR/USD", "return_pct": 0.2, "z": 1.1, "rank": 1,
                                      "metrics": m}, "n": 3})
        self.assertIn("EUR/USD", row)
        self.assertIn('class="r num pos-t"', row)
        self.assertIn("gold-t", row)
        self.assertIn("VOL 7D", row)
        card = render_to_string("partials/_mkt_card.html",
                                {"q": {"symbol": "EUR_USD", "asset_class": "forex", "display_symbol": "EUR/USD",
                                       "price": "1.1001", "change_pct": 0.2, "positive": True, "metrics": m}})
        self.assertIn('class="mkt-metrics"', card)
        self.assertIn("SPREAD", card)
