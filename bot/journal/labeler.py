# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/journal/labeler.py
#
# claude code changed: new file — 2026-10-07, Decision Journal PHASE 2. Owner-delegated rules (sign-off record):
#   OD-J2  counterfactual rule — signal at the close of t, fill at the OPEN of t+1 (D-1), the decision's planned
#          SL/TP, a gap through a level at a bar's OPEN exits at that open, same-bar stop+target = stop first
#          (baseline, an ASSUMPTION) with target-first stored as the bound, time exit after H = 24 bars (at the
#          close of the 24th bar counted from the fill bar), venue fee + slippage from execution_costs, shorts
#          financed from recorded perpetual funding (missing coverage -> COST_UNAVAILABLE, never 0).
#   OD-J3  forward returns at h = 1, 4, 24 (bot.quant_core.timing.research_forward_return, D-1 contract).
#   OD-J4  a label whose window reaches the sealed holdout F is SEALED with no values.
#   OD-J9  bar-resolution MAE/MFE with AND without the exit bar.
#   OD-J17 ambiguity: path_resolution / ambiguity / outcome_assumption on every rule label.
# Labels are POST-decision outcomes, physically separate from real P&L (no TradeRecord link). The labeler refuses
# to run when the price file no longer reproduces the run's recorded data_version (G5), and labels only on a
# complete hourly grid: any missing candle inside a label window -> UNTESTABLE (no fill, no interpolation).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

LABEL_VERSION = "QBP-JRNL-LABEL-1"
METRICS_VERSION = "QBP-JRNL-PTM-1"
FORWARD_HORIZONS = (1, 4, 24)          # OD-J3
RULE_MAX_HOLD = 24                     # OD-J2 H
OHLC = ("open", "high", "low", "close")


class LabelerRefused(RuntimeError):
    """The labeler will not write labels (provenance mismatch, labels already exist, run not closed, ...)."""


def rule_id(fee_rate: float, slippage_rate: float) -> str:
    spec = {"version": LABEL_VERSION, "fill": "OPEN_T_PLUS_1", "levels": "PLANNED_SL_TP", "gap": "EXIT_AT_OPEN",
            "same_bar": "STOP_FIRST_BASELINE+TARGET_FIRST_BOUND", "max_hold": RULE_MAX_HOLD, "time_exit": "CLOSE",
            "fee_rate": fee_rate, "slippage_rate": slippage_rate, "short_financing": "RECORDED_PERP_FUNDING"}
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


@dataclass
class PathOutcome:
    exit_index: int            # 0 = the fill bar
    exit_price: float
    exit_reason: str           # STOP | TARGET | GAP_STOP | GAP_TARGET | TIME
    ambiguity: str = "NONE"
    assumption: str = "RESOLVED"


def simulate_rule_path(o, h, l, c, direction: str, stop: float, target: float, max_hold: int = RULE_MAX_HOLD):
    """Pure function. Bars start at the fill bar (fill = o[0]). Returns (status, reason, [PathOutcome...]) where the
    list has ONE outcome when resolved and TWO (stop-first baseline, target-first bound) when ambiguous."""
    o, h, l, c = (np.asarray(x, dtype=float) for x in (o, h, l, c))
    if direction not in ("LONG", "SHORT"):
        return "UNTESTABLE", f"no direction ({direction!r})", []
    if any(v is None or not math.isfinite(v) for v in (stop, target)):
        return "UNTESTABLE", "no planned stop/target recorded", []
    n = len(o)
    if n == 0:
        return "CENSORED", "no bar after the decision", []
    if not np.isfinite(np.concatenate([o, h, l, c])).all():
        return "UNTESTABLE", "missing candle inside the label window", []
    entry = o[0]
    long = direction == "LONG"
    risk = (entry - stop) if long else (stop - entry)
    reward = (target - entry) if long else (entry - target)
    if risk <= 0:
        return "UNTESTABLE", "the stop is not beyond the fill price", []
    if reward <= 0:
        return "UNTESTABLE", "the target is not beyond the fill price", []
    for k in range(min(n, max_hold)):
        if k > 0:
            if (long and o[k] <= stop) or (not long and o[k] >= stop):
                return "OK", "", [PathOutcome(k, float(o[k]), "GAP_STOP")]
            if (long and o[k] >= target) or (not long and o[k] <= target):
                return "OK", "", [PathOutcome(k, float(o[k]), "GAP_TARGET")]
        hit_stop = (l[k] <= stop) if long else (h[k] >= stop)
        hit_tgt = (h[k] >= target) if long else (l[k] <= target)
        if hit_stop and hit_tgt:
            amb = "FILL_BAR_BOTH_TOUCHED" if k == 0 else "STOP_AND_TARGET_SAME_BAR"
            return "OK", "", [PathOutcome(k, float(stop), "STOP", amb, "STOP_FIRST_ASSUMED"),
                              PathOutcome(k, float(target), "TARGET", amb, "TARGET_FIRST_ASSUMED")]
        if hit_stop:
            return "OK", "", [PathOutcome(k, float(stop), "STOP")]
        if hit_tgt:
            return "OK", "", [PathOutcome(k, float(target), "TARGET")]
    if n < max_hold:
        return "CENSORED", f"only {n} of {max_hold} bars exist after the decision", []
    return "OK", "", [PathOutcome(max_hold - 1, float(c[max_hold - 1]), "TIME")]


