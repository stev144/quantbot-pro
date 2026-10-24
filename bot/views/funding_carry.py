# ============================================================
# bot/views/funding_carry.py
# claude code changed: new file — 2026-10-22, Futures Funding Intelligence Phase 1. Thin, read-only view of the
# PERSISTED funding-carry report (research_data/funding_carry/funding_carry_report.json, written only by
# `manage.py run_funding_carry_monitor`). Nothing is recomputed on a page view; no exchange call; no orders.
# ============================================================

from statistics import median

from django.shortcuts import render

from bot.research import funding_carry_monitor as fcm


def _summary(assets):
    ok = [a for a in assets if a["status"] == fcm.OK]
    if not ok:
        return None
    net = [a["scope_A_funding_only"]["net_pct_of_spot_notional"] for a in ok]
    neg = [a["scope_A_funding_only"]["negative_month_pct"] for a in ok if a["scope_A_funding_only"]["negative_month_pct"] is not None]
    breach = [r["breached_without_topups"] for a in ok for r in a["capital_and_liquidation"]["rows"] if r["short_leverage"] == 2.0]
    return {"n_ok": len(ok), "median_net_pct": median(net), "n_negative_net": sum(v < 0 for v in net),
            "median_negative_month_pct": median(neg) if neg else None,
            "pct_breach_2x": (100.0 * sum(breach) / len(breach)) if breach else None}


def funding_carry(request):
    report = fcm.load_report()
    if report is None:
        return render(request, "funding_carry.html", {"active": "funding_carry", "report": None,
                                                      "command": "python manage.py run_funding_carry_monitor"})
    assets = sorted(report["assets"], key=lambda a: a["symbol"])
    return render(request, "funding_carry.html", {
        "active": "funding_carry", "report": report, "assets": assets,
        "unavailable": [a for a in assets if a["status"] != fcm.OK],
        "summary": _summary(assets),
    })
