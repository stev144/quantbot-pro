# bot/research/recent_pairs_walkforward.py  # claude code changed: 2026-10-27 — new; Stage 2 of the owner-agreed recent-window pairs plan
"""
STAGE 2 — rolling walk-forward of "select pairs cointegrated over the last 12 months, trade them for one month".

The design is fixed in STAGE2_RECENT_PAIRS_WALKFORWARD_DESIGN_2026-10-27.md (its SHA-256 is recorded in every run).
EXPLORATORY, NOT CLAIM-ELIGIBLE: every candle is pre-seal history (CRRP-2026-10-02). Reuses Stage 1's data rules
(bot.research.recent_cointegration) and the canonical tests in bot.quant_core.
"""
from __future__ import annotations

import glob
import hashlib
import json
import math
import subprocess
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from bot.quant_core.cointegration import engle_granger
from bot.quant_core.half_life import ar1_half_life

DESIGN_FILE = "STAGE2_RECENT_PAIRS_WALKFORWARD_DESIGN_2026-10-27.md"
LABEL = "EXPLORATORY — Stage 2 walk-forward, NOT CLAIM-ELIGIBLE (pre-seal history is pseudo-holdout, CRRP-2026-10-02)"
HOUR = pd.Timedelta(hours=1)
SELECT, TRADE = pd.DateOffset(months=12), pd.DateOffset(months=1)
FIRST_MONTH = {"crypto": pd.Timestamp("2022-10-01", tz="UTC"), "forex": pd.Timestamp("2021-11-01", tz="UTC")}
LAST_MONTH = pd.Timestamp("2026-09-01", tz="UTC")
Q, HL_MIN, HL_MAX = 0.05, 2.0, 120.0
ENTRY_Z, BREAK_Z, BREAK_P, CHECK_EVERY_H = 2.0, 3.0, 0.10, 168
KALMAN_DELTA = 1e-4
NOTIONAL = 1000.0
CRYPTO_FEE, CRYPTO_SLIP = 0.0005, 0.0005    # per side per leg (PERP_TAKER_FEE_RATE; 5 bp slippage)
COVERAGE_MIN, CRYPTO_TOP_N = 0.95, 50


def schwert(n: int) -> int:
    return int(12 * (n / 100) ** 0.25)


def months(asset_class: str) -> List[pd.Timestamp]:
    return list(pd.date_range(FIRST_MONTH[asset_class], LAST_MONTH, freq="MS"))


# ── data ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def load_crypto(data_dir="data"):
    """{coin: DataFrame[close, usdt_vol]} and {coin: funding Series}, everything before the crypto seal."""
    from bot.quant_core.research_zones import ZONES
    end = ZONES.F - HOUR
    perps = sorted(Path(p).name[:-len("_funding.csv")] for p in glob.glob(str(Path(data_dir) / "*_USDT_funding.csv")))
    candles, funding = {}, {}
    for sym in perps:
        path = Path(data_dir) / f"{sym}_1h.csv"
        if not path.exists():
            continue
        cols = pd.read_csv(path, nrows=0).columns
        use = ["timestamp", "close"] + (["qav"] if "qav" in cols else ["volume"])
        d = pd.read_csv(path, usecols=use, float_precision="round_trip")
        d.index = pd.to_datetime(d.pop("timestamp"), utc=True)
        d = d[~d.index.duplicated()].sort_index().loc[:end]
        ZONES.assert_no_sealed_data(d.index, f"stage 2 walk-forward ({sym})")
        d["usdt_vol"] = d["qav"] if "qav" in d else d["volume"] * d["close"]
        candles[sym] = d[["close", "usdt_vol"]].astype(float)
        f = pd.read_csv(Path(data_dir) / f"{sym}_funding.csv", usecols=["timestamp", "funding_rate"])
        f.index = pd.DatetimeIndex(pd.to_datetime(f.pop("timestamp").astype("int64"), unit="ms", utc=True)).floor("s")
        funding[sym] = f["funding_rate"].astype(float).loc[:end].sort_index()
    return candles, funding


def load_forex(data_dir="data/forex"):
    from bot.research.fx_holdout_guard import FX_RESEARCH_DATA_CUTOFF
    end = pd.Timestamp(FX_RESEARCH_DATA_CUTOFF) - HOUR
    out = {}
    for p in sorted(glob.glob(str(Path(data_dir) / "*_1h.csv"))):
        d = pd.read_csv(p, usecols=["timestamp", "close"], float_precision="round_trip")
        d.index = pd.to_datetime(d.pop("timestamp"), utc=True)
        out[Path(p).name[:-len("_1h.csv")]] = d[~d.index.duplicated()].sort_index().loc[:end].astype(float)
    return out


