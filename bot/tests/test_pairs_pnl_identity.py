# claude code changed: rewritten for Phase 1 (PAIRS_PNL_SPECIFICATION.md). The
# previous version tested the interim audit-C-01 formula (fixed quantities +
# a flat 0.3% cost), which has been deleted — not preserved for compatibility.
# These are ENGINE-level tests: they prove EntryExitEngine books exactly the
# specification's P&L end-to-end (real prices joined, sizing, partial exits,
# costs, borrow), with expected values written out as plain arithmetic from the
# spec's formulas — never obtained by calling bot/pairs/pnl.py. The component
# itself is tested independently in test_pairs_pnl_component.py.

import math
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.pairs.kalman_online import RollingZScore, kalman_step
from bot.pairs.pnl import open_pair_position
from bot.research.entry_exit_engine import (
    EntryExitEngine,
    PLACEHOLDER_WIN_RATE_SIZING_IRRELEVANT,
    TradeRecord,
)
from bot.research.kalman_filter_engine import KalmanState


def _two_candle_trade_csv(path, zscores, spreads, close_a, close_b, beta=1.5):
    """12 flat candles, then (claude code changed: Stage 2D, d = 1) an entry
    DECISION (row 12), its FILL candle (row 13, priced close_a[0]/close_b[0]),
    a STOPLOSS decision (row 14) and the exit FILL candle (row 15, priced
    close_a[1]/close_b[1]); row 16 trails. Entry and exit prices are therefore
    the same as the old same-candle scenario; the hold is now 2 hours."""
    n_flat = 12
    z_entry, z_exit = zscores
    sgn = np.sign(z_entry)
    pd.DataFrame({
        "timestamp": list(pd.date_range("2024-01-01", periods=n_flat + 5, freq="h", tz="UTC")),
        "kalman_zscore":       [0.1 * sgn] * n_flat + [z_entry, z_entry, z_exit, 0.1 * sgn, 0.1],
        "kalman_zscore_lag1":  [0.1 * sgn] * n_flat + [0.1 * sgn, z_entry, z_entry, z_exit, 0.1 * sgn],
        "pair_signal_dynamic": [0.05] * n_flat + [-sgn * 0.5, -sgn * 0.5, -sgn * 0.5, 0.05, 0.05],
        "kalman_beta":         [beta] * (n_flat + 5),
        "kalman_spread":       [0.0] * n_flat + [spreads[0], spreads[0], spreads[1], spreads[2], spreads[2]],
        "prediction_error":    [0.005] * (n_flat + 5),
        "beta_uncertainty":    [0.01] * (n_flat + 5),
        "close_a":             [close_a[0]] * (n_flat + 3) + [close_a[1], close_a[2]],
        "close_b":             [close_b[0]] * (n_flat + 3) + [close_b[1], close_b[2]],
    }).to_csv(path, index=False)


