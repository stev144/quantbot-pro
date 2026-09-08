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
import shutil
import tempfile

import numpy as np
import pandas as pd
from django.test import TestCase

from bot.research.oos_validator import (
    WalkForwardConfig, FoldSpec, OOSResult,
    build_folds, assert_temporal_disjoint, evaluate_feature_oos,
    evaluate_cross_sectional_oos, _compute_turnover, _hold_and_realize_portfolio,
)
from bot.research.cross_sectional_permutation_test import (
    run_cross_sectional_permutation_test, run_topk_sweep,
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


# ═══════════════════════════════════════════════════════════════════════
# TYPE C — CROSS-SECTIONAL RANKING SYNTHETIC PROOF CASES
#
# claude code changed: new — statistics-infrastructure mission, Milestone
# B3. evaluate_cross_sectional_oos() (Section 10's Type C evaluator) had
# ZERO tests exercising it directly before this: the pre-existing
# "CrossSectionalShapeTest" above tests evaluate_feature_oos()'s
# multi-asset asset_col support, a different code path. Mirrors the
# SyntheticProofCasesTest pattern above (A-E), adapted to the
# cross-sectional-ranking question, plus shape/plumbing coverage.
# ═══════════════════════════════════════════════════════════════════════

def _cross_sectional_panel(n_timestamps, n_assets, feature_fn, return_fn, seed=42, freq="h"):
    """
    Build a long-format (timestamp, asset, feature, fwd_return) panel.
    feature_fn(rng, n_assets) -> array of this timestamp's feature values.
    return_fn(feature_values, rng, n_assets) -> array of forward returns
    for that SAME timestamp, as a function of its own feature values (so
    callers can encode a real, known relationship — or none at all).
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n_timestamps, freq=freq)
    assets = [f"ASSET_{i}" for i in range(n_assets)]
    rows = []
    for ts in idx:
        feat = feature_fn(rng, n_assets)
        ret = return_fn(feat, rng, n_assets)
        for a, f, r in zip(assets, feat, ret):
            rows.append({"ts": ts, "asset": a, "feature": f, "fwd_return": r})
    return pd.DataFrame(rows)


_CS_KNOWN_EDGE_RETURN_FN = lambda feat, rng, n: feat * 0.02 + rng.normal(scale=0.003, size=n)   # noqa: E731 — real, strong, monotonic feature->return relationship
_CS_NO_EDGE_RETURN_FN = lambda feat, rng, n: rng.normal(scale=0.01, size=n)                       # noqa: E731 — return independent of feature


class CrossSectionalOOSSyntheticProofTest(TestCase):

    def test_case_a_known_cross_sectional_edge_is_recovered(self):
        df = _cross_sectional_panel(200, 10, lambda rng, n: rng.normal(size=n), _CS_KNOWN_EDGE_RETURN_FN, seed=700)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=40, test_periods=20, purge_periods=0, embargo_periods=0, min_test_periods=10, seed=700)
        result = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd_return", cfg, top_k=2, long_short=True)
        agg = result.aggregate
        self.assertIsNotNone(agg["sharpe_ratio"])
        self.assertGreater(agg["sharpe_ratio"], 1.0, f"expected a clearly positive recovered Sharpe, got {agg['sharpe_ratio']}")
        self.assertGreater(agg["hit_rate_pct"], 60.0)

    def test_case_c_leakage_trap_fit_fn_never_sees_test_period_rows(self):
        """fit_fn is called once per evaluated fold with ONLY that fold's
        purged train slice — proven here by an independent, external check
        (comparing recorded max train timestamp vs. min test timestamp per
        fold), not by trusting assert_temporal_disjoint() not to have
        raised (that's Section 5's own guarantee; this proves the DATA
        fit_fn actually received honors it)."""
        df = _cross_sectional_panel(150, 8, lambda rng, n: rng.normal(size=n), _CS_KNOWN_EDGE_RETURN_FN, seed=702)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=40, test_periods=20, purge_periods=2, embargo_periods=1, min_test_periods=10, seed=702)

        seen_train_max_ts = []

        def _spy_fit_fn(train_slice):
            seen_train_max_ts.append(train_slice["ts"].max())
            return {"mean": float(train_slice["feature"].mean()), "std": float(train_slice["feature"].std()) or 1.0}

        result = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd_return", cfg, top_k=2, fit_fn=_spy_fit_fn)

        evaluated = [f for f in result.folds if not f.skipped and f.trades]
        self.assertGreaterEqual(len(evaluated), 2, "need multiple evaluated folds to prove the point")
        self.assertEqual(len(seen_train_max_ts), len(evaluated), "fit_fn must be called exactly once per evaluated fold")
        for fold_result, train_max in zip(evaluated, seen_train_max_ts):
            test_min_ts = min(r["timestamp"] for r in fold_result.trades)
            self.assertLess(train_max, test_min_ts, "fit_fn's train slice leaked a timestamp at or after this fold's own test window start")

    def test_case_d_regime_reversal_visible_at_fold_level(self):
        n_timestamps, n_assets = 200, 8
        rng = np.random.default_rng(703)
        idx = pd.date_range("2025-01-01", periods=n_timestamps, freq="h")
        assets = [f"ASSET_{i}" for i in range(n_assets)]
        midpoint = n_timestamps // 2
        rows = []
        for i, ts in enumerate(idx):
            feat = rng.normal(size=n_assets)
            sign = 1.0 if i < midpoint else -1.0
            ret = sign * feat * 0.02 + rng.normal(scale=0.003, size=n_assets)
            for a, f, r in zip(assets, feat, ret):
                rows.append({"ts": ts, "asset": a, "feature": f, "fwd_return": r})
        df = pd.DataFrame(rows)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=40, test_periods=20, purge_periods=0, embargo_periods=0, min_test_periods=10, seed=703)
        result = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd_return", cfg, top_k=2)
        evaluated = [f for f in result.folds if not f.skipped]
        self.assertGreaterEqual(len(evaluated), 4, "need multiple folds spanning the regime change to prove the point")
        early_return = evaluated[0].metrics.get("mean_net_return")
        late_return = evaluated[-1].metrics.get("mean_net_return")
        self.assertIsNotNone(early_return)
        self.assertIsNotNone(late_return)
        self.assertGreater(early_return, 0, f"expected a clearly positive early-fold return, got {early_return}")
        self.assertLess(late_return, 0, f"expected a clearly negative late-fold return (sign-reversed regime), got {late_return}")

    def test_case_e_transaction_costs_reduce_net_return_by_expected_amount(self):
        df = _cross_sectional_panel(150, 10, lambda rng, n: rng.normal(size=n), _CS_KNOWN_EDGE_RETURN_FN, seed=704)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=40, test_periods=20, purge_periods=0, embargo_periods=0, min_test_periods=10, seed=704)
        cost_rate = 0.001
        result_no_cost = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd_return", cfg, top_k=2, cost_rate=0.0)
        result_with_cost = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd_return", cfg, top_k=2, cost_rate=cost_rate)
        # _compute_cross_sectional_fold_metrics applies a flat `cost_rate * 2`
        # (full round-trip) subtraction to every period's return — with
        # identical fold structure (cost_rate doesn't affect which folds/
        # periods exist), the pooled mean must shift by EXACTLY that amount.
        diff = result_no_cost.aggregate["mean_net_return"] - result_with_cost.aggregate["mean_net_return"]
        self.assertAlmostEqual(diff, cost_rate * 2, places=6)

    def test_top_k_controls_leg_size_without_changing_core_evaluator(self):
        df = _cross_sectional_panel(150, 12, lambda rng, n: rng.normal(size=n), _CS_KNOWN_EDGE_RETURN_FN, seed=705)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=40, test_periods=20, purge_periods=0, embargo_periods=0, min_test_periods=10, seed=705)
        for k in (1, 3, 5):
            result = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd_return", cfg, top_k=k)
            evaluated = [f for f in result.folds if not f.skipped]
            self.assertGreater(len(evaluated), 0)
            for f in evaluated:
                for r in f.trades:
                    self.assertEqual(r["n_long"], k)
                    self.assertEqual(r["n_short"], k)

    def test_long_only_never_shorts(self):
        df = _cross_sectional_panel(150, 10, lambda rng, n: rng.normal(size=n), _CS_KNOWN_EDGE_RETURN_FN, seed=706)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=40, test_periods=20, purge_periods=0, embargo_periods=0, min_test_periods=10, seed=706)
        result = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd_return", cfg, top_k=2, long_short=False)
        evaluated = [f for f in result.folds if not f.skipped]
        self.assertGreater(len(evaluated), 0)
        for f in evaluated:
            for r in f.trades:
                self.assertEqual(r["n_short"], 0)
                self.assertEqual(r["short_return"], 0.0)

    def test_point_in_time_missing_asset_data_excluded_not_crashed(self):
        """A NaN feature at some (timestamp, asset) rows — simulating an
        asset not yet listed / temporarily missing data — must be dropped
        from THAT timestamp's ranking, never crash the evaluator or leak
        a fabricated value."""
        df = _cross_sectional_panel(150, 8, lambda rng, n: rng.normal(size=n), _CS_KNOWN_EDGE_RETURN_FN, seed=707)
        rng = np.random.default_rng(999)
        mask = rng.random(len(df)) < 0.1
        df.loc[mask, "feature"] = np.nan
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=40, test_periods=20, purge_periods=0, embargo_periods=0, min_test_periods=10, seed=707)
        result = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd_return", cfg, top_k=2)   # must not raise
        self.assertGreater(result.n_folds_evaluated, 0)


class CrossSectionalPermutationSyntheticProofTest(TestCase):
    """Proof cases for cross_sectional_permutation_test.py — the piece
    Phase 1's audit found genuinely missing (no permutation testing
    existed anywhere for the cross-sectional evaluator)."""

    def test_known_edge_is_significant(self):
        df = _cross_sectional_panel(150, 10, lambda rng, n: rng.normal(size=n), _CS_KNOWN_EDGE_RETURN_FN, seed=800)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=40, test_periods=20, purge_periods=0, embargo_periods=0, min_test_periods=10, seed=800)
        result = run_cross_sectional_permutation_test(
            df, "ts", "asset", "feature", "fwd_return", cfg, top_k=2, n_permutations=30, random_seed=800,
        )
        self.assertTrue(result["verdict"]["edge_appears_real"], result["verdict"])

    def test_no_edge_is_not_significant(self):
        df = _cross_sectional_panel(150, 10, lambda rng, n: rng.normal(size=n), _CS_NO_EDGE_RETURN_FN, seed=801)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=40, test_periods=20, purge_periods=0, embargo_periods=0, min_test_periods=10, seed=801)
        result = run_cross_sectional_permutation_test(
            df, "ts", "asset", "feature", "fwd_return", cfg, top_k=2, n_permutations=30, random_seed=801,
        )
        self.assertFalse(result["verdict"]["edge_appears_real"], result["verdict"])

    def test_topk_sweep_known_edge_survives_fdr(self):
        df = _cross_sectional_panel(150, 12, lambda rng, n: rng.normal(size=n), _CS_KNOWN_EDGE_RETURN_FN, seed=802)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=40, test_periods=20, purge_periods=0, embargo_periods=0, min_test_periods=10, seed=802)
        sweep = run_topk_sweep(
            df, "ts", "asset", "feature", "fwd_return", cfg,
            top_k_values=(1, 3, 5), n_permutations=30, random_seed=802,
        )
        self.assertTrue(sweep["any_survivor"], sweep)
        for k in sweep["top_k_values"]:
            self.assertIn("sharpe_ratio_passes_fdr", sweep["per_config"][k]["verdict"])

    def test_topk_sweep_no_edge_finds_no_survivor(self):
        df = _cross_sectional_panel(150, 12, lambda rng, n: rng.normal(size=n), _CS_NO_EDGE_RETURN_FN, seed=803)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=40, test_periods=20, purge_periods=0, embargo_periods=0, min_test_periods=10, seed=803)
        sweep = run_topk_sweep(
            df, "ts", "asset", "feature", "fwd_return", cfg,
            top_k_values=(1, 3, 5), n_permutations=30, random_seed=803,
        )
        self.assertFalse(sweep["any_survivor"], sweep)


class TopkSweepCheckpointResumeTest(TestCase):
    """claude code changed: new — real bug this directly guards against:
    an overnight run_topk_sweep() call (4 top-K configs, ~100 permutations
    each against the real 100-asset universe) was silently killed by a
    machine sleep/reboot after 2 of 4 configs had already finished HOURS
    of real compute, with nothing on disk to show for it — a full restart
    from config 1 was the only option. checkpoint_dir exists specifically
    so that never happens again; these tests prove it actually works, not
    just that the parameter exists."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_completed_configs_are_not_recomputed_on_resume(self):
        df = _cross_sectional_panel(150, 12, lambda rng, n: rng.normal(size=n), _CS_KNOWN_EDGE_RETURN_FN, seed=804)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=40, test_periods=20, purge_periods=0, embargo_periods=0, min_test_periods=10, seed=804)
        checkpoint_dir = os.path.join(self.tmp_dir, "checkpoints")

        call_count = {"n": 0}
        real_run_fn = run_cross_sectional_permutation_test

        def _counting_wrapper(*args, **kwargs):
            call_count["n"] += 1
            return real_run_fn(*args, **kwargs)

        import bot.research.cross_sectional_permutation_test as cspt
        original = cspt.run_cross_sectional_permutation_test
        cspt.run_cross_sectional_permutation_test = _counting_wrapper
        try:
            # First "run" — simulates the process being killed after only
            # the first 2 of 3 configs finished (never even attempts k=5).
            for k in (1, 3):
                result = _counting_wrapper(df, "ts", "asset", "feature", "fwd_return", cfg, top_k=k, n_permutations=20, random_seed=804)
                cspt._save_checkpoint(checkpoint_dir, k, result)
            self.assertEqual(call_count["n"], 2)

            # "Resume" — a fresh run_topk_sweep() call, same checkpoint_dir,
            # asking for all 3 configs again (exactly what a restarted
            # standalone script does — it has no memory of how far the
            # killed process got, only the checkpoint directory tells it).
            sweep = run_topk_sweep(
                df, "ts", "asset", "feature", "fwd_return", cfg,
                top_k_values=(1, 3, 5), n_permutations=20, random_seed=804,
                checkpoint_dir=checkpoint_dir,
            )
        finally:
            cspt.run_cross_sectional_permutation_test = original

        # Only k=5 should have triggered a real computation on resume —
        # k=1 and k=3 must come from the checkpoint written above.
        self.assertEqual(call_count["n"], 3, "expected exactly one NEW computation (k=5) on top of the 2 pre-seeded checkpoints")
        for k in (1, 3, 5):
            self.assertIn(k, sweep["per_config"])
            self.assertIn("sharpe_ratio_passes_fdr", sweep["per_config"][k]["verdict"], f"FDR must still be computed fresh across ALL configs (including checkpoint-restored ones) on resume, k={k}")

    def test_resumed_sweep_produces_the_same_final_verdict_as_an_uninterrupted_one(self):
        df = _cross_sectional_panel(150, 12, lambda rng, n: rng.normal(size=n), _CS_KNOWN_EDGE_RETURN_FN, seed=805)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=40, test_periods=20, purge_periods=0, embargo_periods=0, min_test_periods=10, seed=805)

        uninterrupted = run_topk_sweep(
            df, "ts", "asset", "feature", "fwd_return", cfg,
            top_k_values=(1, 3), n_permutations=20, random_seed=805,
        )

        checkpoint_dir = os.path.join(self.tmp_dir, "checkpoints_b")
        resumed_part1 = run_topk_sweep(
            df, "ts", "asset", "feature", "fwd_return", cfg,
            top_k_values=(1,), n_permutations=20, random_seed=805, checkpoint_dir=checkpoint_dir,
        )
        resumed_full = run_topk_sweep(
            df, "ts", "asset", "feature", "fwd_return", cfg,
            top_k_values=(1, 3), n_permutations=20, random_seed=805, checkpoint_dir=checkpoint_dir,
        )

        self.assertEqual(uninterrupted["any_survivor"], resumed_full["any_survivor"])
        for k in (1, 3):
            self.assertEqual(
                uninterrupted["per_config"][k]["verdict"]["sharpe_ratio_p_value"],
                resumed_full["per_config"][k]["verdict"]["sharpe_ratio_p_value"],
                f"k={k}: resumed sweep's verdict must exactly match an uninterrupted run with the same seed",
            )

    def test_force_recompute_ignores_existing_checkpoint(self):
        df = _cross_sectional_panel(150, 12, lambda rng, n: rng.normal(size=n), _CS_KNOWN_EDGE_RETURN_FN, seed=806)
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=40, test_periods=20, purge_periods=0, embargo_periods=0, min_test_periods=10, seed=806)
        checkpoint_dir = os.path.join(self.tmp_dir, "checkpoints_c")

        run_topk_sweep(df, "ts", "asset", "feature", "fwd_return", cfg, top_k_values=(1,), n_permutations=20, random_seed=806, checkpoint_dir=checkpoint_dir)

        import bot.research.cross_sectional_permutation_test as cspt
        call_count = {"n": 0}
        original = cspt.run_cross_sectional_permutation_test

        def _counting_wrapper(*args, **kwargs):
            call_count["n"] += 1
            return original(*args, **kwargs)

        cspt.run_cross_sectional_permutation_test = _counting_wrapper
        try:
            run_topk_sweep(df, "ts", "asset", "feature", "fwd_return", cfg, top_k_values=(1,), n_permutations=20, random_seed=806, checkpoint_dir=checkpoint_dir, force_recompute=True)
        finally:
            cspt.run_cross_sectional_permutation_test = original
        self.assertEqual(call_count["n"], 1, "force_recompute=True must bypass the existing checkpoint and recompute")


