# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/step17_calibration.py
# claude code changed: new file — 2026-10-07, D-4 Step-17 null SIZE calibration under the owner-signed OPEN-36
# criterion (DECISION_SHEET_OPEN36_D3_PROPOSAL_2026-10-07.md, signed 2026-10-07).
#
#   Scenario (D-4 sheet, signed 2026-10-03): UNRELATED random-walk pairs — two independent Gaussian log random walks
#   over the full canonical span (Kalman init window → end of zone V), so the canonical pipeline runs exactly as it
#   would on real data (causal seed at the S start, D-3 endpoint on label-complete zone-V decisions).
#   Each replicate = one complete Step-17 test: the observed statistic + B = 999 circular-shift null statistics, each
#   a full canonical pipeline run (bot.research.pairs_step17_null). Reject at p ≤ 0.05.
#   Verdict: OPEN-36 signed size gate (≥ 3,202 TESTABLE replicates; one-sided 95 % Wilson upper bound ≤ 6 %) AND the
#   signed D-4 observed-size band (3–7 %). A replicate whose test is UNTESTABLE (no zone-V trade on the observed pair
#   or on any shift) is recorded and reported, and does NOT count towards the 3,202 — counting it as a non-rejection
#   would flatter the size.
#   Never loosened: R, B, δ, α are fixed constants; other values run only as a labelled PILOT (never evidence).
#   Resumable: every finished replicate is appended (fsync) to a checkpoint keyed by the exact code state; a run only
#   resumes on identical code. The canonical gate artifact (research_data/_governance/step17_null_calibration.json)
#   is NEVER written here: the D-4 gate also needs a power calibration whose criterion the owner has not specified.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from __future__ import annotations

import json
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

CALIBRATION_VERSION = "QBP-D4-SIZE-CAL-1"
SIGNED_B = 999                       # D-4 (signed 2026-10-03)
REJECT_AT = 0.05                     # nominal α of the test being calibrated
SCENARIOS = {
    "RW_GAUSSIAN_UNRELATED": {
        "description": "two independent Gaussian log random walks (no relationship by construction), hourly, "
                       "full canonical span: Kalman init window start → zone-V end",
        "sigma_a": 0.01, "sigma_b": 0.01, "start_a": 100.0, "start_b": 50.0, "source": "D-4 sheet 2026-10-03",
    },
}
OUT_DIR = Path("research_data") / "_governance" / "step17_calibration"


def canonical_index() -> pd.DatetimeIndex:
    from bot.quant_core.research_zones import ZONES
    return pd.date_range(ZONES.init_window()[0], ZONES.V1, freq="h")


def scenario_pair(scenario: str, rep: int, index: Optional[pd.DatetimeIndex] = None):
    """The rep-th null pair of a scenario — deterministic from (scenario, rep)."""
    from bot.quant_core.inference import stable_seed
    sc = SCENARIOS[scenario]
    idx = canonical_index() if index is None else index
    rng = np.random.default_rng(stable_seed(CALIBRATION_VERSION, scenario, "pair", rep))
    n = len(idx)
    a = pd.Series(sc["start_a"] * np.exp(np.cumsum(rng.normal(0.0, sc["sigma_a"], n))), index=idx, name="X_USDT")
    b = pd.Series(sc["start_b"] * np.exp(np.cumsum(rng.normal(0.0, sc["sigma_b"], n))), index=idx, name="Y_USDT")
    return a, b


def _worker_init():
    import logging
    import django
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    logging.disable(logging.CRITICAL)