# ── selection ───────────────────────────────────────────────────────────────────────────────────────────────────────
def universe(asset_class: str, data: dict, t: pd.Timestamp, tradable=None) -> List[str]:
    s0, s1 = t - SELECT, t - HOUR
    if asset_class == "crypto":
        expected = len(pd.date_range(s0, s1, freq="h"))
        vol = {}
        for sym, d in data.items():
            w = d.loc[s0:s1]
            if len(w) >= COVERAGE_MIN * expected:
                vol[sym] = float(w["usdt_vol"].resample("1D").sum().median())
        return sorted(sorted(vol, key=lambda s: -vol[s])[:CRYPTO_TOP_N])
    lengths = {s: len(d.loc[s0:s1]) for s, d in data.items() if tradable is None or s in tradable}   # addendum A1
    ref = float(np.median([v for v in lengths.values() if v])) if any(lengths.values()) else 0
    return sorted(s for s, n in lengths.items() if ref and n >= COVERAGE_MIN * ref)


def eg_fixed(la: pd.Series, lb: pd.Series):
    d = pd.concat([la, lb], axis=1, join="inner").dropna()
    if len(d) < 1000:
        return None, d
    r = engle_granger(d.iloc[:, 0], d.iloc[:, 1], min_obs=1000, maxlag=schwert(len(d)), autolag=None)
    return (r.value if r.ok else None), d


def adf_p(resid: pd.Series) -> Optional[float]:
    from statsmodels.tsa.stattools import adfuller
    x = resid.dropna().to_numpy()
    if len(x) < 100:
        return None
    try:
        return float(adfuller(x, maxlag=schwert(len(x)), autolag=None, regression="c")[1])
    except Exception:
        return None


# ── trading one pair for one month ──────────────────────────────────────────────────────────────────────────────────
def trade_pair(a, b, pa: pd.Series, pb: pd.Series, sel: dict, t: pd.Timestamp, variant: str, cost) -> dict:
    """Simulate one variant on one selected pair for the trading month. pa / pb: closes from t − 12 months to the end of
    the month (the trailing break test needs the history). Returns {'trades': [...], 'breaks': {...}, 'beta_drift'}."""
    t_end = t + TRADE - HOUR
    d = pd.concat([pa, pb], axis=1, join="inner").dropna()
    d.columns = ["a", "b"]
    m = d.loc[t:t_end]
    if len(m) < 2:
        return {"trades": [], "breaks": {}, "beta_drift": None}
    la, lb = np.log(m["a"].to_numpy()), np.log(m["b"].to_numpy())
    alpha, beta, mu, sd, hl = sel["alpha"], sel["beta"], sel["mu"], sel["sd"], sel["half_life_h"]
    z_static = (la - alpha - beta * lb - mu) / sd
    # Kalman on (alpha, beta): prior at each candle uses information up to the previous candle only
    x, P = np.array([alpha, beta]), np.zeros((2, 2))
    Qm, R = KALMAN_DELTA / (1 - KALMAN_DELTA) * np.eye(2), sd ** 2
    zk, beta_f = np.empty(len(m)), np.empty(len(m))
    for i in range(len(m)):
        P = P + Qm
        H = np.array([1.0, lb[i]])
        S = float(H @ P @ H + R)
        e = la[i] - float(H @ x)
        zk[i] = e / math.sqrt(S)
        K = P @ H / S
        x = x + K * e
        P = P - np.outer(K, H) @ P
        beta_f[i] = x[1]
    z = z_static if variant == "S" else zk
    ts = m.index
    trades, pos, breaks, dropped, over = [], 0, {"eg_p": 0, "sigma_hl": 0}, False, 0
    entry = None
    checks = [t + pd.Timedelta(hours=CHECK_EVERY_H * k) for k in range(1, 5)]   # run at the first candle at/after each
    n_checks = 0

    def close_at(j, reason):
        nonlocal pos, entry
        trades.append(cost.settle(a, b, entry, ts[j], m["a"].iloc[j], m["b"].iloc[j], reason))
        pos, entry = 0, None

    last = len(m) - 1
    for i in range(last + 1):
        # break rule (ii): static |z| > 3 on every candle for more than 2 half-lives
        over = over + 1 if abs(z_static[i]) > BREAK_Z else 0
        broke = over > 2 * hl
        if broke:
            breaks["sigma_hl"] += 1
        # break rule (i): weekly Engle–Granger on the trailing 12 months
        due = False
        while checks and ts[i] >= checks[0]:      # forex: a check time can fall on a weekend (no candle)
            checks.pop(0)
            due = True
        if not broke and due:
            n_checks += 1
            w = d.loc[ts[i] - SELECT + HOUR: ts[i]]
            r, _ = eg_fixed(np.log(w["a"]), np.log(w["b"]))
            if r is None or r.pvalue > BREAK_P:
                broke = True
                breaks["eg_p"] += 1
        j = min(i + 1, last)                       # fill at the next close
        if broke:
            if pos:
                close_at(j, "break")
            dropped = True
            break
        if i == last:
            if pos:
                close_at(i, "month_end")
            break
        if pos == 1 and z[i] >= 0 or pos == -1 and z[i] <= 0:
            close_at(j, "z_cross")
        elif pos == 0 and not dropped and abs(z[i]) >= ENTRY_Z and j < last:
            pos = -1 if z[i] > 0 else 1                # +1 = long the spread (long A, short beta*B)
            b_e = beta if variant == "S" else beta_f[i]
            entry = {"t": ts[j], "pa": m["a"].iloc[j], "pb": m["b"].iloc[j], "dir": pos, "beta": float(b_e),
                     "variant": variant}
    breaks["checks_run"] = n_checks
    return {"trades": trades, "breaks": breaks, "dropped": dropped,
            "beta_drift": float(beta_f[-1] - beta) if len(beta_f) else None}


