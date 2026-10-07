# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/deep_health/core.py
# claude code changed: new file — 2026-10-07 Deep Health core (DEEP_HEALTH_PLAN_2026-10-07.md §2).
#
# Vocabulary: PASS / PARTIAL / FAIL / BLOCKED / UNKNOWN / N/A, per component and per gate (G1 software, G2 maths,
# G3 statistics, G4 trading discipline, G5 research integrity). Aggregation is WORST-OF, never averaged:
# FAIL > BLOCKED > UNKNOWN > PARTIAL > PASS, N/A ignored — one FAIL can never hide behind green.
# Rules enforced here, not left to each check:
#   * a check that raises is FAIL on G1 ("check crashed"), never skipped;
#   * a gate a component declares but no result covers is UNKNOWN (a missing check is not a pass);
#   * every level runs inside a no-network guard (socket connects refused) — Deep Health is read-only and offline.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from __future__ import annotations

import contextlib
import json
import math
import socket
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

PASS, PARTIAL, FAIL, BLOCKED, UNKNOWN, NA = "PASS", "PARTIAL", "FAIL", "BLOCKED", "UNKNOWN", "N/A"
STATUSES = (PASS, PARTIAL, FAIL, BLOCKED, UNKNOWN, NA)
RANK = {NA: -1, PASS: 0, PARTIAL: 1, UNKNOWN: 2, BLOCKED: 3, FAIL: 4}
GATES = ("G1", "G2", "G3", "G4", "G5")
GATE_TITLES = {"G1": "Software", "G2": "Maths", "G3": "Statistics", "G4": "Trading discipline", "G5": "Research integrity"}
INFRA, QUANT, RESEARCH = "INFRASTRUCTURE", "QUANT_ENGINE", "RESEARCH_INFRASTRUCTURE"
AREAS = (INFRA, QUANT, RESEARCH)
AREA_TITLES = {INFRA: "Infrastructure Health", QUANT: "Quant Engine Health", RESEARCH: "Research Infrastructure Health"}
LEVELS = ("L0", "L1", "L2")
LEVEL_TITLES = {"L0": "L0 Infrastructure", "L1": "L1 Functional deep health", "L2": "L2 Research integrity"}
HEALTH_VERSION = "QBP-DEEP-HEALTH-1"
REPORT_DIR = Path(__file__).resolve().parents[2] / "logs" / "deep_health"


def worst(statuses: Iterable[str]) -> str:
    """Worst-of; N/A is ignored; nothing applicable at all is N/A."""
    s = [x for x in statuses if x != NA]
    return max(s, key=RANK.__getitem__) if s else NA


@dataclass
class CheckResult:
    gate: str
    name: str
    status: str
    expected: str = ""
    observed: str = ""
    reason: str = ""

    def __post_init__(self):
        if self.gate not in GATES:
            raise ValueError(f"unknown gate {self.gate!r}")
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}")


@dataclass
class ComponentSpec:
    key: str
    title: str
    area: str
    level: str
    gates: Sequence[str]
    fn: Callable[[], List[CheckResult]]
    reuses: Sequence[str] = ()
    version: Optional[Callable[[], str]] = None


REGISTRY: Dict[str, ComponentSpec] = {}


def health_check(key: str, title: str, *, area: str, level: str, gates: Sequence[str], reuses: Sequence[str] = (),
                 version: Optional[Callable[[], str]] = None):
    """Register a component check. `gates` = the gates this component is judged on (others are N/A)."""
    if area not in AREAS or level not in LEVELS or not set(gates) <= set(GATES):
        raise ValueError(f"bad registration for {key}")

    def deco(fn):
        if key in REGISTRY:
            raise ValueError(f"duplicate health check {key}")
        REGISTRY[key] = ComponentSpec(key, title, area, level, tuple(gates), fn, tuple(reuses), version)
        return fn
    return deco


def _fmt(v) -> str:
    if isinstance(v, float):
        return repr(round(v, 12)) if math.isfinite(v) else repr(v)
    return str(v)


class Probe:
    """Collects CheckResults for one component. Comparisons are exact unless a tolerance is passed WITH its
    justification (floating-point identities only)."""

    def __init__(self):
        self.out: List[CheckResult] = []

    def add(self, gate, name, status, *, expected="", observed="", reason=""):
        self.out.append(CheckResult(gate, name, status, _fmt(expected), _fmt(observed), reason))

    def eq(self, gate, name, expected, observed, reason=""):
        ok = expected == observed
        self.add(gate, name, PASS if ok else FAIL, expected=expected, observed=observed,
                 reason="" if ok else (reason or "observed value differs from the known answer"))

    def near(self, gate, name, expected, observed, tol, why):
        try:
            ok = observed is not None and math.isfinite(float(observed)) and abs(float(observed) - float(expected)) <= tol
        except (TypeError, ValueError):
            ok = False
        self.add(gate, name, PASS if ok else FAIL, expected=f"{_fmt(expected)} ± {tol:g} ({why})", observed=observed,
                 reason="" if ok else "outside the justified tolerance")

    def true(self, gate, name, cond, *, expected="True", observed="", reason=""):
        self.add(gate, name, PASS if cond else FAIL, expected=expected, observed=observed if observed != "" else bool(cond),
                 reason="" if cond else (reason or "invariant violated"))

    def raises(self, gate, name, fn, exc=Exception, match: Optional[str] = None, reason=""):
        try:
            fn()
        except exc as e:
            ok = match is None or match in str(e)
            self.add(gate, name, PASS if ok else FAIL, expected=f"raises {getattr(exc, '__name__', exc)}"
                     + (f" containing {match!r}" if match else ""), observed=f"{type(e).__name__}: {str(e)[:160]}",
                     reason="" if ok else "raised for the wrong reason")
            return
        self.add(gate, name, FAIL, expected=f"raises {getattr(exc, '__name__', exc)}", observed="no exception",
                 reason=reason or "a refusal that must happen did not")


