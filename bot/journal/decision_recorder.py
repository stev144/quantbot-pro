# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/journal/decision_recorder.py
#
# claude code changed: new file — 2026-10-07, Trading Decision Journal Phase 1 (backtester-only recorder).
# Owner sign-off: TRADING_DECISION_JOURNAL_PHASE1_OWNER_SIGNOFF_2026-10-07.md.
#
# BacktestDecisionRecorder OBSERVES bot.backtesting.backtester.Backtester through a handful of hooks and records
# what the backtester ACTUALLY evaluated (OD-J13 backtester half): every genuine evaluation point becomes one
# DecisionEvent (NO_SIGNAL included); warm-up, in-position, exit-candle and pre-loop candles are coverage only
# (D8), never decisions. It never writes to the backtester, the router, the strategies, the signal dicts or the
# data, and every hook is called through Backtester._journal(), which swallows recorder errors — so turning the
# recorder on cannot change a single trade (proved by the ON/OFF equivalence tests).
#
# Failure semantics (OD-J7a, D4 #15): a raw reason with no mapping → JournalGap MAPPING_FAILURE; any recorder
# exception → JournalGap RECORDER_FAILURE, later hooks become no-ops; either way the run closes INCOMPLETE with
# the reasons listed, and an explicit error is logged. A run is CLOSED only when nothing went wrong.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from __future__ import annotations

import hashlib
import logging
import math
import uuid
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from bot.journal import taxonomy as T

logger = logging.getLogger(__name__)

DECISION_NAMESPACE = uuid.UUID("8f7c1d2e-4b6a-5c3d-9e1f-0a2b3c4d5e6f")   # fixed: decision ids are reproducible
OHLCV = ("open", "high", "low", "close", "volume")
REGIME_FIELDS = ("regime", "confidence", "adx", "atr_ratio", "ema_spread_pct", "bb_width_pct", "adx_trending",
                 "volatility_extreme")
STRATEGY_OUTPUT_FIELDS = ("rsi", "structure", "trend", "ema_alignment", "pullback", "momentum")
FINANCING_STATE = {"modelled": False, "reason": "the backtester applies no financing, funding or borrow cost"}
LIQUIDITY_STATE = {"modelled": False, "reason": "the backtester has no spread, depth or volume constraint"}


def _utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")   # tz-naive is UTC (QBP-DATAV1)


def _clean(v):
    """JSON-safe scalar (numpy → python); NaN/inf → None. Non-scalars are not snapshot values."""
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if v is None or isinstance(v, str):
        return v
    return None


class RecorderDefect(RuntimeError):
    """An internal inconsistency the recorder refuses to paper over (becomes a RECORDER_FAILURE gap)."""


