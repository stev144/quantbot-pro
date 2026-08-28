# ============================================================
# bot/tests/test_oos_validator.py
# claude code changed: new file — OOS/Walk-Forward Validation
# Infrastructure mission. Covers: build_folds() boundary correctness
# (expanding + rolling), the 12-item enumerated leakage test suite
# (Section 5), the 5 synthetic ground-truth proof cases A-E (Section 17),
# governance integration via trial_service.record_oos_trial() (Section
# 8/13), and a real-dataset comparison against Phase 2E's existing
# ad-hoc single-split IC (Section 18).
# ============================================================

import os

import numpy as np
import pandas as pd
from django.test import TestCase

from bot.research.oos_validator import (
    WalkForwardConfig, FoldSpec, OOSResult,
    build_folds, assert_temporal_disjoint, evaluate_feature_oos,
)
from bot.research_lab.data_fingerprint import DatasetIdentity
from bot.research_lab.trial_service import freeze_family_before_testing, record_oos_trial

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _rng(seed=42):
    return np.random.default_rng(seed)


def _synthetic_df(n=1000, seed=42):
    idx = pd.date_range("2025-01-01", periods=n, freq="h")
    rng = _rng(seed)
    feature = rng.normal(size=n)
    return idx, feature, rng


# ═══════════════════════════════════════════════════════════════════════
# WalkForwardConfig / build_folds — boundary correctness
# ═══════════════════════════════════════════════════════════════════════

class WalkForwardConfigTest(TestCase):
    def test_resolved_purge_defaults_to_horizon(self):
        cfg = WalkForwardConfig(horizon=7, purge_periods=None)
        self.assertEqual(cfg.resolved_purge(), 7)

    def test_resolved_purge_respects_explicit_override(self):
        cfg = WalkForwardConfig(horizon=7, purge_periods=2)
        self.assertEqual(cfg.resolved_purge(), 2)

    def test_resolved_step_defaults_to_test_periods(self):
        cfg = WalkForwardConfig(test_periods=50, step_periods=None)
        self.assertEqual(cfg.resolved_step(), 50)


class BuildFoldsExpandingTest(TestCase):
    def setUp(self):
        self.idx = pd.date_range("2025-01-01", periods=1000, freq="h")

    def test_expanding_train_always_starts_at_zero(self):
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=200, test_periods=100, horizon=1, purge_periods=0)
        folds = build_folds(self.idx, cfg)
        self.assertGreater(len(folds), 1)
        for f in folds:
            self.assertEqual(f.train_start_pos, 0)

    def test_expanding_train_grows_each_fold(self):
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=200, test_periods=100, horizon=1, purge_periods=0)
        folds = build_folds(self.idx, cfg)
        sizes = [f.train_end_pos for f in folds]
        self.assertEqual(sizes, sorted(sizes))
        self.assertLess(sizes[0], sizes[-1])

    def test_never_pads_at_data_end(self):
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=900, test_periods=150, horizon=1, purge_periods=0, min_test_periods=1)
        folds = build_folds(self.idx, cfg)
        last = folds[-1]
        self.assertLessEqual(last.test_end_pos, len(self.idx))
        self.assertEqual(last.test_end, self.idx[-1])

    def test_skips_fold_below_min_test_periods(self):
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=950, test_periods=100, horizon=1, purge_periods=0, min_test_periods=80)
        folds = build_folds(self.idx, cfg)
        self.assertTrue(folds[-1].skipped)
        self.assertIn("min_test_periods", folds[-1].skip_reason)

    def test_never_shuffles_chronological_order(self):
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=200, test_periods=100, horizon=1, purge_periods=0)
        folds = build_folds(self.idx, cfg)
        for f in folds:
            self.assertLessEqual(f.train_start, f.train_end)
            self.assertLess(f.train_end, f.test_start)
            self.assertLessEqual(f.test_start, f.test_end)
        starts = [f.test_start_pos for f in folds]
        self.assertEqual(starts, sorted(starts))

    def test_rejects_bad_mode(self):
        with self.assertRaises(ValueError):
            build_folds(self.idx, WalkForwardConfig(mode="shuffled"))

    def test_rejects_duplicate_timestamps(self):
        dup_idx = self.idx.append(pd.DatetimeIndex([self.idx[0]]))
        with self.assertRaises(ValueError):
            build_folds(dup_idx, WalkForwardConfig())


