# bot/views/dca_engine.py  # claude code changed: new — QC-1 DCA Engine dashboard page
"""
Django view for the Forex DCA Engine walk-forward results page.

Runs QC-1 (FX-QC-PROTOCOL-1) across GBP/JPY and AUD/CAD LONG, 5 walk-forward
folds, then builds equity curves, per-fold stats, and fills distributions.
Results are cached 1 hour — the simulation takes ~30 s on first load.

Permutation-test summary (N=499, block=48h, post-hoc) is a baked-in constant;
it does not re-run on page load.
"""

import json
import logging

import numpy as np
import pandas as pd
from django.core.cache import cache
from django.shortcuts import render
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT      = Path(__file__).resolve().parents[2]
_CACHE_KEY = "dca_engine_results_v1"
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

_PROTOCOL = {
    "id":             "FX-QC-PROTOCOL-1",
    "initial_usdt":   6.0,
    "max_cycle_h":    72,
    "profit_act_pct": 0.5,
    "trailing_pct":   0.2,
    "mae_horizon":    6,
    "ladder_levels":  7,
    "multipliers":    "1 · 2 · 4 · 8 · 16 · 32 · 64×",
    "folds":          5,
    "embargo_bars":   48,
    "max_deployed":   762,
}

_LEVEL_CAPITAL = [6, 18, 42, 90, 186, 378, 762]  # cumulative per level L1..L7


# ── data build (single pass per pair per fold) ────────────────────────────────

def _build_results():
    from bot.research.fx_multipair_runner import (
        run_multipair_fold,
        QC1_MAX_CYCLE_CANDLES, QC1_PROFIT_ACTIVATION, QC1_TRAILING_PCT,
        QC1_MAE_HORIZON, QC1_INITIAL_USDT,
    )
    from bot.research.forex_instrument_spec import QC_PORTFOLIO_SPECS, QC_PORTFOLIO_CSV_FILES
    from bot.research.fx_fold_splitter import make_fx_splits

    PAIRS = ["GBP/JPY", "AUD/CAD"]
    pair_results = {}

    for pair in PAIRS:
        csv_file  = QC_PORTFOLIO_CSV_FILES[pair]
        pair_spec = QC_PORTFOLIO_SPECS[pair]
        df        = pd.read_csv(_ROOT / "data" / "forex" / csv_file, parse_dates=["timestamp"])
        splits    = make_fx_splits(df)

        # --- single pass over all folds ---
        equity_pts      = []   # {"d": "YYYY-MM-DD", "p": cumPnl}
        fold_boundaries = []
        fold_stats      = []
        fills_dist      = [0] * 7
        all_costs       = []
        cum_pnl         = 0.0

        for fold in splits.folds:
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
                lvl = min(t.get("fills", 1), 7) - 1
                fills_dist[lvl] += 1
                all_costs.append(t.get("total_cost_usdt", 0.0))

            fold_stats.append({
                "fold":    fold.fold_num,
                "start":   fold_start,
                "end":     fold_end,
                "trades":  len(closed),
                "wins":    fold_wins,
                "win_pct": round(fold_wins / len(closed) * 100, 1) if closed else 0.0,
                "pnl":     round(fold_pnl, 4),
            })

        total = sum(s["trades"] for s in fold_stats)
        wins  = sum(s["wins"]   for s in fold_stats)
        pnl   = equity_pts[-1]["p"] if equity_pts else 0.0

        eq_arr = np.array([t["p"] for t in equity_pts]) if equity_pts else np.array([0.0])
        rm     = np.maximum.accumulate(eq_arr)
        max_dd = float((eq_arr - rm).min())

        pair_results[pair] = {
            "equity":          equity_pts,
            "fold_boundaries": fold_boundaries,
            "fold_stats":      fold_stats,
            "stats": {
                "total_trades": total,
                "wins":         wins,
                "win_pct":      round(wins / total * 100, 1) if total else 0.0,
                "total_pnl":    round(pnl, 4),
                "max_dd":       round(max_dd, 4),
                "avg_cost":     round(float(np.mean(all_costs)), 2) if all_costs else 0.0,
                "max_cost":     round(float(np.max(all_costs)), 2)  if all_costs else 0.0,
            },
            "fills_dist": fills_dist,
        }

    # Portfolio equity: merge both pairs sorted by exit date
    all_deltas = []
    for pair in PAIRS:
        prev = 0.0
        for t in pair_results[pair]["equity"]:
            all_deltas.append({"d": t["d"], "delta": t["p"] - prev})
            prev = t["p"]
    all_deltas.sort(key=lambda x: x["d"])
    cum_port    = 0.0
    port_equity = []
    for t in all_deltas:
        cum_port += t["delta"]
        port_equity.append({"d": t["d"], "p": round(cum_port, 4)})

    port_total = sum(pair_results[p]["stats"]["total_trades"] for p in PAIRS)
    port_wins  = sum(pair_results[p]["stats"]["wins"]         for p in PAIRS)

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
                "fold_stats": results["pairs"][p]["fold_stats"],
                "fills_dist": results["pairs"][p]["fills_dist"],
            }
            for p in results["pair_list"]
        })
        portfolio_equity_json = json.dumps(results["portfolio"]["equity"])
        fold_boundaries_json  = json.dumps(results["fold_boundaries"])
    else:
        pairs_json = portfolio_equity_json = fold_boundaries_json = "null"

    context = {
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
            ("AUD/CAD", "AUD/CAD", "background:var(--term-warn,#e8a838)"),
        ],
    }
    return render(request, "dca_engine.html", context)