class CryptoCost:
    def __init__(self, funding):
        self.funding = funding

    def settle(self, a, b, e, t_exit, pa_x, pb_x, reason):
        na, nb = NOTIONAL, NOTIONAL * abs(e["beta"])
        da = e["dir"]
        db = -da * (1 if e["beta"] >= 0 else -1)
        gross = da * na * (pa_x / e["pa"] - 1) + db * nb * (pb_x / e["pb"] - 1)
        fees = 2 * (na + nb) * (CRYPTO_FEE + CRYPTO_SLIP)
        fund = 0.0
        for sym, n, dleg in ((a, na, da), (b, nb, db)):
            f = self.funding.get(sym)
            if f is not None:
                fund += -dleg * n * float(f.loc[e["t"] + pd.Timedelta(seconds=1): t_exit].sum())   # longs pay positive rates
        return _trade_row(a, b, e, t_exit, reason, gross, fees, fund, pa_x, pb_x)


class ForexCost:
    def __init__(self, snapshot):
        self.p = snapshot["pairs"]

    def _leg(self, sym, n, d, price, days):
        c = self.p.get(sym) or {}
        point, spr = c.get("point"), c.get("h1_spread_mean_pts_30d")
        side = 2 * n * (0.5 * spr * point / price) if point and spr is not None else 0.0
        swap = c.get("swap_long") if d > 0 else c.get("swap_short")
        carry = n * days * swap * point / price if point and swap is not None and c.get("swap_mode") == 1 else 0.0
        return side, carry, bool(point and spr is not None and c.get("swap_mode") == 1)

    def settle(self, a, b, e, t_exit, pa_x, pb_x, reason):
        na, nb = NOTIONAL, NOTIONAL * abs(e["beta"])
        da = e["dir"]
        db = -da * (1 if e["beta"] >= 0 else -1)
        gross = da * na * (pa_x / e["pa"] - 1) + db * nb * (pb_x / e["pb"] - 1)
        days = (t_exit - e["t"]).total_seconds() / 86400
        sa, ca, oka = self._leg(a, na, da, e["pa"], days)
        sb, cb, okb = self._leg(b, nb, db, e["pb"], days)
        row = _trade_row(a, b, e, t_exit, reason, gross, sa + sb, ca + cb, pa_x, pb_x)
        row["cost_complete"] = oka and okb
        return row


def _trade_row(a, b, e, t_exit, reason, gross, fees, carry, pa_x, pb_x):
    return {"pair": f"{a}~{b}", "variant": e["variant"], "dir": e["dir"], "beta": e["beta"],
            "entry": e["t"].isoformat(), "exit": t_exit.isoformat(),
            "hours": (t_exit - e["t"]).total_seconds() / 3600, "reason": reason,
            "gross": gross, "fees": fees, "carry": carry, "net": gross - fees + carry}