class BuildFoldsRollingTest(TestCase):
    def setUp(self):
        self.idx = pd.date_range("2025-01-01", periods=1000, freq="h")

    def test_rolling_train_window_size_stays_constant(self):
        cfg = WalkForwardConfig(mode="rolling", min_train_periods=300, test_periods=100, horizon=1, purge_periods=0)
        folds = build_folds(self.idx, cfg)
        self.assertGreater(len(folds), 2)
        non_skipped = [f for f in folds if not f.skipped]
        sizes = {f.train_end_pos - f.train_start_pos for f in non_skipped[:-1]}
        self.assertEqual(sizes, {300})

    def test_rolling_train_start_advances(self):
        cfg = WalkForwardConfig(mode="rolling", min_train_periods=300, test_periods=100, horizon=1, purge_periods=0)
        folds = build_folds(self.idx, cfg)
        starts = [f.train_start_pos for f in folds]
        self.assertLess(starts[0], starts[-1])


# ═══════════════════════════════════════════════════════════════════════
# 12-ITEM ENUMERATED LEAKAGE TEST SUITE (Section 5)
# ═══════════════════════════════════════════════════════════════════════

class LeakageTestSuite(TestCase):
    """
    Each test below is one numbered item of the mission's required
    12-item leakage test suite. Numbered in docstrings so the final
    report can cite them directly.
    """

    def setUp(self):
        self.idx = pd.date_range("2025-01-01", periods=1000, freq="h")

    def test_01_folds_stay_chronological_never_shuffle(self):
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=200, test_periods=100, horizon=5)
        folds = build_folds(self.idx, cfg)
        ids = [f.fold_id for f in folds]
        self.assertEqual(ids, sorted(ids))
        for a, b in zip(folds, folds[1:]):
            self.assertLessEqual(a.test_start, b.test_start)

    def test_02_train_and_test_positions_are_always_disjoint(self):
        cfg = WalkForwardConfig(mode="rolling", min_train_periods=200, test_periods=100, horizon=5, embargo_periods=3)
        for f in build_folds(self.idx, cfg):
            if f.skipped:
                continue
            train_positions = set(range(f.train_start_pos, f.train_end_pos))
            test_positions = set(range(f.test_start_pos, f.test_end_pos))
            self.assertEqual(train_positions & test_positions, set())

    def test_03_default_purge_equals_horizon_exactly(self):
        cfg = WalkForwardConfig(min_train_periods=200, test_periods=100, horizon=6, purge_periods=None)
        folds = build_folds(self.idx, cfg)
        self.assertTrue(all(f.n_purged == 6 for f in folds))

    def test_04_purge_disabled_leaves_boundary_rows_with_label_windows_overlapping_test(self):
        """Case C (unsafe side): with purge=0, every one of the last
        `horizon` train rows has a forward-looking label window
        [i+1, i+1+horizon) that overlaps the test region — proving those
        rows really would leak test-period information into the label
        used to fit, if purge were not applied."""
        horizon = 5
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=300, test_periods=100, horizon=horizon, purge_periods=0, embargo_periods=0)
        fold = build_folds(self.idx, cfg)[0]
        boundary_rows = range(fold.train_end_pos - horizon, fold.train_end_pos)
        for i in boundary_rows:
            label_window = range(i + 1, i + 1 + horizon)
            overlaps_test = bool(set(label_window) & set(range(fold.test_start_pos, fold.test_end_pos)))
            self.assertTrue(overlaps_test, f"row {i}'s label window {list(label_window)} should overlap test but did not")

    def test_05_purge_equal_to_horizon_removes_every_leaking_boundary_row(self):
        """Case C (safe side): with purge=horizon (the default), no
        remaining train row's label window overlaps test — the leak
        proven possible in test_04 is fully closed."""
        horizon = 5
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=300, test_periods=100, horizon=horizon, purge_periods=None, embargo_periods=0)
        fold = build_folds(self.idx, cfg)[0]
        test_positions = set(range(fold.test_start_pos, fold.test_end_pos))
        for i in range(fold.train_start_pos, fold.train_end_pos):
            label_window = set(range(i + 1, i + 1 + horizon))
            self.assertEqual(label_window & test_positions, set(), f"purged train row {i} still leaks into test")

    def test_06_embargo_creates_a_dead_zone_used_by_neither_train_nor_test(self):
        """Case E: embargo adds a buffer strictly between purged-train-end
        and test-start that is used by neither split."""
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=300, test_periods=100, horizon=5, purge_periods=5, embargo_periods=10)
        fold = build_folds(self.idx, cfg)[0]
        dead_zone_size = fold.test_start_pos - (fold.train_end_pos + fold.n_purged)
        self.assertEqual(dead_zone_size, 10)
        dead_zone = set(range(fold.train_end_pos + fold.n_purged, fold.test_start_pos))
        self.assertEqual(dead_zone & set(range(fold.train_start_pos, fold.train_end_pos)), set())
        self.assertEqual(dead_zone & set(range(fold.test_start_pos, fold.test_end_pos)), set())

    def test_07_assert_temporal_disjoint_raises_on_direct_overlap(self):
        with self.assertRaises(RuntimeError):
            assert_temporal_disjoint([1, 2, 3, 10], [10, 11, 12])

    def test_08_assert_temporal_disjoint_raises_when_train_max_reaches_test_start(self):
        with self.assertRaises(RuntimeError):
            assert_temporal_disjoint([1, 2, 3, 10], [10])

    def test_08b_assert_temporal_disjoint_passes_on_a_real_safe_gap(self):
        assert_temporal_disjoint([1, 2, 3, 9], [15, 16, 17])  # should not raise

    def test_09_fit_fn_receives_only_purged_train_rows(self):
        """Uses feature=row-position itself so a leak is unmistakable:
        if fit_fn ever saw a test-range value, max(train_feat) would be
        >= the fold's test_start_pos."""
        n = 500
        idx = pd.date_range("2025-01-01", periods=n, freq="h")
        positions = np.arange(n, dtype=float)
        df = pd.DataFrame({"ts": idx, "feature": positions, "label": positions})
        calls = []   # claude code changed: one entry per fit_fn call, in fold order — a single shared dict would only keep the LAST fold's value

        def spy_fit_fn(train_feature):
            calls.append(float(np.max(train_feature)))
            return {"mean": 0.0, "std": 1.0}

        cfg = WalkForwardConfig(mode="expanding", min_train_periods=200, test_periods=100, horizon=3, min_test_periods=1)
        result = evaluate_feature_oos(df, "feature", "label", "ts", cfg, fit_fn=spy_fit_fn)
        evaluated_folds = [f for f in result.folds if not f.skipped]
        self.assertEqual(len(calls), len(evaluated_folds))
        for max_train_value, fold_result in zip(calls, evaluated_folds):
            self.assertLess(max_train_value, fold_result.fold.test_start_pos)

    def test_10_permuting_test_labels_does_not_change_fitted_params(self):
        """predict-time information (the label) cannot reach fit_fn:
        two runs differing ONLY in test-window labels must fit identical
        params per fold."""
        n = 500
        idx = pd.date_range("2025-01-01", periods=n, freq="h")
        rng = _rng(1)
        feature = rng.normal(size=n)
        label_a = rng.normal(size=n)
        label_b = rng.permutation(label_a)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=200, test_periods=100, horizon=1, min_test_periods=1)

        df_a = pd.DataFrame({"ts": idx, "feature": feature, "label": label_a})
        df_b = pd.DataFrame({"ts": idx, "feature": feature, "label": label_b})
        result_a = evaluate_feature_oos(df_a, "feature", "label", "ts", cfg)
        result_b = evaluate_feature_oos(df_b, "feature", "label", "ts", cfg)

        for fa, fb in zip(result_a.folds, result_b.folds):
            self.assertEqual(fa.fitted_params, fb.fitted_params)

    def test_11_skipped_folds_never_contribute_to_aggregate_ic(self):
        n = 400
        idx = pd.date_range("2025-01-01", periods=n, freq="h")
        rng = _rng(2)
        feature = rng.normal(size=n)
        label = feature * 2 + rng.normal(size=n) * 0.1
        # deliberately tiny min_test_periods=1 with a large test window that
        # will get clipped to almost nothing on the final fold, plus one fold
        # whose test window is forced empty via a huge min_test_periods.
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=350, test_periods=100, horizon=1, min_test_periods=200)
        df = pd.DataFrame({"ts": idx, "feature": feature, "label": label})
        result = evaluate_feature_oos(df, "feature", "label", "ts", cfg)
        self.assertTrue(any(f.skipped for f in result.folds))
        valid_ics = [f.ic for f in result.folds if not f.skipped and f.ic is not None]
        agg = result.aggregate
        self.assertEqual(agg["n_folds_with_ic"], len(valid_ics))

    def test_12_deterministic_given_identical_inputs_and_seed(self):
        idx, feature, rng = _synthetic_df(n=600, seed=7)
        label = feature * 0.5 + _rng(8).normal(size=600) * 0.2
        df = pd.DataFrame({"ts": idx, "feature": feature, "label": label})
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=200, test_periods=80, horizon=2, seed=7)

        d1 = evaluate_feature_oos(df, "feature", "label", "ts", cfg).to_dict()
        d2 = evaluate_feature_oos(df, "feature", "label", "ts", cfg).to_dict()
        d1.pop("run_id"); d2.pop("run_id")
        self.assertEqual(d1, d2)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 17 — 5 SYNTHETIC GROUND-TRUTH PROOF CASES
