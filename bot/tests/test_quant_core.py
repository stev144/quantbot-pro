# claude code changed: new file — Stage 2D tests for bot/quant_core (Stage 2C
# corrected specification). Four layers: software, mathematical identity,
# causality/leakage, execution. Statistical checks here are implementation
# sanity only — formal calibration gates need owner-declared margins (OPEN-36).

import math
from unittest import mock

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.pairs.margin_adapter import AmountRules
from bot.pairs.pnl import PairCosts, close_pair_slice, open_pair_position
from bot.quant_core import accounting, cointegration, costs, metrics, provenance, targets, time_grid
from bot.quant_core.half_life import ar1_half_life
from bot.quant_core.status import FailClosedError, Status


def _grid(n, start="2024-01-01"):
    return pd.date_range(start, periods=n, freq="h", tz="UTC")


def _ou(n, hl, seed, beta=1.3, alpha=0.2):
    g = np.random.default_rng(seed)
    y = np.cumsum(g.normal(0, 0.01, n))
    phi = math.exp(-math.log(2) / hl)
    u = np.zeros(n)
    e = g.normal(0, 0.01, n)
    for t in range(1, n):
        u[t] = phi * u[t - 1] + e[t]
    return alpha + beta * y + u, y


# ── Layer A: software ────────────────────────────────────────────────────────

class TimeGridTests(SimpleTestCase):
    def test_naive_timestamps_fail(self):
        df = pd.DataFrame({"close": [1.0, 2.0]}, index=pd.date_range("2024-01-01", periods=2, freq="h"))
        with self.assertRaises(time_grid.GridError):
            time_grid.to_hourly_grid(df)

    def test_unaligned_timestamps_fail(self):
        df = pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex(["2024-01-01 00:30"], tz="UTC"))
        with self.assertRaises(time_grid.GridError):
            time_grid.to_hourly_grid(df)

    def test_identical_duplicates_removed_conflicting_fail(self):
        idx = pd.DatetimeIndex(["2024-01-01 00:00", "2024-01-01 00:00", "2024-01-01 01:00"], tz="UTC")
        g = time_grid.to_hourly_grid(pd.DataFrame({"close": [1.0, 1.0, 2.0]}, index=idx))
        self.assertEqual(g.n_duplicates_removed, 1)
        self.assertEqual(len(g.frame), 2)
        with self.assertRaises(time_grid.GridError):
            time_grid.to_hourly_grid(pd.DataFrame({"close": [1.0, 1.5, 2.0]}, index=idx))

    def test_missing_candle_stays_missing(self):
        idx = pd.DatetimeIndex(["2024-01-01 00:00", "2024-01-01 01:00", "2024-01-01 04:00"], tz="UTC")
        g = time_grid.to_hourly_grid(pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=idx))
        self.assertEqual(len(g.frame), 5)
        self.assertEqual(g.n_missing, 2)
        self.assertTrue(g.frame["close"].iloc[2:4].isna().all())

    def test_lag_pairs_never_span_a_gap(self):
        s = pd.Series([1.0, 2.0, np.nan, 4.0, 5.0], index=_grid(5))
        pairs = time_grid.lag_pairs(s)
        self.assertEqual(list(pairs["x"]), [2.0, 5.0])        # (2,1) and (5,4); never (4,2)
        self.assertEqual(list(pairs["x_lag"]), [1.0, 4.0])

    def test_lag_pairs_rejects_compressed_index(self):
        s = pd.Series([1.0, 2.0, 3.0], index=pd.DatetimeIndex(
            ["2024-01-01 00:00", "2024-01-01 01:00", "2024-01-01 05:00"], tz="UTC"))
        with self.assertRaises(time_grid.GridError):
            time_grid.lag_pairs(s)

    def test_segments(self):
        s = pd.Series([1.0, np.nan, 2.0, 3.0, np.nan], index=_grid(5))
        segs = time_grid.segments(s)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[1], (s.index[2], s.index[3]))

    def test_close_time_is_open_plus_one_hour(self):
        o = pd.Timestamp("2024-01-01 05:00", tz="UTC")
        self.assertEqual(time_grid.close_time(o), pd.Timestamp("2024-01-01 06:00", tz="UTC"))


