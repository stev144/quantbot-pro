# claude code changed: rewritten — one-question-at-a-time onboarding
# (Academy UX/UI transformation, Phase B) replaces the old single giant
# form. Each question is now saved as soon as it's answered (real
# persistence per-step, so a student who navigates away partway keeps
# their progress), and completion is checked by comparing saved
# DiagnosticResponse rows against FLAT_DIAGNOSTIC_QUESTIONS rather than
# requiring one big POST payload.

from django.utils import timezone

from bot.academy.diagnostics import (
    FLAT_DIAGNOSTIC_QUESTIONS, build_profile_scores, recommend_starting_path_slug,
)
from bot.academy.models import DiagnosticResponse, StudentProfile


def parse_single_answer(question, post_data):
    """
    Reads this one question's answer out of a POST QueryDict, in the same
    value-encoding scheme the scoring functions expect (index-based for
    knowledge-check questions with a correct_index, raw string/bool
    otherwise). Returns None if unanswered/malformed — caller should not
    advance to the next question in that case.
    """
    qtype = question["type"]

    if qtype == "boolean":
        raw = post_data.get("value")
        if raw is None:
            return None
        return {"value": raw == "true"}

    if qtype == "choice":
        raw = post_data.get("value")
        if raw is None or raw == "":
            return None
        if "correct_index" in question:
            try:
                return {"selected_index": int(raw)}
            except ValueError:
                return None
        return {"value": raw}

    if qtype == "multi_choice":
        raw_list = post_data.getlist("value")
        if not raw_list:
            return None
        return {"value": raw_list}

    return None


def save_single_response(user, question, post_data):
    """Parses and persists one answer. Returns True if saved, False if the
    submission was empty/invalid (caller should re-render the same step)."""
    payload = parse_single_answer(question, post_data)
    if payload is None:
        return False

    DiagnosticResponse.objects.update_or_create(
        student=user, question_key=question["key"],
        defaults={"category": question["category"], "response_payload": payload},
    )
    return True


def is_diagnostic_complete(user):
    required_keys = {q["key"] for q in FLAT_DIAGNOSTIC_QUESTIONS}
    answered_keys = set(
        DiagnosticResponse.objects.filter(student=user, question_key__in=required_keys)
        .values_list("question_key", flat=True)
    )
    return answered_keys == required_keys


def complete_onboarding(user, responses_by_category):
    scores = build_profile_scores(responses_by_category)
    goal_response = responses_by_category.get("goals", {}).get("primary_goal", {})

    profile, _ = StudentProfile.objects.update_or_create(
        student=user,
        defaults={
            **scores,
            "primary_goal": goal_response.get("value", ""),
            "onboarding_completed_at": timezone.now(),
        },
    )
    recommended_slug = recommend_starting_path_slug(scores)
    return profile, recommended_slug


def finish_onboarding_if_ready(user):
    """
    Reconstructs responses_by_category from saved DiagnosticResponse rows
    and completes onboarding once every flattened question has been
    answered. Returns (None, None) if the diagnostic isn't complete yet —
    callers should send the student back to the first unanswered step.
    """
    if not is_diagnostic_complete(user):
        return None, None

    required_keys = {q["key"] for q in FLAT_DIAGNOSTIC_QUESTIONS}
    saved = DiagnosticResponse.objects.filter(student=user, question_key__in=required_keys)

    responses_by_category = {}
    for response in saved:
        responses_by_category.setdefault(response.category, {})[response.question_key] = response.response_payload

    return complete_onboarding(user, responses_by_category)


def first_unanswered_step(user):
    """1-indexed position of the first question this student hasn't
    answered yet, or len(FLAT_DIAGNOSTIC_QUESTIONS) + 1 if all are done."""
    answered_keys = set(
        DiagnosticResponse.objects.filter(student=user).values_list("question_key", flat=True)
    )
    for index, question in enumerate(FLAT_DIAGNOSTIC_QUESTIONS, start=1):
        if question["key"] not in answered_keys:
            return index
    return len(FLAT_DIAGNOSTIC_QUESTIONS) + 1
