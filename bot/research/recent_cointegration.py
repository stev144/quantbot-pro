# bot/research/recent_cointegration.py  # claude code changed: 2026-10-27 — new; Stage 1 of the owner-agreed recent-window pairs plan
"""
STAGE 1 — DESCRIPTIVE scan: which pairs were cointegrated over the LAST 12 MONTHS before each market's seal.

Agreed with the owner 2026-10-27: estimate relationships on recent data only (selection window 12 months), because
old hedge ratios do not describe today's market. This module only DESCRIBES the last 12 months; whether "recently
cointegrated" predicts the next month is Stage 2 (rolling walk-forward), and only forward data after the seals can
ever be claim-eligible (D-8 / CRRP-2026-10-02). Every output is labelled EXPLORATORY, NOT CLAIM-ELIGIBLE.

Windows (candle open times, UTC):
  crypto  ZONES.F − 12 months → ZONES.F − 1 h   (crypto forward holdout sealed from 2026-10-04, D-8)
  forex   FX_RESEARCH_DATA_CUTOFF − 12 months → cutoff − 1 h  (FX sealed from 2026-10-16 08:00, QC-2 / v7)

Per pair (log prices, x = A on y = B with an intercept):
  Engle-Granger with MacKinnon cointegration p-values (bot.quant_core.cointegration.engle_granger, autolag AIC — the
  canonical selection test; ordinary ADF tables are never used for the decision); exact AR(1) half-life of the
  residual (bot.quant_core.half_life, gaps never bridged); Benjamini-Hochberg across ALL testable pairs of the
  asset class (one family per class), reported at q = 0.05 (primary, strict) and q = 0.10 (the platform's
  exploration level); half-life bounds 2-120 h (cointegration_engine constants); and, for BH survivors only, the
  same test on each half of the window (does the relationship hold in both halves?) plus the latest spread z-score.

Universe:
  crypto  coins with Binance perpetual funding data (a short leg needs a perp) and >= 95% candle coverage in the
          window, the 50 largest by median daily USDT volume in the window (liquidity, and a bounded family)
  forex   every pair in data/forex/*_1h.csv with >= 95% coverage of the pair's own trading candles. Pairs whose
          relationship is mechanical through a currency peg (DKK~EUR, HKD~USD) are flagged, not tested as evidence.
"""
from __future__ import annotations

import glob
import json
import math
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from bot.quant_core.cointegration import engle_granger
from bot.quant_core.half_life import ar1_half_life

WINDOW = pd.DateOffset(months=12)
COVERAGE_MIN = 0.95
CRYPTO_TOP_N = 50
Q_PRIMARY, Q_EXPLORATORY = 0.05, 0.10
LABEL = "EXPLORATORY — descriptive Stage 1 scan, NOT CLAIM-ELIGIBLE (pre-seal history is pseudo-holdout, CRRP-2026-10-02)"
PEGS = {"DKK": "EUR", "HKD": "USD"}   # currencies held to an anchor by their central bank


def windows() -> Dict[str, Tuple[pd.Timestamp, pd.Timestamp]]:
    from bot.quant_core.research_zones import ZONES
    from bot.research.fx_holdout_guard import FX_RESEARCH_DATA_CUTOFF
    hour = pd.Timedelta(hours=1)
    fx_end = pd.Timestamp(FX_RESEARCH_DATA_CUTOFF)
    return {"crypto": (ZONES.F - WINDOW, ZONES.F - hour), "forex": (fx_end - WINDOW, fx_end - hour)}


def _read(path: Path, cols=("timestamp", "close")) -> pd.DataFrame:
    d = pd.read_csv(path, usecols=list(cols), float_precision="round_trip")
    d.index = pd.to_datetime(d.pop("timestamp"), utc=True)
    return d[~d.index.duplicated()].sort_index()


