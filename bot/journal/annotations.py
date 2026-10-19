# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/journal/annotations.py
# claude code changed: new file — 2026-10-07, Decision Journal PHASE 5. The one writer of HumanAnnotation rows:
# validates each kind's value, computes the hindsight flag, never touches machine records. Staff-only (the caller
# checks). Human text is never fed to analytics, discovery or exports (separation tests enforce it).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from __future__ import annotations

import re
import uuid
from datetime import timedelta
from typing import Optional

from django.utils import timezone

OUTCOME_KNOWABLE_AFTER = timedelta(hours=24)   # the longest label window (OD-J2 H = 24, OD-J3 max h = 24)
MAX_TEXT = 5000
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,39}$")


class AnnotationRejected(ValueError):
    pass


def _validate(kind: str, text: str, value: dict) -> dict:
    from bot.journal.models import ANNOTATION_KINDS
    if kind not in ANNOTATION_KINDS:
        raise AnnotationRejected(f"kind must be one of {ANNOTATION_KINDS}")
    if len(text) > MAX_TEXT:
        raise AnnotationRejected(f"text longer than {MAX_TEXT} characters")
    value = dict(value or {})
    if kind == "CONFIDENCE":
        c = value.get("confidence")
        if not (isinstance(c, int) and not isinstance(c, bool) and 1 <= c <= 5):
            raise AnnotationRejected("CONFIDENCE needs value.confidence = an integer 1-5")
    elif kind == "TAG":
        t = (value.get("tag") or text).strip().lower()
        if not TAG_RE.match(t):
            raise AnnotationRejected("TAG must be a short slug: a-z, 0-9, '_' or '-', up to 40 characters")
        value = {"tag": t}
    elif kind == "CHECKLIST":
        items = value.get("items")
        if not (isinstance(items, dict) and items and all(isinstance(v, bool) for v in items.values())):
            raise AnnotationRejected("CHECKLIST needs value.items = {item: true/false}")
    elif not text.strip() and not value:
        raise AnnotationRejected(f"{kind} needs some text")
    return value


def written_after_outcome(*, decision=None, trade_record=None, now=None) -> bool:
    now = now or timezone.now()
    if decision is not None:
        return now > decision.information_time + OUTCOME_KNOWABLE_AFTER
    closed = getattr(trade_record, "closed_at", None)
    return closed is not None and now > closed


def add_annotation(author, *, kind: str, text: str = "", value: Optional[dict] = None, decision=None, trade_record=None,
                   supersedes=None):
    from bot.journal.models import HumanAnnotation
    if (decision is None) == (trade_record is None):
        raise AnnotationRejected("annotate exactly one decision or one trade")
    if not getattr(author, "is_staff", False):
        raise AnnotationRejected("journal annotations are staff-only")
    value = _validate(kind, text or "", value or {})
    if supersedes is not None and (supersedes.decision_id != getattr(decision, "pk", None) or
                                   supersedes.trade_record_id != getattr(trade_record, "pk", None)):
        raise AnnotationRejected("a superseding note must target the same decision/trade")
    return HumanAnnotation.objects.create(
        annotation_id=uuid.uuid4(), decision=decision, trade_record=trade_record, author=author, kind=kind,
        text=(text or "").strip(), value=value, supersedes=supersedes,
        written_after_outcome=written_after_outcome(decision=decision, trade_record=trade_record))
