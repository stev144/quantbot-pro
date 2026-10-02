# claude code changed: new file — Post-audit Phase 2 (DCA B1–B8), per
# DCA_RESEARCH_PROTOCOL_2026-10-02.md. Five test levels: software, known-answer
# mathematics, synthetic statistics, synthetic market scenarios, research
# integrity (look-ahead / leakage / censoring). Synthetic OHLC only — no
# network, no real data files. Expected values are hand arithmetic.

import math
from dataclasses import replace

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research import dca_engine as de
from bot.research.dca_engine import (
    BASELINE_LEVELS, DCAConfig, DCALevel, simulate_dca_cycles, simulate_dca_path, summarize_dca_cycles,
    summarize_dca_tail_risk,
)

Z = dict(fee_rate=0.0, slippage_rate=0.0)   # isolates the mechanics


def _ohlc(rows, start="2024-01-01", freq="1h", index=None):
    idx = index if index is not None else pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def _flat(p, n):
    return [(p, p, p, p)] * n


def _random_ohlc(n, seed, drift=0.0, vol=0.01):
    rng = np.random.default_rng(seed)
    c = 100.0 * np.exp(np.cumsum(drift + vol * rng.standard_normal(n)))
    o = np.concatenate([[100.0], c[:-1]]) * np.exp(0.002 * rng.standard_normal(n))
    hi = np.maximum(o, c) * (1 + np.abs(0.004 * rng.standard_normal(n)))
    lo = np.minimum(o, c) * (1 - np.abs(0.004 * rng.standard_normal(n)))
    return _ohlc(list(zip(o, hi, lo, c)))


# ── B2 canonical clock ─────────────────────────────────────────────────────────
class CanonicalClockTests(SimpleTestCase):
    def test_entry_is_decided_at_close_and_filled_at_next_open(self):
        cfg = DCAConfig(initial_usdt=6.0, levels=(), **Z)
        df = _ohlc([(100, 100, 100, 100), (101, 101, 101, 101)] + _flat(101, 3))
        t = simulate_dca_cycles(df, cfg)[0]
        self.assertEqual(t["entry_time"], df.index[1])
        self.assertAlmostEqual(t["cycle_fills"][0]["price"], 101.0)

    def test_dca_level_confirmed_at_close_executes_at_next_open(self):
        cfg = DCAConfig(initial_usdt=6.0, levels=(DCALevel(0.05, 2.0),), profit_activation_pct=1.0, **Z)
        # entry fills at open[1]=100; close[2]=94 (−6 % ≥ 5 %) → level fills at open[3]=93
        df = _ohlc([(100,) * 4, (100,) * 4, (99, 99, 94, 94), (93, 93, 93, 93), (93,) * 4])
        fills = simulate_dca_cycles(df, cfg)[0]["cycle_fills"]
        self.assertEqual([f["level"] for f in fills], [0, 1])
        self.assertEqual(fills[1]["timestamp"], df.index[3])
        self.assertAlmostEqual(fills[1]["price"], 93.0)

    def test_exit_and_next_entry_never_share_an_instant(self):
        cfg = DCAConfig(initial_usdt=6.0, levels=BASELINE_LEVELS)
        trades = simulate_dca_cycles(_random_ohlc(3000, seed=4, vol=0.02), cfg)
        self.assertGreater(len(trades), 3)
        for a, b in zip(trades, trades[1:]):
            self.assertGreater(b["entry_time"], a["exit_time"])

    def test_no_look_ahead_future_candles_cannot_change_the_past(self):
        cfg = DCAConfig(initial_usdt=6.0, levels=BASELINE_LEVELS)
        df = _random_ohlc(600, seed=11, vol=0.02)
        k = 300
        alt = df.copy()
        alt.iloc[k:] = alt.iloc[k:].to_numpy() * 0.5          # the future changes completely
        _, e1 = simulate_dca_path(df, cfg)
        _, e2 = simulate_dca_path(alt, cfg)
        pd.testing.assert_frame_equal(e1.iloc[:k], e2.iloc[:k])   # every close before k: identical state

    def test_missing_ohlc_is_refused_never_invented(self):
        df = pd.DataFrame({"close": [1.0, 2.0]}, index=pd.date_range("2024", periods=2, freq="h", tz="UTC"))
        with self.assertRaisesRegex(ValueError, "missing"):
            simulate_dca_cycles(df, DCAConfig(levels=()))

    def test_unsorted_or_duplicate_index_is_refused(self):
        df = _ohlc(_flat(100, 3))
        with self.assertRaisesRegex(ValueError, "sorted"):
            simulate_dca_cycles(df.iloc[::-1], DCAConfig(levels=()))
        with self.assertRaisesRegex(ValueError, "sorted"):
            simulate_dca_cycles(pd.concat([df, df.iloc[[0]]]), DCAConfig(levels=()))


