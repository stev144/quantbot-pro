# ============================================================
# bot/research_lab/interpreter.py
# Research Lab — hypothesis formalization (section 6) and evidence
# interpretation (section 15).
#
# claude code changed: new file. HONEST LIMITATION, documented here and in
# the final engineering report, not hidden: this is a deterministic,
# keyword-based SUGGESTION engine, not a real AI call. It is intentionally
# NOT labeled "AI Formalization" anywhere in its own output — every field
# it can't confidently resolve is marked ambiguous per section 6's own
# rule, and the Research Lab UI (views/hypothesis.py) requires the
# student to review and confirm every suggested field in a form before a
# spec can validate — the human-in-the-loop confirmation IS this MVP's
# safe way to handle "ambiguous," not a workaround for lacking real NLU.
#
# Swapping this for a real Anthropic API call later is a self-contained
# change: interpret_hypothesis()'s signature (str in, ResearchSpec out,
# ambiguous fields marked) does not need to change for the rest of the
# Research Lab to keep working.
#
# claude code changed: Conditional Hypothesis Integrity fix. GOVERNING
# STATISTICAL PRINCIPLE: a research system must test the hypothesis that
# was specified, not a nearby hypothesis that happens to be easier to
# compute. "RSI < 30 -> forward return" (conditional/event) is NOT
# equivalent to "RSI -> forward return" (continuous feature). This module
# now attempts to detect an explicit numeric-threshold condition
# ("RSI < 30", "RSI below 30", "RSI falls below 30") and, when found,
# marks hypothesis_type="conditional" and populates spec.conditions
# instead of silently falling through to the feature path. A RELATIVE
# condition ("volume > average") is deliberately left unparsed and
# ambiguous — this placeholder has no reliable way to turn "average" into
# a fixed numeric threshold without inventing one, which section 6
# explicitly forbids.
# ============================================================

from __future__ import annotations

import re
from typing import Optional

from bot.research_lab.spec import ResearchSpec, SUPPORTED_ASSETS, DERIVABLE_FROM_OHLCV

INTERPRETER_NAME = "rule_based_placeholder_v1"  # claude code changed: never claims to be "AI" — surfaced in the plan screen so this is never mistaken for a real model call

# claude code changed: new — common names/tickers mapped to this
# platform's real symbol universe. Deliberately small and literal (no
# fuzzy matching) — a wrong guess here is worse than an honest ambiguous
# flag.
ASSET_ALIASES = {
    "bitcoin": "BTC/USDT", "btc": "BTC/USDT",
    "ethereum": "ETH/USDT", "eth": "ETH/USDT",
    "solana": "SOL/USDT", "sol": "SOL/USDT",
    "avalanche": "AVAX/USDT", "avax": "AVAX/USDT",
    "cardano": "ADA/USDT", "ada": "ADA/USDT",
    "cosmos": "ATOM/USDT", "atom": "ATOM/USDT",
    "ripple": "XRP/USDT", "xrp": "XRP/USDT",
    "dogecoin": "DOGE/USDT", "doge": "DOGE/USDT",
    "chainlink": "LINK/USDT", "link": "LINK/USDT",
}

_RISE_WORDS = ("rise", "rises", "rising", "increase", "increases", "rally", "pump", "gain", "gains", "climb", "higher")
_FALL_WORDS = ("fall", "falls", "falling", "decrease", "decreases", "drop", "drops", "decline", "declines", "dump", "crash", "lower")

# claude code changed: was `\s*` between the number and the unit word —
# didn't match hyphenated phrasing like "24-hour" (a hyphen is not `\s`).
# Confirmed against this exact failure using the brief's own required
# Test 1 example ("...1-hour RSI...24-hour forward return...").
_HOUR_RE = re.compile(r"(\d+)[\s-]*(?:hour|hours|hr|hrs|h)\b", re.IGNORECASE)
_DAY_RE = re.compile(r"(\d+)[\s-]*(?:day|days|d)\b", re.IGNORECASE)

# claude code changed: new — a hint that a nearby hour/day number is
# describing the HORIZON (the forward-looking label), not the signal's own
# timeframe. "1-hour RSI" (timeframe) and "24-hour forward return"
# (horizon) commonly both appear in the same sentence — the one nearest a
# horizon-ish word is preferred; see _guess_horizon_candles().
_HORIZON_CONTEXT_RE = re.compile(r"(forward return|subsequent|within|later|horizon)", re.IGNORECASE)

