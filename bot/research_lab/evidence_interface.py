# ============================================================
# bot/research_lab/evidence_interface.py
# Research Lab — the Research Evidence Interface (Phase 2D/Research Lab
# Completion, Step 5).
#
# claude code changed: new file. NOT a new storage layer, NOT a new
# schema — every function here is a read-only query over the EXISTING
# ResearchExperiment table (bot/research_lab/models.py), which already
# carries everything a research question needs (spec, verdict, warnings,
# statistical/validation/robustness results). This module exists because,
# without it, an AI assistant (or a human) answering "which features have
# survived validation" had no path except hand-writing a fresh ORM query
# every time.
#
# THE BOUNDARY THIS FILE ENFORCES (Phase 2D's central principle):
#   Research engines -> validated evidence (verdict.py, ResearchExperiment)
#   -> THIS FILE (read-only query layer) -> an AI research assistant ->
#   human review -> ... -> execution.
#   Every function below returns data ALREADY computed and stored by the
#   deterministic verdict engine — nothing here computes a NEW verdict,
#   runs a NEW statistical test, or lets a caller (AI or otherwise)
#   convert raw numbers into a conclusion. An AI consuming this module can
#   summarize, compare, and surface evidence; it cannot manufacture it.
#
# WHAT THIS FILE DELIBERATELY DOES NOT ANSWER (honest gaps, not silently
# guessed at — see the Phase 2D engineering report's Known Limitations):
#   - "Which features are redundant" / "which show incremental
#     information" — these need a correlation/regression ANALYSIS across
#     experiments' raw feature data, not a query over already-stored
#     verdicts. Phase 2C/2D's own incremental-information testing was run
#     as ad-hoc research scripts, never through the Research Lab
#     experiment pipeline at all — so there is no ResearchExperiment row
#     to query for that specific finding today. Flagged as a real gap,
#     not answered here by guessing.
#   - "What evidence supports/contradicts a strategy" — too open-ended
#     for a bounded query function; this is exactly the kind of judgment
#     call Step 6 reserves for a future AI summarization capability
#     working ON TOP of the functions below, not inside this file.
# ============================================================

from __future__ import annotations

from typing import Dict, List, Optional

from bot.research_lab.capability_registry import RESEARCH_CAPABILITIES
from bot.research_lab.models import ResearchExperiment

SURVIVED_VERDICTS = ("SUPPORTED", "PARTIALLY_SUPPORTED")
FAILED_VERDICTS = ("REJECTED", "INCONCLUSIVE")
INVALID_VERDICTS = ("INVALID_RESEARCH", "INSUFFICIENT_DATA")


def _feature_name(experiment: ResearchExperiment) -> Optional[str]:
    """claude code changed: new — a 'feature' hypothesis names its subject
    in structured_spec['features'] (a list, but this platform's Policy
    Gate caps feature-type hypotheses at one named feature per experiment
    — see policy_gate.py's MAX_FEATURES_PER_EXPERIMENT); a 'conditional'
    hypothesis names it inside structured_spec['conditions'][0]['feature']
    instead. Returns None for a 'pairs' hypothesis (no single feature) or
    a spec with neither shape populated — never guesses."""
    spec = experiment.structured_spec or {}
    features = spec.get("features") or []
    if features:
        return features[0]
    conditions = spec.get("conditions") or []
    if conditions and conditions[0].get("feature"):
        return conditions[0]["feature"]
    return None


def list_experiments_by_verdict(verdicts, user=None) -> List[ResearchExperiment]:
    """
    claude code changed: new. Returns COMPLETED experiments whose verdict
    is in `verdicts` (a string or iterable of strings), most recent
    first. `user=None` (the default) intentionally returns evidence
    across ALL researchers — a finding like "RSI failed validation" is an
    objective research fact, not private data, and the whole point of a
    shared evidence layer is pooling institutional knowledge rather than
    silo-ing it per user. Pass a specific user to scope to their own
    experiments only.
    """
    if isinstance(verdicts, str):
        verdicts = [verdicts]
    qs = ResearchExperiment.objects.filter(status="COMPLETED", verdict__in=list(verdicts))
    if user is not None:
        qs = qs.filter(student=user)
    return list(qs.order_by("-created_at"))


def list_supported_features(user=None) -> List[Dict]:
    """claude code changed: new — 'which features have survived
    validation' (Step 5's first question). Returns one dict per
    SUPPORTED/PARTIALLY_SUPPORTED experiment: feature name, asset,
    verdict, and experiment id (for traceback to the full evidence via
    experiment_evidence_summary() below)."""
    return [
        {
            "experiment_id": str(exp.id), "feature": _feature_name(exp),
            "asset": (exp.structured_spec or {}).get("asset"), "verdict": exp.verdict,
        }
        for exp in list_experiments_by_verdict(SURVIVED_VERDICTS, user=user)
        if _feature_name(exp) is not None
    ]


