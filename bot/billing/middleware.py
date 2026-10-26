# claude code changed: 2026-10-26 — new file: blocks premium pages for visitors without a trial or subscription.
# Off unless BILLING_ENFORCED=true, so the site behaves exactly as before until the owner switches it on.

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render

from bot.billing.access import has_premium_access, is_premium_request, plan_state, start_trial
from bot.billing.stripe_service import payments_configured


class PremiumAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not settings.BILLING_ENFORCED or not is_premium_request(request):
            return None
        if has_premium_access(request.user):
            return None
        # 402 Payment Required: browsers show the page normally; scripts polling an API see a clear status.
        wants_json = "/api/" in request.path_info or request.path_info.endswith(".json") \
            or "application/json" in request.headers.get("Accept", "")
        if wants_json:
            return JsonResponse({"error": "premium", "detail": "A free trial or subscription is required."}, status=402)
        sub = start_trial(request.user) if request.user.is_authenticated else None
        return render(request, "billing/upgrade.html", {
            "sub": sub,
            "state": plan_state(sub) if sub else "anonymous",
            "payments_configured": payments_configured(),
            "trial_days": settings.BILLING_TRIAL_DAYS,
            "price_label": settings.BILLING_PRICE_LABEL,
        }, status=402)
