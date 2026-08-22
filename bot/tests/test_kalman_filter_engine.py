# claude code changed: new file — Research Agent architecture Phase 1
# (published blueprint, §04): kalman_filter_engine.py had zero automated
# tests despite its own comments documenting a real, already-fixed
# look-ahead leakage bug (kalman_beta_pred/kalman_alpha_pred — the
# pre-update/"prior" state — vs kalman_beta/kalman_alpha — the
# post-update/"posterior" state, which has already incorporated this same
# candle's own AVAX price and therefore cannot be used to measure this
# same candle's tradeable spread without a self-referential shrinkage).
# These tests prove that fix holds, and cover the core recursion,
# warmup masking, z-score winsorization, and forward-return shift
# direction with small hand-constructed fixtures.

import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.kalman_filter_engine import (  # claude code changed: module under test
    KalmanFilterEngine, KalmanState, load_pair_config, ZSCORE_WINSOR_LIMIT,
)


def _write_pairs_csv(tmp_dir, passes_filters=True, hedge_ratio=1.5, intercept=0.1):
    """Minimal cointegration_pairs.csv fixture — the file KalmanFilterEngine's
    __init__ reads its OLS seed from, in the exact shape load_pair_config()
    expects."""
    path = Path(tmp_dir) / "cointegration_pairs.csv"  # claude code changed: temp fixture, not research_data/
    pd.DataFrame([{
        "pair_name": "TEST_A/TEST_B",
        "symbol_a": "TEST_A", "symbol_b": "TEST_B",
        "hedge_ratio": hedge_ratio, "intercept": intercept,
        "half_life_hours": 100.0, "adf_pvalue": 0.001, "coint_pvalue": 0.001,
        "passes_filters": passes_filters,
        "reject_reason": "" if passes_filters else "half_life_exceeds_ceiling",
    }]).to_csv(path, index=False)
    return str(path)


def _make_engine(tmp_dir, warmup_candles=5, require_passes_filters=True, passes_filters=True, **kwargs):
    csv_path = _write_pairs_csv(tmp_dir, passes_filters=passes_filters)
    return KalmanFilterEngine(  # claude code changed: module under test
        pair_name="TEST_A/TEST_B", cointegration_pairs_csv=csv_path,
        require_passes_filters=require_passes_filters, warmup_candles=warmup_candles, **kwargs,
    )


class LoadPairConfigTest(SimpleTestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep fixtures out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp fixture dir

    def test_missing_csv_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):  # claude code changed: fail closed, not a crash further downstream
            load_pair_config("ANY/PAIR", cointegration_pairs_csv=str(Path(self.tmp_dir) / "nope.csv"))

    def test_unknown_pair_name_raises_value_error(self):
        csv_path = _write_pairs_csv(self.tmp_dir)
        with self.assertRaises(ValueError):  # claude code changed: fail closed on unknown pair
            load_pair_config("DOES/NOTEXIST", cointegration_pairs_csv=csv_path)

    def test_failing_filter_rejected_by_default(self):
        csv_path = _write_pairs_csv(self.tmp_dir, passes_filters=False)  # claude code changed: pair failed cointegration_engine.py's own filter
        with self.assertRaises(ValueError):  # claude code changed: require_passes_filters=True (default) must refuse it
            load_pair_config("TEST_A/TEST_B", cointegration_pairs_csv=csv_path)

    def test_failing_filter_allowed_with_explicit_override(self):
        csv_path = _write_pairs_csv(self.tmp_dir, passes_filters=False)
        config = load_pair_config(  # claude code changed: explicit override must succeed
            "TEST_A/TEST_B", cointegration_pairs_csv=csv_path, require_passes_filters=False,
        )
        self.assertFalse(config["passes_filters"])  # claude code changed: config still honestly reports the rejection


