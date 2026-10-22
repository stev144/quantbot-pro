# bot/tests/test_fx_survival_replay.py
"""Known-answer, regression and leakage tests for bot/research/fx_survival_replay.py.   # claude code changed: 2026-10-21 — new

Synthetic price paths (no mocks) for exact known answers; one real-data class (GBP/JPY, frozen cutoff) for
end-to-end reconciliation and decision equivalence with the legacy QC-1 loop.
"""
import dataclasses
import math
import unittest

import numpy as np
import pandas as pd

import bot.research.fx_survival_replay as R
from bot.research.fx_fold_runner import FxLadderSpec, _simulate_data_derived_next_open, compute_causal_atr_series

PIP = 0.0001
NO_COST = R.FxCostModel(spread_source="NONE", swap_source="NONE")


def _pair(close, *, low=None, high=None, open_=None, start="2024-01-01 00:00", spread_points=0.0, pair="EUR/USD",
          base_pips=(10.0, 15.0, 20.0, 25.0), swap_long=0.0, triple_weekday=2, freq="h"):
    """USD-quoted synthetic pair: quote->USD = 1, base->USD = the pair's own price (exactly like real EUR/USD)."""
    c = np.asarray(close, float)
    o = np.asarray(open_ if open_ is not None else np.r_[c[0], c[:-1]], float)
    l = np.asarray(low if low is not None else np.minimum(o, c), float)
    h = np.asarray(high if high is not None else np.maximum(o, c), float)
    ts = pd.date_range(start, periods=len(c), freq=freq, tz="UTC")
    spec = FxLadderSpec(pair=pair, direction="LONG", pip_size=PIP, atr_ref=0.001, base_pips=tuple(base_pips))
    return R.PairData(
        pair=pair, base="EUR", quote="USD", ts=ts, o=o, h=h, l=l, c=c, atr=np.full(len(c), 0.001),
        spread_px=np.full(len(c), spread_points * 1e-5), q2usd_open=np.ones(len(c)), q2usd_close=np.ones(len(c)),
        b2usd_open=o.copy(), b2usd_close=c.copy(), pip_size=PIP, point=1e-5, contract_size=100_000.0,
        min_lot=0.01, lot_step=0.01, swap_long_points=swap_long, swap_short_points=0.0, triple_weekday=triple_weekday,
        spec_schedule=[(ts[0], spec, "test")], replay_start=ts[0],
    )


def _strat(**kw):
    base = dict(entry_notional_usd=3000.0, max_cycle_candles=None)
    base.update(kw)
    return R.StrategyConfig(**base)


