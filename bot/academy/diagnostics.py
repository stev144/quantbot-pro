# ============================================================
# bot/academy/diagnostics.py
# claude code changed: new file — the onboarding diagnostic (section 5)
# and its scoring logic (section 6). Deterministic, rules-based scoring
# on purpose, not ML — this is the explicit seam the architecture report
# flagged for a future AI tutor to eventually replace without touching
# DiagnosticResponse/StudentProfile's shape.
# ============================================================

from typing import Dict, List

# ─────────────────────────────────────────────────────────────
# QUESTIONS
# Each question_key must be globally unique (DiagnosticResponse enforces
# uniqueness per (student, question_key)).
# ─────────────────────────────────────────────────────────────

DIAGNOSTIC_QUESTIONS: Dict[str, List[dict]] = {
    "trading_experience": [
        {"key": "has_traded", "prompt": "Have you traded before?", "type": "boolean"},
        {"key": "markets_traded", "prompt": "Which markets? (select all that apply)", "type": "multi_choice",
         "choices": ["crypto", "forex", "equities", "none"]},
        {"key": "years_experience", "prompt": "How long have you been trading?", "type": "choice",
         "choices": ["none", "under_1_year", "1_to_3_years", "3_plus_years"]},
        {"key": "manual_or_automated", "prompt": "Manual or automated trading?", "type": "choice",
         "choices": ["manual", "automated", "both", "neither"]},
        {"key": "used_leverage", "prompt": "Have you used leverage?", "type": "boolean"},
    ],
    "programming": [
        {"key": "knows_python", "prompt": "Do you know Python?", "type": "boolean"},
        {"key": "used_pandas", "prompt": "Have you used Pandas?", "type": "boolean"},
        {"key": "used_numpy", "prompt": "Have you used NumPy?", "type": "boolean"},
        {"key": "used_apis", "prompt": "Have you worked with APIs?", "type": "boolean"},
        {"key": "built_trading_bot", "prompt": "Have you built a trading bot?", "type": "boolean"},
    ],
    # claude code changed: these are real knowledge-check questions
    # (right/wrong answers), not self-assessment — section 5 explicitly
    # asks to test whether the student actually understands these
    # concepts, not just whether they claim to.
    "quantitative": [
        {"key": "quant_pvalue", "prompt": "A p-value of 0.03 means:", "type": "choice",
         "choices": [
             "There's a 3% chance the effect is real",
             "There's a 3% chance of seeing data this extreme if there's truly no effect",
             "The effect explains 3% of the variance",
             "The result is 97% likely to replicate",
         ], "correct_index": 1},
        {"key": "quant_multiple_testing", "prompt": (
            "You test 20 independent hypotheses at 5% significance, and none of them "
            "have a true effect. About how many will still appear 'significant' by chance?"
         ), "type": "choice", "choices": ["0", "~1", "~5", "~10"], "correct_index": 1},
        {"key": "quant_autocorrelation", "prompt": "Autocorrelation in time-series data means:", "type": "choice",
         "choices": [
             "The data has no missing values",
             "A value is correlated with its own past values",
             "Two different variables move together",
             "The data follows a normal distribution",
         ], "correct_index": 1},
        {"key": "quant_confidence_interval", "prompt": "A 95% confidence interval means:", "type": "choice",
         "choices": [
             "There's a 95% chance the true value is in this specific interval",
             "95% of the data falls in this interval",
             "The method produces intervals containing the true value 95% of the time, over repeated sampling",
             "The result is correct 95% of the time",
         ], "correct_index": 2},
    ],
    "research": [
        {"key": "research_lookahead", "prompt": "Look-ahead bias means:", "type": "choice",
         "choices": [
             "A backtest accidentally uses information not yet available at that point in time",
             "A strategy loses money in live trading",
             "A feature has low predictive power",
             "A dataset is too small",
         ], "correct_index": 0},
        {"key": "research_survivorship", "prompt": "Survivorship bias in a research universe means:", "type": "choice",
         "choices": [
             "The data has too many missing candles",
             "The universe only includes assets that still exist today, missing delisted/failed ones",
             "The strategy only works in trending markets",
             "The backtest period is too short",
         ], "correct_index": 1},
        {"key": "research_walkforward", "prompt": "Walk-forward testing is primarily used to check whether:", "type": "choice",
         "choices": [
             "A strategy would have kept working if re-validated periodically over time, not just once",
             "A strategy is profitable in a single backtest",
             "An exchange's API is reliable",
             "A dataset has outliers",
         ], "correct_index": 0},
        {"key": "research_ic", "prompt": "The Information Coefficient measures:", "type": "choice",
         "choices": [
             "How much money a strategy made",
             "How well a feature predicts future returns",
             "How fast a strategy executes orders",
             "How volatile an asset is",
         ], "correct_index": 1},
    ],
    "goals": [
        {"key": "primary_goal", "prompt": "Why are you learning quantitative trading?", "type": "choice",
         "choices": [
             "research_strategies", "build_trading_bots", "become_quant_developer",
             "become_quant_researcher", "trade_own_capital", "build_financial_software",
         ]},
    ],
}


