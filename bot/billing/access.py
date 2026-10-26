# claude code changed: 2026-10-26 — new file: who may open a premium page, and which pages are premium.
# Pure rules over the local Subscription row — no Stripe call here, so every page check is one DB read at most.

import math
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from bot.billing.models import Subscription

# Stripe subscription statuses that grant access. "past_due" keeps access while Stripe retries a failed card; Stripe
# moves the subscription to "unpaid"/"canceled" when retries run out, and those statuses do not grant access.
PAID_STATUSES = frozenset({"active", "trialing", "past_due"})

# Premium = research, scanners, Market Intelligence, portfolio and execution pages. Free = Markets, Forex dashboard,
# Academy, the backtest dashboard and the Paper Trader ("test before you trade"). Paths are checked before URL names.
PREMIUM_PATH_PREFIXES = (
    "/research/",
    "/research-lab/",
    "/forex/scanner/",
    "/market/",          # Market Intelligence (note: "/markets/" — the free Markets page — does not start with this)
    "/portfolio/",
    "/execution/",
)
FREE_PATH_PREFIXES = (
    "/execution/paper-trading/",
)
PREMIUM_URL_NAMES = frozenset({
    "market_workspace", "mw_core", "mw_risk", "mw_news", "mw_research", "mw_history_start", "mw_history_reveal",
})


def is_premium_request(request):
    path = request.path_info
    if any(path.startswith(p) for p in FREE_PATH_PREFIXES):
        return False
    if any(path.startswith(p) for p in PREMIUM_PATH_PREFIXES):
        return True
    match = getattr(request, "resolver_match", None)
    return bool(match and match.url_name in PREMIUM_URL_NAMES)


def start_trial(user, now=None):
    """The user's billing row, created with a fresh trial when missing. Users who signed up before billing existed
    get their 30 days from the first time this runs for them, not from their old sign-up date."""
    now = now or timezone.now()
    sub, _ = Subscription.objects.get_or_create(
        user=user,
        defaults={"trial_started_at": now, "trial_ends_at": now + timedelta(days=settings.BILLING_TRIAL_DAYS)},
    )
    return sub


def plan_state(sub, now=None):
    """'subscribed' | 'trial' | 'expired'."""
    now = now or timezone.now()
    if sub.stripe_status in PAID_STATUSES:
        return "subscribed"
    if now < sub.trial_ends_at:
        return "trial"
    return "expired"


def trial_days_left(sub, now=None):
    now = now or timezone.now()
    seconds = (sub.trial_ends_at - now).total_seconds()
    return max(0, math.ceil(seconds / 86400))


def has_premium_access(user, now=None):
    if not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    return plan_state(start_trial(user, now), now) in ("subscribed", "trial")