class ProvenanceTests(SimpleTestCase):
    def test_config_hash_is_order_independent_and_sensitive(self):
        a = provenance.config_hash({"x": 1, "y": [1.5, "a"]})
        b = provenance.config_hash({"y": [1.5, "a"], "x": 1})
        c = provenance.config_hash({"x": 1, "y": [1.50001, "a"]})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_non_finite_config_rejected(self):
        with self.assertRaises(ValueError):
            provenance.config_hash({"x": float("nan")})

    def test_universe_rejects_duplicates_and_depends_on_timestamp(self):
        with self.assertRaises(ValueError):
            provenance.universe_id(["A", "A"], "2024-01-01T00:00:00Z")
        self.assertNotEqual(provenance.universe_id(["A", "B"], "2024-01-01T00:00:00Z"),
                            provenance.universe_id(["A", "B"], "2024-02-01T00:00:00Z"))

    def test_trial_id_changes_with_zone(self):
        code = provenance.CodeState("abc", False, None)
        kw = dict(config={"k": 1}, data=[], universe="u", code=code)
        self.assertNotEqual(provenance.trial_id(zone={"v": [0, 10]}, **kw),
                            provenance.trial_id(zone={"v": [0, 11]}, **kw))

    def test_code_state_reports_dirty_tree_honestly(self):
        st = provenance.code_state()
        if st.commit is not None:
            self.assertEqual(len(st.commit), 40)
            self.assertEqual(st.reproducible, st.dirty is False)


class StatusTests(SimpleTestCase):
    def test_require_fails_closed(self):
        r = accounting.validate_beta(float("nan"))
        with self.assertRaises(FailClosedError):
            r.require()


# ── Layer B: mathematical identity ───────────────────────────────────────────

class TradeableTargetTests(SimpleTestCase):
    def test_target_equals_pnl_mark_pnl_over_capital(self):
        g = np.random.default_rng(7)
        n, d, h, T = 60, 1, 4, 1000.0
        idx = _grid(n)
        pa = pd.Series(np.exp(np.cumsum(g.normal(0, 0.02, n))) * 30, index=idx)
        pb = pd.Series(np.exp(np.cumsum(g.normal(0, 0.02, n))) * 7, index=idx)
        beta = pd.Series(g.uniform(0.2, 3.0, n), index=idx)
        side = pd.Series(g.choice([-1.0, 1.0], n), index=idx)
        r = targets.tradeable_return(pa, pb, beta, side, d=d, h=h)
        zero = PairCosts(0.0, 0.0)
        checked = 0
        for t in range(n - d - h):
            direction = "LONG_SPREAD" if side.iloc[t] > 0 else "SHORT_SPREAD"
            pos = open_pair_position(direction, float(beta.iloc[t]), T, float(pa.iloc[t + d]),
                                     float(pb.iloc[t + d]), idx[t + d])
            sl, _ = close_pair_slice(pos, 1.0, float(pa.iloc[t + d + h]), float(pb.iloc[t + d + h]),
                                     idx[t + d + h], zero)
            self.assertAlmostEqual(r.iloc[t], sl.mark_pnl / T, delta=1e-12)
            checked += 1
        self.assertEqual(checked, n - d - h)

    def test_same_candle_execution_is_rejected(self):
        idx = _grid(10)
        one = pd.Series(1.0, index=idx)
        with self.assertRaises(ValueError):
            targets.tradeable_return(one, one, one, one, d=0, h=4)

    def test_invalid_beta_and_missing_prices_give_nan_not_substitutes(self):
        idx = _grid(12)
        pa = pd.Series(np.linspace(10, 11, 12), index=idx)
        pb = pa.copy()
        side = pd.Series(1.0, index=idx)
        beta = pd.Series([1.0, 0.0, -1.0, np.inf] + [1.0] * 8, index=idx)
        pa_gap = pa.copy()
        pa_gap.iloc[6] = np.nan
        r = targets.tradeable_return(pa_gap, pb, beta, side, d=1, h=2)
        self.assertTrue(np.isfinite(r.iloc[0]))
        self.assertTrue(r.iloc[1:4].isna().all())            # β = 0, β < 0, β = inf
        self.assertTrue(np.isnan(r.iloc[3 + 0]))
        self.assertTrue(np.isnan(r.iloc[5]))                 # needs price at t+d = 6 (missing)
        self.assertTrue(np.isnan(r.iloc[3]))

    def test_flat_side_is_zero(self):
        idx = _grid(10)
        p = pd.Series(np.linspace(1, 2, 10), index=idx)
        r = targets.tradeable_return(p, p, pd.Series(1.0, index=idx), pd.Series(0.0, index=idx), d=1, h=1)
        self.assertTrue((r.iloc[:8] == 0.0).all())

    def test_side_from_zscore(self):
        z = pd.Series([-2.5, 0.5, 2.0, np.nan], index=_grid(4))
        self.assertEqual(list(targets.side_from_zscore(z, entry_threshold=2.0)), [1.0, 0.0, -1.0, 0.0])

    def test_retired_labels_are_refused(self):
        with self.assertRaises(targets.RetiredLabelError):
            targets.assert_label_admissible("spread_forward_4h")
        targets.assert_label_admissible("forward_return_4h")


