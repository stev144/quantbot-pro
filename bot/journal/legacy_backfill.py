# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/journal/legacy_backfill.py
#
# claude code changed: new file — 2026-10-07, Decision Journal PHASE 3 (legacy backfill, Rev B §8/§17).
# Brings EXISTING journal rows into the decision journal as clearly-limited LEGACY decisions:
#   TradeRecord        -> EXECUTED   (pairs legs grouped by pair_trade_id: ONE decision per pair trade)
#   ExecutionIncident  -> EXECUTION_FAILED (reason LEGACY_EXECUTION_INCIDENT, kind/state in detail)
#   SignalRejection    -> NOT backfilled: it records no execution mode, and the environment is never invented.
# Binding limits: no snapshot (nothing is reconstructed), provenance NOT_RECORDED, terminal_source LEGACY_BACKFILL,
# taxonomy QBP-JRNL-LEGACY-1 (never mixed with backtest vocabularies), times are the RECORDED times (zone_basis
# LEGACY_RECORDED_TIME — not decision candles), and the run is flagged LEGACY_INCOMPLETE_POPULATION: it has no
# NO_SIGNAL rows and no denominator, so it can never support rates, rejection value, episodes or a family.
# Old rows are never modified except to set their own nullable decision_id link (only when it is empty).
# Idempotent: decision ids are uuid5 of the source rows, and already-linked rows are skipped.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from __future__ import annotations

import uuid
from collections import defaultdict

from bot.journal import taxonomy as T

LEGACY_NAMESPACE = uuid.UUID("3b1d9a52-6e7f-5c18-9a4b-2f6d8e0c1a77")
EVIDENCE = "LEGACY — incomplete population (no denominator, no snapshot), never evidence"
MODE_TO_ENV = {"LIVE": "LIVE", "DRY_RUN": "DRY_RUN", "RESEARCH": "RESEARCH"}   # UNKNOWN -> skipped, never invented


def _direction(side: str) -> str:
    s = (side or "").upper()
    return "LONG" if s in ("BUY", "LONG") else "SHORT" if s in ("SELL", "SHORT") else ""


def plan_backfill() -> dict:
    """What a backfill WOULD do (no writes): groups, counts and every skipped row with its reason."""
    from bot.journal.models import ExecutionIncident, SignalRejection, TradeRecord
    trades = TradeRecord.objects.filter(decision_id__isnull=True).order_by("created_at", "pk")
    groups = defaultdict(list)
    skipped = []
    for t in trades:
        env = MODE_TO_ENV.get(t.execution_mode)
        if env is None:
            skipped.append({"source": "TradeRecord", "pk": t.pk, "reason": f"execution_mode {t.execution_mode!r} — environment unknown"})
            continue
        key = ("PAIR", str(t.pair_trade_id)) if t.pair_trade_id else ("SINGLE", str(t.pk))
        groups[(t.execution_mode, key)].append(t)
    incidents = []
    for inc in ExecutionIncident.objects.filter(decision_id__isnull=True).order_by("created_at", "pk"):
        if MODE_TO_ENV.get(inc.execution_mode) is None:
            skipped.append({"source": "ExecutionIncident", "pk": inc.pk, "reason": f"execution_mode {inc.execution_mode!r} — environment unknown"})
        else:
            incidents.append(inc)
    for sr in SignalRejection.objects.filter(decision_id__isnull=True):
        skipped.append({"source": "SignalRejection", "pk": sr.pk, "reason": "no execution mode recorded — environment unknown"})
    return {"trade_groups": groups, "incidents": incidents, "skipped": skipped}


