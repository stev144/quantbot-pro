# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/journal/analytics.py
#
# claude code changed: new file — 2026-10-07, Decision Journal PHASE 4 (descriptive analytics). Owner decisions:
#   OD-J14 (iii)  opportunity episodes break at a trade exit; EVERY table also reports the never-break count.
#   OD-J12        display_min_episodes = 30 — a DISPLAY threshold only (suppressed cells are counted, never "valid").
#   OD-J16        labels are read only through read_labels(): zone S by default; zone V only inside a registered,
#                 open family (logged in the hypothesis registry); never zone F / unassigned; never legacy runs.
#   V-1 (ruled 2026-10-07)  zone V is NOT independent of the backtester strategies -> V is refused for them.
#   OD-J17        rule results reported as a [stop-first baseline, target-first bound] interval + ambiguous share.
# Everything here is DESCRIPTIVE: means and dependence-aware intervals (time-block bootstrap, D-10 block length);
# no p-values, no claims, no ladder level above OBSERVATION.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from __future__ import annotations

from collections import OrderedDict
from typing import Dict, Optional

import pandas as pd

from bot.journal import taxonomy as T

EPISODE_RULE_VERSION = "QBP-JRNL-EPISODE-1"
DISPLAY_MIN_EPISODES = 30            # OD-J12 — display only
DEFAULT_ZONES = ("SELECTION",)
# V-1: zone V cannot confirm anything about these strategy sets (their design post-dates most of V)
V_NOT_INDEPENDENT_STRATEGY_SETS = ("StrategyRouter[MovingAverageStrategy,MeanReversionStrategy]",)
NON_BREAKING_GAPS = (T.COV_IN_POSITION, T.COV_EXIT_CANDLE)   # what the never-break rule looks through
GROUPINGS = ("status", "reason_code", "direction", "hour_utc", "weekday", "regime")
LABELS = {"rule": ("COUNTERFACTUAL_RULE", 24), "fwd1": ("FORWARD_RETURN", 1), "fwd4": ("FORWARD_RETURN", 4),
          "fwd24": ("FORWARD_RETURN", 24)}


class ZoneReadRefused(PermissionError):
    """A journal label read that the research-zone rules (OD-J16, V-1, D-8) do not allow."""


# ── episodes (OD-J14 iii) ────────────────────────────────────────────────────────────────────────────────────
def coverage_by_index(run) -> Dict[int, str]:
    out = {}
    for cat, a, b, *_ in (run.coverage or {}).get("intervals", []):
        for i in range(a, b + 1):
            out[i] = cat
    return out


def assign_episodes(decisions, coverage: Dict[int, str], step: pd.Timedelta):
    """decisions: candle_index-ordered iterable of objects with candle_index, candle_open_time, direction.
    Returns ({decision_id: episode_no} break-at-exit, {decision_id: episode_no} never-break). Only decisions with a
    candidate direction belong to an episode. Both rules break on a direction change, on an evaluation point without
    a same-direction candidate, and on a missing-data gap; break-at-exit ALSO breaks on any non-evaluated candle,
    never-break looks through in-position / exit-candle stretches only (the backtester did not evaluate there, so
    continuity is unobserved — hence both counts are always reported)."""
    brk, nev = {}, {}
    prev = None
    eb = en = 0
    for d in decisions:
        if d.direction not in ("LONG", "SHORT"):
            prev = None
            continue
        new_b = new_n = True
        if prev is not None and prev.direction == d.direction:
            idx_gap = d.candle_index - prev.candle_index
            time_ok = (pd.Timestamp(d.candle_open_time) - pd.Timestamp(prev.candle_open_time)) == idx_gap * step
            between = [coverage.get(i) for i in range(prev.candle_index + 1, d.candle_index)]
            if time_ok and idx_gap == 1:
                new_b = new_n = False
            elif time_ok and between and all(c in NON_BREAKING_GAPS for c in between):
                new_n = False
        if new_b:
            eb += 1
        if new_n:
            en += 1
        brk[d.decision_id], nev[d.decision_id] = eb, en
        prev = d
    return brk, nev


# ── the guarded label reader (OD-J16 + V-1) ────────────────────────────────────────────────────────────────
def read_labels(run, *, zones=DEFAULT_ZONES, confirmatory: Optional[dict] = None, registry=None):
    """The ONLY way journal analytics reads labels. confirmatory = {"program", "quarter", "purpose"} is required
    for zone V and is logged in the hypothesis registry; F and unassigned are never readable here."""
    from bot.journal.models import DecisionLabel
    zones = tuple(zones)
    if run.taxonomy_version == T.LEGACY_TAXONOMY_VERSION:
        raise ZoneReadRefused("legacy run: incomplete population (no denominator) — never analysed")
    bad = [z for z in zones if z not in ("SELECTION", "VALIDATION")]
    if bad:
        raise ZoneReadRefused(f"zones {bad} are never readable by journal analytics (F only through the D-8 ledger)")
    qs = DecisionLabel.objects.filter(run=run, research_zone__in=zones)
    if "VALIDATION" in zones:
        if run.strategy_set in V_NOT_INDEPENDENT_STRATEGY_SETS:
            raise ZoneReadRefused(f"V-1: zone V is not independent of {run.strategy_set}; it cannot confirm anything about it")
        if not confirmatory:
            raise ZoneReadRefused("zone V labels need a registered, open family (OD-J16)")
        from bot.quant_core.hypothesis_registry import HypothesisRegistry, RegistryError
        reg = registry or HypothesisRegistry()
        try:
            reg.log_journal_validation_read(confirmatory["program"], confirmatory["quarter"], run_id=run.run_id,
                                            purpose=confirmatory.get("purpose", ""),
                                            n_rows=qs.filter(research_zone="VALIDATION").count())
        except RegistryError as e:
            raise ZoneReadRefused(str(e))
    return qs


