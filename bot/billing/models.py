# claude code changed: 2026-10-26 — new file: one billing record per user (trial window + mirrored Stripe subscription state).
# Stripe is the source of truth for paid status; these fields are only a local copy written by the verified webhook
# (bot/billing/stripe_service.py), so a page check never needs a network call.

from django.conf import settings
from django.db import models


class Subscription(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="billing")

    trial_started_at = models.DateTimeField()
    trial_ends_at = models.DateTimeField()

    stripe_customer_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    stripe_subscription_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    stripe_status = models.CharField(max_length=32, blank=True, default="")   # Stripe's own status string, e.g. "active", "canceled"
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    # `created` of the last Stripe event applied — Stripe does not guarantee delivery order, so an older event never
    # overwrites state written by a newer one.
    last_event_created = models.BigIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} — {self.stripe_status or 'trial'}"
