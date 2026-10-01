# claude code changed: new file — Phase 1 of the pairs quantitative ground-truth
# work (PAIRS_PNL_SPECIFICATION.md). Written BEFORE bot/pairs/pnl.py existed.
# Every expected number below is either a literal from the specification's §11
# hand calculation or is re-derived here with plain arithmetic — never by
# calling back into the component under test — so a wrong formula in the
# component cannot also be the source of its own expected value.

import math

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.pairs.pnl import PairCosts, close_pair_slice, open_pair_position

T0 = pd.Timestamp("2024-01-01 00:00", tz="UTC")


def _h(hours):
    return T0 + pd.Timedelta(hours=hours)


class HandCalculatedTwoCandleTest(SimpleTestCase):
    """PAIRS_PNL_SPECIFICATION.md §11, verbatim."""

    def setUp(self):
        self.costs = PairCosts(fee_rate=0.001, slippage_rate=0.0005, borrow_rate_hourly_a=0.0, borrow_rate_hourly_b=0.0001)
        self.pos = open_pair_position("LONG_SPREAD", beta=1.5, gross_notional=1000.0, mark_a=10.0, mark_b=20.0, entry_time=T0)
        self.s, self.rest = close_pair_slice(self.pos, 1.0, mark_a=11.0, mark_b=21.0, exit_time=_h(5), costs=self.costs)

    def test_quantities_and_notionals(self):
        self.assertAlmostEqual(self.pos.notional_a, 400.0, places=9)
        self.assertAlmostEqual(self.pos.notional_b, 600.0, places=9)
        self.assertAlmostEqual(self.pos.qty_a, 40.0, places=9)
        self.assertAlmostEqual(self.pos.qty_b, -30.0, places=9)
        self.assertAlmostEqual(self.pos.capital, 1000.0, places=9)

    def test_every_pnl_component(self):
        self.assertAlmostEqual(self.s.mark_pnl, 10.0, places=9)
        self.assertAlmostEqual(self.s.slippage_cost, 1.035, places=9)
        self.assertAlmostEqual(self.s.fee_cost, 2.069995, places=9)
        self.assertAlmostEqual(self.s.borrow_cost, 0.3151575, places=9)
        self.assertAlmostEqual(self.s.net_pnl, 6.5798475, places=9)
        self.assertAlmostEqual(self.s.roc, 0.0065798475, places=12)
        self.assertIsNone(self.rest)

    def test_entry_and_exit_fill_notionals(self):
        self.assertAlmostEqual(self.s.entry_fill_notional, 40 * 10.005 + 30 * 19.99, places=9)
        self.assertAlmostEqual(self.s.exit_fill_notional, 40 * 10.9945 + 30 * 21.0105, places=9)


class DirectionTest(SimpleTestCase):

    def test_short_spread_hand_calculated(self):
        # SHORT_SPREAD, same prices: short A (borrowed), long B.
        costs = PairCosts(fee_rate=0.001, slippage_rate=0.0005, borrow_rate_hourly_a=0.0001, borrow_rate_hourly_b=0.0)
        pos = open_pair_position("SHORT_SPREAD", beta=1.5, gross_notional=1000.0, mark_a=10.0, mark_b=20.0, entry_time=T0)
        self.assertAlmostEqual(pos.qty_a, -40.0, places=9)
        self.assertAlmostEqual(pos.qty_b, 30.0, places=9)
        s, _ = close_pair_slice(pos, 1.0, 11.0, 21.0, _h(5), costs)
        fees = 0.001 * (40 * 9.995 + 30 * 20.01 + 40 * 11.0055 + 30 * 20.9895)
        borrow = 40 * 0.0001 * 5 * 11.0055
        self.assertAlmostEqual(s.mark_pnl, -10.0, places=9)
        self.assertAlmostEqual(s.slippage_cost, 1.035, places=9)
        self.assertAlmostEqual(s.fee_cost, fees, places=9)
        self.assertAlmostEqual(s.borrow_cost, borrow, places=9)
        self.assertAlmostEqual(s.net_pnl, -10.0 - 1.035 - fees - borrow, places=9)

    def test_long_and_short_mark_pnl_are_exact_mirrors(self):
        for a1, b1 in [(11.0, 21.0), (9.0, 23.0), (10.5, 18.0)]:
            long_ = open_pair_position("LONG_SPREAD", 1.5, 1000.0, 10.0, 20.0, T0)
            short = open_pair_position("SHORT_SPREAD", 1.5, 1000.0, 10.0, 20.0, T0)
            zero = PairCosts(0.0, 0.0)
            sl, _ = close_pair_slice(long_, 1.0, a1, b1, _h(1), zero)
            ss, _ = close_pair_slice(short, 1.0, a1, b1, _h(1), zero)
            self.assertAlmostEqual(sl.mark_pnl, -ss.mark_pnl, places=9)

    def test_invalid_direction_rejected(self):
        with self.assertRaises(ValueError):
            open_pair_position("LONG", 1.0, 100.0, 1.0, 1.0, T0)