def excursions_r(h, l, entry: float, risk: float, direction: str, exit_index: int):
    """Bar-resolution MAE/MFE in R over bars 0..exit_index, and over 0..exit_index-1 (None when empty)."""
    h, l = np.asarray(h, dtype=float), np.asarray(l, dtype=float)
    def ex(e):
        if e < 0:
            return None, None
        if direction == "LONG":
            return float((l[:e + 1].min() - entry) / risk), float((h[:e + 1].max() - entry) / risk)
        return float((entry - h[:e + 1].max()) / risk), float((entry - l[:e + 1].min()) / risk)
    mae, mfe = ex(exit_index)
    mae_x, mfe_x = ex(exit_index - 1)
    return mae, mfe, mae_x, mfe_x


def rule_r(entry: float, exit_price: float, stop: float, direction: str, fee_rate: float, slippage_rate: float,
           financing_per_unit: Optional[float]):
    """(gross_r, net_r, cost_detail). Slippage via bot.engines.simulation.apply_slippage (the backtester's own
    convention), fees on both sides' effective notional, financing per unit (None -> net unavailable)."""
    from bot.engines.simulation import apply_slippage
    sign = 1.0 if direction == "LONG" else -1.0
    risk = abs(entry - stop)
    gross = sign * (exit_price - entry) / risk
    e_in = apply_slippage(entry, direction, True, slippage_rate=slippage_rate)
    e_out = apply_slippage(exit_price, direction, False, slippage_rate=slippage_rate)
    fees = fee_rate * (e_in + e_out)
    detail = {"risk_per_unit": risk, "entry_effective": e_in, "exit_effective": e_out, "fees_per_unit": fees,
              "fee_rate": fee_rate, "slippage_rate": slippage_rate,
              "financing_per_unit": financing_per_unit if direction == "SHORT" else "NOT_APPLICABLE_LONG_SPOT"}
    if direction == "SHORT" and financing_per_unit is None:
        return gross, None, detail
    fin = financing_per_unit if direction == "SHORT" else 0.0
    net = (sign * (e_out - e_in) - fees - fin) / risk
    return gross, net, detail


