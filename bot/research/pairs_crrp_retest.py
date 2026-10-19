# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/pairs_crrp_retest.py
#
# claude code changed: new file — 2026-10-03. Descriptive re-test of the 10
# previously "approved" cointegrated pairs (the Pairs Performance dropdown)
# with the CORRECTED system, after CRRP-2026-10-02.
#
# WHAT THIS IS — AND IS NOT
#   * Corrected pieces used: fixed-quantity leg P&L marked at real closes
#     (C-01 fix), the Stage 2D trade lifecycle via rule_set
#     "CANONICAL_UNCERTIFIED" (same rules as live: decision at a close,
#     fill d=1 candle later, H=24, entry-sign check), declared fee/slippage,
#     Engle–Granger/MacKinnon cointegration (H-03 fix).
#   * NOT canonical evidence (even under amendment 2): pair SELECTION was the
#     legacy full-history scan (zone S BLOCKED); short-leg borrow cost is not
#     modelled (UNAVAILABLE); every candle is exposed history (PSEUDO_HOLDOUT
#     — NOT CLAIM-ELIGIBLE); these pairs were SELECTED on this same data, so
#     any positive figure carries selection bias; no permutation p-value is
#     computed (the canonical pairs null is Stage 2D Step 17 — not built).
#   * Evidence class of every output: LEGACY / DESCRIPTIVE (registry).
#
# PRE-DECLARED PROTOCOL (fixed before any result was seen)
#   SCORED SAMPLE   trades whose ENTRY DECISION is after the legacy seed window
#                   (the OLS seed is fitted on those first 10,000 candles — a
#                   trade inside it uses a hedge ratio fitted on its own future).
#   CONTROL         the identical pipeline (seed refit, Kalman filter, lifecycle,
#                   costs) on the same pair with leg B CIRCULARLY SHIFTED by
#                   25 / 50 / 75 % of the common sample: each leg keeps its own
#                   dynamics, the cross-asset relationship is destroyed.
#   READING (first rule that applies):
#     NO_EVIDENCE                       scored net P&L <= 0
#     NOT_DISTINGUISHABLE_FROM_CONTROL  scored mean net P&L per trade <= the best
#                                       of the three shifted controls
#     UNSTABLE                          positive in fewer than a strict majority of
#                                       calendar years with >= 20 scored trades, OR
#                                       the single best year is > 75 % of the sum of
#                                       positive years
#     CONSISTENT_WITH_STRUCTURE         otherwise — DESCRIPTIVE ONLY, never "edge"
#   Family: 10 pairs. No significance is claimed for any of them.
#
# PROTOCOL AMENDMENT (2026-10-03, after the first pair, before the other nine —
# a construction ERROR, not a tuning choice): the control first shifted leg B's
# PRICE LEVELS circularly. That splices B's last price onto its first (FIDA:
# ~$0.1 in 2026 onto ~$10 in 2021 — a ~100x jump in one candle), so any trade
# open across the seam booked a fake loss (one DODO/FIDA control: −25,292 USDT,
# −90/trade on ~$25 legs — impossible). The control now rolls B's LOG RETURNS
# circularly and rebuilds a continuous path from B's first close: B keeps
# exactly its own return dynamics, the alignment with A is destroyed, and there
# is no level jump. The reading rule is unchanged.
#
# PROTOCOL AMENDMENT 2 (2026-10-03, remediation F-03 — before any further run):
# the legacy seed (OLS over the first 10,000 candles, applied from candle 0) is
# look-ahead. The pipeline now uses KalmanFilterEngine.run_canonical(): θ₀ =
# OLS on the first L = 1000 grid candles ONLY (protocol), the filter starts
# after them, and the output is a validated CANONICAL Kalman dataset (seed
# record + provenance sidecar) consumed with rule_set="CANONICAL". The scored
# sample is every trade (the init window carries no state). Controls run the
# identical canonical path on the return-rolled leg. Nothing was run under
# this amendment yet; a rerun needs owner approval.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

