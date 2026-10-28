# bot/tests/test_quant_metrics.py  # claude code changed: 2026-10-28 — new: institutional market metrics
"""Known-answer tests for bot.quant_metrics, plus one real-data run (no mocking)."""
import math

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.quant_metrics import factors as F
from bot.quant_metrics import regimes as R
from bot.quant_metrics.api import SymbolMetrics, completed, rank_universe, symbol_metrics
from bot.quant_metrics.ranking import cross_sectional_percentile, time_series_percentile
from bot.quant_metrics.statistics import (lo_mackinlay_variance_ratio, newey_west_lag, newey_west_se_of_mean,
                                          sharpe_ratio)

H = pd.Timedelta(hours=1)


def _hourly(close, start="2025-01-01"):
    idx = pd.date_range(start, periods=len(close), freq="h", tz="UTC")
    return pd.DataFrame({"close": close}, index=idx)


class StatisticsTest(SimpleTestCase):
    def test_newey_west_lag_rule(self):
        self.assertEqual(newey_west_lag(100), 4)
        self.assertEqual(newey_west_lag(168), 4)
        self.assertEqual(newey_west_lag(1000), 6)

    def test_newey_west_with_zero_lag_is_the_iid_se(self):
        x = np.array([1.0, 2.0, 4.0, 7.0, 11.0])
        self.assertAlmostEqual(newey_west_se_of_mean(x, lag=0), x.std(ddof=0) / math.sqrt(5))

    def test_newey_west_widens_for_positively_autocorrelated_data(self):
        rng = np.random.default_rng(0)
        e = rng.normal(size=2000)
        ar = np.empty_like(e); ar[0] = e[0]
        for i in range(1, len(e)):
            ar[i] = 0.7 * ar[i - 1] + e[i]
        self.assertGreater(newey_west_se_of_mean(ar), 1.5 * ar.std(ddof=0) / math.sqrt(len(ar)))

    def test_variance_ratio_detects_random_walk_trend_and_reversion(self):
        rng = np.random.default_rng(1)
        n, q = 3000, 10
        rw = np.cumsum(rng.normal(size=n))
        vr = lo_mackinlay_variance_ratio(rw, q)
        self.assertLess(abs(vr.z_robust), 3.0)
        self.assertAlmostEqual(vr.vr, 1.0, delta=0.25)
        for phi, sign in ((0.3, 1), (-0.3, -1)):
            e = rng.normal(size=n); r = np.empty(n); r[0] = e[0]
            for i in range(1, n):
                r[i] = phi * r[i - 1] + e[i]
            v = lo_mackinlay_variance_ratio(np.concatenate([[0.0], np.cumsum(r)]), q)
            self.assertGreater(sign * v.z_robust, 1.96)
            self.assertEqual(sign > 0, v.hurst > 0.5)
        self.assertIsNone(lo_mackinlay_variance_ratio(rw[:50], q))   # T < 10 q

    def test_sharpe_known_answer(self):
        r = np.array([0.01, 0.02, -0.01, 0.03, 0.0, 0.01, 0.02, -0.02, 0.01, 0.01])
        s = sharpe_ratio(r, 365.0)
        sr_p = r.mean() / r.std(ddof=1)
        self.assertAlmostEqual(s.sharpe, sr_p * math.sqrt(365))
        self.assertAlmostEqual(s.se, math.sqrt((1 + sr_p ** 2 / 2) / 10) * math.sqrt(365))
        self.assertAlmostEqual(sharpe_ratio(r, 365.0, risk_free_annual=0.0365).sharpe,
                               (r.mean() - 0.0001) / r.std(ddof=1) * math.sqrt(365))
        self.assertIsNone(sharpe_ratio(np.zeros(20), 365.0))


