# claude code changed: new file — Django admin registration for Research
# Lab models, same purpose/mechanism as bot/academy/admin.py: lets an
# operator inspect experiments through the admin UI, and its import (via
# bot/admin.py) is what makes Django's app registry aware
# bot/research_lab/models.py exists at all.

from django.contrib import admin

from bot.research_lab.models import ResearchExperiment, ResearchSubscription


@admin.register(ResearchExperiment)
class ResearchExperimentAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "status", "verdict", "created_at")
    list_filter = ("status", "verdict")
    readonly_fields = [f.name for f in ResearchExperiment._meta.fields]  # claude code changed: immutable research records — no accidental admin edits to evidence


# claude code changed: new — Advanced Quant Research Capability
# Architecture. Unlike ResearchExperiment, this IS meant to be edited here
# — it is the "development-safe implementation" of the entitlement
# abstraction (see models.py's ResearchSubscription docstring): an
# operator grants/revokes Pro access by editing a row in this admin, no
# payment provider involved.
@admin.register(ResearchSubscription)
class ResearchSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "tier", "status", "started_at", "expires_at")
    list_filter = ("tier", "status")
    search_fields = ("user__username",)