# ── B1 crash candles: the exposure bracket ────────────────────────────────────
class CrashFillModelTests(SimpleTestCase):
    CRASH = [(100,) * 4, (100,) * 4, (100, 100, 60, 61), (61,) * 4, (61,) * 4]

    def test_intrabar_ladder_fills_every_crossed_trigger_at_its_price(self):
        cfg = DCAConfig(initial_usdt=6.0, levels=BASELINE_LEVELS, profit_activation_pct=1.0,
                        fill_model="INTRABAR_LADDER", **Z)
        fills = simulate_dca_cycles(_ohlc(self.CRASH), cfg)[0]["cycle_fills"]
        # triggers from the 100 entry: 95, 88.35, 79.515, 67.58775 — next 57.4496 is below the 60 low
        self.assertEqual([f["level"] for f in fills], [0, 1, 2, 3, 4])
        for f, exp in zip(fills[1:], (95.0, 88.35, 79.515, 67.58775)):
            self.assertAlmostEqual(f["price"], exp, places=6)
        self.assertAlmostEqual(sum(f["usdt_amount"] for f in fills), 6 + 12 + 24 + 48 + 96)

    def test_next_open_model_fills_one_level_per_decision(self):
        cfg = DCAConfig(initial_usdt=6.0, levels=BASELINE_LEVELS, profit_activation_pct=1.0, **Z)
        fills = simulate_dca_cycles(_ohlc(self.CRASH), cfg)[0]["cycle_fills"]
        # entry at open[1]=100; close[2]=61 (−39 %) confirms ONE level → L1 at open[3]=61; L2 needs a further
        # −7 % from 61, never reached → [0, 1] (the intrabar model above deploys 4 levels on the same candle)
        self.assertEqual([f["level"] for f in fills], [0, 1])
        self.assertAlmostEqual(fills[1]["price"], 61.0)

    def test_models_bracket_exposure_on_random_crashes(self):
        df = _random_ohlc(4000, seed=5, vol=0.03)
        base = DCAConfig(initial_usdt=6.0, levels=BASELINE_LEVELS)
        _, e_no = simulate_dca_path(df, base)
        _, e_ib = simulate_dca_path(df, replace(base, fill_model="INTRABAR_LADDER"))
        self.assertGreaterEqual(e_ib["capital_deployed"].max(), e_no["capital_deployed"].max())

    def test_gap_through_trigger_fills_at_the_open(self):
        cfg = DCAConfig(initial_usdt=6.0, levels=(DCALevel(0.05, 2.0),), profit_activation_pct=1.0,
                        fill_model="INTRABAR_LADDER", **Z)
        df = _ohlc([(100,) * 4, (100,) * 4, (90, 91, 89, 90), (90,) * 4])   # opens at 90, below the 95 trigger
        self.assertAlmostEqual(simulate_dca_cycles(df, cfg)[0]["cycle_fills"][1]["price"], 90.0)

    def test_short_intrabar_uses_the_high(self):
        cfg = DCAConfig(initial_usdt=6.0, direction="SHORT", levels=(DCALevel(0.05, 2.0),), profit_activation_pct=1.0,
                        fill_model="INTRABAR_LADDER", **Z)
        df = _ohlc([(100,) * 4, (100,) * 4, (100, 106, 99, 100), (100,) * 4])
        self.assertAlmostEqual(simulate_dca_cycles(df, cfg)[0]["cycle_fills"][1]["price"], 105.0)