# claude code changed: new — a small, literal feature-name alias map for
# condition parsing. Deliberately narrow (matches the brief's own required
# test cases: RSI, volume) rather than attempting general NLU over every
# DERIVABLE_FROM_OHLCV name — most of those (bb_width, atr_indicator)
# don't appear as natural words in free text anyway.
_CONDITION_FEATURE_ALIASES = {
    "rsi": "rsi",
    "volume": "volume",
    "atr": "atr",
    "adx": "adx",
    "macd": "macd",
}
assert set(_CONDITION_FEATURE_ALIASES.values()) <= DERIVABLE_FROM_OHLCV  # claude code changed: fail loudly at import time if this ever drifts from the real feature set

# claude code changed: new — numeric-threshold condition patterns. Each
# entry is (regex, operator), with the threshold as capture group 1.
# Checked in order; the first match wins. "falls below"/"below"/"<" all
# map to "<" ; "rises above"/"above"/">" all map to ">" — per the brief's
# required equivalence list.
#
# claude code changed: fixed a real bug — \b (word boundary) only fires
# between a \w and a \W character. In "RSI < 30", the space before "<" and
# "<" itself are BOTH non-word characters, so \b<  never matches at all
# ("<" was silently unreachable, only the word-phrase alternatives
# "falls below"/"below" ever actually matched). \b is now scoped only to
# the word-phrase alternatives via a nested non-capturing group; the
# symbol alternatives ("<", ">") are checked without it.
_CONDITION_PATTERNS = [
    (re.compile(r"(?:\b(?:falls below|below)|<)\s*(\d+(?:\.\d+)?)"), "<"),
    (re.compile(r"(?:\b(?:rises above|above)|>)\s*(\d+(?:\.\d+)?)"), ">"),
]

# claude code changed: new — relative-threshold phrases this placeholder
# can RECOGNIZE as a condition attempt but cannot reliably resolve to a
# fixed number. Detected specifically so the ambiguity is explicit
# ("we saw you meant a condition, but couldn't parse the threshold") rather
# than the hypothesis silently falling through to the feature path with no
# explanation at all.
_RELATIVE_THRESHOLD_RE = re.compile(r"\b(above|below|>|<)\s*(?:the\s+)?average\b", re.IGNORECASE)


def _guess_asset(text: str) -> Optional[str]:
    lower = text.lower()
    for alias, symbol in ASSET_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lower):
            return symbol
    for symbol in SUPPORTED_ASSETS:  # claude code changed: also catch a directly-typed symbol like "BTC/USDT" or "AVAX"
        base = symbol.split("/")[0]
        if symbol.lower() in lower or re.search(rf"\b{base.lower()}\b", lower):
            return symbol
    return None


def _guess_direction(text: str) -> Optional[str]:
    lower = text.lower()
    if any(re.search(rf"\b{w}\b", lower) for w in _FALL_WORDS):
        return "negative"
    if any(re.search(rf"\b{w}\b", lower) for w in _RISE_WORDS):
        return "positive"
    return None


def _guess_horizon_candles(text: str) -> Optional[int]:
    """
    A hypothesis sentence commonly mentions TWO hour/day numbers: the
    signal's own timeframe ("1-hour RSI") and the actual forward-looking
    HORIZON ("24-hour forward return") — only the second is target.horizon.
    Strategy: collect every hour/day match; prefer whichever one sits
    closest to a horizon-context word (_HORIZON_CONTEXT_RE); if none of
    them do, fall back to the LAST match in the text (horizon mentions
    conventionally follow timeframe mentions in this phrasing, not the
    reverse) rather than the first.
    """
    day_matches = list(_DAY_RE.finditer(text))
    hour_matches = list(_HOUR_RE.finditer(text))

    def _to_candles(match, is_day):
        value = int(match.group(1))
        return value * 24 if is_day else value  # claude code changed: this platform's only interval is 1h candles, so "N days" -> N*24 candles

    all_matches = [(m, True) for m in day_matches] + [(m, False) for m in hour_matches]
    if not all_matches:
        return None

    context_match = _HORIZON_CONTEXT_RE.search(text)
    if context_match:
        # claude code changed: prefer the number/unit match closest (by
        # character distance) to a horizon-context word, not just the
        # first or last number in the sentence.
        closest = min(all_matches, key=lambda pair: abs(pair[0].start() - context_match.start()))
        return _to_candles(*closest)

    last_match, last_is_day = all_matches[-1]  # claude code changed: was "first match" — wrong for "1-hour RSI...24-hour forward return" phrasing, see docstring
    return _to_candles(last_match, last_is_day)


