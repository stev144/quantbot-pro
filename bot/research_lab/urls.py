# claude code changed: new file — Research Lab URL routes, included from
# bot/urls.py via one path('research-lab/', include(...)) line, same
# pattern as bot/academy/urls.py.

from django.urls import path

from bot.research_lab.views.dashboard import research_lab_dashboard
from bot.research_lab.views.formalize import formalize
from bot.research_lab.views.history import history
from bot.research_lab.views.plan import plan
from bot.research_lab.views.report import report
from bot.research_lab.views.results import results

urlpatterns = [
    path("", research_lab_dashboard, name="research_lab_dashboard"),
    path("history/", history, name="research_lab_history"),
    path("<uuid:experiment_id>/formalize/", formalize, name="research_lab_formalize"),
    path("<uuid:experiment_id>/plan/", plan, name="research_lab_plan"),
    path("<uuid:experiment_id>/results/", results, name="research_lab_results"),
    path("<uuid:experiment_id>/report/", report, name="research_lab_report"),
]
