# claude code changed: new file — Stage 2D engine-level tests: execution timing
# (no same-candle fills), size floor, dead-filter removal, β domain, metric
# bases. Synthetic CSVs only; no exchange, no scan.

import math
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.entry_exit_engine import (
    ENTRY_SIGNAL_MIN, MAX_BETA_UNCERTAINTY, PLACEHOLDER_WIN_RATE_SIZING_IRRELEVANT, PRED_ERROR_NORMAL,
    EntryExitEngine, FixedFractionSizer,   # claude code changed: Stage 2D Rev B step 8 — was KalmanPositionSizer
)


def _scenario(path, *, n_flat=12, z_path, close_a, close_b, beta=1.5, drop_rows=()):
    """n_flat quiet candles followed by the given z path and prices."""
    n = n_flat + len(z_path)
    z = [-0.1] * n_flat + list(z_path)
    df = pd.DataFrame({
        "timestamp": list(pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")),
        "kalman_zscore": z,
        "kalman_zscore_lag1": [z[0]] + z[:-1],
        "pair_signal_dynamic": [-v for v in z],
        "kalman_beta": [beta] * n,
        "kalman_spread": [0.0] * n,
        "prediction_error": [0.005] * n,
        "beta_uncertainty": [0.01] * n,
        "close_a": [close_a[0]] * n_flat + list(close_a[1:]),
        "close_b": [close_b[0]] * n_flat + list(close_b[1:]),
    })
    if drop_rows:
        df = df.drop(index=[n_flat + r for r in drop_rows])
    df.to_csv(path, index=False)


class ExecutionTimingTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.csv = Path(self.tmp) / "AVAX_USDT_ATOM_USDT_kalman.csv"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _engine(self, **kw):
        return EntryExitEngine(output_dir=self.tmp, validated_win_rate=PLACEHOLDER_WIN_RATE_SIZING_IRRELEVANT, **kw)

    def test_same_candle_execution_rejected(self):
        for bad in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                self._engine(execution_delay_candles=bad)

    def test_entry_and_exit_fill_at_t_plus_d(self):
        # decision row 0 (z=-2.2), fill row 1, stop decision row 2 (z=-4.0), exit fill row 3
        _scenario(self.csv, z_path=[-2.2, -2.3, -4.0, -0.1, -0.1],
                  close_a=[10.0, 10.0, 12.0, 13.0, 14.0, 15.0], close_b=[20.0] * 6)
        trades, _, _ = self._engine().run(kalman_csv=str(self.csv))
        self.assertEqual(len(trades), 1)
        t = trades.iloc[0]
        base = pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(hours=12)
        self.assertEqual(pd.Timestamp(t["entry_decision_timestamp"]), base)
        self.assertEqual(pd.Timestamp(t["entry_timestamp"]), base + pd.Timedelta(hours=1))
        self.assertEqual(t["entry_price_a"], 12.0)             # close of t+1, NOT the decision candle's 10.0
        self.assertEqual(pd.Timestamp(t["exit_decision_timestamp"]), base + pd.Timedelta(hours=2))
        self.assertEqual(pd.Timestamp(t["exit_timestamp"]), base + pd.Timedelta(hours=3))
        self.assertEqual(t["exit_price_a"], 14.0)              # close of u+1, NOT the decision candle's 13.0
        self.assertEqual(t["exit_reason"], "STOPLOSS")

    def test_delay_two_candles(self):
        _scenario(self.csv, z_path=[-2.2, -2.3, -2.3, -4.0, -0.1, -0.1, -0.1],
                  close_a=[10.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0], close_b=[20.0] * 8)
        t = self._engine(execution_delay_candles=2).run(kalman_csv=str(self.csv))[0].iloc[0]
        self.assertEqual(t["entry_price_a"], 12.0)             # decision row 0 → fill row 2
        self.assertEqual(t["exit_price_a"], 15.0)              # stop decision row 3 → fill row 5

    def test_missing_fill_candle_skips_entry(self):
        # the t+1 candle is absent from the data: no fill price exists → SKIP, no substitute
        _scenario(self.csv, z_path=[-2.2, -2.3, -0.1, -0.1, -0.1],
                  close_a=[10.0] * 6, close_b=[20.0] * 6, drop_rows=(1,))
        engine = self._engine()
        trades, summary, _ = engine.run(kalman_csv=str(self.csv))
        self.assertEqual(engine.n_entries_skipped_missing_fill, 1)
        self.assertEqual(int(summary.iloc[0]["entries_skipped_missing_fill"]) if not summary.empty else 1, 1)

    def test_future_prices_after_decision_do_not_change_the_decision(self):
        _scenario(self.csv, z_path=[-2.2, -2.3, -4.0, -0.1, -0.1],
                  close_a=[10.0, 10.0, 12.0, 13.0, 14.0, 15.0], close_b=[20.0] * 6)
        t1 = self._engine().run(kalman_csv=str(self.csv))[0].iloc[0]
        _scenario(self.csv, z_path=[-2.2, -2.3, -4.0, -0.1, -0.1],
                  close_a=[10.0, 10.0, 99.0, 99.0, 99.0, 99.0], close_b=[20.0] * 6)
        t2 = self._engine().run(kalman_csv=str(self.csv))[0].iloc[0]
        self.assertEqual(t1["entry_decision_timestamp"], t2["entry_decision_timestamp"])
        self.assertEqual(t1["entry_zscore"], t2["entry_zscore"])
        self.assertEqual(t1["direction"], t2["direction"])
        self.assertNotEqual(t1["entry_price_a"], t2["entry_price_a"])   # positive control: the fill moved


class SizeFloorAndDomainTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.csv = Path(self.tmp) / "AVAX_USDT_ATOM_USDT_kalman.csv"
        _scenario(self.csv, z_path=[-2.2, -2.3, -4.0, -0.1, -0.1], close_a=[10.0] * 6, close_b=[20.0] * 6)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_research_uses_the_instance_floor_not_a_hardcoded_100(self):
        # claude code changed: Stage 2D Rev B step 8 — capital 400: fixed-fraction
        # T = 0.05 × 400 = $20, below the module's $100 but above the instance's $15
        small = EntryExitEngine(output_dir=self.tmp, validated_win_rate=PLACEHOLDER_WIN_RATE_SIZING_IRRELEVANT,
                                capital_usdt=400.0, min_position_usdt=15.0)
        trades = small.run(kalman_csv=str(self.csv))[0]
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades.iloc[0]["position_usdt"], 20.0, places=2)

    def test_sizer_skips_non_positive_or_nonfinite_beta(self):   # claude code changed: Stage 2D Rev B step 8 — FixedFractionSizer
        sizer = FixedFractionSizer(capital_usdt=10_000.0)
        for beta in (0.0, -0.5, -1.0, float("nan"), float("inf")):
            self.assertFalse(sizer.size(beta).ok, msg=repr(beta))

    def test_sizer_split_for_positive_beta(self):   # claude code changed: Stage 2D Rev B step 8 — FixedFractionSizer
        total, a, b = FixedFractionSizer(capital_usdt=10_000.0).size(1.7).value
        self.assertAlmostEqual(total, 500.0, places=9)
        self.assertAlmostEqual(a, total / 2.7, places=9)
        self.assertAlmostEqual(b, total * 1.7 / 2.7, places=9)


