# claude code changed: new file — 2026-10-04, asset-agnostic research infrastructure (bot/quant_core/market_profile.py).
# Proves: crypto (REVB-4) is unchanged; FX scheduled closures are not crypto-style missing candles (decision lock
# Decision 5 A) while missing TRADING hours still are (5 B); the session seed is causal; FX can never become
# canonical while Decision 5's owner rules are undecided (FX_CALENDAR_UNAVAILABLE, not negative evidence);
# financing/cost/universe/symbol rules cannot leak between asset classes; the holdout seal applies to every class.
# The SessionCalendar used here is a TEST FIXTURE (Fri 22:00 → Sun 22:00 UTC) — not an FX calendar decision.
import json   # claude code changed: 2026-10-06
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
from bot.tests.synthetic_data import cointegrated_pair   # claude code changed: 2026-10-04 — shared generator (bot/tests/synthetic_data.py), identical draws
from django.test import SimpleTestCase

from bot.quant_core.kalman import CanonicalKalman
from bot.quant_core.market_profile import (CRYPTO_PROFILE, FOREX_PROFILE, FX_CALENDAR_UNAVAILABLE, AssetClassMismatch,
                                           ContinuousCalendar, OwnerDecisionMissing, SessionCalendar,
                                           asset_class_for_symbol, max_missing_trading_run, profile_for_pair,
                                           weekly_closure_intervals)
from bot.quant_core.protocol import CANONICAL
from bot.quant_core.research_zones import ZONES, HoldoutSealedError
from bot.quant_core.seed import causal_init

H = pd.Timedelta(hours=1)
REVB4_PROTOCOL_HASH = "3f0da81fdd35ca80c454012a1ea145977fe4175ccf4e939da138ce6785f884ca"   # recorded before this change
FIXTURE_START = pd.Timestamp("2021-08-01", tz="UTC")
FIXTURE_END = pd.Timestamp("2024-05-31 23:00", tz="UTC")
TEST_CAL = SessionCalendar(weekly_closure_intervals(FIXTURE_START, FIXTURE_END, close=(4, 22), reopen=(6, 22)),
                           source="TEST FIXTURE — Fri 22:00 → Sun 22:00 UTC; not an FX calendar decision")


def _coint(idx, seed=3):   # FX-like price level (~1.1) and volatility
    return cointegrated_pair(idx, seed, beta=0.8, alpha=0.05, rw_sd=0.002, spread_sd=0.0008, start=0.1)


def _fx_like(seed=3):
    """Hourly data on the TEST calendar's trading grid only (weekend closures absent, as in real MT5 files)."""
    return _coint(TEST_CAL.trading_grid(FIXTURE_START, FIXTURE_END), seed)


def _fx_real(seed=3):   # claude code changed: 2026-10-06 — data on the REAL FX calendar (weekly NY close + holidays)
    return _coint(FOREX_PROFILE.calendar.trading_grid(FIXTURE_START, FIXTURE_END), seed)


def _crypto_like(seed=3, end="2024-06-30 23:00"):
    return _coint(pd.date_range(ZONES.init_window()[0], pd.Timestamp(end, tz="UTC"), freq="h"), seed)


