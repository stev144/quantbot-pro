# claude code changed: new file — onboarding diagnostic view.

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

from bot.academy.diagnostics import DIAGNOSTIC_QUESTIONS
from bot.academy.views.onboarding_data import parse_and_save_responses, complete_onboarding


@login_required
def onboarding(request):
    if request.method == "POST":
        responses = parse_and_save_responses(request.user, request.POST)
        profile, recommended_slug = complete_onboarding(request.user, responses)
        return redirect("academy_path_detail", slug=recommended_slug)

    return render(request, "academy/onboarding.html", {"questions_by_category": DIAGNOSTIC_QUESTIONS})
