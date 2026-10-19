# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/zone_s_pair_diagnostics.py
# claude code changed: new file — 2026-10-07, owner choice "Zone-S only, keep V clean": DESCRIPTIVE economic
# pre-screen and walk-forward for the five nominated crypto pairs, computed ONLY on zone S.
#
# Zone discipline (G5): every price and funding series is cut at the last zone-S candle (S1) BEFORE it reaches any
# engine, and the cut is asserted — no zone-V/F row can enter. The canonical Kalman seed uses the INIT window before
# S (as the protocol requires). Trades count only if their entry decision lies in S and is label-complete inside S.
#
# Economic pre-screen (signed rule 2026-10-03): mean gross edge per trade ≥ 3 × realistic round-trip cost. Costs come
# from the canonical engine (fees + slippage via bot.pairs.pnl; short-leg financing = recorded perp funding). A trade
# whose short leg has no recorded funding is FINANCING-UNAVAILABLE (never priced at 0) and is reported separately.
# Walk-forward: bot.research.walk_forward_engine.canonical_walk_forward on calendar-quarter folds inside S (a
# descriptive choice — NOT the owner's canonical fold boundaries, which remain unset).
# Status of every output: EXPLORATORY / DESCRIPTIVE / NOT CLAIMABLE. No verdict, no registration.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

DIAG_VERSION = "QBP-ZONE-S-PAIR-DIAG-1"
NOMINATED = ("AVA_USDT/PUNDIX_USDT", "DIA_USDT/FIL_USDT", "DIA_USDT/IOTX_USDT", "DIA_USDT/LPT_USDT", "DIA_USDT/ONT_USDT")
PRESCREEN_MULTIPLE = 3.0
LABEL = "EXPLORATORY / DESCRIPTIVE — zone S only, not claimable, no verdict (zone V untouched)"


class ZoneViolation(RuntimeError):
    pass


def quarter_edges(zones=None) -> List[pd.Timestamp]:
    """Calendar-quarter fold edges strictly inside S: S0, each quarter start, S1 + 1 h."""
    from bot.quant_core.research_zones import ZONES
    z = zones or ZONES
    qs = pd.date_range(z.S0.normalize() + pd.offsets.QuarterBegin(startingMonth=1), z.S1, freq="QS-JAN", tz="UTC")
    return [z.S0] + [q for q in qs if z.S0 < q <= z.S1] + [z.S1 + pd.Timedelta(hours=1)]


def cut_to_zone_s(series: pd.Series, name: str, zones=None) -> pd.Series:
    """Drop every row after S1 and prove it."""
    from bot.quant_core.research_zones import ZONES
    z = zones or ZONES
    s = series[series.index <= z.S1]
    if len(s) and s.index.max() > z.S1:
        raise ZoneViolation(f"{name}: data after S1 survived the cut")
    return s


def load_close(symbol: str, data_dir="data", zones=None) -> pd.Series:
    df = pd.read_csv(Path(data_dir) / f"{symbol}_1h.csv", float_precision="round_trip")
    s = pd.Series(df["close"].to_numpy(float), index=pd.to_datetime(df["timestamp"], utc=True), name=symbol)
    return cut_to_zone_s(s[~s.index.duplicated(keep="first")].sort_index(), symbol, zones)


def load_funding(symbol: str, data_dir="data", zones=None) -> pd.Series:
    from bot.quant_core.funding import load_funding_series
    return cut_to_zone_s(load_funding_series(symbol, data_dir=data_dir), f"{symbol} funding", zones)


def prescreen(gross: float, cost: float) -> str:
    if gross is None or cost is None or not np.isfinite(gross) or not np.isfinite(cost):
        return "COST_UNAVAILABLE"
    return "MEETS_3X" if gross >= PRESCREEN_MULTIPLE * cost else "FAILS_3X"


def _trade_frame(tl: pd.DataFrame, gap_ids: set, zones, protocol) -> pd.DataFrame:
    trunc = pd.Timedelta(hours=protocol.execution_delay_candles + protocol.require_max_holding())
    t = pd.to_datetime(tl["entry_decision_timestamp"], utc=True)
    keep = ((t >= zones.S0) & (t <= zones.S1 - trunc)).to_numpy()
    d = tl.loc[keep].copy()
    d["decision_time"] = t[keep].to_numpy()
    cap = d["capital_usdt"].astype(float)
    d["gross"] = d["mark_pnl_usdt"].astype(float) / cap
    d["trading_cost"] = (d["fee_usdt"].astype(float) + d["slippage_usdt"].astype(float)) / cap
    d["financed"] = ~d["trade_id"].isin(gap_ids)
    d["financing"] = np.where(d["financed"], d["borrow_usdt"].astype(float) / cap, np.nan)
    return d


def _summ(d: pd.DataFrame) -> dict:
    fin = d[d["financed"]]
    g_all, c_all = (float(d["gross"].mean()), float(d["trading_cost"].mean())) if len(d) else (None, None)
    g_f = float(fin["gross"].mean()) if len(fin) else None
    c_f = float((fin["trading_cost"] + fin["financing"]).mean()) if len(fin) else None
    return {"n_trades": int(len(d)), "n_financing_priced": int(len(fin)), "n_financing_unavailable": int(len(d) - len(fin)),
            "mean_gross_per_trade": g_all, "mean_fees_slippage_per_trade": c_all,
            "prescreen_fees_slippage_only": prescreen(g_all, c_all),
            "mean_gross_priced": g_f, "mean_full_cost_priced": c_f,
            "prescreen_priced_trades": prescreen(g_f, c_f) if len(fin) else "COST_UNAVAILABLE",
            "mean_net_priced": float(fin["net_pnl_pct"].astype(float).mean()) if len(fin) else None,
            "win_rate_priced": float((fin["net_pnl_pct"].astype(float) > 0).mean()) if len(fin) else None}