# ── offline guard ───────────────────────────────────────────────────────────────────────────────────────────
class HealthNetworkBlocked(RuntimeError):
    pass


@contextlib.contextmanager
def no_network():
    """Refuse every Python-level socket connection for the duration (the PostgreSQL driver is a C library and
    is unaffected; exchanges / data providers are). Deep Health never needs the internet."""
    orig_connect, orig_create = socket.socket.connect, socket.create_connection

    def refuse(*a, **k):
        raise HealthNetworkBlocked("Deep Health is offline: network access refused")
    socket.socket.connect = refuse
    socket.create_connection = refuse
    try:
        yield
    finally:
        socket.socket.connect, socket.create_connection = orig_connect, orig_create


# ── running ─────────────────────────────────────────────────────────────────────────────────────────────────
def run_component(spec: ComponentSpec) -> dict:
    t = time.perf_counter()
    try:
        results = list(spec.fn())
    except Exception as e:     # a crashing check is a G1 FAIL with its reason — never a skip
        results = [CheckResult("G1", "check executed", FAIL, "the check runs to completion",
                               f"{type(e).__name__}: {e}", "check crashed: " + traceback.format_exc(limit=3)[-600:])]
    gates = {}
    for g in GATES:
        if g not in spec.gates:
            gates[g] = NA
            continue
        rs = [r for r in results if r.gate == g]
        gates[g] = worst(r.status for r in rs) if rs else UNKNOWN
    for r in results:
        if r.gate not in spec.gates:     # a result on an undeclared gate is a registration bug — surface it
            gates[r.gate] = worst([gates[r.gate] if gates[r.gate] != NA else PASS, r.status])
    version = None
    if spec.version:
        try:
            version = spec.version()
        except Exception as e:
            version = f"unavailable ({type(e).__name__})"
    return {"key": spec.key, "title": spec.title, "area": spec.area, "level": spec.level, "gates": gates,
            "status": worst(gates.values()), "reuses": list(spec.reuses), "version": version,
            "results": [asdict(r) for r in results], "elapsed_ms": round((time.perf_counter() - t) * 1000, 1)}


def _ensure_registered():
    from bot.deep_health import l0_infrastructure, l1_quant, l1_research, l2_integrity  # noqa: F401  (registers)


def run_health(levels: Sequence[str] = ("L0", "L1"), *, only: Optional[Iterable[str]] = None) -> dict:
    """Run every registered component of the given levels. Read-only and offline."""
    _ensure_registered()
    from bot.quant_core.protocol import METHODOLOGY_VERSION
    from bot.quant_core.provenance import code_state
    from bot.quant_core.research_zones import ZONES_VERSION
    started = datetime.now(timezone.utc)
    t = time.perf_counter()
    only = set(only) if only else None
    with no_network():
        code = code_state()
        comps = [run_component(s) for s in REGISTRY.values()
                 if s.level in levels and (only is None or s.key in only)]
    counts = {s: sum(1 for c in comps if c["status"] == s) for s in STATUSES}
    areas = {a: worst(c["status"] for c in comps if c["area"] == a) for a in AREAS}
    return {"health_version": HEALTH_VERSION, "run_id": uuid.uuid4().hex[:16], "levels": list(levels),
            "started_at": started.isoformat(), "duration_ms": round((time.perf_counter() - t) * 1000, 1),
            "code_commit": code.commit, "code_dirty": code.dirty, "code_dirty_digest": code.dirty_digest,
            "methodology_version": METHODOLOGY_VERSION,
            "zones_version": ZONES_VERSION, "overall": worst(c["status"] for c in comps), "areas": areas,
            "counts": counts, "components": comps}


def comparable(report: dict) -> dict:
    """The report without run-specific fields (run id, timestamps, timings) — for determinism checks."""
    r = {k: v for k, v in report.items() if k not in ("run_id", "started_at", "duration_ms")}
    r["components"] = [{k: v for k, v in c.items() if k != "elapsed_ms"} for c in report["components"]]
    return r


def save_report(report: dict, report_dir: Optional[Path] = None) -> Path:
    """Write a report (never overwriting): <dir>/<levels>_<run_id>.json."""
    report_dir = Path(report_dir) if report_dir is not None else REPORT_DIR      # resolved at call time
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{'-'.join(report['levels'])}_{report['run_id']}.json"
    with open(path, "x", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, default=str)
    return path


def load_latest_report(level: str = "L2", report_dir: Optional[Path] = None) -> Optional[dict]:
    """The newest saved report that includes `level`, or None."""
    report_dir = Path(report_dir) if report_dir is not None else REPORT_DIR
    if not report_dir.exists():
        return None
    best = None
    for p in report_dir.glob("*.json"):
        try:
            rep = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if level in rep.get("levels", []) and (best is None or rep.get("started_at", "") > best.get("started_at", "")):
            best = rep
    return best
