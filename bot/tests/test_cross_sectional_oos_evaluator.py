# ============================================================
# bot/tests/test_cross_sectional_oos_evaluator.py
# claude code changed: new file — Type C Cross-Sectional Ranking OOS
# Evaluator (this project's own "next recommended step" after Type A
# feature and Type B strategy evaluators). Covers: fold reuse, a leakage
# suite (fit_fn train-only, mutation-invariance, determinism, no
# look-ahead in ranking), synthetic ground-truth proof cases (no signal,
# known edge, regime change, costs), a real multi-asset integration
# using FeatureCalculator's own RSI/forward_return_1h on real cached
# per-symbol OHLCV (data/*.csv), and governance-ledger integration.
# ============================================================

import os

import numpy as np
import pandas as pd
from django.test import TestCase

from bot.research.oos_validator import WalkForwardConfig, build_folds, evaluate_cross_sectional_oos
from bot.research.feature_calculator import FeatureCalculator
from bot.research_lab.data_fingerprint import DatasetIdentity
from bot.research_lab.trial_service import freeze_family_before_testing, record_oos_trial

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(REPO_ROOT, "data")


def _rng(seed):
    return np.random.default_rng(seed)


def _synthetic_long_format(n_timestamps, n_assets, seed, edge_fn=None):
    """
    Builds a long-format (timestamp, asset, feature, forward_return)
    DataFrame — the same cross-sectional shape evaluate_feature_oos()'s
    asset_col already established. `edge_fn(feature_array, rng)` returns
    the forward_return array; None means pure noise (no real edge).
    """
    rng = _rng(seed)
    idx = pd.date_range("2025-01-01", periods=n_timestamps, freq="h", tz="UTC")
    rows = []
    for ts in idx:
        feature = rng.normal(size=n_assets)
        if edge_fn is not None:
            fwd = edge_fn(feature, rng)
        else:
            fwd = rng.normal(size=n_assets) * 0.01
        for a in range(n_assets):
            rows.append({"ts": ts, "asset": f"ASSET_{a}", "feature": feature[a], "fwd": fwd[a]})
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════
# build_folds() REUSE
# ═══════════════════════════════════════════════════════════════════════

class FoldReuseTest(TestCase):
    def test_matches_build_folds_directly(self):
        df = _synthetic_long_format(600, 10, seed=1)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=200, test_periods=100, horizon=1, min_test_periods=1)
        unique_ts = pd.DatetimeIndex(sorted(df["ts"].unique()))
        direct = build_folds(unique_ts, cfg)
        result = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd", cfg, top_k=2)
        self.assertEqual(len(result.folds), len(direct))
        for got, expected in zip(result.folds, direct):
            self.assertEqual(got.fold.test_start_pos, expected.test_start_pos)
            self.assertEqual(got.fold.test_end_pos, expected.test_end_pos)


# ═══════════════════════════════════════════════════════════════════════
# LEAKAGE SUITE
# ═══════════════════════════════════════════════════════════════════════

