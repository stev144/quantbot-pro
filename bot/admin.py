from django.contrib import admin

# Register your models here.

# claude code changed: new — bot/admin.py is the module
# django.contrib.admin's autodiscover() is guaranteed to import for the
# 'bot' app (that's what "Register your models here" was always for).
# Importing bot.academy.admin here is what actually makes Django's app
# registry aware the Academy models exist — see that module's own comment
# for why this matters for manage.py makemigrations, not just the admin UI.
from bot.academy import admin as academy_admin  # noqa: F401
from bot.research_lab import admin as research_lab_admin  # noqa: F401  # claude code changed: new — same discovery mechanism, Research Lab models
