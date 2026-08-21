# claude code changed: new file — course detail + enroll views.

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render, redirect

from bot.academy.views.courses_data import (
    get_course_detail, get_unmet_prerequisites, get_enrollment, enroll_student,
)


def course_detail(request, slug):
    course = get_course_detail(slug)
    if course is None:
        raise Http404("Course not found")

    context = {
        "course": course,
        "unmet_prerequisites": get_unmet_prerequisites(request.user, course),
        "enrollment": get_enrollment(request.user, course),
    }
    return render(request, "academy/course_detail.html", context)


@login_required
def enroll(request, slug):
    if request.method != "POST":
        return redirect("academy_course_detail", slug=slug)

    course = get_course_detail(slug)
    if course is None:
        raise Http404("Course not found")

    enrollment = enroll_student(request.user, course)
    if enrollment is None:
        messages.error(
            request,
            f"Complete the prerequisite course(s) first: "
            f"{', '.join(c.title for c in get_unmet_prerequisites(request.user, course))}.",
        )
    else:
        messages.success(request, f"Enrolled in {course.title}.")

    return redirect("academy_course_detail", slug=slug)
