# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/evidence_registry.py
#
# claude code changed: new file — Post-CRRP Task 3 (evidence classification),
# 2026-10-02. The single source of truth for "what is this result worth as
# evidence?" — read by every Research page that shows historical results, so
# an invalidated or legacy result can never silently look like current
# evidence. Data: bot/research/governance/evidence_registry_CRRP-2026-10-02.json
# (version-controlled; classifications and reasons from
# PRE_PHASE3_RESEARCH_GOVERNANCE_REPORT_2026-10-02.md).
#
# Rules enforced here, not just documented:
#   * an artifact / log section / experiment that is NOT registered is
#     UNVERIFIED — never "current" by default;
#   * a registry entry may only be CANONICAL as a *result* when every
#     provenance field is present (run id, methodology version, protocol
#     hash, data fingerprint, code commit, sample period, universe,
#     parameters, costs) — governance records (kind="governance_record")
#     are exempt because they are not research results;
#   * provenance is never invented: absent fields read "MISSING".
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import fnmatch
import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

CANONICAL = "CANONICAL"
LEGACY_DESCRIPTIVE = "LEGACY_DESCRIPTIVE"
SUPERSEDED = "SUPERSEDED"
INVALIDATED = "INVALIDATED"
UNVERIFIED = "UNVERIFIED"
CLASSES = (CANONICAL, LEGACY_DESCRIPTIVE, SUPERSEDED, INVALIDATED, UNVERIFIED)

LABELS = {
    CANONICAL: "CANONICAL",
    LEGACY_DESCRIPTIVE: "LEGACY / DESCRIPTIVE",
    SUPERSEDED: "SUPERSEDED",
    INVALIDATED: "INVALIDATED",
    UNVERIFIED: "UNVERIFIED",
}
BADGE_CSS = {   # existing static/css/terminal.css badge classes
    CANONICAL: "pos", LEGACY_DESCRIPTIVE: "warn", SUPERSEDED: "neu", INVALIDATED: "neg", UNVERIFIED: "dim",
}

RESET_POINT = "CRRP-2026-10-02"
RESET_POINT_DATE = "2026-10-02"
REGISTRY_PATH = Path(__file__).resolve().parent / "governance" / f"evidence_registry_{RESET_POINT}.json"
GOVERNANCE_REPORT = "PRE_PHASE3_RESEARCH_GOVERNANCE_REPORT_2026-10-02.md"


class RegistryError(ValueError):
    pass


def _status(cls: str, reason: str, ref: str = "", source: str = "", provenance: Optional[Dict] = None) -> Dict:
    return {
        "class": cls,
        "label": LABELS[cls],
        "css": BADGE_CSS[cls],
        "reason": reason,
        "ref": ref,
        "source": source,
        "is_current_evidence": cls == CANONICAL,
        "provenance": provenance or {},
        "reset_point": RESET_POINT,
    }


def validate_registry(data: Dict) -> Dict:
    """Raise RegistryError on an invalid class or a CANONICAL *result* without
    complete provenance. Returns the data unchanged."""
    required = data["rules"]["canonical_result_requires"]
    missing_value = data["rules"]["missing_provenance_value"]
    entries = list(data.get("artifacts", [])) + list(data.get("governance_log_sections", []))
    lab = data.get("research_lab_experiments", {})
    entries += [lab.get("pre_reset_default", {})] + list(lab.get("pre_reset_overrides", []))
    for e in entries:
        cls = e.get("class")
        if cls not in CLASSES:
            raise RegistryError(f"invalid evidence class {cls!r} in {e}")
        if not e.get("reason"):
            raise RegistryError(f"entry without a reason: {e}")
        if cls == CANONICAL and e.get("kind") != "governance_record":
            prov = e.get("provenance") or {}
            gaps = [f for f in required if prov.get(f) in (None, "", missing_value)]
            if gaps:
                raise RegistryError(f"CANONICAL result {e.get('pattern') or e.get('title_prefix')} lacks provenance {gaps}")
    return data


@lru_cache(maxsize=1)
def load_registry() -> Dict:
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        return validate_registry(json.load(f))


def _provenance(entry: Dict) -> Dict:
    data = load_registry()
    mv = data["rules"]["missing_provenance_value"]
    given = entry.get("provenance") or {}
    return {f: given.get(f, mv) for f in data["rules"]["canonical_result_requires"]}


def classify_artifact(relative_path: str) -> Dict:
    """Evidence status of a research_data/ artifact (path relative to
    research_data/, '/'-separated). First matching pattern wins; unregistered
    → UNVERIFIED."""
    rel = relative_path.replace("\\", "/").lstrip("./")
    for e in load_registry()["artifacts"]:
        if fnmatch.fnmatch(rel, e["pattern"]):
            return _status(e["class"], e["reason"], e.get("ref", ""), f"artifact:{e['pattern']}", _provenance(e))
    return _status(UNVERIFIED, f"'{rel}' is not in the evidence registry — no recorded provenance, so it cannot count as evidence.",
                   source="default")


def classify_log_section(title: str) -> Dict:
    """Evidence status of a model_governance_log.md '## ' section by title."""
    t = title.strip()
    for e in load_registry()["governance_log_sections"]:
        if t.startswith(e["title_prefix"]):
            return _status(e["class"], e["reason"], e.get("ref", ""), f"log:{e['title_prefix']}")
    return _status(UNVERIFIED, "Governance-log section not in the evidence registry — unclassified, so not current evidence.",
                   source="default")


def classify_research_experiment(created_at, capability_id: str = "", verdict: str = "") -> Dict:
    """Evidence status of a research-lab ResearchExperiment row. Every
    experiment before the reset point is pre-reset (no methodology version,
    protocol hash or code commit recorded)."""
    lab = load_registry()["research_lab_experiments"]
    created = str(created_at)[:10] if created_at is not None else ""
    if created and created >= RESET_POINT_DATE:
        return _status(UNVERIFIED, "Post-reset experiment without registered provenance — register it before citing.",
                       source="default")
    for o in lab.get("pre_reset_overrides", []):
        if capability_id == o["capability_id"] and str(verdict).upper() in o["verdicts"]:
            return _status(o["class"], o["reason"], source=f"lab:{capability_id}")
    d = lab["pre_reset_default"]
    return _status(d["class"], d["reason"], source="lab:pre_reset_default")
