# ============================================================
# bot/quant_core/status.py
# claude code changed: new file — Stage 2D, Stage 2C §18 fail-closed policy.
#
# Every canonical calculation returns an explicit status instead of a
# substituted value. A failed calculation can never turn into a usable
# number: callers must check `ok` before reading `value`.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class Status(str, Enum):
    OK = "OK"
    SKIP = "SKIP"                          # trading: do not open/size this trade
    FAIL = "FAIL"                          # research: calculation invalid
    UNTESTABLE = "UNTESTABLE"              # research: test could not be computed
    COST_UNAVAILABLE = "COST_UNAVAILABLE"  # a required cost input is unknown
    NOT_MEAN_REVERTING = "NOT_MEAN_REVERTING"
    NO_EDGE = "NO_EDGE"                    # sizing: Kelly fraction <= 0


@dataclass(frozen=True)
class Result:
    """A value together with the status that says whether it may be used."""
    status: Status
    value: Any = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status is Status.OK

    def require(self) -> Any:
        """Return the value, or raise if the status is not OK (fail closed)."""
        if not self.ok:
            raise FailClosedError(f"{self.status.value}: {self.reason}")
        return self.value


class FailClosedError(RuntimeError):
    """Raised when a caller tries to use a value whose status is not OK."""


def ok(value: Any) -> Result:
    return Result(Status.OK, value)


def bad(status: Status, reason: str, value: Optional[Any] = None) -> Result:
    if status is Status.OK:
        raise ValueError("bad() requires a non-OK status")
    return Result(status, value, reason)
