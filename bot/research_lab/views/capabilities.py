# claude code changed: new file — Advanced Quant Research Capability
# Architecture, Phase E (UI capability catalog + locked states). Every
# capability is always visible here regardless of entitlement — section
# 10/11's "visible but locked" principle — the badge is what changes per
# user, computed by ResearchEntitlementService.capability_ui_state(),
# never by this view re-deriving its own access logic.

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from bot.research_lab.capability_registry import list_by_category
from bot.research_lab.entitlements import ResearchEntitlementService


@login_required
def capabilities(request):
    categories = []
    for category_name, capability_list in list_by_category().items():
        rows = []
        for capability in capability_list:
            ui_state = ResearchEntitlementService.capability_ui_state(request.user, capability.id)
            rows.append({"capability": capability, "ui_state": ui_state})
        categories.append({"name": category_name, "capabilities": rows})

    return render(request, "research_lab/capabilities.html", {"categories": categories})
