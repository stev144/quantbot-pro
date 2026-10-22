# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/pt_og_dca_calibration.py
#
# claude code changed: new file — 2026-10-21, PT-OG-DCA-1 preregistration §5 (owner: "go calibration").
# Calibrates the PRIMARY test BEFORE any arm exists: size (H0 true) and power (known drift) of the paired
# block sign-flip test on hourly net-equity differences (arm − baseline), under owner decisions
#   D-2  confirmatory test = block permutation (here: arm labels permuted within blocks = block sign-flip);
#   D-10 block = max(4·h, 24) candles, h = the decision horizon (72-bar time exit → 288; 84 → 336);
#        2·h / 8·h reported as sensitivity only;
#   D-3  B = 999, Holm FWER 0.05, at most 5 primary hypotheses → exact-resolution floor needs ≥ 8 blocks.
# Nulls are synthetic (known truth) plus one semi-real null built from the QC-1 GBP/JPY $5k baseline equity
# path of the development history (rows end before FX_RESEARCH_DATA_CUTOFF; nothing sealed is read).
# Research only: no strategy, configuration, order or live behaviour is touched.
# Output: research_data/pt_og_dca/calibration_2026-10-21.json
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Callable, Dict, Optional

import numpy as np
import pandas as pd
from scipy.signal import lfilter

from bot.quant_core.inference import CONFIRMATORY_B, d10_block_length, d10_sensitivity_blocks, stable_seed
from bot.research.fx_holdout_guard import FX_RESEARCH_DATA_CUTOFF

METHOD_VERSION = "PT-OG-DCA-1-CAL-1"
BASIS = "BLOCK_SIGN_FLIP_PAIRED_D2"
BARS_PER_WEEK = 120                       # FX hourly: 24 h × 5 trading days
HORIZON_WEEKS = (26, 52, 78)              # 6 / 12 / 18 months (prereg §7)
DECISION_HORIZONS = {"T1-T5 (72-bar time exit)": 72, "H6 (84-bar time exit)": 84}
ALPHA_FWER = 0.05
FAMILY_CAP = 5                            # D-3
R_SIZE = 2000
R_POWER = 1000
EFFECTS_USD_PER_WEEK = (5.0, 10.0, 20.0, 40.0, 80.0)
DIFF_SCALES = (1.0, 0.5, 0.25)            # SD of the arm−baseline difference relative to an independent copy
BASELINE_ROWS = Path("research_data/dca_survival/2026-10-16_0800/runs/gbpjpy_5000_rows.csv")
OUT_PATH = Path("research_data/pt_og_dca/calibration_2026-10-21.json")


def min_blocks_for(alpha: float = ALPHA_FWER, m: int = FAMILY_CAP) -> int:
    """Smallest block count k whose exact two-sided sign-flip floor 2^(1−k) reaches Holm's first threshold α/m."""
    return int(math.ceil(1 + math.log2(m / alpha)))


def block_sums(d: np.ndarray, block: int) -> np.ndarray:
    """Sums of consecutive length-`block` chunks; a shorter final chunk is its own block (no data dropped)."""
    d = np.asarray(d, float)
    return np.add.reduceat(d, np.arange(0, len(d), int(block)))


def block_sign_flip_test(d, block: int, *, seed: int, n_perm: int = CONFIRMATORY_B,
                         min_blocks: Optional[int] = None) -> Dict:
    """Two-sided paired test of H0: arm and baseline labels are exchangeable within each block.

    Statistic |Σ d|; null = |Σ ε_b S_b| with random ε_b = ±1 per block sum S_b; p = (k+1)/(B+1).
    Fails closed (status UNTESTABLE, p_value None) with non-finite input or too few blocks — never a substitute."""
    d = np.asarray(d, float)
    mb = min_blocks_for() if min_blocks is None else int(min_blocks)
    out = {"method_version": METHOD_VERSION, "basis": BASIS, "block": int(block), "n_perm": int(n_perm),
           "seed": int(seed), "n": int(len(d)), "min_blocks": mb, "p_floor": 1.0 / (n_perm + 1)}
    if len(d) == 0 or not np.isfinite(d).all():
        return {**out, "status": "UNTESTABLE", "p_value": None, "reason": "empty or non-finite differences"}
    s = block_sums(d, block)
    if len(s) < mb:
        return {**out, "status": "UNTESTABLE", "p_value": None, "n_blocks": int(len(s)),
                "reason": f"{len(s)} blocks < {mb} needed for the exact floor to reach alpha/m"}
    obs = abs(s.sum())
    eps = np.random.default_rng(seed).choice((-1.0, 1.0), size=(int(n_perm), len(s)))
    k = int((np.abs(eps @ s) >= obs * (1.0 - 1e-12)).sum())
    return {**out, "status": "OK", "n_blocks": int(len(s)), "p_value": (k + 1) / (n_perm + 1),
            "mean_usd_per_week": float(d.mean() * BARS_PER_WEEK), "positive": bool(s.sum() > 0)}