# ─────────────────────────────────────────────────────────────
# FLATTENED QUESTION ORDER
# claude code changed: new — one-question-at-a-time onboarding UI (Academy
# UX/UI transformation, Phase B) needs a single stable ordering across all
# categories instead of DIAGNOSTIC_QUESTIONS' category-grouped dict. Each
# entry carries its own "category" so downstream saving/scoring keeps
# working exactly as before — this is a display-order view over the same
# data, not a second copy of it.
# ─────────────────────────────────────────────────────────────

FLAT_DIAGNOSTIC_QUESTIONS: List[dict] = [
    {**question, "category": category}
    for category, questions in DIAGNOSTIC_QUESTIONS.items()
    for question in questions
]


# ─────────────────────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────────────────────

def _bucket_from_fraction(fraction: float) -> str:
    if fraction >= 0.75:
        return "advanced"
    if fraction >= 0.4:
        return "intermediate"
    return "beginner"


def score_trading_experience(responses: Dict[str, dict]) -> str:
    """Self-reported — rules-based bucketing, not a knowledge check."""
    has_traded = responses.get("has_traded", {}).get("value")
    years = responses.get("years_experience", {}).get("value")
    automated = responses.get("manual_or_automated", {}).get("value")

    if not has_traded or years in (None, "none"):
        return "beginner"
    if years == "3_plus_years" and automated in ("automated", "both"):
        return "advanced"
    return "intermediate"


def score_programming(responses: Dict[str, dict]) -> str:
    built_bot = bool(responses.get("built_trading_bot", {}).get("value"))
    used_apis = bool(responses.get("used_apis", {}).get("value"))
    used_pandas = bool(responses.get("used_pandas", {}).get("value"))
    used_numpy = bool(responses.get("used_numpy", {}).get("value"))
    knows_python = bool(responses.get("knows_python", {}).get("value"))

    if built_bot and used_apis and used_pandas:
        return "advanced"
    if knows_python and (used_pandas or used_numpy):
        return "intermediate"
    return "beginner"


def _score_knowledge_check(category: str, responses: Dict[str, dict]) -> str:
    """Real right/wrong scoring against DIAGNOSTIC_QUESTIONS' correct_index."""
    questions = DIAGNOSTIC_QUESTIONS[category]
    scored = [q for q in questions if "correct_index" in q]
    if not scored:
        return "beginner"

    correct = 0
    for q in scored:
        answer = responses.get(q["key"], {})
        if answer.get("selected_index") == q["correct_index"]:
            correct += 1

    return _bucket_from_fraction(correct / len(scored))


def score_quantitative(responses: Dict[str, dict]) -> str:
    return _score_knowledge_check("quantitative", responses)


def score_research(responses: Dict[str, dict]) -> str:
    return _score_knowledge_check("research", responses)


def build_profile_scores(responses_by_category: Dict[str, Dict[str, dict]]) -> Dict[str, str]:
    """
    responses_by_category: {"trading_experience": {question_key: {"value"/"selected_index": ...}, ...}, ...}
    Returns the four StudentProfile level fields, ready to save.
    """
    return {
        "trading_experience_level": score_trading_experience(responses_by_category.get("trading_experience", {})),
        "programming_level": score_programming(responses_by_category.get("programming", {})),
        "quant_level": score_quantitative(responses_by_category.get("quantitative", {})),
        "research_level": score_research(responses_by_category.get("research", {})),
    }


def recommend_starting_path_slug(profile_scores: Dict[str, str]) -> str:
    """
    Simple, explainable recommendation — not ML. Quant-beginner takes
    priority over programming-beginner (foundational market/statistics
    concepts are needed regardless of coding skill); a student who
    already clears the quant bar but not the programming one starts on
    Python instead of repeating quant fundamentals they've already
    demonstrated; a student who clears both skips straight to research.
    """
    if profile_scores.get("quant_level") == "beginner":
        return "quant-foundations"
    if profile_scores.get("programming_level") == "beginner":
        return "python-for-quant-trading"
    return "quantitative-research"