class AccountingTests(unittest.TestCase):
    def test_flat_market_no_trades_no_cash_movement(self):
        p = _pair([1.1] * 50)
        r = R.run_replay([p], R.AccountConfig(5000), NO_COST, _strat())
        self.assertAlmostEqual(r["rows"]["equity"].iloc[-1], 5000.0, places=6)
        self.assertTrue((r["ledger"]["type"] != "DEPOSIT").all())

    def test_profitable_cycle_exact_pnl_and_identity(self):
        # buy at 1.1000 (bar 1 open), rise to activate +0.5 %, trail 0.2 % -> exit at next open
        up = list(np.linspace(1.1, 1.1070, 30))
        path = [1.1] + up + [1.1040, 1.1040]
        p = _pair(path)
        r = R.run_replay([p], R.AccountConfig(5000), NO_COST, _strat())
        c = r["cycles"].iloc[0]
        lots = math.floor(3000 / 1.1 / 100000 / 0.01) * 0.01          # 0.02 lots — floored, never up-sized
        self.assertAlmostEqual(c["lots"], lots)
        exp = (c["exit_exec_price"] - 1.1) * lots * 100000
        self.assertAlmostEqual(c["net_pnl_usd"], exp, places=8)
        self.assertEqual(c["outcome"], R.PROFITABLE_EXIT)
        R.reconcile(r)

    def test_spread_on_buys_only_and_gross_identity(self):
        path = [1.1] + list(np.linspace(1.1, 1.1070, 30)) + [1.1040, 1.1040]
        p = _pair(path, spread_points=20)                                 # 2.0 pips
        r = R.run_replay([p], R.AccountConfig(5000), R.FxCostModel(swap_source="NONE"), _strat())
        c = r["cycles"].iloc[0]
        self.assertAlmostEqual(c["spread_usd"], 0.0002 * c["units"], places=9)     # one spread per round trip
        self.assertAlmostEqual(c["gross_pnl_usd"] - c["spread_usd"], c["realised_trading_usd"], places=9)

    def test_slippage_both_sides_and_commission_every_fill(self):
        path = [1.1] + list(np.linspace(1.1, 1.1070, 30)) + [1.1040, 1.1040]
        p = _pair(path)
        cost = R.FxCostModel(spread_source="NONE", swap_source="NONE", slippage_pips=0.5, commission_per_lot_side_usd=3.5)
        r = R.run_replay([p], R.AccountConfig(5000), cost, _strat())
        c = r["cycles"].iloc[0]
        self.assertAlmostEqual(c["slippage_usd"], 2 * 0.00005 * c["units"], places=9)
        self.assertAlmostEqual(c["commission_usd"], 2 * 3.5 * c["lots"], places=9)
        led = r["ledger"]
        self.assertAlmostEqual(-led.loc[led.type == "COMMISSION", "amount_usd"].sum(), c["commission_usd"], places=9)
        R.reconcile(r)

    def test_weighted_average_entry_is_unit_weighted(self):
        cyc = R._LiveCycle(1, "EUR/USD", None, "t", 0, pd.Timestamp("2024-01-01", tz="UTC"))
        for px, u in ((1.10, 2000), (1.09, 5000)):
            cyc.add_fill(R._Fill(0, None, px, px, u, u / 1e5, 0, 0, 0, 0))
        self.assertAlmostEqual(cyc.weighted_avg_price, (1.10 * 2000 + 1.09 * 5000) / 7000)

    def test_swap_rollovers_and_triple_day(self):
        # hold Mon 00:00 -> Fri 18:00 UTC: rollovers Mon..Thu 17:00 NY = 4, Wednesday counts 3x -> 6 swap-days
        n = 24 * 4 + 19
        p = _pair([1.1] * n, start="2024-01-08 00:00", swap_long=10.0, base_pips=(9999, 9999, 9999, 9999))
        r = R.run_replay([p], R.AccountConfig(5000), R.FxCostModel(spread_source="NONE"), _strat())
        sw = r["ledger"][r["ledger"].type == "SWAP"]
        lots = r["cycles"].iloc[0]["lots"]
        self.assertEqual(len(sw), 4)
        self.assertAlmostEqual(sw["amount_usd"].sum(), 6 * 10.0 * 1e-5 * 100000 * lots, places=8)
        R.reconcile(r)

    def test_cost_ratios_na_on_zero_denominator(self):
        self.assertIsNone(R._safe_ratio(5.0, 0.0))
        self.assertIsNone(R._safe_ratio(5.0, -1.0))
        self.assertAlmostEqual(R._safe_ratio(5.0, 20.0), 25.0)
        self.assertAlmostEqual(R._safe_ratio(30.0, 20.0), 150.0)        # never capped at 100 %


