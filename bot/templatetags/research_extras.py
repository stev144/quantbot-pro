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
    # claude code changed: new — bot/research/validated_feature_registry.py's
    # own 5-state taxonomy (SUPPORTED/CONDITIONAL/WEAK/REJECTED/UNTESTED) is
    # distinct from the markdown research log's 6-state vocabulary above.
    # REJECTED already maps identically; the rest didn't exist as keys here
    # and were silently falling through to "dim" (gray) regardless of verdict.
    "SUPPORTED": "pos",
    "CONDITIONAL": "neu",
    # claude code changed: owner decision D-7 — verdict ladder (OBSERVATION is unconfirmed → neutral)
    "OBSERVATION": "neu",
    "STATISTICAL": "pos",
    "ECONOMIC": "pos",
    "TRADEABLE": "pos",
    "CANONICAL": "pos",
    "WEAK": "warn",
    "UNTESTED": "dim",
}


@register.filter
def verdict_class(verdict):
    return VERDICT_CLASS_MAP.get(str(verdict).upper(), "dim")


@register.filter
def as_pct(value):   # claude code changed: 2026-10-26 — fractions (0.02645) shown as percent (2.645); None/'' stay ''
    """Multiply a stored fraction by 100 for display as a percentage. Non-numbers return ''."""
    try:
        return float(value) * 100
    except (TypeError, ValueError):
        return ""


@register.filter
def pvalue(value):   # claude code changed: 2026-10-26 — "0.0000267…" shown as "< 0.0001"; 4 decimals otherwise
    """Display a p-value: '< 0.0001' below that, else 4 decimals. None/'' → 'not evaluated'."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "not evaluated"
    return "< 0.0001" if v < 0.0001 else f"{v:.4f}"

