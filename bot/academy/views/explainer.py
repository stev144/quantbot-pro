# claude code changed: 2026-10-26 — new file: Steph Quant Research Explainer page (Academy). Definitions and evidence
# are public; asking the AI requires a signed-in user (owner decision 2026-10-26) and a CSRF-protected POST.
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import render

from bot.academy.explainer import service as svc
from bot.academy.explainer.registry import REGISTRY_VERSION, all_concepts, get_concept
from bot.ai.client import has_api_key


def research_explainer(request):
    data = request.POST if request.method == "POST" else request.GET
    concept_id = (data.get("concept") or "").strip()
    mode = data.get("mode") if data.get("mode") in svc.MODES else "beginner"
    result = None
    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        result = svc.explain(request.user, request.POST.get("question", ""), concept_id, mode)
        result["status_label"] = svc.STATUS_LABELS.get(result["status"], result["status"])
        local = {"concept": result.get("concept"), "ladder": result.get("ladder")}
    else:
        local = svc.local_documentation(concept_id)
    concept = local["concept"]
    related = [c for c in (get_concept(r) for r in (concept.related if concept else ())) if c]
    return render(request, "academy/explainer.html", {
        "concepts": all_concepts(),
        "concept": concept,
        "ladder": local["ladder"],
        "related": related,
        "mode": mode,
        "result": result,
        "question": (result or {}).get("question") or request.POST.get("question", ""),
        "ai_available": settings.EXPLAINER_AI_ENABLED and has_api_key(),
        "max_chars": settings.EXPLAINER_MAX_QUESTION_CHARS,
        "model": svc.model_for(mode),   # claude code changed: 2026-10-26 — per-mode model
        "models": {"beginner": settings.EXPLAINER_AI_MODEL, "research": settings.EXPLAINER_AI_MODEL_RESEARCH},
        "registry_version": REGISTRY_VERSION,
    })
