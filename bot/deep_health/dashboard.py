# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/deep_health/dashboard.py
# claude code changed: new file — 2026-10-07 Deep Health page model (DEEP_HEALTH_PLAN_2026-10-07.md §2, UI).
# L0 + L1 run live on every request (offline, read-only, ~1 s warm, nothing written). L2 is never run from a page:
# the newest saved L2 report is attached ONLY when it was produced on exactly this code state (same commit AND same
# uncommitted-change digest); otherwise L2 is UNKNOWN with the reason — stale evidence is never shown as current.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from bot.deep_health.core import (AREA_TITLES, AREAS, GATE_TITLES, GATES, LEVEL_TITLES, REGISTRY, STATUSES, UNKNOWN,
                                  load_latest_report, run_health, worst)

L2_COMMAND = "python manage.py deep_health_check --level L2"


def l2_state(live: dict, l2: dict):
    """(status, reason, components) for the L2 level as seen from the live run's code state."""
    if l2 is None:
        return UNKNOWN, f"L2 has never been run — run `{L2_COMMAND}`", []
    same = (l2.get("code_commit") == live.get("code_commit")
            and l2.get("code_dirty_digest") == live.get("code_dirty_digest"))
    if not same:
        return (UNKNOWN, f"the last L2 run ({l2.get('started_at', '?')[:19]}, commit {(l2.get('code_commit') or '?')[:12]}"
                         f"{' dirty' if l2.get('code_dirty') else ''}) was on different code — re-run `{L2_COMMAND}`", [])
    return worst(c["status"] for c in l2["components"]), "", l2["components"]


def build_dashboard() -> dict:
    live = run_health(("L0", "L1"))
    l2 = load_latest_report("L2")
    l2_status, l2_reason, l2_components = l2_state(live, l2)
    components = live["components"] + l2_components
    l2_areas = {s.area for s in REGISTRY.values() if s.level == "L2"}   # a missing L2 only affects areas that HAVE L2 checks
    areas = []
    for a in AREAS:
        comps = [c for c in components if c["area"] == a]
        status = worst([c["status"] for c in comps] + ([l2_status] if not l2_components and a in l2_areas else []))
        non_pass = [c for c in comps if c["status"] != "PASS"]
        areas.append({"key": a, "title": AREA_TITLES[a], "status": status, "components": comps,
                      "n_non_pass": len(non_pass), "n": len(comps)})
    overall = worst([c["status"] for c in components] + [l2_status])
    attention = [{"component": c["title"], "area": AREA_TITLES[c["area"]], "level": c["level"], **r}
                 for c in components for r in c["results"] if r["status"] != "PASS"]
    order = {s: i for i, s in enumerate(("FAIL", "UNKNOWN", "BLOCKED", "PARTIAL"))}
    attention.sort(key=lambda r: order.get(r["status"], 9))
    counts = {s: sum(1 for c in components if c["status"] == s) for s in STATUSES}
    return {"overall": overall, "areas": areas, "gates": [(g, GATE_TITLES[g]) for g in GATES], "counts": counts,
            "attention": attention, "n_fail": counts["FAIL"],
            "live": {k: live[k] for k in ("run_id", "started_at", "duration_ms", "code_commit", "code_dirty",
                                          "methodology_version", "zones_version", "health_version", "levels")},
            "l2": {"status": l2_status, "reason": l2_reason, "command": L2_COMMAND,
                   "run_id": l2.get("run_id") if l2 else None, "started_at": l2.get("started_at") if l2 else None,
                   "code_commit": l2.get("code_commit") if l2 else None,
                   "duration_ms": l2.get("duration_ms") if l2 else None, "attached": bool(l2_components)},
            "level_titles": LEVEL_TITLES}
