# ============================================================
# bot/research_lab/trial_service.py
# claude code changed: new file — Hardening Phase 2, Priority 1. The
# shared boundary between ad-hoc research scripts (phase2d/e/f-style,
# living outside Django's request/orchestrator flow) and the governance
# primitives the previous hardening session built (HypothesisFamily,
# ResearchExperiment's append-only enforcement, data_fingerprint.py).
#
# Why this exists rather than each phase script calling the ORM directly:
# the one property this mission requires — "a script must NOT be able to
# silently run an FDR correction over an arbitrary DataFrame without
# first declaring which hypothesis family those tests belong to" — needs
# to be enforced at ONE call site, not re-implemented (and potentially
# gotten wrong) in every phase script. freeze_family_before_testing()
# below IS that one call site: it returns only a FROZEN family, never a
# draft one, so a caller physically cannot hold a mutable-scope family
# object past this call.
#
# Deliberately NOT a duplicate of ResearchSpec/orchestrator.py's
# student-submitted-hypothesis flow (bot/research_lab/orchestrator.py) —
# that flow is real, tested, and untouched by this module. This is the
# equivalent boundary for the OTHER real, pre-existing way research
# happens in this project: a researcher (human or, later, an AI Research
# Scientist under the governance rules Hardening Phase 2 §18 sets out)
# running a script directly against the engines. Both flows converge on
# the same ResearchExperiment table — one hypothesis family, one
# experiment ledger, regardless of which flow produced it.
# ============================================================

from __future__ import annotations

from typing import Dict, List, Optional

from django.contrib.auth import get_user_model

from bot.research_lab.data_fingerprint import DatasetIdentity
from bot.research_lab.models import HypothesisFamily, ResearchExperiment

RESEARCH_SCRIPT_USERNAME = "research_script"   # claude code changed: new — the documented actor identity for headless/ad-hoc research runs, distinct from a real student. See get_research_script_actor()'s docstring.


def get_research_script_actor():
    """
    claude code changed: new. ResearchExperiment.student is a required FK
    (the model's existing, correct design — every OTHER row in this table
    is a real person's submitted hypothesis, and that FK is load-bearing
    for entitlements/audit). Ad-hoc scripts (phase2d/e/f-style) have no
    such person. Rather than weaken the model's own constraint, this
    creates/reuses ONE real, clearly-named Django user
    ("research_script", inactive — cannot log in, cannot authenticate,
    exists purely as a queryable FK target) that every headless research
    run attributes its experiments to. `researcher/agent identity` (this
    mission's §16 reproducibility question: "who/what generated it?") is
    answered by this username plus whatever `code_version` was recorded —
    never ambiguous about "was this a student or a script."
    """
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=RESEARCH_SCRIPT_USERNAME,
        defaults={"is_active": False, "email": "", "first_name": "Research Script (automated)"},
    )
    return user


def freeze_family_before_testing(
    name: str, feature_family: List[str], assets: List[str],
    venue: str, timeframe: str, horizons: List[str],
    correction_method: str = "fdr_bh", alpha: float = 0.05,
    created_by=None,
) -> HypothesisFamily:
    """
    THE enforcement boundary (Hardening Phase 2 §3/§4): creates a
    HypothesisFamily and freezes it in the same call, before returning
    control to the caller. There is no code path in this module that
    returns an unfrozen family — a caller cannot accidentally run
    statistical tests against a family whose scope could still change.

    claude code changed: idempotent by (name, feature_family, assets,
    venue, timeframe, horizons) — re-running the SAME script twice with
    the SAME declared scope reuses the existing frozen family (a rerun of
    an unchanged hypothesis is not a new hypothesis); any actual
    difference in scope creates a genuinely new family, since
    HypothesisFamily.save() would reject reusing the old row's PK with
    different scope fields once frozen anyway.
    """
    existing = HypothesisFamily.objects.filter(
        name=name, feature_family=feature_family, assets=assets,
        venue=venue, timeframe=timeframe, horizons=horizons, frozen_at__isnull=False,
    ).first()
    if existing is not None:
        return existing

    family = HypothesisFamily.objects.create(
        name=name, feature_family=feature_family, assets=assets,
        venue=venue, timeframe=timeframe, horizons=horizons,
        correction_method=correction_method, alpha=alpha, created_by=created_by,
    )
    family.freeze()
    return family


