# claude code changed: new file — learning path catalog data.

from bot.academy.models import LearningPath


def get_all_paths():
    return LearningPath.objects.prefetch_related("courses").all()


def get_path_detail(slug):
    return LearningPath.objects.prefetch_related("courses").filter(slug=slug).first()