def run_pair(pair: str, *, data_dir="data") -> dict:
    from bot.quant_core.inference import d10_block_length, stable_seed, time_block_bootstrap_mean_ci
    from bot.quant_core.protocol import CANONICAL
    from bot.quant_core.research_zones import ZONES
    from bot.research.entry_exit_engine import EntryExitEngine
    from bot.research.kalman_filter_engine import KalmanFilterEngine
    from bot.research.walk_forward_engine import canonical_walk_forward
    sa, sb = pair.split("/")
    a, b = load_close(sa, data_dir), load_close(sb, data_dir)
    fa, fb = load_funding(sa, data_dir), load_funding(sb, data_dir)
    for name, s in ((sa, a), (sb, b), (sa + " funding", fa), (sb + " funding", fb)):
        if len(s) and s.index.max() > ZONES.S1:
            raise ZoneViolation(name)
    with tempfile.TemporaryDirectory() as d:
        keng = KalmanFilterEngine(pair_name=pair, symbol_a=sa, symbol_b=sb, ols_beta=1.0, ols_alpha=0.0)
        res = keng.run_canonical(a, b)
        if pd.DatetimeIndex(res.index).max() > ZONES.S1:
            raise ZoneViolation("Kalman output extends past S1")
        eng = EntryExitEngine(pair_name=pair, symbol_a=sa, symbol_b=sb, output_dir=d, rule_set="CANONICAL",
                              funding_rates_a=fa, funding_rates_b=fb)
        tl = eng.run_canonical_frame(res, keng.seed_record)
        gaps = list(eng.funding_gaps)
    gap_ids = {int(m.group(1)) for g in gaps for m in [re.match(r"trade (\d+)", g)] if m}
    trades = _trade_frame(tl, gap_ids, ZONES, CANONICAL)
    edges = quarter_edges()
    wf = canonical_walk_forward(tl, edges)
    sel_end = ZONES.S0 + pd.Timedelta(hours=10_000)          # the screen's Engle-Granger window (first 10,000 S candles)
    trunc = pd.Timedelta(hours=CANONICAL.execution_delay_candles + CANONICAL.require_max_holding())
    folds = []
    for f, (lo, hi) in zip(wf["folds"], zip(edges, edges[1:])):
        m = (trades["decision_time"] >= lo) & (trades["decision_time"] <= hi - pd.Timedelta(hours=1) - trunc)
        s = _summ(trades.loc[m])
        folds.append({**f, "window": "inside the selection test window" if hi <= sel_end else
                      ("after the selection test window (pseudo-OOS within S)" if lo >= sel_end else "straddles the selection window end"),
                      **{k: s[k] for k in ("mean_gross_per_trade", "mean_fees_slippage_per_trade", "n_financing_unavailable",
                                           "prescreen_fees_slippage_only")}})
    post = trades[trades["decision_time"] >= sel_end]
    ci = time_block_bootstrap_mean_ci(trades["gross"].to_numpy(), pd.DatetimeIndex(trades["decision_time"]),
                                      block_hours=d10_block_length(CANONICAL.require_max_holding()),
                                      seed=stable_seed(DIAG_VERSION, pair), n_boot=999) if len(trades) else {"ok": False}
    return {"pair": pair, "label": LABEL, "data_end_used": max(a.index.max(), b.index.max()).isoformat(),
            "funding_coverage_in_S": {sa: [fa.index.min().isoformat(), fa.index.max().isoformat()] if len(fa) else None,
                                      sb: [fb.index.min().isoformat(), fb.index.max().isoformat()] if len(fb) else None},
            "overall_zone_s": _summ(trades), "post_selection_window": _summ(post),
            "gross_mean_block_bootstrap_ci_95": ci, "walk_forward_quarters": folds,
            "walk_forward_meta": {k: wf[k] for k in ("version", "status", "truncation_candles", "n_trades_in_folds",
                                                      "n_trades_excluded")},
            "selection_test_window_end": sel_end.isoformat(), "n_funding_gaps": len(gaps)}


def run_all(pairs=NOMINATED, *, data_dir="data", out_path: Optional[Path] = None) -> dict:
    from bot.quant_core.protocol import METHODOLOGY_VERSION
    from bot.quant_core.provenance import code_state, input_files_fingerprint, write_research_output_provenance
    from bot.quant_core.research_zones import ZONES
    code = code_state()
    files = sorted({str(Path(data_dir) / f"{s}_{k}.csv") for p in pairs for s in p.split("/") for k in ("1h", "funding")})
    out = {"version": DIAG_VERSION, "label": LABEL, "methodology_version": METHODOLOGY_VERSION,
           "zone_s": [ZONES.S0.isoformat(), ZONES.S1.isoformat()], "zones_hash": ZONES.zones_hash(),
           "prescreen_rule": f"mean gross edge per trade >= {PRESCREEN_MULTIPLE} x mean round-trip cost (signed 2026-10-03)",
           "folds": "calendar quarters inside zone S (descriptive choice; owner canonical fold boundaries not set)",
           "code_commit": code.commit, "code_dirty": code.dirty, "inputs": input_files_fingerprint(files),
           "pairs": [run_pair(p, data_dir=data_dir) for p in pairs]}
    if out_path is not None:
        out_path = Path(out_path)
        with open(out_path, "x", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1, default=str)
        write_research_output_provenance(out_path, kind="ZONE_S_PAIR_DIAGNOSTICS", producer="bot.research.zone_s_pair_diagnostics",
                                         inputs=out["inputs"], config={"pairs": list(pairs)}, code=code)
        out["output_path"] = str(out_path)
    return out