# ── B3 tail risk & censoring ──────────────────────────────────────────────────
class TailRiskAndCensoringTests(SimpleTestCase):
    def test_censored_cycle_is_labelled_and_kept_out_of_realised(self):
        cfg = DCAConfig(initial_usdt=6.0, levels=(), profit_activation_pct=0.5, **Z)
        df = _ohlc([(100,) * 4, (100,) * 4, (90,) * 4, (80,) * 4])
        trades = simulate_dca_cycles(df, cfg)
        self.assertTrue(trades[-1]["censored"])
        s = summarize_dca_cycles(trades, cfg)
        self.assertEqual(s["total_realised_net_profit"], 0.0)
        self.assertAlmostEqual(s["censored_mtm_pnl"], (80 - 100) * 0.06, places=4)   # 6 USDT / 100 = 0.06 units
        self.assertAlmostEqual(s["total_mark_to_market_pnl"], s["total_realised_net_profit"] + s["censored_mtm_pnl"], places=6)

    def test_unrealised_drawdown_known_answer(self):
        cfg = DCAConfig(initial_usdt=6.0, levels=(), profit_activation_pct=0.10, trailing_pct=0.01, **Z)
        rows = [(100,) * 4, (100,) * 4, (80,) * 4, (90,) * 4, (115,) * 4, (113,) * 4, (113,) * 4, (113,) * 4]
        trades, eq = simulate_dca_path(_ohlc(rows), cfg)
        r = summarize_dca_tail_risk(trades, eq, cfg)
        self.assertAlmostEqual(r["max_unrealised_loss"], -1.2, places=6)      # 0.06 × (80 − 100)
        self.assertAlmostEqual(r["worst_open_cycle_loss"], -1.2, places=6)
        self.assertAlmostEqual(r["max_mtm_drawdown_usd"], 1.2, places=6)
        self.assertEqual(trades[0]["cycle_exit_reason"], "PROFIT_TARGET_TRAILING")
        self.assertAlmostEqual(r["longest_underwater_hours"], 2.0)             # closes at 80, 90 below the 0 peak; 115 recovers

    def test_relentless_crash_exhausts_the_configured_ladder(self):
        rows = [(100 * 0.9 ** k,) * 4 for k in range(60)]
        cfg = DCAConfig(initial_usdt=6.0, levels=BASELINE_LEVELS)
        trades, eq = simulate_dca_path(_ohlc(rows), cfg)
        r = summarize_dca_tail_risk(trades, eq, cfg)
        self.assertEqual(r["max_ladder_depth"], 6)
        self.assertEqual(r["ladder_full_exhaustion_count"], 1)
        self.assertTrue(r["underwater_unrecovered_at_data_end"])
        self.assertEqual(len(r["censored_cycles"]), 1)


# ── B4 nulls ──────────────────────────────────────────────────────────────────
class NullModelTests(SimpleTestCase):
    def test_block_shuffle_preserves_endpoints_and_candle_shapes(self):
        df = _random_ohlc(500, seed=2, drift=0.001)
        syn = de._synthetic_path(df, 24, np.random.default_rng(0), de.NULL_BLOCK_SHUFFLE)
        self.assertAlmostEqual(syn["close"].iloc[-1], df["close"].iloc[-1], places=6)   # the drift is kept EXACTLY
        shape = lambda d: np.sort(np.round(np.log(d["high"].to_numpy()[1:] / d["close"].to_numpy()[:-1]) -
                                           np.log(d["close"].to_numpy()[1:] / d["close"].to_numpy()[:-1]), 10))
        np.testing.assert_allclose(shape(syn), shape(df), atol=1e-9)   # intrabar shapes are a permutation of the real ones

    def test_drift_removed_null_returns_to_the_start_price(self):
        df = _random_ohlc(500, seed=3, drift=0.002)
        syn = de._synthetic_path(df, 24, np.random.default_rng(0), de.NULL_DRIFT_REMOVED)
        self.assertAlmostEqual(syn["close"].iloc[-1], df["close"].iloc[0], places=6)

    def test_low_permutation_count_cannot_support_a_claim(self):
        df = _random_ohlc(300, seed=8)
        r = de.run_dca_permutation_test(df, DCAConfig(levels=BASELINE_LEVELS), n_permutations=19)
        self.assertEqual(r["raw_assessment"], "INSUFFICIENT_RESOLUTION")
        self.assertAlmostEqual(r["p_value_resolution"], 0.05)
        self.assertGreaterEqual(r["p_value_mtm_pnl"], 1 / 20)

    def test_drift_explains_a_long_ladder_on_a_trending_path(self):
        # Statistical sanity: on a strongly up-trending path a LONG ladder beats the
        # drift-REMOVED null's average, while the drift-PRESERVING null is comparable.
        df = _random_ohlc(1500, seed=21, drift=0.002, vol=0.01)
        cfg = DCAConfig(levels=BASELINE_LEVELS)
        dr = de.run_dca_permutation_test(df, cfg, n_permutations=19, null_model=de.NULL_DRIFT_REMOVED)
        self.assertGreater(dr["real_total_mtm_pnl"], dr["shuffled_mtm_mean"])

    def test_suite_reports_family_and_bonferroni(self):
        df = _random_ohlc(300, seed=9)
        s = de.run_dca_null_suite(df, DCAConfig(levels=BASELINE_LEVELS), block_sizes=(24, 48), n_permutations=9)
        self.assertEqual(s["family_size_within_suite"], 4)
        for r in s["results"]:
            self.assertAlmostEqual(r["p_value_mtm_pnl_bonferroni"], min(1.0, r["p_value_mtm_pnl"] * 4))
        self.assertEqual(set(s["reading_by_block_size"].values()), {"INSUFFICIENT_RESOLUTION"})
        self.assertFalse(s["robust_across_block_sizes"] and False)

    def test_unknown_null_is_refused(self):
        with self.assertRaises(ValueError):
            de._synthetic_path(_random_ohlc(50, 1), 5, np.random.default_rng(0), "SIGN_FLIP")