class DeadFilterRemovalTests(SimpleTestCase):
    def test_removed_composite_check_never_changed_a_decision(self):
        """The removed check (signal must agree with z) is reimplemented here; for
        every caller-supplied signal = −z it never rejects, so decisions are
        identical before and after removal."""
        engine = EntryExitEngine(output_dir=tempfile.mkdtemp(), validated_win_rate=PLACEHOLDER_WIN_RATE_SIZING_IRRELEVANT)
        self.assertGreaterEqual(engine.entry_threshold, ENTRY_SIGNAL_MIN)
        g = np.random.default_rng(5)
        for _ in range(5000):
            z, lag = g.uniform(-5, 5), g.uniform(-5, 5)
            unc, pe = g.uniform(0, 2 * MAX_BETA_UNCERTAINTY), g.uniform(-4, 4) * PRED_ERROR_NORMAL
            new = engine._check_entry_conditions(z, lag, -z, unc, pe)
            old_extra_reject = (z > 0 and -z > -ENTRY_SIGNAL_MIN) or (z < 0 and -z < ENTRY_SIGNAL_MIN)
            old = new and not old_extra_reject if abs(z) >= engine.entry_threshold else new
            self.assertEqual(new, old)


class MetricBasisTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.csv = Path(self.tmp) / "AVAX_USDT_ATOM_USDT_kalman.csv"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_summary_bases_and_provenance(self):
        # entry fill at 10, adverse dip to 6 while held, exit fill at 11 → a winner with an intratrade drawdown
        _scenario(self.csv, z_path=[-2.2, -2.3, -2.4, -4.0, -0.1, -0.1],
                  close_a=[10.0, 10.0, 10.0, 6.0, 6.0, 11.0, 11.0], close_b=[20.0] * 7)
        engine = EntryExitEngine(output_dir=self.tmp, validated_win_rate=PLACEHOLDER_WIN_RATE_SIZING_IRRELEVANT,
                                 capital_usdt=1000.0, min_position_usdt=15.0)
        trades, summary, realised_curve = engine.run(kalman_csv=str(self.csv))
        self.assertEqual(len(trades), 1)
        self.assertGreater(trades.iloc[0]["net_pnl_usdt"], 0)                      # a winning trade...
        self.assertEqual(float(realised_curve["drawdown_pct"].min()), 0.0)        # ...shows NO drawdown realised-only
        s = summary.iloc[0]
        self.assertEqual(s["max_drawdown_basis"], "mark_to_market")
        self.assertEqual(s["profit_factor_basis"], "net_usdt")
        self.assertEqual(s["sharpe_basis"], "realised_exit_daily_legacy")
        self.assertEqual(s["cost_status"], "COST_UNAVAILABLE:borrow")
        self.assertEqual(int(s["execution_delay_candles"]), 1)
        self.assertLess(s["max_drawdown_pct"], -0.005)                     # mark-to-market sees the intratrade dip
        self.assertTrue(math.isinf(s["profit_factor"]) or s["profit_factor"] != 999.0)
        self.assertIn("spec_version", s.index)
        # mark-to-market equity ends at capital + total net P&L
        self.assertAlmostEqual(engine._mtm_equity.iloc[-1], 1000.0 + trades["net_pnl_usdt"].sum(), places=1)


