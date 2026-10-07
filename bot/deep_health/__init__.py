# claude code changed: new package — 2026-10-07 Deep Health (DEEP_HEALTH_PLAN_2026-10-07.md). Five-gate, known-answer
# health checks run by `python manage.py deep_health_check --level L0|L1|L2` and shown on /system/health/.
# Importing the level modules registers their checks.
from bot.deep_health.core import (AREAS, BLOCKED, FAIL, GATES, NA, PARTIAL, PASS, STATUSES, UNKNOWN,  # noqa: F401
                                  load_latest_report, run_health, save_report)
