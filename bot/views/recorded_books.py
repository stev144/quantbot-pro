# ============================================================
# bot/views/recorded_books.py
# claude code changed: new file — 2026-10-25. "Recorded order books" panel on the Venue Comparison page: shows the
# LATEST sample of each book written by the Phase A research recorder (manage.py research_record_cross_exchange_books).
# Read-only: it opens the recorder's files, never an exchange connection, and starts nothing. Display only — no
# cross-exchange gap, spread-between-venues or "opportunity" figure is calculated here (Phase B is sealed: ZONES.F).
# ============================================================

import time
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse

from bot.research.cross_exchange.config import RecorderConfig
from bot.research.cross_exchange.recorder import latest_books

VENUE_ORDER = ("binance", "kraken", "coinbase")
SNAPSHOT_STALE_S = 10      # a "latest" row older than this means the recorder is not writing (stopped / asleep)


def recorder_dir() -> Path:
    return Path(settings.BASE_DIR) / RecorderConfig().out_dir


def _trim(x):
    """'80909.34000000' → '80909.34' (exact decimal text kept, only trailing zeros dropped); None stays None."""
    if x is None:
        return None
    s = str(x)
    return s.rstrip("0").rstrip(".") if "." in s else s


def _book(row: dict, now_ns: int) -> dict:
    levels = lambda side: [[_trim(p), _trim(q)] for p, q in (row.get(side) or [])]
    bid, ask = row.get("best_bid"), row.get("best_ask")
    spread_pct = None
    if row.get("usable") and bid and ask:
        b, a = float(bid), float(ask)
        spread_pct = (a - b) / ((a + b) / 2) * 100            # this exchange's own bid/ask spread
    return {
        "venue": row.get("venue"), "symbol": row.get("venue_symbol"), "status": row.get("status"),
        "status_reason": row.get("status_reason"), "usable": bool(row.get("usable")), "flags": row.get("flags") or [],
        "age_s": round((now_ns - row["sample_utc_ns"]) / 1e9, 1) if row.get("sample_utc_ns") else None,
        "book_age_s": row.get("book_age_s"), "feed_delay_ms": row.get("feed_delay_ms"),
        "best_bid": _trim(bid), "best_ask": _trim(ask), "spread_pct": spread_pct,
        "bids": levels("bids"), "asks": levels("asks"), "quote": row.get("quote"), "base": row.get("base"),
    }


def recorded_books_payload(out_dir=None) -> dict:
    now_ns = time.time_ns()
    meta, rows = latest_books(str(out_dir or recorder_dir()))
    if meta is None:
        return {"available": False, "reason": "No recording session found. Start one with: "
                                              "python manage.py research_record_cross_exchange_books"}
    by_inst: dict = {}
    for r in rows.values():
        by_inst.setdefault(r.get("instrument"), []).append(_book(r, now_ns))
    instruments = []
    for inst in sorted(by_inst, key=lambda k: (k or "")):
        books = sorted(by_inst[inst], key=lambda b: VENUE_ORDER.index(b["venue"]) if b["venue"] in VENUE_ORDER else 99)
        instruments.append({"instrument": inst, "base": books[0]["base"], "books": books})
    ages = [b["age_s"] for i in instruments for b in i["books"] if b["age_s"] is not None]
    newest = min(ages) if ages else None
    return {
        "available": True, "session_id": meta.get("session_id"), "status": meta.get("status"),
        "status_note": meta.get("status_note"), "start_utc": meta.get("start_utc"), "heartbeat_utc": meta.get("heartbeat_utc"),
        "samples_written": meta.get("samples_written"), "depth": (meta.get("config") or {}).get("depth"),
        "interval_s": (meta.get("config") or {}).get("sample_interval_s"),
        "snapshot_age_s": newest, "live": meta.get("status") == "RUNNING" and newest is not None and newest <= SNAPSHOT_STALE_S,
        "instruments": instruments, "governance": meta.get("governance"),
    }


def recorded_books_json(request):
    return JsonResponse(recorded_books_payload(), json_dumps_params={"separators": (",", ":")})
