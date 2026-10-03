# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/quant_core/inference.py
#
# claude code changed: new file — 2026-10-03, remediation F-02 (IID p-values on
# overlapping / serially dependent observations). CANDIDATE dependence-aware
# tests for "is the cross-sectional/time-series association between a feature
# x_t and a forward label y_t zero?" — NOT wired into any engine. The method
# to adopt is an owner decision, made from the calibration evidence in
# bot/research/inference_calibration.py, never from which method yields more
# significance.
#
# FORMAL PROBLEM
#   y_t = sum of the next h returns (overlapping for h > 1, so y_t is MA(h−1)
#   even when returns are i.i.d.); x_t is a trailing feature, often highly
#   persistent. H0: x and y are independent. The IID Spearman/Pearson p-value
#   assumes n independent pairs; with both series autocorrelated the effective
#   sample is far smaller, so the IID p-value is anti-conservative
#   (audit F-02: 67.7 % size at nominal 5 % for h = 24).
#
# CANDIDATES (each returns a two-sided p-value)
#   iid_spearman_p        the current method (baseline, known-bad)
#   hac_spearman_p        OLS of rank(y) on rank(x) with a Newey–West (Bartlett)
#                         HAC standard error, maxlags = L
#   circular_shift_p      null distribution from random circular shifts of y
#                         against x (shifts kept away from 0 by a margin):
#                         preserves BOTH series' own autocorrelation exactly
#   block_permutation_p   y cut into blocks of length b, blocks shuffled
#                         (preserves y's dependence within blocks only)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import numpy as np
from scipy import stats

# claude code changed: F-02 — one label for every analytic p-value that assumes
# independent observations. Calibration (research/inference_calibration.py):
# size ≈ nominal for non-overlapping labels (h = 1), 27–60 % at nominal 5 % for
# overlapping labels with a persistent feature (h = 4, 24). Such a p-value is
# descriptive only until the owner-chosen dependence-aware method (D-2) is wired.
IID_PVALUE_BASIS = "IID_ANALYTIC_DESCRIPTIVE_ONLY_F02"


def _ranks(a: np.ndarray) -> np.ndarray:
    return stats.rankdata(a) / len(a)


def iid_spearman_p(x: np.ndarray, y: np.ndarray) -> float:
    return float(stats.spearmanr(x, y)[1])


def hac_spearman_p(x: np.ndarray, y: np.ndarray, maxlags: int) -> float:
    """Two-sided p of the slope in rank(y) = a + b·rank(x) + e with a
    Newey–West (Bartlett kernel) HAC covariance, `maxlags` lags."""
    import statsmodels.api as sm
    rx, ry = _ranks(np.asarray(x)), _ranks(np.asarray(y))
    res = sm.OLS(ry, sm.add_constant(rx)).fit(cov_type="HAC", cov_kwds={"maxlags": int(maxlags), "use_correction": True})
    return float(res.pvalues[1])


def circular_shift_p(x: np.ndarray, y: np.ndarray, n_shifts: int, min_shift: int, rng: np.random.Generator) -> float:
    """(k+1)/(B+1) with k = #{|ρ(x, roll(y, s))| >= |ρ(x, y)|}, s uniform on
    [min_shift, n − min_shift] (Spearman ρ)."""
    x, y = np.asarray(x), np.asarray(y)
    n = len(x)
    if n <= 2 * min_shift:
        raise ValueError("series too short for the requested min_shift")
    rx, ry = _ranks(x), _ranks(y)
    obs = abs(np.corrcoef(rx, ry)[0, 1])
    shifts = rng.integers(min_shift, n - min_shift, size=n_shifts)
    k = sum(1 for s in shifts if abs(np.corrcoef(rx, np.roll(ry, s))[0, 1]) >= obs)
    return (k + 1) / (n_shifts + 1)


def block_permutation_p(x: np.ndarray, y: np.ndarray, block: int, n_perm: int, rng: np.random.Generator) -> float:
    x, y = np.asarray(x), np.asarray(y)
    rx, ry = _ranks(x), _ranks(y)
    obs = abs(np.corrcoef(rx, ry)[0, 1])
    n = len(y)
    nb = int(np.ceil(n / block))
    blocks = [ry[i * block:(i + 1) * block] for i in range(nb)]
    k = 0
    for _ in range(n_perm):
        yy = np.concatenate([blocks[i] for i in rng.permutation(nb)])[:n]
        k += abs(np.corrcoef(rx, yy)[0, 1]) >= obs
    return (k + 1) / (n_perm + 1)
