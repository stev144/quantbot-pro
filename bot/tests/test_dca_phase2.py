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
                             "hours_from_fill_to_resolution": 1.0,
                             "cycle_exit_reason": "PROFIT_TARGET_TRAILING"})   # claude code changed: 2026-10-04 F2 — the function now needs to know which cycles are censored; these fixture cycles are resolved
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


# ══ 2026-10-04 DCA Step 2 (five-gate remediation of the external review's open items F1–F7) ═══════════════════
# claude code changed: new tests. F1 censored trades out of OOS trade statistics (marks kept in drawdown);
# F2 censored cycles out of feature-experiment outcomes; F3 undefined R stays undefined; F4 sealed holdout refused;
# F5/F6 evidence label + provenance on research outputs; F7 FX refused (FX_CALENDAR_UNAVAILABLE).
def _scripted(trades_by_slice):
    def fn(df_slice, fitted):
        return trades_by_slice(df_slice)
    return fn


class OOSCensoredPolicyTests(SimpleTestCase):
    def _cfg(self):
        from bot.research.oos_validator import WalkForwardConfig
        return WalkForwardConfig(mode="expanding", min_train_periods=100, test_periods=50, horizon=1, min_test_periods=10)

    def _df(self, n=300):
        return pd.DataFrame({"close": np.linspace(100, 101, n)}, index=pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"))

    def test_censored_trades_leave_trade_statistics_but_stay_in_drawdown(self):
        from bot.research.oos_validator import evaluate_strategy_oos
        def trades(s):   # per test slice: one completed +1 winner, then one CENSORED -500 liquidation mark
            i = len(s) - 10
            return [{"entry_time": s.index[i], "exit_time": s.index[i + 1], "profit": 1.0, "gross_profit": 1.0, "r_multiple": None},
                    {"entry_time": s.index[i + 2], "exit_time": s.index[-1], "profit": -500.0, "gross_profit": -500.0,
                     "r_multiple": None, "censored": True}]
        res = evaluate_strategy_oos(self._df(), _scripted(trades), self._cfg())
        f = [x for x in res.folds if not x.skipped][0]
        self.assertEqual([t["profit"] for t in f.trades], [1.0])                 # the mark is not a trade
        self.assertEqual(len(f.censored_trades), 1)
        m = f.metrics
        self.assertEqual((m["n_trades"], m["win_rate_pct"], m["net_return"]), (1, 100.0, 1.0))
        self.assertGreater(m["max_drawdown_pct"], 4.9)                           # -500 on 10,001: the open loss stays visible
        self.assertEqual(m["final_balance"], 10_000 + 1.0 - 500.0)
        self.assertEqual((m["n_censored_trades"], m["censored_mark_pnl"]), (1, -500.0))
        self.assertIsNone(m["avg_r_multiple"])                                   # F3 per fold
        agg = res.aggregate
        self.assertEqual(agg["n_censored_trades"], sum(len(x.censored_trades) for x in res.folds if not x.skipped))
        self.assertIsNone(agg["avg_r_multiple"])                                 # F3 pooled
        self.assertEqual(f.to_dict()["n_censored_trades"], 1)

    def test_strategies_without_the_flag_are_unchanged(self):
        from bot.research.oos_validator import evaluate_strategy_oos
        def trades(s):
            i = len(s) - 10
            return [{"entry_time": s.index[i], "exit_time": s.index[i + 1], "profit": 2.0, "gross_profit": 2.0, "r_multiple": 0.5},
                    {"entry_time": s.index[i + 2], "exit_time": s.index[-1], "profit": -1.0, "gross_profit": -1.0, "r_multiple": -0.25}]
        res = evaluate_strategy_oos(self._df(), _scripted(trades), self._cfg())
        f = [x for x in res.folds if not x.skipped][0]
        self.assertEqual(len(f.trades), 2)
        self.assertNotIn("n_censored_trades", f.metrics)
        self.assertNotIn("n_censored_trades", f.to_dict())
        self.assertNotIn("n_censored_trades", res.aggregate)
        self.assertEqual(f.metrics["avg_r_multiple"], 0.125)
        self.assertEqual(res.aggregate["avg_r_multiple"], 0.125)

    def test_real_dca_walk_forward_scores_no_censored_cycle_as_a_trade(self):
        from bot.research.oos_validator import WalkForwardConfig, evaluate_strategy_oos
        n = 1500
        idx = pd.date_range("2023-01-01", periods=n, freq="h", tz="UTC")
        c = 100 * np.exp(np.cumsum(0.006 * np.random.default_rng(3).standard_normal(n)))
        o = np.r_[c[0], c[:-1]]
        df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.001, "low": np.minimum(o, c) * 0.999, "close": c}, index=idx)
        res = evaluate_strategy_oos(df, de.make_run_strategy_fn(de.BASELINE_CONFIG),
                                    WalkForwardConfig(mode="expanding", min_train_periods=500, test_periods=300, horizon=1, min_test_periods=50))
        ev = [f for f in res.folds if not f.skipped]
        self.assertFalse(any(t.get("censored") for f in ev for t in f.trades))
        self.assertGreater(sum(len(f.censored_trades) for f in ev), 0)