class EngineBooksSpecificationPnLTest(SimpleTestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run(self, engine_kwargs=None, **csv_kwargs):
        csv = Path(self.tmp_dir) / "AVAX_USDT_ATOM_USDT_kalman.csv"
        _two_candle_trade_csv(csv, **csv_kwargs)
        engine = EntryExitEngine(output_dir=self.tmp_dir, validated_win_rate=PLACEHOLDER_WIN_RATE_SIZING_IRRELEVANT,
                                 **(engine_kwargs or {}))
        trade_log, summary, _ = engine.run(kalman_csv=str(csv))
        self.assertEqual(len(trade_log), 1)
        return engine, trade_log.iloc[0], summary.iloc[0]

    def test_long_spread_matches_hand_formula(self):
        engine, t, _ = self._run(
            engine_kwargs={"borrow_rate_hourly_a": 0.0, "borrow_rate_hourly_b": 0.0001},
            zscores=(-2.2, -4.0), spreads=(-0.01, -0.05, -0.05), close_a=(10.0, 11.0, 11.0), close_b=(20.0, 21.0, 21.0))
        f, s, T = engine.fee_rate, engine.slippage_rate, t["capital_usdt"]
        n_a = T / 2.5
        qa, qb = n_a / 10.0, 1.5 * n_a / 20.0                               # long A, short B (units)
        mark = qa * 1.0 - qb * 1.0
        slip = s * (qa * (10 + 11) + qb * (20 + 21))
        fees = f * (qa * 10 * (1 + s) + qb * 20 * (1 - s) + qa * 11 * (1 - s) + qb * 21 * (1 + s))
        borrow = qb * 0.0001 * 2 * 21 * (1 + s)                            # claude code changed: Stage 2D — fill t+1 to fill u+1: held 2h -> 2 accrual hours
        self.assertEqual(t["direction"], "LONG_SPREAD")
        self.assertAlmostEqual(t["qty_a"], qa, places=6)
        self.assertAlmostEqual(t["qty_b"], -qb, places=6)
        self.assertAlmostEqual(t["mark_pnl_usdt"], mark, places=5)
        self.assertAlmostEqual(t["slippage_usdt"], slip, places=5)
        self.assertAlmostEqual(t["fee_usdt"], fees, places=5)
        self.assertAlmostEqual(t["borrow_usdt"], borrow, places=5)
        self.assertAlmostEqual(t["net_pnl_pct"], (mark - slip - fees - borrow) / T, places=6)

    def test_short_spread_matches_hand_formula(self):
        engine, t, _ = self._run(
            engine_kwargs={"borrow_rate_hourly_a": 0.0001, "borrow_rate_hourly_b": 0.0},
            zscores=(2.2, 4.0), spreads=(0.01, 0.05, 0.05), close_a=(10.0, 11.0, 11.0), close_b=(20.0, 21.0, 21.0))
        f, s, T = engine.fee_rate, engine.slippage_rate, t["capital_usdt"]
        n_a = T / 2.5
        qa, qb = n_a / 10.0, 1.5 * n_a / 20.0                               # short A, long B
        mark = -qa * 1.0 + qb * 1.0
        fees = f * (qa * 10 * (1 - s) + qb * 20 * (1 + s) + qa * 11 * (1 + s) + qb * 21 * (1 - s))
        slip = s * (qa * 21 + qb * 41)
        borrow = qa * 0.0001 * 2 * 11 * (1 + s)                            # claude code changed: Stage 2D — held 2h
        self.assertEqual(t["direction"], "SHORT_SPREAD")
        self.assertAlmostEqual(t["net_pnl_pct"], (mark - slip - fees - borrow) / T, places=6)

    def test_kalman_spread_does_not_enter_pnl(self):
        """Flat prices + a huge favourable spread move: the old formula booked a
        large win here. Correct result: exactly −(fees + slippage)."""
        engine, t, _ = self._run(zscores=(-2.2, -4.0), spreads=(-0.30, 0.30, 0.30),
                                 close_a=(10.0, 10.0, 10.0), close_b=(20.0, 20.0, 20.0))
        self.assertAlmostEqual(t["gross_pnl_pct"], 0.0, places=9)
        self.assertAlmostEqual(t["net_pnl_pct"], -2 * (engine.fee_rate + engine.slippage_rate), places=6)

    def test_negative_beta_is_skipped_not_traded(self):
        """claude code changed: Stage 2D (Stage 2C §11) — β ≤ 0 is outside the
        executable domain (same-side legs, net exposure +T). It used to open a
        bounded same-side position; it must now produce no trade at all."""
        csv = Path(self.tmp_dir) / "AVAX_USDT_ATOM_USDT_kalman.csv"
        _two_candle_trade_csv(csv, zscores=(-2.2, -4.0), spreads=(-0.01, -0.05, -0.05),
                              close_a=(10.0, 11.0, 11.0), close_b=(20.0, 21.0, 21.0), beta=-0.5)
        engine = EntryExitEngine(output_dir=self.tmp_dir, validated_win_rate=PLACEHOLDER_WIN_RATE_SIZING_IRRELEVANT)
        trade_log, _, _ = engine.run(kalman_csv=str(csv))
        self.assertEqual(len(trade_log), 0)

    def test_borrow_not_modelled_is_flagged_not_hidden(self):
        engine, t, summary = self._run(zscores=(-2.2, -4.0), spreads=(-0.01, -0.05, -0.05),
                                       close_a=(10.0, 11.0, 11.0), close_b=(20.0, 21.0, 21.0))
        self.assertFalse(engine.borrow_cost_modeled)
        self.assertFalse(bool(summary["borrow_cost_modeled"]))
        self.assertEqual(t["borrow_usdt"], 0.0)
        engine2, t2, summary2 = self._run(
            engine_kwargs={"borrow_rate_hourly_a": 0.0001, "borrow_rate_hourly_b": 0.0001},
            zscores=(-2.2, -4.0), spreads=(-0.01, -0.05, -0.05), close_a=(10.0, 11.0, 11.0), close_b=(20.0, 21.0, 21.0))
        self.assertTrue(bool(summary2["borrow_cost_modeled"]))
        self.assertGreater(t2["borrow_usdt"], 0.0)


class EnginePartialExitTest(SimpleTestCase):
    """Partial + final exit through the engine's own methods; totals must equal
    the spec's hand-written slice arithmetic (PAIRS_PNL_SPECIFICATION.md §9)."""

    def test_partial_then_final(self):
        tmp = tempfile.mkdtemp()
        try:
            engine = EntryExitEngine(output_dir=tmp, validated_win_rate=PLACEHOLDER_WIN_RATE_SIZING_IRRELEVANT,
                                     borrow_rate_hourly_a=0.0, borrow_rate_hourly_b=0.0001)
            t0 = pd.Timestamp("2024-01-01", tz="UTC")
            pos = open_pair_position("LONG_SPREAD", 1.5, 1000.0, 10.0, 20.0, t0)
            engine.current_trade = TradeRecord(
                trade_id=1, entry_timestamp=t0, direction="LONG_SPREAD", position_usdt=1000.0,
                leg_a_usdt=400.0, leg_b_usdt=600.0, capital_usdt=1000.0, pair_position=pos,
            )
            engine._execute_partial_exit(t0 + pd.Timedelta(hours=2), 0.5, 0.0, 2, 11.0, 21.0)
            engine._execute_full_exit(t0 + pd.Timedelta(hours=7), -4.0, 0.0, "STOPLOSS", 12.0, 19.0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        trade = engine.completed_trades[-1]
        f, s, frac = engine.fee_rate, engine.slippage_rate, engine.exit_partial_frac
        self.assertEqual(frac, 0.5, "hand arithmetic below assumes the default 50% partial")
        mark = 20 * 1.0 + (-15) * 1.0 + 20 * 2.0 + (-15) * (-1.0)
        slip = s * (40 * 10 + 30 * 20 + 20 * 11 + 15 * 21 + 20 * 12 + 15 * 19)
        fees = f * (40 * 10 * (1 + s) + 30 * 20 * (1 - s)
                    + 20 * 11 * (1 - s) + 15 * 21 * (1 + s)
                    + 20 * 12 * (1 - s) + 15 * 19 * (1 + s))
        borrow = 15 * 0.0001 * 2 * 21 * (1 + s) + 15 * 0.0001 * 7 * 19 * (1 + s)
        net = mark - slip - fees - borrow
        self.assertAlmostEqual(trade.mark_pnl_usdt, mark, places=9)
        self.assertAlmostEqual(trade.slippage_usdt, slip, places=9)
        self.assertAlmostEqual(trade.fee_usdt, fees, places=9)
        self.assertAlmostEqual(trade.borrow_usdt, borrow, places=9)
        self.assertAlmostEqual(trade.net_pnl_usdt, net, places=9)
        self.assertAlmostEqual(trade.net_pnl_pct, net / 1000.0, places=12)
        self.assertIsNone(trade.pair_position)


class RandomWalkNullCalibrationTest(SimpleTestCase):
    """The real Kalman signal pipeline + engine on two INDEPENDENT random walks:
    mean gross P&L must be ~0 and the win rate must not look like an edge. The
    deleted spread-difference formula showed +1.2% to +1.5%/trade at 74–81% here."""

    N = 12000

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _kalman_csv_from_random_walks(self, seed):
        rng = np.random.default_rng(seed)
        pa = np.exp(np.cumsum(rng.normal(0, 0.01, self.N)))
        pb = np.exp(np.cumsum(rng.normal(0, 0.01, self.N)))
        state, roller, prev_z, rows = KalmanState(theta=np.array([1.0, 0.0]), P=np.eye(2)), RollingZScore(), None, []
        for i in range(self.N):
            state, nat = kalman_step(state, pa[i], pb[i], i)
            z = None if nat.is_warmup else roller.update(nat.spread)
            rows.append({
                "kalman_zscore": z, "kalman_zscore_lag1": prev_z,
                "pair_signal_dynamic": None if z is None else -z,
                "kalman_beta": nat.beta_pred, "kalman_spread": nat.spread,
                "prediction_error": nat.prediction_error, "beta_uncertainty": nat.beta_uncertainty,
                "is_warmup": nat.is_warmup, "close_a": pa[i], "close_b": pb[i],
            })
            if not nat.is_warmup:
                prev_z = z
        df = pd.DataFrame(rows)
        df.insert(0, "timestamp", pd.date_range("2020-01-01", periods=self.N, freq="h", tz="UTC"))
        path = Path(self.tmp_dir) / f"X_USDT_Y_USDT_kalman_{seed}.csv"
        df.dropna().to_csv(path, index=False)
        return path

    def test_no_edge_on_independent_random_walks(self):
        gross = []
        for seed in (0, 1):
            csv = self._kalman_csv_from_random_walks(seed)
            engine = EntryExitEngine(
                output_dir=self.tmp_dir, validated_win_rate=PLACEHOLDER_WIN_RATE_SIZING_IRRELEVANT,
                pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT",
                disable_target_exit=True, exit_time_stop_hours=4,
            )
            trade_log, _, _ = engine.run(kalman_csv=str(csv))
            gross.extend(trade_log["gross_pnl_pct"].tolist())
        gross = np.array(gross)
        self.assertGreater(len(gross), 200, "calibration needs a meaningful number of trades")
        se = gross.std(ddof=1) / math.sqrt(len(gross))
        self.assertLess(abs(gross.mean()), 3.5 * se, f"mean gross {gross.mean():.4%} is not ~0 on a no-edge null")
        self.assertLess((gross > 0).mean(), 0.60, "gross win rate on unrelated random walks must not look like an edge")
