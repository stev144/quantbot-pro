# claude code changed: new file — onboarding diagnostic data layer.
# Single-page diagnostic (all categories on one form) rather than a
# multi-step wizard — a deliberate scope simplification for this narrow
# first slice; DiagnosticResponse/StudentProfile's shape doesn't change
# either way, so splitting into a true multi-step wizard later is a
# view-layer change only.

from django.utils import timezone

from bot.academy.diagnostics import DIAGNOSTIC_QUESTIONS, build_profile_scores, recommend_starting_path_slug
from bot.academy.models import DiagnosticResponse, StudentProfile


def parse_and_save_responses(user, post_data):
    """
    Reads DIAGNOSTIC_QUESTIONS-shaped fields out of a POST QueryDict,
    saves one DiagnosticResponse per answered question, and returns the
    responses grouped by category in the shape score_* functions expect.
    """
    responses_by_category = {}

    for category, questions in DIAGNOSTIC_QUESTIONS.items():
        category_responses = {}
        for q in questions:
            field_name = f"{category}__{q['key']}"

            if q["type"] == "boolean":
                raw = post_data.get(field_name)
                if raw is None:
                    continue
                value = {"value": raw == "true"}
            elif q["type"] == "choice":
                raw = post_data.get(field_name)
                if raw is None or raw == "":
                    continue
                if "correct_index" in q:
                    try:
                        value = {"selected_index": int(raw)}
                    except ValueError:
                        continue
                else:
                    value = {"value": raw}
            elif q["type"] == "multi_choice":
                raw_list = post_data.getlist(field_name)
                if not raw_list:
                    continue
                value = {"value": raw_list}
            else:
                continue

            DiagnosticResponse.objects.update_or_create(
                student=user, question_key=q["key"],
                defaults={"category": category, "response_payload": value},
            )
            category_responses[q["key"]] = value

        responses_by_category[category] = category_responses

    return responses_by_category


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
