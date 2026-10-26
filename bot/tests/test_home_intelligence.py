# claude code changed: 2026-10-26 — new file; tests for the home page redesign (bot/views/home_intelligence.py,
# templates/dashboard.html). Known answers are worked by hand in the comments. No mocking: synthetic inputs go through
# the real functions, the real RegimeDetector, the real research cross-section engine and the real template.
import math

import numpy as np
import pandas as pd
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from bot.engines.regime_detector import RegimeDetector
from bot.research.cross_section_engine import CrossSectionEngine, MIN_ASSETS_FOR_CROSS_SECTION
from bot.views import home_intelligence as hi

NOW = pd.Timestamp("2026-10-26 12:00", tz="UTC")


def _ohlc(close, start="2026-09-01"):
    close = np.asarray(close, dtype=float)
    idx = pd.date_range(start, periods=len(close), freq="h", tz="UTC")
    return pd.DataFrame({"open": close, "high": close * 1.002, "low": close * 0.998, "close": close,
                         "volume": 1.0}, index=idx)


class CrossSectionKnownAnswerTest(SimpleTestCase):
    def test_known_answer(self):
        # r = 1, 2, 3, 6: mean 3; sample var = (4 + 1 + 0 + 9) / 3 = 14/3; σ = 2.16025; z_D = 3 / σ = 1.38873
        cs = hi.cross_section({"A": 1, "B": 2, "C": 3, "D": 6})
        self.assertTrue(cs["available"])
        self.assertAlmostEqual(cs["mean_pct"], 3.0)
        self.assertAlmostEqual(cs["std_pct"], math.sqrt(14 / 3))
        z = {r["symbol"]: r["z"] for r in cs["rows"]}
        self.assertAlmostEqual(z["D"], 3 / math.sqrt(14 / 3))
        self.assertAlmostEqual(z["A"], -2 / math.sqrt(14 / 3))
        self.assertAlmostEqual(sum(z.values()), 0.0)
        self.assertEqual([r["symbol"] for r in cs["rows"]], ["D", "C", "B", "A"])   # rank 1 = highest return
        self.assertEqual([r["rank"] for r in cs["rows"]], [1, 2, 3, 4])
        self.assertEqual((cs["n_up"], cs["n_down"], cs["n_flat"]), (4, 0, 0))
        self.assertAlmostEqual(cs["breadth_pct"], 100.0)

    def test_ties_are_deterministic_and_flat_counted(self):
        cs = hi.cross_section({"B": 1, "A": 1, "C": 0, "D": -1})
        self.assertEqual([r["symbol"] for r in cs["rows"]], ["A", "B", "C", "D"])
        self.assertEqual((cs["n_up"], cs["n_flat"], cs["n_down"]), (2, 1, 1))
        self.assertAlmostEqual(cs["breadth_pct"], 50.0)

    def test_zero_variance_and_small_universe_are_unavailable(self):
        self.assertFalse(hi.cross_section({"A": 1, "B": 1, "C": 1, "D": 1})["available"])
        self.assertFalse(hi.cross_section({s: i for i, s in enumerate("ABC")})["available"])
        self.assertEqual(MIN_ASSETS_FOR_CROSS_SECTION, 4)

    def test_missing_values_are_dropped_and_counted(self):
        cs = hi.cross_section({"A": 1, "B": None, "C": float("nan"), "D": 2, "E": 3, "F": 4})
        self.assertEqual(cs["n"], 4)

    def test_matches_the_research_engine_definition(self):
        rng = np.random.default_rng(7)
        row = dict(zip([f"S{i}" for i in range(12)], rng.normal(0, 0.02, 12)))
        matrix = pd.DataFrame([row], index=[NOW])
        feats = CrossSectionEngine()._calculate_cross_sectional_features(matrix)
        cs = hi.cross_section(row)
        for r in cs["rows"]:
            self.assertAlmostEqual(r["z"], float(feats[f"{r['symbol']}__cs_zscore"].iloc[0]), places=10)


