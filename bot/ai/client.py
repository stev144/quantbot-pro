# claude code changed: 2026-10-26 — new file. The generic Claude API pieces that bot/market_intel/news_ai.py had
# privately (hourly budget counter, refusal check, usage reader), moved here so the Research Explainer
# (bot/academy/explainer/) reuses them instead of keeping a second copy. Adds the key lookup, an error classifier and a
# key redactor. Nothing here sends a request by itself.
#
# Security: the API key is read server-side only (ANTHROPIC_API_KEY from the environment / .env via python-decouple)
# and is never returned to a caller that renders pages — has_api_key() answers "is one configured?" without exposing it.

import re
import time
from typing import Dict

from django.core.cache import cache

_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")


class Declined(Exception):
    """Claude returned stop_reason == "refusal" (after any server-side fallback)."""


def api_key() -> str:
    """The configured key, for constructing a client inside a service module. Never pass it to a template."""
    from decouple import config
    return config("ANTHROPIC_API_KEY", default="").strip()


def has_api_key() -> bool:
    return bool(api_key())


def redact(text: str) -> str:
    """Removes anything shaped like an Anthropic key (and the configured key itself) from text shown or logged."""
    s = str(text or "")
    key = api_key()
    if key:
        s = s.replace(key, "[redacted]")
    return _KEY_PATTERN.sub("[redacted]", s)


def take_hourly_budget(prefix: str, max_per_hour: int) -> bool:
    """Counts one call against a per-hour cap kept in the Django cache; False once the cap is reached. The cache is
    per process (LocMemCache), so this is a coarse guard — durable per-user limits must be counted in the database."""
    key = f"{prefix}:calls:{int(time.time() // 3600)}"
    cache.add(key, 0, timeout=3700)
    try:
        n = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=3700)
        n = 1
    return n <= max_per_hour


def check_stop(resp) -> None:
    if getattr(resp, "stop_reason", None) == "refusal":
        raise Declined(getattr(getattr(resp, "stop_details", None), "category", None) or "declined")


def usage_of(resp) -> Dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    stu = getattr(u, "server_tool_use", None)
    return {"input_tokens": getattr(u, "input_tokens", None), "output_tokens": getattr(u, "output_tokens", None),
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", None),
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", None),
            "web_search_requests": getattr(stu, "web_search_requests", None) if stu else None}


def classify_api_error(exc: Exception) -> Dict:
    """(status, user-facing message) for an SDK exception, most specific first. Never includes the request body,
    headers or key. Statuses: NOT_CONFIGURED, CREDITS_EXHAUSTED, RATE_LIMITED, TIMEOUT, UNAVAILABLE, ERROR."""
    import anthropic
    if isinstance(exc, anthropic.AuthenticationError):
        return {"status": "NOT_CONFIGURED", "message": "The configured API key was rejected."}
    if isinstance(exc, anthropic.PermissionDeniedError):
        return {"status": "NOT_CONFIGURED", "message": "The API key does not have permission for this request."}
    if isinstance(exc, anthropic.RateLimitError):
        return {"status": "RATE_LIMITED", "message": "The Claude API is rate-limiting requests; try again shortly."}
    if isinstance(exc, anthropic.APITimeoutError):
        return {"status": "TIMEOUT", "message": "The Claude API did not answer in time."}
    if isinstance(exc, anthropic.APIConnectionError):
        return {"status": "UNAVAILABLE", "message": "The Claude API could not be reached."}
    if isinstance(exc, anthropic.APIStatusError):
        code = getattr(exc, "status_code", None)
        if code == 402 or getattr(exc, "type", None) == "billing_error":
            return {"status": "CREDITS_EXHAUSTED", "message": "The API account has no usable credit (billing error)."}
        if code is not None and code >= 500:
            return {"status": "UNAVAILABLE", "message": f"The Claude API had a server error ({code})."}
        return {"status": "ERROR", "message": f"The Claude API returned an error ({code})."}
    return {"status": "ERROR", "message": f"Unexpected error: {type(exc).__name__}"}
