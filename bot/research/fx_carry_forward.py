# bot/research/fx_carry_forward.py  # claude code changed: 2026-10-28 — new: FX carry study step 3 (FXC-F1)
"""Frozen forward paper position FXC-F1 (FX_CARRY_FORWARD_PREREGISTRATION_2026-10-28.md). Paper only: no orders.

State lives in an append-only JSON ledger: one REGISTERED event (design hash, T0), SNAPSHOT events (the broker's live
swap and spread of a day), and the daily book is REPLAYED from T0 on every update, so a missed day changes nothing and
results are reproducible from the ledger plus the price files."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

DESIGN_FILE = "FX_CARRY_FORWARD_PREREGISTRATION_2026-10-28.md"
REGISTRATION_ID = "FXC-F1"
PAIRS = ("USD/JPY", "USD/CHF")
CAPITAL = 10_000.0
TARGET_VOL = 0.05
VOL_LOOKBACK = 60
TRADING_DAYS = 260.0
LOT_UNITS = 1_000.0                 # 0.01 lot of a USD-based pair = 1,000 USD
STOP_DRAWDOWN = 0.10
DEFAULT_LEDGER = Path("research_data/fx_carry/forward/ledger_FXC-F1.json")
FX_DIR = Path(__file__).resolve().parents[2] / "data" / "forex"


class ForwardTestError(RuntimeError):
    pass


# ── ledger ──────────────────────────────────────────────────────────────────────────────────────────────────────
def read_ledger(path: Path = DEFAULT_LEDGER) -> Dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"id": REGISTRATION_ID, "events": []}


def _write(ledger: Dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=1), encoding="utf-8")
    tmp.replace(path)


def register(signed_by: str, now: Optional[pd.Timestamp] = None, path: Path = DEFAULT_LEDGER,
             design_path: Path = Path(DESIGN_FILE)) -> Dict:
    """Start the clock. Refuses a second registration (a changed design is a new registration id)."""
    ledger = read_ledger(path)
    if any(e["type"] == "REGISTERED" for e in ledger["events"]):
        raise ForwardTestError(f"{REGISTRATION_ID} is already registered — rules cannot change after the start")
    now = (now or pd.Timestamp.now(tz="UTC")).tz_convert("UTC")
    ledger["events"].append({"type": "REGISTERED", "at": now.isoformat(), "signed_by": signed_by,
                             "design_file": str(design_path), "design_sha256": hashlib.sha256(design_path.read_bytes()).hexdigest(),
                             "rules": {"pairs": PAIRS, "capital": CAPITAL, "target_vol": TARGET_VOL,
                                       "vol_lookback": VOL_LOOKBACK, "lot_units": LOT_UNITS,
                                       "stop_drawdown": STOP_DRAWDOWN}})
    _write(ledger, path)
    return ledger


def add_snapshot(terms: Dict[str, Dict], now: Optional[pd.Timestamp] = None, path: Path = DEFAULT_LEDGER) -> None:
    """Record the broker's live swap/spread for the pairs (one per UTC day; a later one the same day replaces it)."""
    ledger = read_ledger(path)
    if not any(e["type"] == "REGISTERED" for e in ledger["events"]):
        raise ForwardTestError("not registered")
    now = (now or pd.Timestamp.now(tz="UTC")).tz_convert("UTC")
    day = str(now.date())
    ledger["events"] = [e for e in ledger["events"] if not (e["type"] == "SNAPSHOT" and e["day"] == day)]
    ledger["events"].append({"type": "SNAPSHOT", "day": day, "at": now.isoformat(),
                             "terms": {p: {k: terms[p][k] for k in ("point", "swap_mode", "swap_long", "bid", "ask")}
                                       for p in PAIRS}})
    _write(ledger, path)


# ── replay ──────────────────────────────────────────────────────────────────────────────────────────────────────
def daily_closes(symbol: str) -> pd.Series:
    p = FX_DIR / f"{symbol.replace('/', '_')}_1h.csv"
    d = pd.read_csv(p, usecols=["timestamp", "close"])
    d.index = pd.to_datetime(d.pop("timestamp"), utc=True)
    return d["close"].astype(float).sort_index().resample("1D").last().dropna()


def _snapshot_for(snaps: List[Dict], day: pd.Timestamp) -> Optional[Dict]:
    usable = [s for s in snaps if pd.Timestamp(s["day"], tz="UTC") <= day]
    return usable[-1]["terms"] if usable else (snaps[0]["terms"] if snaps else None)


def target_units(rets: pd.DataFrame, equity: float) -> Dict[str, float]:
    """Equal-risk weights scaled to TARGET_VOL with the trailing covariance; USD units rounded DOWN to LOT_UNITS."""
    hist = rets.tail(VOL_LOOKBACK)
    if len(hist) < VOL_LOOKBACK:
        raise ForwardTestError(f"only {len(hist)} days of history for the volatility window ({VOL_LOOKBACK} needed)")
    sig = hist.std(ddof=1) * math.sqrt(TRADING_DAYS)
    raw = 1.0 / sig
    cov = hist.cov() * TRADING_DAYS
    w = raw * (TARGET_VOL / math.sqrt(float(raw @ cov @ raw)))
    return {p: math.floor(w[p] * equity / LOT_UNITS) * LOT_UNITS for p in PAIRS}


