# claude code changed: new file — data/business logic for the Academy
# dashboard, kept separate from the thin view per this project's existing
# view.py/view_data.py convention (see bot/views/research_lab_data.py
# etc.).

from bot.academy.models import (
    Enrollment, LessonProgress, QuizAttempt, Course, StudentProfile,
)


def get_student_dashboard(user):
    """
    Real, computed dashboard data for an authenticated student — no
    stored/cached skill scores (see architecture report: computed on
    read from QuizAttempt/LessonProgress, one source of truth).
    """
    profile = StudentProfile.objects.filter(student=user).first()

    enrollments = list(
        Enrollment.objects.filter(student=user, status="active")
        .select_related("course", "learning_path")
    )

    # claude code changed: 2026-10-26 — "X / Y" now counts completed lessons only inside the enrolled courses, so X
    # can never exceed Y (it counted every completed lesson anywhere, e.g. after leaving a course: "5 / 4")
    enrolled_lesson_ids = set()
    for enrollment in enrollments:
        if enrollment.course:
            for m in enrollment.course.modules.all():
                enrolled_lesson_ids.update(m.lessons.values_list("id", flat=True))
    total_lessons_enrolled = len(enrolled_lesson_ids)
    completed_lessons = LessonProgress.objects.filter(student=user, status="completed",
                                                      lesson_id__in=enrolled_lesson_ids).count()
    # progress through the enrolled courses' lessons; None (shown as "—") when there is nothing to measure
    progress_pct = round(completed_lessons / total_lessons_enrolled * 100) if total_lessons_enrolled else None

    attempts = QuizAttempt.objects.filter(student=user, completed_at__isnull=False)
    quiz_scores = [a.score for a in attempts if a.score is not None]
    avg_quiz_score = round(sum(quiz_scores) / len(quiz_scores), 1) if quiz_scores else None

    recommended_course = None
    next_lesson = None   # claude code changed: 2026-10-26 — kept (was computed then discarded) for "Continue learning"
    for enrollment in enrollments:
        if enrollment.course:
            candidate = _first_incomplete_lesson(user, enrollment.course)
            if candidate is not None:
                recommended_course, next_lesson = enrollment.course, candidate
                break

    from bot.academy.journey import build_journey   # claude code changed: 2026-10-26 — Quant Learning Journey
    journey = build_journey()
    start_course = next((st["first_course"] for st in journey if st["first_course"]), None)   # first available, journey order
    skill_levels = ([("Trading experience", profile.trading_experience_level), ("Programming", profile.programming_level),
                     ("Quantitative", profile.quant_level), ("Research", profile.research_level)] if profile else [])

    return {
        "profile": profile,
        "enrollments": enrollments,
        "completed_lessons": completed_lessons,
        "total_lessons_enrolled": total_lessons_enrolled,
        "avg_quiz_score": avg_quiz_score,
        "quiz_attempts_count": attempts.count(),
        "recommended_course": recommended_course,
        "next_lesson": next_lesson,                       # claude code changed: 2026-10-26
        "progress_pct": progress_pct,                     # claude code changed: 2026-10-26
        "all_enrolled_done": bool(total_lessons_enrolled) and completed_lessons >= total_lessons_enrolled,
        "journey": journey,                               # claude code changed: 2026-10-26
        "start_course": start_course,                     # claude code changed: 2026-10-26
        "skill_levels": skill_levels,                     # claude code changed: 2026-10-26
        "onboarding_needed": profile is None or profile.onboarding_completed_at is None,
    }


def _first_incomplete_lesson(user, course):
    completed_lesson_ids = set(
        LessonProgress.objects.filter(student=user, status="completed").values_list("lesson_id", flat=True)
    )
    for module in course.modules.all():
        for lesson in module.lessons.all():
            if lesson.id not in completed_lesson_ids:
                return lesson
    return None