def _guess_condition_feature(text: str) -> Optional[str]:
    lower = text.lower()
    for alias, feature in _CONDITION_FEATURE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lower):
            return feature
    return None


def _guess_condition(text: str):
    """
    Returns (feature, operator, threshold, match_end) if a fully-specified
    numeric condition was found; (feature, None, None, match_end) if a
    feature + a relative ("above/below average") phrase was found but
    couldn't be resolved to a number; or None if no condition-shaped
    language was found at all. `match_end` is the string index right after
    the condition clause — used by suggest_spec() to avoid reading a
    condition word (e.g. "falls" in "RSI falls below 30") as the outcome
    DIRECTION of a separate, later clause in the same sentence.
    """
    feature = _guess_condition_feature(text)
    if feature is None:
        return None

    for pattern, operator in _CONDITION_PATTERNS:
        match = pattern.search(text)
        if match:
            threshold = float(match.group(1))  # claude code changed: group(1), not group(2) — the phrase alternatives are now non-capturing, only the threshold digits are captured
            return (feature, operator, threshold, match.end())

    relative_match = _RELATIVE_THRESHOLD_RE.search(text)
    if relative_match:
        return (feature, None, None, relative_match.end())  # claude code changed: recognized as a condition attempt, but relative — cannot resolve a fixed threshold, must stay ambiguous

    return None


def suggest_spec(hypothesis_text: str) -> ResearchSpec:
    """
    Best-effort, honestly-limited extraction. Every field this function
    cannot confidently resolve is listed in ambiguous_fields — the caller
    (the Formalize view) must have the student confirm or correct each one
    before the spec can pass validate_spec().
    """
    ambiguous = []

    asset = _guess_asset(hypothesis_text)
    if asset is None:
        ambiguous.append("asset")

    # claude code changed: new — Conditional Hypothesis Integrity fix.
    # Detect an explicit condition BEFORE direction/feature detection, so
    # (a) "RSI < 30" never silently becomes a bare "rsi" feature
    # suggestion, and (b) direction detection can skip past the condition
    # clause — otherwise a word like "falls" in "RSI falls below 30"
    # (describing the CONDITION) gets misread as the outcome DIRECTION of
    # a separate, later clause in the same sentence ("...forward return is
    # higher than normal").
    hypothesis_type = "feature"
    features: list = []
    conditions: list = []
    direction_search_text = hypothesis_text

    condition_guess = _guess_condition(hypothesis_text)
    if condition_guess is not None:
        feature, operator, threshold, match_end = condition_guess
        hypothesis_type = "conditional"
        direction_search_text = hypothesis_text[match_end:]  # claude code changed: only look for the OUTCOME direction after the condition clause ends
        if operator is not None and threshold is not None:
            conditions = [{"feature": feature, "operator": operator, "threshold": threshold}]
        else:
            # claude code changed: feature + relative phrase recognized,
            # but no fixed threshold could be resolved — surfaced as
            # ambiguous "conditions" rather than silently dropped.
            conditions = [{"feature": feature, "operator": None, "threshold": None}]
            ambiguous.append("conditions")
    else:
        feature = _guess_condition_feature(hypothesis_text)
        if feature is not None:
            features = [feature]  # claude code changed: a bare feature mention with no comparison language -> continuous feature hypothesis, unchanged existing behavior

    direction = _guess_direction(direction_search_text)
    if direction is None:
        ambiguous.append("direction")

    horizon = _guess_horizon_candles(hypothesis_text)
    target = {"type": "forward_return", "horizon": horizon} if horizon else {"type": "forward_return"}
    if horizon is None:
        ambiguous.append("target")

    # claude code changed: timeframe is never guessed by this placeholder
    # at all — always ambiguous, always left to the confirmation form.
    ambiguous.append("timeframe")

    return ResearchSpec(
        hypothesis_text=hypothesis_text,
        asset=asset,
        timeframe="1h" if "timeframe" not in ambiguous else None,
        hypothesis_type=hypothesis_type,
        features=features,
        conditions=conditions,
        direction=direction,
        target=target,
        ambiguous_fields=ambiguous,
    )


