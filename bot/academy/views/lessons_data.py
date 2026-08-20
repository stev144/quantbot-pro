# claude code changed: new file — lesson access + progress data layer.

from django.utils import timezone

from bot.academy.content_render import render_lesson_body
from bot.academy.models import Lesson, LessonProgress, Enrollment


def get_lesson(course_slug, lesson_slug):
    return (
        Lesson.objects.select_related("module__course")
        .filter(module__course__slug=course_slug, slug=lesson_slug)
        .first()
    )


def student_can_access_lesson(user, lesson):
    """Requires an active enrollment in the lesson's course — no student
    reads gated lesson content without enrolling first."""
    if not user.is_authenticated:
        return False
    return Enrollment.objects.filter(
        student=user, course=lesson.module.course, status="active"
    ).exists()


def get_rendered_body(lesson):
    return render_lesson_body(lesson.body)


def mark_lesson_complete(user, lesson):
    progress, _ = LessonProgress.objects.update_or_create(
        student=user, lesson=lesson,
        defaults={"status": "completed", "completed_at": timezone.now()},
    )
    return progress


def get_next_lesson(lesson):
    course = lesson.module.course
    all_lessons = []
    for module in course.modules.all():
        all_lessons.extend(module.lessons.all())
    try:
        idx = [l.id for l in all_lessons].index(lesson.id)
    except ValueError:
        return None
    if idx + 1 < len(all_lessons):
        return all_lessons[idx + 1]
    return None