class FactorTest(SimpleTestCase):
    def test_zscore_known_answer_and_zero_dispersion(self):
        close = pd.Series(np.exp(np.arange(168, dtype=float) / 100.0))
        x = np.arange(168) / 100.0
        self.assertAlmostEqual(F.rolling_zscore(close), (x[-1] - x.mean()) / x.std(ddof=1))
        self.assertIsNone(F.rolling_zscore(pd.Series(np.ones(168))))
        self.assertIsNone(F.rolling_zscore(pd.Series(np.ones(10))))

    def test_efficiency_ratio_extremes(self):
        self.assertAlmostEqual(F.efficiency_ratio(pd.Series(np.arange(1.0, 26.0))), 1.0)
        zigzag = pd.Series([1.0, 2.0] * 12 + [1.0])
        self.assertAlmostEqual(F.efficiency_ratio(zigzag), 0.0)

    def test_trend_quality_is_50_without_drift_and_high_with_drift(self):
        rng = np.random.default_rng(2)
        up = pd.Series(np.exp(np.cumsum(0.002 + 0.001 * rng.normal(size=200))))
        self.assertGreater(F.trend_quality(up)["score"], 97.5)
        flat = pd.Series(np.exp(np.cumsum(np.tile([0.01, -0.01], 100))))
        self.assertAlmostEqual(F.trend_quality(flat)["score"], 50.0, delta=1.0)

    def test_realized_vol_uses_returns_not_levels(self):
        c = pd.Series(np.exp(np.cumsum(np.tile([0.01, -0.01], 30))))
        v = F.realized_vol_series(c, 365.0).dropna().iloc[-1]
        self.assertAlmostEqual(v, np.std(np.tile([0.01, -0.01], 10), ddof=1) * math.sqrt(365) * 100, places=6)
        self.assertAlmostEqual(F.realized_vol_series(c * 1000, 365.0).dropna().iloc[-1], v, places=6)   # scale-free

    def test_momentum_and_amihud_known_answers(self):
        c = pd.Series([100.0] * 30 + [110.0])
        self.assertAlmostEqual(F.momentum_return(c), math.log(1.1))
        close = pd.Series([100.0, 101.0, 100.0] * 11)
        vol = pd.Series([2e6] * len(close))
        r = np.abs(np.diff(np.log(close.to_numpy())))[-30:]
        self.assertAlmostEqual(F.amihud_illiquidity(close, vol), float(np.mean(r / 2e6) * 1e6 * 100))


class RegimeAndRankingTest(SimpleTestCase):
    def test_zscore_labels(self):
        self.assertEqual(R.zscore_labels(2.14), {"strength": "STRONG", "regime": "EXTENDED_ABOVE"})
        self.assertEqual(R.zscore_labels(-1.2), {"strength": "MODERATE", "regime": "NEAR_MEAN"})

    def test_volatility_quintiles(self):
        self.assertEqual([R.volatility_regime(p) for p in (5, 25, 50, 70, 95)],
                         ["VERY_LOW", "LOW", "NORMAL", "HIGH", "EXTREME"])

    def test_variance_ratio_regime_uses_5pct_critical_value(self):
        self.assertEqual(R.variance_ratio_regime(2.0), "TRENDING")
        self.assertEqual(R.variance_ratio_regime(-2.0), "MEAN_REVERTING")
        self.assertEqual(R.variance_ratio_regime(1.9), "RANDOM_WALK")

    def test_cross_sectional_percentile(self):
        p = cross_sectional_percentile({"a": 1.0, "b": 3.0, "c": 2.0, "d": None})
        self.assertEqual(p, {"a": 0.0, "b": 100.0, "c": 50.0, "d": None})
        self.assertEqual(cross_sectional_percentile({"a": 1.0}), {"a": None})

    def test_time_series_percentile_is_causal(self):
        self.assertEqual(time_series_percentile(pd.Series([1.0, 2.0, 3.0, 4.0]), 3), 100.0)
        self.assertEqual(time_series_percentile(pd.Series([4.0, 3.0, 2.0, 1.0]), 3), 25.0)
        self.assertIsNone(time_series_percentile(pd.Series([1.0, 2.0]), 3))

    def test_rank_universe_one_class_only(self):
        rows = {"A": SymbolMetrics(symbol="A", asset_class="crypto", momentum_30d=0.1, amihud_illiquidity=2.0),
                "B": SymbolMetrics(symbol="B", asset_class="crypto", momentum_30d=0.3, amihud_illiquidity=1.0)}
        rank_universe(rows)
        self.assertEqual((rows["A"].momentum_rank, rows["B"].momentum_rank), (0.0, 100.0))
        self.assertEqual((rows["A"].liquidity_score, rows["B"].liquidity_score), (0.0, 100.0))
        rows["C"] = SymbolMetrics(symbol="C", asset_class="forex")
        with self.assertRaises(ValueError):
            rank_universe(rows)