class ComputeTurnoverTest(TestCase):
    """claude code changed: new — direct unit tests for _compute_turnover(),
    the real bug fix (see oos_validator.py's evaluate_cross_sectional_oos
    docstring: the old flat-cost-every-period assumption produced a
    >10,000% cumulative cost and Sharpe ratios in the -20 to -70 range on
    a real 100-asset run — this function is what replaces "assume 100%
    turnover always" with an actually-measured fraction)."""

    def test_first_rebalance_from_empty_book_is_full_turnover(self):
        self.assertEqual(_compute_turnover([], [], ["A"], ["D"], top_k=1, long_short=True), 1.0)

    def test_identical_book_is_zero_turnover(self):
        self.assertEqual(_compute_turnover(["B"], ["C"], ["B"], ["C"], top_k=1, long_short=True), 0.0)

    def test_completely_disjoint_book_is_full_turnover(self):
        self.assertEqual(_compute_turnover(["A"], ["D"], ["B"], ["C"], top_k=1, long_short=True), 1.0)

    def test_partial_overlap_is_a_fractional_value(self):
        # book_size = 2*top_k = 4; old={A,B,C,D}, new={A,B,X,Y} -> symmetric diff = {C,D,X,Y} = 4 -> 4/4=1.0...
        # use a case with real partial overlap: old long/short = {A},{B} (top_k=1, long_short=False semantics not used here)
        # top_k=2: old_long={A,B}, old_short={C,D}; new_long={A,X}, new_short={C,Y}
        # old_book={A,B,C,D}, new_book={A,X,C,Y}; symmetric_diff={B,D,X,Y} size=4; book_size=2*2=4 -> turnover=1.0 still full since B,D leaving and X,Y entering are 4 distinct changes
        # for a genuinely partial case: old_long={A,B}, new_long={A,X} (only B->X changes, A stays); old_short=new_short={C,D} (unchanged)
        turnover = _compute_turnover(["A", "B"], ["C", "D"], ["A", "X"], ["C", "D"], top_k=2, long_short=True)
        # symmetric_diff({A,B,C,D}, {A,X,C,D}) = {B,X} size=2; book_size=2*2=4 -> 2/4=0.5
        self.assertAlmostEqual(turnover, 0.5)

    def test_long_only_uses_top_k_not_2x_as_book_size(self):
        # long_short=False: book_size = top_k (not 2*top_k) — full disjoint long-only leg must still cap at 1.0, not exceed it
        self.assertEqual(_compute_turnover(["A"], [], ["B"], [], top_k=1, long_short=False), 1.0)

    def test_turnover_never_exceeds_one(self):
        # defensive: even a pathological input can't produce turnover > 1.0 (min() cap)
        turnover = _compute_turnover(["A"], ["B"], ["C", "D", "E"], ["F", "G", "H"], top_k=1, long_short=True)
        self.assertLessEqual(turnover, 1.0)