class CryptoProfileUnchangedTests(SimpleTestCase):
    def test_crypto_profile_describes_revb4_exactly(self):
        self.assertEqual(CANONICAL.config_hash(), REVB4_PROTOCOL_HASH)          # the refactor did not touch the protocol
        self.assertEqual(CRYPTO_PROFILE.protocol_hash, REVB4_PROTOCOL_HASH)
        self.assertEqual(CRYPTO_PROFILE.gap_max_candles, CANONICAL.gap_max_candles)
        self.assertTrue(CRYPTO_PROFILE.calendar.is_continuous)
        self.assertEqual(CRYPTO_PROFILE.missing_decisions(), [])
        self.assertIs(CRYPTO_PROFILE.require_canonical("test"), CRYPTO_PROFILE)

    def test_continuous_calendar_reproduces_the_crypto_grid_seed_and_gap_rule(self):
        from bot.research.cointegration_engine import _zone_s_slice
        from bot.research.kalman_filter_engine import KalmanFilterEngine
        a, b = _crypto_like()
        k = a.index.get_loc(ZONES.S0)
        a = a.drop(a.index[k + 3000:k + 3002])                                     # a short gap the grid must keep as NaN rows
        eng = KalmanFilterEngine(pair_name="X/Y", symbol_a="X_USDT", symbol_b="Y_USDT", ols_beta=1.0, ols_alpha=0.0)
        base = eng._align_prices(a, b)
        cont = eng._align_prices(a, b, calendar=ContinuousCalendar())
        pd.testing.assert_frame_equal(base, cont)
        la, lb = eng._to_log_prices(base)
        s0, s1 = causal_init(la, lb), causal_init(la, lb, calendar=ContinuousCalendar())
        self.assertEqual((s0.seed_timestamp, s0.init_window_start), (s1.seed_timestamp, s1.init_window_start))
        np.testing.assert_array_equal(s0.theta0, s1.theta0)
        self.assertEqual(s0.seed_timestamp, ZONES.S0)                              # 24/7: first trading candle ≥ S IS S
        x0, r0 = _zone_s_slice(np.log(a), np.log(b))
        x1, r1 = _zone_s_slice(np.log(a), np.log(b), calendar=ContinuousCalendar())
        self.assertEqual(r0, r1)
        pd.testing.assert_frame_equal(x0, x1)


class SessionCalendarMechanismTests(SimpleTestCase):
    def test_refuses_unnamed_overlapping_or_off_hour_closures(self):
        t = pd.Timestamp("2024-01-05 22:00", tz="UTC")
        with self.assertRaisesRegex(ValueError, "named source"):
            SessionCalendar([(t, t + 48 * H)], source="")
        with self.assertRaisesRegex(ValueError, "overlap"):
            SessionCalendar([(t, t + 48 * H), (t + 10 * H, t + 60 * H)], source="x")
        with self.assertRaisesRegex(ValueError, "hour boundaries"):
            SessionCalendar([(t + pd.Timedelta(minutes=30), t + 48 * H)], source="x")

    def test_weekend_is_not_a_missing_run_but_a_midweek_hole_is(self):
        fri, mon = pd.Timestamp("2024-01-05 12:00", tz="UTC"), pd.Timestamp("2024-01-10 12:00", tz="UTC")
        grid = TEST_CAL.trading_grid(fri, mon)
        self.assertFalse(any(pd.Timestamp("2024-01-06 05:00", tz="UTC") == g for g in grid))   # Saturday excluded
        self.assertEqual(len(pd.date_range(fri, mon, freq="h")) - len(grid), 48)
        self.assertEqual(max_missing_trading_run(grid, TEST_CAL, fri, mon), 0)
        hole = grid.drop(grid[(grid >= pd.Timestamp("2024-01-09 03:00", tz="UTC")) & (grid < pd.Timestamp("2024-01-09 08:00", tz="UTC"))])
        self.assertEqual(max_missing_trading_run(hole, TEST_CAL, fri, mon), 5)


