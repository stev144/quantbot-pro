# claude code changed: new file — Django admin registration for Research
# Lab models, same purpose/mechanism as bot/academy/admin.py: lets an
# operator inspect experiments through the admin UI, and its import (via
# bot/admin.py) is what makes Django's app registry aware
# bot/research_lab/models.py exists at all.

from django.contrib import admin

from bot.research_lab.models import ResearchExperiment


@admin.register(ResearchExperiment)
class ResearchExperimentAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "status", "verdict", "created_at")
    list_filter = ("status", "verdict")
    readonly_fields = [f.name for f in ResearchExperiment._meta.fields]  # claude code changed: immutable research records — no accidental admin edits to evidence
