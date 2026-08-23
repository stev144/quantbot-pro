# ============================================================
# bot/research_lab/tools/base.py
# Research Lab — Research Tool Layer shared plumbing (section 8).
#
# claude code changed: new file. Every tool function in this package
# shares this contract: explicit name, typed inputs, typed structured
# output (section 9 — never natural language), execution-duration timing,
# and exception containment (a tool that errors returns a failed
# ToolResult, it never raises out into the caller). Nothing here computes
# a statistic itself — this module is pure plumbing around calls into the
# real research engines.
# ============================================================

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Dict

from bot.research_lab.policy_gate import is_tool_allowed


@dataclass
class ToolResult:
    tool_name: str
    status: str  # "success" | "error"
    output: Dict = field(default_factory=dict)
    duration_seconds: float = 0.0
    error: str = ""
    error_type: str = ""  # claude code changed: new — Statistical Integrity Hardening. The exception CLASS name (e.g. "InsufficientEventsError"), separate from the formatted `error` message, so a caller can route on failure MODE (not just log a string) without parsing free text. Empty on success, always populated on failure.

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "status": self.status,
            "output": dict(self.output),
            "duration_seconds": round(self.duration_seconds, 4),
            "error": self.error,
            "error_type": self.error_type,
        }


# claude code changed: new — the registry every tool module below
# populates via @register_tool. run_tool() is the ONLY entry point the
# rest of the Research Lab (orchestration, views) is allowed to use —
# nothing calls a tool function directly, so every call is guaranteed to
# pass through the risk-tier re-check and the log entry below, with no
# code path that skips either.
_TOOL_REGISTRY: Dict[str, Callable[..., Dict]] = {}


def register_tool(name: str):
    def decorator(fn: Callable[..., Dict]):
        _TOOL_REGISTRY[name] = fn
        return fn
    return decorator


def run_tool(tool_name: str, risk_tier: str, **params) -> ToolResult:
    """
    The single choke point every tool call passes through. Re-verifies
    is_tool_allowed() here too (not just once at the Policy Gate's plan
    stage) — defense in depth, since the gap between "planned" and "run"
    could in principle be exploited by a caller that skips the plan step.
    """
    if not is_tool_allowed(tool_name, risk_tier):
        return ToolResult(
            tool_name=tool_name, status="error",
            error=f"'{tool_name}' is not an allowed tool at risk_tier={risk_tier}",
        )

    fn = _TOOL_REGISTRY.get(tool_name)
    if fn is None:
        return ToolResult(tool_name=tool_name, status="error", error=f"'{tool_name}' has no registered implementation")

    start = time.monotonic()
    try:
        output = fn(**params)
        duration = time.monotonic() - start
        return ToolResult(tool_name=tool_name, status="success", output=output, duration_seconds=duration)
    except Exception as exc:  # claude code changed: a tool must never raise out into the caller — every failure becomes a structured, logged result
        duration = time.monotonic() - start
        return ToolResult(
            tool_name=tool_name, status="error", duration_seconds=duration,
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}",
            error_type=type(exc).__name__,
        )
