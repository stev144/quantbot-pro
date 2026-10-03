# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/inference_calibration.py
#
# claude code changed: new file — 2026-10-03, remediation F-02. Calibration of
# the candidate tests in bot/quant_core/inference.py on synthetic data with
# KNOWN truth: Type-I error (H0 true) across overlap horizons h and feature
# persistence φ, and power under a known alternative. Synthetic only — says
# nothing about any market. Output: research_data/_governance/inference_calibration_2026-10-03.json
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import math
import time
from typing import Dict

import numpy as np

from bot.quant_core import inference as inf

N = 2000
R_SIZE = 300
R_POWER = 200
B = 99
HORIZONS = (1, 4, 24)
PERSISTENCE = (0.0, 0.9, 0.99)


def _ar1(n, phi, rng):
    e = rng.standard_normal(n + 500)
    x = np.zeros_like(e)
    for t in range(1, len(e)):
        x[t] = phi * x[t - 1] + e[t]
    return x[500:]


def _sample(h, phi, rng, ic=0.0):
    """x: AR(1) feature with persistence phi. y: forward h-sum of returns r,
    r_t = ic·x̃_t + noise, where x̃ is x standardized (ic = 0 ⇒ H0 true)."""
    x = _ar1(N + h, phi, rng)
    xs = (x - x.mean()) / x.std()
    r = ic * xs + rng.standard_normal(N + h)
    y = np.convolve(r, np.ones(h), "valid")[1:N + 1]          # y_t = r_{t+1} + … + r_{t+h}
    return x[:N], y


def _methods(h, phi):
    lag = max(1, 2 * h)                                        # HAC lags: 2h covers the MA(h−1) overlap with margin
    margin = max(5 * h, 50)
    return {
        "iid_spearman": lambda x, y, rng: inf.iid_spearman_p(x, y),
        f"hac_L{lag}": lambda x, y, rng: inf.hac_spearman_p(x, y, maxlags=lag),
        "hac_L_auto": lambda x, y, rng: inf.hac_spearman_p(x, y, maxlags=int(math.floor(4 * (N / 100) ** (2 / 9))) + 2 * h),
        "circular_shift": lambda x, y, rng: inf.circular_shift_p(x, y, n_shifts=B, min_shift=margin, rng=rng),
        f"block_perm_b{4 * h}": lambda x, y, rng: inf.block_permutation_p(x, y, block=4 * h, n_perm=B, rng=rng),
    }


def _rate(h, phi, ic, reps, seed) -> Dict[str, float]:
    rng = np.random.default_rng(seed)
    meths = _methods(h, phi)
    hits = {m: 0 for m in meths}
    for _ in range(reps):
        x, y = _sample(h, phi, rng, ic)
        for m, f in meths.items():
            hits[m] += f(x, y, rng) < 0.05
    return {m: round(float(v) / reps, 4) for m, v in hits.items()}


def run(out_path: str = "research_data/_governance/inference_calibration_2026-10-03.json") -> Dict:
    t0 = time.time()
    size, power = {}, {}
    for h in HORIZONS:
        for phi in PERSISTENCE:
            size[f"h{h}_phi{phi}"] = _rate(h, phi, 0.0, R_SIZE, seed=1000 * h + int(phi * 100))
            print("size", h, phi, size[f"h{h}_phi{phi}"], flush=True)
    for h, phi, ic in ((4, 0.9, 0.05), (24, 0.99, 0.05), (24, 0.9, 0.03)):
        power[f"h{h}_phi{phi}_ic{ic}"] = _rate(h, phi, ic, R_POWER, seed=7000 + h)
        print("power", h, phi, ic, power[f"h{h}_phi{phi}_ic{ic}"], flush=True)
    out = {"artifact": "inference_calibration", "reset_point": "CRRP-2026-10-02", "n": N, "reps_size": R_SIZE,
           "reps_power": R_POWER, "B": B, "alpha": 0.05,
           "se_at_alpha": round(math.sqrt(0.05 * 0.95 / R_SIZE), 4),
           "design": "x = AR(1)(phi); y = forward h-sum of r, r = ic·std(x) + N(0,1); ic=0 is H0",
           "size": size, "power": power, "runtime_s": round(time.time() - t0, 1)}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    return out


if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.getcwd())
    run(*sys.argv[1:])