# ═══════════════════════════════════════════════════════════════════════

class SyntheticProofCasesTest(TestCase):

    def test_case_a_no_signal_ic_is_near_zero(self):
        n = 2000
        idx = pd.date_range("2025-01-01", periods=n, freq="h")
        rng = _rng(100)
        feature = rng.normal(size=n)
        label = rng.normal(size=n)   # independent of feature — no real relationship
        df = pd.DataFrame({"ts": idx, "feature": feature, "label": label})
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=500, test_periods=200, horizon=1, seed=100)
        result = evaluate_feature_oos(df, "feature", "label", "ts", cfg)
        agg = result.aggregate
        self.assertIsNotNone(agg["mean_ic"])
        self.assertLess(abs(agg["mean_ic"]), 0.12, f"expected near-zero IC under no true signal, got {agg['mean_ic']}")

    def test_case_b_known_signal_is_recovered_oos(self):
        n = 2000
        idx = pd.date_range("2025-01-01", periods=n, freq="h")
        rng = _rng(200)
        feature = rng.normal(size=n)
        label = feature * 3.0 + rng.normal(size=n) * 0.3   # strong, real, monotonic relationship
        df = pd.DataFrame({"ts": idx, "feature": feature, "label": label})
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=500, test_periods=200, horizon=1, seed=200)
        result = evaluate_feature_oos(df, "feature", "label", "ts", cfg)
        agg = result.aggregate
        self.assertGreater(agg["mean_ic"], 0.5, f"expected a strong recovered OOS IC, got {agg['mean_ic']}")
        self.assertGreater(agg["pct_folds_positive_ic"], 0.8)

    def test_case_c_leakage_trap_prevented_by_default_purge(self):
        """Re-verifies test_04/test_05 at the evaluator level: running the
        SAME leak-prone data (label window overlapping the boundary) through
        evaluate_feature_oos's default config (purge=horizon) never raises
        a leakage assertion and never lets a train row's positions collide
        with test — the trap is prevented by construction, not by luck."""
        n = 1500
        idx = pd.date_range("2025-01-01", periods=n, freq="h")
        rng = _rng(300)
        feature = rng.normal(size=n)
        horizon = 8
        label = pd.Series(feature).shift(-horizon).to_numpy().copy()  # label literally IS a future feature value at exactly `horizon` lag; .copy() — pandas' shift() output can be read-only
        label[-horizon:] = np.nan
        df = pd.DataFrame({"ts": idx, "feature": feature, "label": label})
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=400, test_periods=150, horizon=horizon, purge_periods=None, seed=300)
        result = evaluate_feature_oos(df, "feature", "label", "ts", cfg)   # must not raise
        self.assertGreater(result.n_folds_evaluated, 0)

    def test_case_d_regime_change_visible_at_fold_level_not_hidden_in_aggregate(self):
        n = 2000
        idx = pd.date_range("2025-01-01", periods=n, freq="h")
        rng = _rng(400)
        feature = rng.normal(size=n)
        label = np.empty(n)
        midpoint = n // 2
        label[:midpoint] = feature[:midpoint] * 3.0 + rng.normal(size=midpoint) * 0.2       # regime 1: positive relationship
        label[midpoint:] = -feature[midpoint:] * 3.0 + rng.normal(size=n - midpoint) * 0.2   # regime 2: sign-reversed
        df = pd.DataFrame({"ts": idx, "feature": feature, "label": label})
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=400, test_periods=150, horizon=1, seed=400)
        result = evaluate_feature_oos(df, "feature", "label", "ts", cfg)
        valid_folds = [f for f in result.folds if not f.skipped and f.ic is not None]
        self.assertGreaterEqual(len(valid_folds), 4, "need multiple folds spanning the regime change to prove the point")
        early_ics = [f.ic for f in valid_folds[:2]]
        late_ics = [f.ic for f in valid_folds[-2:]]
        self.assertTrue(all(ic > 0.3 for ic in early_ics), f"expected clearly positive early-fold ICs, got {early_ics}")
        self.assertTrue(all(ic < -0.3 for ic in late_ics), f"expected clearly negative late-fold ICs, got {late_ics}")
        # the whole point of Case D: the aggregate mean masks the regime
        # change that is obvious fold-by-fold — proving why Section 6's
        # "fold-level results are mandatory" requirement matters.
        agg_mean = result.aggregate["mean_ic"]
        self.assertLess(abs(agg_mean), max(abs(v) for v in early_ics + late_ics) - 0.15)

    def test_case_e_overlapping_label_purge_and_embargo_together(self):
        """Extends test_06: with an additional feature-side lookback
        (simulated as embargo_periods), the combined purge+embargo dead
        zone must fully cover both the label-horizon overlap AND the
        extra lookback contamination window, and evaluate_feature_oos
        must run cleanly (no leakage assertion) across it."""
        n = 1500
        idx = pd.date_range("2025-01-01", periods=n, freq="h")
        rng = _rng(500)
        horizon = 6
        extra_lookback = 12
        feature = rng.normal(size=n)
        label = pd.Series(feature).rolling(1).mean().shift(-horizon).to_numpy()
        df = pd.DataFrame({"ts": idx, "feature": feature, "label": label})
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=400, test_periods=150, horizon=horizon,
                                 purge_periods=horizon, embargo_periods=extra_lookback, seed=500)
        folds = build_folds(idx, cfg)
        fold = next(f for f in folds if not f.skipped)
        dead_zone = fold.test_start_pos - (fold.train_end_pos + fold.n_purged)
        self.assertEqual(dead_zone, extra_lookback)
        result = evaluate_feature_oos(df, "feature", "label", "ts", cfg)   # must not raise
        self.assertGreater(result.n_folds_evaluated, 0)


