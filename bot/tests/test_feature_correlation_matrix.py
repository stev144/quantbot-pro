# claude code changed: new file — feature-correlation upgrade
# (bot/views/feature_intelligence_data.py::get_feature_correlation_matrix).
# Synthetic observations files with KNOWN structure in a temp directory:
# expected values follow from how the data was built, not from the code
# under test. No network, no real research_data/ files touched.

import os
import shutil
import tempfile
import uuid
from unittest import mock

import numpy as np
import pandas as pd
from django.test import TestCase

from bot.views import feature_intelligence_data as fid


def _frame(n=6000, seed=7):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2022-01-01", periods=n, freq="h", tz="UTC")
    close = 100.0 * np.exp(np.cumsum(0.0004 + 0.01 * rng.standard_normal(n)))   # strong trend
    eff = rng.uniform(0.05, 0.95, n)
    df = pd.DataFrame({
        "timestamp": ts.astype(str), "close": close,
        "ema_12": close * (1 + 0.01 * rng.standard_normal(n)),   # independent deviations from price
        "ema_26": close * (1 + 0.01 * rng.standard_normal(n)),
        "atr": close * rng.uniform(0.005, 0.02, n),
        "efficiency": eff, "atr_ratio": np.clip(1 / eff, 0, 10),
        "rsi": rng.uniform(10, 90, n),
        "adx": np.nan,                                            # empty column
    })
    df["atr_indicator"] = df["atr"]                               # exact duplicate
    return df