# ── null data-generating processes (every one has mean difference exactly 0) ──────────────────────────────

def _t3(n, rng):
    return rng.standard_t(3, n) / math.sqrt(3.0)          # unit variance


def _ar1(e, phi):
    return lfilter([1.0], [1.0, -phi], e)


def _skewed_lumps(n, rng, clustered=False):
    """DCA-shaped: lumps at ~2.4 per week; 90 % small gains (Exp mean 1), 10 % large losses (Exp mean 9) → mean 0."""
    hit = rng.random(n) < 2.4 / BARS_PER_WEEK
    gain = rng.random(n) < 0.9
    lump = np.where(gain, rng.exponential(1.0, n), -rng.exponential(9.0, n))
    if clustered:   # persistent volatility state (half-life ≈ 700 bars), mean of the scale kept at 1
        h = _ar1(rng.standard_normal(n) * 0.05, 0.999)
        lump = lump * np.exp(h - h.var() / 2)
    return np.where(hit, lump, 0.0)


SYNTHETIC_NULLS: Dict[str, Callable] = {
    "iid_normal": lambda n, rng: rng.standard_normal(n),
    "iid_t3": _t3,
    "ar1_0.99_t3": lambda n, rng: _ar1(_t3(n, rng), 0.99),
    "ar1_0.999_t3 (stress: dependence longer than the block)": lambda n, rng: _ar1(_t3(n, rng), 0.999),
    "skewed_lumps": lambda n, rng: _skewed_lumps(n, rng),
    "skewed_lumps_vol_clustered": lambda n, rng: _skewed_lumps(n, rng, clustered=True),
}


def load_baseline_increments(path: Path = BASELINE_ROWS, rows: Optional[pd.DataFrame] = None) -> np.ndarray:
    """Hourly equity increments of the QC-1 baseline (development history). Refuses any row at or after the seal."""
    df = rows if rows is not None else pd.read_csv(path)
    ts = pd.to_datetime(df["timestamp"], utc=True)
    if (ts >= FX_RESEARCH_DATA_CUTOFF).any():
        raise ValueError(f"baseline rows reach the sealed window (>= {FX_RESEARCH_DATA_CUTOFF}); refusing to read")
    x = np.diff(df["equity"].to_numpy(float))
    return x[np.isfinite(x)]