class FxClosureIsNotMissingDataTests(SimpleTestCase):
    """Decision 5 A/B on the real code paths (selection gap rule, grid alignment, the shared canonical Kalman)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.a, cls.b = _fx_like()

    def test_zone_s_gap_rule(self):
        from bot.research.cointegration_engine import _zone_s_slice
        la, lb = np.log(self.a), np.log(self.b)
        s, why = _zone_s_slice(la, lb)                                             # crypto rule: every weekend is a > 3 gap
        self.assertIsNone(s)
        self.assertIn("exceeds the 3-candle gap policy", why)
        s, why = _zone_s_slice(la, lb, calendar=TEST_CAL, gap_max_candles=3)       # closures excluded from the grid
        self.assertEqual(why, "")
        self.assertGreater(len(s), 10000)
        wed = pd.Timestamp("2023-03-08 03:00", tz="UTC")
        la2 = la.drop(la.index[(la.index >= wed) & (la.index < wed + 5 * H)])      # 5 missing TRADING hours
        s, why = _zone_s_slice(la2, lb, calendar=TEST_CAL, gap_max_candles=3)
        self.assertIsNone(s)
        self.assertIn("5 consecutive missing trading candles", why)
        with self.assertRaisesRegex(ValueError, "owner value"):
            _zone_s_slice(la, lb, calendar=TEST_CAL)                               # FX g_max has no default

    def test_kalman_never_reseeds_on_closures_and_warms_up_in_trading_candles(self):
        from bot.research.kalman_filter_engine import KalmanFilterEngine
        eng = KalmanFilterEngine(pair_name="X/Y", symbol_a="X", symbol_b="Y", ols_beta=1.0, ols_alpha=0.0)
        for cal, expect_inert in ((None, True), (TEST_CAL, False)):
            al = eng._align_prices(self.a, self.b, calendar=cal)
            la, lb = eng._to_log_prices(al)
            kf = CanonicalKalman(np.array([0.8, 0.05]), CANONICAL)
            states = [kf.step(x, y) for x, y in zip(la.to_numpy(float), lb.to_numpy(float))]
            reseeds = sum(s.reseeded for s in states)
            signal_ok = sum((not s.in_warmup) and s.state_valid for s in states)
            if expect_inert:   # the 24/7 grid inserts 48 NaN hours each weekend → re-seed weekly, never leaves warmup
                self.assertGreater(reseeds, 100)
                self.assertEqual(signal_ok, 0)
            else:              # trading grid: no closure row exists, so no step, no Q, no warmup count, no re-seed
                self.assertFalse(al.isna().any().any())
                self.assertEqual(int(TEST_CAL.is_trading(al.index).sum()), len(al))
                self.assertEqual(reseeds, 0)
                self.assertTrue(states[CANONICAL.warmup_candles - 1].in_warmup)
                self.assertFalse(states[CANONICAL.warmup_candles].in_warmup)        # warmup = 168 TRADING candles
                self.assertGreater(signal_ok, len(states) - 200)

    def test_observation_inside_a_scheduled_closure_is_refused(self):
        from bot.research.kalman_filter_engine import KalmanFilterEngine
        sat = pd.Timestamp("2023-03-11 10:00", tz="UTC")
        a = pd.concat([self.a, pd.Series([1.0], index=[sat])]).sort_index()
        eng = KalmanFilterEngine(pair_name="X/Y", symbol_a="X", symbol_b="Y", ols_beta=1.0, ols_alpha=0.0)
        with self.assertRaisesRegex(ValueError, "inside scheduled closures"):
            eng._align_prices(a, self.b, calendar=TEST_CAL)


class SessionSeedIsCausalTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        a, b = _fx_like()
        cls.la, cls.lb = np.log(a), np.log(b)

    def test_seed_is_first_trading_candle_at_or_after_s_and_ignores_the_future(self):
        self.assertFalse(TEST_CAL.is_trading(pd.DatetimeIndex([ZONES.S0]))[0])   # S0 (Sunday 00:00) is a closure hour
        init = causal_init(self.la, self.lb, calendar=TEST_CAL)
        self.assertEqual(init.seed_timestamp, pd.Timestamp("2021-11-07 22:00", tz="UTC"))
        self.assertLess(init.init_window_end, init.seed_timestamp)
        self.assertEqual(len(TEST_CAL.trading_grid(init.init_window_start, init.init_window_end)),
                         CANONICAL.init_window_candles)                             # exactly L TRADING candles
        fut = self.la.copy()
        fut[fut.index >= init.seed_timestamp] += 5.0                               # change every price at/after the seed
        np.testing.assert_array_equal(causal_init(fut, self.lb, calendar=TEST_CAL).theta0, init.theta0)

    def test_refusals(self):
        seed = TEST_CAL.first_trading_at_or_after(ZONES.S0)
        nan_seed = self.la.copy()
        nan_seed[seed] = np.nan
        with self.assertRaisesRegex(ValueError, "not observed on both legs"):
            causal_init(nan_seed, self.lb, calendar=TEST_CAL)
        k = self.la.index.get_loc(seed)
        holed_a, holed_b = self.la.drop(self.la.index[k - 50]), self.lb.drop(self.lb.index[k - 50])
        with self.assertRaisesRegex(ValueError, "complete trading grid"):
            causal_init(holed_a, holed_b, calendar=TEST_CAL)                      # an absent trading hour is not NaN
        short_a, short_b = self.la.iloc[k - 900:], self.lb.iloc[k - 900:]
        with self.assertRaisesRegex(ValueError, "needs L = 1000"):
            causal_init(short_a, short_b, calendar=TEST_CAL)
        with self.assertRaisesRegex(ValueError, "development fallback"):
            causal_init(self.la, self.lb, CANONICAL.with_overrides(selection_start_utc=None), calendar=TEST_CAL)


class ForexCannotBeCanonicalTests(SimpleTestCase):
    """Every CANONICAL consumer refuses FX with FX_CALENDAR_UNAVAILABLE (not negative evidence). claude code changed:
    2026-10-06 — the owner-delegated selection rules (calendar, gap limit, seed rule, universe) now allow the
    exploratory zone-S SELECTION screen for in-universe FX pairs; Kalman state, lifecycle and evidence stay blocked."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.a, cls.b = _fx_real()   # claude code changed: 2026-10-06 — real FX calendar (was the TEST calendar)

    def test_profile_and_symbol_resolution(self):
        # claude code changed: 2026-10-06 — calendar (OD-FX-1) and the delegated selection rules (OD-FX-2/3/7) are
        # decided; horizon, financing, costs and protocol are not, so FX stays FX_CALENDAR_UNAVAILABLE for canonical use.
        self.assertEqual(set(FOREX_PROFILE.missing_decisions()), set(FOREX_PROFILE.DECISION_FIELDS) - set(FOREX_PROFILE.SELECTION_FIELDS))
        self.assertIs(FOREX_PROFILE.require_selection("test"), FOREX_PROFILE)
        with self.assertRaisesRegex(OwnerDecisionMissing, FX_CALENDAR_UNAVAILABLE):
            FOREX_PROFILE.require_canonical("test")
        for s, c in (("EUR/USD", "FOREX"), ("EUR_USD", "FOREX"), ("XAU/USD", "FOREX"), ("BTC/USDT", "CRYPTO"),
                     ("AVAX_USDT", "CRYPTO"), ("X_USDT", "CRYPTO"), ("X", "CRYPTO")):
            self.assertEqual(asset_class_for_symbol(s), c, s)
        with self.assertRaisesRegex(AssetClassMismatch, "mixes asset classes"):
            profile_for_pair("BTC/USDT", "EUR/USD")

    def test_selection_runs_in_universe_and_blocks_outside_it(self):   # claude code changed: 2026-10-06 — was "selection is blocked"
        from bot.research.cointegration_engine import pair_test_in_selection_zone, run_selection_zone_screen
        from bot.research_lab.verdict import compute_verdict_pairs
        r = pair_test_in_selection_zone("EUR/USD", "GBP/USD", self.a, self.b)
        self.assertEqual((r["status"], r["seed_eligible"]), ("OK", True))
        self.assertIn("FX_SELECTION_ONLY", r["selection_mode"])
        p = pair_test_in_selection_zone("EUR/USD", "USD/HKD", self.a, self.b)   # hard peg: outside the universe
        self.assertEqual(p["status"], "BLOCKED")
        self.assertIn("outside the FOREX universe", p["reject_reason"])
        self.assertIsNone(p["is_cointegrated"])                                  # unknown, not False
        self.assertEqual(compute_verdict_pairs(p).verdict, "REQUIRES_REVIEW")
        scr = run_selection_zone_screen({"EUR/USD": self.a, "GBP/USD": self.b, "USD/HKD": self.a * 1.1})
        self.assertEqual(scr["n_testable"], 1)                                   # only the in-universe pair enters the family
        self.assertEqual(sorted(x["status"] for x in scr["pairs"]), ["BLOCKED", "BLOCKED", "OK"])
        self.assertFalse(scr["claimable"])

    def test_fx_gap_rule_uses_the_trading_grid_and_the_delegated_limit(self):   # claude code changed: 2026-10-06
        from bot.research.cointegration_engine import pair_test_in_selection_zone
        wed = pd.Timestamp("2023-03-08 03:00", tz="UTC")
        for hole, expect in ((5, "OK"), (6, "UNTESTABLE")):                     # g_max = 5 missing TRADING hours
            a = self.a.drop(self.a.index[(self.a.index >= wed) & (self.a.index < wed + hole * H)])
            self.assertEqual(pair_test_in_selection_zone("EUR/USD", "GBP/USD", a, self.b)["status"], expect, hole)

    def test_kalman_pipeline_and_lab_refuse(self):
        from bot.research.cointegration_pipeline_runner import run_pair_canonical
        from bot.research.kalman_filter_engine import KalmanFilterEngine
        from bot.research_lab.tools import research_tools as rt
        with self.assertRaisesRegex(OwnerDecisionMissing, FX_CALENDAR_UNAVAILABLE):
            KalmanFilterEngine(pair_name="EUR_USD/GBP_USD", symbol_a="EUR_USD", symbol_b="GBP_USD",
                               ols_beta=1.0, ols_alpha=0.0).run_canonical(self.a, self.b)
        d = Path(tempfile.mkdtemp())
        try:
            r = run_pair_canonical("EUR_USD", "GBP_USD", self.a, self.b, output_dir=str(d), funding_dir=str(d))
            self.assertEqual((r["stopped_at"], r["stages"]["market"]["status"]), ("market", "BLOCKED"))
            self.assertNotIn("kalman", r["stages"])
        finally:
            shutil.rmtree(d, ignore_errors=True)
        frames = {"EUR/USD": pd.DataFrame({"close": self.a}), "GBP/USD": pd.DataFrame({"close": self.b})}
        with mock.patch.object(rt, "load_ohlcv", lambda s: frames[s]):
            k = rt.run_kalman_pairs_test("EUR/USD", "GBP/USD")
            c = rt.run_cointegration_test("EUR/USD", "GBP/USD")
        self.assertEqual(k["verdict"], "REQUIRES_REVIEW")
        self.assertIsNone(k["observation"])
        self.assertIn(FX_CALENDAR_UNAVAILABLE, k["reject_reason"])               # claude code changed: 2026-10-06 — canonical Kalman still blocked
        self.assertEqual(c["status"], "OK")                                       # claude code changed: 2026-10-06 — selection now allowed (exploratory)
        self.assertIn("FX_SELECTION_ONLY", c["selection_mode"])

    def test_holdout_seal_applies_to_fx_before_any_market_rule(self):
        from bot.research.cointegration_engine import pair_test_in_selection_zone
        from bot.research.cointegration_pipeline_runner import run_pair_canonical
        a = pd.concat([self.a, pd.Series([1.0], index=[ZONES.F + 22 * H])])
        with self.assertRaises(HoldoutSealedError):
            pair_test_in_selection_zone("EUR/USD", "GBP/USD", a, self.b)
        with self.assertRaises(HoldoutSealedError):
            run_pair_canonical("EUR_USD", "GBP_USD", a, self.b, output_dir=tempfile.gettempdir(), funding_dir=".")


