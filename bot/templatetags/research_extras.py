# ============================================================
# bot/templatetags/research_extras.py
# claude code changed: new file — template filter mapping the Research
# Lab's six-state verdict vocabulary (see bot/views/research_lab_data.py
# module docstring) onto the badge CSS classes already defined in
# static/css/terminal.css, so every panel renders verdicts consistently
# instead of repeating the same if/elif chain in every template block.
# ============================================================

from django import template

register = template.Library()

VERDICT_CLASS_MAP = {
    "STATISTICALLY SUPPORTED": "pos",
    "PROMISING": "neu",
    "TESTED": "dim",
    "UNSTABLE": "warn",
    "REQUIRES FURTHER RESEARCH": "warn",
    "REJECTED": "neg",
}


@register.filter
def verdict_class(verdict):
    return VERDICT_CLASS_MAP.get(str(verdict).upper(), "dim")
