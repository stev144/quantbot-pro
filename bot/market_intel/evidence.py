# bot/market_intel/evidence.py
"""Strategy research evidence relevant to one asset.   # claude code changed: 2026-10-21 — new

Only results that exist on disk are shown, each tagged with its class from bot.research.evidence_registry
(CANONICAL / LEGACY / SUPERSEDED / INVALIDATED / UNVERIFIED). No metric is computed or filled in here: values are read
from the artifact as stored. Sample size and validation status are always shown.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import pandas as pd
from django.conf import settings

_RD = Path(settings.BASE_DIR) / "research_data"
_PAIRS_FIELDS = (("n_total_trades", "trades"), ("win_rate", "win rate"), ("profit_factor", "profit factor"),
                 ("avg_pnl_pct", "expectancy % / trade"), ("max_drawdown_pct", "max drawdown %"),
                 ("total_pnl_pct", "total return %"), ("fee_rate", "fee rate / side"))


def _classify(rel: str) -> Dict:
    from bot.research.evidence_registry import classify_artifact
    return classify_artifact(rel)


def _pairs_entries(symbol: str) -> List[Dict]:
    base = symbol.split("_")[0]
    out = []
    for f in sorted(_RD.glob("*_strategy_summary.csv")):
        legs = f.name.replace("_strategy_summary.csv", "").split("_")
        if base not in legs:
            continue
        try:
            row = pd.read_csv(f).iloc[0].to_dict()
        except Exception:
            continue
        cls = _classify(f.name)
        out.append({
            "strategy": f"Kalman pairs mean-reversion ({'/'.join(legs)})",
            "evidence_class": cls.get("class"), "evidence_label": cls.get("label", cls.get("class")),
            "evidence_reason": cls.get("reason"), "result_type": "BACKTEST (legacy pipeline)",
            "metrics": [{"label": lab, "value": row.get(k)} for k, lab in _PAIRS_FIELDS if k in row and pd.notna(row.get(k))],
            "sample_size": row.get("n_total_trades"),
            "validation": "not validated out of sample (pseudo-holdout history, CRRP-2026-10-02)",
            "artifact": f"research_data/{f.name}",
            "limitations": "Selected on the same history it was tested on; see the evidence reason.",
        })
    return out


def _dca_entries(symbol: str) -> List[Dict]:
    marker = _RD / "dca_survival" / "LATEST"
    if not marker.exists():
        return []
    folder = marker.read_text().strip()
    path = _RD / "dca_survival" / folder / "report.json"
    if not path.exists():
        return []
    rep = json.loads(path.read_text())
    pair = symbol.replace("_", "/")
    cls = _classify(f"dca_survival/{folder}/report.json")
    out = []
    if rep.get("primary_pair") == pair:
        for key, run in rep.get("capital_runs", {}).items():
            m = run["metrics"]
            out.append({
                "strategy": f"QC-1 forex DCA — fixed-capital ESMA replay (${run['capital']:,.0f} account)",
                "evidence_class": cls.get("class"), "evidence_label": cls.get("label", cls.get("class")),
                "evidence_reason": cls.get("reason"), "result_type": "HISTORICAL SIMULATION (paper only)",
                "metrics": [{"label": "completed cycles", "value": m.get("completed_cycles")},
                            {"label": "win rate after costs %", "value": m.get("win_rate_after_costs_pct")},
                            {"label": "net profit factor", "value": m.get("net_profit_factor")},
                            {"label": "net expectancy $ / cycle", "value": m.get("net_expectancy_per_cycle_usd")},
                            {"label": "max drawdown %", "value": m.get("max_equity_drawdown_pct")},
                            {"label": "total costs $", "value": (m.get("costs") or {}).get("total")},
                            {"label": "liquidations", "value": m.get("liquidation_count")}],
                "sample_size": m.get("completed_cycles"),
                "validation": f"historical replay to {rep.get('cutoff', '')[:10]}; not out-of-sample evidence",
                "artifact": f"research_data/dca_survival/{folder}/report.json",
                "limitations": "Swap from today's broker rates, commission unknown, 1 h bars; see /execution/dca-engine/.",
            })
    for cap, run in (rep.get("portfolio") or {}).items():
        by_pair = (run.get("by_pair") or {}).get(pair)
        if by_pair:
            out.append({
                "strategy": f"QC-1 forex DCA — 5-pair shared account (${float(cap):,.0f}), this pair's share",
                "evidence_class": cls.get("class"), "evidence_label": cls.get("label", cls.get("class")),
                "evidence_reason": cls.get("reason"), "result_type": "HISTORICAL SIMULATION (paper only)",
                "metrics": [{"label": "cycles", "value": by_pair.get("cycles")}, {"label": "net $", "value": by_pair.get("net")},
                            {"label": "costs $", "value": by_pair.get("costs")}, {"label": "swap $", "value": by_pair.get("swap")}],
                "sample_size": by_pair.get("cycles"),
                "validation": "historical replay; not out-of-sample evidence",
                "artifact": f"research_data/dca_survival/{folder}/report.json",
                "limitations": "Shared-account result: depends on the other pairs' losses and margin use.",
            })
    return out


def strategy_evidence(asset_class: str, symbol: str) -> Dict:
    entries = _pairs_entries(symbol) if asset_class == "crypto" else _dca_entries(symbol)
    return {
        "entries": entries,
        "headline": "No strategy on this platform has CANONICAL (validated) evidence of an edge (research reset "
                    "CRRP-2026-10-02). Everything below is descriptive history, labelled with its evidence class.",
        "empty_message": "No stored research result covers this asset." if not entries else "",
        "live_results": "No live or paper-trading results are published for this asset on this page.",
    }
