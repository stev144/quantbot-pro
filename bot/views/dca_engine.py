# bot/views/dca_engine.py  # claude code changed: new — QC-1 DCA Engine dashboard page
"""
Django view for the Forex DCA Engine walk-forward results page.

Runs QC-1 (FX-QC-PROTOCOL-1) on GBP/JPY LONG only (AUD/CAD dropped after
holdout evaluation returned negative P&L), 5 walk-forward folds.
Results are cached 1 hour — the simulation takes ~15 s on first load.

Permutation-test summary (N=499, block=48h, post-hoc) is a baked-in constant;
it does not re-run on page load.
"""  # claude code changed: AUD/CAD dropped — holdout negative; GBP/JPY solo

import json
import logging

import numpy as np
import pandas as pd
from django.core.cache import cache
from django.shortcuts import render
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT      = Path(__file__).resolve().parents[2]
_CACHE_KEY = "dca_engine_results_v8"  # claude code changed: v8 — 2026-10-21 frozen data cutoff (was v7: LCEX-1 5-fill cap)
_CACHE_TTL = 3600  # 1 hour

_PERM_RESULT = {
    "p_value":       0.094,
    "beats_pct":     90.8,
    "n_perm":        499,
    "block_h":       48,
    "alpha":         0.05,
    "significant":   False,
    "post_hoc_warn": True,
}

# QC-1 calibration runs at $6/cycle (notional, no leverage).
# Display applies $100 margin × 1:30 leverage = $3,000 notional per entry.
# Scale factor = 3000/6 = 500. Win rates and fill distributions unchanged.
_QC1_ENTRY_USDT     = 6.0    # claude code changed: QC-1 calibration constant (do not change)
_DISPLAY_MARGIN     = 100.0  # claude code changed: margin per cycle entry
_LEVERAGE           = 30.0   # claude code changed: 1:30 EU retail max; matches ForexInstrumentSpec.leverage_max
_DISPLAY_NOTIONAL   = _DISPLAY_MARGIN * _LEVERAGE   # $3,000 per entry
_DISPLAY_SCALE      = _DISPLAY_NOTIONAL / _QC1_ENTRY_USDT  # 500.0

_PROTOCOL = {
    "id":             "FX-QC-PROTOCOL-1",
    "initial_usdt":   int(_DISPLAY_MARGIN),      # claude code changed: $100 margin per entry
    "leverage":       int(_LEVERAGE),            # claude code changed: 1:30
    "notional":       int(_DISPLAY_NOTIONAL),    # claude code changed: $3,000 notional per entry
    "max_cycle_h":    72,
    "profit_act_pct": 0.5,
    "trailing_pct":   0.2,
    "mae_horizon":    6,
    "ladder_levels":  5,                       # claude code changed: LCEX-1 — 5 fills (was 7)
    "multipliers":    "1 · 2 · 4 · 8 · 16×",  # claude code changed: LCEX-1 — 5-element (was 7)
    "folds":          5,
    "embargo_bars":   48,
    "max_margin":     3100,    # claude code changed: LCEX-1 — $100 × (1+2+4+8+16) = $3,100 margin (was $12,700)
    "max_notional":   93000,   # claude code changed: LCEX-1 — $3,100 × 30 = $93,000 notional (was $381,000)
}

# Cumulative NOTIONAL deployed at each ladder level ($100 margin × 30 leverage)
_LEVEL_CAPITAL = [3000, 9000, 21000, 45000, 93000]  # claude code changed: LCEX-1 — 5 levels (was 7)


# ── data build (single pass per pair per fold) ────────────────────────────────