PAIRS: List[str] = [
    "AVAX_USDT/ATOM_USDT", "AVA_USDT/PHA_USDT", "DEXE_USDT/ALGO_USDT", "DODO_USDT/FIDA_USDT",
    "DOT_USDT/LINK_USDT", "FET_USDT/BCH_USDT", "FET_USDT/DASH_USDT", "MINA_USDT/ONG_USDT",
    "ONT_USDT/RIF_USDT", "SOL_USDT/MASK_USDT",
]
SHIFT_FRACTIONS = (0.25, 0.50, 0.75)
MIN_TRADES_PER_YEAR = 20
BEST_YEAR_SHARE_MAX = 0.75
OUT_DIR = Path("research_data/crrp_retest_2026-10-02")
DATA_DIR = "data"


def _common_closes(a: str, b: str, data_dir: str = DATA_DIR) -> pd.DataFrame:
    from bot.research.kalman_filter_engine import load_close_series_from_csv
    df = pd.concat({"a": load_close_series_from_csv(a, data_dir), "b": load_close_series_from_csv(b, data_dir)}, axis=1, join="inner")
    return df.dropna()


def _eg(la: pd.Series, lb: pd.Series, min_obs: int = 500):
    from bot.quant_core.cointegration import engle_granger
    r = engle_granger(la, lb, min_obs=min_obs, maxlag=None, autolag="aic")
    return round(r.value.pvalue, 6) if r.ok else None


def _run_pipeline(a: str, b: str, closes: pd.DataFrame, out: Path) -> pd.DataFrame:
    """Canonical Kalman (causal init, L = 1000) -> validated canonical dataset ->
    canonical lifecycle with fixed-quantity P&L. Amendment 2 (F-03)."""
    from bot.research.kalman_filter_engine import KalmanFilterEngine
    from bot.research.entry_exit_engine import EntryExitEngine
    out.mkdir(parents=True, exist_ok=True)
    # ols_beta/ols_alpha are NOT used by run_canonical — θ₀ comes only from causal_init (same convention as test_canonical_seed)
    eng = KalmanFilterEngine(pair_name=f"{a}/{b}", symbol_a=a, symbol_b=b, ols_beta=1.0, ols_alpha=0.0)
    eng.run_canonical(closes["a"], closes["b"], output_dir=str(out))
    kc = out / "canonical" / f"{a}_{b}_kalman.csv"
    EntryExitEngine(pair_name=f"{a}/{b}", symbol_a=a, symbol_b=b, output_dir=str(out),   # must equal the seed record's pair name (canonical provenance gate)
                    rule_set="CANONICAL").run(kalman_csv=str(kc))
    tl = pd.read_csv(next(out.glob("*_trade_log.csv")))
    tl["entry_decision_timestamp"] = pd.to_datetime(tl["entry_decision_timestamp"], utc=True)
    return tl


def _score(tl: pd.DataFrame, seed_end) -> Dict:
    s = tl[tl["entry_decision_timestamp"] > seed_end]
    pnl = s["net_pnl_usdt"].astype(float)
    wins, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    by_year = s.groupby(s["entry_decision_timestamp"].dt.year)["net_pnl_usdt"].agg(["count", "sum"])
    return {"n_trades": int(len(s)), "net_pnl_usdt": round(float(pnl.sum()), 2),
            "mean_pnl_per_trade": round(float(pnl.mean()), 4) if len(s) else None,
            "win_rate": round(float((pnl > 0).mean()), 4) if len(s) else None,
            "profit_factor": round(float(wins / losses), 3) if losses > 0 else None,
            "by_year": {int(y): {"n": int(r["count"]), "net": round(float(r["sum"]), 2)} for y, r in by_year.iterrows()},
            "n_trades_in_seed_window_excluded": int((tl["entry_decision_timestamp"] <= seed_end).sum())}


