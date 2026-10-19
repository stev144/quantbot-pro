# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/journal/live_recorder.py
#
# claude code changed: new file — 2026-10-07, Decision Journal PHASE 6 (owner: "Build it, strictly DRY_RUN and
# equivalence-tested"; the OD-J13 live half approved with the Revision B §4 definition).
#
# LiveDecisionRecorder OBSERVES one bot_runner venue session through bot_runner._journal() and the
# ExecutionEngine.journal_hook. It writes each decision as it happens (a live session is long-running):
#   * evaluation point = a newly closed candle on which the router ran while the engine was FLAT;
#   * a BUY/SELL while a position is open -> recorded with entry_eligible = False (DUPLICATE_SIGNAL /
#     POSITION_ALREADY_OPEN by direction, §5.3); no-signal candles while in position -> coverage only;
#   * a skipped hour -> JournalGap EVALUATION_NOT_PERFORMED (never NO_SIGNAL); the same candle again in the same run
#     (e.g. a 60 s network retry) -> not a new point; the same candle in a later run -> reevaluation_of;
#   * write-ahead: a DecisionAttempt row is committed BEFORE the order is sent; an attempt left without an outcome
#     when the process stops becomes an ORPHANED_ATTEMPT gap at the next start (the outcome is never guessed).
# It refuses to attach to a non-DRY_RUN engine. Every argument it receives is a copy; its errors are logged and
# swallowed by the callers, so it cannot change a trading decision (proved by the replay equivalence tests).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Optional

import numpy as np
import pandas as pd

from bot.journal import taxonomy as T
from bot.journal.decision_recorder import REGIME_FIELDS, STRATEGY_OUTPUT_FIELDS, _clean

logger = logging.getLogger(__name__)
LIVE_NAMESPACE = uuid.UUID("6c2e8f14-9d3b-5a71-8e0f-4b7a2c9d1e35")
STRATEGY_SET = "StrategyRouter[MovingAverageStrategy,MeanReversionStrategy]"
LIVE_FEATURE_SET_VERSION = "QBP-JRNL-SNAP-LIVE-1"
OHLCV = ("open", "high", "low", "close", "volume")


class RecorderRefused(RuntimeError):
    pass


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


