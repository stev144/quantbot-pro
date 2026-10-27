# claude code changed: 2026-10-26 — new file. The Quant Learning Journey: six stages shown on the Academy dashboard,
# each mapped onto the existing learning paths. Configuration only — counts of available (published) and planned
# courses are read from the database, never written here, and a stage with no path yet is shown as "planned".
from typing import Dict, List

JOURNEY = [
    {"key": "foundations", "title": "Foundations", "level": "Beginner",
     "summary": "Markets, trading, returns and compounding, risk and drawdown — starting from zero.",
     "paths": ["quant-foundations"]},
    {"key": "mathematics", "title": "Mathematics for Quant Finance", "level": "Beginner",
     "summary": "Percentages, logarithms, mean and standard deviation, Z-scores, correlation, regression.",
     "paths": []},
    {"key": "statistics", "title": "Statistics & Research", "level": "Intermediate",
     "summary": "Hypotheses, significance, multiple testing, leakage, in-sample vs out-of-sample, walk-forward.",
     "paths": ["quantitative-research", "backtesting-and-robustness"]},
    {"key": "engineering", "title": "Python & Quant Engineering", "level": "Intermediate",
     "summary": "Python, NumPy, pandas, financial data, backtesting architecture, testing and reproducibility.",
     "paths": ["python-for-quant-trading", "production-quant-systems"]},
    {"key": "strategies", "title": "Quantitative Strategies", "level": "Advanced",
     "summary": "Regimes, trend vs mean reversion, cross-sectional ranking, cointegration, costs and risk.",
     "paths": ["strategy-engineering", "execution-engineering", "risk-engineering", "multi-asset-quant-research"]},
    {"key": "ai", "title": "AI & Machine Learning", "level": "Advanced",
     "summary": "Features and labels, training vs validation vs testing, overfitting, drift, responsible use.",
     "paths": []},
]


def build_journey() -> List[Dict]:
    """Each stage with its real published / planned course counts and the first published course to start with."""
    from bot.academy.models import Course
    out = []
    for stage in JOURNEY:
        courses = list(Course.objects.filter(path__slug__in=stage["paths"]).select_related("path")
                       .order_by("path__order", "order"))
        published = [c for c in courses if c.is_published]
        out.append({**stage, "n_available": len(published), "n_planned": len(courses) - len(published),
                    "first_course": published[0] if published else None})
    return out


# claude code changed: 2026-10-26 — which Research Explainer concept each authored lesson is about (the lesson page's
# "Ask the tutor" box). Course slug → concept for every lesson of that course, overridden per lesson where needed.
COURSE_CONCEPTS = {"information-coefficient": "information_coefficient", "cointegration": "cointegration"}
LESSON_CONCEPTS = {
    ("cointegration", "two-prices-and-correlation"): "correlation",
    ("cointegration", "spreads"): "spread",
    ("cointegration", "stationarity"): "stationarity",
    ("cointegration", "cointegration-engle-granger"): "cointegration",
}


def concept_for_lesson(course_slug: str, lesson_slug: str):
    return LESSON_CONCEPTS.get((course_slug, lesson_slug)) or COURSE_CONCEPTS.get(course_slug)