class HalfLifeTests(SimpleTestCase):
    def _series(self, phi, n=20000, seed=3):
        g = np.random.default_rng(seed)
        x = np.zeros(n)
        e = g.normal(0, 0.01, n)
        for t in range(1, n):
            x[t] = phi * x[t - 1] + e[t]
        return pd.Series(x, index=_grid(n))

    def test_recovers_known_ar1_half_life(self):
        true_hl = 13.5
        res = ar1_half_life(self._series(math.exp(-math.log(2) / true_hl)), candle_hours=1.0, min_obs=100)
        self.assertTrue(res.ok)
        self.assertLess(abs(res.value.half_life_candles / true_hl - 1), 0.08)

    def test_exact_formula(self):
        res = ar1_half_life(self._series(0.9), candle_hours=1.0, min_obs=100)
        hl = res.value
        self.assertAlmostEqual(hl.half_life_candles, -math.log(2) / math.log(hl.phi), places=12)

    def test_unit_conversion(self):
        s = self._series(0.9)
        res1 = ar1_half_life(s, candle_hours=1.0, min_obs=100).value
        res4 = ar1_half_life(s, candle_hours=4.0, min_obs=100).value
        self.assertAlmostEqual(res4.half_life_hours, 4 * res1.half_life_candles, places=10)

    def test_explosive_rejected(self):
        res = ar1_half_life(self._series(1.002, n=3000), candle_hours=1.0, min_obs=100)
        self.assertIs(res.status, Status.NOT_MEAN_REVERTING)

    def test_negative_phi_invalid(self):
        res = ar1_half_life(self._series(-0.5), candle_hours=1.0, min_obs=100)
        self.assertIs(res.status, Status.FAIL)

    def test_insufficient_data_untestable_no_fallback(self):
        res = ar1_half_life(self._series(0.9, n=50), candle_hours=1.0, min_obs=100)
        self.assertIs(res.status, Status.UNTESTABLE)
        self.assertIsNone(res.value)

    def test_gap_pairs_excluded(self):
        s = self._series(0.9, n=500)
        s.iloc[100:110] = np.nan
        res = ar1_half_life(s, candle_hours=1.0, min_obs=100).value
        self.assertEqual(res.n_obs, 499 - 11)   # 10 missing values remove 11 adjacent pairs


