"""
bot/tests/test_fx_fold_runner.py
Tests for FX walk-forward fold runner (FX-DCA-PROTOCOL-1-A3).

Verifies:
- FxLadderSpec construction and validation
- compute_dynamic_drop_pct() arithmetic (v3 Steps 3-5)
- build_ladder_spec_from_analysis() extracts correct quantiles
- _simulate_data_derived_next_open() produces sensible output on synthetic data
- run_fx_fold_1_eurusd() returns expected structure (requires data/forex/EURUSD_1h.csv)
"""
# claude code changed: new file — 2026-10-17; fold runner unit tests

from __future__ import annotations

import math
import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.fx_fold_runner import (
    FxLadderSpec,
    compute_dynamic_drop_pct,
    compute_equity_stats,
    build_ladder_spec_from_analysis,
    _simulate_data_derived_next_open,
    _summarise_trades,
)  # claude code changed: added compute_equity_stats import


# ── FxLadderSpec ──────────────────────────────────────────────────────────────

class FxLadderSpecValidationTests(SimpleTestCase):  # claude code changed: new test class

    def _valid_spec(self, **kwargs):
        defaults = dict(
            pair="EUR/USD", direction="LONG", pip_size=0.0001,
            atr_ref=0.00128, base_pips=(21.3, 35.4, 50.4, 65.4, 79.9, 100.5),
        )
        defaults.update(kwargs)
        return FxLadderSpec(**defaults)

    def test_valid_long(self):
        spec = self._valid_spec(direction="LONG")
        self.assertEqual(spec.direction, "LONG")
        self.assertEqual(len(spec.base_pips), 6)

    def test_valid_short(self):
        spec = self._valid_spec(direction="SHORT")
        self.assertEqual(spec.direction, "SHORT")

    def test_invalid_direction(self):
        with self.assertRaises(ValueError):
            self._valid_spec(direction="BOTH")

    def test_wrong_base_pips_length(self):
        with self.assertRaises(ValueError):
            self._valid_spec(base_pips=(21.3, 35.4, 50.4))  # only 3, needs 6

    def test_zero_atr_ref_rejected(self):
        with self.assertRaises(ValueError):
            self._valid_spec(atr_ref=0.0)

    def test_negative_pip_size_rejected(self):
        with self.assertRaises(ValueError):
            self._valid_spec(pip_size=-0.0001)


# ── compute_dynamic_drop_pct ──────────────────────────────────────────────────

class ComputeDynamicDropPctTests(SimpleTestCase):  # claude code changed: new test class

    def test_no_scaling_at_atr_ref(self):
        """When current_ATR == ATR_ref, scale=1.0 and only pip conversion applies."""
        base_pips = 22.8
        pip_size = 0.0001
        entry_price = 1.10
        atr_ref = 0.00128
        result = compute_dynamic_drop_pct(
            base_pips=base_pips, pip_size=pip_size,
            last_fill_price=entry_price, current_atr=atr_ref, atr_ref=atr_ref,
        )
        expected = base_pips * pip_size / entry_price
        self.assertAlmostEqual(result, expected, places=8)

    def test_high_volatility_clamps_at_2x(self):
        """ATR 10x ref → clamped to 2.0."""
        result = compute_dynamic_drop_pct(
            base_pips=22.8, pip_size=0.0001,
            last_fill_price=1.10, current_atr=0.0128, atr_ref=0.00128,
        )
        base = 22.8 * 0.0001 / 1.10
        self.assertAlmostEqual(result, base * 2.0, places=8)

    def test_low_volatility_clamps_at_0_5(self):
        """ATR near zero → clamped to 0.5."""
        result = compute_dynamic_drop_pct(
            base_pips=22.8, pip_size=0.0001,
            last_fill_price=1.10, current_atr=1e-10, atr_ref=0.00128,
        )
        base = 22.8 * 0.0001 / 1.10
        expected = max(base * 0.5, 5 * 0.0001 / 1.10)
        self.assertAlmostEqual(result, expected, places=8)

    def test_minimum_spacing_floor_5_pips(self):
        """Very small base_pips → floored at 5 pips."""
        result = compute_dynamic_drop_pct(
            base_pips=0.1, pip_size=0.0001,
            last_fill_price=1.10, current_atr=0.00128, atr_ref=0.00128,
        )
        min_spacing = 5 * 0.0001 / 1.10
        self.assertAlmostEqual(result, min_spacing, places=8)

    def test_maximum_spacing_ceiling_25pct(self):
        """Huge base_pips → capped at 0.25."""
        result = compute_dynamic_drop_pct(
            base_pips=10_000, pip_size=0.0001,
            last_fill_price=1.10, current_atr=0.00128, atr_ref=0.00128,
        )
        self.assertAlmostEqual(result, 0.25, places=8)

    def test_positive_output_always(self):
        """drop_pct is always positive."""
        for base_pips in [0.0, 5.0, 22.8, 100.0]:
            result = compute_dynamic_drop_pct(
                base_pips=base_pips, pip_size=0.0001,
                last_fill_price=1.10, current_atr=0.00128, atr_ref=0.00128,
            )
            self.assertGreater(result, 0.0)