def backfill(*, commit: bool = False) -> dict:
    """Create LEGACY runs/decisions for unlinked rows. commit=False is a dry run (default)."""
    from django.db import transaction
    from django.utils import timezone
    from bot.journal.models import (DecisionEvent, ExecutionIncident, JournalRun, TradeRecord, JOURNAL_RUN_CLOSED,
                                    JOURNAL_RUN_OPEN)
    from bot.quant_core.research_zones import ZONES, ZONES_VERSION
    plan = plan_backfill()
    summary = {"decisions": 0, "trade_records_linked": 0, "incidents_linked": 0,
               "skipped": plan["skipped"], "committed": commit, "runs": []}
    by_mode = defaultdict(list)
    for (mode, key), legs in plan["trade_groups"].items():
        by_mode[mode].append(("TRADE", key, legs))
    for inc in plan["incidents"]:
        by_mode[inc.execution_mode].append(("INCIDENT", ("INCIDENT", str(inc.pk)), [inc]))
    if not commit:
        summary["decisions"] = sum(len(v) for v in by_mode.values())
        summary["runs"] = sorted(by_mode)
        return summary
    with transaction.atomic():
        for mode, items in sorted(by_mode.items()):
            run_id = f"LEGACY-{mode}-{timezone.now():%Y%m%dT%H%M%S}"
            run = JournalRun.objects.create(
                run_id=run_id, environment=MODE_TO_ENV[mode], execution_mode=mode, symbol="MULTIPLE",
                asset_class="MULTIPLE", venue="MULTIPLE", timeframe="UNKNOWN", status=JOURNAL_RUN_OPEN,
                started_at=timezone.now(), methodology_version="LEGACY_NOT_RECORDED", protocol_hash="",
                config_hash="NOT_RECORDED", config={}, data_version="NOT_RECORDED", data_version_detail={},
                zones_version=ZONES_VERSION, zones_hash=ZONES.zones_hash(), market_profile_hash="",
                strategy_set="LEGACY", strategy_version="NOT_RECORDED", taxonomy_version=T.LEGACY_TAXONOMY_VERSION,
                schema_version=T.SCHEMA_VERSION, gate_order_version="NOT_RECORDED", feature_set_version="NONE",
                coverage_version="NOT_RECORDED", evidence_status=EVIDENCE,
                coverage={"population": "LEGACY_INCOMPLETE_POPULATION",
                          "note": "only rows that were journaled at the time; no NO_SIGNAL, no other rejections, no denominator"})
            rows, links = [], []
            for kind, key, srcs in items:
                first = srcs[0]
                did = uuid.uuid5(LEGACY_NAMESPACE, f"{kind}|{key[0]}|{key[1]}")
                t_rec = first.created_at
                common = dict(decision_id=did, run=run, environment=run.environment, execution_mode=mode,
                              asset_class="NOT_RECORDED", venue=getattr(first, "venue", "") or "NOT_RECORDED",
                              timeframe="UNKNOWN", candle_index=-1, candle_open_time=t_rec, information_time=t_rec,
                              research_zone=ZONES.classify(t_rec), zone_basis="LEGACY_RECORDED_TIME",
                              zones_hash=run.zones_hash, strategy_version="NOT_RECORDED", gate_vector={},
                              gate_order_version="NOT_RECORDED", methodology_version=run.methodology_version,
                              config_hash="NOT_RECORDED", data_version="NOT_RECORDED",
                              taxonomy_version=T.LEGACY_TAXONOMY_VERSION, terminal_source="LEGACY_BACKFILL")
                if kind == "TRADE":
                    sym = (first.strategy.split("_", 1)[1] if key[0] == "PAIR" and "_" in (first.strategy or "")
                           else first.symbol)
                    rows.append(DecisionEvent(**common, symbol=sym[:40], strategy=(first.strategy or "")[:60],
                                              direction=_direction(first.side) if key[0] == "SINGLE" else "",
                                              status=T.EXECUTED, lifecycle_stages=list(T.STAGE_ORDER),
                                              planned_stop=first.sl if key[0] == "SINGLE" else None,
                                              planned_target=first.tp if key[0] == "SINGLE" else None,
                                              execution_eligible=True,
                                              reason_detail={"legacy_source": "TradeRecord", "trade_record_pks": [s.pk for s in srcs],
                                                             "pair_trade_id": key[1] if key[0] == "PAIR" else None,
                                                             "legs": [{"pk": s.pk, "symbol": s.symbol, "side": s.side, "leg": s.leg} for s in srcs]}))
                    links.append(("TradeRecord", [s.pk for s in srcs], did))
                else:
                    rows.append(DecisionEvent(**common, symbol=(first.pair_name or "")[:40], strategy="LEGACY",
                                              status=T.EXECUTION_FAILED, reason_code="LEGACY_EXECUTION_INCIDENT",
                                              reason_stage="EXECUTION", origin="EXECUTION", lifecycle_stages=list(T.STAGE_ORDER),
                                              execution_eligible=True,
                                              reason_detail={"legacy_source": "ExecutionIncident", "pk": first.pk,
                                                             "kind": first.kind, "state": first.state}))
                    links.append(("ExecutionIncident", [first.pk], did))
            for r in rows:
                T.validate_legacy_terminal(r.status, r.reason_code, r.reason_stage)
            DecisionEvent.objects.bulk_create(rows)
            for model_name, pks, did in links:
                model = TradeRecord if model_name == "TradeRecord" else ExecutionIncident
                n = model.objects.filter(pk__in=pks, decision_id__isnull=True).update(decision_id=did)   # only empty links
                summary["trade_records_linked" if model_name == "TradeRecord" else "incidents_linked"] += n
            run.status, run.n_decisions, run.closed_at = JOURNAL_RUN_CLOSED, len(rows), timezone.now()
            run.save()
            summary["decisions"] += len(rows)
            summary["runs"].append(run_id)
    return summary
