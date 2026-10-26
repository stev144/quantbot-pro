# claude code changed: 2026-10-26 — new file: billing rows in Django admin (imported by bot/admin.py, which is also what
# makes the app registry see bot/billing/models.py — same mechanism as bot/academy/admin.py)

from django.contrib import admin

from bot.billing.models import Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "stripe_status", "trial_ends_at", "current_period_end", "cancel_at_period_end", "updated_at")
    list_filter = ("stripe_status", "cancel_at_period_end")
    search_fields = ("user__username", "user__email", "stripe_customer_id", "stripe_subscription_id")
    readonly_fields = ("stripe_customer_id", "stripe_subscription_id", "stripe_status", "current_period_end",
                       "cancel_at_period_end", "last_event_created", "created_at", "updated_at")