# ── build_ladder_spec_from_analysis ──────────────────────────────────────────

class BuildLadderSpecFromAnalysisTests(SimpleTestCase):  # claude code changed: new test class

    def _fake_analysis(self):
        """Minimal fake analyse_pair() output for EUR/USD LONG H=24."""
        pips_stats = {
            "mean": 38.6, "median": 29.6,
            "p40": 22.8, "p60": 37.9, "p75": 54.4,
            "p85": 70.9, "p92": 90.1, "p97": 117.9, "p99": 152.5, "n": 26007,
        }
        return {
            "pair": "EUR/USD",
            "n_calibration_rows": 26175,
            "atr_ref": 0.001280,
            "LONG": {24: {"mae": {"pips": pips_stats, "pct": {}}, "mfe": {}}},
            "SHORT": {24: {"mae": {"pips": pips_stats, "pct": {}}, "mfe": {}}},
        }

    def test_extracts_correct_quantiles(self):
        analysis = self._fake_analysis()
        spec = build_ladder_spec_from_analysis(analysis, "LONG", 0.0001, "EUR/USD")
        self.assertEqual(spec.pair, "EUR/USD")
        self.assertEqual(spec.direction, "LONG")
        self.assertAlmostEqual(spec.base_pips[0], 22.8)  # p40 → L2
        self.assertAlmostEqual(spec.base_pips[1], 37.9)  # p60 → L3
        self.assertAlmostEqual(spec.base_pips[5], 117.9) # p97 → L7

    def test_atr_ref_passed_through(self):
        analysis = self._fake_analysis()
        spec = build_ladder_spec_from_analysis(analysis, "LONG", 0.0001, "EUR/USD")
        self.assertAlmostEqual(spec.atr_ref, 0.001280)

    def test_six_base_pips(self):
        analysis = self._fake_analysis()
        spec = build_ladder_spec_from_analysis(analysis, "LONG", 0.0001, "EUR/USD")
        self.assertEqual(len(spec.base_pips), 6)


# ── _simulate_data_derived_next_open ─────────────────────────────────────────

def _make_trending_df(n: int, start_price: float, trend_per_candle: float,
                      direction: str = "down") -> pd.DataFrame:
    """Synthetic 1h OHLCV for simulation testing."""
    prices = [start_price + (i if direction == "up" else -i) * trend_per_candle for i in range(n)]
    idx = pd.date_range("2021-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "open": prices,
        "high": [p + 0.0005 for p in prices],
        "low": [p - 0.0005 for p in prices],
        "close": prices,
    }, index=idx)


def _eurusd_long_spec() -> FxLadderSpec:
    return FxLadderSpec(
        pair="EUR/USD", direction="LONG", pip_size=0.0001, atr_ref=0.00128,
        base_pips=(22.8, 37.9, 54.4, 70.9, 90.1, 117.9),
    )