def _build_results(cutoff=None):  # claude code changed: 2026-10-21 — optional frozen data cutoff (audit D6); default = FX_RESEARCH_DATA_CUTOFF
    from bot.research.fx_holdout_guard import FX_RESEARCH_DATA_CUTOFF  # claude code changed: 2026-10-21 — single seal source
    cutoff = FX_RESEARCH_DATA_CUTOFF if cutoff is None else cutoff  # claude code changed: 2026-10-21 — page no longer drifts with each hourly MT5 refresh
    from bot.research.fx_multipair_runner import (
        run_multipair_fold,
        QC1_MAX_CYCLE_CANDLES, QC1_PROFIT_ACTIVATION, QC1_TRAILING_PCT,
        QC1_MAE_HORIZON, QC1_INITIAL_USDT,
    )
    from bot.research.forex_instrument_spec import QC_PORTFOLIO_SPECS, QC_PORTFOLIO_CSV_FILES
    from bot.research.fx_fold_splitter import make_fx_splits, FxFold

    PAIRS = ["GBP/JPY"]  # claude code changed: AUD/CAD dropped — holdout negative
    pair_results = {}

    for pair in PAIRS:
        csv_file  = QC_PORTFOLIO_CSV_FILES[pair]
        pair_spec = QC_PORTFOLIO_SPECS[pair]
        df        = pd.read_csv(_ROOT / "data" / "forex" / csv_file, parse_dates=["timestamp"])
        df        = df[pd.to_datetime(df["timestamp"], utc=True) < cutoff].reset_index(drop=True)  # claude code changed: 2026-10-21 — frozen cutoff; fold boundaries no longer move hourly and sealed rows are excluded
        splits    = make_fx_splits(df)

        # --- single pass over calibration folds + holdout as fold 6 ---
        # Holdout consumed 2026-10-18 (GBP/JPY +$9.55); appended as fold 6 so the
        # equity curve extends to 2026-10-02 instead of truncating at the cal boundary.
        holdout_fold = FxFold(  # claude code changed: v4 — synthetic fold for consumed holdout
            fold_num=6,
            train=splits.calibration,
            embargo=splits.calibration_embargo,
            test=splits.holdout,
        )
        all_folds = list(splits.folds) + [holdout_fold]

        equity_pts      = []   # {"d": "YYYY-MM-DD", "p": cumPnl}
        fold_boundaries = []
        fold_stats      = []
        fills_dist      = [0] * 5  # claude code changed: LCEX-1 — 5 levels (was 7)
        all_costs       = []
        cum_pnl         = 0.0

        for fold in all_folds:
            result = run_multipair_fold(
                pair=pair, csv_file=csv_file,
                pair_spec=pair_spec, pip_size=pair_spec.pip_size,
                direction="LONG", fold=fold, fold_num=fold.fold_num,
            )
            closed = sorted(
                [t for t in result["trades"] if t.get("exit_reason") != "DATA_END_STILL_OPEN"],
                key=lambda t: t["exit_time"],
            )

            fold_start = fold.test["timestamp"].iloc[0].isoformat()[:10]
            fold_end   = fold.test["timestamp"].iloc[-1].isoformat()[:10]
            fold_boundaries.append({"fold": fold.fold_num, "start": fold_start, "end": fold_end})

            fold_pnl  = 0.0
            fold_wins = 0
            for t in closed:
                cum_pnl  += t["profit_usdt"]
                fold_pnl += t["profit_usdt"]
                if t["profit_usdt"] > 0:
                    fold_wins += 1
                equity_pts.append({"d": str(t["exit_time"])[:10], "p": round(cum_pnl, 4)})
                lvl = min(t.get("fills", 1), 5) - 1  # claude code changed: LCEX-1 — cap at 5 (was 7)
                fills_dist[lvl] += 1
                all_costs.append(t.get("total_cost_usdt", 0.0))

            fold_label = "Holdout (OOS)" if fold.fold_num == 6 else str(fold.fold_num)
            fold_stats.append({
                "fold":    fold_label,
                "start":   fold_start,
                "end":     fold_end,
                "trades":  len(closed),
                "wins":    fold_wins,
                "win_pct": round(fold_wins / len(closed) * 100, 1) if closed else 0.0,
                "pnl":     round(fold_pnl, 4),
                "is_holdout": fold.fold_num == 6,  # claude code changed: flag for template styling
            })

        total = sum(s["trades"] for s in fold_stats)
        wins  = sum(s["wins"]   for s in fold_stats)
        pnl   = equity_pts[-1]["p"] if equity_pts else 0.0

        eq_arr = np.array([t["p"] for t in equity_pts]) if equity_pts else np.array([0.0])
        rm     = np.maximum.accumulate(eq_arr)
        max_dd = float((eq_arr - rm).min())
        _dd_i  = int(np.argmin(eq_arr - rm))  # claude code changed: 2026-10-21 — drawdown % (owner rule: always show % in brackets)
        max_dd_pct = round(float((eq_arr - rm)[_dd_i] / rm[_dd_i] * 100), 1) if rm[_dd_i] > 0 else None  # claude code changed: % of peak cumulative P&L (no account in the legacy view)

        # Dollar drawdown from running equity peak
        drawdown_pts = []
        peak_eq = 0.0
        for t in equity_pts:
            peak_eq = max(peak_eq, t["p"])
            drawdown_pts.append(round(peak_eq - t["p"], 4))

        # ── scale P&L to display capital ($100/cycle) ────────────────────────
        # Simulation ran at QC-1 $6; multiply all dollar values by _DISPLAY_SCALE.
        # Trade counts, win rates, and fill distributions are unaffected.  # claude code changed: new scaling
        scaled_equity = [{"d": pt["d"], "p": round(pt["p"] * _DISPLAY_SCALE, 2)} for pt in equity_pts]
        scaled_dd     = [round(v * _DISPLAY_SCALE, 2) for v in drawdown_pts]
        for fs in fold_stats:
            fs["pnl"] = round(fs["pnl"] * _DISPLAY_SCALE, 2)
        scaled_pnl    = round(pnl * _DISPLAY_SCALE, 2)
        scaled_max_dd = round(max_dd * _DISPLAY_SCALE, 2)
        scaled_avg_c  = round(float(np.mean(all_costs)) * _DISPLAY_SCALE, 2) if all_costs else 0.0
        scaled_max_c  = round(float(np.max(all_costs))  * _DISPLAY_SCALE, 2) if all_costs else 0.0

        pair_results[pair] = {
            "equity":          scaled_equity,
            "drawdown":        scaled_dd,
            "fold_boundaries": fold_boundaries,
            "fold_stats":      fold_stats,
            "stats": {
                "total_trades": total,
                "wins":         wins,
                "win_pct":      round(wins / total * 100, 1) if total else 0.0,
                "total_pnl":    scaled_pnl,
                "max_dd":       scaled_max_dd,
                "max_dd_abs":   abs(scaled_max_dd),  # claude code changed: 2026-10-21 — for "−$X (−Y%)" display
                "max_dd_pct":   max_dd_pct,  # claude code changed: 2026-10-21 — % of peak cumulative P&L
                "avg_cost":     scaled_avg_c,
                "max_cost":     scaled_max_c,
            },
            "fills_dist": fills_dist,
        }

    # Solo pair — portfolio equity = GBP/JPY equity directly  # claude code changed: simplified for solo pair
    port_equity = pair_results["GBP/JPY"]["equity"]
    port_total  = pair_results["GBP/JPY"]["stats"]["total_trades"]
    port_wins   = pair_results["GBP/JPY"]["stats"]["wins"]

    return {
        "pairs":             pair_results,
        "pair_list":         PAIRS,
        "portfolio": {
            "equity":        port_equity,
            "total_trades":  port_total,
            "wins":          port_wins,
            "win_pct":       round(port_wins / port_total * 100, 1) if port_total else 0.0,
            "total_pnl":     round(port_equity[-1]["p"], 4) if port_equity else 0.0,
        },
        "fold_boundaries":   pair_results[PAIRS[0]]["fold_boundaries"],
        "perm":              _PERM_RESULT,
        "protocol":          _PROTOCOL,
    }