class CurrencyVsUsdTest(SimpleTestCase):
    def test_each_currency_once_against_usd(self):
        out = hi.currency_vs_usd({"EUR_USD": 1.0, "USD_JPY": 1.0, "EUR_GBP": 5.0, "XAU_USD": 3.0, "GBP_USD": None})
        self.assertEqual(set(out), {"EUR", "JPY"})          # cross, metal and missing value left out
        self.assertAlmostEqual(out["EUR"], 1.0)
        self.assertAlmostEqual(out["JPY"], (1 / 1.01 - 1) * 100)   # exact inverse, not -1.0

    def test_forex_pulse_counts_pairs_without_a_live_price(self):
        listing = {"forex": [{"symbol": s, "available": True} for s in
                             ("EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "EUR_DKK")]}
        live = {"forex": {s: {"change_pct": c, "as_of": NOW.isoformat()} for s, c in
                          [("EUR_USD", 0.2), ("GBP_USD", -0.1), ("USD_JPY", 0.3), ("AUD_USD", 0.1), ("USD_CAD", -0.2)]},
                "forex_session": {"is_open": True}, "status": {}}
        f = hi.forex_pulse(live, listing, NOW)
        self.assertTrue(f["available"])
        self.assertEqual((f["n_pairs_listed"], f["n_pairs_live"], f["n_pairs_not_live"]), (6, 5, 1))
        self.assertEqual(f["n"], 5)
        self.assertEqual(f["strongest"]["symbol"], "CAD")   # USD/CAD −0.2% → CAD +0.2004% vs USD
        self.assertEqual(f["top_pair"]["symbol"], "USD_JPY")


class CryptoPulseTest(SimpleTestCase):
    def _inputs(self):
        listing = {"crypto": [{"symbol": f"C{i}_USDT", "available": True, "display_symbol": f"C{i}/USDT"} for i in range(8)]}
        fresh = (NOW - pd.Timedelta(minutes=1)).isoformat()
        live = {"crypto": {f"C{i}_USDT": {"change_pct": c, "as_of": fresh, "quote_volume": 1e6}
                           for i, c in enumerate([5, 3, 1, 0, -1, -2])},
                "status": {"crypto": "LIVE"}, "fetched_at": NOW.isoformat()}
        live["crypto"]["C6_USDT"] = {"change_pct": 40, "as_of": (NOW - pd.Timedelta(hours=3)).isoformat()}
        return live, listing

    def test_stale_and_missing_quotes_are_excluded_and_reported(self):
        live, listing = self._inputs()
        c = hi.crypto_pulse(live, listing, NOW)
        self.assertEqual((c["n_universe"], c["n"], c["n_stale"], c["n_missing"]), (8, 6, 1, 1))
        self.assertEqual(c["stale_symbols"], ["C6"])
        self.assertEqual((c["n_up"], c["n_flat"], c["n_down"]), (3, 1, 2))
        self.assertAlmostEqual(c["breadth_pct"], 50.0)
        self.assertEqual(c["top"]["symbol"], "C0_USDT")

    def test_watchlist_only_extremes_largest_first(self):
        listing = {"crypto": [{"symbol": f"C{i}_USDT", "available": True} for i in range(10)]}
        live = {"crypto": {f"C{i}_USDT": {"change_pct": c, "as_of": NOW.isoformat()}
                           for i, c in enumerate([30, 0, 0, 0, 0, 0, 0, 0, 0, -1])}, "status": {}}
        c = hi.crypto_pulse(live, listing, NOW)
        hi.annotate_regimes(c, {"available": False})
        wl = hi.research_watchlist(c, {"available": False})
        self.assertEqual([w["symbol"] for w in wl], ["C0_USDT"])   # z ≈ +2.85; nothing else reaches |z| 2
        self.assertTrue(all(abs(w["z"]) >= hi.EXTREME_Z for w in wl))
        self.assertEqual(wl[0]["regime_words"], "Not in snapshot")


class CorrelationSummaryTest(SimpleTestCase):
    def test_comoving_coins_and_a_delisted_coin(self):
        rng = np.random.default_rng(1)
        idx = pd.date_range("2026-10-14", periods=300, freq="h", tz="UTC")
        common = np.cumsum(rng.normal(0, 0.01, 300))
        closes = {f"C{i}": pd.Series(100 * np.exp(common + rng.normal(0, 0.001, 300)), index=idx) for i in range(4)}
        # a delisted coin whose candles stop weeks earlier must not shrink the synchronised window to nothing
        closes["OLD"] = pd.Series(np.linspace(1, 2, 300), index=pd.date_range("2026-08-01", periods=300, freq="h", tz="UTC"))
        out = hi.correlation_summary(closes)
        self.assertTrue(out["available"])
        self.assertEqual(out["n_coins"], 4)
        self.assertEqual(out["n_obs"], 299)
        self.assertGreater(out["mean"], 0.9)

    def test_too_few_observations_is_unavailable(self):
        idx = pd.date_range("2026-10-14", periods=50, freq="h", tz="UTC")
        closes = {f"C{i}": pd.Series(np.linspace(1, 2, 50) + i, index=idx) for i in range(4)}
        self.assertFalse(hi.correlation_summary(closes)["available"])


class RegimeIntelligenceTest(SimpleTestCase):
    def _series(self):
        rng = np.random.default_rng(3)
        n = 600
        return {
            "up": 100 * np.exp(np.cumsum(rng.normal(0.003, 0.004, n))),
            "down": 100 * np.exp(np.cumsum(rng.normal(-0.003, 0.004, n))),
            "flat": 100 + rng.normal(0, 0.05, n),
            "spike": np.concatenate([100 + rng.normal(0, 0.05, n - 20), 100 * np.exp(np.cumsum(rng.normal(0, 0.05, 20)))]),
        }

    def test_explanation_reproduces_the_detector_on_varied_series(self):
        det = RegimeDetector()
        seen = set()
        for name, close in self._series().items():
            df = _ohlc(close)
            res = det.detect(df)
            ri = hi.regime_intelligence(df, res, det, now=NOW)
            seen.add(res.regime)
            with self.subTest(series=name, regime=res.regime):
                self.assertTrue(ri["available"])
                self.assertEqual(ri["regime"], res.regime)
                self.assertTrue(ri["consistent"], ri["rule_applied"])
                self.assertTrue(ri["history"]["matches_current"])
                self.assertEqual(sum(s["bars"] for s in ri["history"]["segments"]), hi.HISTORY_BARS)
                self.assertIn(ri["agreement"], ("HIGH", "MEDIUM", "LOW"))
        self.assertGreaterEqual(len(seen), 3)   # the series really exercise different branches

    def test_agreement_is_a_vote_count(self):
        df = _ohlc(self._series()["up"])
        res = RegimeDetector().detect(df)
        ri = hi.regime_intelligence(df, res, now=NOW)
        if res.regime.startswith("TRENDING"):
            self.assertEqual(res.confidence, "HIGH" if ri["trend_votes"] == 3 else "MEDIUM")

    def test_insufficient_data_is_not_shown_as_ranging(self):
        df = _ohlc(np.linspace(100, 101, 30))
        res = RegimeDetector().detect(df)
        self.assertEqual(res.regime, "RANGING")   # the detector's safe default
        ri = hi.regime_intelligence(df, res, now=NOW)
        self.assertFalse(ri["available"])
        self.assertIn("not enough data", ri["reason"])


class StrategyEvidenceTest(SimpleTestCase):
    def test_zero_trades_is_not_a_measured_zero(self):
        ev = hi.strategy_evidence({"total_trades": 0, "avg_r_multiple": 0, "expectancy": 0}, "AVAXUSDT")
        self.assertEqual(ev["state"], "NO_TRADES_GATED")   # no strategy in the registry is production-eligible today
        self.assertNotIn("expectancy_r", ev)

    def test_insufficient_and_measured(self):
        self.assertEqual(hi.strategy_evidence({"total_trades": 12}, "X")["state"], "INSUFFICIENT")
        ev = hi.strategy_evidence({"total_trades": 40, "avg_r_multiple": -0.2, "expectancy": -3.1, "profit_factor": 0.8,
                                   "win_rate": 41.0, "max_drawdown": 12.5}, "X")
        self.assertEqual(ev["state"], "MEASURED")
        self.assertEqual(ev["expectancy_r"], -0.2)
        self.assertIn("In-sample", ev["classification"])


@override_settings(HOME_UNIVERSE_SNAPSHOT_BACKGROUND=False)
class UniverseSnapshotCacheTest(SimpleTestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self._real_file = hi.SNAPSHOT_FILE
        self._tmp = tempfile.TemporaryDirectory()
        hi.SNAPSHOT_FILE = Path(self._tmp.name) / "snap.pkl"   # never read or overwrite the server's own file
        cache.delete(hi.SNAPSHOT_KEY)
        cache.delete(hi.SNAPSHOT_LOCK_KEY)

    def tearDown(self):
        hi.SNAPSHOT_FILE = self._real_file
        self._tmp.cleanup()
        cache.delete(hi.SNAPSHOT_KEY)
        cache.delete(hi.SNAPSHOT_LOCK_KEY)

    def test_saved_snapshot_survives_an_emptied_cache(self):
        snap = {"available": True, "computed_at": (NOW - pd.Timedelta(minutes=5)).isoformat(), "latest_candle": None,
                "counts": {"TRENDING_UP": 1, "TRENDING_DOWN": 1, "RANGING": 1, "HIGH_VOLATILITY": 1},
                "n_classified": 4, "n_insufficient": 0, "n_unknown": 0, "regimes": {}, "correlation": {"available": False}}
        hi._save_snapshot_file(snap)
        got = hi.get_universe_snapshot(NOW)          # cache is empty, as after a server restart
        self.assertTrue(got["available"])
        self.assertEqual(got["age_minutes"], 5)
        hi._save_snapshot_file(dict(snap, computed_at=(NOW - pd.Timedelta(hours=7)).isoformat()))
        cache.delete(hi.SNAPSHOT_KEY)
        self.assertFalse(hi.get_universe_snapshot(NOW)["available"])   # older than SNAPSHOT_KEEP_SECONDS → not used

    def test_missing_snapshot_is_reported_not_invented(self):
        snap = hi.get_universe_snapshot(NOW)
        self.assertFalse(snap["available"])
        self.assertFalse(snap["refreshing"])

    def test_cached_snapshot_distribution_and_age(self):
        cache.set(hi.SNAPSHOT_KEY, {
            "available": True, "computed_at": (NOW - pd.Timedelta(minutes=20)).isoformat(), "latest_candle": None,
            "counts": {"TRENDING_UP": 2, "TRENDING_DOWN": 5, "RANGING": 2, "HIGH_VOLATILITY": 1},
            "n_classified": 10, "n_insufficient": 1, "n_unknown": 1, "regimes": {}, "correlation": {"available": False}})
        snap = hi.get_universe_snapshot(NOW)
        self.assertEqual(snap["age_minutes"], 20)
        self.assertEqual([d["n"] for d in snap["distribution"]], [2, 5, 2, 1, 2])
        self.assertAlmostEqual(sum(d["pct"] for d in snap["distribution"]), 100.0)


def _intel():
    """A minimal real-shaped context, built through the module's own functions."""
    listing = {"crypto": [{"symbol": f"C{i}_USDT", "available": True, "display_symbol": f"C{i}/USDT"} for i in range(6)],
               "forex": []}
    live = {"crypto": {f"C{i}_USDT": {"change_pct": c, "as_of": NOW.isoformat()} for i, c in enumerate([4, 2, 1, 0, -2, -9])},
            "forex": {}, "forex_session": {"is_open": False}, "status": {"crypto": "LIVE", "forex": "closed"},
            "fetched_at": NOW.isoformat()}
    crypto = hi.crypto_pulse(live, listing, NOW)
    snap = {"available": False, "refreshing": False, "reason": "not computed yet"}
    hi.annotate_regimes(crypto, snap)
    df = _ohlc(100 * np.exp(np.cumsum(np.random.default_rng(5).normal(0.002, 0.004, 400))))
    res = RegimeDetector().detect(df)
    return {"now": NOW, "crypto": crypto, "forex": hi.forex_pulse(live, listing, NOW), "snapshot": snap,
            "regime": hi.regime_intelligence(df, res, now=NOW),
            "evidence": hi.strategy_evidence({"total_trades": 0}, "AVAXUSDT"), "integrity": hi.research_integrity(None),
            "watchlist": hi.research_watchlist(crypto, snap), "extreme_z": hi.EXTREME_Z,
            "adverse_move_pct": hi.ADVERSE_MOVE_PCT, "min_assets": 4, "atr_extreme": 1.7}


class HomeTemplateTest(TestCase):
    def _render(self, user, show_private):
        request = RequestFactory().get("/")
        request.user = user
        ctx = {"symbol": "AVAXUSDT", "intel": _intel(), "show_private": show_private, "stale_notice": None,
               "research_state": {"features_valid": 14, "features_evaluated": 190, "feature_files": 78,
                                  "governance_entries": 8, "pipeline_health": "fresh", "latest_run": None, "age_days": None},
               "system_health": {"overall": "ok"},
               "execution_state": {"open_count": 3, "realized_pnl": 12.5, "total_closed": 9} if show_private else None}
        return render_to_string("dashboard.html", ctx, request=request)

    def test_anonymous_visitor_sees_no_platform_journal(self):
        html = self._render(AnonymousUser(), False)
        self.assertIn("Portfolio risk unavailable — no authorized portfolio data.", html)
        self.assertNotIn("Platform journal", html)

    def test_staff_sees_the_platform_journal(self):
        staff = get_user_model().objects.create_user("staff_home", password="x", is_staff=True)
        html = self._render(staff, True)
        self.assertIn("Platform journal (staff only)", html)

    def test_no_indicator_panels_and_honest_empty_states(self):
        html = self._render(AnonymousUser(), False)
        for gone in ("BB Width", "EMA Spread</div>", "Legacy Feature IC", "Strategy Expectancy (R)", "Cross-Sect. Dispersion"):
            self.assertNotIn(gone, html)
        for present in ("Quantitative Market Intelligence", "Cross-Sectional Relative Value", "Regime Intelligence",
                        "Risk Conditions", "Research &amp; Evidence", "Research Watchlist",
                        "not a probability", "the production gate blocks every strategy",
                        "Research candidates are not trade recommendations", "U.S. equities and ETFs: no data source"):
            self.assertIn(present, html)
        self.assertIn("MARKET CLOSED", html)     # forex shown as closed, not live
        self.assertIn("UNAVAILABLE", html)       # universe snapshot missing → said so
