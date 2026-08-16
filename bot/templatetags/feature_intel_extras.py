# ============================================================
# bot/templatetags/feature_intel_extras.py
# claude code changed: new file — template filters for the Feature
# Intelligence module's own seven-state research-status vocabulary
# (UNTESTED/UNDER_RESEARCH/PROMISING/STABLE/UNSTABLE/REJECTED/
# REQUIRES_VALIDATION), kept separate from research_extras.py's
# six-state pipeline-stage vocabulary since the two are not the same
# taxonomy and shouldn't be conflated.
# ============================================================

from django import template

register = template.Library()

STATUS_CLASS_MAP = {
    "STABLE": "pos",
    "PROMISING": "neu",
    "UNDER_RESEARCH": "dim",
    "REQUIRES_VALIDATION": "warn",
    "UNSTABLE": "warn",
    "REJECTED": "neg",
    "UNTESTED": "dim",
}


@register.filter
def status_class(status):
    return STATUS_CLASS_MAP.get(str(status).upper(), "dim")


@register.filter
def corr_bg(value):
    """
    Maps a correlation coefficient in [-1, 1] to a background color
    using the same --positive/--negative CSS variables as the rest of
    the terminal theme, so the heatmap stays theme-aware (light/dark)
    without hardcoding colors.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    alpha = min(abs(v), 1.0) * 0.55
    var = "--positive" if v >= 0 else "--negative"
    return f"background-color: color-mix(in srgb, var({var}) {alpha * 100:.0f}%, transparent);"


@register.filter
def get_item(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None
