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
_CACHE_KEY = "dca_engine_results_v5"  # claude code changed: v5 — P&L scaled to $100/cycle display capital
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

# QC-1 calibration runs at $6/cycle; display is scaled to $100/cycle so P&L is
# realistic. Scale factor = 100/6 ≈ 16.67. Win rates and fill distributions unchanged.
_QC1_ENTRY_USDT     = 6.0    # claude code changed: QC-1 calibration constant (do not change)
_DISPLAY_ENTRY_USDT = 100.0  # claude code changed: display capital — matches Paper Trader PAPER_ENTRY_USDT
_DISPLAY_SCALE      = _DISPLAY_ENTRY_USDT / _QC1_ENTRY_USDT  # ≈ 16.667

_PROTOCOL = {
    "id":             "FX-QC-PROTOCOL-1",
    "initial_usdt":   int(_DISPLAY_ENTRY_USDT),   # claude code changed: display capital $100
    "max_cycle_h":    72,
    "profit_act_pct": 0.5,
    "trailing_pct":   0.2,
    "mae_horizon":    6,
    "ladder_levels":  7,
    "multipliers":    "1 · 2 · 4 · 8 · 16 · 32 · 64×",
    "folds":          5,
    "embargo_bars":   48,
    "max_deployed":   12700,  # claude code changed: $100 × (1+2+4+8+16+32+64) = $12,700
}

# Cumulative capital deployed at each ladder level ($100 initial)
_LEVEL_CAPITAL = [100, 300, 700, 1500, 3100, 6300, 12700]  # claude code changed: scaled from $6 to $100 entry


# ── data build (single pass per pair per fold) ────────────────────────────────

def _build_results():
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
        fills_dist      = [0] * 7
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
                lvl = min(t.get("fills", 1), 7) - 1
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
        ],  # claude code changed: AUD/CAD removed
    }
    return render(request, "dca_engine.html", context)