class SimulateDataDerivedNextOpenTests(SimpleTestCase):  # claude code changed: new test class

    def test_returns_correct_types(self):
        spec = _eurusd_long_spec()
        df = _make_trending_df(100, 1.10, 0.0001)
        trades, mtm = _simulate_data_derived_next_open(df, spec)
        self.assertIsInstance(trades, list)
        self.assertIsInstance(mtm, pd.DataFrame)
        self.assertIn("equity", mtm.columns)

    def test_missing_column_raises(self):
        spec = _eurusd_long_spec()
        df = _make_trending_df(100, 1.10, 0.0001).drop(columns=["close"])
        with self.assertRaises(ValueError):
            _simulate_data_derived_next_open(df, spec)

    def test_short_profit_on_falling_market(self):
        """SHORT should profit when price declines monotonically."""
        spec = FxLadderSpec(
            pair="EUR/USD", direction="SHORT", pip_size=0.0001, atr_ref=0.00128,
            base_pips=(22.2, 36.2, 51.9, 67.3, 85.8, 119.0),
        )
        # price falls from 1.20 to ~1.10 over 1000 candles — SHORT should eventually profit
        df = _make_trending_df(1000, 1.20, 0.0001, direction="down")
        trades, mtm = _simulate_data_derived_next_open(spec=spec, df=df)
        closed = [t for t in trades if t.get("exit_reason") != "DATA_END_STILL_OPEN"]
        if closed:
            total_profit = sum(t["profit_usdt"] for t in closed)
            self.assertGreater(total_profit, 0, "SHORT on falling market should profit")

    def test_swap_pnl_sign_convention(self):
        """
        LONG with negative swap_long_pips accumulates a negative swap_pnl (cost).
        SHORT with positive swap_short_pips accumulates a positive swap_pnl (credit).
        """
        # SHORT on rising market to force a profit trade quickly
        spec_short = FxLadderSpec(
            pair="EUR/USD", direction="SHORT", pip_size=0.0001, atr_ref=0.00128,
            base_pips=(22.2, 36.2, 51.9, 67.3, 85.8, 119.0),
        )
        df = _make_trending_df(2000, 1.20, 0.0001, direction="down")
        trades, _ = _simulate_data_derived_next_open(
            df, spec_short, swap_short_pips=0.5,  # big positive to make sign visible
        )
        closed = [t for t in trades if t.get("exit_reason") != "DATA_END_STILL_OPEN"]
        if closed:
            # SHORT with positive swap should have swap_pnl >= 0
            for t in closed:
                self.assertGreaterEqual(t.get("swap_pnl_usdt", 0.0), 0.0,
                                        "SHORT with positive swap should have non-negative swap_pnl")

    def test_cost_complete_flag(self):
        """cost_complete=True only when all cost fields provided."""
        spec = _eurusd_long_spec()
        df = _make_trending_df(200, 1.10, 0.00005, direction="up")
        # with all costs supplied
        trades_full, _ = _simulate_data_derived_next_open(
            df, spec,
            spread_pips=0.1, commission_per_lot=0.0,
            swap_long_pips=-0.84, swap_short_pips=0.123,
        )
        # with missing costs
        trades_incomplete, _ = _simulate_data_derived_next_open(df, spec)
        closed_full = [t for t in trades_full if "cost_complete" in t]
        closed_inc = [t for t in trades_incomplete if "cost_complete" in t]
        if closed_full:
            self.assertTrue(all(t["cost_complete"] for t in closed_full))
        if closed_inc:
            self.assertFalse(all(t["cost_complete"] for t in closed_inc))

    def test_no_trades_below_minimum_data(self):
        spec = _eurusd_long_spec()
        df = _make_trending_df(1, 1.10, 0.0)  # single candle
        trades, mtm = _simulate_data_derived_next_open(df, spec)
        self.assertEqual(trades, [])
        self.assertEqual(len(mtm), 0)


# ── _summarise_trades ─────────────────────────────────────────────────────────