class LiveDecisionRecorder:
    def __init__(self, engine, *, symbol: str, venue: str, timeframe: str = "1h"):
        if not getattr(engine, "dry_run", False):
            raise RecorderRefused("the decision journal attaches to DRY_RUN engines only (owner Phase 6 decision)")
        from django.utils import timezone
        from bot.journal.models import JournalRun, JOURNAL_RUN_OPEN
        from bot.quant_core.market_profile import asset_class_for_symbol
        from bot.quant_core.provenance import RESEARCH_ENGINE_METHODOLOGY, code_state, config_hash, make_run_id
        from bot.quant_core.research_zones import ZONES, ZONES_VERSION
        self.engine, self.symbol, self.venue, self.timeframe = engine, symbol, venue, timeframe
        self.step = pd.Timedelta(timeframe)
        self.zones = ZONES
        self.failed = None
        self.pending = None
        self.last_key = None
        self.coverage = {"EVALUATED": 0, T.COV_IN_POSITION: 0, "REEVALUATED_SAME_CANDLE": 0, T.COV_WARMUP: 0,
                         "EVALUATION_NOT_PERFORMED": 0}
        self.warmup_open = {"TREND": True, "RANGING": True}
        self.closed = False
        self.orphans_found = self._close_stale_runs()
        code = code_state()
        cfg = {"engine": "bot.core.bot_runner (live loop, DRY_RUN)", "symbol": symbol, "venue": venue,
               "timeframe": timeframe, "position_sizer": {k: v for k, v in vars(engine.position_sizer).items()
                                                          if isinstance(v, (int, float, str, bool))}}
        now = timezone.now()
        self.strategy_version = "UNVERSIONED@" + (code.commit or "NO_GIT")[:12] + (f"+dirty:{code.dirty_digest[:12]}" if code.dirty else "")
        self.run = JournalRun.objects.create(
            run_id="LIVE-" + make_run_id(symbol=symbol, venue=venue, started=now.isoformat(), commit=code.commit)[:32],
            environment="DRY_RUN", execution_mode="DRY_RUN", symbol=symbol.replace("/", "_"),
            asset_class=asset_class_for_symbol(symbol), venue=venue, timeframe=timeframe, status=JOURNAL_RUN_OPEN,
            started_at=now, code_commit=code.commit, code_dirty=code.dirty, dirty_digest=code.dirty_digest,
            methodology_version=RESEARCH_ENGINE_METHODOLOGY, protocol_hash="", config_hash=config_hash(cfg), config=cfg,
            data_version="LIVE_STREAM", data_version_detail={"note": "live: each decision carries the SHA-256 of the "
                                                                     "exact closed candles it saw (input_window_sha256)"},
            zones_version=ZONES_VERSION, zones_hash=ZONES.zones_hash(), market_profile_hash="",
            strategy_set=STRATEGY_SET, strategy_version=self.strategy_version, taxonomy_version=T.LIVE_TAXONOMY_VERSION,
            schema_version=T.SCHEMA_VERSION, gate_order_version=T.LIVE_GATE_ORDER_VERSION,
            feature_set_version=LIVE_FEATURE_SET_VERSION, coverage_version="QBP-JRNL-COVERAGE-LIVE-1",
            evidence_status="OPERATIONAL RECORD — live/dry-run (zone F or unassigned), never evidence")

    def attach(self, session) -> "LiveDecisionRecorder":
        session.journal = self
        self.engine.journal_hook = self.on_engine_event
        return self

    # ── crashed sessions ────────────────────────────────────────────────────────────────────────────────────
    def _close_stale_runs(self) -> int:
        from django.utils import timezone
        from bot.journal.models import DecisionAttempt, DecisionEvent, JournalGap, JournalRun
        orphans = 0
        for run in JournalRun.objects.filter(status="OPEN", environment="DRY_RUN", strategy_set=STRATEGY_SET,
                                             symbol=self.symbol.replace("/", "_"), venue=self.venue):
            done = set(DecisionEvent.objects.filter(run=run).values_list("decision_id", flat=True))
            for a in DecisionAttempt.objects.filter(run=run):
                if a.decision_id not in done:
                    JournalGap.objects.create(run=run, kind="ORPHANED_ATTEMPT", candle_open_time=a.candle_open_time,
                                              detail={"decision_id": str(a.decision_id),
                                                      "note": "order attempted, outcome never recorded; not guessed"})
                    orphans += 1
            run.status, run.closed_at = "INCOMPLETE", timezone.now()
            run.n_decisions = len(done)   # claude code changed: 2026-10-07 Phase 6 — a crashed run's count was left at 0 (found by the synthetic UI demo)
            run.incomplete_reasons = sorted(set((run.incomplete_reasons or []) + ["PROCESS_ENDED_WITHOUT_CLOSE"] +
                                                (["ORPHANED_ATTEMPT"] if orphans else [])))
            run.save()
        return orphans

    # ── hooks (called through bot_runner._journal / ExecutionEngine._journal) ─────────────────────────────────
    def on_no_evaluation(self, reason: str):
        if self.failed:
            return
        self._gap("EVALUATION_NOT_PERFORMED", None, {"reason": reason})
        self.coverage["EVALUATION_NOT_PERFORMED"] += 1

    def _new_candle(self, df) -> Optional[pd.Timestamp]:
        """The decision candle (last closed row). None when this candle was already evaluated in this run."""
        key = _utc(df.index[-1])
        if self.last_key is not None:
            if key == self.last_key:
                self.coverage["REEVALUATED_SAME_CANDLE"] += 1
                return None
            missing = int((key - self.last_key) / self.step) - 1
            if missing > 0:
                self._gap("EVALUATION_NOT_PERFORMED", self.last_key + self.step,
                          {"reason": "candles never evaluated (loop drift or downtime)", "n_candles": missing})
                self.coverage["EVALUATION_NOT_PERFORMED"] += missing
        self.last_key = key
        return key

    def on_data_refusal(self, df, code: str, detail: dict, in_position: bool):
        if self.failed:
            return
        key = self._new_candle(df)
        if key is None:
            return
        if in_position:
            self.coverage[T.COV_IN_POSITION] += 1
            return
        d = self._draft(df, key, None, {}, "DATA", execution_eligible=False)
        d["gate_vector"] = T.gate_vector("DATA", failed=T.G_PRICE, paths=T.LIVE_PATHS, order=T.LIVE_GATE_ORDER)
        d["lifecycle_stages"] = [T.EVALUATED]
        self._write(d, T.DATA_REJECTED, code, T.STAGE_DATA, {"price_check": {k: _clean(v) for k, v in detail.items()}})

    def on_evaluation(self, df, regime_result, signal: dict, in_position: bool, price_checked: bool):
        if self.failed:
            return
        key = self._new_candle(df)
        if key is None:
            return
        regime = getattr(regime_result, "regime", None)
        candidate = signal.get("signal") in ("BUY", "SELL")
        if in_position and not candidate:
            self.coverage[T.COV_IN_POSITION] += 1
            return
        disabled = () if price_checked else (T.G_PRICE,)
        try:
            path = T.path_for_regime(regime)
            if candidate:
                mapped = None
            elif signal.get("signal") == "NO_SIGNAL":
                mapped = T.map_no_signal_reason(signal.get("reason"), regime, warmup_open=self.warmup_open.get(path, False))
            else:
                mapped = T.map_invalid_router_output()
        except T.UnmappedReason as e:
            self._gap("MAPPING_FAILURE", key, {"error": str(e), "raw_reason": signal.get("reason")})
            return
        if isinstance(mapped, T.Warmup):
            self.coverage[T.COV_WARMUP] += 1
            return
        if candidate or (mapped is not None and (mapped.candidate or mapped.gate in T.MA_GATES or mapped.gate in T.MR_GATES)):
            self.warmup_open[path] = False
        d = self._draft(df, key, regime_result, signal, path, execution_eligible=candidate and not in_position)
        if candidate:
            d.update(direction="LONG" if signal["signal"] == "BUY" else "SHORT", lifecycle_stages=[T.EVALUATED, T.SIGNAL_GENERATED],
                     _path=path, _disabled=disabled, _signal_reason=str(signal.get("reason", ""))[:200])
            self._planned(d, signal)
            self.pending = d
            return
        d["direction"] = mapped.direction
        d["lifecycle_stages"] = [T.EVALUATED, T.SIGNAL_GENERATED, T.GATES_EVALUATED] if mapped.candidate else [T.EVALUATED]
        d["gate_vector"] = T.gate_vector(path, failed=mapped.gate, disabled=disabled, paths=T.LIVE_PATHS, order=T.LIVE_GATE_ORDER)
        d["raw_reason"] = str(signal.get("reason") or "")[:200]
        self._write(d, mapped.status, mapped.code, mapped.stage, mapped.detail)

    def on_engine_event(self, event: str, *args):
        if self.failed or event != "before_order" or self.pending is None:
            return
        from bot.journal.models import DecisionAttempt
        DecisionAttempt.objects.create(decision_id=self.pending["decision_id"], run=self.run, symbol=self.symbol,
                                       candle_open_time=self.pending["candle_open_time"])   # write-ahead (Rev B §5.4)

    def on_execution_result(self, refusal: Optional[str], open_position: Optional[dict]):
        if self.failed:
            return
        d, self.pending = self.pending, None
        if d is None:
            return                                   # the candle was a same-run re-evaluation: nothing pending
        path, disabled = d.pop("_path"), d.pop("_disabled")
        reason_detail = {"signal_reason": d.pop("_signal_reason", "")}
        if refusal is None and open_position is not None:
            d["lifecycle_stages"] += [T.GATES_EVALUATED, T.EXECUTION_ATTEMPTED]
            d["gate_vector"] = T.gate_vector(path, failed=None, passed_through=T.ENGINE_GATES[-1], disabled=disabled,
                                             paths=T.LIVE_PATHS, order=T.LIVE_GATE_ORDER)
            self._write(d, T.EXECUTED, None, None, reason_detail, link_order_id=open_position.get("order_id"))
            return
        try:
            mapped = T.map_engine_refusal(refusal, direction=d["direction"],
                                          open_side=(open_position or {}).get("side"))
        except T.UnmappedReason as e:
            self._gap("MAPPING_FAILURE", d["candle_open_time"], {"error": str(e), "stage": "execution"})
            return
        d["lifecycle_stages"] += ([T.GATES_EVALUATED, T.EXECUTION_ATTEMPTED] if mapped.status == T.EXECUTION_FAILED
                                  else [T.GATES_EVALUATED])
        d["gate_vector"] = T.gate_vector(path, failed=mapped.gate, disabled=disabled, paths=T.LIVE_PATHS, order=T.LIVE_GATE_ORDER)
        d["raw_reason"] = refusal
        self._write(d, mapped.status, mapped.code, mapped.stage, {**reason_detail, **mapped.detail})

    def record_failure(self, event, exc):
        if self.failed is None:
            self.failed = {"hook": event, "error": f"{type(exc).__name__}: {exc}"}
            logger.error(f"[DecisionJournal:live] recorder failure in {event}: {exc} — run will close INCOMPLETE")
            try:
                self._gap("RECORDER_FAILURE", None, dict(self.failed))
            except Exception:
                pass

    def close(self):
        from django.utils import timezone
        from bot.journal.models import DecisionEvent, JournalGap
        if self.closed:
            return
        self.closed = True
        kinds = set(JournalGap.objects.filter(run=self.run).exclude(kind="EVALUATION_NOT_PERFORMED")
                    .values_list("kind", flat=True))
        self.run.status = "INCOMPLETE" if (kinds or self.failed) else "CLOSED"
        self.run.incomplete_reasons = sorted(kinds)
        self.run.n_decisions = DecisionEvent.objects.filter(run=self.run).count()
        self.run.coverage = {"counts": dict(self.coverage), "orphans_found_at_start": self.orphans_found}
        self.run.closed_at = timezone.now()
        self.run.save()

    # ── helpers ─────────────────────────────────────────────────────────────────────────────────────────────
    def _gap(self, kind, t, detail):
        from bot.journal.models import JournalGap
        JournalGap.objects.create(run=self.run, kind=kind, candle_open_time=t.to_pydatetime() if t is not None else None,
                                  detail={k: (str(v) if isinstance(v, (pd.Timestamp,)) else v) for k, v in detail.items()})

    def _draft(self, df, key, regime_result, signal, path, *, execution_eligible: bool) -> dict:
        cols = [c for c in OHLCV if c in df.columns]
        arr = df[cols].to_numpy(dtype="float64")
        idx = pd.DatetimeIndex([_utc(t) for t in df.index])
        h = hashlib.sha256(idx.asi8.tobytes())
        h.update(np.ascontiguousarray(arr).tobytes())
        info = key + self.step
        values = {f"candle_{c}": _clean(v) for c, v in zip(cols, arr[-1])}
        for f in REGIME_FIELDS:
            values[f"regime_{f}"] = _clean(getattr(regime_result, f, None)) if regime_result is not None else None
        for f in STRATEGY_OUTPUT_FIELDS:
            if f in signal:
                values[f"strategy_{f}"] = _clean(signal[f])
        did = uuid.uuid5(LIVE_NAMESPACE, f"{self.run.run_id}|{self.symbol}|{key.isoformat()}")
        return {"decision_id": did, "candle_open_time": key.to_pydatetime(), "information_time": info.to_pydatetime(),
                "candle_index": int(key.value // 3_600_000_000_000), "research_zone": self.zones.classify(key),
                "strategy": T.STRATEGY_FOR_PATH.get(path, "LIVE_DATA_CHECK"), "execution_eligible": execution_eligible,
                "_snapshot": {"window_start_time": idx[0].to_pydatetime(), "window_end_time": key.to_pydatetime(),
                              "window_rows": int(len(arr)), "input_window_sha256": h.hexdigest(), "values": values,
                              "available_at": {k: info.isoformat() for k in values},
                              "missing": [k for k, v in values.items() if v is None]},
                "risk_state": {"drawdown_guard_tripped": bool(self.engine.drawdown_guard.tripped),
                               "peak_balance": _clean(self.engine.drawdown_guard.peak_balance)},
                "portfolio_state": {"open_positions": sorted(self.engine.position_tracker.open_positions)},
                "liquidity_state": {"modelled": False}, "financing_state": {"modelled": False, "reason": "spot DRY_RUN"},
                "data_quality_state": {"window_rows": int(len(arr)), "window_nan_values": int(np.isnan(arr).sum())}}

    def _planned(self, d, signal):
        def f(k):
            try:
                return _clean(float(signal.get(k)))
            except (TypeError, ValueError):
                return None
        d["planned_entry"], d["planned_stop"], d["planned_target"] = f("entry"), f("sl"), f("tp")
        if d["planned_entry"] is not None and d["planned_stop"] is not None:
            r = abs(d["planned_entry"] - d["planned_stop"])
            d["planned_risk_per_unit"] = r
            if d["planned_target"] is not None and r > 0:
                d["planned_reward_risk"] = abs(d["planned_target"] - d["planned_entry"]) / r

    def _write(self, d, status, code, stage, detail, link_order_id=None):
        from django.db import transaction
        from bot.journal.models import DecisionEvent, MarketSnapshot, TradeRecord
        T.validate_terminal(d["lifecycle_stages"], status, code, stage)
        snap = d.pop("_snapshot")
        for k in ("_path", "_disabled", "_signal_reason"):
            d.pop(k, None)
        prev = DecisionEvent.objects.filter(symbol=self.symbol, venue=self.venue, environment="DRY_RUN",
                                            candle_open_time=d["candle_open_time"]).exclude(run=self.run).order_by("recorded_at").first()
        with transaction.atomic():
            ev = DecisionEvent.objects.create(
                **d, run=self.run, environment="DRY_RUN", execution_mode="DRY_RUN", symbol=self.symbol,
                asset_class=self.run.asset_class, venue=self.venue, timeframe=self.timeframe, zones_hash=self.run.zones_hash,
                strategy_version=self.strategy_version, status=status, reason_code=code, reason_stage=stage,
                origin=T.ORIGIN_BY_STAGE.get(stage) if stage else None, reason_detail=detail,
                gate_order_version=T.LIVE_GATE_ORDER_VERSION, methodology_version=self.run.methodology_version,
                config_hash=self.run.config_hash, data_version="LIVE_STREAM", taxonomy_version=T.LIVE_TAXONOMY_VERSION,
                reevaluation_of=prev)
            MarketSnapshot.objects.create(decision=ev, snapshot_kind="POINT_IN_TIME_LIVE",
                                          feature_set_version=LIVE_FEATURE_SET_VERSION,
                                          information_time=d["information_time"], **snap)
            if link_order_id is not None:
                TradeRecord.objects.filter(symbol=self.symbol, venue=self.venue, order_id=link_order_id,
                                           decision_id__isnull=True).update(decision_id=ev.decision_id)
        self.coverage["EVALUATED"] += 1
        return ev
