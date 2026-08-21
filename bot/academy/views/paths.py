# claude code changed: new file — learning path catalog views. Public
# (no @login_required) — browsing the curriculum shouldn't require an
# account, matching this project's existing "dashboard is public" stance;
# only enrollment/progress-tracking actions require being signed in.

from django.http import Http404
from django.shortcuts import render

from bot.academy.views.paths_data import get_all_paths, get_path_detail


def path_list(request):
    return render(request, "academy/path_list.html", {"paths": get_all_paths()})


def path_detail(request, slug):
    path = get_path_detail(slug)
    if path is None:
        raise Http404("Learning path not found")
    return render(request, "academy/path_detail.html", {"path": path})