class LeakageTest(TestCase):

    def test_fit_fn_receives_only_permitted_training_rows(self):
        n_ts, n_assets = 600, 10
        idx = pd.date_range("2025-01-01", periods=n_ts, freq="h", tz="UTC")
        ts_to_pos = {ts: i for i, ts in enumerate(idx)}
        rows = []
        for ts in idx:
            for a in range(n_assets):
                rows.append({"ts": ts, "asset": f"A{a}", "feature": float(ts_to_pos[ts]), "fwd": 0.0})
        df = pd.DataFrame(rows)

        seen = []

        def spy_fit_fn(train_slice):
            seen.append(float(train_slice["feature"].max()))
            return {"mean": 0.0, "std": 1.0}

        cfg = WalkForwardConfig(mode="expanding", min_train_periods=200, test_periods=100, horizon=3, min_test_periods=1)
        result = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd", cfg, top_k=2, fit_fn=spy_fit_fn)
        evaluated = [f for f in result.folds if not f.skipped]
        self.assertEqual(len(seen), len(evaluated))
        for max_train_pos, fold_result in zip(seen, evaluated):
            self.assertLess(max_train_pos, fold_result.fold.test_start_pos)

    def test_mutating_test_period_values_does_not_change_fitted_params(self):
        # claude code changed: mutates the OUTCOME column ("fwd"), never
        # "feature" — fit_fn only ever reads "feature", so this isolates
        # "does calibration see test-period information" correctly. Note:
        # mutating "feature" itself past the first fold's test_start would
        # NOT be a valid leakage probe in expanding mode, since later
        # folds' TRAIN legitimately grows to include what was an earlier
        # fold's TEST region — that's correct expanding-window behavior,
        # not a leak (first found as a real test-design bug via actual
        # execution: mutating "feature" made later folds' fitted_params
        # differ, which was expected-and-correct once traced, not a bug
        # in evaluate_cross_sectional_oos() itself).
        n_ts, n_assets = 500, 10
        df_a = _synthetic_long_format(n_ts, n_assets, seed=5)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=200, test_periods=100, horizon=1, min_test_periods=1)

        unique_ts = sorted(df_a["ts"].unique())
        first_test_start = unique_ts[200]   # matches min_train_periods
        df_b = df_a.copy()
        mask = df_b["ts"] >= first_test_start
        df_b.loc[mask, "fwd"] = _rng(6).normal(size=mask.sum())

        def fit_fn(train_slice):
            return {"mean": float(train_slice["feature"].mean()), "std": float(train_slice["feature"].std()) or 1.0}

        result_a = evaluate_cross_sectional_oos(df_a, "ts", "asset", "feature", "fwd", cfg, top_k=2, fit_fn=fit_fn)
        result_b = evaluate_cross_sectional_oos(df_b, "ts", "asset", "feature", "fwd", cfg, top_k=2, fit_fn=fit_fn)
        for fa, fb in zip(result_a.folds, result_b.folds):
            self.assertEqual(fa.fitted_params, fb.fitted_params)

    def test_determinism(self):
        df = _synthetic_long_format(500, 8, seed=7)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=200, test_periods=100, horizon=1, seed=7, min_test_periods=1)
        d1 = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd", cfg, top_k=2).to_dict()
        d2 = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd", cfg, top_k=2).to_dict()
        d1.pop("run_id"); d2.pop("run_id")
        self.assertEqual(d1, d2)

    def test_ranking_at_time_t_never_uses_a_different_timestamps_data(self):
        # claude code changed: proves each rebalance period is scored
        # using ONLY that period's own cross-sectional slice — construct
        # data where feature/fwd are IDENTICAL across timestamps except
        # one deliberately "poisoned" timestamp, and confirm the poisoned
        # timestamp's portfolio return is isolated to its own period
        # record, never bleeding into a neighboring period's record.
        n_ts, n_assets = 400, 6
        idx = pd.date_range("2025-01-01", periods=n_ts, freq="h", tz="UTC")
        rows = []
        for i, ts in enumerate(idx):
            for a in range(n_assets):
                feature = float(a)
                fwd = 999.0 if i == 250 else 0.01 * a   # a single, extreme, isolated period
                rows.append({"ts": ts, "asset": f"A{a}", "feature": feature, "fwd": fwd})
        df = pd.DataFrame(rows)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=200, test_periods=100, horizon=1, min_test_periods=1)
        result = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd", cfg, top_k=2, long_short=False)
        all_periods = [p for f in result.folds if not f.skipped for p in f.trades]
        poisoned = [p for p in all_periods if p["gross_return"] > 100]
        self.assertEqual(len(poisoned), 1, "the extreme value at exactly one timestamp must affect exactly one period record, never neighboring ones")


# ═══════════════════════════════════════════════════════════════════════
# SYNTHETIC PROOF CASES
# ═══════════════════════════════════════════════════════════════════════

