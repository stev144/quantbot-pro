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

    completed_lessons = LessonProgress.objects.filter(student=user, status="completed").count()
    total_lessons_enrolled = 0
    for enrollment in enrollments:
        if enrollment.course:
            total_lessons_enrolled += sum(
                m.lessons.count() for m in enrollment.course.modules.all()
            )

    attempts = QuizAttempt.objects.filter(student=user, completed_at__isnull=False)
    quiz_scores = [a.score for a in attempts if a.score is not None]
    avg_quiz_score = round(sum(quiz_scores) / len(quiz_scores), 1) if quiz_scores else None

    recommended_course = None
    for enrollment in enrollments:
        if enrollment.course:
            next_lesson = _first_incomplete_lesson(user, enrollment.course)
            if next_lesson is not None:
                recommended_course = enrollment.course
                break

    return {
        "profile": profile,
        "enrollments": enrollments,
        "completed_lessons": completed_lessons,
        "total_lessons_enrolled": total_lessons_enrolled,
        "avg_quiz_score": avg_quiz_score,
        "quiz_attempts_count": attempts.count(),
        "recommended_course": recommended_course,
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