class DcaBehaviourTests(unittest.TestCase):
    def _falling_then_recovering(self, depth_steps=60, step=0.0004, recover=80):
        down = [1.1 - step * k for k in range(depth_steps)]
        up = list(np.linspace(down[-1], 1.12, recover))
        return [1.1] + down + up + [1.118, 1.118]

    def test_ladder_progression_multipliers_and_exhaustion(self):
        p = _pair(self._falling_then_recovering())
        r = R.run_replay([p], R.AccountConfig(100_000), NO_COST, _strat())
        f = r["fills"]
        first = f[f.cycle_id == 1]
        self.assertEqual(list(first.level), [1, 2, 3, 4, 5])
        self.assertEqual(list(np.round(first.lots / first.lots.iloc[0])), [1, 2, 4, 8, 16])
        self.assertIn(R.DCA_LADDER_EXHAUSTED, set(r["events"]["type"]))
        self.assertNotIn(R.LIQUIDATED, set(r["cycles"]["outcome"]))       # exhaustion is not liquidation

    def test_insufficient_free_margin_rejects_add_on_without_liquidation(self):
        p = _pair(self._falling_then_recovering())
        r = R.run_replay([p], R.AccountConfig(700), NO_COST, _strat())
        ev = r["events"]
        self.assertIn(R.INSUFFICIENT_FREE_CASH, set(ev["type"]))
        c1 = r["cycles"][r["cycles"].cycle_id == 1].iloc[0]
        self.assertLess(c1["fills"], 5)
        self.assertGreater(c1["rejected_order_attempts"], 0)
        self.assertNotEqual(c1["outcome"], R.LIQUIDATED)

    def test_open_cycle_at_data_end_is_reported_and_marked(self):
        p = _pair([1.1] + [1.1 - 0.0004 * k for k in range(30)])
        r = R.run_replay([p], R.AccountConfig(100_000), NO_COST, _strat())
        last = r["cycles"].iloc[-1]
        self.assertEqual(last["outcome"], R.OPEN_UNRESOLVED_AT_DATA_END)
        self.assertLess(last["mtm_unrealised_usd"], 0)
        self.assertAlmostEqual(r["rows"]["equity"].iloc[-1], 100_000 + last["net_pnl_usd"], places=6)

    def test_execution_delay_shifts_fills_by_one_bar(self):
        path = [1.1] + list(np.linspace(1.1, 1.1070, 30)) + [1.1040] * 4
        p = _pair(path)
        a = R.run_replay([p], R.AccountConfig(5000), NO_COST, _strat())
        b = R.run_replay([p], R.AccountConfig(5000), NO_COST, _strat(execution_delay_bars=1))
        self.assertGreater(len(b["fills"]), 0)
        self.assertEqual(pd.Timestamp(b["fills"]["timestamp"].iloc[0]) - pd.Timestamp(a["fills"]["timestamp"].iloc[0]),
                         pd.Timedelta(hours=1))

    def test_minimum_volume_rejection(self):
        p = _pair([1.1] * 20)
        r = R.run_replay([p], R.AccountConfig(5000), NO_COST, _strat(entry_notional_usd=500.0))   # 0.0045 lots < 0.01
        self.assertIn(R.ORDER_REJECTED, set(r["events"]["type"]))
        self.assertEqual(len(r["cycles"]), 0)

    def test_equivalence_with_legacy_loop_unlimited_capital_zero_cost(self):
        rng = np.random.default_rng(7)
        close = 1.1 * np.exp(np.cumsum(rng.normal(0, 0.0012, 3000)))
        p = _pair(close, base_pips=(12.0, 18.0, 24.0, 30.0))
        p.atr = compute_causal_atr_series(pd.DataFrame({"high": p.h, "low": p.l, "close": p.c}))
        spec = p.spec_schedule[0][1]
        strat = R.StrategyConfig(entry_notional_usd=6.0, max_cycle_candles=72)
        r = R.run_replay([p], R.AccountConfig(1e12, fractional_lots=True), NO_COST, strat)
        df = pd.DataFrame({"open": p.o, "high": p.h, "low": p.l, "close": p.c}, index=p.ts)
        legacy, _ = _simulate_data_derived_next_open(df, spec, initial_usdt=6.0, profit_activation_pct=0.005,
                                                     trailing_pct=0.002, max_cycle_candles=72)
        legacy_closed = [t for t in legacy if t["exit_reason"] != "DATA_END_STILL_OPEN"]
        mine = r["cycles"][r["cycles"].outcome != R.OPEN_UNRESOLVED_AT_DATA_END]
        self.assertEqual(len(mine), len(legacy_closed))
        for (_, m), t in zip(mine.iterrows(), legacy_closed):
            self.assertEqual(pd.Timestamp(m["entry_ts"]), pd.Timestamp(t["entry_time"]))
            self.assertEqual(pd.Timestamp(m["exit_ts"]), pd.Timestamp(t["exit_time"]))
            self.assertEqual(m["fills"], t["fills"])
            self.assertEqual(m["exit_reason"], t["exit_reason"])
            self.assertAlmostEqual(m["net_pnl_usd"], t["profit_usdt"], places=9)


