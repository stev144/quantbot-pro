# claude code changed: new file — 2026-10-03, owner decision (borrow row: perpetual funding rates).
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.quant_core.funding import funding_cost, normalise_funding

T = lambda s: pd.Timestamp(s, tz="UTC")


def _prices(start="2025-01-01", periods=200, px=100.0):
    return pd.Series(px, index=pd.date_range(start, periods=periods, freq="h", tz="UTC"))


class FundingCostTests(SimpleTestCase):
    def test_known_answer_and_sign(self):
        f = pd.Series([0.0001, 0.0002, -0.0003, 0.0004],
                      index=[T("2025-01-01 00:00"), T("2025-01-01 08:00"), T("2025-01-01 16:00"), T("2025-01-02 00:00")])
        px = _prices()
        fc = funding_cost(2.0, px, f, T("2025-01-01 01:00"), T("2025-01-02 00:00"))
        # settlements inside (01:00, next 00:00]: 08:00, 16:00, 00:00 → short receives rate × 2 × 100
        self.assertTrue(fc.complete)
        self.assertEqual(fc.n_settlements, 3)
        self.assertAlmostEqual(fc.cost, -(0.0002 - 0.0003 + 0.0004) * 200.0, places=12)

    def test_four_hour_cadence_is_accepted(self):
        f = pd.Series(0.0001, index=pd.date_range("2025-01-01", periods=20, freq="4h", tz="UTC"))
        fc = funding_cost(1.0, _prices(), f, T("2025-01-01 02:00"), T("2025-01-02 02:00"))
        self.assertTrue(fc.complete)
        self.assertEqual(fc.n_settlements, 6)

    def test_missing_coverage_fails_closed_never_zero(self):
        f = pd.Series(0.0001, index=pd.date_range("2025-01-01", periods=10, freq="8h", tz="UTC"))
        not_spanned = funding_cost(1.0, _prices(), f, T("2025-01-01 01:00"), T("2025-01-05 00:00"))
        self.assertFalse(not_spanned.complete)
        self.assertIsNone(not_spanned.cost)
        gap = f.drop(f.index[3])                                   # 16 h hole
        g = funding_cost(1.0, _prices(), gap, T("2025-01-01 01:00"), T("2025-01-03 00:00"))
        self.assertFalse(g.complete)
        self.assertIn("gap", g.reason)

    def test_no_settlement_inside_a_covered_window_costs_zero(self):
        f = pd.Series(0.0001, index=pd.date_range("2025-01-01", periods=10, freq="8h", tz="UTC"))
        fc = funding_cost(1.0, _prices(), f, T("2025-01-01 01:00"), T("2025-01-01 07:00"))
        self.assertEqual((fc.cost, fc.complete, fc.n_settlements), (0.0, True, 0))

    def test_nan_rates_and_epoch_ms_index(self):
        with self.assertRaises(ValueError):
            normalise_funding(pd.Series([0.0001, np.nan], index=[T("2025-01-01"), T("2025-01-01 08:00")]))
        ms = pd.Series([0.0001, 0.0002], index=[1735689600000, 1735718400000])     # 2025-01-01 00:00 / 08:00
        self.assertEqual(normalise_funding(ms).index[1], T("2025-01-01 08:00"))


