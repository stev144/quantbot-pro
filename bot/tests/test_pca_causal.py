# claude code changed: new file — 2026-10-03, remediation F-01 (PCA look-ahead).
import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.pca_engine import PCAConfig, compute_residuals, compute_residuals_causal, run_pca


def _factor_returns(n=1600, n_assets=6, seed=0):
    """One-factor market: r_it = b_i·f_t + e_it (known truth)."""
    rng = np.random.default_rng(seed)
    f = 0.01 * rng.standard_normal(n)
    betas = np.linspace(0.6, 1.4, n_assets)
    e = 0.004 * rng.standard_normal((n, n_assets))
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(np.outer(f, betas) + e, index=idx, columns=[f"S{i}" for i in range(n_assets)]), f, e


CFG = PCAConfig(n_components=1, min_fit_candles=1000, refit_every=168)


class CausalPCATests(SimpleTestCase):
    def test_future_perturbation_leaves_past_residuals_identical(self):
        r, _, _ = _factor_returns()
        cut = 1300                                                  # inside a segment, not on a refit boundary
        r2 = r.copy()
        r2.iloc[cut:] = r2.iloc[cut:] * 3.0 + 0.02                  # change only the future
        a, _ = compute_residuals_causal(r, CFG)
        b, _ = compute_residuals_causal(r2, CFG)
        pd.testing.assert_frame_equal(a.iloc[:cut], b.iloc[:cut])   # byte-identical past
        self.assertFalse(np.allclose(a.iloc[cut:].values, b.iloc[cut:].values))

    def test_defect_injection_full_history_fit_fails_the_same_check(self):
        # The legacy full-history fit must FAIL the check above — proves the test detects F-01.
        r, _, _ = _factor_returns()
        cut = 1300
        r2 = r.copy()
        r2.iloc[cut:] = r2.iloc[cut:] * 3.0 + 0.02
        def legacy(x):
            _, _, L, _, m, s = run_pca(x, CFG)
            return compute_residuals(x, L, m, s, CFG)
        self.assertGreater(np.abs(legacy(r).iloc[:cut].values - legacy(r2).iloc[:cut].values).max(), 1e-6)

    def test_first_segment_matches_hand_computed_fit_on_prior_rows(self):
        r, _, _ = _factor_returns()
        res, log = compute_residuals_causal(r, CFG)
        fit = r.values[:1000]
        m, s = fit.mean(axis=0), fit.std(axis=0, ddof=1)
        w, v = np.linalg.eigh(np.corrcoef((fit - m) / s, rowvar=False))
        L = v[:, [np.argmax(w)]]
        z = (r.values[1000:1168] - m) / s
        np.testing.assert_allclose(res.values[1000:1168], (z - z @ L @ L.T) * s, rtol=0, atol=1e-15)
        self.assertTrue(res.iloc[:1000].isna().all().all())         # no residual before the first fit
        self.assertTrue((log["fit_end"] < log["applied_from"]).all())

    def test_residual_removes_the_known_factor_out_of_sample(self):
        r, f, e = _factor_returns(n=3000)
        res, _ = compute_residuals_causal(r, CFG)
        oos = slice(1000, None)
        raw_corr = np.mean([abs(np.corrcoef(r.values[oos, i], f[oos])[0, 1]) for i in range(r.shape[1])])
        res_corr = np.mean([abs(np.corrcoef(res.values[oos, i], f[oos])[0, 1]) for i in range(r.shape[1])])
        idio_corr = np.mean([np.corrcoef(res.values[oos, i], e[oos, i])[0, 1] for i in range(r.shape[1])])
        self.assertGreater(raw_corr, 0.85)                          # factor dominates raw returns (observed 0.91)
        # Observed over seeds 0–4: residual–factor |corr| 0.030–0.047, residual–true-noise corr 0.888–0.898.
        # Not exactly 0 / 1: PCA on the correlation matrix recovers b/σ, not b, and the fit is estimated.
        self.assertLess(res_corr, 0.10)
        self.assertGreater(idio_corr, 0.85)

    def test_refuses_insufficient_history_and_degenerate_std(self):
        r, _, _ = _factor_returns(n=900)
        with self.assertRaisesRegex(ValueError, "min_fit_candles"):
            compute_residuals_causal(r, CFG)
        r, _, _ = _factor_returns()
        r["S0"] = 0.0
        with self.assertRaisesRegex(ValueError, "std"):
            compute_residuals_causal(r, CFG)