class HedgeRatioSignTest(SimpleTestCase):
    """β > 0: opposite sides. β < 0: same side, bounded. β = 0: no B leg.
    Notionals always sum to T (no explosion near β = −1)."""

    def test_beta_positive_opposite_sides(self):
        pos = open_pair_position("LONG_SPREAD", 0.8, 900.0, 10.0, 20.0, T0)
        self.assertGreater(pos.qty_a, 0)
        self.assertLess(pos.qty_b, 0)
        self.assertAlmostEqual(pos.notional_a, 500.0, places=9)
        self.assertAlmostEqual(pos.notional_b, 400.0, places=9)

    def test_beta_negative_same_side(self):
        pos = open_pair_position("LONG_SPREAD", -0.5, 900.0, 10.0, 20.0, T0)
        self.assertAlmostEqual(pos.qty_a, 60.0, places=9)
        self.assertAlmostEqual(pos.qty_b, 15.0, places=9)   # long B: −d·β·N_A/M_B = −1·(−0.5)·600/20
        pos_s = open_pair_position("SHORT_SPREAD", -0.5, 900.0, 10.0, 20.0, T0)
        self.assertAlmostEqual(pos_s.qty_a, -60.0, places=9)
        self.assertAlmostEqual(pos_s.qty_b, -15.0, places=9)

    def test_beta_near_minus_one_is_bounded(self):
        for beta in (-0.99, -1.0, -1.01, -5.0):
            pos = open_pair_position("LONG_SPREAD", beta, 50.0, 10.0, 20.0, T0)
            self.assertAlmostEqual(pos.notional_a + pos.notional_b, 50.0, places=9)
            self.assertLessEqual(abs(pos.qty_a) * 10.0, 50.0 + 1e-9)
            self.assertLessEqual(abs(pos.qty_b) * 20.0, 50.0 + 1e-9)

    def test_beta_zero_has_no_b_leg(self):
        pos = open_pair_position("LONG_SPREAD", 0.0, 100.0, 10.0, 20.0, T0)
        self.assertEqual(pos.qty_b, 0.0)
        self.assertAlmostEqual(pos.notional_a, 100.0, places=9)

    def test_mark_pnl_tracks_fixed_beta_spread_for_small_moves(self):
        # §2: MarkPnL ≈ N_A · Δ(log A − β log B) for small moves, any sign of β.
        for beta in (1.5, 0.3, -0.5):
            pos = open_pair_position("LONG_SPREAD", beta, 1000.0, 10.0, 20.0, T0)
            a1, b1 = 10.0 * 1.002, 20.0 * 0.999
            s, _ = close_pair_slice(pos, 1.0, a1, b1, _h(1), PairCosts(0.0, 0.0))
            dspread = math.log(a1 / 10.0) - beta * math.log(b1 / 20.0)
            self.assertAlmostEqual(s.mark_pnl / pos.notional_a, dspread, delta=5e-5)

    def test_non_positive_inputs_rejected(self):
        for kwargs in ({"gross_notional": 0.0}, {"mark_a": 0.0}, {"mark_b": -1.0}):
            args = dict(direction="LONG_SPREAD", beta=1.0, gross_notional=100.0, mark_a=1.0, mark_b=1.0, entry_time=T0)
            args.update(kwargs)
            with self.assertRaises(ValueError):
                open_pair_position(**args)


