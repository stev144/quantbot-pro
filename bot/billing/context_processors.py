# claude code changed: 2026-10-26 — new file: the plan label on the navbar's Billing button ("Trial · 23d", "Pro", "Upgrade")

from bot.billing.access import plan_state, start_trial, trial_days_left


def billing_nav(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    if user.is_staff or user.is_superuser:
        return {"billing_nav_label": "Billing"}
    sub = start_trial(user)
    state = plan_state(sub)
    label = {"subscribed": "Pro", "expired": "Upgrade"}.get(state) or f"Trial · {trial_days_left(sub)}d"
    return {"billing_nav_label": label}