class RunFilterConvergenceTest(SimpleTestCase):
    """The core Kalman recursion — feeds a synthetic log-price pair with a
    KNOWN true beta/alpha relationship plus small noise, and confirms the
    filter's post-warmup beta estimate converges close to the true value.
    ~2000 candles is small enough to run in well under a second (the loop
    is simple 2x2 matrix arithmetic per row)."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep fixtures out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp fixture dir

    def test_beta_converges_to_true_synthetic_relationship(self):
        true_beta, true_alpha = 1.7, 0.3  # claude code changed: known relationship the synthetic series is built from
        n = 2000
        rng = np.random.default_rng(42)  # claude code changed: reproducible synthetic data

        log_b = pd.Series(
            np.cumsum(rng.normal(0, 0.01, n)) + 5.0,  # claude code changed: a random-walk-like log price series
            index=pd.date_range("2020-01-01", periods=n, freq="1h"),
        )
        log_a = true_alpha + true_beta * log_b + rng.normal(0, 0.001, n)  # claude code changed: log_a exactly tracks log_b via the true relationship, plus tiny noise

        engine = _make_engine(self.tmp_dir, warmup_candles=200, process_noise_beta=1e-4, process_noise_alpha=1e-4)  # claude code changed: module under test
        initial_state = KalmanState(theta=np.array([1.0, 0.0]), P=np.eye(2))  # claude code changed: deliberately wrong seed — filter must still converge

        results = engine._run_filter(log_a, log_b, initial_state)

        post_warmup_beta = results.loc[~results["is_warmup"], "kalman_beta"]
        self.assertAlmostEqual(post_warmup_beta.mean(), true_beta, delta=0.05)  # claude code changed: converges close to the true relationship

    def test_warmup_flag_covers_exactly_the_first_n_candles(self):
        n = 50
        log_b = pd.Series(np.linspace(5, 6, n), index=pd.date_range("2020-01-01", periods=n, freq="1h"))
        log_a = 0.3 + 1.5 * log_b

        engine = _make_engine(self.tmp_dir, warmup_candles=10)  # claude code changed: module under test
        initial_state = engine._initialise_state(log_a, log_b)
        results = engine._run_filter(log_a, log_b, initial_state)

        self.assertEqual(results["is_warmup"].sum(), 10)  # claude code changed: exactly warmup_candles rows flagged
        self.assertTrue(results["is_warmup"].iloc[:10].all())  # claude code changed: the first 10, specifically
        self.assertFalse(results["is_warmup"].iloc[10:].any())  # claude code changed: none after that


class DynamicSpreadLeakageTest(SimpleTestCase):
    """Regression coverage for the documented, already-fixed leakage bug:
    the tradeable spread must be built from the PRE-update
    (kalman_beta_pred/kalman_alpha_pred) state, never the POST-update
    (kalman_beta/kalman_alpha) state — the posterior has already seen
    this same candle's AVAX price."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep fixtures out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp fixture dir

    def _minimal_results(self, n=5):
        idx = pd.date_range("2020-01-01", periods=n, freq="1h")
        # claude code changed: pred and posterior deliberately set to DIFFERENT
        # values per row so a spread computed from the wrong pair is
        # numerically distinguishable, not accidentally equal.
        results = pd.DataFrame({
            "kalman_beta_pred":  [1.0, 1.1, 1.2, 1.3, 1.4],
            "kalman_alpha_pred": [0.1, 0.1, 0.1, 0.1, 0.1],
            "kalman_beta":       [2.0, 2.1, 2.2, 2.3, 2.4],  # claude code changed: posterior, intentionally far from pred
            "kalman_alpha":      [0.9, 0.9, 0.9, 0.9, 0.9],  # claude code changed: posterior, intentionally far from pred
            "is_warmup":         [False] * n,
        }, index=idx)
        return results

    def test_spread_uses_pred_state_not_posterior_state(self):
        n = 5
        idx = pd.date_range("2020-01-01", periods=n, freq="1h")
        log_a = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0], index=idx)
        log_b = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0], index=idx)

        engine = _make_engine(self.tmp_dir)  # claude code changed: module under test
        results = self._minimal_results(n)

        out = engine._calculate_dynamic_spread(results, log_a, log_b)

        # claude code changed: hand-computed from kalman_beta_pred/kalman_alpha_pred —
        # spread_t = log_a - alpha_pred - beta_pred * log_b = 1.0 - 0.1 - beta_pred*1.0
        expected_from_pred = 1.0 - results["kalman_alpha_pred"] - results["kalman_beta_pred"] * 1.0
        # claude code changed: what it would be if the bug had regressed (using posterior instead)
        would_be_from_posterior = 1.0 - results["kalman_alpha"] - results["kalman_beta"] * 1.0

        pd.testing.assert_series_equal(
            out["kalman_spread"], expected_from_pred, check_names=False,
            obj="kalman_spread must be built from the pre-update (pred) state",
        )
        self.assertFalse(
            out["kalman_spread"].equals(would_be_from_posterior),
            "kalman_spread matches the posterior-based calculation — the leakage bug has regressed",
        )  # claude code changed: explicit negative assertion, not just a positive match

    def test_warmup_rows_spread_is_nan(self):
        n = 5
        idx = pd.date_range("2020-01-01", periods=n, freq="1h")
        log_a = pd.Series([1.0] * n, index=idx)
        log_b = pd.Series([1.0] * n, index=idx)

        engine = _make_engine(self.tmp_dir)  # claude code changed: module under test
        results = self._minimal_results(n)
        results["is_warmup"] = [True, True, False, False, False]  # claude code changed: first two rows still warming up

        out = engine._calculate_dynamic_spread(results, log_a, log_b)

        self.assertTrue(out["kalman_spread"].iloc[:2].isna().all())  # claude code changed: warmup rows must not be used
        self.assertFalse(out["kalman_spread"].iloc[2:].isna().any())  # claude code changed: post-warmup rows must be populated