# ── view ──────────────────────────────────────────────────────────────────────

# ── capital-survival replay report (precomputed by `manage.py run_dca_survival_study`) ──────────────────────
_SURVIVAL_CYCLE_FIELDS = (  # claude code changed: 2026-10-21 — slim cycle rows sent to the page
    "cycle_id", "pair", "fold_spec", "entry_ts", "exit_ts", "outcome", "exit_reason", "fills", "lots", "duration_h",  # claude code changed: 2026-10-21 — fold_spec for the run-driven fold table
    "gross_pnl_usd", "spread_usd", "slippage_usd", "commission_usd", "swap_usd", "net_pnl_usd", "mae_usd",
    "peak_margin_usd", "peak_notional_usd", "rejected_order_attempts", "ladder_exhausted",
)
_survival_cache = {"mtime": None, "payload": None}  # claude code changed: 2026-10-21 — reload only when report.json changes


def _survival_payload():  # claude code changed: 2026-10-21 — new
    from bot.research.fx_survival_study import OUT_ROOT
    marker = OUT_ROOT / "LATEST"
    if not marker.exists():
        return None
    path = OUT_ROOT / marker.read_text().strip() / "report.json"
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    if _survival_cache["mtime"] == mtime:
        return _survival_cache["payload"]
    rep = json.loads(path.read_text())

    def _slim(c):  # claude code changed: 2026-10-21 — 2-dp floats, minute timestamps (page size)
        out = {}
        for k in _SURVIVAL_CYCLE_FIELDS:
            v = c.get(k)
            if isinstance(v, float):
                v = round(v, 2)
            elif k in ("entry_ts", "exit_ts") and isinstance(v, str):
                v = v[:16]
            out[k] = v
        return out

    def _dedupe_markers(markers):  # claude code changed: 2026-10-21 — one marker per (day, type, pair); keeps the first cycle id
        seen, out = set(), []
        for mk in markers:
            period = 7 if mk.get("type") in ("INSUFFICIENT_FREE_CASH", "MARGIN_CALL") else 10  # claude code changed: noisy types -> one per month
            key = (str(mk.get("t", ""))[:period], mk.get("type"), mk.get("pair"))
            if key not in seen:
                seen.add(key)
                out.append(mk)
        return out

    def _columnar(rows):  # claude code changed: 2026-10-21 — {cols, rows} instead of repeated keys
        return {"cols": list(_SURVIVAL_CYCLE_FIELDS), "rows": [[r.get(k) for k in _SURVIVAL_CYCLE_FIELDS] for r in rows]}

    runs = {}
    for key, run in rep["capital_runs"].items():
        m = {k: v for k, v in run["metrics"].items() if k not in ("by_month", "by_fold_spec")}
        runs[key] = {
            "capital": run["capital"], "metrics": m, "series": run["series"], "markers": _dedupe_markers(run["markers"]),
            "failures": run["failures"], "event_windows": run["event_windows"],
            "cycles": _columnar([_slim(c) for c in run["cycles"]]),
        }
    for cap, run in rep["portfolio"].items():
        runs[f"portfolio_{cap}"] = {"capital": float(cap), "metrics": run["metrics"], "series": run["series"],
                                    "markers": _dedupe_markers(run["markers"]), "by_pair": run["by_pair"],
                                    "event_windows": run["event_windows"], "portfolio": True}
        cyc_csv = path.parent / "runs" / f"portfolio_{cap}_cycles.csv"   # claude code changed: 2026-10-21 — portfolio cycles + failures for the run-driven sections
        if cyc_csv.exists():
            cdf = pd.read_csv(cyc_csv)
            recs = json.loads(cdf.to_json(orient="records"))
            runs[f"portfolio_{cap}"]["cycles"] = _columnar([_slim(c) for c in recs])
            bad = cdf[cdf["outcome"].isin(["LIQUIDATED", "OPEN_UNRESOLVED_AT_DATA_END"]) | (cdf["net_pnl_usd"] < -500)]
            runs[f"portfolio_{cap}"]["failures"] = json.loads(bad.to_json(orient="records"))
    payload = {
        "cutoff": rep["cutoff"], "generated_at": rep["generated_at"], "primary_pair": rep["primary_pair"],
        "capital_levels": rep["capital_levels"], "cost_labels": rep["cost_labels"], "account_rules": rep["account_rules"],
        "strategy": rep["strategy"], "decomposition": rep["decomposition"], "legacy_baseline": rep["legacy_baseline"],
        "stress": rep["stress"], "events": rep["events"], "runs": runs,
        "provenance": {k: {kk: v.get(kk) for kk in ("replay_start", "replay_end", "bars", "spread_bars_missing_ffilled",
                                                     "quote_usd_source", "base_usd_source", "broker_snapshot", "folds")}  # claude code changed: 2026-10-21 — folds for the run-driven fold table / bands
                       for k, v in rep["provenance"].items()},
        "report_dir": path.parent.name,
    }
    _survival_cache.update(mtime=mtime, payload=payload)
    return payload