class LiquidationTests(unittest.TestCase):
    NO_DCA = (9999.0, 9999.0, 9999.0, 9999.0)

    def test_close_out_at_trigger_price_without_gap(self):
        # 3.0 lots-equivalent: notional $330k at 1:30 -> margin $11k; capital $14k; low -3 % breaches 50 %
        path = [1.1] * 5 + [1.1] * 3
        low = list(path)
        low[6] = 1.1 * 0.97
        p = _pair(path, low=low, base_pips=self.NO_DCA)
        r = R.run_replay([p], R.AccountConfig(14_000), NO_COST, _strat(entry_notional_usd=330_000))
        liq = r["cycles"][r["cycles"].outcome == R.LIQUIDATED].iloc[0]
        self.assertAlmostEqual(liq["equity_before_usd"] + liq["net_pnl_usd"], 0.5 * liq["margin_used_usd"], delta=0.01)
        self.assertGreater(liq["exit_exec_price"], low[6])                # filled at the trigger, not the low
        R.reconcile(r)

    def test_gap_through_fills_at_open(self):
        path = [1.1] * 5 + [1.05, 1.05, 1.05]
        p = _pair(path, open_=[1.1] * 5 + [1.05, 1.05, 1.05], base_pips=self.NO_DCA)
        r = R.run_replay([p], R.AccountConfig(14_000), NO_COST, _strat(entry_notional_usd=330_000))
        liq = r["cycles"][r["cycles"].outcome == R.LIQUIDATED].iloc[0]
        self.assertAlmostEqual(liq["exit_ref_price"], 1.05)
        self.assertIn("gapped", liq["rule"])

    def test_negative_balance_protection_and_insolvency(self):
        path = [1.1] * 5 + [0.95, 0.95, 0.95]
        p = _pair(path, open_=[1.1] * 5 + [0.95, 0.95, 0.95], base_pips=self.NO_DCA)
        r = R.run_replay([p], R.AccountConfig(14_000), NO_COST, _strat(entry_notional_usd=330_000))
        led = r["ledger"]
        self.assertIn("NEGATIVE_BALANCE_PROTECTION", set(led["type"]))
        self.assertAlmostEqual(r["rows"]["balance"].iloc[-1], 0.0, places=6)
        self.assertTrue(r["insolvent"])
        self.assertIn(R.BANKRUPTCY_OR_INSOLVENCY, set(r["events"]["type"]))
        R.reconcile(r)

    def test_multi_position_close_out_largest_loser_first_then_stops(self):
        crash = [1.1] * 5 + [1.1] * 3
        low = list(crash)
        low[6] = 1.1 * 0.935   # A loses ~$21.5k: level (30k-21.5k)/22k < 50 %; after closing A, 8.5k/11k > 50 %
        a = _pair(crash, low=low, base_pips=self.NO_DCA, pair="EUR/USD")
        b = _pair([1.3] * 8, base_pips=self.NO_DCA, pair="GBP/USD")
        b.base = "GBP"
        r = R.run_replay([a, b], R.AccountConfig(30_000), NO_COST, _strat(entry_notional_usd=330_000))
        liq = r["cycles"][r["cycles"].outcome == R.LIQUIDATED]
        self.assertEqual(list(liq["pair"]), ["EUR/USD"])           # the flat GBP/USD position is not closed
        self.assertIn(R.OPEN_UNRESOLVED_AT_DATA_END, set(r["cycles"][r["cycles"].pair == "GBP/USD"]["outcome"]))
        R.reconcile(r)

    def test_leverage_rules(self):
        self.assertEqual(R.esma_leverage("GBP/JPY"), 30.0)
        self.assertEqual(R.esma_leverage("NZD/CHF"), 20.0)


