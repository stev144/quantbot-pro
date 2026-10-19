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


# claude code changed: 2026-10-07 Deep Health — its own six-state vocabulary (PASS/PARTIAL/FAIL/BLOCKED/UNKNOWN/N/A),
# kept separate from HEALTH_CLASS_MAP so BLOCKED/UNKNOWN never borrow a "fine" colour.
DEEP_HEALTH_CLASS = {"PASS": "pos", "PARTIAL": "warn", "FAIL": "neg", "BLOCKED": "neu", "UNKNOWN": "warn", "N/A": "dim"}   # claude code changed: 2026-10-07 Deep Health
DEEP_HEALTH_DOT = {"PASS": "ok", "PARTIAL": "warn", "FAIL": "err", "BLOCKED": "warn", "UNKNOWN": "warn", "N/A": "off"}   # claude code changed: 2026-10-07 Deep Health


@register.filter
def deep_health_class(status):   # claude code changed: 2026-10-07 Deep Health
    return DEEP_HEALTH_CLASS.get(str(status).upper(), "neg")   # an unrecognised status is never shown as fine


@register.filter
def deep_health_dot(status):   # claude code changed: 2026-10-07 Deep Health
    return DEEP_HEALTH_DOT.get(str(status).upper(), "err")


@register.filter
def dict_get(d, key):   # claude code changed: 2026-10-07 Deep Health — gate lookup in templates
    return d.get(key) if hasattr(d, "get") else None
