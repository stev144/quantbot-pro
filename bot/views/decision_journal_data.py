# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/views/decision_journal_data.py
#
# claude code changed: new file — 2026-10-07, Decision Journal UI (read-only). Reads the Phase 1 journal tables
# (bot.journal.models: JournalRun / DecisionEvent / MarketSnapshot / JournalGap) — the database is the canonical
# journal (OD-J5). Pure reads: no view in this module writes, recomputes or re-labels anything; statuses, reason
# codes and gate results are shown exactly as recorded. Decision-time information only — no outcome/label data
# exists yet (DecisionLabel is Phase 2), so nothing here can be read as a performance or edge statistic.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import Count
from django.http import Http404

from bot.journal import taxonomy as T

PAGE_SIZE = 200
# OD-J16 (owner-delegated): label/outcome VALUES are shown only for decisions in zone S; everything else (zone V,
# unassigned, sealed F, pre-S) shows the label's status but not its numbers, so browsing can never consume V.
VISIBLE_OUTCOME_ZONES = ("SELECTION",)   # claude code changed: 2026-10-07 Phase 2 — outcomes (zone-guarded)
STATUS_BADGE = {T.EXECUTED: "pos", T.NO_SIGNAL: "dim", T.REJECTED: "warn", T.REGIME_REJECTED: "warn",
                T.SYSTEM_REJECTED: "neg", T.DATA_REJECTED: "neg", T.EXECUTION_FAILED: "neg",
                T.CENSORED_END_OF_DATA: "neu"}
GATE_BADGE = {T.PASS: "pos", T.FAIL: "neg", T.NOT_EVALUATED: "dim"}
RUN_BADGE = {"CLOSED": "pos", "INCOMPLETE": "neg", "OPEN": "warn"}
COVERAGE_LABEL = {
    T.COV_PRE_LOOP: "Before the loop start (no evaluation possible)",
    T.COV_EVALUATED: "Evaluated — one decision each",
    T.COV_WARMUP: "Warm-up (required history not yet available)",
    T.COV_IN_POSITION: "Not evaluated — position open",
    T.COV_EXIT_CANDLE: "Not evaluated — trade closed on this candle",
    T.COV_ENTRY_PENDING: "Not evaluated — entry pending",
    T.COV_MAPPING_FAILURE: "Evaluated, reason unmapped (run INCOMPLETE)",
}


def _models():
    from bot.journal.models import DecisionEvent, JournalGap, JournalRun, MarketSnapshot
    return JournalRun, DecisionEvent, MarketSnapshot, JournalGap


def get_runs() -> dict:
    JournalRun, _, _, _ = _models()
    runs = list(JournalRun.objects.order_by("-started_at"))
    for r in runs:
        r.badge = RUN_BADGE.get(r.status, "dim")
    return {"runs": runs, "n_runs": len(runs)}


def _run_or_404(run_id):
    JournalRun, _, _, _ = _models()
    try:
        return JournalRun.objects.get(run_id=run_id)
    except JournalRun.DoesNotExist:
        raise Http404(f"no journal run {run_id}")