class NoCrossAssetLeakTests(SimpleTestCase):
    def test_financing_costs_and_universe_stay_in_their_asset_class(self):
        from bot.quant_core.funding import load_funding_series
        from bot.research.entry_exit_engine import EntryExitEngine
        from bot.research.point_in_time_universe import Listing, is_member
        fund = pd.Series(0.0001, index=pd.date_range("2024-01-01", periods=10, freq="8h", tz="UTC"))
        with self.assertRaisesRegex(AssetClassMismatch, "perpetual funding"):
            load_funding_series("EUR/USD", ".")
        with self.assertRaisesRegex(AssetClassMismatch, "perpetual funding"):     # LEGACY too: perp funding never finances FX
            EntryExitEngine(pair_name="EUR_USD/GBP_USD", symbol_a="EUR_USD", symbol_b="GBP_USD", output_dir=tempfile.gettempdir(),
                            funding_rates_a=fund, funding_rates_b=fund)
        with self.assertRaisesRegex(AssetClassMismatch, "symbols are"):            # crypto default cannot price an FX pair
            EntryExitEngine(pair_name="EUR_USD/GBP_USD", symbol_a="EUR_USD", symbol_b="GBP_USD",
                            output_dir=tempfile.gettempdir(), rule_set="CANONICAL")
        with self.assertRaisesRegex(OwnerDecisionMissing, FX_CALENDAR_UNAVAILABLE):
            EntryExitEngine(pair_name="EUR_USD/GBP_USD", symbol_a="EUR_USD", symbol_b="GBP_USD",
                            output_dir=tempfile.gettempdir(), rule_set="CANONICAL", asset_class="FOREX")
        with self.assertRaisesRegex(AssetClassMismatch, "CRYPTO universe rule"):
            is_member(Listing("EUR/USD", pd.Timestamp("2020-01-01", tz="UTC"), None), "2024-01-01", 1e9, min_volume_usdt=0)

    def test_session_data_under_a_crypto_name_is_refused(self):
        from bot.research.cointegration_engine import pair_test_in_selection_zone
        from bot.research.kalman_filter_engine import KalmanFilterEngine
        a, b = _fx_like()
        r = pair_test_in_selection_zone("X_USDT", "Y_USDT", a, b)
        self.assertEqual(r["status"], "BLOCKED")
        self.assertIn("UTC Saturdays", r["reject_reason"])
        with self.assertRaisesRegex(AssetClassMismatch, "UTC Saturdays"):
            KalmanFilterEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT",
                               ols_beta=1.0, ols_alpha=0.0).run_canonical(a, b)

    def test_a_crypto_outage_is_not_mistaken_for_a_session_market(self):
        from bot.research.cointegration_engine import pair_test_in_selection_zone
        a, b = _crypto_like()
        k = a.index.get_loc(ZONES.S0) + 4000
        a, b = a.drop(a.index[k:k + 72]), b.drop(b.index[k:k + 72])               # a 3-day exchange outage
        r = pair_test_in_selection_zone("X_USDT", "Y_USDT", a, b)
        self.assertEqual(r["status"], "UNTESTABLE")                               # the crypto gap rule, unchanged
        self.assertIn("gap policy", r["reject_reason"])