# ── one month (runs in a worker) ────────────────────────────────────────────────────────────────────────────────────
_W = {}


def _init(asset_class, data, extra):
    _W.update(asset_class=asset_class, data=data, extra=extra)


def run_month(t: pd.Timestamp) -> dict:
    from statsmodels.stats.multitest import multipletests
    from bot.research.recent_cointegration import peg_flag
    ac, data = _W["asset_class"], _W["data"]
    s0, s1, t_end = t - SELECT, t - HOUR, t + TRADE - HOUR
    tradable = None
    if ac == "forex":   # addendum A1: only pairs the current MT5 broker offers (their spread and swap are known)
        tradable = {s for s, c in _W["extra"]["pairs"].items() if "error" not in c}
    syms = universe(ac, data, t, tradable)
    close = {s: data[s]["close"] for s in syms}
    rows = []
    for a, b in combinations(syms, 2):
        if ac == "forex" and peg_flag(a, b):
            continue
        la, lb = np.log(close[a].loc[s0:s1]), np.log(close[b].loc[s0:s1])
        r, dsel = eg_fixed(la, lb)
        if r is None:
            continue
        resid = dsel.iloc[:, 0] - r.alpha - r.beta * dsel.iloc[:, 1]
        rows.append({"a": a, "b": b, "p": r.pvalue, "alpha": r.alpha, "beta": r.beta,
                     "mu": float(resid.mean()), "sd": float(resid.std(ddof=1)), "resid_sel": resid})
    out = {"month": t.isoformat(), "n_universe": len(syms), "n_testable": len(rows), "selected": [], "trades": [],
           "persist_sel": [], "persist_base": []}
    if not rows:
        return out
    reject, padj, _, _ = multipletests([r["p"] for r in rows], alpha=Q, method="fdr_bh")
    cost = CryptoCost(_W["extra"]) if ac == "crypto" else ForexCost(_W["extra"])
    for r, rej, pa_ in zip(rows, reject, padj):
        r["p_bh"] = float(pa_)
        hl = None
        if rej:
            grid = r["resid_sel"].reindex(pd.date_range(r["resid_sel"].index[0], r["resid_sel"].index[-1], freq="h"))
            h = ar1_half_life(grid, candle_hours=1.0, min_obs=1000)
            hl = h.value.half_life_hours if h.ok else None
        selected = bool(rej) and hl is not None and HL_MIN <= hl <= HL_MAX
        # persistence: frozen-coefficient residual over the trading month
        dm = pd.concat([np.log(close[r["a"]].loc[t:t_end]), np.log(close[r["b"]].loc[t:t_end])], axis=1, join="inner").dropna()
        pp = adf_p(dm.iloc[:, 0] - r["alpha"] - r["beta"] * dm.iloc[:, 1]) if len(dm) else None
        (out["persist_sel"] if selected else out["persist_base"]).append(pp)
        if not selected:
            continue
        sel = {k: r[k] for k in ("alpha", "beta", "mu", "sd")}
        sel["half_life_h"] = hl
        info = {"pair": f"{r['a']}~{r['b']}", "p": r["p"], "p_bh": r["p_bh"], "beta": r["beta"], "half_life_h": hl,
                "persist_p": pp}
        hist_a, hist_b = close[r["a"]].loc[s0:t_end], close[r["b"]].loc[s0:t_end]
        for v in ("S", "K"):
            res = trade_pair(r["a"], r["b"], hist_a, hist_b, sel, t, v, cost)
            for tr in res["trades"]:
                tr["month"] = t.isoformat()
            out["trades"] += res["trades"]
            info[f"breaks_{v}"] = res["breaks"]
            info[f"dropped_{v}"] = res.get("dropped", False)
            if v == "K":
                info["kalman_beta_drift"] = res["beta_drift"]
        out["selected"].append(info)
    return out


# ── whole run ───────────────────────────────────────────────────────────────────────────────────────────────────────
def _monthly_tests(monthly: List[float]) -> dict:
    from scipy import stats
    x = np.asarray(monthly, float)
    n = len(x)
    if n < 3 or np.std(x, ddof=1) == 0:
        return {"n_months": n, "mean": float(x.mean()) if n else None, "t_p_one_sided": None, "sign_p_one_sided": None}
    t, p2 = stats.ttest_1samp(x, 0.0)
    pos, nz = int((x > 0).sum()), int((x != 0).sum())
    sign_p = float(stats.binomtest(pos, nz, 0.5, alternative="greater").pvalue) if nz else None
    return {"n_months": n, "mean": float(x.mean()), "sd": float(x.std(ddof=1)), "total": float(x.sum()),
            "t_stat": float(t), "t_p_one_sided": float(p2 / 2 if t > 0 else 1 - p2 / 2),
            "months_positive": pos, "months_nonzero": nz, "sign_p_one_sided": sign_p,
            "worst_month": float(x.min()), "best_month": float(x.max())}