class AccountingTests(SimpleTestCase):
    def test_beta_domain(self):
        for b in (0.0, -0.5, -1.0, float("nan"), float("inf"), None):
            self.assertIs(accounting.leg_notionals(1000.0, b).status, Status.SKIP, msg=repr(b))

    def test_leg_split_uses_abs_beta_and_sums_to_total(self):
        for b in (0.01, 0.7, 1.0, 3.0, 250.0):
            na, nb = accounting.leg_notionals(1000.0, b).value
            self.assertAlmostEqual(na + nb, 1000.0, places=9)
            self.assertAlmostEqual(nb / na, b, places=9)

    def test_checked_position_matches_pnl(self):
        res = accounting.open_position_checked("SHORT_SPREAD", 1.7, 500.0, 2.0, 3.0, pd.Timestamp("2024-01-01", tz="UTC"))
        ref = open_pair_position("SHORT_SPREAD", 1.7, 500.0, 2.0, 3.0, pd.Timestamp("2024-01-01", tz="UTC"))
        self.assertEqual(res.value, ref)
        self.assertIs(accounting.open_position_checked("LONG_SPREAD", -1.0, 500.0, 2.0, 3.0, None).status,
                      Status.SKIP)

    def test_minimum_gross_notional(self):
        m_a, m_b = 5.0, 10.0
        best = accounting.minimum_gross_notional(m_b / m_a, m_a, m_b, buffer_kappa=0.0).value
        self.assertAlmostEqual(best, m_a + m_b, places=9)
        for b in (0.3, 1.0, 4.0):
            self.assertGreaterEqual(accounting.minimum_gross_notional(b, m_a, m_b, buffer_kappa=0.0).value,
                                    m_a + m_b - 1e-9)

    def test_executable_quantities_floor_and_min_notional(self):
        pos = open_pair_position("LONG_SPREAD", 1.0, 30.0, 2.0, 3.0, None)   # N_A = N_B = 15
        rules = AmountRules(step=1.0, min_qty=1.0)
        res = accounting.executable_quantities(pos, rules_a=rules, rules_b=rules,
                                               min_notional_a=5.0, min_notional_b=5.0)
        self.assertTrue(res.ok)
        self.assertEqual(res.value.qty_a, 7.0)      # 7.5 floored
        self.assertEqual(res.value.qty_b, 5.0)
        res2 = accounting.executable_quantities(pos, rules_a=rules, rules_b=rules,
                                                min_notional_a=15.0, min_notional_b=5.0)
        self.assertIs(res2.status, Status.SKIP)     # 7 × 2 = 14 < 15 after flooring

    def test_kelly_negative_expectancy_gets_no_position(self):
        res = accounting.kelly_fraction(0.6, 1.0, 3.0)
        self.assertIs(res.status, Status.NO_EDGE)
        self.assertLessEqual(res.value.fraction, 0.0)

    def test_kelly_formula(self):
        res = accounting.kelly_fraction(0.69, 1.0, 1.0)
        self.assertAlmostEqual(res.value.fraction, 0.69 - 0.31, places=12)
        for bad_args in ((1.0, 1, 1), (0.5, 0, 1), (0.5, 1, -1), (float("nan"), 1, 1)):
            self.assertIs(accounting.kelly_fraction(*bad_args).status, Status.FAIL)


class CointegrationTests(SimpleTestCase):
    def test_cointegrated_pair_rejects_null(self):
        x, y = _ou(4000, 13.5, seed=1)
        res = cointegration.engle_granger(pd.Series(x), pd.Series(y), min_obs=500, maxlag=None, autolag="aic")
        self.assertTrue(res.ok)
        self.assertLess(res.value.pvalue, 0.01)
        self.assertAlmostEqual(res.value.beta, 1.3, delta=0.1)

    def test_gap_in_segment_is_untestable(self):
        x, y = _ou(1000, 13.5, seed=2)
        x[500] = np.nan
        res = cointegration.engle_granger(pd.Series(x), pd.Series(y), min_obs=100, maxlag=None, autolag="aic")
        self.assertIs(res.status, Status.UNTESTABLE)

    def test_eg_failure_never_becomes_adf(self):
        x, y = _ou(1000, 13.5, seed=2)
        with mock.patch("statsmodels.tsa.stattools.coint", side_effect=np.linalg.LinAlgError("boom")), \
             mock.patch("statsmodels.tsa.stattools.adfuller") as adf:
            res = cointegration.engle_granger(pd.Series(x), pd.Series(y), min_obs=100, maxlag=None, autolag="aic")
        self.assertIs(res.status, Status.UNTESTABLE)
        self.assertIsNone(res.value)
        adf.assert_not_called()

    def test_constant_and_short_inputs_untestable(self):
        self.assertIs(cointegration.engle_granger(pd.Series([1.0] * 600), pd.Series(np.arange(600.0)),
                                                  min_obs=100, maxlag=None, autolag="aic").status, Status.UNTESTABLE)
        self.assertIs(cointegration.engle_granger(pd.Series([1.0, 2.0]), pd.Series([1.0, 3.0]),
                                                  min_obs=100, maxlag=None, autolag="aic").status, Status.UNTESTABLE)

    def test_persistence_uses_frozen_coefficients(self):
        x, y = _ou(3000, 13.5, seed=4)
        res = cointegration.persistence_adf(pd.Series(x[2000:]), pd.Series(y[2000:]), alpha_hat=0.2, beta_hat=1.3,
                                            min_obs=500, maxlag=20, autolag="AIC")
        self.assertTrue(res.ok)
        self.assertLess(res.value, 0.01)
        self.assertIs(cointegration.persistence_adf(pd.Series(x), pd.Series(y), alpha_hat=float("nan"),
                                                    beta_hat=1.3, min_obs=10, maxlag=20, autolag="AIC").status,
                      Status.UNTESTABLE)

    def test_duplicate_orientations_cannot_coexist(self):
        with self.assertRaises(cointegration.DuplicateRelationshipError):
            cointegration.assert_no_duplicate_relationships([("MINA", "ONG"), ("ONG", "MINA")])
        cointegration.assert_no_duplicate_relationships([("MINA", "ONG"), ("DODO", "FIDA")])

    def test_shared_leg_map(self):
        m = cointegration.shared_leg_map([("A", "B"), ("A", "C"), ("B", "C")])
        self.assertEqual(len(m["A"]), 2)

    def test_eg_sanity_on_independent_random_walks(self):
        """Implementation sanity only (not a calibration gate): 30 independent
        random-walk pairs should rarely reject at 5 %."""
        rejections = 0
        for seed in range(30):
            g = np.random.default_rng(1000 + seed)
            x, y = np.cumsum(g.normal(0, 0.01, 2000)), np.cumsum(g.normal(0, 0.01, 2000))
            r = cointegration.engle_granger(pd.Series(x), pd.Series(y), min_obs=500, maxlag=None, autolag="aic")
            rejections += int(r.ok and r.value.pvalue < 0.05)
        self.assertLessEqual(rejections, 6)


