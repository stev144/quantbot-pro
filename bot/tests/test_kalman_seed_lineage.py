# claude code changed: new file — 2026-10-03, remediation F-03 (legacy Kalman
# seed look-ahead). Proves: the legacy writer self-identifies its look-ahead
# seed; consumers record the input seed lineage; the canonical causal path is
# invariant to future shocks; the amended retest pipeline runs end-to-end on
# the canonical path. Synthetic data only.

import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from bot.tests.synthetic_data import cointegrated_log_pair   # claude code changed: 2026-10-04 — shared generator (bot/tests/synthetic_data.py), identical draws
from bot.quant_core.research_zones import ZONES   # claude code changed: D-8
from django.test import SimpleTestCase

from bot.quant_core.protocol import CANONICAL
from bot.research.entry_exit_engine import EntryExitEngine
from bot.research.kalman_filter_engine import KalmanFilterEngine


def _pair(n=3200, seed=0, shock_from=None):
    la, lb = cointegrated_log_pair(n, np.random.default_rng(seed))   # claude code changed: 2026-10-04 — shared generator (bot/tests/synthetic_data.py), identical draws
    if shock_from is not None:
        la[shock_from:] += 0.5
    idx = pd.date_range(ZONES.init_window()[0], periods=n, freq="h", tz="UTC")   # claude code changed: D-8 — candle L = S start
    return pd.Series(np.exp(la), index=idx), pd.Series(np.exp(lb), index=idx)


def _engine():   # ols_* are NOT used for θ₀ by run_canonical (test_canonical_seed convention)
    return KalmanFilterEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT", ols_beta=1.0, ols_alpha=0.0)


class SeedLineageTests(SimpleTestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_legacy_writer_declares_its_lookahead_seed(self):
        a, b = _pair(n=1500)
        data = self.d / "data"
        data.mkdir()
        for sym, s in (("X_USDT", a), ("Y_USDT", b)):
            pd.DataFrame({"timestamp": s.index, "close": s.to_numpy()}).to_csv(data / f"{sym}_1h.csv", index=False)
        KalmanFilterEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT",
                           ols_beta=1.3, ols_alpha=0.1).run(data_dir=str(data), output_dir=str(self.d / "out"))
        csv = self.d / "out" / "X_USDT_Y_USDT_kalman.csv"
        side = json.loads(next((self.d / "out").glob("X_USDT_Y_USDT_kalman*.json")).read_text())
        self.assertEqual(side["status"], "LEGACY")
        self.assertEqual(side["extra"]["seed_method"], "LEGACY_OLS_FIRST_N")
        self.assertTrue(side["extra"]["seed_lookahead"])
        self.assertEqual(EntryExitEngine._input_seed_lineage(str(csv)), "LEGACY_OLS_FIRST_N_LOOKAHEAD")
        # claude code changed: F-18 regression — the writer's first column must be "timestamp"
        # (it was written as "index" before 39b76a2) and must carry the input candles' real times.
        out = pd.read_csv(csv)
        self.assertEqual(out.columns[0], "timestamp")
        ts = pd.to_datetime(out["timestamp"], utc=True)
        self.assertTrue(ts.is_monotonic_increasing and ts.is_unique)
        self.assertTrue(set(ts).issubset(set(a.index)))

    def test_canonical_dataset_reads_as_causal_seed(self):
        a, b = _pair()
        _engine().run_canonical(a, b, output_dir=str(self.d))
        csv = self.d / "canonical" / "X_USDT_Y_USDT_kalman.csv"
        self.assertEqual(EntryExitEngine._input_seed_lineage(str(csv)), "CANONICAL_CAUSAL_SEED")

    def test_file_without_sidecar_is_unknown_legacy_never_canonical(self):
        p = self.d / "bare_kalman.csv"
        p.write_text("timestamp,kalman_beta\n2024-01-01 00:00:00+00:00,1.0\n")
        self.assertEqual(EntryExitEngine._input_seed_lineage(str(p)), "UNKNOWN_SEED_LEGACY")

    def test_canonical_path_is_invariant_to_future_shocks(self):
        L = CANONICAL.require_init_window()
        a1, b1 = _pair()
        a2, b2 = _pair(shock_from=2500)
        r1 = _engine().run_canonical(a1, b1)
        r2 = _engine().run_canonical(a2, b2)
        cut = r1.index[r1.index < a1.index[2500]]
        for col in ("kalman_beta", "kalman_alpha", "kalman_zscore"):
            with self.subTest(col=col):
                pd.testing.assert_series_equal(r1.loc[cut, col], r2.loc[cut, col])
        self.assertEqual(r1.index[0], a1.index[L])          # state starts after the L-candle init window

    def test_canonical_seed_recovers_known_beta(self):
        a, b = _pair()
        r = _engine().run_canonical(a, b)
        self.assertAlmostEqual(float(r["kalman_beta"].iloc[-1]), 1.3, delta=0.05)


class RetestPipelineSmokeTest(SimpleTestCase):
    def test_amended_retest_runs_on_the_canonical_path(self):
        from bot.research import pairs_crrp_retest as rt
        d = Path(tempfile.mkdtemp())
        try:
            a, b = _pair(n=3500, seed=3)
            closes = pd.concat({"a": a, "b": b}, axis=1)
            tl = rt._run_pipeline("X_USDT", "Y_USDT", closes, d / "real")
            summary = pd.read_csv(next((d / "real").glob("*_strategy_summary.csv")))
            self.assertEqual(summary["rule_set"].iloc[0], "CANONICAL")
            self.assertEqual(summary["kalman_seed_lineage"].iloc[0], "CANONICAL_CAUSAL_SEED")
            self.assertTrue((tl["entry_decision_timestamp"] > a.index[CANONICAL.require_init_window() - 1]).all())
        finally:
            shutil.rmtree(d, ignore_errors=True)


class CostCompletenessGateTest(SimpleTestCase):   # claude code changed: F-12 — incomplete economics never stamped CANONICAL
    def _run(self, d, **borrow):
        a, b = _pair(n=3500, seed=3)
        _engine().run_canonical(a, b, output_dir=str(d))
        EntryExitEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT", output_dir=str(d),
                        rule_set="CANONICAL", **borrow).run(kalman_csv=str(d / "canonical" / "X_USDT_Y_USDT_kalman.csv"))
        side = next(p for p in d.glob("*_strategy_summary*.json"))
        return json.loads(side.read_text())

    def test_unmodelled_borrow_downgrades_canonical_output(self):
        d = Path(tempfile.mkdtemp())
        try:
            prov = self._run(d)
            self.assertEqual(prov["status"], "UNCERTIFIED")
            self.assertFalse(prov["extra"]["cost_complete"])
            self.assertIn("F-12", prov["extra"]["downgrade_reason"])
            self.assertTrue(prov["run_id"])                       # lineage kept, not discarded
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_modelled_borrow_keeps_canonical_status(self):
        d = Path(tempfile.mkdtemp())
        try:
            # claude code changed: owner decision (borrow row) — canonical financing = recorded perpetual funding
            f = pd.Series(0.0001, index=pd.date_range("2021-09-01", "2022-06-30", freq="8h", tz="UTC"))
            prov = self._run(d, funding_rates_a=f, funding_rates_b=f)
            self.assertEqual(prov["status"], "CANONICAL")
            self.assertTrue(prov["extra"]["cost_complete"])
        finally:
            shutil.rmtree(d, ignore_errors=True)
