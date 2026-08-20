# claude code changed: new file — Academy dashboard view. Requires login
# (unlike the rest of this project's public dashboard) because there is
# no meaningful per-student dashboard for an anonymous visitor.

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from bot.academy.views.dashboard_data import get_student_dashboard


@login_required
def academy_dashboard(request):
    context = get_student_dashboard(request.user)
    return render(request, "academy/dashboard.html", context)