class DynamicZscoreWinsorizationTest(SimpleTestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep fixtures out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp fixture dir

    def test_zscore_clipped_to_winsor_limit(self):
        n = 700
        rng = np.random.default_rng(9)  # claude code changed: reproducible synthetic spread
        idx = pd.date_range("2020-01-01", periods=n, freq="1h")
        spread = pd.Series(rng.normal(0, 1, n), index=idx)
        spread.iloc[-1] = 1000.0  # claude code changed: deliberately extreme final value

        results = pd.DataFrame({"kalman_spread": spread, "ols_spread": spread}, index=idx)
        engine = _make_engine(self.tmp_dir)  # claude code changed: module under test

        out = engine._calculate_dynamic_zscore(results)

        self.assertLessEqual(out["kalman_zscore"].max(), ZSCORE_WINSOR_LIMIT)  # claude code changed: never exceeds the documented clip
        self.assertGreaterEqual(out["kalman_zscore"].min(), -ZSCORE_WINSOR_LIMIT)  # claude code changed: never below the documented clip
        self.assertAlmostEqual(out["kalman_zscore"].iloc[-1], ZSCORE_WINSOR_LIMIT)  # claude code changed: the deliberately extreme row hits the ceiling exactly


class ForwardReturnShiftTest(SimpleTestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep fixtures out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp fixture dir

    def test_forward_return_is_future_spread_change_not_past(self):
        idx = pd.date_range("2020-01-01", periods=10, freq="1h")
        spread = pd.Series([0.0, 1.0, 2.0, 4.0, 7.0, 11.0, 16.0, 22.0, 29.0, 37.0], index=idx)  # claude code changed: distinct values so shift direction is unambiguous
        results = pd.DataFrame({"kalman_spread": spread}, index=idx)

        engine = _make_engine(self.tmp_dir, forward_horizons={"spread_forward_2h": 2})  # claude code changed: module under test
        out = engine._calculate_forward_returns(results)

        # claude code changed: spread_forward_2h at row 2 (value=2.0) = spread[4] - spread[2] = 7.0 - 2.0 = 5.0
        self.assertAlmostEqual(out["spread_forward_2h"].iloc[2], 5.0, places=10)
        self.assertTrue(pd.isna(out["spread_forward_2h"].iloc[-1]))  # claude code changed: no future spread left at the tail
