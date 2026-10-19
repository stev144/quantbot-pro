# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/quant_core/hypothesis_registry.py
#
# claude code changed: new file — 2026-10-03, owner decision D-3 (DECISION_SHEET_D1-D11_2026-10-03.md, signed).
#
#   FAMILY        one per research program per calendar quarter (e.g. "cross_sectional" / "2026Q4")
#   PRIMARY       at most 5 PRE-REGISTERED hypotheses per family, each with a written economic reason
#   ENDPOINT      NET_MEAN_RETURN_PER_TRADE on zone V (IC for pure feature research)
#   CONFIRMATORY  Holm, FWER α = 0.05, B = 999 permutations per test, data from zone V ONLY
#   EXPLORATION   BH q = 0.10, data from zone S ONLY, permanently labelled EXPLORATORY — NEVER claimed
#   SCANS         two-stage: analytic screen in S → at most 5 survivors registered → confirmed in V
#   DUPLICATES    atr ≡ atr_indicator counts ONCE (F-21): registering both in one family is refused
#
# Registration must precede testing: a family becomes CLOSED when its confirmatory result is recorded and
# accepts no further hypotheses. The registry file is append-only (events, never edits).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from bot.quant_core.inference import CONFIRMATORY_B
from bot.quant_core.research_zones import SELECTION, VALIDATION, ZONES, ResearchZones

REGISTRY_VERSION = "QBP-HYP-D3-2"   # claude code changed: 2026-10-07 decision journal Phase 4 (OD-J11 endpoint + OD-J16 journal V-read events; was QBP-HYP-D3-1)
MAX_PRIMARY = 5
FWER_ALPHA = 0.05
EXPLORATION_Q = 0.10
ENDPOINTS = ("NET_MEAN_RETURN_PER_TRADE", "IC", "JOURNAL_RULE_NET_R")   # claude code changed: 2026-10-07 decision journal Phase 4 (OD-J11: mean counterfactual net R per journal episode)
EXPLORATORY_LABEL = "EXPLORATORY — NOT CLAIMABLE (zone S, BH q = 0.10)"
FEATURE_ALIASES = {"atr_indicator": "atr"}            # F-21: identical columns count once
DEFAULT_REGISTRY = Path(__file__).resolve().parents[1] / "research" / "governance" / "hypothesis_registry.json"