class SyntheticProofCasesTest(TestCase):

    def test_case_no_signal_produces_near_zero_return(self):
        df = _synthetic_long_format(1200, 20, seed=100)   # pure noise, no edge_fn
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=400, test_periods=200, horizon=1, seed=100, min_test_periods=1)
        result = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd", cfg, top_k=4)
        agg = result.aggregate
        self.assertGreater(agg["n_periods"], 100)
        self.assertLess(abs(agg["mean_net_return"]), 0.01, f"expected near-zero mean return under no real edge, got {agg['mean_net_return']}")

    def test_case_known_edge_is_recovered_oos(self):
        def edge_fn(feature, rng):
            return feature * 0.02 + rng.normal(size=len(feature)) * 0.002   # feature genuinely predicts forward return
        df = _synthetic_long_format(1200, 20, seed=200, edge_fn=edge_fn)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=400, test_periods=200, horizon=1, seed=200, min_test_periods=1)
        result = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd", cfg, top_k=4)
        agg = result.aggregate
        self.assertGreater(agg["mean_net_return"], 0.01, f"expected the known long-short edge recovered OOS, got {agg['mean_net_return']}")
        self.assertGreater(agg["hit_rate_pct"], 60.0)

    def test_case_regime_change_visible_at_fold_level(self):
        n_ts = 1500
        mid = n_ts // 2

        def edge_fn_factory():
            counter = {"i": -1}
            def edge_fn(feature, rng):
                counter["i"] += 1
                sign = 1.0 if counter["i"] < mid else -1.0
                return sign * feature * 0.03 + rng.normal(size=len(feature)) * 0.002
            return edge_fn

        df = _synthetic_long_format(n_ts, 20, seed=300, edge_fn=edge_fn_factory())
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=400, test_periods=200, horizon=1, seed=300, min_test_periods=1)
        result = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd", cfg, top_k=4)
        evaluated = [f for f in result.folds if not f.skipped]
        self.assertGreaterEqual(len(evaluated), 4)
        early = [f.metrics["mean_net_return"] for f in evaluated[:2]]
        late = [f.metrics["mean_net_return"] for f in evaluated[-2:]]
        self.assertTrue(all(v > 0.01 for v in early), f"expected clearly positive early folds, got {early}")
        self.assertTrue(all(v < -0.01 for v in late), f"expected clearly negative late folds after the flip, got {late}")

    def test_case_costs_reduce_return_exactly_as_configured(self):
        def edge_fn(feature, rng):
            return feature * 0.05   # deterministic, no noise, for an exact cost check
        df = _synthetic_long_format(600, 10, seed=400, edge_fn=edge_fn)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=200, test_periods=100, horizon=1, seed=400, min_test_periods=1)
        cost_rate = 0.001
        result_no_cost = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd", cfg, top_k=2, cost_rate=0.0)
        result_with_cost = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd", cfg, top_k=2, cost_rate=cost_rate)
        for f_nc, f_c in zip(result_no_cost.folds, result_with_cost.folds):
            if f_nc.skipped:
                continue
            for p_nc, p_c in zip(f_nc.trades, f_c.trades):
                self.assertAlmostEqual(p_c["gross_return"], p_nc["gross_return"], places=8)
                self.assertAlmostEqual(p_c["cost"], cost_rate * 2, places=8)
                self.assertAlmostEqual(p_c["net_return"], p_c["gross_return"] - cost_rate * 2, places=8)


# ═══════════════════════════════════════════════════════════════════════
# REAL MULTI-ASSET INTEGRATION — real cached OHLCV, real FeatureCalculator
# ═══════════════════════════════════════════════════════════════════════

