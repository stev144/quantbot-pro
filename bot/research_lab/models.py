# ============================================================
# bot/research_lab/models.py
# Research Lab — data model for durable, immutable research experiments.
#
# claude code changed: new file — Research Lab MVP (approved implementation
# phase, following the Research Agent architecture study + audit). Lives as
# a subpackage of the existing single `bot` Django app, same precedent as
# bot/academy/ — Django associates a model with an app by package path, not
# physical proximity to bot/models.py.
#
# No FK to any exchange/execution model anywhere in this file, and no
# import path into bot.engines/bot.core anywhere in this package by
# construction — the Research Lab has no route to live trading (section 20
# of the implementation brief).
# ============================================================

import uuid

from django.conf import settings
from django.db import models


# ─────────────────────────────────────────────────────────────
# APPEND-ONLY / FAMILY-FREEZE ENFORCEMENT
# claude code changed: new — Hardening Mission Section 4. The prior
# platform audit's single biggest RED finding: "FDR family boundaries are
# enforced by researcher discipline rather than by code." Re-verified
# during this mission (not assumed): ResearchExperiment already existed
# and already models most of a research ledger (hypothesis, spec, tool
# log, results, verdict, code_version/git SHA, random_seed, rerun_of
# self-FK) — but had NO save()/delete() override anywhere, and NO concept
# of a hypothesis family at all. "Immutable" was a docstring promise plus
# "orchestrator.py is the only mutator" as a comment, not a guarantee the
# database itself enforces. These two exception types are what these
# models' save()/delete() overrides below actually raise.
# ─────────────────────────────────────────────────────────────

class FamilyAlreadyFrozenError(Exception):
    """Raised when code attempts to change a HypothesisFamily's frozen
    scope (feature list / assets / horizons / venue / timeframe) after it
    has already been frozen — the exact failure mode this mission's
    Section 4 names explicitly: run experiment -> inspect results ->
    expand/redefine family -> rerun FDR."""


class ResearchRecordIsImmutableError(Exception):
    """Raised when code attempts to modify or delete a ResearchExperiment
    that has already reached a terminal status (COMPLETED/FAILED/BLOCKED).
    Research records are append-only: a wrong or unwanted result gets a
    NEW experiment (via rerun_of), never a silently edited old one."""


# ─────────────────────────────────────────────────────────────
# VERDICT TAXONOMY
# Matches the Research Agent architecture study's §09 taxonomy exactly —
# the two additions beyond the brief's own strawman (REGIME_DEPENDENT,
# SUPERSEDED_BY_EXISTING_RESEARCH) are kept, since the brief's own §14
# explicitly lists all nine states.
# ─────────────────────────────────────────────────────────────

VERDICT_CHOICES = [
    ("SUPPORTED", "Supported"),
    ("PARTIALLY_SUPPORTED", "Partially Supported"),
    ("INCONCLUSIVE", "Inconclusive"),
    ("REJECTED", "Rejected"),
    ("INVALID_RESEARCH", "Invalid Research"),
    ("INSUFFICIENT_DATA", "Insufficient Data"),
    ("REQUIRES_REVIEW", "Requires Review"),
    ("REGIME_DEPENDENT", "Regime Dependent"),
    ("SUPERSEDED_BY_EXISTING_RESEARCH", "Superseded By Existing Research"),
]

# claude code changed: new — experiment lifecycle per implementation
# brief §12. BLOCKED is distinct from FAILED: BLOCKED means the Research
# Policy Gate refused to run it at all (unsupported request, missing data,
# over budget); FAILED means it was approved and started but a tool
# errored partway through.
STATUS_CHOICES = [
    ("PENDING", "Pending"),
    ("PLANNED", "Planned"),
    ("RUNNING", "Running"),
    ("COMPLETED", "Completed"),
    ("FAILED", "Failed"),
    ("BLOCKED", "Blocked"),
]