class EngineFundingTests(SimpleTestCase):
    """Canonical runs: recorded funding → CANONICAL; constant rates → BORROW_RATE_ASSUMED; a gap → BORROW_UNAVAILABLE."""

    def _run(self, d, **fin):
        import json
        from bot.tests.test_kalman_seed_lineage import _engine, _pair
        from bot.research.entry_exit_engine import EntryExitEngine
        a, b = _pair(n=3500, seed=3)
        _engine().run_canonical(a, b, output_dir=str(d))
        eng = EntryExitEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT", output_dir=str(d),
                              rule_set="CANONICAL", **fin)
        tl, summary, _ = eng.run(kalman_csv=str(d / "canonical" / "X_USDT_Y_USDT_kalman.csv"))
        side = json.loads(next(d.glob("*_strategy_summary*.json")).read_text())
        return tl, summary.iloc[0], side, a.index

    def _funding(self, idx, rate=0.0001, drop=None):
        f = pd.Series(rate, index=pd.date_range(idx[0].floor("8h") - pd.Timedelta(hours=8), idx[-1] + pd.Timedelta(hours=16), freq="8h"))
        return f.drop(f.index[drop]) if drop is not None else f

    def test_recorded_funding_is_canonical_and_moves_pnl_by_exactly_the_funding(self):
        d1, d2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        try:
            tl0, s0, side0, idx = self._run(d1)                      # no financing model → not canonical
            f = self._funding(idx)
            tl1, s1, side1, _ = self._run(d2, funding_rates_a=f, funding_rates_b=f)
            self.assertEqual((side0["status"], s0["borrow_status"]), ("UNCERTIFIED", "ASSUMED_ZERO_BORROW"))
            self.assertEqual((side1["status"], s1["borrow_status"]), ("CANONICAL", "FUNDING_KNOWN"))
            self.assertGreater(len(tl1), 0)
            diff = tl1["net_pnl_usdt"].to_numpy() - tl0["net_pnl_usdt"].to_numpy()
            # net_pnl_usdt is logged at 2 dp (±0.005 each) and borrow_usdt at 6 dp, so a difference of two rounded
            # nets agrees with −borrow to within 0.01 + 5e-7 — the rounding bound, not a loosened tolerance.
            np.testing.assert_allclose(diff, -tl1["borrow_usdt"].to_numpy(), rtol=0, atol=0.0100005)   # funding is the ONLY change
            ident = tl1["mark_pnl_usdt"] - tl1["slippage_usdt"] - tl1["fee_usdt"] - tl1["borrow_usdt"]
            np.testing.assert_allclose(tl1["net_pnl_usdt"], ident, rtol=0, atol=0.005 + 2e-6)          # P&L decomposition holds
            # positive funding is income to the short (≤ 0 cost); a hold with no settlement inside it (median hold
            # ≈ 10 h vs 8-h settlements) correctly has exactly 0.
            self.assertTrue((tl1["borrow_usdt"] <= 0).all())
            self.assertTrue((tl1["borrow_usdt"] < 0).any())
        finally:
            shutil.rmtree(d1, ignore_errors=True); shutil.rmtree(d2, ignore_errors=True)

    def test_constant_rates_are_an_assumption_not_canonical(self):
        d = Path(tempfile.mkdtemp())
        try:
            _, s, side, _ = self._run(d, borrow_rate_hourly_a=0.00001, borrow_rate_hourly_b=0.00001)
            self.assertEqual((side["status"], s["borrow_status"], bool(s["cost_complete"])),
                             ("UNCERTIFIED", "BORROW_RATE_ASSUMED", False))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_a_funding_gap_makes_the_run_borrow_unavailable(self):
        d = Path(tempfile.mkdtemp())
        try:
            from bot.tests.test_kalman_seed_lineage import _pair
            a, _ = _pair(n=3500, seed=3)
            f = self._funding(a.index)
            gappy = f.drop(f.index[len(f) // 2: len(f) // 2 + 40])             # ~13 days missing mid-sample
            _, s, side, _ = self._run(d, funding_rates_a=gappy, funding_rates_b=gappy)
            self.assertEqual((side["status"], s["borrow_status"]), ("UNCERTIFIED", "BORROW_UNAVAILABLE"))
            self.assertTrue(side["extra"]["funding_gaps"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_mixed_or_one_sided_financing_is_refused(self):
        from bot.research.entry_exit_engine import EntryExitEngine
        f = pd.Series([0.0001], index=[T("2025-01-01")])
        with self.assertRaises(ValueError):
            EntryExitEngine(pair_name="X/Y", symbol_a="X", symbol_b="Y", funding_rates_a=f)
        with self.assertRaises(ValueError):
            EntryExitEngine(pair_name="X/Y", symbol_a="X", symbol_b="Y", funding_rates_a=f, funding_rates_b=f,
                            borrow_rate_hourly_a=0.0, borrow_rate_hourly_b=0.0)


class RecordedFundingTests(SimpleTestCase):   # recorded Binance stamps carry millisecond jitter
    def test_millisecond_jitter_is_not_a_gap_but_a_missing_settlement_is(self):
        idx = pd.date_range("2025-01-01", periods=12, freq="8h", tz="UTC") + pd.to_timedelta(np.arange(12) * 47, unit="ms")
        f = pd.Series(0.0001, index=idx)
        self.assertTrue(funding_cost(1.0, _prices(), f, T("2025-01-01 01:00"), T("2025-01-03 00:00")).complete)
        self.assertFalse(funding_cost(1.0, _prices(), f.drop(f.index[3]), T("2025-01-01 01:00"), T("2025-01-03 00:00")).complete)