def _reading(real: Dict, controls: List[Dict]) -> str:
    if not real["n_trades"] or real["net_pnl_usdt"] <= 0:
        return "NO_EVIDENCE"
    best_ctrl = max((c["mean_pnl_per_trade"] for c in controls if c["mean_pnl_per_trade"] is not None), default=None)
    if best_ctrl is not None and real["mean_pnl_per_trade"] <= best_ctrl:
        return "NOT_DISTINGUISHABLE_FROM_CONTROL"
    years = [v for v in real["by_year"].values() if v["n"] >= MIN_TRADES_PER_YEAR]
    positive = [v["net"] for v in years if v["net"] > 0]
    if len(positive) * 2 <= len(years) or (positive and max(positive) > BEST_YEAR_SHARE_MAX * sum(positive)):
        return "UNSTABLE"
    return "CONSISTENT_WITH_STRUCTURE"


def retest_pair(pair: str) -> Dict:
    from bot.quant_core.protocol import CANONICAL
    a, b = pair.split("/")
    closes = _common_closes(a, b)
    seed_end = closes.index[CANONICAL.require_init_window() - 1]   # end of the causal init window (no trades inside it)
    base = OUT_DIR / f"{a}_{b}"
    real = _score(_run_pipeline(a, b, closes, base / "real"), seed_end)
    la, lb = np.log(closes["a"]), np.log(closes["b"])
    post = closes.index > seed_end
    eg = {"init_window": _eg(la[~post], lb[~post]), "scored_period": _eg(la[post], lb[post]),
          "by_year": {int(y): _eg(la[closes.index.year == y], lb[closes.index.year == y]) for y in sorted(set(closes.index.year))}}
    controls = []
    r_b = np.diff(np.log(closes["b"].to_numpy()))
    for f in SHIFT_FRACTIONS:
        k = int(len(closes) * f)
        shifted = closes.copy()
        shifted["b"] = float(closes["b"].iloc[0]) * np.exp(np.concatenate([[0.0], np.cumsum(np.roll(r_b, -k))]))   # amendment 1: roll B's log returns
        c = _score(_run_pipeline(a, b, shifted, base / f"ctrl_shift{int(f * 100)}"), seed_end)
        c["shift_candles"] = k
        controls.append(c)
    return {"pair": pair, "n_common_candles": int(len(closes)), "init_window_end": str(seed_end),
            "engle_granger_pvalue": eg, "real": real, "controls": controls, "reading": _reading(real, controls)}


def run_all(pairs: List[str] = PAIRS) -> Dict:
    import subprocess
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "bot"], capture_output=True, text=True).stdout.strip())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for p in pairs:
        r = retest_pair(p)
        print(p, r["reading"], r["real"]["n_trades"], r["real"]["net_pnl_usdt"], flush=True)
        results.append(r)
    out = {
        "artifact": "pairs_crrp_retest", "generated_at": datetime.now(timezone.utc).isoformat(),
        "reset_point": "CRRP-2026-10-02", "evidence_class": "LEGACY_DESCRIPTIVE",
        "code_commit": commit, "code_dirty": dirty, "rule_set": "CANONICAL",
        "seed": "canonical causal_init: OLS on the first L=1000 grid candles only (amendment 2, F-03)",
        "costs": "declared fee + slippage (binance cost model); short-leg borrow NOT modelled (UNAVAILABLE)",
        "data_role": "PSEUDO_HOLDOUT — NOT CLAIM-ELIGIBLE; pairs were selected on this same data",
        "protocol": {"scored": "every trade (canonical output starts after the causal init window)", "controls": list(SHIFT_FRACTIONS),
                     "min_trades_per_year": MIN_TRADES_PER_YEAR, "best_year_share_max": BEST_YEAR_SHARE_MAX},
        "results": results,
    }
    with open(OUT_DIR / "retest_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=str)
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.getcwd())
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    run_all(sys.argv[1:] or PAIRS)