# claude code changed: 2026-10-06 — data clock fix (owner option A) and the owner's FX calendar (OD-FX-1).
class Mt5ServerClockTests(SimpleTestCase):
    def test_server_time_converts_to_the_real_market_open_and_close(self):
        from bot.mt5_data_fetcher import mt5_server_time_to_utc
        cases = {"2026-01-05 00:00": "2026-01-04 22:00",   # winter: Monday-open bar = Sunday 22:00 UTC
                 "2026-01-09 23:00": "2026-01-09 21:00",   # winter: last Friday bar (closes 22:00 UTC)
                 "2026-07-06 00:00": "2026-07-05 21:00",   # summer (US DST): Sunday 21:00 UTC
                 "2026-07-10 23:00": "2026-07-10 20:00"}   # summer: last Friday bar (closes 21:00 UTC)
        got = mt5_server_time_to_utc(pd.Series([pd.Timestamp(k, tz="UTC") for k in cases]))   # stored form: server digits labelled UTC
        self.assertEqual([str(x) for x in got], [str(pd.Timestamp(v, tz="UTC")) for v in cases.values()])
        epoch = int(pd.Timestamp("2026-01-05 00:00").timestamp())
        self.assertEqual(mt5_server_time_to_utc(pd.Series([epoch])).iloc[0], pd.Timestamp("2026-01-04 22:00", tz="UTC"))
        with self.assertRaises(Exception):                                     # a bar at a non-existent NY time = wrong clock
            mt5_server_time_to_utc(pd.Series([pd.Timestamp("2026-03-08 09:30")]))

    def test_fx_calendar_follows_new_york_daylight_saving(self):
        cal = FOREX_PROFILE.calendar
        self.assertIn("America/New_York", cal.source)
        win_close, sum_close = pd.Timestamp("2026-01-09 22:00", tz="UTC"), pd.Timestamp("2026-07-10 21:00", tz="UTC")
        hours = pd.DatetimeIndex([win_close - H, win_close, pd.Timestamp("2026-01-11 21:00", tz="UTC"), pd.Timestamp("2026-01-11 22:00", tz="UTC"),
                                  sum_close - H, sum_close, pd.Timestamp("2026-07-12 20:00", tz="UTC"), pd.Timestamp("2026-07-12 21:00", tz="UTC")])
        self.assertEqual(cal.is_trading(hours).tolist(), [True, False, False, True, True, False, False, True])
        # claude code changed: 2026-10-06 — measured Christmas/New Year closures are closures; the 2021-12-02 broker
        # outage is NOT (it must stay missing data, never be relabelled as a holiday)
        probe = pd.DatetimeIndex(["2023-12-25 12:00", "2024-01-01 12:00", "2021-12-24 20:00", "2021-12-02 10:00", "2023-12-27 12:00"], tz="UTC")
        self.assertEqual(cal.is_trading(probe).tolist(), [False, False, False, True, True])

    def test_one_off_conversion_archives_converts_and_never_runs_twice(self):
        from bot.mt5_data_fetcher import convert_server_clock_files
        d = Path(tempfile.mkdtemp())
        try:
            idx = FOREX_PROFILE.calendar.trading_grid("2026-01-05", "2026-01-23 23:00")
            server = (idx.tz_convert("America/New_York").tz_localize(None) + pd.Timedelta(hours=7)).tz_localize("UTC")
            pd.DataFrame({"timestamp": server, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}).to_csv(d / "EUR_USD_1h.csv", index=False)
            pd.DataFrame({"timestamp": idx, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}).to_csv(d / "GBP_USD_1h.csv", index=False)   # already UTC
            rep = {r["file"]: r for r in convert_server_clock_files(d, d / "archive")}
            self.assertTrue(rep["EUR_USD_1h.csv"]["converted"])
            self.assertFalse(rep["GBP_USD_1h.csv"]["converted"])               # no server-time signature: untouched
            back = pd.to_datetime(pd.read_csv(d / "EUR_USD_1h.csv")["timestamp"], utc=True)
            self.assertTrue(back.equals(pd.Series(idx)))                       # exactly the true UTC trading grid
            self.assertTrue((d / "archive" / "EUR_USD_1h.csv").exists())       # original kept
            self.assertIn("original marker missing", json.loads((d / "EUR_USD_1h.provenance.json").read_text())["clock"])
            with self.assertRaises(FileExistsError):                           # never converts (or archives) twice
                convert_server_clock_files(d, d / "archive")
        finally:
            shutil.rmtree(d, ignore_errors=True)
