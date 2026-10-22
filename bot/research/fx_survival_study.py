# bot/research/fx_survival_study.py
"""
DCA capital-survival study: baseline reproduction, step-by-step decomposition, fixed-capital replays,
shared-account portfolio, stress suite and historical event windows.   # claude code changed: 2026-10-21 — new file

Writes one reproducible report (JSON + CSV ledgers) to research_data/dca_survival/<cutoff date>/ that the
/execution/dca-engine/ page reads. Nothing here trades, tunes or selects parameters: every scenario runs the
unchanged QC-1 strategy; stress parameters are fixed in STRESS_SCENARIOS with their rationale.
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import bot.research.fx_survival_replay as R

OUT_ROOT = R._ROOT / "research_data" / "dca_survival"
EVENTS_FILE = Path(__file__).with_name("fx_market_events.json")   # version-controlled sourced dataset (data/ is gitignored)
PRIMARY_PAIR = "GBP/JPY"   # what /execution/dca-engine/ shows
CAPITAL_LEVELS = (3_100.0, 5_000.0, 10_000.0)   # owner decision 2026-10-21: full-ladder margin, $5k, $10k
PORTFOLIO_PAIRS = ("EUR/USD", "EUR/JPY", "GBP/JPY", "EUR/CAD", "NZD/CHF")   # QC_PORTFOLIO_SPECS as of 2026-10-21
STRESS_SEED = 20261021

# (key, label, class, rationale, cost-model overrides, strategy overrides, data transform)
STRESS_SCENARIOS = [
    ("spread_x2", "Spread x2", "hypothetical", "MT5 stores one (usually the lowest) spread per bar; x2 approximates the average spread actually paid.", {"spread_multiplier": 2.0}, {}, None),
    ("spread_x3", "Spread x3", "hypothetical", "Stressed-liquidity spreads (news, rollover hour).", {"spread_multiplier": 3.0}, {}, None),
    ("spread_floor_live", "Spread floor = today's live spread", "hypothetical", "Bars recording 0 spread are implausible for a retail fill; floor every bar at the snapshot's current spread.", {"spread_floor_points": "LIVE"}, {}, None),
    ("slip_0.5", "Slippage 0.5 pip / fill", "hypothetical", "No slippage evidence exists for this account; 0.5 pip each side is a common retail market-order estimate.", {"slippage_pips": 0.5}, {}, None),
    ("slip_1.0", "Slippage 1.0 pip / fill", "hypothetical", "Adverse execution during fast markets.", {"slippage_pips": 1.0}, {}, None),
    ("comm_3.5", "Commission $3.5 / lot / side", "hypothetical", "Commission is unavailable from MT5; the recorded spreads look raw (ECN-like), and raw-spread accounts typically charge a per-lot commission. Value is an illustrative level, not this broker's schedule.", {"commission_per_lot_side_usd": 3.5}, {}, None),
    ("comm_7.0", "Commission $7 / lot / side", "hypothetical", "Upper illustrative commission level.", {"commission_per_lot_side_usd": 7.0}, {}, None),
    ("no_swap", "No swap credit", "hypothetical", "Swap uses today's broker rates for all history; GBP/JPY long is currently a CREDIT. Removing it shows how much of the result depends on that estimate.", {"swap_source": "NONE"}, {}, None),
    ("all_costs_realistic", "Spread x2 + 0.5 pip slip + $3.5 commission, no swap", "hypothetical", "Combined plausible-retail cost case.", {"spread_multiplier": 2.0, "slippage_pips": 0.5, "commission_per_lot_side_usd": 3.5, "swap_source": "NONE"}, {}, None),
    ("delay_1bar", "Execution delayed 1 bar", "hypothetical", "Delayed candles / execution latency: every decision fills one hour later.", {}, {"execution_delay_bars": 1}, None),
    ("missing_2pct", "2 % of candles missing", "hypothetical", "Random (seeded) data gaps.", {}, {}, ("drop", 0.02)),
    ("gap_-3pct", "Hypothetical -3 % gap at peak exposure", "hypothetical", "Instantaneous adverse gap at the bar where the baseline run carried its largest position.", {}, {}, ("gap", 0.03)),
    ("gap_-5pct", "Hypothetical -5 % gap at peak exposure", "hypothetical", "As above, -5 % (the size of the Sep-2022 / Aug-2024 GBP/JPY moves).", {}, {}, ("gap", 0.05)),
]


def _metric_subset(m: Dict) -> Dict:
    keys = ("starting_capital", "ending_balance", "ending_equity", "net_pnl", "net_return_pct", "gross_trading_pnl_all",
            "gross_return_pct", "cost_drag_pp", "swap_net", "max_drawdown_usd", "max_equity_drawdown_pct",
            "max_drawdown_intrabar_usd", "max_drawdown_intrabar_pct", "liquidation_count", "insolvent", "capital_insufficiency_events",
            "margin_call_events", "ladder_exhaustion_count", "completed_cycles", "unresolved_cycles",
            "worst_floating_loss_usd", "worst_realised_cycle_loss_usd", "min_free_margin_usd", "min_margin_level_pct",
            "win_rate_before_costs_pct", "win_rate_after_costs_pct", "net_profit_factor", "gross_profit_factor",
            "cost_to_gross_profit_pct", "cost_pct_of_notional")
    out = {k: m.get(k) for k in keys}
    out["total_costs"] = m["costs"]["total"]
    return out


def _dd_pct_of_peak_pnl(cum_pnl) -> Optional[float]:
    """Max drawdown as % of the running peak of cumulative P&L (used where there is no account / capital base)."""
    x = np.asarray(cum_pnl, float)
    if not len(x):
        return None
    peak = np.maximum.accumulate(np.maximum(x, 0.0))
    i = int(np.argmin(x - peak))
    return float((x[i] - peak[i]) / peak[i] * 100) if peak[i] > 0 else None


def _clone(p: R.PairData, **arrays) -> R.PairData:
    return dataclasses.replace(p, **arrays)


def _drop_bars(p: R.PairData, frac: float, seed: int) -> R.PairData:
    rng = np.random.default_rng(seed)
    keep = rng.random(len(p.ts)) >= frac
    keep[0] = keep[-1] = True
    sl = {k: getattr(p, k)[keep] for k in ("o", "h", "l", "c", "atr", "spread_px", "q2usd_open", "q2usd_close", "b2usd_open", "b2usd_close")}
    return _clone(p, ts=p.ts[keep], **sl)


def _gap_at(p: R.PairData, k: int, g: float) -> R.PairData:
    f = np.ones(len(p.ts))
    f[k:] = 1.0 - g
    return _clone(p, o=p.o * f, h=p.h * f, l=p.l * f, c=p.c * f)


def _daily_series(res: Dict, price: Optional[R.PairData] = None) -> Dict:
    rows = res["rows"].set_index("timestamp")
    start = res["starting_capital"]
    day = rows.resample("1D")
    d = pd.DataFrame({
        "equity": day["equity"].last(), "balance": day["balance"].last(),
        "equity_low": day["equity_intrabar_worst"].min(), "margin_used": day["margin_used"].max(),
        "free_margin": day["free_margin"].min(), "open_notional": day["open_notional_usd"].max(),
        "costs": (day["cum_spread"].last() + day["cum_slippage"].last() + day["cum_commission"].last()),
        "swap": day["cum_swap"].last(), "net": day["cum_net_pnl"].last(),
    }).dropna(subset=["equity"])
    peak = np.maximum.accumulate(np.maximum(d["equity"].to_numpy(), start))
    d["peak"] = peak
    d["dd_pct"] = (d["equity"] - peak) / peak * 100
    d["gross"] = d["net"] + d["costs"] - d["swap"]
    out = {"d": [t.strftime("%Y-%m-%d") for t in d.index]}
    for col in d.columns:
        out[col] = [round(float(v), 2) for v in d[col]]
    if price is not None:
        px = pd.Series(price.c, index=price.ts).resample("1D").last().reindex(d.index).ffill()
        out["price"] = [round(float(v), 3) for v in px]
    return out


def _markers(res: Dict) -> List[Dict]:
    ev, cyc = res["events"], res["cycles"]
    out = []
    keep = {R.LIQUIDATED, R.DCA_LADDER_EXHAUSTED, R.MARGIN_CALL, R.BANKRUPTCY_OR_INSOLVENCY, R.INSUFFICIENT_FREE_CASH,
            R.OPEN_UNRESOLVED_AT_DATA_END}
    if len(ev):
        for _, e in ev[ev["type"].isin(keep)].iterrows():
            out.append({"t": pd.Timestamp(e["timestamp"]).strftime("%Y-%m-%d %H:%M"), "type": e["type"],
                        "pair": e.get("pair"), "cycle_id": None if pd.isna(e.get("cycle_id")) else int(e["cycle_id"])})
    if len(cyc):
        big = cyc[(cyc["outcome"] == R.LOSING_EXIT) & (cyc["net_pnl_usd"] < -500)]
        for _, c in big.iterrows():
            out.append({"t": pd.Timestamp(c["exit_ts"]).strftime("%Y-%m-%d %H:%M"), "type": "LOSING_EXIT_GT_500",
                        "pair": c["pair"], "cycle_id": int(c["cycle_id"])})
    return out


def _event_windows(res: Dict, pairs: List[R.PairData], events: List[Dict], pre_h: int = 24, post_h: int = 72) -> List[Dict]:
    rows = res["rows"].set_index("timestamp")
    cyc, evs = res["cycles"], res["events"]
    out = []
    for e in events:
        t0 = pd.Timestamp(e["start"]) - pd.Timedelta(hours=pre_h)
        t1 = pd.Timestamp(e["end"]) + pd.Timedelta(hours=post_h)
        for p in pairs:
            ccys = {p.base, p.quote}
            if not (set(e["assets"]) & ccys or "global_risk" in e["assets"]):
                continue
            m = (p.ts >= t0) & (p.ts <= t1)
            n = int(m.sum())
            rec = {"event_id": e["id"], "title": e["title"], "category": e["category"], "pair": p.pair,
                   "window": [str(t0), str(t1)], "bars": n,
                   "expected_bars_approx": int((t1 - t0).total_seconds() // 3600)}
            if n < 2:
                rec["coverage"] = "OUTSIDE_REPLAY_OR_NO_DATA"
                out.append(rec)
                continue
            o, l, h, c = p.o[m], p.l[m], p.h[m], p.c[m]
            lr = np.diff(np.log(c))
            rec.update({
                "measured_return_pct": float((c[-1] / o[0] - 1) * 100),
                "measured_hourly_vol_pct": float(lr.std() * 100) if len(lr) > 1 else None,
                "max_adverse_move_long_pct": float((l.min() / o[0] - 1) * 100),
                "max_favourable_move_long_pct": float((h.max() / o[0] - 1) * 100),
            })
            r = rows.loc[(rows.index >= t0) & (rows.index <= t1)]
            rec["account_max_open_notional_usd"] = float(r["open_notional_usd"].max()) if len(r) else None
            rec["account_worst_unrealised_usd"] = float(r["unrealised"].min()) if len(r) else None
            rec["account_min_equity_usd"] = float(r["equity_intrabar_worst"].min()) if len(r) else None
            if len(cyc):
                cc = cyc[cyc["pair"] == p.pair]
                ent = pd.to_datetime(cc["entry_ts"], utc=True)
                ex = pd.to_datetime(cc["exit_ts"], utc=True).fillna(pd.Timestamp.max.tz_localize("UTC"))
                overl = cc[(ent <= t1) & (ex >= t0)]
                rec["max_ladder_fills"] = int(overl["fills"].max()) if len(overl) else 0
                exits_in = cc[(pd.to_datetime(cc["exit_ts"], utc=True) >= t0) & (pd.to_datetime(cc["exit_ts"], utc=True) <= t1)]
                rec["realised_losses_usd"] = float(exits_in.loc[exits_in["net_pnl_usd"] < 0, "net_pnl_usd"].sum())
            if len(evs):
                ee = evs[(pd.to_datetime(evs["timestamp"], utc=True) >= t0) & (pd.to_datetime(evs["timestamp"], utc=True) <= t1)]
                ee = ee[(ee["pair"] == p.pair) | ee["pair"].isna()]
                rec["coincided_with"] = sorted(set(ee["type"]) & {R.LIQUIDATED, R.DCA_LADDER_EXHAUSTED, R.MARGIN_CALL,
                                                                    R.INSUFFICIENT_FREE_CASH, R.BANKRUPTCY_OR_INSOLVENCY})
            rec["coverage"] = "OK" if n >= 0.5 * rec["expected_bars_approx"] * (5 / 7) else "PARTIAL"
            out.append(rec)
    return out


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    return o


def _save_run(res: Dict, folder: Path, key: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("rows", "ledger", "cycles", "fills", "events"):
        res[name].to_csv(folder / f"{key}_{name}.csv", index=False)


def run_study(out_root: Path = OUT_ROOT, verbose: bool = True) -> Path:
    t_start = time.time()
    log = (lambda *a: print(*a, flush=True)) if verbose else (lambda *a: None)
    cutoff = R.FX_RESEARCH_DATA_CUTOFF
    out = out_root / cutoff.strftime("%Y-%m-%d_%H%M")
    out.mkdir(parents=True, exist_ok=True)
    events = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))["events"]   # claude code changed: 2026-10-21 — UTF-8 (Windows default cp1252 garbled em-dashes)
    snapshot, snap_path = R.latest_broker_snapshot()

    log(f"[1/6] loading {PRIMARY_PAIR} (cutoff {cutoff})")
    primary = R.build_pair_data(PRIMARY_PAIR, cutoff=cutoff, snapshot=snapshot)

    log("[2/6] legacy page baseline + decomposition")
    from bot.views.dca_engine import _build_results
    legacy = _build_results(cutoff=cutoff)["pairs"][PRIMARY_PAIR]
    legacy_dd_pct = _dd_pct_of_peak_pnl([pt["p"] for pt in legacy["equity"]])
    no_cost = R.FxCostModel(spread_source="NONE", swap_source="NONE")
    huge = 1e9
    steps = [
        ("S0", "Legacy page (folds reset, $6 x 500 display scale, zero costs, closed trades only)", None),
        ("S1", "Continuous replay, unlimited capital, exact $3,000 notional, zero costs", (R.AccountConfig(huge, fractional_lots=True), no_cost)),
        ("S2", "+ broker lot sizing (0.01-lot grid, 1:2:4:8:16 of executed entry)", (R.AccountConfig(huge), no_cost)),
        ("S3", "+ MT5 spread history", (R.AccountConfig(huge), R.FxCostModel(swap_source="NONE"))),
        ("S4", "+ swap (broker's current rates)", (R.AccountConfig(huge), R.FxCostModel())),
    ]
    decomposition = [{"step": "S0", "label": steps[0][1], "net_pnl": legacy["stats"]["total_pnl"],
                      "max_drawdown_usd": legacy["stats"]["max_dd"], "cycles": legacy["stats"]["total_trades"],
                      "win_rate_pct": legacy["stats"]["win_pct"], "drawdown_basis": "closed trades",
                      "max_drawdown_pct": legacy_dd_pct, "drawdown_pct_basis": "% of peak cumulative P&L",
                      "liquidations": "not modelled"}]
    for key, label, (acct, cost) in steps[1:]:
        res = R.run_replay([primary], acct, cost, label=key)
        m = R.compute_metrics(res)
        decomposition.append({"step": key, "label": label, "net_pnl": m["net_pnl"], "max_drawdown_usd": m["max_drawdown_usd"],
                              "max_drawdown_intrabar_usd": m["max_drawdown_intrabar_usd"],
                              "max_drawdown_pct": _dd_pct_of_peak_pnl(res["rows"]["equity"] - res["starting_capital"]),
                              "max_drawdown_intrabar_pct": _dd_pct_of_peak_pnl(np.minimum(res["rows"]["equity_intrabar_worst"], res["rows"]["equity"]) - res["starting_capital"]),
                              "drawdown_pct_basis": "% of peak cumulative P&L (unlimited capital — no account base)",
                              "cycles": m["completed_cycles"] + m["unresolved_cycles"], "win_rate_pct": m["win_rate_after_costs_pct"],
                              "drawdown_basis": "mark-to-market equity", "liquidations": "not applicable (unlimited capital)",
                              "worst_floating_loss_usd": m["worst_floating_loss_usd"], "costs_total": m["costs"]["total"],
                              "swap_net": m["swap_net"]})

    log("[3/6] fixed-capital replays")
    capital_runs = {}
    peak_bar = None
    for cap in CAPITAL_LEVELS:
        res = R.run_replay([primary], R.AccountConfig(cap), R.FxCostModel(), label=f"cap{int(cap)}")
        m = R.compute_metrics(res)
        key = f"gbpjpy_{int(cap)}"
        _save_run(res, out / "runs", key)
        liq = res["cycles"][res["cycles"]["outcome"] == R.LIQUIDATED] if len(res["cycles"]) else res["cycles"]
        failures = res["cycles"][(res["cycles"]["outcome"].isin([R.LIQUIDATED, R.OPEN_UNRESOLVED_AT_DATA_END])) |
                                 (res["cycles"]["net_pnl_usd"] < -500)] if len(res["cycles"]) else res["cycles"]
        capital_runs[key] = {
            "capital": cap, "metrics": m, "series": _daily_series(res, primary), "markers": _markers(res),
            "cycles": res["cycles"].to_dict("records"), "failures": failures.to_dict("records"),
            "liquidations": liq.to_dict("records"),
            "event_windows": _event_windows(res, [primary], events),
        }
        if cap == 5_000.0:
            peak_bar = int(np.argmax(res["rows"]["open_notional_usd"].to_numpy()))
        log(f"   ${cap:,.0f}: equity {m['ending_equity']:,.2f}  liquidations {m['liquidation_count']}  insolvent {m['insolvent']}")

    log("[4/6] stress suite")
    live_floor = float(snapshot["symbols"][PRIMARY_PAIR]["spread"])
    stress = []
    for key, label, klass, why, cost_kw, strat_kw, transform in STRESS_SCENARIOS:
        ckw = {k: (live_floor if v == "LIVE" else v) for k, v in cost_kw.items()}
        cost = dataclasses.replace(R.FxCostModel(), **ckw)
        strat = dataclasses.replace(R.StrategyConfig(), **strat_kw)
        data = primary
        detail = {}
        if transform and transform[0] == "drop":
            data = _drop_bars(primary, transform[1], STRESS_SEED)
            detail = {"bars_dropped": int(len(primary.ts) - len(data.ts)), "seed": STRESS_SEED}
        elif transform and transform[0] == "gap":
            data = _gap_at(primary, peak_bar, transform[1])
            detail = {"gap_bar": str(primary.ts[peak_bar]), "gap_pct": -transform[1] * 100}
        per_cap = {}
        for cap in CAPITAL_LEVELS:
            res = R.run_replay([data], R.AccountConfig(cap), cost, strat, label=key)
            m = R.compute_metrics(res)
            per_cap[str(int(cap))] = _metric_subset(m)
            per_cap[str(int(cap))]["net_pnl_pct_of_capital"] = m["net_pnl"] / cap * 100
        stress.append({"key": key, "label": label, "class": klass, "rationale": why, "cost": dataclasses.asdict(cost),
                       "strategy_overrides": strat_kw, "detail": detail, "results": per_cap})
        log(f"   {label}: " + ", ".join(f"${c}: {v['ending_equity']:,.0f}{' LIQ' * v['liquidation_count']}" for c, v in per_cap.items()))

    log("[5/6] shared-account portfolio (5 QC pairs)")
    port_data = [primary] + [R.build_pair_data(p, cutoff=cutoff, snapshot=snapshot) for p in PORTFOLIO_PAIRS if p != PRIMARY_PAIR]
    portfolio = {}
    for cap in CAPITAL_LEVELS:
        res = R.run_replay(port_data, R.AccountConfig(cap), R.FxCostModel(), label=f"portfolio{int(cap)}")
        m = R.compute_metrics(res)
        _save_run(res, out / "runs", f"portfolio_{int(cap)}")
        portfolio[str(int(cap))] = {"metrics": _metric_subset(m), "by_pair": m["by_pair"], "series": _daily_series(res),
                                    "max_simultaneous_open_cycles": m["max_simultaneous_open_cycles"],
                                    "event_windows": _event_windows(res, port_data, events), "markers": _markers(res)}
        log(f"   ${cap:,.0f}: equity {m['ending_equity']:,.2f}  liquidations {m['liquidation_count']}  insolvent {m['insolvent']}")

    log("[6/6] writing report")
    report = {
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(), "runtime_s": round(time.time() - t_start, 1),
        "is_paper_only": R.IS_PAPER_ONLY, "cutoff": str(cutoff),
        "primary_pair": PRIMARY_PAIR, "capital_levels": list(CAPITAL_LEVELS),
        "strategy": dataclasses.asdict(R.StrategyConfig()), "cost_labels": R.FxCostModel().labels,
        "account_rules": {"close_out_level": R.ESMA_CLOSE_OUT_LEVEL, "leverage": {p.pair: R.esma_leverage(p.pair) for p in port_data},
                          "margin_call_level": 1.0, "negative_balance_protection": True,
                          "broker_account": snapshot.get("account"), "broker_snapshot_file": snap_path.name},
        "provenance": {p.pair: p.provenance for p in port_data},
        "legacy_baseline": legacy["stats"], "decomposition": decomposition,
        "capital_runs": capital_runs, "stress": stress, "portfolio": portfolio,
        "events": events,
    }
    path = out / "report.json"
    path.write_text(json.dumps(_clean(report), default=str))
    (out_root / "LATEST").write_text(out.name)
    log(f"done in {time.time() - t_start:.0f}s -> {path}")
    return path


def load_latest_report(out_root: Path = OUT_ROOT) -> Optional[Dict]:
    marker = out_root / "LATEST"
    if not marker.exists():
        return None
    path = out_root / marker.read_text().strip() / "report.json"
    return json.loads(path.read_text()) if path.exists() else None
