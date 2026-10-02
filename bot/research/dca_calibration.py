# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/dca_calibration.py
#
# claude code changed: new file — Post-CRRP Task 5 (DCA statistical calibration),
# 2026-10-02. Answers "can the proposed DCA significance experiment answer its
# question?" BEFORE it is run: Type-I error of the canonical nulls on synthetic
# data where H0 is known to be true, power on synthetic data with a planted
# effect, block-length sensitivity, and the resolution / family arithmetic.
# Synthetic data only — no real asset is tested here, so nothing in this file
# is evidence about any market.
#
# PRE-DECLARED HYPOTHESIS (DCA_RESEARCH_PROTOCOL_2026-10-02.md §4):
#   H1 (primary)  the canonical baseline LONG ladder (BASELINE_CONFIG,
#                 fill_model NEXT_OPEN) earns more mark-to-market P&L — and
#                 more MTM P&L per dollar of maximum capital — on the real
#                 path than on paths built from the SAME candles re-ordered in
#                 blocks (drift, endpoints and candle shapes preserved).
#   H0            the path is exchangeable with its block-shuffled versions at
#                 the chosen block length (no ordering structure beyond the
#                 block that the ladder can exploit).
#   Test          one-sided empirical permutation p = (k+1)/(B+1) on both
#                 statistics; reject only if BOTH p < alpha (intersection-
#                 union). DRIFT_REMOVED is NOT a test of H1: it asks whether
#                 the ladder makes money without the drift (attribution).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import replace
from typing import Dict, List

import numpy as np
import pandas as pd

from bot.research import dca_engine as de

N_CANDLES = 2000
HOURLY_VOL = 0.01
DRIFT_PER_HOUR = 0.0002


def synthetic_ohlc(kind: str, seed: int, n: int = N_CANDLES, strength: float = 0.0) -> pd.DataFrame:
    """Synthetic 1h OHLC with KNOWN structure.
    rw_drift   iid normal log returns + drift: increments exchangeable → H0 exactly true
    garch      GARCH(1,1) increments + drift: volatility clustering (ordering structure that
               is NOT mean reversion) → probes size under realistic dependence
    mr         drift + random walk + an AR(1) mean-reverting component of strength `strength`
               (stationary deviation with persistence 0.995/h) → a planted effect for power
    Opens are the previous close (no gaps); high/low envelope the open/close by a small
    random amount (intrabar shape only, never used by NEXT_OPEN decisions)."""
    rng = np.random.default_rng(seed)
    eps = rng.standard_normal(n)
    if kind == "rw_drift":
        r = DRIFT_PER_HOUR + HOURLY_VOL * eps
    elif kind == "garch":
        omega, alpha, beta = 2e-6, 0.08, 0.90                      # unconditional var = omega/(1-a-b) = 1e-4 → 1 %/h
        var = np.empty(n); var[0] = omega / (1 - alpha - beta)
        r = np.empty(n)
        for t in range(n):
            if t:
                var[t] = omega + alpha * r[t - 1] ** 2 + beta * var[t - 1]
            r[t] = math.sqrt(var[t]) * eps[t]
        r = r + DRIFT_PER_HOUR
    elif kind == "mr":
        phi = 0.995
        x = np.zeros(n)
        u = rng.standard_normal(n)
        for t in range(1, n):
            x[t] = phi * x[t - 1] + strength * u[t]
        r = DRIFT_PER_HOUR + HOURLY_VOL * eps + np.diff(np.concatenate([[0.0], x]))
    else:
        raise ValueError(kind)
    close = 100.0 * np.exp(np.cumsum(r))
    open_ = np.concatenate([[100.0], close[:-1]])
    wig = np.abs(rng.standard_normal((2, n))) * 0.002
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * (1 + wig[0]),
                         "low": np.minimum(open_, close) * (1 - wig[1]), "close": close},
                        index=pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"))


