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
# ============================================================

from __future__ import annotations

import re
from typing import Optional

from bot.research_lab.spec import ResearchSpec, SUPPORTED_ASSETS

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

_RISE_WORDS = ("rise", "rises", "rising", "increase", "increases", "rally", "pump", "gain", "gains", "climb")
_FALL_WORDS = ("fall", "falls", "falling", "decrease", "decreases", "drop", "drops", "decline", "declines", "dump", "crash")

_HOUR_RE = re.compile(r"(\d+)\s*(?:hour|hours|hr|hrs|h)\b", re.IGNORECASE)
_DAY_RE = re.compile(r"(\d+)\s*(?:day|days|d)\b", re.IGNORECASE)


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
    day_match = _DAY_RE.search(text)
    if day_match:
        return int(day_match.group(1)) * 24  # claude code changed: this platform's only interval is 1h candles, so "N days" -> N*24 candles
    hour_match = _HOUR_RE.search(text)
    if hour_match:
        return int(hour_match.group(1))
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

    direction = _guess_direction(hypothesis_text)
    if direction is None:
        ambiguous.append("direction")

    horizon = _guess_horizon_candles(hypothesis_text)
    target = {"type": "forward_return", "horizon": horizon} if horizon else {"type": "forward_return"}
    if horizon is None:
        ambiguous.append("target")

    # claude code changed: timeframe/features/conditions are never guessed
    # by this placeholder at all — a keyword parser has no reliable way to
    # infer which technical feature a free-text hypothesis is actually
    # about (section 6's own example: "extremely positive funding rate" is
    # not extractable as a feature *name* by keyword matching alone,
    # even when the human meaning is clear). Always ambiguous, always
    # left to the confirmation form.
    ambiguous.append("timeframe")

    return ResearchSpec(
        hypothesis_text=hypothesis_text,
        asset=asset,
        timeframe="1h" if "timeframe" not in ambiguous else None,
        direction=direction,
        target=target,
        ambiguous_fields=ambiguous,
    )


def explain_evidence(experiment) -> str:
    """
    Section 15 — AI interpretation of ALREADY-COMPLETE evidence. This
    placeholder builds a plain, templated explanation strictly from the
    experiment's own stored statistical_results/verdict fields — it never
    introduces a number that isn't already sitting in those fields, which
    is the one hard rule that must hold even for a real future LLM
    integration (section 15: "The AI must never introduce a number that
    does not exist in the evidence package").
    """
    stat = experiment.statistical_results or {}
    verdict = experiment.verdict or "UNKNOWN"

    if not stat:
        return "No statistical evidence was produced for this experiment — nothing to interpret."

    ic = stat.get("ic")
    p = stat.get("block_permutation_p_value")
    lines = [f"This experiment tested '{experiment.hypothesis_text}'."]
    if ic is not None:
        lines.append(f"The measured Information Coefficient was {ic:+.4f}" + (f" (block-permutation p={p:.4f})." if p is not None else "."))
    lines.append(f"Deterministic verdict: {verdict}.")
    if verdict in ("REJECTED", "INCONCLUSIVE", "INSUFFICIENT_DATA", "INVALID_RESEARCH"):
        lines.append("This evidence does not support the hypothesis as stated.")
    elif verdict in ("SUPPORTED", "PARTIALLY_SUPPORTED"):
        lines.append("This evidence is consistent with the hypothesis, within the limits of a single-experiment test.")
    return " ".join(lines)