class FeatureCorrelationMatrixTests(TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.patch = mock.patch.object(fid, "RESEARCH_DATA_DIR", self.dir)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, df):
        sym = f"T{uuid.uuid4().hex[:8].upper()}_USDT"             # unique → no cross-test cache hits
        df.to_csv(os.path.join(self.dir, f"{sym}_observations.csv"), index=False)
        return sym

    def _c(self, m, a, b):
        i, j = m["features"].index(a), m["features"].index(b)
        return m["matrix"][i][j]

    def test_empty_column_is_reported_not_rendered_as_nan(self):
        m = fid.get_feature_correlation_matrix(self._write(_frame()))
        self.assertNotIn("adx", m["features"])
        self.assertEqual([u["feature"] for u in m["unavailable"]], ["adx"])
        self.assertIn("only 0 of 6,000 values present", m["unavailable"][0]["reason"])   # an EMPTY column, not mislabelled "constant"
        self.assertFalse(any(np.isnan(v) for row in m["matrix"] for v in row))

    def test_exact_duplicate_is_reported_once(self):
        m = fid.get_feature_correlation_matrix(self._write(_frame()))
        self.assertNotIn("atr_indicator", m["features"])
        self.assertEqual(m["duplicates"][0]["feature"], "atr_indicator")
        self.assertEqual(m["duplicates"][0]["duplicate_of"], "atr")
        self.assertIn("_calculate_atr", m["duplicates"][0]["why"])

    def test_trend_driven_correlation_disappears_on_stationary_basis(self):
        # ema_12 and ema_26 share ONLY the price trend (independent 1 % deviations):
        # raw ≈ 1, price-free ≈ 0
        sym = self._write(_frame())
        raw = fid.get_feature_correlation_matrix(sym, basis="raw")
        sta = fid.get_feature_correlation_matrix(sym, basis="stationary")
        self.assertGreater(self._c(raw, "ema_12", "ema_26"), 0.99)
        self.assertLess(abs(self._c(sta, "ema_12", "ema_26")), 0.05)
        self.assertIn("ema_12 ÷ close − 1", sta["labels"])
        self.assertIn("atr ÷ close", sta["labels"])

    def test_by_construction_pair_is_labelled(self):
        # 10,000 h from 2022-01-01: 8,760 in 2022 + 1,240 in 2023 (both >= STABILITY_MIN_YEAR_OBS)
        m = fid.get_feature_correlation_matrix(self._write(_frame(n=10000)), method="spearman")
        pair = next(p for p in m["redundant_pairs"] if {p["feature_a"], p["feature_b"]} == {"efficiency", "atr_ratio"})
        self.assertEqual(pair["kind"], "BY CONSTRUCTION")
        self.assertLess(pair["corr"], -0.99)          # atr_ratio = 1/efficiency is strictly decreasing → Spearman −1
        self.assertEqual(pair["yearly"]["n_years"], 2)
        self.assertFalse(pair["yearly"]["sign_flip"])

    def test_spearman_is_robust_to_one_extreme_candle(self):
        n = 3000
        rng = np.random.default_rng(1)
        df = pd.DataFrame({"timestamp": pd.date_range("2023-01-01", periods=n, freq="h", tz="UTC").astype(str),
                           "close": 100.0, "rsi": rng.standard_normal(n), "efficiency": rng.standard_normal(n)})
        df.loc[0, ["rsi", "efficiency"]] = 1e4        # one shared outlier
        sym = self._write(df)
        p = self._c(fid.get_feature_correlation_matrix(sym, method="pearson"), "rsi", "efficiency")
        s = self._c(fid.get_feature_correlation_matrix(sym, method="spearman"), "rsi", "efficiency")
        self.assertGreater(p, 0.9)                    # one candle manufactures a "redundant" Pearson pair
        self.assertLess(abs(s), 0.1)                  # ranks see two unrelated series

    def test_constant_column_is_unavailable(self):
        df = _frame()
        df["rsi"] = 50.0
        m = fid.get_feature_correlation_matrix(self._write(df))
        self.assertIn("rsi", [u["feature"] for u in m["unavailable"]])

    def test_invalid_basis_and_method_fall_back_to_defaults(self):
        m = fid.get_feature_correlation_matrix(self._write(_frame()), basis="nope", method="kendall")
        self.assertEqual((m["basis"], m["method"]), ("stationary", "pearson"))

    def test_regenerated_file_is_not_served_from_stale_cache(self):
        sym = self._write(_frame(seed=1))
        path = os.path.join(self.dir, f"{sym}_observations.csv")
        first = fid.get_feature_correlation_matrix(sym)["matrix"]
        df = _frame(seed=2)
        df.to_csv(path, index=False)
        os.utime(path, (os.path.getmtime(path) + 10, os.path.getmtime(path) + 10))
        self.assertNotEqual(first, fid.get_feature_correlation_matrix(sym)["matrix"])

    def test_view_renders_both_bases(self):
        sym = self._write(_frame())
        for basis in ("stationary", "raw"):
            r = self.client.get("/research/features/correlation/", {"symbol": sym, "basis": basis, "method": "spearman"})
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "DUPLICATE of atr")
            self.assertContains(r, "NO USABLE DATA")


class YearlyStabilityTests(TestCase):
    def test_sign_flip_between_years_is_detected(self):
        n = 2 * 8760
        rng = np.random.default_rng(3)
        ts = pd.Series(pd.date_range("2022-01-01", periods=n, freq="h", tz="UTC"))
        x = pd.Series(rng.standard_normal(n))
        sign = np.where(ts.dt.year == 2022, 1.0, -1.0)
        y = pd.Series(sign * x + 0.3 * rng.standard_normal(n))
        r = fid._yearly_corr_range(x, y, ts.dt.year, "pearson")
        self.assertEqual(r["n_years"], 2)
        self.assertTrue(r["sign_flip"])
        self.assertLess(r["min"], -0.9)
        self.assertGreater(r["max"], 0.9)

    def test_single_year_gives_no_stability_claim(self):
        n = 3000
        ts = pd.Series(pd.date_range("2023-01-01", periods=n, freq="h", tz="UTC"))
        x = pd.Series(np.arange(n, dtype=float))
        self.assertIsNone(fid._yearly_corr_range(x, x, ts.dt.year, "pearson"))
