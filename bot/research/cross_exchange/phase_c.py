# claude code changed: new file — 2026-10-25, cross-exchange research PHASE C: preregistered confirmatory test.
#
# Phase B (descriptive) found 0/24 directions ever net-positive at base-tier fees. Phase C turns the one question that
# still matters into a single, pre-registered, dependence-aware test on FRESH data:
#
#   H1 (XARB-C1): at the registered taker fees, the share of time during which SOME direction on SOME coin shows a
#   net-positive gap for a 1,000-USDT trade that LASTS at least L_min consecutive samples exceeds θ.
#
#   unit        one clock hour (UTC) with ≥ 90 % eligible instants; Y_h = qualifying instants / eligible instants
#   statistic   mean of Y over the FIRST 336 eligible hours recorded after registration (no optional stopping)
#   test        batch means over 7 non-overlapping 48-h batches, one-sided Student t (6 df), α = 0.025; PASS only if
#               the lower bound > θ. Calibrated 2026-10-25 on AR(1) nulls at the boundary: false-pass 2.7 / 2.8 / 3.2 /
#               4.1 / 5.9 % for ρ = 0 / .5 / .8 / .9 / .95; power ≈ 1.00 at 2θ, 0.75 at 1.5θ (ρ = .8). If the observed
#               hourly lag-1 autocorrelation exceeds ρ_max = 0.9 the verdict is INCONCLUSIVE (test not valid there).
#   once        a signed registration is evaluated exactly once; the result file is never overwritten
#
# Guards (fail closed): the evaluator refuses an unsigned or altered registration, any session whose first sample is
# earlier than the registration time (so nothing Phase B looked at can be reused), a recorder config different from the
# registered one, too few eligible hours, and a second evaluation. Research-only: no orders, no signals, no execution;
# a PASS would only justify further research (execution realism), never trading.
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from bot.research.cross_exchange.gap_study import iter_instants, pair_measure, row_exclusion
from bot.research.cross_exchange.recorder import _effective_status

PREREG_ID = "XARB-C1"
DRAFT, SIGNED = "DRAFT", "SIGNED"


@dataclass(frozen=True)
class Registration:
    prereg_id: str = PREREG_ID
    hypothesis: str = ("At the registered taker fees, the mean hourly share of eligible instants that belong to a "
                       "net-positive episode (any coin, any direction, 1,000-USDT trade, walking recorded depth) of at "
                       "least l_min consecutive samples exceeds theta.")
    size_quote: int = 1000
    l_min: int = 2                       # ≥ 2 consecutive 1-s samples: must outlive feed delay + order latency
    theta: float = 0.001                 # 0.1 % of time ≈ 3.6 s per hour — economic floor, fixed before data
    n_hours: int = 336                   # first 14 × 24 eligible hours after registration, then stop
    min_eligible_share: float = 0.90     # an hour counts only if ≥ 90 % of its instants have an eligible direction
    alpha: float = 0.025                 # nominal; calibrated false-pass ≤ 4.1 % for hourly AR(1) ρ ≤ 0.9
    batch_hours: int = 48                # 7 two-day batch means → one-sided t on 6 degrees of freedom
    rho_max: float = 0.9                 # observed hourly lag-1 autocorrelation above this → INCONCLUSIVE
    max_feed_delay_ms: float = 1000.0
    fees: Dict[str, float] = field(default_factory=dict)          # venue → taker fraction (owner tier when known)
    fee_basis: Dict[str, str] = field(default_factory=dict)
    recorder: Dict[str, object] = field(default_factory=lambda: {"recorder_version": "SQT-XARB-REC-1",
                                                                 "schema_version": "xarb-obs-v1",
                                                                 "sample_interval_s": 1.0, "depth": 10,
                                                                 "venues": ["binance", "kraken", "coinbase"]})
    status: str = DRAFT
    registered_utc: Optional[str] = None      # set at signing; data must start after it
    signed_by: Optional[str] = None
    notes: Tuple[str, ...] = (
        "Phase B (descriptive, same recorder, 2026-10-24/25 session) showed no net-positive instant at base-tier "
        "fees; the expected outcome at those fees is FAIL. The test is informative only if the registered fees are "
        "the owner's real (lower) tiers.",
        "Data recorded before registered_utc — including everything Phase B used — is ineligible.",
        "A PASS would justify only an execution-realism study (latency, inventory, rebalancing); it grants no "
        "permission to trade.",
    )

    def body(self) -> dict:
        d = asdict(self)
        d["notes"] = list(self.notes)
        return d

    def digest(self) -> str:
        """Hash of everything except the signature fields — any change after signing is detected."""
        b = {k: v for k, v in self.body().items() if k not in ("status", "registered_utc", "signed_by")}
        return hashlib.sha256(json.dumps(b, sort_keys=True).encode()).hexdigest()