def summarise(asset_class: str, results: List[dict]) -> dict:
    by_month = {r["month"]: r for r in results}
    ms = sorted(by_month)
    trades = [t for r in results for t in r["trades"]]
    ok = lambda xs: [x for x in xs if x is not None]
    ps, pb = ok([p for r in results for p in r["persist_sel"]]), ok([p for r in results for p in r["persist_base"]])
    out = {"asset_class": asset_class, "months": len(ms),
           "pairs_selected_per_month": [len(by_month[m]["selected"]) for m in ms],
           "persistence": {"selected_n": len(ps), "selected_rate": float(np.mean(np.array(ps) < 0.05)) if ps else None,
                           "baseline_n": len(pb), "baseline_rate": float(np.mean(np.array(pb) < 0.05)) if pb else None}}
    for v in ("S", "K"):
        tv = [t for t in trades if t["variant"] == v]
        for label, mult in (("net", 1), ("net_2x_costs", 2)):
            monthly = [sum(t["gross"] - mult * t["fees"] + (t["carry"] if t["carry"] > 0 else mult * t["carry"])
                           for t in tv if t["month"] == m) for m in ms]
            out[f"{v}_{label}"] = _monthly_tests(monthly)
        out[f"{v}_gross"] = _monthly_tests([sum(t["gross"] for t in tv if t["month"] == m) for m in ms])
        cum = np.cumsum([sum(t["net"] for t in tv if t["month"] == m) for m in ms]) if ms else np.array([])
        peak = np.maximum.accumulate(np.concatenate([[0.0], cum])) if len(cum) else np.array([0.0])
        dd = (np.concatenate([[0.0], cum]) - peak).min() if len(cum) else 0.0
        out[f"{v}_trades"] = {"n": len(tv), "win_rate": float(np.mean([t["net"] > 0 for t in tv])) if tv else None,
                              "net_per_trade": float(np.mean([t["net"] for t in tv])) if tv else None,
                              "gross_per_trade": float(np.mean([t["gross"] for t in tv])) if tv else None,
                              "costs_per_trade": float(np.mean([t["fees"] - t["carry"] for t in tv])) if tv else None,
                              "exit_reasons": pd.Series([t["reason"] for t in tv]).value_counts().to_dict() if tv else {},
                              "max_drawdown_usd": float(dd), "peak_cum_net": float(peak.max())}
    return out


def run(n_workers: int = 6, out_dir="research_data/recent_pairs_wf", classes=("crypto", "forex")) -> Path:
    from concurrent.futures import ProcessPoolExecutor
    design = Path(DESIGN_FILE).read_bytes()
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        commit = None
    result = {"label": LABEL, "design_file": DESIGN_FILE, "design_sha256": hashlib.sha256(design).hexdigest(),
              "code_version": commit, "created_utc": pd.Timestamp.now(tz="UTC").isoformat(),
              "bonferroni_alpha": 0.05 / 4}
    for ac in classes:
        if ac == "crypto":
            data, extra = load_crypto()
        else:
            data = load_forex()
            extra = json.loads(Path(out_dir, "fx_costs_snapshot.json").read_text())
        with ProcessPoolExecutor(max_workers=n_workers, initializer=_init, initargs=(ac, data, extra)) as ex:
            res = list(ex.map(run_month, months(ac)))
        result[ac] = {"summary": summarise(ac, res), "months": res}
    stamp = pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%SZ")
    path = Path(out_dir) / f"stage2_{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as f:
        json.dump(result, f, indent=1, default=lambda v: v.isoformat() if hasattr(v, "isoformat") else
                  (None if isinstance(v, float) and not math.isfinite(v) else str(v)))
    return path


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.getcwd())
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    print(run(n_workers=int(sys.argv[1]) if len(sys.argv) > 1 else 6,
              classes=tuple(sys.argv[2].split(",")) if len(sys.argv) > 2 else ("crypto", "forex")))
