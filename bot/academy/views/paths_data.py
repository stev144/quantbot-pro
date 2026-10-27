# claude code changed: new file — learning path catalog data.

from bot.academy.models import LearningPath


def get_all_paths():
    # claude code changed: 2026-10-26 — a path row with an empty slug (present in the database) made {% url %} raise and
    # the whole page return 500; such rows are skipped. Each path carries its real available / planned course counts.
    from django.db.models import Count, Q
    return (LearningPath.objects.exclude(slug="").prefetch_related("courses")
            .annotate(n_available=Count("courses", filter=Q(courses__is_published=True)),
                      n_planned=Count("courses", filter=Q(courses__is_published=False))))


def get_path_detail(slug):
    return LearningPath.objects.prefetch_related("courses").filter(slug=slug).first()