# ── B5 volatility-scaling leakage (positive control) ──────────────────────────
class VolatilityLeakageTests(SimpleTestCase):
    def setUp(self):
        idx = pd.date_range("2022-01-01", periods=2000, freq="h", tz="UTC")
        rng = np.random.default_rng(0)
        ref_vol = np.where(np.arange(2000) < 1000, 0.01, 0.04)      # the REFERENCE's future differs too, so a fit that
        self.ref = pd.Series(100 * np.exp(np.cumsum(ref_vol * rng.standard_normal(2000))), index=idx)   # read it would change
        vol = np.where(np.arange(2000) < 1000, 0.002, 0.02)          # target volatility ×10 in the second half
        self.tgt = pd.Series(1.1 * np.exp(np.cumsum(vol * rng.standard_normal(2000))), index=idx)

    def test_full_history_scaling_is_refused(self):
        with self.assertRaisesRegex(ValueError, "calibration_end"):
            de.compute_volatility_scale_factor("BTC/USDT", "EUR/USD")

    def test_fold_fit_uses_only_the_training_window(self):
        fit_fn, _ = de.make_fx_scaled_oos_fns(self.ref)
        train = pd.DataFrame({"close": self.tgt.iloc[:1000]})
        fitted = fit_fn(train)["scale_factor"]
        prefix = de._vol_ratio_on_window(self.ref.iloc[:1000], self.tgt.iloc[:1000])
        full = de._vol_ratio_on_window(self.ref, self.tgt)
        self.assertAlmostEqual(fitted, prefix, places=12)            # identical to the prefix-only computation
        # positive control: the future WOULD change it. Analytic: prefix 0.002/0.01 = 0.20; full history
        # √((0.002²+0.02²)/2) / √((0.01²+0.04²)/2) = 0.01421/0.02915 = 0.4875 → ×2.44 (simulated ≈ 2.40)
        self.assertGreater(full / fitted, 2.0)

    def test_calibration_window_end_is_exclusive(self):
        end = self.tgt.index[1000]
        self.assertAlmostEqual(de._vol_ratio_on_window(self.ref, self.tgt, end=end),
                               de._vol_ratio_on_window(self.ref.iloc[:1000], self.tgt.iloc[:1000]), places=12)


# ── B6 financing ──────────────────────────────────────────────────────────────
class FinancingTests(SimpleTestCase):
    def test_defaults_and_cost_completeness(self):
        self.assertEqual(DCAConfig(direction="LONG").resolved_financing_status, "NOT_APPLICABLE")
        self.assertEqual(DCAConfig(direction="SHORT").resolved_financing_status, "UNAVAILABLE")
        cfg = DCAConfig(initial_usdt=6.0, direction="SHORT", levels=(), **Z)
        s = summarize_dca_cycles(simulate_dca_cycles(_ohlc(_flat(100, 5)), cfg), cfg)
        self.assertFalse(s["cost_complete"])                          # never presented as cost-realistic
        self.assertEqual(s["financing_statuses"], ["UNAVAILABLE"])

    def test_known_rate_is_charged_per_elapsed_hour(self):
        cfg = DCAConfig(initial_usdt=6.0, direction="SHORT", levels=(), financing_status="KNOWN",
                        financing_rate_per_hour=0.001, **Z)
        trades = simulate_dca_cycles(_ohlc(_flat(100, 5)), cfg)        # entry at open[1]; censored at close[4] mark
        t = trades[0]
        # accrued from open[1] (01:00) to close[4] (05:00) = 4 h × 0.001 × 6 USDT = 0.024
        self.assertAlmostEqual(t["financing_cost_usdt"], 0.024, places=9)
        self.assertAlmostEqual(t["profit"], -0.024, places=9)

    def test_invalid_financing_configs(self):
        with self.assertRaises(ValueError):
            DCAConfig(financing_status="KNOWN")
        with self.assertRaises(ValueError):
            DCAConfig(financing_rate_per_hour=0.001)
        with self.assertRaises(ValueError):
            DCAConfig(financing_status="FREE")