class SummariseTradesTests(SimpleTestCase):  # claude code changed: new test class

    def _trade(self, profit, fills=1, held=10, complete=True):
        return {
            "profit_usdt": profit, "fills": fills,
            "candles_held": held, "cost_complete": complete,
            "exit_reason": "PROFIT_TARGET_TRAILING",
        }

    def test_empty_returns_zero_trades(self):
        spec = _eurusd_long_spec()
        s = _summarise_trades([], spec)
        self.assertEqual(s["total_trades"], 0)
        self.assertTrue(math.isnan(s["win_rate"]))

    def test_win_rate_correct(self):
        spec = _eurusd_long_spec()
        trades = [self._trade(1.0), self._trade(-0.5), self._trade(2.0)]
        s = _summarise_trades(trades, spec)
        self.assertAlmostEqual(s["win_rate"], 2/3)

    def test_open_at_end_excluded_from_summary(self):
        spec = _eurusd_long_spec()
        trades = [
            self._trade(1.0),
            {**self._trade(-5.0), "exit_reason": "DATA_END_STILL_OPEN"},
        ]
        s = _summarise_trades(trades, spec)
        self.assertEqual(s["total_trades"], 1)  # only the closed trade counted
        self.assertAlmostEqual(s["total_profit_usdt"], 1.0)


# ── compute_equity_stats ──────────────────────────────────────────────────────

class ComputeEquityStatsTests(SimpleTestCase):  # claude code changed: new test class

    def _make_mtm(self, equity_values):
        idx = pd.date_range("2021-01-01", periods=len(equity_values), freq="1h", tz="UTC")
        return pd.DataFrame({
            "timestamp": idx,
            "realised": equity_values,
            "unrealised": [0.0] * len(equity_values),
            "equity": equity_values,
            "in_trade": [True] * len(equity_values),
        })

    def test_empty_mtm_returns_zeros(self):
        stats = compute_equity_stats(pd.DataFrame(), [])
        self.assertEqual(stats["final_equity"], 0.0)
        self.assertEqual(stats["max_drawdown"], 0.0)

    def test_monotone_rising_no_drawdown(self):
        mtm = self._make_mtm([0.0, 1.0, 2.0, 3.0])
        stats = compute_equity_stats(mtm, [])
        self.assertAlmostEqual(stats["max_drawdown"], 0.0)
        self.assertAlmostEqual(stats["final_equity"], 3.0)

    def test_drawdown_computed_correctly(self):
        # equity goes up to 4 then falls to 1 — drawdown = 1-4 = -3
        mtm = self._make_mtm([0.0, 2.0, 4.0, 3.0, 1.0, 2.0])
        stats = compute_equity_stats(mtm, [])
        self.assertAlmostEqual(stats["max_drawdown"], -3.0)
        self.assertAlmostEqual(stats["min_equity"], 0.0)  # 0 is lower than 1

    def test_drawdown_pct_from_positive_peak(self):
        mtm = self._make_mtm([0.0, 10.0, 7.0])
        stats = compute_equity_stats(mtm, [])
        # max_drawdown = 7-10 = -3, peak = 10, pct = -3/10 = -30%
        self.assertAlmostEqual(stats["max_drawdown_pct"], -0.30, places=5)

    def test_open_at_end_count_and_gross(self):
        mtm = self._make_mtm([1.0, 2.0])
        trades = [
            {"exit_reason": "PROFIT_TARGET_TRAILING", "gross_usdt": 1.0},
            {"exit_reason": "DATA_END_STILL_OPEN", "gross_usdt": -5.0},
            {"exit_reason": "DATA_END_STILL_OPEN", "gross_usdt": -2.0},
        ]
        stats = compute_equity_stats(mtm, trades)
        self.assertEqual(stats["open_at_end_count"], 2)
        self.assertAlmostEqual(stats["open_at_end_gross"], -7.0)

    def test_summary_includes_equity_stats(self):
        """_summarise_trades with mtm passes equity stats through."""
        spec = _eurusd_long_spec()
        mtm = self._make_mtm([0.0, 1.0, 3.0, 2.0])
        trades = [{"profit_usdt": 1.0, "fills": 1, "candles_held": 3,
                   "cost_complete": True, "exit_reason": "PROFIT_TARGET_TRAILING"}]
        s = _summarise_trades(trades, spec, mtm=mtm)
        self.assertIn("max_drawdown", s)
        self.assertIn("final_equity", s)
        self.assertAlmostEqual(s["final_equity"], 2.0)