# ── runner ──────────────────────────────────────────────────────────────────────────────────────────────────
def load_run_grid(run, ohlcv: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """The run's symbol on a COMPLETE hourly UTC grid (missing candles stay NaN). Refuses unless the rows of the
    run's recorded range reproduce its data_version exactly."""
    from bot.quant_core.provenance import data_version
    if ohlcv is None:
        from bot.instruments import resolve_ohlcv_path
        df = pd.read_csv(resolve_ohlcv_path(run.symbol.replace("_", "/", 1)), float_precision="round_trip")
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        ohlcv = df.set_index("timestamp")
    d = ohlcv.copy()
    d.index = pd.DatetimeIndex(d.index).tz_convert("UTC") if pd.DatetimeIndex(d.index).tz else pd.DatetimeIndex(d.index).tz_localize("UTC")
    d = d[~d.index.duplicated(keep="first")].sort_index()
    detail = run.data_version_detail or {}
    cols = list(detail.get("columns") or ["open", "high", "low", "close", "volume"])
    start, end = pd.Timestamp(detail["start"]), pd.Timestamp(detail["end"])
    window = d.loc[(d.index >= start) & (d.index <= end), cols].dropna(subset=["close", "high", "low"])
    dv = data_version({run.symbol: window}, timeframe=run.timeframe, columns=cols).data_version
    if dv != run.data_version:
        raise LabelerRefused(f"price data no longer reproduces run {run.run_id}'s data_version "
                             f"({dv[:12]} != {run.data_version[:12]}): labels would not describe the recorded run")
    grid = pd.date_range(start, d.index.max(), freq=pd.Timedelta(run.timeframe))
    return d.reindex(grid)[list(OHLC)]


def label_run(run, *, ohlcv: Optional[pd.DataFrame] = None) -> dict:
    """Write forward-return and counterfactual-rule labels for every decision of a CLOSED/INCOMPLETE run, plus
    nothing else. Insert-only: refuses when labels of this LABEL_VERSION already exist for the run."""
    from django.db import transaction
    from bot.config.execution_costs import get_venue_execution_costs
    from bot.journal.models import DecisionEvent, DecisionLabel
    from bot.quant_core.research_zones import ZONES
    from bot.quant_core.timing import RESEARCH_LABEL_CONTRACT, research_forward_return
    if run.status == "OPEN":
        raise LabelerRefused(f"run {run.run_id} is still OPEN")
    if DecisionLabel.objects.filter(run=run, label_version=LABEL_VERSION).exists():
        raise LabelerRefused(f"run {run.run_id} already has {LABEL_VERSION} labels (insert-only; bump the version)")
    grid = load_run_grid(run, ohlcv)
    costs = get_venue_execution_costs(run.venue)
    fee, slip = costs["fee_rate"], costs["slippage_rate"]
    rid = rule_id(fee, slip)
    step = pd.Timedelta(run.timeframe)
    pos = {t: i for i, t in enumerate(grid.index)}
    o, hi, lo, cl = (grid[c].to_numpy(float) for c in OHLC)
    fwd = {h: research_forward_return(grid, h, RESEARCH_LABEL_CONTRACT).to_numpy(float) for h in FORWARD_HORIZONS}
    funding = _funding(run.symbol)
    rows: List[DecisionLabel] = []
    counts = {}
    for d in DecisionEvent.objects.filter(run=run).order_by("candle_index"):
        t_open = pd.Timestamp(d.candle_open_time).tz_convert("UTC")
        i = pos.get(t_open)
        base = dict(decision=d, run=run, label_version=LABEL_VERSION, information_time=d.information_time,
                    label_window_start=d.information_time, research_zone=d.research_zone,
                    data_version=run.data_version)
        for h in FORWARD_HORIZONS:
            st, why, end, val = _forward_status(i, h, o, fwd[h], grid.index, step, ZONES)
            rows.append(DecisionLabel(**base, outcome_kind="FORWARD_RETURN_NOT_EXECUTED", label_type="FORWARD_RETURN",
                                      variant="NONE", horizon_candles=h, label_window_end=end, status=st,
                                      status_reason=why, window_end_zone=ZONES.classify(end) if end is not None else "",
                                      gross_return=val))
            counts[st] = counts.get(st, 0) + 1
        if d.direction in ("LONG", "SHORT"):
            rows.extend(_rule_rows(d, i, base, o, hi, lo, cl, grid.index, step, fee, slip, rid, funding, ZONES, counts))
    with transaction.atomic():
        DecisionLabel.objects.bulk_create(rows, batch_size=1000)
    return {"run_id": run.run_id, "labels": len(rows), "by_status": counts, "label_version": LABEL_VERSION, "rule_id": rid}


def _funding(symbol):
    from bot.quant_core.funding import load_funding_series
    try:
        return load_funding_series(symbol)
    except Exception:
        return None


def _forward_status(i, h, o, fwd, index, step, zones):
    if i is None:
        return "UNTESTABLE", "decision candle not on the price grid", None, None
    last = i + 1 + h
    if last >= len(o):
        return "CENSORED", f"open of t+1+{h} not in the data yet", None, None
    end = index[last]
    if end >= zones.F:
        return "SEALED", "label window reaches the sealed forward holdout (OD-J4)", end.to_pydatetime(), None
    if not np.isfinite(o[i + 1:last + 1]).all():
        return "UNTESTABLE", "missing candle inside the label window", end.to_pydatetime(), None
    return "OK", "", end.to_pydatetime(), float(fwd[i])


def _rule_rows(d, i, base, o, hi, lo, cl, index, step, fee, slip, rid, funding, zones, counts):
    from bot.journal.models import DecisionLabel
    common = dict(**base, outcome_kind="COUNTERFACTUAL_NOT_EXECUTED", label_type="COUNTERFACTUAL_RULE",
                  rule_id=rid, horizon_candles=RULE_MAX_HOLD)
    def one(status, why, variant="BASELINE_STOP_FIRST", end=None, **vals):
        counts[status] = counts.get(status, 0) + 1
        return DecisionLabel(**common, variant=variant, status=status, status_reason=why, label_window_end=end,
                             window_end_zone=zones.classify(end) if end is not None else "", **vals)
    if i is None:
        return [one("UNTESTABLE", "decision candle not on the price grid")]
    seg = slice(i + 1, min(i + 1 + RULE_MAX_HOLD, len(o)))
    window_end = index[min(i + RULE_MAX_HOLD, len(o) - 1)] if i + 1 < len(o) else None
    if window_end is not None and window_end >= zones.F:
        return [one("SEALED", "label window reaches the sealed forward holdout (OD-J4)", end=window_end.to_pydatetime())]
    status, why, outs = simulate_rule_path(o[seg], hi[seg], lo[seg], cl[seg], d.direction,
                                           d.planned_stop if d.planned_stop is not None else float("nan"),
                                           d.planned_target if d.planned_target is not None else float("nan"))
    if status != "OK":
        return [one(status, why, end=window_end.to_pydatetime() if window_end is not None else None)]
    rows = []
    entry = float(o[i + 1])
    risk = abs(entry - d.planned_stop)
    fill_time = index[i + 1]
    for out in outs:
        exit_time = index[i + 1 + out.exit_index]
        fin = None
        if d.direction == "SHORT":
            fin = _financing(funding, cl, index, i + 1, i + 1 + out.exit_index)
        gross, net, detail = rule_r(entry, out.exit_price, d.planned_stop, d.direction, fee, slip, fin)
        mae, mfe, mae_x, mfe_x = excursions_r(hi[seg], lo[seg], entry, risk, d.direction, out.exit_index)
        st = "OK" if net is not None else "COST_UNAVAILABLE"
        variant = "BOUND_TARGET_FIRST" if out.assumption == "TARGET_FIRST_ASSUMED" else "BASELINE_STOP_FIRST"
        rows.append(one(st, "" if st == "OK" else "short financing not covered by recorded funding",
                        variant=variant, end=exit_time.to_pydatetime(), fill_time=fill_time.to_pydatetime(),
                        fill_price=entry, exit_time=exit_time.to_pydatetime(), exit_price=out.exit_price,
                        exit_reason=out.exit_reason, gross_r=gross, net_r=net, cost_detail=detail,
                        mae_bar_1h_r=mae, mfe_bar_1h_r=mfe, mae_excl_exit_bar_r=mae_x, mfe_excl_exit_bar_r=mfe_x,
                        bars_to_exit=out.exit_index + 1, ambiguity=out.ambiguity, outcome_assumption=out.assumption))
    return rows


def _financing(funding, closes, index, i_fill, i_exit):
    """Funding cost of ONE unit short from the fill (exclusive) to the exit (inclusive); None when not covered."""
    from bot.quant_core.funding import funding_cost
    if funding is None:
        return None
    prices = pd.Series(closes[i_fill:i_exit + 1], index=index[i_fill:i_exit + 1]).dropna()
    fc = funding_cost(1.0, prices, funding, index[i_fill], index[i_exit])
    return fc.cost if fc.complete else None


# ── post-trade metrics for EXECUTED simulated trades (called by the recorder at close) ─────────────────────────
def post_trade_metrics(trade: dict, decision_close: float, arr_open, arr_high, arr_low) -> dict:
    """Derived metrics of one backtest trade dict (entry_price/exit_price/sl/direction/entry_index/exit_index).
    Bar-resolution only (OD-J9): the exit bar's extreme may occur after the actual exit."""
    direction = trade["direction"]
    entry, sl = float(trade["entry_price"]), float(trade["sl"])
    e0, e1 = int(trade["entry_index"]), int(trade["exit_index"])
    risk = abs(entry - sl)
    out = {"realized_risk_per_unit": risk, "bars_held": e1 - e0 + 1}
    sign = 1.0 if direction == "LONG" else -1.0
    out["implementation_shortfall_bps"] = sign * (entry - decision_close) / decision_close * 1e4
    out["fill_vs_rule_reference_bps"] = sign * (entry - float(arr_open[e0])) / float(arr_open[e0]) * 1e4
    if risk <= 0:
        out.update(status="UNTESTABLE", status_reason="zero realized risk")
        return out
    hseg, lseg = np.asarray(arr_high[e0:e1 + 1], float), np.asarray(arr_low[e0:e1 + 1], float)
    mae, mfe, mae_x, mfe_x = excursions_r(hseg, lseg, entry, risk, direction, e1 - e0)
    adverse = (lseg - entry) / risk if direction == "LONG" else (entry - hseg) / risk
    favour = (hseg - entry) / risk if direction == "LONG" else (entry - lseg) / risk
    out.update(status="OK", mae_bar_1h_r=mae, mfe_bar_1h_r=mfe, mae_excl_exit_bar_r=mae_x, mfe_excl_exit_bar_r=mfe_x,
               bars_to_mae=int(np.argmin(adverse)) + 1, bars_to_mfe=int(np.argmax(favour)) + 1)
    return out