# ── Layer C: causality / leakage ─────────────────────────────────────────────

class CausalityTests(SimpleTestCase):
    def test_future_prices_cannot_change_a_generated_signal(self):
        g = np.random.default_rng(11)
        n = 1200
        s = pd.Series(np.cumsum(g.normal(0, 1, n)), index=_grid(n))
        z1 = time_grid.rolling_zscore(s, window_hours=504, min_periods=168, clip=5.0, max_gap_hours=0)
        s2 = s.copy()
        s2.iloc[900:] += 50.0
        z2 = time_grid.rolling_zscore(s2, window_hours=504, min_periods=168, clip=5.0, max_gap_hours=0)
        pd.testing.assert_series_equal(z1.iloc[:900], z2.iloc[:900])
        self.assertFalse(np.allclose(z1.iloc[900:], z2.iloc[900:], equal_nan=True))   # positive control

    def test_target_ignores_cutoff_candle_and_far_future(self):
        g = np.random.default_rng(12)
        n, d, h = 40, 1, 3
        idx = _grid(n)
        pa = pd.Series(np.exp(np.cumsum(g.normal(0, 0.02, n))), index=idx)
        pb = pd.Series(np.exp(np.cumsum(g.normal(0, 0.02, n))), index=idx)
        beta, side = pd.Series(1.2, index=idx), pd.Series(1.0, index=idx)
        r = targets.tradeable_return(pa, pb, beta, side, d=d, h=h)
        t = 10
        pa2 = pa.copy()
        pa2.iloc[t] *= 1.5                      # the cutoff candle's own close
        pa2.iloc[t + d + h + 1:] *= 2.0         # everything after the exit
        r2 = targets.tradeable_return(pa2, pb, beta, side, d=d, h=h)
        self.assertEqual(r.iloc[t], r2.iloc[t])

    def test_zscore_gap_tolerance(self):
        s = pd.Series(np.arange(300, dtype=float), index=_grid(300))
        s.iloc[250:253] = np.nan
        z0 = time_grid.rolling_zscore(s, window_hours=100, min_periods=50, clip=5.0, max_gap_hours=0)
        z5 = time_grid.rolling_zscore(s, window_hours=100, min_periods=50, clip=5.0, max_gap_hours=5)
        self.assertTrue(z0.iloc[260:300].isna().all())
        self.assertTrue(z5.iloc[260:300].notna().all())


# ── Layer D: execution / costs / metrics ─────────────────────────────────────

