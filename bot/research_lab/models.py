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

    class Meta:
        ordering = ["-created_at"]

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