class AnalyticsAndLeakageTests(unittest.TestCase):
    def test_drawdown_from_equity_peak_and_intrabar(self):
        p = _pair([1.1] + [1.1 - 0.0004 * k for k in range(30)] + list(np.linspace(1.088, 1.115, 60)) + [1.113] * 3)
        r = R.run_replay([p], R.AccountConfig(100_000), NO_COST, _strat())
        m = R.compute_metrics(r)
        eq = r["rows"]["equity"]
        self.assertAlmostEqual(m["max_drawdown_usd"], float((eq - eq.cummax().clip(lower=100_000)).min()), places=8)
        self.assertLessEqual(m["max_drawdown_intrabar_usd"], m["max_drawdown_usd"] + 1e-9)

    def test_cutoff_past_seal_refused(self):
        with self.assertRaises(ValueError):
            R.build_pair_data("GBP/JPY", cutoff=R.FX_RESEARCH_DATA_CUTOFF + pd.Timedelta(hours=1))

    def test_deterministic(self):
        rng = np.random.default_rng(3)
        p = _pair(1.1 * np.exp(np.cumsum(rng.normal(0, 0.001, 800))))
        a = R.run_replay([p], R.AccountConfig(5000), R.FxCostModel(swap_source="NONE"), _strat(max_cycle_candles=72))
        b = R.run_replay([p], R.AccountConfig(5000), R.FxCostModel(swap_source="NONE"), _strat(max_cycle_candles=72))
        pd.testing.assert_frame_equal(a["rows"], b["rows"])

    def test_no_look_ahead_future_bars_do_not_change_past(self):
        rng = np.random.default_rng(11)
        close = 1.1 * np.exp(np.cumsum(rng.normal(0, 0.001, 600)))
        k = 400
        p1 = _pair(close)
        altered = close.copy()
        altered[k + 1:] *= 0.9
        p2 = _pair(altered)
        a = R.run_replay([p1], R.AccountConfig(5000), NO_COST, _strat(max_cycle_candles=72))
        b = R.run_replay([p2], R.AccountConfig(5000), NO_COST, _strat(max_cycle_candles=72))
        pd.testing.assert_frame_equal(a["rows"].iloc[:k], b["rows"].iloc[:k])


class RealDataGbpJpyTests(unittest.TestCase):
    """Frozen-cutoff GBP/JPY: end-to-end reconciliation and walk-forward schedule wiring."""

    @classmethod
    def setUpClass(cls):
        cls.data = R.build_pair_data("GBP/JPY")

    def test_frozen_cutoff_and_schedule(self):
        self.assertLess(self.data.ts[-1], R.FX_RESEARCH_DATA_CUTOFF)
        self.assertEqual([s[2] for s in self.data.spec_schedule], ["fold1", "fold2", "fold3", "fold4", "fold5", "holdout"])

    def test_full_replay_reconciles_and_keeps_open_cycles(self):
        r = R.run_replay([self.data], R.AccountConfig(10_000))
        R.reconcile(r)
        m = R.compute_metrics(r)
        self.assertEqual(m["completed_cycles"] + m["unresolved_cycles"], len(r["cycles"]))
        self.assertGreater(m["costs"]["spread"], 0)


if __name__ == "__main__":
    unittest.main()
