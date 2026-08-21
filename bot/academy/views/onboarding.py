# claude code changed: rewritten — one-question-at-a-time onboarding
# (Academy UX/UI transformation, Phase B). academy_onboarding is now just
# an entry point that routes to the right step (or straight to the result
# page if already complete); the actual question/answer cycle lives in
# onboarding_step, one full request per question, per Phase A's decision
# to avoid a JSON API / JS bundle for this (section 19 — no unnecessary
# API calls, no huge JS bundles).

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

from bot.academy.diagnostics import FLAT_DIAGNOSTIC_QUESTIONS
from bot.academy.models import LearningPath, StudentProfile
from bot.academy.views.onboarding_data import (
    save_single_response, finish_onboarding_if_ready, first_unanswered_step,
)

TOTAL_QUESTIONS = len(FLAT_DIAGNOSTIC_QUESTIONS)


@login_required
def onboarding(request):
    profile = StudentProfile.objects.filter(student=request.user).first()
    if profile is not None and profile.onboarding_completed_at is not None:
        return redirect("academy_onboarding_result")
    return redirect("academy_onboarding_step", step=first_unanswered_step(request.user))


@login_required
def onboarding_step(request, step):
    if step < 1 or step > TOTAL_QUESTIONS:
        return redirect("academy_onboarding_step", step=1)

    question = FLAT_DIAGNOSTIC_QUESTIONS[step - 1]

    if request.method == "POST":
        saved = save_single_response(request.user, question, request.POST)
        if not saved:
            return render(request, "academy/onboarding_step.html", {
                "question": question, "step": step, "total": TOTAL_QUESTIONS,
                "progress_pct": round(step / TOTAL_QUESTIONS * 100),
                "error": "Please choose an answer before continuing.",
            })
        if step >= TOTAL_QUESTIONS:
            return redirect("academy_onboarding_result")
        return redirect("academy_onboarding_step", step=step + 1)

    return render(request, "academy/onboarding_step.html", {
        "question": question, "step": step, "total": TOTAL_QUESTIONS,
        "progress_pct": round(step / TOTAL_QUESTIONS * 100),
    })


@login_required
def onboarding_result(request):
    profile, recommended_slug = finish_onboarding_if_ready(request.user)
    if profile is None:
        return redirect("academy_onboarding_step", step=first_unanswered_step(request.user))

    recommended_path = LearningPath.objects.filter(slug=recommended_slug).first()
    return render(request, "academy/onboarding_result.html", {
        "profile": profile,
        "recommended_path": recommended_path,
    })