# ── descriptive outcome table ─────────────────────────────────────────────────────────────────────────────
def outcome_table(run, *, by: str = "status", label: str = "rule", zones=DEFAULT_ZONES, confirmatory=None,
                  registry=None, n_boot: int = 999) -> dict:
    from bot.journal.models import DecisionEvent, MarketSnapshot
    from bot.quant_core.inference import d10_block_length, stable_seed, time_block_bootstrap_mean_ci
    if by not in GROUPINGS:
        raise ValueError(f"by must be one of {GROUPINGS}")
    if label not in LABELS:
        raise ValueError(f"label must be one of {tuple(LABELS)}")
    label_type, horizon = LABELS[label]
    labels = read_labels(run, zones=zones, confirmatory=confirmatory, registry=registry).filter(
        label_type=label_type, horizon_candles=horizon)
    base = {l.decision_id: l for l in labels if l.variant in ("NONE", "BASELINE_STOP_FIRST")}
    bound = {l.decision_id: l for l in labels if l.variant == "BOUND_TARGET_FIRST"}
    decs = list(DecisionEvent.objects.filter(run=run).order_by("candle_index"))
    regimes = dict(MarketSnapshot.objects.filter(decision__run=run).values_list("decision_id", "values__regime_regime"))
    brk, nev = assign_episodes(decs, coverage_by_index(run), pd.Timedelta(run.timeframe))
    value_field = "net_r" if label_type == "COUNTERFACTUAL_RULE" else "gross_return"
    groups: Dict[str, list] = OrderedDict()
    excluded = {"not_ok": 0, "no_label": 0}
    for d in decs:
        lab = base.get(d.decision_id)
        if lab is None:
            excluded["no_label"] += 1
            continue
        if lab.status != "OK" or getattr(lab, value_field) is None:
            excluded["not_ok"] += 1
            continue
        t = pd.Timestamp(d.candle_open_time)
        key = {"status": d.status, "reason_code": d.reason_code or f"({d.status})", "direction": d.direction or "(none)",
               "hour_utc": f"{t.hour:02d}", "weekday": t.day_name(), "regime": regimes.get(d.decision_id) or "?"}[by]
        b = bound.get(d.decision_id)
        groups.setdefault(key, []).append({"t": t, "v": getattr(lab, value_field), "ep_b": brk.get(d.decision_id),
                                           "ep_n": nev.get(d.decision_id),
                                           "vb": getattr(b, value_field) if b is not None else getattr(lab, value_field),
                                           "amb": lab.ambiguity != "NONE"})
    rows = []
    block = d10_block_length(horizon)
    for key, items in groups.items():
        # unit = episode (first decision of each break-at-exit episode); non-candidate decisions are their own unit
        seen, unit = set(), []
        for x in items:
            ep = x["ep_b"]
            if ep is None or ep not in seen:
                unit.append(x)
                if ep is not None:
                    seen.add(ep)
        n_eps_never = len({x["ep_n"] for x in items if x["ep_n"] is not None}) + sum(1 for x in items if x["ep_n"] is None)
        row = {"group": key, "n_decisions": len(items), "n_units": len(unit), "n_episodes_never_break": n_eps_never,
               "ambiguous_share": (sum(x["amb"] for x in unit) / len(unit)) if unit else 0.0}
        if len(unit) < DISPLAY_MIN_EPISODES:
            row.update(suppressed=True)
        else:
            vals = [x["v"] for x in unit]
            ci = time_block_bootstrap_mean_ci(vals, [x["t"] for x in unit], block_hours=block, n_boot=n_boot,
                                              seed=stable_seed(run.run_id, by, label, key))
            row.update(suppressed=False, mean=ci["mean"], ci_ok=ci["ok"], ci_lo=ci.get("lo"), ci_hi=ci.get("hi"),
                       ci_reason=ci.get("reason", ""), n_blocks=ci["n_blocks"])
            if label_type == "COUNTERFACTUAL_RULE":
                row["mean_target_first_bound"] = float(sum(x["vb"] for x in unit) / len(unit))
        rows.append(row)
    rows.sort(key=lambda r: -r["n_units"])
    return {"run_id": run.run_id, "by": by, "label": label, "label_type": label_type, "horizon": horizon,
            "value": value_field, "zones": list(zones), "rows": rows, "excluded": excluded,
            "n_suppressed": sum(1 for r in rows if r.get("suppressed")), "display_min_episodes": DISPLAY_MIN_EPISODES,
            "episode_rule": EPISODE_RULE_VERSION, "block_hours": block,
            "status": "DESCRIPTIVE — exploratory (zone S); not a test, not a claim; ladder level at most OBSERVATION"}
