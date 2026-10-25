# bot/research/lthp_study.py
"""LTHP-1 exploratory study runner.   # claude code changed: 2026-10-25 — new (LTHP_1_PREREGISTRATION_2026-10-25.md)

Runs the registered arms (A0 baseline, A1 STOP5, A3 HEDGE_HOLD, A4 HEDGE_REMOVE) through
fx_survival_replay.run_replay on GBP/JPY frozen at FX_RESEARCH_DATA_CUTOFF, under the registered margin models, capital
levels and one cost stress; computes the registered metrics and the monthly paired sign-flip test (Holm across the three
arm comparisons). EXPLORATORY: may not select or inform the full-ladder stop rule's K; no production use.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from bot.research import fx_survival_replay as R

IS_PAPER_ONLY = True
PROTOCOL_FILE = "LTHP_1_PREREGISTRATION_2026-10-25.md"
PROTOCOL_SHA256 = "42e517b1ac3663f5dd09ddaf33005d3876e514e543f2f326dd73f754ae74416a"
CAPITALS = (10_000.0, 5_000.0, 3_100.0)
PRIMARY_CAPITAL = 10_000.0
STRESS_COST = R.FxCostModel(spread_multiplier=2.0, slippage_pips=0.5, commission_per_lot_side_usd=3.5)
N_FLIPS, SEED = 10_000, 20261025

ARMS = {   # (label, policy) — margin model only matters for the hedge arms
    "A0_BASELINE": None,
    "A1_STOP5": R.HedgePolicy("STOP5"),
    "A3_HEDGE_HOLD_GROSS": R.HedgePolicy("HEDGE_HOLD", margin_model="GROSS"),
    "A3_HEDGE_HOLD_ONE_LEG": R.HedgePolicy("HEDGE_HOLD", margin_model="ONE_LEG"),
    "A4_HEDGE_REMOVE_GROSS": R.HedgePolicy("HEDGE_REMOVE", margin_model="GROSS"),
    "A4_HEDGE_REMOVE_ONE_LEG": R.HedgePolicy("HEDGE_REMOVE", margin_model="ONE_LEG"),
}
PRIMARY_COMPARISONS = ("A1_STOP5", "A3_HEDGE_HOLD_GROSS", "A4_HEDGE_REMOVE_GROSS")


def check_protocol(root: Path) -> str:
    h = hashlib.sha256((root / PROTOCOL_FILE).read_bytes()).hexdigest()
    if h != PROTOCOL_SHA256:
        raise RuntimeError(f"{PROTOCOL_FILE} changed after registration (sha256 {h[:12]}…) — this run is refused")
    return h


# ── statistics ────────────────────────────────────────────────────────────────────────────────────────────────────
def monthly_equity_change(rows: pd.DataFrame) -> pd.Series:
    """Mark-to-market equity change per calendar month (UTC), from the replay's equity rows."""
    eq = rows.set_index(pd.to_datetime(rows["timestamp"], utc=True))["equity"]
    month_end = eq.groupby(eq.index.strftime("%Y-%m")).last()
    prev = month_end.shift(1)
    prev.iloc[0] = eq.iloc[0]                                   # the first month is measured from the first row
    return month_end - prev


def sign_flip_p(d: Sequence[float], n_flips: int = N_FLIPS, seed: int = SEED) -> float:
    """One-sided p-value for H0: mean(d) <= 0 by random sign flips (exact under a symmetric null)."""
    d = np.asarray(d, dtype=np.float64)
    obs = d.mean()
    rng = np.random.default_rng(seed)
    flips = rng.choice((-1.0, 1.0), size=(n_flips, d.size))
    null = (flips * np.abs(d)).mean(axis=1)
    return float((1 + np.sum(null >= obs - 1e-12)) / (1 + n_flips))


def holm(pvals: Dict[str, float]) -> Dict[str, float]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m, out, running = len(items), {}, 0.0
    for k, (name, p) in enumerate(items):
        running = max(running, min(1.0, (m - k) * p))
        out[name] = running
    return out


def calibrate_sign_flip(n_months: int, rhos=(0.0, 0.3, 0.6), n_sims: int = 2000, alpha: float = 0.05, seed: int = 11) -> Dict:
    """Registered calibration: rejection rate on symmetric AR(1) null monthly series of length n_months."""
    rng = np.random.default_rng(seed)
    out = {}
    for rho in rhos:
        rej = 0
        for s in range(n_sims):
            e = rng.normal(0, np.sqrt(1 - rho ** 2), n_months)
            x = np.empty(n_months)
            x[0] = rng.normal()
            for t in range(1, n_months):
                x[t] = rho * x[t - 1] + e[t]
            rej += sign_flip_p(x, n_flips=1000, seed=seed + s) <= alpha
        out[str(rho)] = rej / n_sims
    return out


