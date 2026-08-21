# claude code changed: new file — course detail + enrollment data layer.

from bot.academy.models import Course, Enrollment


def get_course_detail(slug):
    return Course.objects.select_related("path").prefetch_related(
        "modules__lessons", "prerequisites"
    ).filter(slug=slug).first()


def get_unmet_prerequisites(user, course):
    """
    Real prerequisite enforcement (section 6: "Do not allow students to
    skip foundational prerequisites blindly"). A prerequisite is "met" if
    the student has a completed Enrollment for it.
    """
    if not user.is_authenticated:
        return list(course.prerequisites.all())

    completed_course_ids = set(
        Enrollment.objects.filter(student=user, status="completed", course__isnull=False)
        .values_list("course_id", flat=True)
    )
    return [p for p in course.prerequisites.all() if p.id not in completed_course_ids]


def get_enrollment(user, course):
    if not user.is_authenticated:
        return None
    return Enrollment.objects.filter(student=user, course=course, status="active").first()


def enroll_student(user, course):
    """
    Creates a free enrollment. Fails closed on unmet prerequisites —
    returns None rather than enrolling, same "missing/failed != approved"
    principle the research-gating side of this platform uses.
    """
    if get_unmet_prerequisites(user, course):
        return None
    enrollment, _ = Enrollment.objects.get_or_create(
        student=user, course=course,
        defaults={"status": "active", "access_source": "free"},
    )
    return enrollment