def one_replication(args) -> Dict:
    kind, strength, null_model, block, n_perm, seed = args
    df = synthetic_ohlc(kind, seed, strength=strength)
    r = de.run_dca_permutation_test(df, replace(de.BASELINE_CONFIG, fill_model="NEXT_OPEN"), n_permutations=n_perm,
                                    block_size=block, seed=seed + 7, null_model=null_model)
    return {"p_mtm": r["p_value_mtm_pnl"], "p_ratio": r["p_value_mtm_to_max_exposure_ratio"],
            "real_mtm": r["real_total_mtm_pnl"]}


def rejection_summary(rows: List[Dict], alpha: float = 0.05) -> Dict:
    n = len(rows)
    rej = sum(1 for r in rows if r["p_mtm"] < alpha and r["p_ratio"] < alpha)
    rate = rej / n
    se = math.sqrt(rate * (1 - rate) / n) if n else float("nan")
    return {"n_replications": n, "rejections": rej, "rejection_rate": round(rate, 4),
            "wilson_95ci": _wilson(rej, n), "naive_se": round(se, 4),
            "mean_p_mtm": round(float(np.mean([r["p_mtm"] for r in rows])), 4)}


def _wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def required_permutations(m_tests: int, alpha_family: float = 0.05, k_discoveries: int = 1) -> int:
    """Smallest B whose floor 1/(B+1) can pass Bonferroni / BH-at-rank-k for m tests:
    1/(B+1) <= alpha·k/m  ⇔  B >= m/(alpha·k) − 1."""
    return int(math.ceil(m_tests / (alpha_family * k_discoveries))) - 1


SCENARIOS = [
    # name,                       kind,       strength, null,                 block, reps
    ("size_iid_block24",          "rw_drift", 0.0,      de.NULL_BLOCK_SHUFFLE, 24,   200),
    ("size_iid_block168",         "rw_drift", 0.0,      de.NULL_BLOCK_SHUFFLE, 168,  200),
    ("size_garch_block24",        "garch",    0.0,      de.NULL_BLOCK_SHUFFLE, 24,   200),
    ("size_garch_block168",       "garch",    0.0,      de.NULL_BLOCK_SHUFFLE, 168,  200),
    ("power_mr_0.004_block168",   "mr",       0.004,    de.NULL_BLOCK_SHUFFLE, 168,  100),
    ("power_mr_0.008_block168",   "mr",       0.008,    de.NULL_BLOCK_SHUFFLE, 168,  100),
    ("drift_removed_on_iid",      "rw_drift", 0.0,      de.NULL_DRIFT_REMOVED, 168,  100),
]


def _init_worker():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()


def run_calibration(out_path: str, n_perm: int = 99, processes: int = 7, base_seed: int = 20261002) -> Dict:
    from multiprocessing import Pool
    t0 = time.time()
    results = {}
    with Pool(processes, initializer=_init_worker) as pool:
        for k, (name, kind, strength, null, block, reps) in enumerate(SCENARIOS):
            jobs = [(kind, strength, null, block, n_perm, base_seed + 100000 * k + i) for i in range(reps)]
            rows = pool.map(one_replication, jobs)
            results[name] = {"kind": kind, "strength": strength, "null_model": null, "block": block,
                             "n_permutations": n_perm, **rejection_summary(rows)}
            print(name, results[name], flush=True)
    out = {
        "artifact": "dca_null_calibration", "reset_point": "CRRP-2026-10-02",
        "evidence_class": "CANONICAL (methodological calibration on synthetic data — not evidence about any market)",
        "code_version": "UNCOMMITTED working tree on base 565e55d (owner commit pending)",
        "config": "BASELINE_CONFIG, fill_model NEXT_OPEN", "n_candles": N_CANDLES, "hourly_vol": HOURLY_VOL,
        "drift_per_hour": DRIFT_PER_HOUR, "alpha": 0.05, "decision_rule": "reject iff p_mtm < alpha AND p_ratio < alpha",
        "base_seed": base_seed, "runtime_seconds": round(time.time() - t0, 1), "scenarios": results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.getcwd())
    _init_worker()
    run_calibration(sys.argv[1] if len(sys.argv) > 1 else "research_data/_governance/dca_calibration_CRRP-2026-10-02.json")