# ── registration files ────────────────────────────────────────────────────────────────────────────────────────────
def prereg_dir(out_dir: str) -> Path:
    return Path(out_dir) / "preregistration"


def write_draft(out_dir: str, reg: Registration) -> Path:
    d = prereg_dir(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{reg.prereg_id}.json"
    with open(path, "x", encoding="utf-8") as fh:                    # a draft is never silently replaced
        json.dump({**reg.body(), "digest": reg.digest()}, fh, indent=1)
    return path


def load_registration(out_dir: str, prereg_id: str = PREREG_ID) -> Tuple[Registration, dict]:
    raw = json.loads((prereg_dir(out_dir) / f"{prereg_id}.json").read_text(encoding="utf-8"))
    fields = {k: raw[k] for k in Registration.__dataclass_fields__ if k in raw}
    fields["notes"] = tuple(fields.get("notes", ()))
    return Registration(**fields), raw


def owner_sign(out_dir: str, signed_by: str, prereg_id: str = PREREG_ID, now: Optional[datetime] = None) -> Path:
    """Owner act: freezes the draft (digest re-checked) and stamps registered_utc; only data recorded after it counts."""
    reg, raw = load_registration(out_dir, prereg_id)
    if raw.get("status") != DRAFT:
        raise ValueError(f"{prereg_id} is {raw.get('status')}, not a draft")
    if raw.get("digest") != reg.digest():
        raise ValueError(f"{prereg_id} draft was edited after it was written (digest mismatch) — write a new draft")
    if not reg.fees or set(reg.recorder["venues"]) - set(reg.fees):
        raise ValueError("every registered venue needs a fee before signing")
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    path = prereg_dir(out_dir) / f"{prereg_id}.json"
    signed = {**raw, "status": SIGNED, "registered_utc": stamp, "signed_by": signed_by}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(signed, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


# ── outcome per hour ─────────────────────────────────────────────────────────────────────────────────────────────
def hourly_outcomes(out_dir: str, session_id: str, reg: Registration) -> Tuple[List[dict], str]:
    """[{hour, eligible, qualifying, y}] in time order for one session, plus the SHA-256 of the bytes read."""
    root = Path(out_dir)
    meta = json.loads((root / "metadata" / f"{session_id}.json").read_text(encoding="utf-8"))
    books_by_inst: Dict[str, List[str]] = {}
    for key, b in (meta.get("books") or {}).items():
        books_by_inst.setdefault(b.get("instrument"), []).append(key)
    digest = hashlib.sha256()
    hours: List[int] = []            # hour bucket per instant
    eligible = bytearray()           # 1 if any direction was measurable at this instant
    qualifying = bytearray()         # 1 if the instant lies in a net-positive run of ≥ l_min (any direction)
    runs: Dict[tuple, List[int]] = {}
    prev_mono, interval = None, float((meta.get("config") or {}).get("sample_interval_s") or 1.0)

    def close(key):
        r = runs.pop(key, None)
        if r and len(r) >= reg.l_min:
            for idx in r:
                qualifying[idx] = 1

    for mono, utc, rows in iter_instants(root / "raw" / session_id, digest):
        i = len(hours)
        hours.append(int(utc // (3600 * 10**9)))
        eligible.append(0)
        qualifying.append(0)
        if prev_mono is not None and (mono - prev_mono) / 1e9 > 2.5 * interval:
            for k in list(runs):
                close(k)                                                # a recorder pause ends every run
        prev_mono = mono
        for inst, keys in books_by_inst.items():
            ok = {k: rows[k] for k in keys if row_exclusion(rows.get(k), reg.max_feed_delay_ms) is None}
            for a in keys:
                for b in keys:
                    if a == b:
                        continue
                    key = (inst, a, b)
                    if a not in ok or b not in ok:
                        close(key)
                        continue
                    va, vb = a.split(":", 1)[0], b.split(":", 1)[0]
                    m = pair_measure(ok[a], ok[b], (reg.size_quote,), reg.fees[va], reg.fees[vb])["sizes"][reg.size_quote]
                    if m is None:                                       # depth cannot fill the size: not measurable
                        close(key)
                        continue
                    eligible[i] = 1
                    if m["net_bps"] > 0:
                        runs.setdefault(key, []).append(i)
                    else:
                        close(key)
    for k in list(runs):
        close(k)

    per_hour: Dict[int, List[int]] = {}
    for h, e, q in zip(hours, eligible, qualifying):
        c = per_hour.setdefault(h, [0, 0, 0])
        c[0] += 1
        c[1] += e
        c[2] += q
    out = []
    expected = int(round(3600 / interval))
    for h in sorted(per_hour):
        n, e, q = per_hour[h]
        ok = e >= reg.min_eligible_share * expected
        out.append({"hour_utc": datetime.fromtimestamp(h * 3600, timezone.utc).isoformat(), "instants": n,
                    "eligible": e, "qualifying": q, "y": (q / e) if e else None, "counts": ok})
    return out, digest.hexdigest()


# ── test ───────────────────────────────────────────────────────────────────────────────────────────────────────────
# claude code changed: 2026-10-25 — batch means, not a percentile block bootstrap: calibration showed the bootstrap
# passing 6.8–14 % of the time at the null boundary (target ≤ 5 %). Non-overlapping 24-h batches absorb time-of-day
# seasonality and any hour-to-hour dependence that decays within a day; their means are tested with a one-sided
# Student t on (batches − 1) degrees of freedom.
def batch_means_test(y: Sequence[float], batch: int, theta: float, alpha: float) -> dict:
    from scipy import stats
    a = np.asarray(y, dtype=np.float64)
    k = a.size // batch
    if k < 3:
        raise ValueError(f"need at least 3 complete batches of {batch} observations, have {k}")
    means = a[: k * batch].reshape(k, batch).mean(axis=1)
    se = means.std(ddof=1) / np.sqrt(k)
    t_crit = float(stats.t.ppf(1 - alpha, k - 1))
    lower = float(means.mean() - t_crit * se)          # one-sided (1 − alpha) lower confidence bound
    t_stat = float((means.mean() - theta) / se) if se > 0 else (float("inf") if means.mean() > theta else float("-inf"))
    return {"batches": int(k), "batch_means": [float(m) for m in means], "t_stat": t_stat, "t_crit": t_crit,
            "lower_bound": lower}


def lag1(y: Sequence[float]) -> float:
    a = np.asarray(y, dtype=np.float64)
    if a.size < 3 or float(a.std()) <= 1e-12 * max(1.0, float(np.abs(a).max())):
        return 0.0                         # constant (up to float rounding): no dependence is measurable
    a = a - a.mean()
    return float((a[1:] * a[:-1]).sum() / (a * a).sum())


def decide(y: Sequence[float], reg: Registration) -> dict:
    r = batch_means_test(y, reg.batch_hours, reg.theta, reg.alpha)
    rho = lag1(y)
    verdict = "PASS" if r["lower_bound"] > reg.theta else "FAIL"
    if verdict == "PASS" and rho > reg.rho_max:
        verdict = "INCONCLUSIVE"                       # dependence beyond the calibrated range: a pass is not trusted
    return {"n_hours": len(y), "mean_y": float(np.mean(y)), "theta": reg.theta, "lag1_autocorrelation": rho, **r,
            "verdict": verdict}


def false_pass_rate(reg: Registration, n_sims: int = 2000, rho: float = 0.5, sd: Optional[float] = None,
                    seed: int = 7) -> float:
    """Calibration: AR(1) hourly series whose TRUE mean equals theta (the null boundary). A valid test passes ≤ alpha."""
    rng = np.random.default_rng(seed)
    sd = reg.theta if sd is None else sd
    passes = 0
    for s in range(n_sims):
        e = rng.normal(0, sd * np.sqrt(1 - rho ** 2), reg.n_hours)
        x = np.empty(reg.n_hours)
        x[0] = rng.normal(0, sd)
        for t in range(1, reg.n_hours):
            x[t] = rho * x[t - 1] + e[t]
        y = reg.theta + x
        passes += decide(y, reg)["verdict"] == "PASS"
    return passes / n_sims


def collect(out_dir: str, reg: Registration, raw: dict):
    """Eligible post-registration sessions and their counted hours (read-only; used by evaluate and --status)."""
    t_reg = datetime.fromisoformat(raw["registered_utc"])
    root = Path(out_dir)
    sessions, refused = [], []
    for mp in sorted((root / "metadata").glob("*.json")):
        meta = _effective_status(json.loads(mp.read_text(encoding="utf-8")))
        start = meta.get("start_utc")
        cfg = meta.get("config") or {}
        why = None
        if not start or datetime.fromisoformat(start) < t_reg:
            why = "started before registration"
        elif (meta.get("recorder_version"), meta.get("schema_version"), float(cfg.get("sample_interval_s") or 0),
              cfg.get("depth"), sorted(cfg.get("venues") or [])) != (
                reg.recorder["recorder_version"], reg.recorder["schema_version"], float(reg.recorder["sample_interval_s"]),
                reg.recorder["depth"], sorted(reg.recorder["venues"])):
            why = "recorder configuration differs from the registered one"
        (refused if why else sessions).append({"session_id": meta.get("session_id"), "start_utc": start,
                                               **({"reason": why} if why else {})})
    hours, digests = [], {}
    for s in sessions:
        h, dg = hourly_outcomes(out_dir, s["session_id"], reg)
        hours += [x for x in h if x["counts"]]
        digests[s["session_id"]] = dg
    hours.sort(key=lambda x: x["hour_utc"])
    return sessions, refused, hours, digests


# ── evaluation (single shot) ──────────────────────────────────────────────────────────────────────────────────────
def evaluate(out_dir: str, prereg_id: str = PREREG_ID) -> dict:
    reg, raw = load_registration(out_dir, prereg_id)
    if raw.get("status") != SIGNED:
        raise PermissionError(f"{prereg_id} is not signed — nothing is evaluated before the owner signs")
    if raw.get("digest") != reg.digest():
        raise PermissionError(f"{prereg_id} changed after signing (digest mismatch) — evaluation refused")
    result_path = prereg_dir(out_dir) / f"{prereg_id}_RESULT.json"
    if result_path.exists():
        raise FileExistsError(f"{prereg_id} was already evaluated ({result_path.name}); a registration is used once")
    sessions, refused, hours, digests = collect(out_dir, reg, raw)
    if len(hours) < reg.n_hours:
        return {"prereg_id": prereg_id, "status": "INSUFFICIENT_DATA", "eligible_hours": len(hours),
                "required_hours": reg.n_hours, "sessions_used": sessions, "sessions_refused": refused,
                "note": "nothing is decided or written until the registered number of eligible hours exists"}
    used = hours[:reg.n_hours]                                          # the FIRST n_hours — no optional stopping
    result = {"prereg_id": prereg_id, "status": "EVALUATED", "evaluated_utc": datetime.now(timezone.utc).isoformat(),
              "registration_digest": reg.digest(), **decide([x["y"] for x in used], reg),
              "hours_used": [used[0]["hour_utc"], used[-1]["hour_utc"]], "sessions_used": sessions,
              "sessions_refused": refused, "input_sha256": digests,
              "meaning": ("PASS = evidence that capturable-looking net gaps exist often enough at these fees to "
                          "justify an execution-realism study; FAIL = no such evidence. Neither is permission to trade.")}
    with open(result_path, "x", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    return result
