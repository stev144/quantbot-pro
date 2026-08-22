# claude code changed: new file — section 17, experiment history. Read-
# only list; experiments are immutable research records, there is no edit
# path here by design.

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from bot.research_lab.models import ResearchExperiment


@login_required
def history(request):
    experiments = ResearchExperiment.objects.filter(student=request.user)
    return render(request, "research_lab/history.html", {"experiments": experiments})
