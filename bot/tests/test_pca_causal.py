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


# claude code changed: owner decision D-1 (PCA option 1) — executable factor-residual labels.
from bot.research.pca_engine import compute_open_labels_causal

LCFG = PCAConfig(n_components=1, min_fit_candles=1000, refit_every=168,
                 forward_horizons={"forward_return_1h": 1, "forward_return_4h": 4})


def _open_close(n=1600, seed=0):
    r, _, _ = _factor_returns(n=n, seed=seed)
    rng = np.random.default_rng(seed + 100)
    o = r + 0.002 * rng.standard_normal(r.shape)            # open returns: same factor structure, different prints
    return r, pd.DataFrame(o.values, index=r.index, columns=r.columns)


class OpenLabelTests(SimpleTestCase):
    def _fit_for_row(self, r, t):
        """Hand-derived decision fit: the refit block containing t, fitted on close rows before its start."""
        start = 1000 + ((t - 1000) // 168) * 168
        fit = r.values[:start]
        m, s = fit.mean(axis=0), fit.std(axis=0, ddof=1)
        w, v = np.linalg.eigh(np.corrcoef((fit - m) / s, rowvar=False))
        return m, s, v[:, [np.argmax(w)]]

    def test_known_answer_spans_open_t1_to_open_t1h_with_frozen_decision_fit(self):
        r, o = _open_close()
        lab = compute_open_labels_causal(r, o, LCFG)
        for t in (1000, 1165, 1166, 1300):                 # 1166/1167: a refit (1168) falls inside the 4h hold
            m, s, L = self._fit_for_row(r, t)
            z = (o.values[t + 2:t + 6] - m) / s            # open-return rows t+2 … t+5 = open(t+1) → open(t+5)
            expected = ((z - z @ L @ L.T) * s).sum(axis=0)
            np.testing.assert_allclose(lab[[f"forward_return_4h_S{i}" for i in range(6)]].values[t], expected, atol=1e-15)

    def test_close_and_pre_fill_open_cannot_change_the_label(self):
        r, o = _open_close()
        t = 1300
        base = compute_open_labels_causal(r, o, LCFG).iloc[t]
        r2 = r.copy(); r2.iloc[1168:] *= 4.0               # every close row from the decision block's start onward
        o2 = o.copy(); o2.iloc[:t + 2] *= 3.0              # open returns up to the fill bar (row t+1 = open_t → open_{t+1})
        pd.testing.assert_series_equal(compute_open_labels_causal(r2, o, LCFG).iloc[t], base)
        pd.testing.assert_series_equal(compute_open_labels_causal(r, o2, LCFG).iloc[t], base)

    def test_holding_period_open_changes_the_label(self):  # positive control
        r, o = _open_close()
        t = 1300
        o2 = o.copy(); o2.iloc[t + 3] += 0.05
        a = compute_open_labels_causal(r, o, LCFG).iloc[t]
        b = compute_open_labels_causal(r, o2, LCFG).iloc[t]
        self.assertFalse(np.allclose(a.filter(like="4h").values, b.filter(like="4h").values))
        pd.testing.assert_series_equal(a.filter(like="1h"), b.filter(like="1h"))   # 1h exits at open(t+2): row t+3 unused

    def test_no_label_without_exit_open_or_before_first_fit(self):
        r, o = _open_close()
        lab = compute_open_labels_causal(r, o, LCFG)
        self.assertTrue(lab.iloc[:1000].isna().all().all())
        self.assertTrue(lab.filter(like="4h").iloc[-5:].isna().all().all())    # open(t+5) beyond the data
        self.assertTrue(lab.filter(like="4h").iloc[-6].notna().all())

    def test_misaligned_open_returns_are_refused(self):
        r, o = _open_close()
        with self.assertRaisesRegex(ValueError, "share one index"):
            compute_open_labels_causal(r, o.iloc[1:], LCFG)
