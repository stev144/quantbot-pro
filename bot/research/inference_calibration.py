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


def run_d10(out_path: str = "research_data/_governance/inference_calibration_D10_2026-10-03.json",
            b: int = 199) -> Dict:   # claude code changed: owner decisions D-2/D-10 — calibrate the APPROVED rule
    """Size and power of the D-2 confirmatory test exactly as wired (block = max(4h, 24)), on the same
    synthetic design as run(). B = 199 here for runtime (production uses 999; more permutations only
    reduce Monte-Carlo noise in p, they do not change the test's size)."""
    t0 = time.time()
    size, power = {}, {}
    def rate(h, phi, ic, reps, seed):
        rng = np.random.default_rng(seed)
        hits, blk = 0, inf.d10_block_length(h)
        for _ in range(reps):
            x, y = _sample(h, phi, rng, ic)
            hits += inf.block_permutation_p(x, y, blk, b, rng) < 0.05
        return round(hits / reps, 4)
    for h in HORIZONS:
        for phi in PERSISTENCE:
            size[f"h{h}_phi{phi}"] = rate(h, phi, 0.0, R_SIZE, 1000 * h + int(phi * 100) + 7)
            print("size", h, phi, size[f"h{h}_phi{phi}"], flush=True)
    for h, phi, ic in ((4, 0.9, 0.05), (24, 0.99, 0.05), (24, 0.9, 0.03)):
        power[f"h{h}_phi{phi}_ic{ic}"] = rate(h, phi, ic, R_POWER, 7000 + h + 7)
        print("power", h, phi, ic, power[f"h{h}_phi{phi}_ic{ic}"], flush=True)
    out = {"artifact": "inference_calibration_D10", "decision": "D-2/D-10", "method_version": inf.INFERENCE_METHOD_VERSION,
           "rule": "block = max(4h, 24)", "n": N, "reps_size": R_SIZE, "reps_power": R_POWER, "B": b, "alpha": 0.05,
           "se_at_alpha": round(math.sqrt(0.05 * 0.95 / R_SIZE), 4),
           "blocks": {f"h{h}": inf.d10_block_length(h) for h in HORIZONS},
           "size": size, "power": power, "runtime_s": round(time.time() - t0, 1)}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    return out


def run_d2_interaction(out_path: str = "research_data/_governance/inference_calibration_D2_interaction_2026-10-03.json",
                       b: int = 199, reps_size: int = 300, reps_power: int = 200) -> Dict:   # claude code changed: D-2
    """Size/power of the Freedman–Lane block-permutation interaction test (regime_conditional_ic.
    test_regime_interaction) on synthetic data with KNOWN truth: y = forward h-sum of r, r = β·x̃ + γ·x̃·1[regime B]
    + noise, regimes in long persistent spells, x AR(1) φ = 0.9. γ = 0 is H0 (main effect present, no
    interaction). Compared with the classical IID ANOVA p on the same samples."""
    import pandas as pd
    from bot.research.regime_conditional_ic import test_regime_interaction
    t0 = time.time()
    def sample(h, gamma, rng):
        x = _ar1(N + h, 0.9, rng)
        xs = (x - x.mean()) / x.std()
        spell = np.repeat(rng.integers(0, 2, size=(N + h) // 200 + 1), 200)[:N + h]
        r = 0.05 * xs + gamma * xs * spell + rng.standard_normal(N + h)
        y = np.convolve(r, np.ones(h), "valid")[1:N + 1]
        idx = pd.date_range("2024-01-01", periods=N, freq="h", tz="UTC")
        return pd.DataFrame({"f": x[:N], f"fwd_{h}h": y, "reg": np.where(spell[:N] == 1, "B", "A")}, index=idx)
    out = {}
    for h in (1, 4, 24):
        for gamma, reps, key in ((0.0, reps_size, "size"), (0.08, reps_power, "power_gamma0.08")):
            rng = np.random.default_rng(5000 + h + int(gamma * 1000))
            hit_fl = hit_iid = untestable = 0
            statuses = {}
            for i in range(reps):
                res = test_regime_interaction(sample(h, gamma, rng), "f", f"fwd_{h}h", "reg", n_perm=b, seed=i)
                if res["status"] != "OK":                    # e.g. one regime only — counted, never scored
                    untestable += 1
                    statuses[res["status"]] = statuses.get(res["status"], 0) + 1
                    continue
                hit_fl += res["p_value"] < 0.05
                hit_iid += res["p_value_iid_anova"] < 0.05
            n_ok = reps - untestable
            out[f"h{h}_{key}"] = {"freedman_lane_block": round(hit_fl / n_ok, 4), "iid_anova": round(hit_iid / n_ok, 4),
                                  "n_testable": n_ok, "untestable": statuses}
            print(h, key, out[f"h{h}_{key}"], flush=True)
    res = {"artifact": "inference_calibration_D2_interaction", "decision": "D-2/D-10", "method_version": inf.INFERENCE_METHOD_VERSION,
           "n": N, "B": b, "alpha": 0.05, "reps_size": reps_size, "reps_power": reps_power,
           "se_at_alpha": round(math.sqrt(0.05 * 0.95 / reps_size), 4), "results": out, "runtime_s": round(time.time() - t0, 1)}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    return res