class HypothesisFamily(models.Model):
    """
    claude code changed: new — Hardening Mission Section 4. A
    machine-checkable declaration of exactly which (feature x asset x
    horizon) hypotheses one multiple-testing correction pass covers,
    declared and frozen BEFORE any statistical result exists for it.

    The scope fields (feature_family/assets/horizons/venue/timeframe) are
    write-once-then-locked: freeze() is the only sanctioned way to lock
    them, and save() refuses any further change to a locked family's scope
    — see FamilyAlreadyFrozenError. This is the concrete code enforcement
    the prior audit found missing; every phase in this engagement
    (trade-flow/derivatives/order-book) declared its family in a comment
    and a print() statement, which is real discipline but not a guarantee.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, blank=True)

    feature_family = models.JSONField(default=list)   # frozen list[str] — the exact candidates, nothing added later
    assets = models.JSONField(default=list)            # e.g. ["BTC/USDT", "ETH/USDT"]
    venue = models.CharField(max_length=40, blank=True)
    timeframe = models.CharField(max_length=10, blank=True)
    horizons = models.JSONField(default=list)           # e.g. ["forward_return_1h", "forward_return_4h"]
    correction_method = models.CharField(max_length=40, default="fdr_bh")
    alpha = models.FloatField(default=0.05)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="hypothesis_families"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # claude code changed: null = not yet frozen (scope may still be edited
    # freely while the researcher is still designing the experiment,
    # BEFORE any tool has run against it). Once set, save() enforces that
    # the scope fields below can never change again.
    frozen_at = models.DateTimeField(null=True, blank=True)

    _SCOPE_FIELDS = ("feature_family", "assets", "venue", "timeframe", "horizons")

    def freeze(self) -> None:
        """The ONE sanctioned transition into the locked state. Idempotent —
        freezing an already-frozen family is a no-op, not an error, so
        callers don't need to check first."""
        if self.frozen_at is None:
            from django.utils import timezone
            self.frozen_at = timezone.now()
        self.save()

    @property
    def n_hypotheses(self) -> int:
        """The exact family size this mission's Section 8 requires printed
        before any FDR correction runs: |features| x |assets| x |horizons|."""
        return len(self.feature_family) * len(self.assets) * len(self.horizons)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            try:
                previous = HypothesisFamily.objects.get(pk=self.pk)
            except HypothesisFamily.DoesNotExist:
                previous = None
            if previous is not None and previous.frozen_at is not None:
                for field in self._SCOPE_FIELDS:
                    if getattr(previous, field) != getattr(self, field):
                        raise FamilyAlreadyFrozenError(
                            f"HypothesisFamily {self.pk} was frozen at {previous.frozen_at} — "
                            f"'{field}' cannot change after freezing. Declare a NEW HypothesisFamily "
                            f"instead of widening or redefining this one after seeing results."
                        )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # claude code changed: a family with any linked experiment is
        # already protected at the DB level (ResearchExperiment.hypothesis_family
        # uses on_delete=PROTECT below) — this blanket refusal additionally
        # covers a frozen-but-not-yet-linked family, so "freeze, then delete
        # before anything runs, then reuse the idea under a fresh family"
        # can never quietly happen either.
        if self.frozen_at is not None:
            raise ResearchRecordIsImmutableError(
                f"HypothesisFamily {self.pk} was frozen at {self.frozen_at} and cannot be deleted — "
                f"failed/rejected families remain part of the research record, not silently removed."
            )
        super().delete(*args, **kwargs)

    def __str__(self):
        frozen = "frozen" if self.frozen_at else "draft"
        return f"{self.name or self.pk} ({frozen}, {self.n_hypotheses} hypotheses)"


