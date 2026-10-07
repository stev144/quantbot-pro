# ============================================================
# bot/quant_core/calibration.py
# claude code changed: new file — Stage 2D statistical-calibration tooling
# (Stage 2C §20, Rev B §4 equivalence gates).
#
# Tools only. The tolerances δ, confidence and power are owner decisions
# (Rev B OPEN-36), so nothing here declares a gate PASS on its own defaults —
# every gate call takes them explicitly.
# claude code changed: 2026-10-07 — OPEN-36 is now SIGNED (δ = 1 pt, α = 0.05, power 0.80, R = 3,202): use
# signed_size_gate() for any size claim; size_gate() stays the parameterised primitive.
#
#   * Wilson bounds for a rejection / coverage rate
#   * minimum repetitions M, found by EXACT binomial search under the Wilson
#     pass rule (Stage 2B found the closed-form shortcut under-delivers power:
#     0.67–0.77 instead of 0.80)
#   * null-world generators, including the never-run Student-t and GARCH
#     worlds (Stage 2C worlds 13/14)
#   * an Engle–Granger size runner on those worlds
# ============================================================

from __future__ import annotations

import math
from typing import Callable, Dict, Tuple

import numpy as np
import pandas as pd
from scipy.stats import binom, norm

from bot.quant_core.cointegration import engle_granger
from bot.quant_core.status import Result, Status, bad, ok


def wilson_bounds(k: int, n: int, confidence: float) -> Tuple[float, float]:
    """Two-sided Wilson score interval at `confidence` (e.g. 0.95)."""
    if n <= 0 or not (0 <= k <= n):
        raise ValueError("need 0 <= k <= n and n > 0")
    z = norm.ppf(0.5 + confidence / 2.0)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    hw = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - hw), min(1.0, c + hw)


def wilson_upper_one_sided(k: int, n: int, alpha: float) -> float:
    """One-sided upper bound at level 1−alpha (the size-gate statistic)."""
    return wilson_bounds(k, n, 1 - 2 * alpha)[1]


def wilson_lower_one_sided(k: int, n: int, alpha: float) -> float:
    """One-sided lower bound at level 1−alpha (the coverage-gate statistic)."""
    return wilson_bounds(k, n, 1 - 2 * alpha)[0]


def size_gate(k: int, n: int, *, nominal: float, delta: float, alpha: float) -> Result:
    """PASS iff the one-sided Wilson upper bound of the observed rejection rate
    is ≤ nominal + delta. δ and α are owner-declared (OPEN-36)."""
    ub = wilson_upper_one_sided(k, n, alpha)
    if ub <= nominal + delta:
        return ok({"rate": k / n, "upper": ub})
    return bad(Status.FAIL, f"upper bound {ub:.4f} > {nominal + delta:.4f}", value={"rate": k / n, "upper": ub})


def coverage_gate(k: int, n: int, *, nominal: float, delta: float, alpha: float) -> Result:
    """PASS iff the one-sided Wilson lower bound of observed coverage ≥ nominal − delta."""
    lb = wilson_lower_one_sided(k, n, alpha)
    if lb >= nominal - delta:
        return ok({"rate": k / n, "lower": lb})
    return bad(Status.FAIL, f"lower bound {lb:.4f} < {nominal - delta:.4f}", value={"rate": k / n, "lower": lb})


def pass_probability(n: int, true_rate: float, *, nominal: float, delta: float, alpha: float) -> float:
    """Exact P(size gate passes | true rejection rate). The Wilson upper bound
    increases with k, so the passing region is k ≤ k*; k* is found by
    binary search and the probability is the binomial CDF at k*."""
    threshold = nominal + delta
    lo, hi = -1, n                       # invariant: ub(lo) passes (or lo = −1), ub(hi) may fail
    if wilson_upper_one_sided(n, n, alpha) <= threshold:
        return 1.0
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if wilson_upper_one_sided(mid, n, alpha) <= threshold:
            lo = mid
        else:
            hi = mid
    return float(binom.cdf(lo, n, true_rate)) if lo >= 0 else 0.0


def minimum_repetitions(*, nominal: float, delta: float, alpha: float, power: float, n_max: int = 20000) -> int:
    """Smallest M such that a procedure whose TRUE rate equals `nominal`
    passes the size gate with probability ≥ `power` (exact binomial, Wilson
    rule). Starts from the exact-variance normal approximation and searches
    upward; the pass probability is not monotone in M (binomial lattice), so
    the first M that reaches `power` is returned."""
    if not (0 < nominal < 1 and delta > 0 and 0 < alpha < 0.5 and 0 < power < 1):
        raise ValueError("invalid calibration parameters")
    p1 = nominal + delta
    za, zb = norm.ppf(1 - alpha), norm.ppf(power)
    start = int(((za * math.sqrt(p1 * (1 - p1)) + zb * math.sqrt(nominal * (1 - nominal))) / delta) ** 2 * 0.8)
    for n in range(max(10, start), n_max + 1):
        if pass_probability(n, nominal, nominal=nominal, delta=delta, alpha=alpha) >= power:
            return n
    raise ValueError(f"no M ≤ {n_max} reaches power {power}")


