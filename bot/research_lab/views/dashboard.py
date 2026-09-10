# claude code changed: new file — Research Lab landing page (section 5).
# Deliberately just "What do you believe about the market?" plus a link
# to history — no technical fields up front, per section 5's own
# instruction not to overwhelm the user initially.

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from bot.instruments import ASSET_CLASS_FOREX
from bot.research_lab.models import ResearchExperiment


def _asset_class_hint(request):
    """
    claude code changed: new — Crypto/Forex Navigation Discoverability
    follow-up. Real user report: clicking the navbar's "Forex" link
    landed on this exact page showing a hardcoded Bitcoin example
    hypothesis, which read as a bug (why does "Forex" show a crypto
    narrative?). This page never asks for an asset up front by design
    (section 5's own "don't overwhelm the user initially" rule — asset
    selection stays on the next, formalize page) — but the EXAMPLE COPY
    shown here can and should still match how the visitor arrived,
    without adding a real field or a second page. Only ever changes
    display text; never persisted onto the experiment itself, and never
    consulted by validate_spec()/entitlements — the real asset_class the
    research actually runs against is still decided entirely by what's
    picked on the formalize page, unaffected by this hint.
    """
    hint = request.GET.get("asset_class") or request.POST.get("asset_class") or ""
    return ASSET_CLASS_FOREX if hint.upper() == ASSET_CLASS_FOREX else None


@login_required
def research_lab_dashboard(request):
    asset_class_hint = _asset_class_hint(request)

    if request.method == "POST":
        hypothesis_text = request.POST.get("hypothesis_text", "").strip()
        if not hypothesis_text:
            return render(request, "research_lab/dashboard.html", {
                "error": "Please describe what you believe about the market.",
                "asset_class_hint": asset_class_hint,
            })

        experiment = ResearchExperiment.objects.create(student=request.user, hypothesis_text=hypothesis_text)
        return redirect("research_lab_formalize", experiment_id=experiment.id)

    recent = ResearchExperiment.objects.filter(student=request.user)[:5]
    return render(request, "research_lab/dashboard.html", {"recent": recent, "asset_class_hint": asset_class_hint})