# claude code changed: Stage 2D — pair-selection guarantees (Stage 2C §5)
from unittest import mock   # noqa: E402

from bot.quant_core.cointegration import EGTest   # noqa: E402
from bot.quant_core.status import Status, bad, ok   # noqa: E402
from bot.research.cointegration_engine import CointegrationEngine   # noqa: E402


def _coint_data(n=1200, seed=0, constant_b=False):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="1h", tz="UTC")
    la = np.cumsum(rng.normal(0, 0.001, n)) + 4.0
    lb = np.full(n, 2.0) if constant_b else 0.8 * la - 1.2 + rng.normal(0, 0.0005, n)
    return {"SYM_A_USDT": pd.DataFrame({"close": np.exp(la)}, index=idx),
            "SYM_B_USDT": pd.DataFrame({"close": np.exp(lb)}, index=idx)}


def _coint_engine(**kw):
    base = dict(universe=["SYM_A_USDT", "SYM_B_USDT"], training_window=800, zscore_window=100, zscore_min_periods=50)
    base.update(kw)
    return CointegrationEngine(**base)


class PairSelectionTests(SimpleTestCase):
    def _test_pair(self, engine, data):
        a = np.log(data["SYM_A_USDT"]["close"])
        b = np.log(data["SYM_B_USDT"]["close"])
        return engine._test_pair("SYM_A_USDT", "SYM_B_USDT", a, b)

    def test_eg_failure_is_untestable_even_when_ordinary_adf_rejects(self):
        engine = _coint_engine()
        with mock.patch("bot.research.cointegration_engine.engle_granger",
                        return_value=bad(Status.UNTESTABLE, "forced failure")), \
             mock.patch("bot.research.cointegration_engine.adfuller", return_value=(-9.0, 1e-12, 1, 100, {}, 0.0)):
            res = self._test_pair(engine, _coint_data())
        self.assertFalse(res.is_cointegrated)
        self.assertTrue(res.reject_reason.startswith("UNTESTABLE"))
        self.assertEqual(res.coint_pvalue, 1.0)            # counted in the family at p = 1

    def test_decision_uses_engle_granger_not_adf(self):
        engine = _coint_engine()
        with mock.patch("bot.research.cointegration_engine.engle_granger",
                        return_value=ok(EGTest(0.40, -1.0, 0.0, 1.0, 800))), \
             mock.patch("bot.research.cointegration_engine.adfuller", return_value=(-9.0, 1e-12, 1, 100, {}, 0.0)):
            res = self._test_pair(engine, _coint_data())
        self.assertFalse(res.is_cointegrated)               # ADF p = 1e-12 would have passed under the old rule
        self.assertIn("Engle-Granger", res.reject_reason)

    def test_fdr_runs_on_engle_granger_pvalues(self):
        engine = _coint_engine()
        engine.run_all(_coint_data())
        r = engine.pair_results[0]
        self.assertIsNotNone(r.coint_pvalue_fdr)
        self.assertEqual(r.passes_fdr, r.coint_pvalue_fdr < engine.coint_threshold)

    def test_nan_correlation_fails_the_prefilter(self):
        engine = _coint_engine(enable_correlation_prefilter=True, correlation_prefilter_threshold=0.0)
        engine.run_all(_coint_data(constant_b=True))       # constant leg → correlation NaN
        self.assertEqual(len(engine.pair_results), 0)       # used to PASS (NaN was never "< threshold")