def load_crypto(data_dir="data", top_n: int = CRYPTO_TOP_N) -> Tuple[Dict[str, pd.Series], dict]:
    start, end = windows()["crypto"]
    from bot.quant_core.research_zones import ZONES
    expected = len(pd.date_range(start, end, freq="h"))
    perps = sorted(Path(p).name[:-len("_funding.csv")] for p in glob.glob(str(Path(data_dir) / "*_USDT_funding.csv")))
    closes, volume, dropped, estimated = {}, {}, {}, []
    for sym in perps:
        path = Path(data_dir) / f"{sym}_1h.csv"
        if not path.exists():
            dropped[sym] = "no 1h candles"
            continue
        header = pd.read_csv(path, nrows=0).columns
        has_qav = "qav" in header
        d = _read(path, ("timestamp", "close", "qav" if has_qav else "volume")).loc[start:end]
        ZONES.assert_no_sealed_data(d.index, f"recent cointegration scan ({sym})")
        if len(d) < COVERAGE_MIN * expected:
            dropped[sym] = f"coverage {len(d) / expected:.0%}"
            continue
        closes[sym] = d["close"].astype(float)
        usdt = d["qav"].astype(float) if has_qav else d["volume"].astype(float) * d["close"].astype(float)
        if not has_qav:
            estimated.append(sym)   # no quote-volume column: coin volume x close, close enough to rank liquidity
        volume[sym] = float(usdt.resample("1D").sum().median())
    keep = sorted(sorted(volume, key=lambda s: -volume[s])[:top_n])
    universe = {"n_perps": len(perps), "dropped": dropped, "selected": keep,
                "median_daily_usdt_volume": {s: volume[s] for s in keep},
                "volume_estimated_from_base_x_close": sorted(estimated),
                "rule": f"perp listed, >= {COVERAGE_MIN:.0%} coverage, top {top_n} by median daily USDT volume"}
    return {s: closes[s] for s in keep}, universe


def load_forex(data_dir="data/forex") -> Tuple[Dict[str, pd.Series], dict]:
    start, end = windows()["forex"]
    closes, dropped = {}, {}
    files = sorted(glob.glob(str(Path(data_dir) / "*_1h.csv")))
    lengths = {}
    for p in files:
        sym = Path(p).name[:-len("_1h.csv")]
        d = _read(Path(p)).loc[start:end]
        if len(d) and d.index.max() >= windows()["forex"][1] + pd.Timedelta(hours=1):
            raise RuntimeError(f"{sym}: sealed FX candle in the window")   # cannot happen with .loc[:end]; fail closed
        closes[sym], lengths[sym] = d["close"].astype(float), len(d)
    ref = float(np.median(list(lengths.values()))) if lengths else 0.0   # a typical pair's trading candles in the window
    for sym in list(closes):
        if lengths[sym] < COVERAGE_MIN * ref:
            dropped[sym] = f"coverage {lengths[sym] / ref:.0%} of the median pair"
            closes.pop(sym)
    return closes, {"n_files": len(files), "dropped": dropped, "selected": sorted(closes),
                    "rule": f">= {COVERAGE_MIN:.0%} of the median pair's trading candles in the window"}


def peg_flag(a: str, b: str) -> Optional[str]:
    """'mechanical' when, after replacing pegged currencies by their anchors, the two FX pairs are the same pair (or
    its inverse); 'near-constant' when one leg is a pegged pair against its own anchor. None otherwise / non-FX."""
    def legs(s):
        p = s.split("_")
        return tuple(PEGS.get(c, c) for c in p) if len(p) == 2 else None
    la, lb = legs(a), legs(b)
    if not la or not lb:
        return None
    if la[0] == la[1] or lb[0] == lb[1]:
        return "near-constant (currency peg)"
    if set(la) == set(lb):
        return "mechanical (currency peg)"
    return None


def test_pair(a: str, b: str, xa: pd.Series, xb: pd.Series) -> dict:
    """Engle-Granger + exact half-life on the aligned log closes of one pair (A regressed on B)."""
    d = pd.concat([np.log(xa), np.log(xb)], axis=1, join="inner").dropna()
    row = {"pair": f"{a}~{b}", "a": a, "b": b, "n_obs": int(len(d))}
    eg = engle_granger(d.iloc[:, 0], d.iloc[:, 1], min_obs=1000, maxlag=None, autolag="aic")
    if not eg.ok:
        return {**row, "status": "UNTESTABLE", "reason": eg.reason, "p": None}
    e = eg.value
    resid = d.iloc[:, 0] - e.alpha - e.beta * d.iloc[:, 1]
    grid = resid.reindex(pd.date_range(resid.index[0], resid.index[-1], freq="h"))   # gaps stay gaps
    hl = ar1_half_life(grid, candle_hours=1.0, min_obs=1000)
    sd = float(resid.std(ddof=1))
    return {**row, "status": "OK", "p": e.pvalue, "stat": e.statistic, "alpha": e.alpha, "beta": e.beta,
            "half_life_h": hl.value.half_life_hours if hl.ok else None,
            "half_life_status": hl.status.value, "spread_sd": sd,
            "z_last": float((resid.iloc[-1] - resid.mean()) / sd) if sd > 0 else None,
            "last_candle": resid.index[-1].isoformat()}


_STATE = None


def _init(closes):
    global _STATE
    _STATE = closes