_SURVIVAL_DEFAULT_RUN = "gbpjpy_5000"  # claude code changed: 2026-10-21 — only this run is embedded; others load on demand


def _survival_page_payload(full):  # claude code changed: 2026-10-21 — strip heavy parts of non-default runs
    if not full:
        return None
    page = dict(full)
    page["runs"] = {}
    for key, run in full["runs"].items():
        if key == _SURVIVAL_DEFAULT_RUN:
            page["runs"][key] = run
        else:
            page["runs"][key] = {k: v for k, v in run.items() if k not in ("series", "cycles", "failures")}  # claude code changed: 2026-10-21 — failures lazy too
            page["runs"][key]["lazy"] = True
    return page


def dca_survival_ledger(request):  # claude code changed: 2026-10-21 — new; ledger + fills for one cycle of one saved run
    import re
    from django.http import JsonResponse
    from bot.research.fx_survival_study import OUT_ROOT
    run = request.GET.get("run", "")
    if not re.fullmatch(r"(gbpjpy|portfolio)_\d{3,6}", run):
        return JsonResponse({"error": "unknown run"}, status=400)
    if request.GET.get("part") == "run":  # claude code changed: 2026-10-21 — lazy series + cycles for a non-default run
        full = _survival_payload()
        if not full or run not in full["runs"]:
            return JsonResponse({"error": "unknown run"}, status=404)
        r = full["runs"][run]
        return JsonResponse({"run": run, "series": r.get("series"), "cycles": r.get("cycles"), "failures": r.get("failures")})  # claude code changed: 2026-10-21
    try:
        cycle_id = int(request.GET.get("cycle", ""))
    except ValueError:
        return JsonResponse({"error": "cycle must be an integer"}, status=400)
    marker = OUT_ROOT / "LATEST"
    if not marker.exists():
        return JsonResponse({"error": "no survival report — run manage.py run_dca_survival_study"}, status=404)
    folder = OUT_ROOT / marker.read_text().strip() / "runs"
    out = {}
    for name in ("ledger", "fills"):
        f = folder / f"{run}_{name}.csv"
        if not f.exists():
            return JsonResponse({"error": f"{f.name} missing"}, status=404)
        df = pd.read_csv(f)
        out[name] = json.loads(df[df["cycle_id"] == cycle_id].to_json(orient="records"))
    out.update(run=run, cycle_id=cycle_id)
    return JsonResponse(out)


