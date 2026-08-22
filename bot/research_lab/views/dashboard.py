# claude code changed: new file — Research Lab landing page (section 5).
# Deliberately just "What do you believe about the market?" plus a link
# to history — no technical fields up front, per section 5's own
# instruction not to overwhelm the user initially.

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from bot.research_lab.models import ResearchExperiment


@login_required
def research_lab_dashboard(request):
    if request.method == "POST":
        hypothesis_text = request.POST.get("hypothesis_text", "").strip()
        if not hypothesis_text:
            return render(request, "research_lab/dashboard.html", {"error": "Please describe what you believe about the market."})

        experiment = ResearchExperiment.objects.create(student=request.user, hypothesis_text=hypothesis_text)
        return redirect("research_lab_formalize", experiment_id=experiment.id)

    recent = ResearchExperiment.objects.filter(student=request.user)[:5]
    return render(request, "research_lab/dashboard.html", {"recent": recent})