def _work(pair):
    a, b = pair
    try:
        return test_pair(a, b, _STATE[a], _STATE[b])
    except Exception as exc:   # one failing pair is recorded, never silently skipped
        return {"pair": f"{a}~{b}", "a": a, "b": b, "status": "UNTESTABLE", "reason": f"{type(exc).__name__}: {exc}",
                "p": None}


def scan(closes: Dict[str, pd.Series], asset_class: str, n_workers: int = 6) -> dict:
    from statsmodels.stats.multitest import multipletests
    from bot.research.cointegration_engine import MAX_HALF_LIFE_HOURS, MIN_HALF_LIFE_HOURS
    pairs = list(combinations(sorted(closes), 2))
    peg = {p: peg_flag(*p) for p in pairs} if asset_class == "forex" else {}
    tested = [p for p in pairs if not peg.get(p)]
    if n_workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=n_workers, initializer=_init, initargs=(closes,)) as ex:
            rows = list(ex.map(_work, tested, chunksize=max(1, len(tested) // (n_workers * 8))))
    else:
        _init(closes)
        rows = [_work(p) for p in tested]
    rows += [{"pair": f"{a}~{b}", "a": a, "b": b, "status": "PEG", "reason": peg[(a, b)], "p": None}
             for a, b in pairs if peg.get((a, b))]
    ok_rows = [r for r in rows if r["status"] == "OK"]
    if ok_rows:
        p = [r["p"] for r in ok_rows]
        for q, key in ((Q_PRIMARY, "bh05"), (Q_EXPLORATORY, "bh10")):
            reject, padj, _, _ = multipletests(p, alpha=q, method="fdr_bh")
            for r, rej, pa in zip(ok_rows, reject, padj):
                r[key], r["p_bh"] = bool(rej), float(pa)
    for r in ok_rows:
        hl = r.get("half_life_h")
        r["half_life_ok"] = hl is not None and MIN_HALF_LIFE_HOURS <= hl <= MAX_HALF_LIFE_HOURS
    # stability: the same test on each half of the window, survivors only (descriptive)
    for r in ok_rows:
        if not r.get("bh10"):
            continue
        d = pd.concat([np.log(closes[r["a"]]), np.log(closes[r["b"]])], axis=1, join="inner").dropna()
        mid = len(d) // 2
        halves = []
        for part in (d.iloc[:mid], d.iloc[mid:]):
            eg = engle_granger(part.iloc[:, 0], part.iloc[:, 1], min_obs=1000, maxlag=None, autolag="aic")
            halves.append(eg.value.pvalue if eg.ok else None)
        r["p_first_half"], r["p_second_half"] = halves
    rows.sort(key=lambda r: (r["p"] is None, r["p"] if r["p"] is not None else 1.0))
    return {"asset_class": asset_class, "window": [w.isoformat() for w in windows()[asset_class]],
            "n_pairs": len(pairs), "n_tested": len(tested), "n_testable": len(ok_rows),
            "n_peg_flagged": len(pairs) - len(tested),
            "n_bh05": sum(1 for r in ok_rows if r.get("bh05")), "n_bh10": sum(1 for r in ok_rows if r.get("bh10")),
            "n_bh05_half_life_ok": sum(1 for r in ok_rows if r.get("bh05") and r["half_life_ok"]),
            "expected_false_at_5pct_raw": round(0.05 * len(ok_rows), 1),
            "n_raw_p_below_05": sum(1 for r in ok_rows if r["p"] < 0.05),
            "half_life_bounds_h": [MIN_HALF_LIFE_HOURS, MAX_HALF_LIFE_HOURS], "rows": rows}


def run(out_dir="research_data/recent_cointegration", n_workers: int = 6) -> Path:
    """Scan both asset classes and write one JSON (never overwrites an earlier scan)."""
    import subprocess
    stamp = pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%SZ")
    out = Path(out_dir) / f"scan_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        commit = None
    c_closes, c_uni = load_crypto()
    f_closes, f_uni = load_forex()
    result = {"label": LABEL, "created_utc": pd.Timestamp.now(tz="UTC").isoformat(), "code_version": commit,
              "method": __doc__.strip().splitlines()[0],
              "crypto": {**scan(c_closes, "crypto", n_workers), "universe": c_uni},
              "forex": {**scan(f_closes, "forex", n_workers), "universe": f_uni}}
    with open(out, "x", encoding="utf-8") as f:
        json.dump(result, f, indent=1, default=lambda v: None if isinstance(v, float) and not math.isfinite(v) else str(v))
    return out


if __name__ == "__main__":
    import os
    import sys
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    print(run(n_workers=int(sys.argv[1]) if len(sys.argv) > 1 else 6))