def dca_engine(request):
    error   = None
    results = cache.get(_CACHE_KEY)

    if results is None:
        try:
            results = _build_results()
            cache.set(_CACHE_KEY, results, _CACHE_TTL)
        except Exception as exc:
            logger.exception("DCA engine build failed")
            error   = str(exc)
            results = None

    if results:
        pairs_json            = json.dumps({
            p: {
                "equity":     results["pairs"][p]["equity"],
                "drawdown":   results["pairs"][p]["drawdown"],  # claude code changed: add flat drawdown for per-pair charts
                "fold_stats": results["pairs"][p]["fold_stats"],
                "fills_dist": results["pairs"][p]["fills_dist"],
            }
            for p in results["pair_list"]
        })
        portfolio_equity_json = json.dumps(results["portfolio"]["equity"])
        fold_boundaries_json  = json.dumps(results["fold_boundaries"])
    else:
        pairs_json = portfolio_equity_json = fold_boundaries_json = "null"

    try:
        survival = _survival_payload()  # claude code changed: 2026-10-21 — capital-survival replay (precomputed)
        survival_error = None
    except Exception as exc:  # claude code changed: a broken report must not take the legacy page down
        logger.exception("DCA survival report load failed")
        survival, survival_error = None, str(exc)

    context = {
        "survival_json":         json.dumps(_survival_page_payload(survival)) if survival else "null",  # claude code changed: 2026-10-21 — default run embedded, others lazy
        "survival":              survival,  # claude code changed: 2026-10-21
        "survival_error":        survival_error,  # claude code changed: 2026-10-21
        "error":                 error,
        "results":               results,
        "pairs_json":            pairs_json,
        "portfolio_equity_json": portfolio_equity_json,
        "fold_boundaries_json":  fold_boundaries_json,
        "protocol":              _PROTOCOL,
        "perm":                  _PERM_RESULT,
        "level_capital":         _LEVEL_CAPITAL,
        # pre-built list so the template's fold-table loop avoids custom filters
        "pair_table_items": [
            ("GBP/JPY", "GBP/JPY", "background:var(--accent)"),
        ],  # claude code changed: AUD/CAD removed
    }
    return render(request, "dca_engine.html", context)