class HoldAndRealizePortfolioTest(TestCase):
    """claude code changed: new — direct unit tests for
    _hold_and_realize_portfolio(), the "no trade this period" path used
    between rebalances when rebalance_frequency > 1."""

    def test_normal_case_realizes_held_names_returns(self):
        period_df = pd.DataFrame({"asset": ["A", "B", "C"], "fwd": [0.05, 0.02, -0.01]})
        record = _hold_and_realize_portfolio(period_df, "asset", "fwd", ["A"], ["C"], long_short=True)
        self.assertIsNotNone(record)
        self.assertAlmostEqual(record["long_return"], 0.05)
        self.assertAlmostEqual(record["short_return"], -0.01)
        self.assertAlmostEqual(record["gross_return"], 0.06)
        self.assertEqual(record["long_names"], ["A"])
        self.assertEqual(record["short_names"], ["C"])

    def test_one_missing_held_name_dropped_for_this_period_only(self):
        # "A" is held but absent from this period's data (e.g. a real gap) — B is present in the long leg conceptually but here we test a 2-name long leg with one missing
        period_df = pd.DataFrame({"asset": ["B", "C"], "fwd": [0.03, -0.02]})
        record = _hold_and_realize_portfolio(period_df, "asset", "fwd", ["A", "B"], ["C"], long_short=True)
        self.assertIsNotNone(record)
        self.assertEqual(record["n_long"], 1)   # only B contributed — A silently dropped for this period, not an error
        self.assertAlmostEqual(record["long_return"], 0.03)

    def test_all_held_names_missing_returns_none(self):
        period_df = pd.DataFrame({"asset": ["X", "Y"], "fwd": [0.01, 0.02]})
        record = _hold_and_realize_portfolio(period_df, "asset", "fwd", ["A"], ["C"], long_short=True)
        self.assertIsNone(record)

    def test_long_only_ignores_short_leg(self):
        period_df = pd.DataFrame({"asset": ["A"], "fwd": [0.04]})
        record = _hold_and_realize_portfolio(period_df, "asset", "fwd", ["A"], [], long_short=False)
        self.assertIsNotNone(record)
        self.assertEqual(record["n_short"], 0)
        self.assertAlmostEqual(record["gross_return"], 0.04)