def run_replicate(scenario: str, rep: int, n_perm: int, statistic=None) -> dict:
    """One complete Step-17 test on the rep-th null pair."""
    from bot.quant_core.inference import stable_seed
    from bot.research import pairs_step17_null as nl
    t = time.perf_counter()
    a, b = scenario_pair(scenario, rep)
    seed = stable_seed(CALIBRATION_VERSION, scenario, "perm", rep)
    r = nl.step17_null_test(a, b, statistic or nl.canonical_net_mean_return_per_trade, seed=seed, n_perm=n_perm,
                            canonical=False)
    p = r.get("p_value")
    return {"rep": rep, "seed": seed, "status": r["status"], "p_value": p,
            "statistic": r.get("statistic"), "reject": bool(p is not None and p <= REJECT_AT),
            "seconds": round(time.perf_counter() - t, 2)}


def evaluate(records, *, target: int) -> dict:
    """The signed verdict from replicate records (pure; used for both final and interim reports)."""
    from bot.quant_core.calibration import OPEN36_ALPHA, OPEN36_MIN_REPS, signed_size_gate, wilson_bounds, \
        wilson_upper_one_sided
    from bot.research.pairs_step17_null import SIZE_BAND
    ok = [r for r in records if r["status"] == "OK"]
    n, k = len(ok), sum(r["reject"] for r in ok)
    out = {"n_replicates_run": len(records), "n_testable": n, "n_untestable": len(records) - n,
           "rejections": k, "rejection_rate": (k / n) if n else None,
           "wilson_upper_one_sided_95": float(wilson_upper_one_sided(k, n, OPEN36_ALPHA)) if n else None,
           "wilson_two_sided_95": [float(x) for x in wilson_bounds(k, n, 0.95)] if n else None,
           "nominal": 0.05, "allowed_upper_bound": 0.06, "d4_observed_band": list(SIZE_BAND),
           "required_testable_replicates": OPEN36_MIN_REPS, "target_testable_replicates": target}
    if n < target:
        out.update(status="INCOMPLETE", reason=f"{n} of {target} testable replicates")
        return out
    if target < OPEN36_MIN_REPS:   # a pilot target: never a calibration verdict, in either direction
        out.update(status="NOT_EVIDENCE", reason=f"pilot of {target} replicates < the signed {OPEN36_MIN_REPS} — no verdict")
        return out
    gate = signed_size_gate(k, n)
    in_band = SIZE_BAND[0] <= k / n <= SIZE_BAND[1]
    out["open36_gate"] = "PASS" if gate.ok else gate.status.value
    out["d4_band"] = "PASS" if in_band else "FAIL"
    out["status"] = "PASS" if (gate.ok and in_band) else "FAIL"
    out["reason"] = "" if out["status"] == "PASS" else (gate.reason or f"observed size {k / n:.4f} outside the D-4 band")
    return out


def _run_key(scenario, n_perm, target, pilot, code) -> str:
    from bot.quant_core.provenance import config_hash
    from bot.research.pairs_step17_null import NULL_VERSION
    return config_hash({"cal": CALIBRATION_VERSION, "null": NULL_VERSION, "scenario": scenario, "n_perm": n_perm,
                        "target": target, "pilot": pilot, "commit": code.commit, "dirty": code.dirty_digest})[:16]