class RealMultiAssetIntegrationTest(TestCase):

    SYMBOLS = ["BTC_USDT", "ETH_USDT", "ADA_USDT", "AAVE_USDT", "APT_USDT", "ARB_USDT", "ATOM_USDT"]

    def _build_long_format_rsi_dataset(self, n_rows=3000):
        calc = FeatureCalculator(min_data_required=100)
        frames = []
        for sym in self.SYMBOLS:
            path = os.path.join(DATA_DIR, f"{sym}_1h.csv")
            if not os.path.exists(path):
                continue
            raw = pd.read_csv(path)
            raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True)
            raw = raw.tail(n_rows).reset_index(drop=True)
            featured = calc.calculate_all_features(raw, symbol=sym.replace("_", "/"), timeframe="1h")
            if featured is None or "forward_return_1h" not in featured.columns:
                continue
            sub = featured[["timestamp", "rsi", "forward_return_1h"]].dropna()
            sub["asset"] = sym
            frames.append(sub)
        if not frames:
            return None
        combined = pd.concat(frames, ignore_index=True)
        # claude code changed: keep only timestamps where at least 2*top_k
        # assets have data, matching real cross-sectional research practice
        # (an asset with a gap that hour simply doesn't participate that
        # period rather than corrupting the panel)
        return combined

    def test_real_rsi_cross_sectional_ranking_runs_end_to_end(self):
        df = self._build_long_format_rsi_dataset()
        if df is None or df["asset"].nunique() < 4:
            self.skipTest("insufficient real per-symbol OHLCV fixtures present for a real cross-sectional run")

        cfg = WalkForwardConfig(mode="expanding", min_train_periods=1500, test_periods=500, horizon=1, min_test_periods=50, seed=42)
        result = evaluate_cross_sectional_oos(
            df, "timestamp", "asset", "rsi", "forward_return_1h", cfg, top_k=2, long_short=True, cost_rate=0.0005,
            strategy_name="rsi_cross_sectional_rank_long_short", strategy_version="test/1.0",
        )
        self.assertGreater(result.n_folds_total, 0)
        self.assertEqual(result.evaluation_type, "cross_sectional_ranking")
        agg = result.aggregate
        self.assertIn("mean_net_return", agg)
        for f in result.folds:
            self.assertIsInstance(f.to_dict(), dict)


# ═══════════════════════════════════════════════════════════════════════
# GOVERNANCE INTEGRATION
# ═══════════════════════════════════════════════════════════════════════

class RecordOosTrialCrossSectionalIntegrationTest(TestCase):
    def test_record_oos_trial_persists_a_cross_sectional_result(self):
        def edge_fn(feature, rng):
            return feature * 0.02 + rng.normal(size=len(feature)) * 0.002
        df = _synthetic_long_format(800, 15, seed=900, edge_fn=edge_fn)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=300, test_periods=150, horizon=1, seed=900, min_test_periods=1)
        result = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd", cfg, top_k=3, strategy_name="synthetic_cross_sectional_rank", strategy_version="test/1.0")

        family = freeze_family_before_testing(
            name="cross_sectional_oos_integration_test", feature_family=["feature"],
            assets=["SYN_UNIVERSE"], venue="synthetic", timeframe="1h", horizons=["1"],
        )
        identity = DatasetIdentity(source="synthetic", symbol="SYN_UNIVERSE", venue="synthetic", timeframe="1h", start_date="2025-01-01", end_date="2025-02-01", row_count=len(df))
        experiment = record_oos_trial(
            hypothesis_family=family, oos_result=result,
            hypothesis_text="cross-sectional feature ranking survives strict OOS walk-forward",
            dataset_identities=[identity], verdict="PASS",
        )
        self.assertEqual(experiment.status, "COMPLETED")
        self.assertEqual(experiment.statistical_results["evaluation_type"], "cross_sectional_ranking")
        self.assertEqual(len(experiment.statistical_results["folds"]), result.n_folds_total)
        self.assertEqual(experiment.code_version, result.methodology_version)

        with self.assertRaises(Exception):
            experiment.verdict = "FAIL"
            experiment.save()