def replay(ledger: Dict, closes: Optional[Dict[str, pd.Series]] = None, through: Optional[pd.Timestamp] = None) -> Dict:
    """Rebuild the book day by day from the first full UTC day after T0 up to `through` (default: the last day both
    pairs have a close, excluding today's incomplete day)."""
    reg = next((e for e in ledger["events"] if e["type"] == "REGISTERED"), None)
    if reg is None:
        raise ForwardTestError("not registered")
    snaps = sorted((e for e in ledger["events"] if e["type"] == "SNAPSHOT"), key=lambda e: e["day"])
    t0 = pd.Timestamp(reg["at"])
    closes = closes or {p: daily_closes(p) for p in PAIRS}
    px = pd.DataFrame(closes).dropna()
    today = pd.Timestamp.now(tz="UTC").normalize()
    through = through or (px.index[px.index < today][-1] if (px.index < today).any() else None)
    rets = (1.0 - px.shift(1) / px).dropna()                      # USD-notional returns of long USD/XXX
    first_day = t0.normalize() + pd.Timedelta(days=1)
    days = px.index[(px.index >= first_day) & (px.index <= through)] if through is not None else px.index[:0]
    units = {p: 0.0 for p in PAIRS}
    cash_equity, peak, stopped, rows, prev = CAPITAL, CAPITAL, None, [], None
    exp_carry = 0.0                                               # carry the DAY-ONE snapshot implied (check 1)
    first_terms = snaps[0]["terms"] if snaps else None
    for k, d in enumerate(days):
        terms = _snapshot_for(snaps, d)
        if terms is None:
            raise ForwardTestError("no broker snapshot recorded yet — run the update with MT5 open")
        if any(int(terms[p]["swap_mode"]) != 1 for p in PAIRS):
            raise ForwardTestError("broker swap is not quoted in POINTS — the frozen carry formula does not apply")
        P = px.loc[d]
        pnl_price = pnl_carry = cost = 0.0
        if prev is not None:
            Pp = px.loc[prev]
            ndays = (d - prev).days
            for p in PAIRS:
                pnl_price += units[p] * (P[p] - Pp[p]) / P[p]
                t = terms[p]
                pnl_carry += units[p] * t["swap_long"] * t["point"] / P[p] * ndays
                if first_terms:
                    exp_carry += units[p] * first_terms[p]["swap_long"] * first_terms[p]["point"] / P[p] * ndays
        cash_equity += pnl_price + pnl_carry
        peak = max(peak, cash_equity)
        action = None
        if cash_equity <= (1.0 - STOP_DRAWDOWN) * peak and any(units.values()):
            for p in PAIRS:                                        # close everything at this close (sell at bid)
                cost += units[p] * (terms[p]["ask"] - terms[p]["bid"]) / 2.0 / P[p]
                units[p] = 0.0
            stopped, action = str(d.date()), "STOP"
        elif stopped is None and (k == 0 or d.month != prev.month):
            new = target_units(rets.loc[:prev if prev is not None else d - pd.Timedelta(days=1)], cash_equity)
            for p in PAIRS:
                cost += abs(new[p] - units[p]) * (terms[p]["ask"] - terms[p]["bid"]) / 2.0 / P[p]
            units, action = new, "REBALANCE"
        cash_equity -= cost
        rows.append({"day": str(d.date()), "equity": cash_equity, "price_pnl": pnl_price, "carry_pnl": pnl_carry,
                     "cost": cost, "units": dict(units), "action": action,
                     "drawdown_pct": (cash_equity / peak - 1.0) * 100})
        prev = d
        if stopped:
            break
    return {"registered": reg, "rows": rows, "stopped": stopped, "expected_carry_day_one": exp_carry,
            "n_snapshots": len(snaps)}


def report(state: Dict) -> Dict:
    """The four pre-registered checks, computed from the replay (descriptive)."""
    rows = state["rows"]
    if not rows:
        return {"days": 0, "status": "WAITING — no full UTC day after T0 has closed yet"}
    eq = pd.Series([r["equity"] for r in rows], index=pd.to_datetime([r["day"] for r in rows]))
    r = eq.pct_change().dropna()
    years = max((eq.index[-1] - eq.index[0]).days, 1) / 365.0
    carry = sum(x["carry_pnl"] for x in rows)
    cost = sum(x["cost"] for x in rows)
    vol = float(r.std(ddof=1) * math.sqrt(TRADING_DAYS) * 100) if len(r) > 2 else None
    exp = state["expected_carry_day_one"]
    return {
        "days": len(rows), "first_day": rows[0]["day"], "last_day": rows[-1]["day"],
        "equity": round(eq.iloc[-1], 2), "return_pct": round((eq.iloc[-1] / CAPITAL - 1) * 100, 3),
        "max_drawdown_pct": round(float((eq / eq.cummax() - 1).min() * 100), 3),
        "carry_usd": round(carry, 2), "cost_usd": round(cost, 2), "realised_vol_pct": vol,
        "units": rows[-1]["units"], "stopped": state["stopped"],
        "checks": {
            "1_carry_delivered": None if exp <= 0 else {"ratio": round(carry / exp, 3), "pass": carry >= 0.5 * exp},
            "2_vol_3_to_8pct": None if vol is None else {"value": round(vol, 2), "pass": 3.0 <= vol <= 8.0},
            "3_cost_le_0.10pct_yr": {"value_pct_yr": round(cost / CAPITAL / years * 100, 4),
                                     "pass": cost / CAPITAL / years <= 0.0010},
            "4_loss_limit_held": {"max_dd_pct": round(float((eq / eq.cummax() - 1).min() * 100), 3),
                                  "pass": state["stopped"] is not None or float((eq / eq.cummax() - 1).min()) > -STOP_DRAWDOWN},
        },
        "note": "Checks are judged at 12 months or at a stop; earlier values are progress only.",
    }