def list_rejected_features(user=None) -> List[Dict]:
    """claude code changed: new — the mirror of list_supported_features()
    for 'which hypotheses were rejected' / 'which features failed OOS'.
    REJECTED/INCONCLUSIVE only — INVALID_RESEARCH/INSUFFICIENT_DATA are
    NOT included here (those mean "never validly tested," a genuinely
    different fact from "tested and failed" — see
    list_untestable_experiments() for those)."""
    return [
        {
            "experiment_id": str(exp.id), "feature": _feature_name(exp),
            "asset": (exp.structured_spec or {}).get("asset"), "verdict": exp.verdict,
        }
        for exp in list_experiments_by_verdict(FAILED_VERDICTS, user=user)
        if _feature_name(exp) is not None
    ]


def list_untestable_experiments(user=None) -> List[Dict]:
    """claude code changed: new — 'which experiments have insufficient
    sample size' (Step 5). Distinct from list_rejected_features(): these
    experiments were never validly tested at all (verdict.py's own
    MIN_SAMPLE_SIZE/MIN_CONDITION_SAMPLE floors), not tested-and-failed."""
    return [
        {"experiment_id": str(exp.id), "feature": _feature_name(exp), "verdict": exp.verdict, "hypothesis_text": exp.hypothesis_text}
        for exp in list_experiments_by_verdict(INVALID_VERDICTS, user=user)
    ]


def cross_symbol_consistency(feature_name: str) -> Dict:
    """
    claude code changed: new — 'which results replicate across assets'
    (Step 5). Groups every COMPLETED experiment testing `feature_name`
    (exact match against the same feature-name resolution
    list_supported_features()/list_rejected_features() use) by asset, and
    reports each asset's verdict. This does NOT compute a new verdict —
    it surfaces what verdict.py already decided per experiment, letting a
    caller (human or AI) see at a glance whether a feature's evidence
    agrees or disagrees across symbols. A feature verdicted SUPPORTED on
    one asset and REJECTED on another is exactly the "must NOT be
    described as universally useful" case Phase 2D's own brief named
    explicitly.
    """
    matches = [
        exp for exp in ResearchExperiment.objects.filter(status="COMPLETED").order_by("-created_at")
        if _feature_name(exp) == feature_name
    ]
    by_asset: Dict[str, List[str]] = {}
    for exp in matches:
        asset = (exp.structured_spec or {}).get("asset") or "UNKNOWN"
        by_asset.setdefault(asset, []).append(exp.verdict)

    verdict_sets = {v for verdicts in by_asset.values() for v in verdicts}
    replicates = len(by_asset) >= 2 and all(
        set(verdicts) & set(SURVIVED_VERDICTS) for verdicts in by_asset.values()
    )
    return {
        "feature": feature_name,
        "n_assets_tested": len(by_asset),
        "verdicts_by_asset": by_asset,
        "replicates_across_assets": replicates if by_asset else None,   # claude code changed: None (not False) when nothing was ever tested — "unknown," not "failed"
    }


def list_untested_capabilities() -> List[Dict]:
    """
    claude code changed: new — 'which data sources have not yet been
    tested' (Step 5). Cross-references capability_registry.py's static
    capability list against ResearchExperiment.capability_id — a
    capability with real, tested engine code (engine_status !=
    NOT_IMPLEMENTED) that has never actually been run as an experiment is
    a real, honest gap worth surfacing, distinct from one that's simply
    NOT_IMPLEMENTED yet.
    """
    tested_ids = set(
        ResearchExperiment.objects.exclude(capability_id="").values_list("capability_id", flat=True).distinct()
    )
    return [
        {"capability_id": cap.id, "name": cap.name, "engine_status": cap.engine_status}
        for cap in RESEARCH_CAPABILITIES.values()
        if cap.id not in tested_ids
    ]


def experiment_evidence_summary(experiment_id) -> Optional[Dict]:
    """
    claude code changed: new — the full evidence bundle for ONE
    experiment, in one call, so an AI assistant reads a single structured
    object instead of traversing the ORM row itself. Returns None for an
    unknown id (fails closed, never fabricates a summary for a
    nonexistent experiment).
    """
    try:
        exp = ResearchExperiment.objects.get(id=experiment_id)
    except ResearchExperiment.DoesNotExist:
        return None

    return {
        "experiment_id": str(exp.id),
        "hypothesis_text": exp.hypothesis_text,
        "structured_spec": exp.structured_spec,
        "status": exp.status,
        "verdict": exp.verdict,
        "statistical_results": exp.statistical_results,
        "validation_results": exp.validation_results,
        "robustness_results": exp.robustness_results,
        "warnings": exp.warnings,               # claude code changed: statistical caveats live here — surfaced as-is, not summarized/reworded
        "ai_interpretation": exp.ai_interpretation,   # claude code changed: kept clearly labeled/separate from the fields above — this is explanation, never evidence
        "code_version": exp.code_version,
        "random_seed": exp.random_seed,
        "created_at": exp.created_at.isoformat() if exp.created_at else None,
    }
