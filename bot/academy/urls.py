# claude code changed: new file — Academy URL routes, included from
# bot/urls.py via one path('academy/', include(...)) line rather than
# growing that file's flat route list, per the approved architecture
# report.

from django.urls import path

from bot.academy.views.dashboard import academy_dashboard
from bot.academy.views.onboarding import onboarding, onboarding_step, onboarding_result
from bot.academy.views.paths import path_list, path_detail
from bot.academy.views.courses import course_detail, enroll
from bot.academy.views.lessons import lesson_detail, complete_lesson
from bot.academy.views.quiz import quiz_detail

urlpatterns = [
    path("", academy_dashboard, name="academy_dashboard"),
    path("onboarding/", onboarding, name="academy_onboarding"),
    path("onboarding/result/", onboarding_result, name="academy_onboarding_result"),
    path("onboarding/<int:step>/", onboarding_step, name="academy_onboarding_step"),
    path("paths/", path_list, name="academy_path_list"),
    path("paths/<slug:slug>/", path_detail, name="academy_path_detail"),
    path("courses/<slug:slug>/", course_detail, name="academy_course_detail"),
    path("courses/<slug:slug>/enroll/", enroll, name="academy_enroll"),
    path("courses/<slug:course_slug>/lessons/<slug:lesson_slug>/", lesson_detail, name="academy_lesson_detail"),
    path("courses/<slug:course_slug>/lessons/<slug:lesson_slug>/complete/", complete_lesson, name="academy_complete_lesson"),
    path("quiz/<int:quiz_id>/", quiz_detail, name="academy_quiz_detail"),
]