class RebalanceFrequencyIntegrationTest(TestCase):
    """claude code changed: new — the end-to-end proof that
    rebalance_frequency actually fixes the real bug: a fully
    deterministic, hand-verified 6-period panel (2 rebalance cycles of 3
    periods each, rebalance_frequency=2) where every turnover_fraction,
    cost, and net_return is computed by hand ahead of time and checked
    exactly, not just "runs without crashing"."""

    def _deterministic_panel(self):
        assets = ["A", "B", "C", "D"]
        train_ts = pd.date_range("2025-01-01", periods=3, freq="h")
        test_ts = pd.date_range("2025-01-01", periods=9, freq="h")[3:]   # 6 test timestamps, contiguous with train

        rows = []
        for ts in train_ts:
            for i, a in enumerate(assets):
                rows.append({"ts": ts, "asset": a, "feature": float(i), "fwd": 0.0})

        # Period 0 (idx 0, REBALANCE): A highest (long), D lowest (short)
        feats = {"A": 10, "B": 5, "C": 4, "D": 1}
        fwds = {"A": 0.05, "B": 0.02, "C": 0.03, "D": 0.01}
        for a in assets:
            rows.append({"ts": test_ts[0], "asset": a, "feature": feats[a], "fwd": fwds[a]})

        # Period 1 (idx 1, HOLD — still A long / D short): feature values
        # DELIBERATELY REVERSED (D highest, A lowest) to prove a hold
        # period does NOT re-rank — if it did, this would flip long/short.
        feats = {"A": 1, "B": 5, "C": 4, "D": 10}
        fwds = {"A": 0.02, "B": 0.10, "C": 0.10, "D": 0.005}
        for a in assets:
            rows.append({"ts": test_ts[1], "asset": a, "feature": feats[a], "fwd": fwds[a]})

        # Period 2 (idx 2, REBALANCE): B highest (long), C lowest (short) — completely disjoint from {A,D}
        feats = {"A": 5, "B": 10, "C": 1, "D": 4}
        fwds = {"A": 0.10, "B": 0.03, "C": 0.01, "D": 0.10}
        for a in assets:
            rows.append({"ts": test_ts[2], "asset": a, "feature": feats[a], "fwd": fwds[a]})

        # Period 3 (idx 3, HOLD — still B long / C short)
        feats = {"A": 10, "B": 1, "C": 4, "D": 5}   # reversed again, proving no re-rank
        fwds = {"A": 0.10, "B": 0.04, "C": 0.02, "D": 0.10}
        for a in assets:
            rows.append({"ts": test_ts[3], "asset": a, "feature": feats[a], "fwd": fwds[a]})

        # Period 4 (idx 4, REBALANCE): SAME book as before — B highest, C lowest again -> zero turnover
        feats = {"A": 4, "B": 10, "C": 1, "D": 5}
        fwds = {"A": 0.10, "B": 0.05, "C": 0.01, "D": 0.10}
        for a in assets:
            rows.append({"ts": test_ts[4], "asset": a, "feature": feats[a], "fwd": fwds[a]})

        # Period 5 (idx 5, HOLD — still B long / C short)
        feats = {"A": 5, "B": 1, "C": 4, "D": 10}
        fwds = {"A": 0.10, "B": 0.02, "C": 0.01, "D": 0.10}
        for a in assets:
            rows.append({"ts": test_ts[5], "asset": a, "feature": feats[a], "fwd": fwds[a]})

        return pd.DataFrame(rows)

    def test_turnover_cost_and_holding_are_computed_exactly_as_hand_derived(self):
        df = self._deterministic_panel()
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=3, test_periods=6, purge_periods=0, embargo_periods=0, min_test_periods=6, seed=1)
        cost_rate = 0.01   # round-trip = 0.02 at full turnover

        result = evaluate_cross_sectional_oos(
            df, "ts", "asset", "feature", "fwd", cfg, top_k=1, long_short=True,
            cost_rate=cost_rate, rebalance_frequency=2,
        )

        evaluated = [f for f in result.folds if not f.skipped]
        self.assertEqual(len(evaluated), 1, "expected exactly one fold covering all 6 test periods")
        trades = evaluated[0].trades
        self.assertEqual(len(trades), 6)

        expected_turnover = [1.0, 0.0, 1.0, 0.0, 0.0, 0.0]
        expected_long = [["A"], ["A"], ["B"], ["B"], ["B"], ["B"]]
        expected_short = [["D"], ["D"], ["C"], ["C"], ["C"], ["C"]]
        expected_cost = [0.02, 0.0, 0.02, 0.0, 0.0, 0.0]
        expected_gross = [0.04, 0.015, 0.02, 0.02, 0.04, 0.01]

        for i, r in enumerate(trades):
            self.assertAlmostEqual(r["turnover_fraction"], expected_turnover[i], msg=f"period {i}")
            self.assertEqual(r["long_names"], expected_long[i], msg=f"period {i} long leg")
            self.assertEqual(r["short_names"], expected_short[i], msg=f"period {i} short leg")
            self.assertAlmostEqual(r["cost"], expected_cost[i], places=6, msg=f"period {i} cost")
            self.assertAlmostEqual(r["gross_return"], expected_gross[i], places=6, msg=f"period {i} gross_return")
            self.assertAlmostEqual(r["net_return"], expected_gross[i] - expected_cost[i], places=6, msg=f"period {i} net_return")

        # mean_turnover_pct: 2 of 6 periods at 100%, rest at 0% -> 33.3333%
        self.assertAlmostEqual(evaluated[0].metrics["mean_turnover_pct"], 100 * 2 / 6, places=2)

    def test_rebalance_frequency_1_pins_turnover_to_full_every_period(self):
        """The default/backward-compatible path: even where periods 4
        and 5 in the panel above have an IDENTICAL ranking to periods 2
        and 3 (zero real turnover), rebalance_frequency<=1 must still
        charge full cost every single period — proving the default
        preserves the exact original (pre-fix) behavior for every
        existing caller that never sets this parameter."""
        df = self._deterministic_panel()
        cfg = WalkForwardConfig(mode="expanding", min_train_periods=3, test_periods=6, purge_periods=0, embargo_periods=0, min_test_periods=6, seed=1)
        result = evaluate_cross_sectional_oos(df, "ts", "asset", "feature", "fwd", cfg, top_k=1, long_short=True, cost_rate=0.01)   # rebalance_frequency omitted -> default 1
        trades = [f for f in result.folds if not f.skipped][0].trades
        self.assertTrue(all(r["turnover_fraction"] == 1.0 for r in trades), "default rebalance_frequency=1 must charge full turnover every period, matching pre-fix behavior exactly")