class RegimeCardRenderTest(SimpleTestCase):
    def test_card_shows_institutional_metrics_instead_of_retail_indicators(self):
        from django.template.loader import render_to_string
        q = {"symbol": "BTC_USDT", "asset_class": "crypto", "display_symbol": "BTC/USDT", "display_name": "Bitcoin",
             "price_decimals": 2, "as_of": "", "sparkline": "", "metrics": None}
        qm = SymbolMetrics(symbol="BTC_USDT", asset_class="crypto", zscore=2.14, momentum_rank=92.0,
                           volatility_regime="HIGH", volatility=55.0, volatility_percentile=70.0, liquidity_score=85.0,
                           hurst=0.47, hurst_regime="RANDOM_WALK", trend_quality=71.0, efficiency_ratio=0.34,
                           sharpe_90=1.2, sharpe_90_se=2.0, sharpe_30=0.5, sharpe_30_se=3.5).model_dump()
        regime = {"regime": "RANGING", "confidence": "MEDIUM", "adx": 22.7, "atr_ratio": 0.9, "ema_spread_pct": 0.1,
                  "bb_width_pct": 3.1, "volatility_extreme": False, "qm": qm}
        html = render_to_string("partials/_mkt_card.html", {"q": q, "regime": regime})
        for s in ("ZS <b>+2.14", "MOM <b>92", "VOL <b>HIGH", "LIQ <b>85", "TQ <b>71", "ER <b>0.34", "SR90 <b>1.2"):
            self.assertIn(s, html)
        self.assertNotIn("ADX <b>", html)
        regime.pop("qm")                                    # rows without metrics keep the old readout
        self.assertIn("ADX <b>", render_to_string("partials/_mkt_card.html", {"q": q, "regime": regime}))


class ApiTest(SimpleTestCase):
    def test_open_candle_is_dropped(self):
        df = _hourly([1.0, 2.0, 3.0], start="2025-01-01 00:00")
        now = pd.Timestamp("2025-01-01 02:30", tz="UTC")      # the 02:00 candle is still open
        self.assertEqual(len(completed(df, "1h", now)), 2)
        self.assertEqual(len(completed(df, "1h", now + H)), 3)

    def test_windowed_factors_ignore_candles_outside_their_window(self):
        rng = np.random.default_rng(3)
        close = np.exp(np.cumsum(0.01 * rng.normal(size=1000)))
        h = _hourly(close)
        now = h.index[-1] + H
        a = symbol_metrics("X", h, None, "crypto", now=now)
        h2 = h.copy()
        h2.iloc[:500, 0] *= 1.5                                 # change only candles older than every window
        b = symbol_metrics("X", h2, None, "crypto", now=now)
        for f in ("zscore", "efficiency_ratio", "trend_quality"):
            self.assertAlmostEqual(getattr(a, f), getattr(b, f), msg=f)
        self.assertEqual(a.as_of, str(h.index[-1]))

    def test_real_btc_data(self):
        df = pd.read_csv("data/BTC_USDT_1h.csv", usecols=["timestamp", "close", "qav"])
        df.index = pd.to_datetime(df.pop("timestamp"), utc=True)
        df = df.tail(24 * 420)
        daily = df.resample("1D").agg({"close": "last", "qav": "sum"}).dropna()
        m = symbol_metrics("BTC/USDT", df.tail(1000), daily, "crypto")
        for f in ("zscore", "efficiency_ratio", "trend_quality", "variance_ratio", "hurst", "sharpe_30",
                  "sharpe_90", "volatility", "volatility_percentile", "momentum_30d", "amihud_illiquidity"):
            self.assertIsNotNone(getattr(m, f), f)
        self.assertTrue(0 <= m.efficiency_ratio <= 1)
        self.assertTrue(0 <= m.trend_quality <= 100)
        self.assertIn(m.volatility_regime, R.VOL_CLASSES)
        self.assertIn(m.hurst_regime, ("TRENDING", "MEAN_REVERTING", "RANDOM_WALK"))