def run_size_calibration(scenario: str = "RW_GAUSSIAN_UNRELATED", *, target: Optional[int] = None,
                         n_perm: int = SIGNED_B, workers: int = 1, pilot: bool = False,
                         out_dir: Path = OUT_DIR, log=print) -> dict:
    """Run (or resume) the size calibration until `target` testable replicates exist, then write the result."""
    from bot.quant_core.calibration import OPEN36_DECISION_REF, OPEN36_MIN_REPS
    from bot.quant_core.protocol import METHODOLOGY_VERSION
    from bot.quant_core.provenance import code_state, file_sha256, input_frames_fingerprint, write_research_output_provenance
    from bot.research.pairs_step17_null import NULL_VERSION
    target = OPEN36_MIN_REPS if target is None else int(target)
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario}")
    if not pilot and (target < OPEN36_MIN_REPS or n_perm != SIGNED_B):
        raise ValueError(f"a calibration run uses the signed values (R >= {OPEN36_MIN_REPS}, B = {SIGNED_B}); "
                         f"anything else must be run with pilot=True and is never evidence")
    code = code_state()
    key = _run_key(scenario, n_perm, target, pilot, code)
    out_dir = Path(out_dir) / ("pilot" if pilot else "")
    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt = out_dir / f"step17_size_{scenario}_{key}.jsonl"
    records = []
    if ckpt.exists():
        records = [json.loads(line) for line in ckpt.read_text(encoding="utf-8").splitlines() if line.strip()]
        log(f"resuming {ckpt.name}: {len(records)} replicates already done")
    started = time.time()
    done = {r["rep"] for r in records}
    next_rep = (max(done) + 1) if done else 0

    def testable():
        return sum(r["status"] == "OK" for r in records)

    with open(ckpt, "a", encoding="utf-8") as fh, ProcessPoolExecutor(max_workers=workers, initializer=_worker_init) as pool:
        running = {}
        while testable() < target or running:
            while testable() + len(running) < target and len(running) < workers:
                running[pool.submit(run_replicate, scenario, next_rep, n_perm)] = next_rep
                next_rep += 1
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for f in finished:
                running.pop(f)
                rec = f.result()
                records.append(rec)
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                n_ok = testable()
                if n_ok % 10 == 0 or rec["status"] != "OK":
                    log(f"rep {rec['rep']}: {rec['status']} p={rec['p_value']} ({rec['seconds']} s) — "
                        f"{n_ok}/{target} testable, {sum(r['reject'] for r in records)} rejections")
    records.sort(key=lambda r: r["rep"])
    result = evaluate(records, target=target)
    a0, b0 = scenario_pair(scenario, 0)
    summary = {
        "artifact": "step17_size_calibration", "calibration_version": CALIBRATION_VERSION, "label":
            "PILOT — TIMING/IMPLEMENTATION CHECK ONLY, NOT CALIBRATION EVIDENCE" if pilot else "CURRENT CALIBRATION ATTEMPT (OPEN-36)",
        "run_id": key, "scenario": scenario, "scenario_definition": SCENARIOS[scenario],
        "null_generating_process": "independent Gaussian log random walks; Step-17 circular shift of leg B (min 720)",
        "null_version": NULL_VERSION, "statistic": "canonical_net_mean_return_per_trade (D-3 endpoint, zone V)",
        "n_perm": n_perm, "reject_at": REJECT_AT, "decision_ref": OPEN36_DECISION_REF,
        "methodology_version": METHODOLOGY_VERSION, "code_commit": code.commit, "code_dirty": code.dirty,
        "code_dirty_digest": code.dirty_digest, "seed_rule": "inference.stable_seed(CALIBRATION_VERSION, scenario, 'pair'|'perm', rep)",
        "input_version_rep0": input_frames_fingerprint({"X_USDT": a0.to_frame("close"), "Y_USDT": b0.to_frame("close")},
                                                        columns=["close"]),
        "checkpoint": str(ckpt), "checkpoint_sha256": file_sha256(ckpt),
        "finished_at": datetime.now(timezone.utc).isoformat(), "runtime_s_this_session": round(time.time() - started, 1),
        "replicate_seconds_total": round(sum(r["seconds"] for r in records), 1), "workers": workers,
        "power": "NOT RUN — the D-4 power criterion (OU parameters, required power) is not specified by the owner",
        "gate_artifact_written": False, **result,
    }
    path = out_dir / f"step17_size_{scenario}_{key}_{summary['status']}.json"
    with open(path, "x", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, default=str)
    write_research_output_provenance(path, kind="STEP17_SIZE_CALIBRATION", producer="bot.research.step17_calibration",
                                     inputs={"version": summary["input_version_rep0"].get("version") if isinstance(summary["input_version_rep0"], dict) else None,
                                             "scenario": scenario}, config={"n_perm": n_perm, "target": target, "pilot": pilot}, code=code)
    summary["output_path"] = str(path)
    return summary