# ═══════════════════════════════════════════════════════════════════════
# CROSS-SECTIONAL-SHAPED INPUT (Section 10)
# ═══════════════════════════════════════════════════════════════════════

class CrossSectionalShapeTest(TestCase):
    def test_multi_asset_long_format_shares_one_fold_calendar(self):
        n = 600
        idx = pd.date_range("2025-01-01", periods=n, freq="h")
        rng = _rng(600)
        rows = []
        for asset in ("BTC/USDT", "ETH/USDT"):
            feature = rng.normal(size=n)
            label = feature * 2.0 + rng.normal(size=n) * 0.3
            rows.append(pd.DataFrame({"ts": idx, "feature": feature, "label": label, "asset": asset}))
        df = pd.concat(rows, ignore_index=True)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=200, test_periods=80, horizon=1, seed=600)
        result = evaluate_feature_oos(df, "feature", "label", "ts", cfg, asset_col="asset")
        self.assertEqual(result.asset_universe, ["BTC/USDT", "ETH/USDT"])
        self.assertGreater(result.aggregate["mean_ic"], 0.3)
        # every fold's train/test row counts must be ~2x the single-asset
        # case (both assets pooled at each shared timestamp boundary)
        single_cfg = cfg
        single_result = evaluate_feature_oos(rows[0], "feature", "label", "ts", single_cfg)
        for multi_fold, single_fold in zip(
            [f for f in result.folds if not f.skipped],
            [f for f in single_result.folds if not f.skipped],
        ):
            self.assertEqual(multi_fold.n_train_obs, single_fold.n_train_obs * 2)