def get_run(run_id: str, *, status: str = "", page: int = 1) -> dict:
    _, DecisionEvent, _, JournalGap = _models()
    run = _run_or_404(run_id)
    run.badge = RUN_BADGE.get(run.status, "dim")
    qs = DecisionEvent.objects.filter(run=run)
    by_status = [{"status": s["status"], "n": s["n"], "badge": STATUS_BADGE.get(s["status"], "dim")}
                 for s in qs.values("status").annotate(n=Count("pk")).order_by("-n")]
    by_reason = list(qs.exclude(reason_code=None).values("reason_code", "status", "reason_stage")
                     .annotate(n=Count("pk")).order_by("-n")[:15])
    for r in by_reason:
        r["badge"] = STATUS_BADGE.get(r["status"], "dim")
    if status and status not in T.TERMINAL_STATUSES:
        raise Http404(f"unknown status filter {status!r}")
    rows = qs.filter(status=status) if status else qs
    pager = Paginator(rows.order_by("candle_index").only(
        "decision_id", "candle_index", "candle_open_time", "information_time", "status", "reason_code",
        "reason_stage", "direction", "strategy", "research_zone", "planned_entry", "planned_stop",
        "planned_target", "backtest_trade_id"), PAGE_SIZE)
    page_obj = pager.get_page(page)
    for d in page_obj.object_list:
        d.badge = STATUS_BADGE.get(d.status, "dim")
    cov = run.coverage or {}
    coverage = [{"category": c, "label": COVERAGE_LABEL.get(c, c), "n": n}
                for c, n in (cov.get("counts") or {}).items() if n]
    from bot.journal.models import DecisionLabel   # claude code changed: 2026-10-07 Phase 2 — outcomes (zone-guarded)
    label_counts = list(DecisionLabel.objects.filter(run=run).values("label_type", "status")
                        .annotate(n=Count("pk")).order_by("label_type", "status"))
    return {"label_counts": label_counts, "visible_outcome_zones": VISIBLE_OUTCOME_ZONES,   # claude code changed: 2026-10-07 Phase 2 — outcomes (zone-guarded)
            "run": run, "by_status": by_status, "by_reason": by_reason, "page": page_obj, "status_filter": status,
            "statuses": [s["status"] for s in by_status], "coverage": coverage,
            "coverage_meta": {k: cov.get(k) for k in ("n_candles", "loop_start_index", "n_missing_grid_candles",
                                                      "rows_dropped_by_backtester_prepare", "warmup_raw_reasons")},
            "gaps": list(JournalGap.objects.filter(run=run).order_by("candle_index")[:50]),
            "n_gaps": JournalGap.objects.filter(run=run).count()}


def get_decision(run_id: str, decision_id: str) -> dict:
    _, DecisionEvent, MarketSnapshot, _ = _models()
    run = _run_or_404(run_id)
    try:
        d = DecisionEvent.objects.get(run=run, decision_id=decision_id)
    except (DecisionEvent.DoesNotExist, ValueError):
        raise Http404("no such decision in this run")
    d.badge = STATUS_BADGE.get(d.status, "dim")
    gates = [{"gate": g, "result": d.gate_vector.get(g, T.NOT_EVALUATED),
              "badge": GATE_BADGE.get(d.gate_vector.get(g, T.NOT_EVALUATED), "dim")} for g in T.GATE_ORDER]
    snap = MarketSnapshot.objects.filter(decision=d).first()
    prev_d = DecisionEvent.objects.filter(run=run, candle_index__lt=d.candle_index).order_by("-candle_index").only("decision_id").first()
    next_d = DecisionEvent.objects.filter(run=run, candle_index__gt=d.candle_index).order_by("candle_index").only("decision_id").first()
    from bot.journal.models import DecisionLabel, PostTradeMetrics   # claude code changed: 2026-10-07 Phase 2 — outcomes (zone-guarded)
    visible = d.research_zone in VISIBLE_OUTCOME_ZONES
    labels = list(DecisionLabel.objects.filter(decision=d).order_by("label_type", "horizon_candles", "variant"))
    ptm = PostTradeMetrics.objects.filter(decision=d).first()
    return {"labels": labels, "outcomes_visible": visible, "post_trade": ptm,   # claude code changed: 2026-10-07 Phase 2 — outcomes (zone-guarded)
            "run": run, "d": d, "gates": gates, "snapshot": snap,
            "snapshot_values": sorted((snap.values or {}).items()) if snap else [],
            "stages": [{"stage": s, "reached": s in (d.lifecycle_stages or [])} for s in T.STAGE_ORDER],
            "prev_id": prev_d.decision_id if prev_d else None, "next_id": next_d.decision_id if next_d else None}
