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


def get_lesson_outline(user, lesson):   # claude code changed: 2026-10-26 — lesson page outline + position + previous
    """Modules and lessons of the lesson's course with each lesson's status ("completed" / "current" / "todo"), the
    previous lesson, and this lesson's 1-based position. Completion comes only from LessonProgress (never from viewing)."""
    course = lesson.module.course
    done = set(LessonProgress.objects.filter(student=user, status="completed",
                                             lesson__module__course=course).values_list("lesson_id", flat=True))
    modules, flat = [], []
    for module in course.modules.all():
        items = []
        for l in module.lessons.all():
            status = "current" if l.id == lesson.id else "completed" if l.id in done else "todo"
            items.append({"lesson": l, "status": status, "done": l.id in done})
            flat.append(l)
        modules.append({"module": module, "lessons": items})
    idx = [l.id for l in flat].index(lesson.id)
    return {"modules": modules, "position": idx + 1, "total": len(flat),
            "previous": flat[idx - 1] if idx > 0 else None, "is_completed": lesson.id in done,
            "n_done": len(done)}

