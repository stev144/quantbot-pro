# ============================================================
# bot/views/cross_exchange_gaps.py
# claude code changed: new file — 2026-10-25, cross-exchange research PHASE B page. Read-only view of the newest
# PERSISTED gap-study report (written only by `manage.py research_cross_exchange_gap_study`). Nothing is recomputed on
# a page view; no exchange call; no orders. DESCRIPTIVE only (owner authorisation + conditions in the report).
# ============================================================
from pathlib import Path

from django.conf import settings
from django.shortcuts import render

from bot.research.cross_exchange.config import RecorderConfig
from bot.research.cross_exchange.gap_study import load_latest_report

COMMAND = "python manage.py research_cross_exchange_gap_study"


def _out_dir() -> Path:
    return Path(settings.BASE_DIR) / RecorderConfig().out_dir


def _row(pair: dict, sizes) -> dict:
    top = pair["by_size"].get("top") or {}
    g, n = top.get("gross_bps"), top.get("net_bps")
    eg = top.get("episodes_gross_positive") or {}
    by_size = []
    for s in sizes:
        c = pair["by_size"].get(str(s)) or {}
        elig = c.get("eligible_instants") or 0
        by_size.append({"size": s, "gross": c.get("gross_bps"), "net": c.get("net_bps"),
                        "insufficient_pct": (100.0 * c.get("insufficient_depth", 0) / elig) if elig else None})
    return {"buy": pair["buy_venue"], "sell": pair["sell_venue"], "fee_bps": pair["fee_bps_total"],
            "eligible": top.get("eligible_instants"), "gross": g, "net": n, "episodes": eg, "by_size": by_size,
            "any_net_positive": bool(n and n["pct_positive"] > 0)
                                or any(b["net"] and b["net"]["pct_positive"] > 0 for b in by_size)}


def cross_exchange_gaps(request):
    report = load_latest_report(str(_out_dir()))
    if report is None:
        return render(request, "cross_exchange_gaps.html", {"active": "cross_exchange_gaps", "report": None,
                                                            "command": COMMAND})
    sizes = report["config"]["sizes_quote"]
    instruments = [{"instrument": i["instrument"], "rows": [_row(p, sizes) for p in i["pairs"]]}
                   for i in report["instruments"]]
    dq = report["data_quality"]
    usable = sum(v["usable_rows"] for v in dq.values())
    excluded = sum(sum(v["excluded"].values()) for v in dq.values())
    inp = report["input"]
    span_h = None
    if inp.get("first_sample_utc") and inp.get("last_sample_utc"):
        from datetime import datetime
        span_h = (datetime.fromisoformat(inp["last_sample_utc"]) - datetime.fromisoformat(inp["first_sample_utc"])).total_seconds() / 3600
    fees = [{"venue": v, "bps": r * 10000, "pct": r * 100,
             "basis": report["config"]["fee_basis"].get(v, "").removeprefix("VERIFIED ").removeprefix("UNVERIFIED "),
             "verified": report["config"]["fee_basis"].get(v, "").startswith("VERIFIED")}
            for v, r in sorted(report["config"]["fees"].items())]
    return render(request, "cross_exchange_gaps.html", {
        "active": "cross_exchange_gaps", "report": report, "instruments": instruments, "sizes": sizes,
        "span_h": span_h, "fees": fees, "excluded_pct": (100.0 * excluded / (usable + excluded)) if usable + excluded else None,
        "n_net_positive_rows": sum(r["any_net_positive"] for i in instruments for r in i["rows"]),
        "n_rows": sum(len(i["rows"]) for i in instruments), "command": COMMAND,
    })