def record_research_trial(
    hypothesis_family: HypothesisFamily, hypothesis_text: str,
    dataset_identities: List[DatasetIdentity], statistical_results: Dict,
    verdict: str, code_version: str = "", structured_spec: Optional[Dict] = None,
    student=None,
) -> ResearchExperiment:
    """
    Persists ONE ResearchExperiment row for a completed ad-hoc research
    trial, referencing the (already-frozen — see freeze_family_before_testing)
    family. `hypothesis_family` MUST already be frozen; this function
    refuses to record a trial against a draft family, since an unfrozen
    family means the FDR-correction scope this trial's results depend on
    could still be silently redefined later.

    `dataset_identities` — one DatasetIdentity per symbol/source acquired
    for this trial (Hardening Mission §5's "every experiment must record
    ... data fingerprint"). Stored inside structured_spec as a list, and
    a single combined fingerprint (sha256 of the sorted per-dataset
    fingerprints) is stored in data_fingerprint — so a change to ANY
    constituent dataset changes the experiment's own fingerprint too.
    """
    if hypothesis_family.frozen_at is None:
        raise ValueError(
            f"HypothesisFamily {hypothesis_family.pk} is not frozen — refusing to record a "
            f"trial against a family whose scope could still change. Call "
            f"freeze_family_before_testing() first."
        )

    # claude code changed: real bug found by this module's own tests —
    # always wrapping in an outer sha256 double-hashes the N=1 case,
    # making a single-symbol trial's data_fingerprint NOT equal to that
    # symbol's own dataset fingerprint (surprising, and pointlessly so —
    # nothing needs the extra hash layer when there's only one input).
    # N=1 uses the dataset's own fingerprint directly; N>1 combines via
    # sha256 of the sorted per-dataset fingerprints, so any change to ANY
    # constituent dataset still changes the combined value.
    if len(dataset_identities) == 1:
        combined_fingerprint = dataset_identities[0].fingerprint()
    else:
        import hashlib
        combined = "|".join(sorted(d.fingerprint() for d in dataset_identities))
        combined_fingerprint = hashlib.sha256(combined.encode("utf-8")).hexdigest()

    spec = dict(structured_spec or {})
    spec["dataset_identities"] = [
        {"source": d.source, "symbol": d.symbol, "venue": d.venue, "timeframe": d.timeframe,
         "start_date": d.start_date, "end_date": d.end_date, "row_count": d.row_count,
         "fingerprint": d.fingerprint()}
        for d in dataset_identities
    ]

    experiment = ResearchExperiment.objects.create(
        student=student or get_research_script_actor(),
        hypothesis_text=hypothesis_text,
        hypothesis_family=hypothesis_family,
        data_fingerprint=combined_fingerprint,
        structured_spec=spec,
        statistical_results=statistical_results,
        verdict=verdict,
        code_version=code_version,
        status="PLANNED",
    )
    experiment.status = "RUNNING"
    experiment.save()
    experiment.status = "COMPLETED"
    experiment.save()   # claude code changed: the ONE sanctioned transition into terminal state — every subsequent save()/delete() on this row is now enforced-immutable (bot/research_lab/models.py)
    return experiment


def record_oos_trial(
    hypothesis_family: HypothesisFamily,
    oos_result,   # bot.research.oos_validator.OOSResult — typed as untyped param to avoid a hard import cycle (oos_validator.py does not import trial_service.py)
    hypothesis_text: str,
    dataset_identities: List[DatasetIdentity],
    verdict: str,
    code_version: str = "",
    student=None,
) -> ResearchExperiment:
    """
    claude code changed: new — OOS/Walk-Forward Validation Infrastructure
    mission. THE integration point between bot/research/oos_validator.py
    and the existing governance ledger (Section 8/13 of that mission:
    "integrate with ResearchTrial/HypothesisFamily... reuse, do not
    duplicate"). Deliberately a thin wrapper around record_research_trial()
    above, not a parallel code path — an OOS trial is still just a
    ResearchExperiment row; the only thing specific to it is that
    `statistical_results` is OOSResult.to_dict() (fold-level detail
    included, per that mission's Section 6 requirement that fold-level
    results are never discarded even once persisted) and `code_version`
    defaults to the engine's own methodology_version if the caller
    didn't pass one, so a stored experiment is traceable to the exact
    fold/purge/embargo semantics that produced it even if the caller
    forgets to pass code_version explicitly.
    """
    statistical_results = oos_result.to_dict()
    resolved_code_version = code_version or oos_result.methodology_version
    return record_research_trial(
        hypothesis_family=hypothesis_family,
        hypothesis_text=hypothesis_text,
        dataset_identities=dataset_identities,
        statistical_results=statistical_results,
        verdict=verdict,
        code_version=resolved_code_version,
        structured_spec={"oos_run_id": oos_result.run_id, "evaluation_type": oos_result.evaluation_type},
        student=student,
    )