class RegistryError(RuntimeError):
    """A D-3 rule was violated (too many hypotheses, late registration, wrong zone, duplicate, ...)."""


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    program: str
    quarter: str                     # "YYYYQn"
    statement: str
    economic_rationale: str
    endpoint: str                    # one of ENDPOINTS
    feature_or_strategy: str
    horizon_candles: int

    def __post_init__(self):
        if not re.fullmatch(r"\d{4}Q[1-4]", self.quarter):
            raise RegistryError(f"quarter must look like 2026Q4, got {self.quarter!r}")
        if self.endpoint not in ENDPOINTS:
            raise RegistryError(f"endpoint must be one of {ENDPOINTS}")
        if len(self.economic_rationale.strip()) < 20:
            raise RegistryError("a written economic rationale (at least one real sentence) is required (D-3)")
        if not (isinstance(self.horizon_candles, int) and self.horizon_candles >= 1):
            raise RegistryError("horizon_candles must be an integer >= 1")

    @property
    def canonical_feature(self) -> str:
        return FEATURE_ALIASES.get(self.feature_or_strategy, self.feature_or_strategy)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HypothesisRegistry:
    def __init__(self, path: Path = DEFAULT_REGISTRY):
        self.path = Path(path)

    # ── storage (append-only events) ─────────────────────────────────────────
    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {"registry_version": REGISTRY_VERSION, "events": []}

    def _append(self, event: dict) -> dict:
        data = self._load()
        event = {"at": _now(), **event}
        data["events"].append(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return event

    def family(self, program: str, quarter: str) -> List[Hypothesis]:
        return [Hypothesis(**e["hypothesis"]) for e in self._load()["events"]
                if e["type"] == "REGISTERED" and e["hypothesis"]["program"] == program and e["hypothesis"]["quarter"] == quarter]

    def is_closed(self, program: str, quarter: str) -> bool:
        return any(e["type"] == "CONFIRMED" and e["program"] == program and e["quarter"] == quarter
                   for e in self._load()["events"])

    # ── registration ─────────────────────────────────────────────────────────
    def register(self, h: Hypothesis) -> dict:
        if self.is_closed(h.program, h.quarter):
            raise RegistryError(f"family {h.program}/{h.quarter} is CLOSED (already tested) — registration must precede testing")
        fam = self.family(h.program, h.quarter)
        if any(x.hypothesis_id == h.hypothesis_id for x in self._all()):
            raise RegistryError(f"hypothesis id {h.hypothesis_id!r} already registered")
        if len(fam) >= MAX_PRIMARY:
            raise RegistryError(f"family {h.program}/{h.quarter} already has {MAX_PRIMARY} primary hypotheses (D-3 cap)")
        if any(x.canonical_feature == h.canonical_feature and x.horizon_candles == h.horizon_candles and x.endpoint == h.endpoint
               for x in fam):
            raise RegistryError(f"{h.feature_or_strategy!r} duplicates an already-registered hypothesis "
                                f"(aliases count once: {FEATURE_ALIASES})")
        return self._append({"type": "REGISTERED", "hypothesis": asdict(h)})

    def _all(self) -> List[Hypothesis]:
        return [Hypothesis(**e["hypothesis"]) for e in self._load()["events"] if e["type"] == "REGISTERED"]

    # ── journal zone-V read log (OD-J16) ────────────────────────────────────────   # claude code changed: 2026-10-07 decision journal Phase 4
    def log_journal_validation_read(self, program: str, quarter: str, *, run_id: str, purpose: str, n_rows: int) -> dict:
        """Record (append-only) that decision-journal labels from zone V are being read FOR a registered, still-open
        family. Refuses when the family has no registered hypotheses or is already CLOSED."""
        if not self.family(program, quarter):
            raise RegistryError(f"no registered hypotheses for {program}/{quarter}: zone V journal labels stay closed")
        if self.is_closed(program, quarter):
            raise RegistryError(f"family {program}/{quarter} is CLOSED: its zone V read already happened")
        return self._append({"type": "JOURNAL_V_READ", "program": program, "quarter": quarter, "run_id": run_id,
                             "purpose": purpose, "n_rows": int(n_rows)})

    # ── confirmatory test (Holm, zone V only) ────────────────────────────────
    def confirm(self, program: str, quarter: str, results: Dict[str, dict], *, run_id: str,
                zones: ResearchZones = ZONES) -> dict:
        """results: {hypothesis_id: {"p_value": float, "n_perm": int, "timestamps": index-like}} for EVERY
        registered hypothesis of the family (none may be dropped). Applies Holm at FWER 0.05 and records the
        result; the family is CLOSED afterwards."""
        fam = self.family(program, quarter)
        if not fam:
            raise RegistryError(f"no registered hypotheses for {program}/{quarter} — register before testing")
        if self.is_closed(program, quarter):
            raise RegistryError(f"family {program}/{quarter} was already confirmed — a family is tested once")
        ids = [h.hypothesis_id for h in fam]
        if set(results) != set(ids):
            raise RegistryError(f"results must cover exactly the registered family {sorted(ids)}; got {sorted(results)}")
        m = len(ids)
        for hid in ids:
            r = results[hid]
            if r.get("n_perm") != CONFIRMATORY_B:
                raise RegistryError(f"{hid}: confirmatory tests use B = {CONFIRMATORY_B} (D-3), got {r.get('n_perm')}")
            zones_seen = set(zones.classify_index(r["timestamps"]))
            if zones_seen != {VALIDATION}:
                raise RegistryError(f"{hid}: confirmatory data must come from zone V only; found {sorted(zones_seen)}")
        if 1.0 / (CONFIRMATORY_B + 1) > FWER_ALPHA / m:
            raise RegistryError("permutation resolution cannot reach the Holm threshold")   # unreachable for m ≤ 5
        adj = holm_adjust([float(results[h]["p_value"]) for h in ids])
        decision = {hid: {"p_value": float(results[hid]["p_value"]), "p_holm": float(a), "reject_h0": bool(a <= FWER_ALPHA)}
                    for hid, a in zip(ids, adj)}
        return self._append({"type": "CONFIRMED", "program": program, "quarter": quarter, "run_id": run_id,
                             "method": f"Holm FWER {FWER_ALPHA}, B = {CONFIRMATORY_B}, zone V", "m": m, "decision": decision})


def holm_adjust(pvalues: Sequence[float]) -> np.ndarray:
    """Holm step-down adjusted p-values (monotone, capped at 1)."""
    p = np.asarray(pvalues, float)
    if p.ndim != 1 or len(p) == 0 or np.any(~np.isfinite(p)) or np.any((p < 0) | (p > 1)):
        raise RegistryError("Holm needs a non-empty list of p-values in [0, 1]")
    m = len(p)
    order = np.argsort(p)
    adj_sorted = np.maximum.accumulate((m - np.arange(m)) * p[order])
    out = np.empty(m)
    out[order] = np.minimum(adj_sorted, 1.0)
    return out


def exploratory_screen(pvalues: Dict[str, float], timestamps, *, zones: ResearchZones = ZONES) -> dict:
    """Stage 1 of a scan: BH at q = 0.10 on data from zone S ONLY. The output is permanently labelled
    exploratory — it can nominate at most MAX_PRIMARY candidates for registration, never support a claim."""
    zones_seen = set(zones.classify_index(timestamps))
    if zones_seen != {SELECTION}:
        raise RegistryError(f"exploratory screening uses zone S only; found {sorted(zones_seen)}")
    from statsmodels.stats.multitest import multipletests
    keys = list(pvalues)
    reject, padj, _, _ = multipletests([pvalues[k] for k in keys], alpha=EXPLORATION_Q, method="fdr_bh")
    survivors = sorted([k for k, r in zip(keys, reject) if r], key=lambda k: pvalues[k])
    return {"label": EXPLORATORY_LABEL, "q": EXPLORATION_Q, "m": len(keys),
            "p_bh": {k: float(a) for k, a in zip(keys, padj)}, "survivors": survivors,
            "nominated": survivors[:MAX_PRIMARY], "claimable": False}
