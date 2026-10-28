# bot/notifications/ntfy.py  # claude code changed: 2026-10-28 — new
"""Push a notification to the owner's phone through ntfy (https://ntfy.sh): an HTTP POST to a private topic.

Settings (.env): NTFY_TOPIC — a long random topic name (it is the only secret: anyone who knows it can read the
alerts); NTFY_SERVER — default https://ntfy.sh. No topic configured → nothing is sent and False is returned.
Never raises: a failed push is logged and reported as False, so a monitoring job can never crash on it."""
from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)
PRIORITIES = {"min": "1", "low": "2", "default": "3", "high": "4", "urgent": "5"}


def send(title: str, message: str, *, priority: str = "default", tags: str = "", click: Optional[str] = None,
         post: Optional[Callable] = None) -> bool:
    from django.conf import settings
    topic = getattr(settings, "NTFY_TOPIC", "")
    if not topic:
        logger.info("ntfy: NTFY_TOPIC not set — notification not sent: %s", title)
        return False
    server = getattr(settings, "NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    headers = {"Title": title.encode("utf-8"), "Priority": PRIORITIES.get(priority, "3")}
    if tags:
        headers["Tags"] = tags
    if click:
        headers["Click"] = click
    if post is None:
        import requests
        post = requests.post
    try:
        r = post(f"{server}/{topic}", data=message.encode("utf-8"), headers=headers, timeout=10)
        ok = 200 <= getattr(r, "status_code", 0) < 300
        if not ok:
            logger.warning("ntfy: HTTP %s for %s", getattr(r, "status_code", None), title)
        return ok
    except Exception as e:
        logger.warning("ntfy: send failed (%s): %s", type(e).__name__, title)
        return False