# claude code changed: Stage 2D — permutation fail-closed (Stage 2C §7/§18)
from bot.research.permutation_stats import compute_permutation_verdict, is_significant   # noqa: E402


class PermutationFailClosedTests(SimpleTestCase):
    def test_unscorable_replicas_make_the_metric_untestable_not_smaller(self):
        real = {"sharpe_ratio": 3.0}
        shuffled = [{"sharpe_ratio": 0.1}] * 50 + [{"sharpe_ratio": float("nan")}] * 50   # half the shuffles never traded
        v = compute_permutation_verdict(real, shuffled, ["sharpe_ratio"])
        self.assertTrue(math.isnan(v["sharpe_ratio_p_value"]))
        self.assertTrue(v["sharpe_ratio_status"].startswith("UNTESTABLE"))
        self.assertFalse(is_significant(v, "sharpe_ratio"))

    def test_complete_replicas_still_scored(self):
        v = compute_permutation_verdict({"m": 3.0}, [{"m": 0.1}] * 99, ["m"])
        self.assertAlmostEqual(v["m_p_value"], 1 / 100)
        self.assertEqual(v["m_status"], "OK")

    def test_infinite_profit_factor_is_not_capped(self):
        v = compute_permutation_verdict({"pf": float("inf")}, [{"pf": 2.0}] * 9 + [{"pf": float("inf")}], ["pf"])
        self.assertAlmostEqual(v["pf_p_value"], 2 / 11)    # the inf replica ties the inf real value; nothing becomes 999