def semi_real_null(x: np.ndarray) -> Callable:
    """d_t = x_t − x_{t+s}: two far-apart windows of the real path (s uniform in [N/4, 3N/4], circular).
    Real tails and dependence, symmetric, E[d] = 0. Its variance is that of two INDEPENDENT paths — an upper
    bound for a real arm−baseline difference, which shares most cycles (hence DIFF_SCALES in the power runs)."""
    N = len(x)

    def gen(n, rng):
        start, s = rng.integers(0, N), rng.integers(N // 4, 3 * N // 4)
        idx = (start + np.arange(n)) % N
        return x[idx] - x[(idx + s) % N]
    return gen


# ── Monte Carlo ───────────────────────────────────────────────────────────────────────────────────────────

def _rates(gen: Callable, n: int, blocks, reps: int, seed: int, drift_per_bar: float = 0.0, scale: float = 1.0,
           test: Optional[Callable] = None) -> Dict:  # claude code changed: 2026-10-21 addendum — pluggable test
    rng = np.random.default_rng(seed)
    p = {b: [] for b in blocks}
    pos = {b: [] for b in blocks}
    for r in range(reps):
        d = scale * gen(n, rng) + drift_per_bar
        for b in blocks:
            res = (test or block_sign_flip_test)(d, b, seed=stable_seed(seed, r, b))  # claude code changed: 2026-10-21 addendum
            if res["status"] != "OK":
                p[b] = None
                continue
            p[b].append(res["p_value"])
            pos[b].append(res["positive"])
    out = {}
    for b in blocks:
        if p[b] is None:
            out[str(b)] = {"status": "UNTESTABLE", "n_blocks": int(math.ceil(n / b)), "min_blocks": min_blocks_for()}
            continue
        pv, ps = np.array(p[b]), np.array(pos[b])
        out[str(b)] = {
            "n_blocks": int(math.ceil(n / b)),
            "two_sided_05": float((pv <= 0.05).mean()),
            "directional_05": float(((pv <= 0.05) & ps).mean()),
            "two_sided_01": float((pv <= 0.01).mean()),
            "directional_01": float(((pv <= 0.01) & ps).mean()),
            "mc_se_at_05": math.sqrt(0.05 * 0.95 / reps),
        }
    return out


def _size_verdict(cell: Dict, reps: int) -> str:
    if cell.get("status") == "UNTESTABLE":
        return "UNTESTABLE"
    tol = 2 * math.sqrt(0.05 * 0.95 / reps)
    return "PASS" if cell["two_sided_05"] <= 0.05 + tol else "FAIL"


def run(out_path: Path = OUT_PATH, r_size: int = R_SIZE, r_power: int = R_POWER) -> Dict:
    t0 = time.time()
    primary = sorted({d10_block_length(h) for h in DECISION_HORIZONS.values()})
    sens = sorted({b for h in DECISION_HORIZONS.values() for b in d10_sensitivity_blocks(h)})
    blocks = sorted(set(primary) | set(sens))
    x = load_baseline_increments()
    nulls = {**SYNTHETIC_NULLS, "semi_real_gbpjpy_baseline": semi_real_null(x)}

    size = {}
    for name, gen in nulls.items():
        size[name] = {}
        for w in HORIZON_WEEKS:
            cells = _rates(gen, w * BARS_PER_WEEK, blocks, r_size, stable_seed("size", name, w))
            for b, c in cells.items():
                c["verdict"] = _size_verdict(c, r_size)
                c["role"] = "PRIMARY" if int(b) in primary else "SENSITIVITY"
            size[name][f"{w}w"] = cells

    gen = semi_real_null(x)
    power = {}
    for s in DIFF_SCALES:
        for eff in EFFECTS_USD_PER_WEEK:
            key = f"scale_{s}_effect_{eff:g}_usd_per_week"
            power[key] = {f"{w}w": _rates(gen, w * BARS_PER_WEEK, primary, r_power,
                                          stable_seed("power", s, eff, w), drift_per_bar=eff / BARS_PER_WEEK, scale=s)
                          for w in HORIZON_WEEKS}

    weekly = pd.Series(x[: len(x) // BARS_PER_WEEK * BARS_PER_WEEK]).groupby(np.arange(len(x) // BARS_PER_WEEK * BARS_PER_WEEK) // BARS_PER_WEEK).sum()
    result = {
        "method_version": METHOD_VERSION, "basis": BASIS, "decisions": ["D-2", "D-3", "D-10"],
        "decision_horizons": DECISION_HORIZONS, "primary_blocks": primary, "sensitivity_blocks": sens,
        "n_perm": CONFIRMATORY_B, "alpha_fwer": ALPHA_FWER, "family_cap": FAMILY_CAP,
        "holm_first_threshold": ALPHA_FWER / FAMILY_CAP, "min_blocks": min_blocks_for(),
        "bars_per_week": BARS_PER_WEEK, "r_size": r_size, "r_power": r_power,
        "size_rule": "PASS iff two-sided rejection at 0.05 <= 0.05 + 2 Monte Carlo SE",
        "power_rule": "directional_01 = P(p <= alpha/m AND arm ahead) — Holm's first step with m = 5",
        "baseline_context": {"source": str(BASELINE_ROWS), "hours": int(len(x)),
                             "mean_usd_per_week": float(x.mean() * BARS_PER_WEEK),
                             "weekly_sd_usd": float(weekly.std(ddof=1)),
                             "data_cutoff": str(FX_RESEARCH_DATA_CUTOFF)},
        "size": size, "power": power, "runtime_s": round(time.time() - t0, 1),
        "scope": "synthetic + semi-real nulls only; says nothing about any arm's edge",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


# ── addendum (2026-10-21): candidates prompted by run 1, chosen by SIZE only ─────────────────────────────────
# claude code changed: 2026-10-21 addendum — run 1 showed (a) sign-flip over-rejects, in the "arm ahead" direction,
# under skewed zero-mean lumps (symmetry is violated: frequent small gains vs rare large losses — the exact shape of
# a profit-target arm) and under persistence comparable to the block; (b) on MTM equity increments the null is far
# too wide (drawdowns revert across blocks), so size collapses to ~0.001 and power to ~0. Candidates below:
# realised-balance increments as the unit, and a block bootstrap-t (not D-2 — owner decision if adopted).
OUT_PATH_ADDENDUM = Path("research_data/pt_og_dca/calibration_addendum_2026-10-21.json")


def block_bootstrap_t_test(d, block: int, *, seed: int, n_boot: int = CONFIRMATORY_B,
                           min_blocks: Optional[int] = None) -> Dict:
    """Two-sided bootstrap-t for H0: E[d] = 0 on non-overlapping block sums (no symmetry assumption).
    T = mean/se of the k block sums; T* = (mean* − mean)/se* from k-of-k resamples; p = (#|T*| >= |T| + 1)/(B+1)."""
    d = np.asarray(d, float)
    mb = min_blocks_for() if min_blocks is None else int(min_blocks)
    out = {"basis": "BLOCK_BOOTSTRAP_T_CANDIDATE_NOT_D2", "block": int(block), "n_perm": int(n_boot), "seed": int(seed)}
    if len(d) == 0 or not np.isfinite(d).all():
        return {**out, "status": "UNTESTABLE", "p_value": None}
    s = block_sums(d, block)
    k = len(s)
    if k < mb or s.std(ddof=1) == 0:
        return {**out, "status": "UNTESTABLE", "p_value": None, "n_blocks": int(k)}
    t_obs = s.mean() / (s.std(ddof=1) / math.sqrt(k))
    bs = s[np.random.default_rng(seed).integers(0, k, size=(int(n_boot), k))]
    se = bs.std(axis=1, ddof=1) / math.sqrt(k)
    with np.errstate(divide="ignore", invalid="ignore"):
        t_star = np.where(se > 0, (bs.mean(axis=1) - s.mean()) / se, 0.0)
    kk = int((np.abs(t_star) >= abs(t_obs)).sum())
    return {**out, "status": "OK", "n_blocks": int(k), "p_value": (kk + 1) / (n_boot + 1), "positive": bool(s.sum() > 0)}


def load_baseline_balance_increments(path: Path = BASELINE_ROWS) -> np.ndarray:
    """Realised-cash increments (closed-cycle P&L + swap) of the same baseline; same seal check as the equity loader."""
    df = pd.read_csv(path)
    if (pd.to_datetime(df["timestamp"], utc=True) >= FX_RESEARCH_DATA_CUTOFF).any():
        raise ValueError("baseline rows reach the sealed window; refusing to read")
    x = np.diff(df["balance"].to_numpy(float))
    return x[np.isfinite(x)]


def run_addendum(out_path: Path = OUT_PATH_ADDENDUM, r_size: int = R_SIZE, r_power: int = R_POWER) -> Dict:
    t0 = time.time()
    primary = sorted({d10_block_length(h) for h in DECISION_HORIZONS.values()})
    nulls = {
        "skewed_lumps": SYNTHETIC_NULLS["skewed_lumps"],
        "skewed_lumps_vol_clustered": SYNTHETIC_NULLS["skewed_lumps_vol_clustered"],
        "ar1_0.99_t3": SYNTHETIC_NULLS["ar1_0.99_t3"],
        "iid_t3": SYNTHETIC_NULLS["iid_t3"],
        "semi_real_equity_mtm": semi_real_null(load_baseline_increments()),
        "semi_real_balance_realised": semi_real_null(load_baseline_balance_increments()),
    }
    tests = {"sign_flip_D2": block_sign_flip_test, "bootstrap_t_candidate": block_bootstrap_t_test}
    size = {t: {name: {f"{w}w": _rates(g, w * BARS_PER_WEEK, primary, r_size, stable_seed("add-size", t, name, w), test=fn)
                       for w in HORIZON_WEEKS} for name, g in nulls.items()} for t, fn in tests.items()}
    for t in size.values():
        for hz in t.values():
            for cells in hz.values():
                for c in cells.values():
                    c["verdict"] = _size_verdict(c, r_size)
    g = nulls["semi_real_balance_realised"]
    power = {t: {f"scale_{sc}_effect_{eff:g}": {f"{w}w": _rates(g, w * BARS_PER_WEEK, primary, r_power,
                                                                  stable_seed("add-power", t, sc, eff, w),
                                                                  drift_per_bar=eff / BARS_PER_WEEK, scale=sc, test=fn)
                                                 for w in HORIZON_WEEKS}
                 for sc in DIFF_SCALES for eff in EFFECTS_USD_PER_WEEK} for t, fn in tests.items()}
    res = {"method_version": METHOD_VERSION + "-ADD", "primary_blocks": primary, "size": size, "power": power,
           "r_size": r_size, "r_power": r_power, "runtime_s": round(time.time() - t0, 1),
           "selection_rule": "a candidate is admissible only if every primary-block size cell PASSes; power is never used to choose"}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(res, indent=2), encoding="utf-8")
    return res


if __name__ == "__main__":
    import sys  # claude code changed: 2026-10-21 addendum switch
    r = run_addendum() if "--addendum" in sys.argv else run()
    print(json.dumps({k: r[k] for k in ("primary_blocks", "runtime_s")}))