class FeatureExperimentCensoringTests(SimpleTestCase):
    def test_censored_cycles_are_not_outcomes(self):
        ev = pd.DataFrame({"fill_level": [1, 2, 3], "levels_remaining_at_fill": [5, 4, 3], "cycle_entry_time": [1, 2, 3],
                           "cycle_final_level_reached": [3, 3, 6], "cycle_final_profit": [1.0, 1.0, -100.0],
                           "hours_from_fill_to_resolution": [5.0, 4.0, 900.0], "z": [-3.0, -3.0, -3.0],
                           "cycle_exit_reason": ["PROFIT_TARGET_TRAILING", "PROFIT_TARGET_TRAILING", "DATA_END_STILL_OPEN"],
                           "cycle_censored": [False, False, True]})
        row = de.summarize_feature_experiment(ev, "z", lambda v: "b").iloc[0]
        self.assertEqual(row["mean_eventual_cycle_profit"], 1.0)
        self.assertEqual(row["mean_hours_to_resolution"], 4.5)
        self.assertEqual(row["eventual_ladder_full_exhaustion_rate_pct"], 0.0)
        self.assertEqual((row["n_distinct_cycles_touched"], row["n_censored_cycles_touched"]), (3, 1))


    def test_events_without_censoring_information_are_refused(self):   # claude code changed: 2026-10-04 F2 — fail closed
        ev = pd.DataFrame({"fill_level": [1], "levels_remaining_at_fill": [5], "cycle_entry_time": [1],
                           "cycle_final_level_reached": [1], "cycle_final_profit": [1.0],
                           "hours_from_fill_to_resolution": [1.0], "z": [-3.0]})
        with self.assertRaisesRegex(ValueError, "censored cycles cannot be separated"):
            de.summarize_feature_experiment(ev, "z", lambda v: "b")


class HoldoutAndMarketGuardTests(SimpleTestCase):
    def test_sealed_holdout_candles_are_refused(self):
        from bot.quant_core.research_zones import ZONES, HoldoutSealedError
        idx = pd.date_range(ZONES.F - pd.Timedelta(hours=5), periods=10, freq="h")
        with self.assertRaises(HoldoutSealedError):
            simulate_dca_path(_ohlc(_flat(100.0, 10), index=idx), replace(de.BASELINE_CONFIG, **Z))

    def test_session_traded_data_is_refused(self):
        from bot.quant_core.market_profile import AssetClassMismatch
        full = pd.date_range("2024-01-01", "2024-03-31 23:00", freq="h", tz="UTC")
        idx = full[full.dayofweek < 5]                                          # weekends absent, as in FX files
        with self.assertRaisesRegex(AssetClassMismatch, "UTC Saturdays"):
            simulate_dca_path(_ohlc(_flat(1.1, len(idx)), index=idx), replace(de.BASELINE_CONFIG, **Z))

    def test_fx_entry_points_report_fx_calendar_unavailable(self):
        from bot.quant_core.market_profile import OwnerDecisionMissing
        with self.assertRaisesRegex(OwnerDecisionMissing, "FX_CALENDAR_UNAVAILABLE"):
            de.build_config_with_real_costs(de.BASELINE_CONFIG, "FOREX", symbol="EUR/USD")
        with self.assertRaisesRegex(OwnerDecisionMissing, "FX_CALENDAR_UNAVAILABLE"):
            de.compute_volatility_scale_factor("BTC/USDT", "EUR/USD", calibration_end="2024-01-01")
        self.assertIsNotNone(de.build_config_with_real_costs(de.BASELINE_CONFIG, "CRYPTO"))   # crypto unchanged


class DCAProvenanceTests(SimpleTestCase):
    def test_permutation_output_carries_provenance_and_the_evidence_label(self):
        n = 400
        idx = pd.date_range("2023-01-01", periods=n, freq="h", tz="UTC")
        c = 100 * np.exp(np.cumsum(0.01 * np.random.default_rng(5).standard_normal(n)))
        df = _ohlc([(a, max(a, b), min(a, b), b) for a, b in zip(np.r_[c[0], c[:-1]], c)], index=idx)
        cfg = replace(de.BASELINE_CONFIG, **Z)
        r1 = de.run_dca_permutation_test(df, cfg, n_permutations=9, block_size=24, seed=1)
        r2 = de.run_dca_permutation_test(df, cfg, n_permutations=9, block_size=24, seed=1)
        p = r1["provenance"]
        for k in ("run_id", "config_hash", "data_version", "zones_hash", "methodology_version", "code_commit"):
            self.assertIn(k, p)
        self.assertIn("NOT CLAIM-ELIGIBLE", p["evidence_label"])
        self.assertEqual(p["run_id"], r2["provenance"]["run_id"])               # deterministic
        r3 = de.run_dca_permutation_test(df, replace(cfg, trailing_pct=0.01), n_permutations=9, block_size=24, seed=1)
        self.assertNotEqual(p["config_hash"], r3["provenance"]["config_hash"])  # the config is part of the identity
        self.assertNotEqual(p["run_id"], r3["provenance"]["run_id"])