class ResearchExperiment(models.Model):
    """
    One durable, immutable record per hypothesis a student submits.

    claude code changed: deliberately ONE model, not a model-per-stage —
    every field below is either write-once (hypothesis_text, structured_spec,
    random_seed, code_version) or append/replace-at-a-known-lifecycle-point
    (research_plan written once the plan stage completes, statistical/
    validation/robustness_results written once tools finish, verdict/
    ai_interpretation written last). This keeps "what the user asked -> what
    was understood -> what was tested -> what data was used -> what tools
    ran -> what numbers were produced -> what verdict was reached"
    reconstructable from a single row, per the brief's own requirement in
    section 4, without a report/artifact needing to duplicate any of it —
    the report is rendered from this row on demand (see views/report.py),
    never stored as a second copy.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="research_experiments"
    )

    # ── What the user asked ──────────────────────────────────────────────
    hypothesis_text = models.TextField()

    # ── What the AI understood (section 6) ───────────────────────────────
    # claude code changed: structured_spec's shape is defined and validated
    # by bot/research_lab/spec.py's ResearchSpec dataclass + validate() —
    # this field stores its serialized form, not a second schema definition.
    structured_spec = models.JSONField(default=dict, blank=True)

    # ── What the system intends to do (section 11) ───────────────────────
    research_plan = models.JSONField(default=dict, blank=True)

    # ── What tools ran (section 22 — observability) ──────────────────────
    # claude code changed: a JSONField list of per-call log entries
    # (tool name, params, duration, status, error) rather than a separate
    # related table — one experiment's tool-call volume is small (a
    # handful of calls, not thousands), so a related table would be
    # unnecessary duplication of what this row already needs to hold for
    # reconstructability per section 4.
    tool_call_log = models.JSONField(default=list, blank=True)

    # ── What numbers were produced — ONLY ever written by tool-layer code,
    # never by the AI interpretation step (section 1's hard rule) ─────────
    statistical_results = models.JSONField(default=dict, blank=True)
    validation_results = models.JSONField(default=dict, blank=True)
    robustness_results = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)

    # ── AI interpretation — explanation only, written after every
    # deterministic result above already exists (section 15) ─────────────
    ai_interpretation = models.TextField(blank=True)

    # ── What verdict was reached — set only by the deterministic verdict
    # engine (bot/research_lab/verdict.py), never by the AI (section 14) ──
    verdict = models.CharField(max_length=40, choices=VERDICT_CHOICES, blank=True)

    # ── Reproducibility (section 16) ──────────────────────────────────────
    random_seed = models.IntegerField(null=True, blank=True)
    code_version = models.CharField(max_length=40, blank=True)  # git SHA at run time

    # ── Lifecycle ──────────────────────────────────────────────────────────
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # claude code changed: new — section 17's "immutable research records;
    # rerunning creates a new experiment with a reference to the previous
    # one" requirement. Self-FK, nullable — most experiments have no
    # predecessor.
    rerun_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="reruns"
    )

    # ── Advanced Quant Research Capability Architecture (section 14) ──────
    # claude code changed: new — which capability_registry.py capability
    # this experiment tested. A flat, queryable field (not nested inside
    # research_plan) since it is the natural filter/reporting dimension for
    # future billing/audit questions ("how many PRO-tier experiments did
    # this user run this month"). Blank for any experiment created before
    # this field existed. Subscription tier at execution time and compute
    # budget consumed are recorded inside research_plan['entitlement'] at
    # plan time instead — they are planning-time execution context, the
    # same bucket data_availability/policy_decision already live in, not a
    # first-class reporting dimension of their own.
    capability_id = models.CharField(max_length=64, blank=True)

    # ── Hardening Mission Sections 4/5 — added to the EXISTING model,
    # never a parallel/duplicate table. Both nullable/blank for backward
    # compatibility with every experiment created before these fields
    # existed (this project's own established convention — see e.g.
    # capability_id's comment above making the identical trade-off). ──
    hypothesis_family = models.ForeignKey(
        HypothesisFamily, on_delete=models.PROTECT, null=True, blank=True, related_name="experiments"
    )   # claude code changed: PROTECT — a family with any linked experiment can never be deleted out from under it
    data_fingerprint = models.CharField(max_length=64, blank=True)   # sha256 hex from bot.research_lab.data_fingerprint

    # claude code changed: which terminal states make a row append-only.
    # PENDING/PLANNED/RUNNING are still legitimately mutable — an
    # experiment is only "the record" once it stops changing.
    _TERMINAL_STATUSES = ("COMPLETED", "FAILED", "BLOCKED")

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        # claude code changed: Hardening Mission Section 4 — this is the
        # concrete code enforcement for what used to be only a file-header
        # comment ("orchestrator.py is the ONLY place that mutates").
        # Once a row has reached a terminal status, NO further save() may
        # succeed, regardless of which fields changed — a wrong or
        # unwanted result becomes a NEW experiment via rerun_of, never a
        # silently corrected old one.
        if self.pk is not None:
            try:
                previous_status = ResearchExperiment.objects.only("status").get(pk=self.pk).status
            except ResearchExperiment.DoesNotExist:
                previous_status = None
            if previous_status in self._TERMINAL_STATUSES:
                raise ResearchRecordIsImmutableError(
                    f"ResearchExperiment {self.pk} already reached terminal status "
                    f"'{previous_status}' and cannot be modified further — research records "
                    f"are append-only. Create a new experiment (hypothesis_family stays linked, "
                    f"set rerun_of=this experiment) instead of editing this one."
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # claude code changed: Hardening Mission Section 4 — "failed
        # experiments must never disappear." No ResearchExperiment may
        # ever be deleted, full stop, regardless of status.
        raise ResearchRecordIsImmutableError(
            f"ResearchExperiment {self.pk} cannot be deleted — research records (including "
            f"failed/rejected ones) are permanent, append-only institutional knowledge."
        )

    def __str__(self):
        return f"{self.student.username} — {self.hypothesis_text[:60]}"


# ─────────────────────────────────────────────────────────────
# SUBSCRIPTION ENTITLEMENT — Advanced Quant Research Capability Architecture
# (sections 9, 17)
#
# claude code changed: new. Per section 17: "If no billing system exists,
# do NOT invent a payment provider or install packages automatically.
# Instead create the entitlement abstraction/interface and a
# development-safe implementation." A repo-wide audit (this session)
# confirmed no Plan/Subscription/billing model, no Stripe or other payment
# integration, exists anywhere in this project — Academy's Enrollment
# model has an "subscription" access_source CHOICE that is a placeholder
# for a future, unbuilt payments phase, never actually written.
#
# This model IS the "development-safe implementation": real, persisted,
# queryable entitlement state that a future payment-provider webhook would
# write into, without this project inventing what that provider is. An
# operator can grant/revoke PRO manually via Django admin today; nothing
# here talks to a payment network.
# ─────────────────────────────────────────────────────────────

SUBSCRIPTION_TIER_CHOICES = [("CORE", "Core"), ("PRO", "Pro")]
SUBSCRIPTION_STATUS_CHOICES = [("ACTIVE", "Active"), ("EXPIRED", "Expired"), ("CANCELED", "Canceled")]


class ResearchSubscription(models.Model):
    """
    claude code changed: new. Absence of a row (or a row with tier=CORE)
    means CORE — every authenticated user implicitly has CORE access, so
    there is no need to backfill a row per user. Only a PRO grant needs an
    actual row. One row per user (OneToOne) — this phase has no concept of
    multiple concurrent plans; a plan change updates the existing row
    rather than creating a second one, keeping "what tier is this user on
    right now" a single, unambiguous read.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="research_subscription")
    tier = models.CharField(max_length=10, choices=SUBSCRIPTION_TIER_CHOICES, default="CORE")
    status = models.CharField(max_length=10, choices=SUBSCRIPTION_STATUS_CHOICES, default="ACTIVE")
    started_at = models.DateTimeField(auto_now_add=True)
    # claude code changed: null = no expiry (a manually-granted PRO plan
    # with no end date) — distinct from a real, dated expiry a future
    # billing webhook would set.
    expires_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} — {self.tier} ({self.status})"