class CostComponentTest(SimpleTestCase):
    """Each cost in isolation, with flat prices so MarkPnL = 0."""

    def _flat(self, costs, hours=5.0, direction="LONG_SPREAD", beta=1.5):
        pos = open_pair_position(direction, beta, 1000.0, 10.0, 20.0, T0)
        s, _ = close_pair_slice(pos, 1.0, 10.0, 20.0, _h(hours), costs)
        return s

    def test_fees_only(self):
        s = self._flat(PairCosts(fee_rate=0.001, slippage_rate=0.0))
        self.assertAlmostEqual(s.fee_cost, 0.001 * 2 * 1000.0, places=9)   # 4 fills, 2T of fill notional
        self.assertAlmostEqual(s.net_pnl, -2.0, places=9)
        self.assertEqual(s.slippage_cost, 0.0)
        self.assertEqual(s.borrow_cost, 0.0)

    def test_slippage_only(self):
        s = self._flat(PairCosts(fee_rate=0.0, slippage_rate=0.0005))
        self.assertAlmostEqual(s.slippage_cost, 0.0005 * 2 * 1000.0, places=9)
        self.assertAlmostEqual(s.net_pnl, -1.0, places=9)
        self.assertAlmostEqual(s.mark_pnl, 0.0, places=12)

    def test_borrow_only_long_spread_borrows_b(self):
        s = self._flat(PairCosts(0.0, 0.0, borrow_rate_hourly_a=0.5, borrow_rate_hourly_b=0.0002), hours=5.5)
        self.assertAlmostEqual(s.borrow_cost, 30 * 0.0002 * 6 * 20.0, places=9)   # ceil(5.5)=6 accrual hours; A is long, its rate is irrelevant

    def test_borrow_minimum_one_hour(self):
        s = self._flat(PairCosts(0.0, 0.0, borrow_rate_hourly_b=0.0002), hours=0.2)
        self.assertAlmostEqual(s.borrow_cost, 30 * 0.0002 * 1 * 20.0, places=9)

    def test_borrow_short_spread_borrows_a(self):
        s = self._flat(PairCosts(0.0, 0.0, borrow_rate_hourly_a=0.0003, borrow_rate_hourly_b=0.5), hours=3.0, direction="SHORT_SPREAD")
        self.assertAlmostEqual(s.borrow_cost, 40 * 0.0003 * 3 * 10.0, places=9)   # B is long, its rate is irrelevant

    def test_borrow_negative_beta(self):
        # β<0 LONG_SPREAD: both legs long -> nothing borrowed. SHORT_SPREAD: both short -> both borrowed.
        c = PairCosts(0.0, 0.0, borrow_rate_hourly_a=0.0001, borrow_rate_hourly_b=0.0002)
        self.assertEqual(self._flat(c, hours=2.0, beta=-0.5).borrow_cost, 0.0)
        s = self._flat(c, hours=2.0, direction="SHORT_SPREAD", beta=-0.5)
        # N_A = 1000/1.5, N_B = 500/1.5 -> |q_A| = 66.667, |q_B| = 16.667
        self.assertAlmostEqual(s.borrow_cost, (1000 / 1.5 / 10) * 0.0001 * 2 * 10.0 + (500 / 1.5 / 20) * 0.0002 * 2 * 20.0, places=9)