class CostTests(SimpleTestCase):
    def setUp(self):
        self.t0 = pd.Timestamp("2024-01-01", tz="UTC")
        self.t1 = self.t0 + pd.Timedelta(hours=5)
        self.pos = open_pair_position("LONG_SPREAD", 1.5, 1000.0, 10.0, 4.0, self.t0)   # B is short

    def test_complete_costs_match_pnl(self):
        cm = costs.CostModel(0.001, 0.0005, 0.0, 0.00002)
        res = costs.evaluate_slice(self.pos, 1.0, 10.5, 4.1, self.t1, cm)
        ref, _ = close_pair_slice(self.pos, 1.0, 10.5, 4.1, self.t1, PairCosts(0.001, 0.0005, 0.0, 0.00002))
        self.assertTrue(res.ok)
        self.assertAlmostEqual(res.value.net_pnl, ref.net_pnl, places=12)

    def test_missing_short_borrow_is_reported_not_zeroed(self):
        cm = costs.CostModel(0.001, 0.0005, 0.0, None)
        res = costs.evaluate_slice(self.pos, 1.0, 10.5, 4.1, self.t1, cm)
        self.assertIs(res.status, Status.COST_UNAVAILABLE)
        self.assertIsNone(res.value.net_pnl)
        r_star = res.value.breakeven_borrow_rate_hourly
        at_be, _ = close_pair_slice(self.pos, 1.0, 10.5, 4.1, self.t1, PairCosts(0.001, 0.0005, 0.0, r_star))
        self.assertAlmostEqual(at_be.net_pnl, 0.0, places=9)

    def test_long_leg_borrow_unknown_is_irrelevant(self):
        cm = costs.CostModel(0.001, 0.0005, None, 0.00001)    # A is long: its borrow rate never applies
        self.assertTrue(costs.evaluate_slice(self.pos, 1.0, 10.5, 4.1, self.t1, cm).ok)

    def test_missing_fee_gives_no_net(self):
        res = costs.evaluate_slice(self.pos, 0.5, 10.5, 4.1, self.t1, costs.CostModel(None, 0.0005, 0.0, 0.0))
        self.assertIs(res.status, Status.COST_UNAVAILABLE)
        self.assertIsNone(res.value.net_pnl)


class MetricTests(SimpleTestCase):
    def test_profit_factor_in_dollars_and_no_losses(self):
        self.assertAlmostEqual(metrics.profit_factor_dollars([30.0, -10.0, 5.0]).value.value, 3.5)
        pf = metrics.profit_factor_dollars([1.0, 2.0]).value
        self.assertTrue(pf.no_losses and math.isinf(pf.value))
        self.assertIs(metrics.profit_factor_dollars([]).status, Status.UNTESTABLE)

    def test_wfe_fails_closed(self):
        self.assertAlmostEqual(metrics.walk_forward_efficiency(0.5, 1.0).value, 0.5)
        for oos, is_ in ((0.5, 0.0), (0.5, -1.0), (float("nan"), 1.0), (0.5, float("nan"))):
            self.assertIs(metrics.walk_forward_efficiency(oos, is_).status, Status.FAIL)

    def test_missing_criterion_fails(self):
        passed, out = metrics.evaluate_criteria([
            metrics.Criterion("win_rate", 0.7, ">=", 0.55),
            metrics.Criterion("wfe", float("nan"), ">=", 0.5),
        ])
        self.assertFalse(passed)
        self.assertFalse(out[1].passed)
        self.assertFalse(metrics.evaluate_criteria([])[0])

    def test_mark_to_market_drawdown_sees_intratrade_loss(self):
        idx = _grid(6)
        close_a = pd.Series([10, 10, 8, 9, 10, 10], index=idx, dtype=float)
        close_b = pd.Series([5, 5, 5, 5, 5, 5], index=idx, dtype=float)
        iv = metrics.HeldInterval(idx[1], idx[4], 10.0, 0.0, 10.0, 5.0)    # long 10 A from 10
        eq = metrics.mark_to_market_equity(1000.0, close_a, close_b, [iv], [(idx[4], 0.0)])
        self.assertAlmostEqual(eq.iloc[2], 980.0)
        dd = metrics.max_drawdown(eq).value
        self.assertAlmostEqual(dd, -0.02)                  # realised-only equity would show 0 drawdown