# ── B7 exhaustion denominator ─────────────────────────────────────────────────
class ExhaustionDenominatorTests(SimpleTestCase):
    def _events(self, levels_reached, n_levels=6):
        rows = []
        for k, lvl in enumerate(levels_reached):
            for f in range(1, lvl + 1):
                rows.append({"cycle_entry_time": k, "fill_level": f, "levels_remaining_at_fill": n_levels - f,
                             "x": -2.0, "cycle_final_level_reached": lvl, "cycle_final_profit": 1.0,
                             "hours_from_fill_to_resolution": 1.0})
        return pd.DataFrame(rows)

    def test_bucket_max_below_configured_is_not_exhaustion(self):
        out = de.summarize_feature_experiment(self._events([3, 3, 2]), "x", lambda v: "b")
        self.assertEqual(out.iloc[0]["eventual_ladder_full_exhaustion_rate_pct"], 0.0)   # old code: 66.67 (3 = the bucket max)
        self.assertEqual(out.iloc[0]["configured_max_level"], 6)

    def test_only_the_configured_final_level_counts(self):
        out = de.summarize_feature_experiment(self._events([6, 3]), "x", lambda v: "b")
        self.assertEqual(out.iloc[0]["eventual_ladder_full_exhaustion_rate_pct"], 50.0)

    def test_mixed_ladders_or_disagreeing_config_are_refused(self):
        mixed = pd.concat([self._events([2], 6), self._events([2], 4).assign(cycle_entry_time=9)])
        with self.assertRaisesRegex(ValueError, "mix"):
            de.summarize_feature_experiment(mixed, "x", lambda v: "b")
        with self.assertRaisesRegex(ValueError, "disagrees"):
            de.summarize_feature_experiment(self._events([2]), "x", lambda v: "b", configured_max_level=7)


# ── B8 gaps & elapsed time ────────────────────────────────────────────────────
class GapTests(SimpleTestCase):
    def _gapped(self):
        idx = pd.DatetimeIndex(["2024-01-01 00:00", "2024-01-01 01:00", "2024-01-01 02:00",
                                "2024-01-01 12:00", "2024-01-01 13:00", "2024-01-01 14:00"], tz="UTC")
        return _ohlc(_flat(100, 6), index=idx)

    def test_gap_is_counted_and_holding_is_elapsed_time(self):
        cfg = DCAConfig(initial_usdt=6.0, levels=(), profit_activation_pct=0.5, **Z)
        t = simulate_dca_cycles(self._gapped(), cfg)[0]
        self.assertEqual(t["gap_count"], 1)
        self.assertAlmostEqual(t["gap_hours"], 9.0)                   # 02:00 → 12:00 is 10 h, one bar expected
        self.assertAlmostEqual(t["holding_hours"], 13.0)              # entry open 01:00 → censored at index 14:00
        self.assertEqual(t["holding_candles"], 5)                     # observed candles, labelled as such

    def test_timeout_counts_elapsed_hours_not_rows(self):
        cfg = DCAConfig(initial_usdt=6.0, levels=(), profit_activation_pct=0.5, max_cycle_hours=5.0, **Z)
        t = simulate_dca_cycles(self._gapped(), cfg)[0]
        # decided at close of the 12:00 candle (13:00): 12 h elapsed ≥ 5 → exits at open 13:00, after only 3 rows
        self.assertEqual(t["cycle_exit_reason"], "MAX_CYCLE_CANDLES_TIMEOUT")
        self.assertEqual(t["exit_time"], self._gapped().index[4])

    def test_no_price_is_invented_across_a_gap(self):
        cfg = DCAConfig(initial_usdt=6.0, levels=(), profit_activation_pct=0.5, **Z)
        _, eq = simulate_dca_path(self._gapped(), cfg)
        self.assertEqual(len(eq), 6)                                  # one row per OBSERVED candle — nothing filled in