class PartialExitTest(SimpleTestCase):

    def test_partial_then_final_hand_calculated_and_conserved(self):
        costs = PairCosts(fee_rate=0.001, slippage_rate=0.0005, borrow_rate_hourly_b=0.0001)
        pos = open_pair_position("LONG_SPREAD", 1.5, 1000.0, 10.0, 20.0, T0)
        s1, rest = close_pair_slice(pos, 0.5, 11.0, 21.0, _h(2), costs)
        self.assertAlmostEqual(rest.qty_a, 20.0, places=9)
        self.assertAlmostEqual(rest.qty_b, -15.0, places=9)
        self.assertAlmostEqual(rest.entry_mark_a, 10.0)
        self.assertEqual(rest.entry_time, T0)
        s2, done = close_pair_slice(rest, 1.0, 12.0, 19.0, _h(7), costs)
        self.assertIsNone(done)

        self.assertAlmostEqual(s1.mark_pnl, 20 * 1.0 + (-15) * 1.0, places=9)
        self.assertAlmostEqual(s2.mark_pnl, 20 * 2.0 + (-15) * (-1.0), places=9)
        fees = 0.001 * (40 * 10.005 + 30 * 19.99                     # entry fills (whole position)
                        + 20 * 11 * 0.9995 + 15 * 21 * 1.0005          # slice 1 exit fills
                        + 20 * 12 * 0.9995 + 15 * 19 * 1.0005)         # slice 2 exit fills
        slip = 0.0005 * (40 * 10 + 30 * 20 + 20 * 11 + 15 * 21 + 20 * 12 + 15 * 19)
        borrow = 15 * 0.0001 * 2 * (21 * 1.0005) + 15 * 0.0001 * 7 * (19 * 1.0005)
        self.assertAlmostEqual(s1.fee_cost + s2.fee_cost, fees, places=9)
        self.assertAlmostEqual(s1.slippage_cost + s2.slippage_cost, slip, places=9)
        self.assertAlmostEqual(s1.borrow_cost + s2.borrow_cost, borrow, places=9)
        self.assertAlmostEqual(s1.capital + s2.capital, 1000.0, places=9)

    def test_fraction_bounds(self):
        pos = open_pair_position("LONG_SPREAD", 1.0, 100.0, 1.0, 1.0, T0)
        for bad in (0.0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                close_pair_slice(pos, bad, 1.0, 1.0, _h(1), PairCosts(0.0, 0.0))

    def test_exit_before_entry_rejected(self):
        pos = open_pair_position("LONG_SPREAD", 1.0, 100.0, 1.0, 1.0, T0)
        with self.assertRaises(ValueError):
            close_pair_slice(pos, 1.0, 1.0, 1.0, T0 - pd.Timedelta(hours=1), PairCosts(0.0, 0.0))


def _rule_trades(log_a, log_b, beta, window=168, entry_z=2.0, hold=12):
    """Independent toy rule (NOT the production strategy): causal rolling z of
    the fixed-β spread; enter against |z| >= entry_z, exit after `hold` candles.
    Returns frictionless mark P&L per trade as a fraction of capital."""
    spread = pd.Series(log_a - beta * log_b)
    z = ((spread - spread.rolling(window).mean()) / spread.rolling(window).std()).to_numpy()
    pa, pb = np.exp(log_a), np.exp(log_b)
    out, i, n = [], window, len(log_a)
    while i < n - hold:
        if np.isfinite(z[i]) and abs(z[i]) >= entry_z:
            d = "SHORT_SPREAD" if z[i] > 0 else "LONG_SPREAD"
            pos = open_pair_position(d, beta, 1000.0, pa[i], pb[i], _h(i))
            s, _ = close_pair_slice(pos, 1.0, pa[i + hold], pb[i + hold], _h(i + hold), PairCosts(0.0, 0.0))
            out.append(s.mark_pnl / 1000.0)
            i += hold
        else:
            i += 1
    return np.array(out)


class NullAndCointegratedTest(SimpleTestCase):
    """The component must show ~zero edge on unrelated random walks and a real,
    positive edge on a synthetic cointegrated (OU) pair — i.e. it measures the
    economics, not the signal."""

    def test_independent_random_walk_null(self):
        pnl = []
        for seed in range(4):
            rng = np.random.default_rng(seed)
            la = np.cumsum(rng.normal(0, 0.01, 20000))
            lb = np.cumsum(rng.normal(0, 0.01, 20000))
            pnl.extend(_rule_trades(la, lb, beta=1.0))
        pnl = np.array(pnl)
        t = pnl.mean() / (pnl.std(ddof=1) / math.sqrt(len(pnl)))
        self.assertGreater(len(pnl), 300)
        self.assertLess(abs(t), 3.5, f"mean {pnl.mean():.4%}, t={t:.2f} on a no-edge null")

    def test_synthetic_ou_cointegrated_pair_shows_edge(self):
        rng = np.random.default_rng(42)
        n, beta, phi, sigma_u = 20000, 1.3, 0.95, 0.01     # AR(1) φ=0.95/h -> half-life ≈ 13.5h
        lb = np.cumsum(rng.normal(0, 0.01, n))
        u = np.zeros(n)
        for t_ in range(1, n):
            u[t_] = phi * u[t_ - 1] + rng.normal(0, sigma_u)
        la = 0.2 + beta * lb + u
        pnl = _rule_trades(la, lb, beta=beta)
        t = pnl.mean() / (pnl.std(ddof=1) / math.sqrt(len(pnl)))
        self.assertGreater(len(pnl), 100)
        self.assertGreater(pnl.mean(), 0.0)
        self.assertGreater(t, 5.0, f"mean {pnl.mean():.4%}, t={t:.2f} — a real OU edge must be detected")