# ═══════════════════════════════════════════════════════════════════════
# GOVERNANCE INTEGRATION (Section 8/13) — reuse trial_service, no duplication
# ═══════════════════════════════════════════════════════════════════════

class RecordOosTrialIntegrationTest(TestCase):
    def test_record_oos_trial_persists_fold_level_results(self):
        idx, feature, rng = _synthetic_df(n=800, seed=900)
        label = feature * 1.5 + _rng(901).normal(size=800) * 0.4
        df = pd.DataFrame({"ts": idx, "feature": feature, "label": label})
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=300, test_periods=100, horizon=1, seed=900)
        result = evaluate_feature_oos(df, "feature", "label", "ts", cfg, feature_name="synthetic_feature", label_name="synthetic_label")

        family = freeze_family_before_testing(
            name="oos_validator_integration_test", feature_family=["synthetic_feature"],
            assets=["SYN/TEST"], venue="synthetic", timeframe="1h", horizons=["1"],
        )
        identity = DatasetIdentity(source="synthetic", symbol="SYN/TEST", venue="synthetic", timeframe="1h", start_date=str(idx[0]), end_date=str(idx[-1]), row_count=len(df))
        experiment = record_oos_trial(
            hypothesis_family=family, oos_result=result,
            hypothesis_text="synthetic_feature predicts synthetic_label OOS",
            dataset_identities=[identity], verdict="PASS",
        )
        self.assertEqual(experiment.status, "COMPLETED")
        self.assertEqual(len(experiment.statistical_results["folds"]), result.n_folds_total)
        self.assertIn("aggregate", experiment.statistical_results)
        self.assertEqual(experiment.code_version, result.methodology_version)

        with self.assertRaises(Exception):
            experiment.verdict = "FAIL"
            experiment.save()   # append-only enforcement (bot/research_lab/models.py) must still hold for OOS-sourced experiments


