# claude code changed: new file — lesson view + completion.

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render, redirect

from bot.academy.views.lessons_data import (
    get_lesson, student_can_access_lesson, get_rendered_body,
    mark_lesson_complete, get_next_lesson,
)


@login_required
def lesson_detail(request, course_slug, lesson_slug):
    lesson = get_lesson(course_slug, lesson_slug)
    if lesson is None:
        raise Http404("Lesson not found")

    if not student_can_access_lesson(request.user, lesson):
        messages.error(request, "Enroll in this course first to access its lessons.")
        return redirect("academy_course_detail", slug=course_slug)

    context = {
        "lesson": lesson,
        "course": lesson.module.course,
        "rendered_body": get_rendered_body(lesson),
        "next_lesson": get_next_lesson(lesson),
    }
    return render(request, "academy/lesson.html", context)


@login_required
def complete_lesson(request, course_slug, lesson_slug):
    if request.method != "POST":
        return redirect("academy_lesson_detail", course_slug=course_slug, lesson_slug=lesson_slug)

    lesson = get_lesson(course_slug, lesson_slug)
    if lesson is None:
        raise Http404("Lesson not found")
    if not student_can_access_lesson(request.user, lesson):
        raise Http404("Lesson not found")

    mark_lesson_complete(request.user, lesson)
    next_lesson = get_next_lesson(lesson)
    if next_lesson:
        return redirect("academy_lesson_detail", course_slug=course_slug, lesson_slug=next_lesson.slug)
    messages.success(request, "Course lessons complete — try the quiz next.")
    return redirect("academy_course_detail", slug=course_slug)