class BacktestDecisionRecorder:
    def __init__(self, symbol: str, *, timeframe: str = "1h", environment: str = T.PHASE1_ENVIRONMENT):
        if environment not in T.ENVIRONMENTS:
            raise ValueError(f"environment must be one of {T.ENVIRONMENTS}")
        self.symbol = symbol
        self.timeframe = timeframe
        self.step = pd.Timedelta(timeframe)
        self.environment = environment
        self.run = None
        self.failed: Optional[dict] = None
        self.closed = False
        self._decisions: List[dict] = []
        self._snapshots: List[dict] = []
        self._gaps: List[dict] = []
        self._pending: Optional[dict] = None
        self._coverage: List[Optional[str]] = []
        self._warmup_counts: Dict[str, int] = {}
        self._warmup_open = {"TREND": True, "RANGING": True}
        self._trade_seq = 0
        self._seen_candles = set()

    # ── run start ───────────────────────────────────────────────────────────────────────────────────────
    def on_run_start(self, bt, regimes, loop_start: int) -> None:
        from bot.journal.models import JournalRun, MODE_RESEARCH, JOURNAL_RUN_OPEN
        from bot.quant_core.market_profile import asset_class_for_symbol, profile_for_asset_class
        from bot.quant_core.provenance import (RESEARCH_ENGINE_METHODOLOGY, code_state, config_hash, data_version,
                                               make_run_id)
        from bot.quant_core.research_zones import ZONES, ZONES_VERSION
        self.bt, self.loop_start, self._regimes = bt, loop_start, regimes
        df = bt.df
        self.n = len(df)
        self.idx = pd.DatetimeIndex([_utc(t) for t in df.index])
        self.cols = [c for c in OHLCV if c in df.columns]
        self.arr = df[self.cols].to_numpy(dtype="float64")
        self.idx_ns = self.idx.asi8
        self._coverage = [T.COV_PRE_LOOP if i < loop_start else None for i in range(self.n)]
        self._mr_prev_candle_unreachable = getattr(bt.router.reversion_strategy, "min_candles", 0) >= 2
        self.code = code_state()
        self.asset_class = asset_class_for_symbol(self.symbol)
        try:
            profile_hash = profile_for_asset_class(self.asset_class).profile_hash()
        except Exception:
            profile_hash = ""
        self.config = self._config(bt, loop_start)
        self.config_hash = config_hash(self.config)
        frame = df[self.cols].copy()
        frame.index = self.idx
        self.dv = data_version({self.symbol: frame}, timeframe=self.timeframe, columns=self.cols)
        self.zones, self.zones_hash = ZONES, ZONES.zones_hash()
        self.strategy_version = "UNVERSIONED@" + (self.code.commit or "NO_GIT")[:12] + (
            f"+dirty:{self.code.dirty_digest[:12]}" if self.code.dirty else "")
        self.methodology = RESEARCH_ENGINE_METHODOLOGY
        started = pd.Timestamp.now(tz="UTC")
        self.run_id = "BT-" + make_run_id(symbol=self.symbol, data=self.dv.data_version, config=self.config_hash,
                                          commit=self.code.commit, dirty=self.code.dirty_digest,
                                          started=started.isoformat())[:32]
        self.run = JournalRun.objects.create(
            run_id=self.run_id, environment=self.environment, execution_mode=MODE_RESEARCH, symbol=self.symbol,
            asset_class=self.asset_class, venue=bt.venue_id, timeframe=self.timeframe, status=JOURNAL_RUN_OPEN,
            started_at=started.to_pydatetime(), code_commit=self.code.commit, code_dirty=self.code.dirty,
            dirty_digest=self.code.dirty_digest, methodology_version=self.methodology, protocol_hash="",
            config_hash=self.config_hash, config=self.config, data_version=self.dv.data_version,
            data_version_detail=self.dv.as_dict(), zones_version=ZONES_VERSION, zones_hash=self.zones_hash,
            market_profile_hash=profile_hash, strategy_set="StrategyRouter[MovingAverageStrategy,MeanReversionStrategy]",
            strategy_version=self.strategy_version, taxonomy_version=T.TAXONOMY_VERSION,
            schema_version=T.SCHEMA_VERSION, gate_order_version=T.GATE_ORDER_VERSION,
            feature_set_version=T.FEATURE_SET_VERSION, coverage_version=T.COVERAGE_VERSION,
            evidence_status="DESCRIPTIVE — non-canonical engine, never evidence")

    def on_empty_run(self, bt, loop_start: int) -> None:
        """The backtester returned early (fewer candles than its loop start): a run with zero evaluation points."""
        self.on_run_start(bt, [], loop_start)

    @staticmethod
    def _config(bt, loop_start) -> dict:
        from bot.backtesting.backtester import INDICATOR_LOOKBACK_CANDLES
        r = bt.router
        scalars = lambda o: {k: v for k, v in sorted(vars(o).items()) if isinstance(v, (bool, int, float, str))}
        return {"engine": "bot.backtesting.backtester.Backtester", "loop_start_index": loop_start,
                "indicator_lookback_candles": INDICATOR_LOOKBACK_CANDLES, "initial_balance": bt.initial_balance,
                "venue_id": bt.venue_id, "fee_rate": bt.fee_rate, "slippage_rate": bt.slippage_rate,
                "router": {"allow_longs": r.allow_longs, "allow_shorts": r.allow_shorts,
                           "require_high_confidence": r.require_high_confidence,
                           "min_adx_for_trend": r.min_adx_for_trend,
                           "allow_unvalidated_strategies": r.allow_unvalidated_strategies},
                "position_sizer": scalars(bt.position_sizer),
                "moving_average": scalars(r.trend_strategy), "mean_reversion": scalars(r.reversion_strategy)}

    # ── hooks ───────────────────────────────────────────────────────────────────────────────────────────
    def on_not_evaluated(self, i: int, category: str) -> None:
        if self.failed:
            return
        if category not in (T.COV_IN_POSITION, T.COV_EXIT_CANDLE, T.COV_ENTRY_PENDING):
            raise RecorderDefect(f"unknown non-evaluation category {category}")
        self._set_coverage(i, category)

    def on_evaluation(self, i: int, current_df, regime_result, signal, disposition: str) -> None:
        if self.failed:
            return
        if current_df.index[-1] != self.bt.df.index[i] or len(current_df) > i + 1:
            raise RecorderDefect(f"candle {i}: the strategy window does not end at the decision candle")
        regime = getattr(regime_result, "regime", None)
        raw = signal.get("reason") if isinstance(signal, dict) else None
        try:
            path = T.path_for_regime(regime)
            if disposition in ("INVALID_OUTPUT", "INVALID_SIGNAL_VALUE"):
                mapped, raw = T.map_invalid_router_output(), "invalid_signal"
            elif disposition == "NO_SIGNAL":
                if path == "RANGING" and raw == "not_enough_data" and not self._mr_prev_candle_unreachable:
                    raise T.UnmappedReason("MR not_enough_data is ambiguous while min_candles < 2")
                mapped = T.map_no_signal_reason(raw, regime, warmup_open=self._warmup_open.get(path, False))
            elif disposition == "CANDIDATE":
                mapped = None
            else:
                raise RecorderDefect(f"unknown disposition {disposition}")
        except T.UnmappedReason as e:
            self._set_coverage(i, T.COV_MAPPING_FAILURE)
            self._gap("MAPPING_FAILURE", i, raw or "", {"error": str(e), "regime": regime, "disposition": disposition})
            return
        if isinstance(mapped, T.Warmup):
            self._set_coverage(i, T.COV_WARMUP)
            self._warmup_counts[mapped.raw] = self._warmup_counts.get(mapped.raw, 0) + 1
            return
        if disposition == "CANDIDATE" or (mapped is not None and (mapped.candidate or mapped.gate in T.MA_GATES
                                                                    or mapped.gate in T.MR_GATES)):
            self._warmup_open[path] = False      # the strategy produced a non-warm-up result: warm-up is over
        d = self._draft(i, current_df, regime_result, signal, path, raw)
        disabled = () if self.bt.router.require_high_confidence else (T.G_CONF,)
        if disposition == "CANDIDATE":
            d["raw_reason"], d["_signal_reason"] = "", str(signal.get("reason", ""))[:200]   # a signal reason, not a refusal
            d["direction"] = "LONG" if signal["signal"] == "BUY" else "SHORT"
            d["lifecycle_stages"] = [T.EVALUATED, T.SIGNAL_GENERATED]
            d["execution_eligible"] = True
            d["gate_vector"] = T.gate_vector(path, failed=None, passed_through=T.G_WELL_FORMED, disabled=disabled)
            d["_path"], d["_disabled"] = path, disabled
            self._planned(d, signal)
            if self._pending is not None:
                raise RecorderDefect("a second candidate while one is pending")
            self._pending = d
            self._set_coverage(i, T.COV_EVALUATED)
            return
        d["direction"] = mapped.direction
        d["lifecycle_stages"] = [T.EVALUATED, T.SIGNAL_GENERATED, T.GATES_EVALUATED] if mapped.candidate else [T.EVALUATED]
        d["gate_vector"] = T.gate_vector(path, failed=mapped.gate, disabled=disabled)
        self._terminal(d, mapped.status, mapped.code, mapped.stage, mapped.detail)
        self._set_coverage(i, T.COV_EVALUATED)

    def on_fill(self, i: int, trade, refusal: Optional[str]) -> None:
        if self.failed:
            return
        d, self._pending = self._pending, None
        if d is None:
            raise RecorderDefect(f"candle {i}: a fill without a pending decision")
        if i != d["candle_index"] + 1:
            raise RecorderDefect(f"fill at candle {i} for a decision at {d['candle_index']}: not the next open")
        path, disabled = d.pop("_path"), d.pop("_disabled")
        if trade is not None:
            if getattr(trade, "entry_index", None) != i:
                raise RecorderDefect(f"trade entry_index {getattr(trade, 'entry_index', None)} != fill candle {i}")
            self._trade_seq += 1
            d["backtest_trade_seq"] = self._trade_seq
            d["backtest_trade_id"] = f"{self.run_id}:T{self._trade_seq:06d}"
            d["fill_candle_index"] = i
            d["lifecycle_stages"] += [T.GATES_EVALUATED, T.EXECUTION_ATTEMPTED]
            d["gate_vector"] = T.gate_vector(path, failed=None, passed_through=T.FILL_GATES[-1], disabled=disabled)
            self._terminal(d, T.EXECUTED, None, None, {})
            return
        try:
            mapped = T.map_fill_refusal(refusal, d["direction"])
        except T.UnmappedReason as e:
            self._gap("MAPPING_FAILURE", d["candle_index"], refusal or "", {"error": str(e), "stage": "fill"})
            return                              # the decision is not written (its snapshot is filtered at close)
        d["lifecycle_stages"] += [T.GATES_EVALUATED]
        d["gate_vector"] = T.gate_vector(path, failed=mapped.gate, disabled=disabled)
        d["raw_reason"] = refusal
        self._terminal(d, mapped.status, mapped.code, mapped.stage, mapped.detail)

    def on_censored_end_of_data(self) -> None:
        if self.failed:
            return
        d, self._pending = self._pending, None
        if d is None:
            raise RecorderDefect("censoring without a pending decision")
        d.pop("_path"), d.pop("_disabled")
        self._terminal(d, T.CENSORED_END_OF_DATA, None, None, {"why": "no next open in the data (D5)"})

    def record_failure(self, hook: str, exc: BaseException) -> None:
        if self.failed is None:
            self.failed = {"hook": hook, "error": f"{type(exc).__name__}: {exc}"}
            logger.error(f"[DecisionJournal] recorder failure in {hook}: {exc} — run will close INCOMPLETE; "
                         f"the backtest result is unaffected")

    # ── close ───────────────────────────────────────────────────────────────────────────────────────────
    def close(self, results: dict) -> None:
        from django.db import transaction
        from bot.journal.models import (DecisionEvent, JournalGap, MarketSnapshot, JOURNAL_RUN_CLOSED,
                                        JOURNAL_RUN_INCOMPLETE)
        if self.closed:
            raise RecorderDefect("close() called twice")
        self.closed = True
        if self.run is None:
            logger.error("[DecisionJournal] no JournalRun could be created — nothing recorded for this backtest")
            return
        reasons = []
        if not self.failed:
            try:
                self._verify_close(results)
            except Exception as e:
                self.record_failure("close", e)
        if self.failed:
            self._gap("RECORDER_FAILURE", None, "", dict(self.failed))
        if self._gaps:
            reasons = sorted({g["kind"] for g in self._gaps})
        written = {d["decision_id"] for d in self._decisions}
        snapshots = [s for s in self._snapshots if s["decision_id"] in written]   # never an orphan snapshot
        try:
            with transaction.atomic():
                DecisionEvent.objects.bulk_create([DecisionEvent(run=self.run, **d) for d in self._decisions],
                                                  batch_size=1000)
                MarketSnapshot.objects.bulk_create([MarketSnapshot(**s) for s in snapshots], batch_size=1000)
                JournalGap.objects.bulk_create([JournalGap(run=self.run, **g) for g in self._gaps])
                self.run.status = JOURNAL_RUN_INCOMPLETE if reasons else JOURNAL_RUN_CLOSED
                self.run.incomplete_reasons = reasons
                self.run.coverage = self._coverage_summary()
                self.run.n_decisions = len(self._decisions)
                self.run.closed_at = pd.Timestamp.now(tz="UTC").to_pydatetime()
                self.run.save()
        except Exception as e:
            logger.error(f"[DecisionJournal] persisting run {self.run_id} failed: {e} — marking INCOMPLETE")
            self.run.refresh_from_db()
            if self.run.status == "OPEN":
                self.run.status, self.run.incomplete_reasons = JOURNAL_RUN_INCOMPLETE, ["PERSIST_FAILURE"]
                self.run.closed_at = pd.Timestamp.now(tz="UTC").to_pydatetime()
                self.run.save()
        if reasons:
            logger.error(f"[DecisionJournal] run {self.run_id} closed INCOMPLETE: {reasons}")

    def _verify_close(self, results: dict) -> None:
        if self._pending is not None:
            raise RecorderDefect("a pending decision was never resolved")
        for d in self._decisions:
            T.validate_terminal(d["lifecycle_stages"], d["status"], d["reason_code"], d["reason_stage"])
        missing = [i for i, c in enumerate(self._coverage) if c is None]
        if missing:
            raise RecorderDefect(f"{len(missing)} candles have no coverage category (first {missing[:5]})")
        trades = list(results.get("trades") or [])
        executed = sorted((d for d in self._decisions if d["status"] == T.EXECUTED), key=lambda d: d["backtest_trade_seq"])
        if len(executed) != len(trades):
            raise RecorderDefect(f"{len(executed)} EXECUTED decisions vs {len(trades)} backtest trades")
        for d, t in zip(executed, trades):
            if t.get("entry_index") != d["fill_candle_index"]:
                raise RecorderDefect(f"trade {d['backtest_trade_id']}: entry_index {t.get('entry_index')} != "
                                     f"fill candle {d['fill_candle_index']}")

    def _coverage_summary(self) -> dict:
        counts = {c: 0 for c in T.COVERAGE_CATEGORIES}
        intervals, start = [], 0
        for i, c in enumerate(self._coverage):
            counts[c if c is not None else "UNASSIGNED"] = counts.get(c if c is not None else "UNASSIGNED", 0) + 1
            if i == len(self._coverage) - 1 or self._coverage[i + 1] != c:
                intervals.append([c, start, i, self.idx[start].isoformat(), self.idx[i].isoformat()])
                start = i + 1
        missing_grid = []
        d = np.diff(self.idx_ns) // int(self.step.value) if self.n > 1 else np.array([], dtype="int64")
        for k in np.nonzero(d > 1)[0]:
            missing_grid.append([(self.idx[k] + self.step).isoformat(), int(d[k] - 1)])
        return {"version": T.COVERAGE_VERSION, "n_candles": self.n, "loop_start_index": self.loop_start,
                "counts": counts, "intervals": intervals, "warmup_raw_reasons": dict(sorted(self._warmup_counts.items())),
                "missing_grid_ranges": missing_grid, "n_missing_grid_candles": int(sum(m[1] for m in missing_grid)),
                "rows_dropped_by_backtester_prepare": int(len(self.bt.original_df) - self.n)}

    # ── helpers ─────────────────────────────────────────────────────────────────────────────────────────
    def _set_coverage(self, i, category):
        if self._coverage[i] is not None:
            raise RecorderDefect(f"candle {i} already has coverage {self._coverage[i]} (new {category})")
        self._coverage[i] = category

    def _gap(self, kind, i, raw, detail):
        self._gaps.append({"kind": kind, "candle_index": i,
                           "candle_open_time": self.idx[i].to_pydatetime() if i is not None else None,
                           "raw_reason": (raw or "")[:200], "detail": detail})

    def _draft(self, i, current_df, regime_result, signal, path, raw) -> dict:
        t_open = self.idx[i]
        t_info = t_open + self.step
        if (i, self.symbol) in self._seen_candles:
            raise RecorderDefect(f"candle {i} evaluated twice")
        self._seen_candles.add((i, self.symbol))
        ws = i + 1 - len(current_df)
        window = self.arr[ws:i + 1]
        h = hashlib.sha256()
        h.update(self.idx_ns[ws:i + 1].tobytes())
        h.update(np.ascontiguousarray(window).tobytes())
        decision_id = uuid.uuid5(DECISION_NAMESPACE, f"{self.run_id}|{self.symbol}|{t_open.isoformat()}")
        values, missing = {}, []
        for c, v in zip(self.cols, self.arr[i]):
            values[f"candle_{c}"] = _clean(v)
        for f in REGIME_FIELDS:
            values[f"regime_{f}"] = _clean(getattr(regime_result, f, None))
        if isinstance(signal, dict):
            for f in STRATEGY_OUTPUT_FIELDS:
                if f in signal:
                    values[f"strategy_{f}"] = _clean(signal[f])
        for k, v in list(values.items()):
            if v is None:
                missing.append(k)
        self._snapshots.append({
            "decision_id": decision_id, "snapshot_kind": "SIMULATED_CAUSAL", "feature_set_version": T.FEATURE_SET_VERSION,
            "information_time": t_info.to_pydatetime(), "window_start_time": self.idx[ws].to_pydatetime(),
            "window_end_time": t_open.to_pydatetime(), "window_rows": int(len(window)),
            "input_window_sha256": h.hexdigest(), "values": values,
            "available_at": {k: t_info.isoformat() for k in values}, "missing": missing})
        gaps_in_window = (np.diff(self.idx_ns[ws:i + 1]) // int(self.step.value)) - 1 if i > ws else np.array([0])
        return {
            "decision_id": decision_id, "environment": self.environment, "execution_mode": self.run.execution_mode,
            "symbol": self.symbol, "asset_class": self.asset_class, "venue": self.run.venue, "timeframe": self.timeframe,
            "candle_index": i, "candle_open_time": t_open.to_pydatetime(), "information_time": t_info.to_pydatetime(),
            "research_zone": self.zones.classify(t_open), "zones_hash": self.zones_hash,
            "strategy": T.STRATEGY_FOR_PATH[path], "strategy_version": self.strategy_version,
            "raw_reason": (raw or "")[:200], "gate_order_version": T.GATE_ORDER_VERSION,
            "risk_state": {"balance": _clean(self.bt.balance), "peak_balance": _clean(self.bt.peak_balance),
                           "max_drawdown_pct": _clean(self.bt.max_drawdown),
                           "consecutive_losses": _clean(self.bt.consecutive_losses)},
            "portfolio_state": {"open_position": False, "pending_entry": False},
            "liquidity_state": dict(LIQUIDITY_STATE), "financing_state": dict(FINANCING_STATE),
            "data_quality_state": {"window_rows": int(len(window)), "window_nan_values": int(np.isnan(window).sum()),
                                   "max_missing_grid_candles_in_window": int(max(0, gaps_in_window.max()))},
            "methodology_version": self.methodology, "protocol_hash": "", "config_hash": self.config_hash,
            "data_version": self.dv.data_version, "taxonomy_version": T.TAXONOMY_VERSION,
        }

    def _planned(self, d, signal):
        def f(k):
            try:
                return _clean(float(signal.get(k)))
            except (TypeError, ValueError):
                return None
        entry, stop, target = f("entry"), f("sl"), f("tp")
        d["planned_entry"], d["planned_stop"], d["planned_target"] = entry, stop, target
        if entry is not None and stop is not None:
            risk = abs(entry - stop)
            d["planned_risk_per_unit"] = risk
            if target is not None and risk > 0:
                d["planned_reward_risk"] = abs(target - entry) / risk
        d["estimated_cost"] = {"fee_rate_per_side": _clean(self.bt.fee_rate),
                               "slippage_rate_per_side": _clean(self.bt.slippage_rate),
                               "basis": "fraction of notional per side, as the backtester applies them",
                               "source": f"bot.config.execution_costs.get_venue_execution_costs({self.run.venue!r})"}

    def _terminal(self, d, status, code, stage, detail):
        d["status"], d["reason_code"], d["reason_stage"] = status, code, stage
        d["origin"] = T.ORIGIN_BY_STAGE.get(stage) if stage else None
        d["reason_detail"] = dict(detail or {})
        if "_signal_reason" in d:
            d["reason_detail"]["signal_reason"] = d.pop("_signal_reason")
        T.validate_terminal(d["lifecycle_stages"], status, code, stage)
        self._decisions.append(d)