# ═══════════════════════════════════════════════════════════════════════
# SECTION 18 — REAL DATASET: compare against Phase 2E's existing ad-hoc
# single-split IC rather than forcing a match, per this mission's own
# instruction to investigate and explain any difference.
# ═══════════════════════════════════════════════════════════════════════

class RealDatasetComparisonTest(TestCase):
    OBS_PATH = os.path.join(REPO_ROOT, "phase2e_cache", "BTC_USDT_observations.pkl")

    def test_generic_oos_engine_runs_against_real_phase2e_btc_data(self):
        if not os.path.exists(self.OBS_PATH):
            self.skipTest(f"Phase 2E cache not present at {self.OBS_PATH} (ad hoc, not committed to git) — skipping real-dataset comparison")

        df = pd.read_pickle(self.OBS_PATH)
        df = df[["timestamp", "rsi", "forward_return_1h"]].dropna()
        self.assertGreater(len(df), 500, "expected the real cached dataset to have enough rows to fold over")

        cfg = WalkForwardConfig(mode="expanding", min_train_periods=1000, test_periods=300, horizon=1, min_test_periods=50, seed=42)
        result = evaluate_feature_oos(df, "rsi", "forward_return_1h", "timestamp", cfg, feature_name="rsi", label_name="forward_return_1h")

        self.assertGreater(result.n_folds_evaluated, 0, "expected at least one evaluable fold against real hourly BTC data")
        agg = result.aggregate
        self.assertIsNotNone(agg["mean_ic"])
        self.assertTrue(np.isfinite(agg["mean_ic"]))
        # No assertion that this matches Phase 2E's old single-split
        # ic_overall (~0.0 range, DELETE recommendation, per
        # phase2e_cache/phase2e_frozen_family_results.csv) — a walk-forward
        # multi-fold mean IC and a single 80/20-style split IC are
        # different statistics computed different ways and are not
        # expected to be numerically identical. What matters here is that
        # both point the same DIRECTION (no real signal): Phase 2E's own
        # ledger recorded rsi/forward_return_1h as DELETE (ic_overall close
        # to 0, p-value not significant) — this generic engine's fold-mean
        # IC should likewise stay small, which the assertion below checks.
        self.assertLess(abs(agg["mean_ic"]), 0.25, f"generic OOS mean IC ({agg['mean_ic']}) unexpectedly far from Phase 2E's own near-zero finding for rsi/forward_return_1h — investigate before trusting either result")
