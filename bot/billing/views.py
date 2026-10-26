# claude code changed: 2026-10-26 — new file: Billing page, Stripe Checkout / Portal redirects, Stripe webhook.

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from bot.billing import stripe_service
from bot.billing.access import plan_state, start_trial, trial_days_left

logger = logging.getLogger(__name__)


@login_required
def billing(request):
    sub = start_trial(request.user)
    checkout = request.GET.get("checkout")
    if checkout == "success":
        messages.success(request, "Thanks! Your subscription shows here as soon as Stripe confirms it, usually within seconds.")
    elif checkout == "cancelled":
        messages.info(request, "Checkout cancelled. Nothing was charged.")
    return render(request, "billing/billing.html", {
        "sub": sub,
        "state": plan_state(sub),
        "days_left": trial_days_left(sub),
        "payments_configured": stripe_service.payments_configured(),
        "enforced": settings.BILLING_ENFORCED,
        "trial_days": settings.BILLING_TRIAL_DAYS,
        "price_label": settings.BILLING_PRICE_LABEL,
        "active": "billing",
    })


@login_required
@require_POST
def checkout(request):
    sub = start_trial(request.user)
    if plan_state(sub) == "subscribed":   # never create a second subscription — manage the existing one instead
        return portal(request)
    billing_url = request.build_absolute_uri(reverse("billing"))
    try:
        url = stripe_service.create_checkout_url(request.user, billing_url + "?checkout=success",
                                                 billing_url + "?checkout=cancelled")
    except stripe_service.BillingNotConfigured:
        messages.error(request, "Payments are not set up yet. Please try again later.")
        return redirect("billing")
    except Exception:
        logger.exception("Stripe Checkout session failed for user %s", request.user.pk)
        messages.error(request, "Could not reach the payment provider. Please try again in a minute.")
        return redirect("billing")
    return redirect(url, permanent=False)


@login_required
@require_POST
def portal(request):
    try:
        url = stripe_service.create_portal_url(request.user, request.build_absolute_uri(reverse("billing")))
    except stripe_service.BillingNotConfigured:
        messages.error(request, "Payments are not set up yet. Please try again later.")
        return redirect("billing")
    except Exception:
        logger.exception("Stripe Portal session failed for user %s", request.user.pk)
        messages.error(request, "Could not reach the payment provider. Please try again in a minute.")
        return redirect("billing")
    if not url:
        messages.info(request, "There is no subscription to manage yet.")
        return redirect("billing")
    return redirect(url, permanent=False)


@csrf_exempt     # Stripe cannot send a CSRF token; the HMAC signature check in parse_webhook replaces it
@require_POST
def stripe_webhook(request):
    try:
        event = stripe_service.parse_webhook(request.body, request.headers.get("Stripe-Signature"))
    except stripe_service.BillingNotConfigured:
        return HttpResponse("webhook secret not configured", status=503)
    except stripe_service.InvalidWebhook:
        return HttpResponse("invalid signature", status=400)
    outcome = stripe_service.handle_event(event)
    logger.info("Stripe webhook %s %s: %s", event.get("id"), event.get("type"), outcome)
    return HttpResponse("ok")