# ── metrics ───────────────────────────────────────────────────────────────────────────────────────────────────────
def arm_metrics(result: Dict) -> Dict:
    m = R.compute_metrics(result)
    cyc, rows, ev = result["cycles"], result["rows"], result["events"]
    types = ev["type"].value_counts().to_dict() if len(ev) else {}
    closed = cyc[cyc["outcome"] != R.OPEN_UNRESOLVED_AT_DATA_END]
    net_sorted = closed["net_pnl_usd"].sort_values(ascending=False)
    total_pos = float(net_sorted[net_sorted > 0].sum())
    years = closed.assign(y=pd.to_datetime(closed["exit_ts"], utc=True).dt.year).groupby("y")["net_pnl_usd"].sum()
    extra = {
        "full_ladder_events": int((cyc["fills"] >= 5).sum()),
        "cycles_by_fills": {int(k): int(v) for k, v in cyc["fills"].value_counts().sort_index().items()},
        "fifth_fill_stops": int((cyc["exit_reason"] == "FIFTH_FILL_STOP").sum()),
        "hedges_opened": int(types.get(R.HEDGE_OPENED, 0)), "hedges_rejected": int(types.get(R.HEDGE_REJECTED, 0)),
        "hedges_removed": int(types.get(R.HEDGE_REMOVED, 0)), "trigger_skipped_exit_pending": int(types.get(R.TRIGGER_SKIPPED_EXIT, 0)),
        "top5_share_of_positive_net_pct": float(net_sorted.head(5).clip(lower=0).sum() / total_pos * 100) if total_pos > 0 else None,
        "net_by_exit_year": {int(k): float(v) for k, v in years.items()},
        "worst_cycle_net_usd": float(closed["net_pnl_usd"].min()) if len(closed) else None,
    }
    if "hedge_realised_usd" in cyc.columns:
        extra.update({
            "hedge_realised_usd": float(cyc["hedge_realised_usd"].sum()), "hedge_spread_usd": float(cyc["hedge_spread_usd"].sum()),
            "hedge_slippage_usd": float(cyc["hedge_slippage_usd"].sum()), "hedge_commission_usd": float(cyc["hedge_commission_usd"].sum()),
            "hedge_swap_usd": float(cyc["hedge_swap_usd"].sum()),
            "max_gross_notional_usd": float(rows["gross_notional_usd"].max()), "max_net_notional_usd": float(rows["net_notional_usd"].max()),
        })
    else:
        extra.update({"max_gross_notional_usd": float(rows["open_notional_usd"].max()), "max_net_notional_usd": float(rows["open_notional_usd"].max())})
    unresolved = cyc[cyc["outcome"] == R.OPEN_UNRESOLVED_AT_DATA_END]
    extra["unresolved_at_end"] = [{k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in rec.items()
                                   if k in ("cycle_id", "fills", "lots", "net_pnl_usd", "mtm_unrealised_usd", "hedge_state")}
                                  for rec in unresolved.to_dict("records")]
    keep = ("ending_equity", "net_pnl", "net_return_pct", "max_equity_drawdown_pct", "max_drawdown_usd",
            "max_drawdown_intrabar_usd", "max_drawdown_intrabar_pct", "longest_drawdown_hours", "max_margin_used_usd",
            "min_margin_level_pct", "margin_call_events", "liquidation_count", "insolvent", "capital_insufficiency_events",
            "worst_floating_loss_usd", "completed_cycles", "unresolved_cycles", "swap_net", "costs")
    return {**{k: m[k] for k in keep}, **extra}


def run_lthp_study(root: Path, out_dir: Optional[Path] = None, capitals: Sequence[float] = CAPITALS) -> Dict:
    t0 = datetime.now(timezone.utc)
    proto = check_protocol(root)
    pair = R.build_pair_data("GBP/JPY")
    runs: Dict[str, Dict] = {}
    monthly: Dict[str, pd.Series] = {}
    for cap in capitals:
        for name, pol in ARMS.items():
            res = R.run_replay([pair], R.AccountConfig(starting_capital_usd=cap), hedge_policy=pol, label=f"{name}_{int(cap)}")
            key = f"{name}@{int(cap)}"
            runs[key] = arm_metrics(res)
            if cap == PRIMARY_CAPITAL:
                monthly[name] = monthly_equity_change(res["rows"])
    stress = {}
    for name in ("A0_BASELINE",) + PRIMARY_COMPARISONS:
        res = R.run_replay([pair], R.AccountConfig(starting_capital_usd=PRIMARY_CAPITAL), STRESS_COST, hedge_policy=ARMS[name],
                           label=f"{name}_stress")
        stress[name] = arm_metrics(res)
    base_m = monthly["A0_BASELINE"]
    n_months = int(len(base_m))
    calibration = calibrate_sign_flip(n_months)
    tests, raw_p = {}, {}
    for name in PRIMARY_COMPARISONS:
        d = (monthly[name] - base_m).dropna()
        p = sign_flip_p(d.to_numpy())
        raw_p[name] = p
        tests[name] = {"months": int(len(d)), "mean_monthly_diff_usd": float(d.mean()), "total_diff_usd": float(d.sum()),
                       "months_better": int((d > 0).sum()), "months_worse": int((d < 0).sum()), "months_equal": int((d == 0).sum()),
                       "p_one_sided": p}
    adj = holm(raw_p)
    for name in PRIMARY_COMPARISONS:
        tests[name]["p_holm"] = adj[name]
    report = {
        "kind": "LTHP_1_EXPLORATORY", "classification": "EXPLORATORY — pseudo-holdout history; nominal p-values are descriptive",
        "binding_limits": "may not select or inform K of FULL_LADDER_STOP_RULE_PRE_REGISTRATION_2026-10-18; no production use",
        "protocol": {"file": PROTOCOL_FILE, "sha256": proto},
        "generated_at": t0.isoformat(), "runtime_s": round((datetime.now(timezone.utc) - t0).total_seconds(), 1),
        "data": pair.provenance, "cost_primary": R.FxCostModel().labels, "cost_stress": STRESS_COST.labels,
        "runs": runs, "stress_at_10000_gross": stress,
        "statistics": {"unit": "calendar month", "n_months": n_months, "calibration_rejection_rate_at_0.05": calibration,
                       "calibration_ok": calibration["0.3"] <= 0.07, "tests_primary_10000_gross": tests},
    }
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"lthp1_report_{t0.strftime('%Y%m%dT%H%M%SZ')}.json"
        with open(path, "x", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, default=str)
        report["path"] = str(path)
    return report