# ── OPEN-36: the owner-signed size gate ───────────────────────────────────────
# claude code changed: 2026-10-07 — owner sign-off of OPEN-36 (DECISION_SHEET_OPEN36_D3_PROPOSAL_2026-10-07.md,
# signed 2026-10-07): nominal 5 %, δ = 1 percentage point, α = 0.05 (one-sided Wilson upper bound at 95 % ≤ 6 %),
# design power 0.80 → the replicate count below. These values may not be loosened for cost or after a failure.
OPEN36_NOMINAL = 0.05            # claude code changed: 2026-10-07 OPEN-36 (signed)
OPEN36_DELTA = 0.01              # claude code changed: 2026-10-07 OPEN-36 (signed)
OPEN36_ALPHA = 0.05              # claude code changed: 2026-10-07 OPEN-36 (signed)
OPEN36_POWER = 0.80              # claude code changed: 2026-10-07 OPEN-36 (signed)
OPEN36_MIN_REPS = 3202           # claude code changed: 2026-10-07 OPEN-36 (signed) — = minimum_repetitions(0.05, 0.01, 0.05, 0.80), pinned by test
OPEN36_DECISION_REF = "DECISION_SHEET_OPEN36_D3_PROPOSAL_2026-10-07.md (owner sign-off 2026-10-07)"   # claude code changed: 2026-10-07 OPEN-36


def signed_size_gate(k: int, n: int) -> Result:   # claude code changed: 2026-10-07 OPEN-36 (signed)
    """The OPEN-36 size criterion: at least OPEN36_MIN_REPS testable replicates AND the one-sided 95 % Wilson upper
    bound of the rejection rate ≤ 5 % + 1 pt. Fewer replicates is UNTESTABLE (never PASS); the Wilson criterion is
    never replaced by the observed rate."""
    if n < OPEN36_MIN_REPS:
        return bad(Status.UNTESTABLE, f"{n} testable replicates < the signed {OPEN36_MIN_REPS} (OPEN-36)",
                   value={"rate": (k / n) if n else None, "n": n})
    return size_gate(k, n, nominal=OPEN36_NOMINAL, delta=OPEN36_DELTA, alpha=OPEN36_ALPHA)


# ── null worlds (log prices; NO cointegration by construction) ────────────────

def _innovations(g: np.random.Generator, n: int, kind: str, scale: float) -> np.ndarray:
    if kind == "gaussian":
        return g.normal(0, scale, n)
    if kind == "student_t3":
        return g.standard_t(3, n) * scale / math.sqrt(3.0)          # unit variance scaled
    if kind == "garch":
        omega, a1, b1 = 0.05, 0.10, 0.85                               # persistent volatility clustering
        e, h = np.empty(n), omega / (1 - a1 - b1)
        z = g.normal(0, 1, n)
        for t in range(n):
            e[t] = math.sqrt(h) * z[t]
            h = omega + a1 * e[t] ** 2 + b1 * h
        return e * scale
    raise KeyError(kind)


def null_world(name: str, seed: int, n: int, innovations: str = "gaussian") -> Tuple[pd.Series, pd.Series]:
    """'rw' independent random walks; 'corr_rw' common factor, not
    cointegrated; 'betawalk' wandering β with a random-walk spread."""
    g = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC")
    ea, eb = _innovations(g, n, innovations, 0.01), _innovations(g, n, innovations, 0.01)
    if name == "rw":
        x, y = np.cumsum(ea), np.cumsum(eb)
    elif name == "corr_rw":
        f = _innovations(g, n, innovations, 0.01)
        x, y = np.cumsum(0.9 * f + 0.436 * ea), np.cumsum(f)
    elif name == "betawalk":
        beta = 1.0 + np.cumsum(g.normal(0, 0.005, n))
        y = np.cumsum(eb)
        x = beta * y + np.cumsum(ea)
    else:
        raise KeyError(name)
    return pd.Series(x, index=idx), pd.Series(y, index=idx)


def eg_size_run(world: str, *, innovations: str, M: int, n_obs: int, seed_base: int,
                level: float = 0.05) -> Dict[str, int]:
    """Count Engle–Granger rejections at `level` over M null replicates.
    UNTESTABLE replicates are counted separately — never dropped silently."""
    rejections = untestable = 0
    for i in range(M):
        x, y = null_world(world, seed_base + i, n_obs, innovations)
        res = engle_granger(x, y, min_obs=n_obs, maxlag=None, autolag="aic")
        if not res.ok:
            untestable += 1
        elif res.value.pvalue < level:
            rejections += 1
    return {"M": M, "rejections": rejections, "untestable": untestable}