def describe_executed_spec(spec: ResearchSpec) -> str:
    """
    Plain-language description of EXACTLY what was executed, built only
    from the confirmed/executed ResearchSpec — never from the original
    free-text hypothesis. This is the single source explain_evidence() and
    the report template both use, so "what we tested" can never drift
    between the two (Bug 2/3 fix).
    """
    horizon = spec.target.get("horizon")
    horizon_desc = f"{horizon} candles"
    if horizon is not None and spec.timeframe == "1h":
        horizon_desc = f"{horizon} candles ({horizon} hours, since timeframe=1h)"

    if spec.hypothesis_type == "conditional" and spec.conditions:
        c = spec.conditions[0]
        condition_desc = f"{c.get('feature')} {c.get('operator')} {c.get('threshold')}"
        return f"Condition: {condition_desc}. Target: forward return over {horizon_desc} on {spec.asset}."

    feature_desc = spec.features[0] if spec.features else "no feature specified"
    return f"Feature: {feature_desc} (continuous). Target: forward return over {horizon_desc} on {spec.asset}."


def explain_evidence(experiment) -> str:
    """
    Section 15 — AI interpretation of ALREADY-COMPLETE evidence. This
    placeholder builds a plain, templated explanation strictly from the
    experiment's own stored statistical_results/verdict fields — it never
    introduces a number that isn't already sitting in those fields, which
    is the one hard rule that must hold even for a real future LLM
    integration (section 15: "The AI must never introduce a number that
    does not exist in the evidence package").

    claude code changed: was `f"This experiment tested '{experiment.
    hypothesis_text}'."` — quoted the RAW ORIGINAL hypothesis text
    verbatim, which goes stale the instant a student's confirmed spec
    (asset/condition/horizon) differs even slightly from their original
    wording (Bug 2/3). Now describes the EXECUTED specification via
    describe_executed_spec(), read from experiment.research_plan['spec']
    — the one place the actually-executed parameters live.
    """
    stat = experiment.statistical_results or {}
    verdict = experiment.verdict or "UNKNOWN"

    executed_spec_dict = (experiment.research_plan or {}).get("spec")
    if executed_spec_dict:
        from bot.research_lab.spec import ResearchSpec as _ResearchSpec
        executed_description = describe_executed_spec(_ResearchSpec.from_dict(executed_spec_dict))
    else:
        executed_description = "no confirmed specification was recorded for this experiment"

    if not stat:
        return f"{executed_description} No statistical evidence was produced for this experiment — nothing to interpret."

    lines = [f"This experiment tested: {executed_description}"]

    if stat.get("metric") == "conditional_event_test":
        n_true = stat.get("n_condition_true")
        mean_true = stat.get("mean_return_when_true")
        mean_false = stat.get("mean_return_when_false")
        p = stat.get("mannwhitney_p_value")
        if mean_true is not None and mean_false is not None:
            lines.append(
                f"When the condition held ({n_true} candles), the mean forward return was {mean_true:+.4%}, "
                f"versus {mean_false:+.4%} when it did not."
            )
        if p is not None:
            lines.append(f"Mann-Whitney U p-value: {p:.4f}.")
    else:
        ic = stat.get("ic")
        p = stat.get("block_permutation_p_value")
        if ic is not None:
            lines.append(f"The measured Information Coefficient was {ic:+.4f}" + (f" (block-permutation p={p:.4f})." if p is not None else "."))

    lines.append(f"Deterministic verdict: {verdict}.")
    if verdict in ("REJECTED", "INCONCLUSIVE", "INSUFFICIENT_DATA", "INVALID_RESEARCH"):
        lines.append("This evidence does not support the hypothesis as stated.")
    elif verdict in ("SUPPORTED", "PARTIALLY_SUPPORTED"):
        lines.append("This evidence is consistent with the hypothesis, within the limits of a single-experiment test.")
    elif verdict == "REQUIRES_REVIEW":
        lines.append("The requested research question may not have been fully tested — see the warnings on this experiment before treating this as a confident result.")
    return " ".join(lines)
