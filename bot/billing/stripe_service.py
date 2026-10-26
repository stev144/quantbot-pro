# claude code changed: 2026-10-26 — new file: every Stripe call lives here (Checkout, Customer Portal, webhook events).
# Card details never touch this server: Checkout and the Portal are Stripe-hosted pages; we only receive the
# signed webhook that tells us the subscription's status.

import json
import logging
from datetime import datetime, timezone as dt_timezone

import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from bot.billing.access import PAID_STATUSES, start_trial
from bot.billing.models import Subscription

logger = logging.getLogger(__name__)

# Stripe Checkout refuses a subscription trial_end less than 48 hours away; one hour of margin on top.
_MIN_CARRIED_TRIAL_SECONDS = 49 * 3600
# Tags these Checkout Sessions in the Stripe Dashboard (Stripe's convention: a label plus 8 random letters).
_INTEGRATION_ID = "sqt-subscribe-trial-qhvmzkrd"


class BillingNotConfigured(Exception):
    """STRIPE_SECRET_KEY / STRIPE_PRICE_ID (or STRIPE_WEBHOOK_SECRET for the webhook) are not set."""


class InvalidWebhook(Exception):
    """The request body or its Stripe-Signature header did not verify."""


def payments_configured():
    return bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_PRICE_ID)


def _client():
    if not payments_configured():
        raise BillingNotConfigured("Set STRIPE_SECRET_KEY and STRIPE_PRICE_ID in .env")
    return stripe.StripeClient(settings.STRIPE_SECRET_KEY)


def checkout_params(user, sub, success_url, cancel_url, now=None):
    """Parameters for a subscription Checkout Session. Kept separate from the network call so it can be tested.
    Unused free-trial time carries over: a user who subscribes on day 10 is first charged when the 30 days end."""
    now = now or timezone.now()
    subscription_data = {"metadata": {"user_id": str(user.pk)}}
    if (sub.trial_ends_at - now).total_seconds() >= _MIN_CARRIED_TRIAL_SECONDS:
        subscription_data["trial_end"] = int(sub.trial_ends_at.timestamp())
    params = {
        "mode": "subscription",
        "line_items": [{"price": settings.STRIPE_PRICE_ID, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(user.pk),
        "metadata": {"user_id": str(user.pk)},
        "subscription_data": subscription_data,
        "integration_identifier": _INTEGRATION_ID,
    }
    if sub.stripe_customer_id:
        params["customer"] = sub.stripe_customer_id
    elif user.email:
        params["customer_email"] = user.email
    return params


def create_checkout_url(user, success_url, cancel_url):
    sub = start_trial(user)
    session = _client().v1.checkout.sessions.create(params=checkout_params(user, sub, success_url, cancel_url))
    return session.url


def create_portal_url(user, return_url):
    sub = start_trial(user)
    if not sub.stripe_customer_id:
        return None
    session = _client().v1.billing_portal.sessions.create(params={"customer": sub.stripe_customer_id,
                                                                    "return_url": return_url})
    return session.url


def parse_webhook(payload, sig_header):
    """Verify the Stripe-Signature header (HMAC over the raw body) and return the event as a plain dict."""
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise BillingNotConfigured("Set STRIPE_WEBHOOK_SECRET in .env")
    try:
        stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise InvalidWebhook(str(exc)) from exc
    # stripe-python 16's StripeObject is not a dict; the verified raw JSON is.
    return json.loads(payload)


def _ts(value):
    return datetime.fromtimestamp(value, tz=dt_timezone.utc) if value else None


def _period_end(stripe_sub):
    # Newer Stripe API versions report the billing period per subscription item, older ones on the subscription.
    items = (stripe_sub.get("items") or {}).get("data") or []
    if items and items[0].get("current_period_end"):
        return _ts(items[0]["current_period_end"])
    return _ts(stripe_sub.get("current_period_end"))


def _user_id_from(obj):
    return obj.get("client_reference_id") or (obj.get("metadata") or {}).get("user_id")


def _find_for_subscription(obj):
    # Stripe's own object graph first (subscription id, then customer id); our metadata user_id only as a fallback,
    # for a subscription event that arrives before checkout.session.completed has linked the ids.
    sub = Subscription.objects.select_for_update().filter(stripe_subscription_id=obj["id"]).first()
    if sub:
        return sub
    if obj.get("customer"):
        sub = Subscription.objects.select_for_update().filter(stripe_customer_id=obj["customer"]).first()
        if sub:
            return sub
    user_id = _user_id_from(obj)
    if user_id:
        return Subscription.objects.select_for_update().filter(user_id=user_id).first()
    return None


# Money-at-risk events: nothing changes access automatically, but each one is logged loudly for the owner to act on
# in the Stripe Dashboard (respond to a dispute, check a refund, review a fraud warning, a failed renewal charge).
_ALERT_EVENTS = frozenset({"charge.dispute.created", "charge.refunded", "radar.early_fraud_warning.created",
                           "invoice.payment_failed"})


@transaction.atomic
def handle_event(event):
    """Apply one verified Stripe event to the local copy. Returns a short outcome string (logged, and used by tests)."""
    etype = event.get("type", "")
    obj = event["data"]["object"]
    created = int(event.get("created") or 0)

    if etype == "checkout.session.completed":
        if obj.get("mode") != "subscription":
            return "ignored: not a subscription checkout"
        user_id = _user_id_from(obj)
        sub = Subscription.objects.select_for_update().filter(user_id=user_id).first() if user_id else None
        if sub is None:
            logger.warning("Stripe checkout %s: no billing row for user_id=%r", obj.get("id"), user_id)
            return "ignored: unknown user"
        sub.stripe_customer_id = obj.get("customer") or sub.stripe_customer_id
        sub.stripe_subscription_id = obj.get("subscription") or sub.stripe_subscription_id
        sub.save(update_fields=["stripe_customer_id", "stripe_subscription_id", "updated_at"])
        return "linked"

    if etype in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        sub = _find_for_subscription(obj)
        if sub is None:
            logger.warning("Stripe %s %s: no matching billing row", etype, obj.get("id"))
            return "ignored: unknown subscription"
        if created < sub.last_event_created:
            return "ignored: older than the state already stored"
        # An event about a superseded subscription must not cancel the user's newer, paid one.
        if (sub.stripe_subscription_id and sub.stripe_subscription_id != obj["id"]
                and sub.stripe_status in PAID_STATUSES and obj.get("status") not in PAID_STATUSES):
            return "ignored: superseded subscription"
        sub.stripe_subscription_id = obj["id"]
        sub.stripe_customer_id = obj.get("customer") or sub.stripe_customer_id
        sub.stripe_status = obj.get("status", "")
        sub.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
        sub.current_period_end = _period_end(obj)
        sub.last_event_created = created
        sub.save()
        return f"status={sub.stripe_status}"

    if etype in _ALERT_EVENTS:
        # Access itself follows customer.subscription.updated (a failed renewal moves the status to past_due, then
        # unpaid/canceled once Stripe's retries run out).
        logger.warning("Stripe %s on %s (customer=%s) — review it in the Stripe Dashboard",
                       etype, obj.get("id"), obj.get("customer"))
        return f"alert: {etype}"

    return f"ignored: {etype}"
