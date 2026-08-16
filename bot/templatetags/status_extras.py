# ============================================================
# bot/templatetags/status_extras.py
# claude code changed: new file — small third status vocabulary,
# deliberately kept separate from research_extras.py's six-state verdict
# vocabulary and feature_intel_extras.py's seven-state research-status
# vocabulary. This one covers "is this real-time signal/system healthy"
# data (System Health checks, Market Intelligence regime alignment,
# Backtesting walk-forward verdicts) — none of that is a research
# verdict, so it doesn't belong in either existing vocabulary.
# ============================================================

from django import template

register = template.Library()

HEALTH_CLASS_MAP = {
    "OK": "pos",
    "PASS": "pos",
    "ALIGNED": "pos",
    "VALIDATED": "pos",
    "ACCEPTABLE": "pos",
    "WARN": "warn",
    "WARNING": "warn",
    "CAUTION": "warn",
    "DEGRADED": "warn",
    "NEUTRAL": "dim",
    "INSUFFICIENT": "dim",
    "ERR": "neg",
    "ERROR": "neg",
    "FAIL": "neg",
    "FAILED": "neg",
}


@register.filter
def health_class(status):
    return HEALTH_CLASS_MAP.get(str(status).upper(), "dim")
