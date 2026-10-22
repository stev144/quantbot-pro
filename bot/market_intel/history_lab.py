# bot/market_intel/history_lab.py
"""'What happened next?' — educational point-in-time research over stored research data.   # claude code changed: 2026-10-21 — new

Chronology is enforced by construction:
  start()  reads ONLY bars with timestamp <= t (the chosen observation time) and returns the state the user could
           have seen then (closes, regime, simple stats) plus a signed token for (asset, t, horizon).
  reveal() requires that token AND the user's recorded hypothesis; only then are bars in (t, t + horizon] read.
Sealed holdouts are respected: the whole reveal window must end before the asset class's research cutoff
(crypto: D-8 holdout F 2026-10-04; FX: FX_RESEARCH_DATA_CUTOFF). Data are the CSVs as stored today (final candle
values; no point-in-time revision history exists), which is stated as a limitation. Educational, not an experiment.
"""
from __future__ import annotations

from typing import Dict

import pandas as pd
from django.core import signing

HYPOTHESES = ("up", "down", "flat")
FLAT_BAND_PCT = 0.25
MAX_HORIZON = 168
_SALT = "market_intel.history_lab.v1"


def research_cutoff(asset_class: str) -> pd.Timestamp:
    if asset_class == "crypto":
        from bot.quant_core.research_zones import ZONES
        return ZONES.F
    from bot.research.fx_holdout_guard import FX_RESEARCH_DATA_CUTOFF
    return FX_RESEARCH_DATA_CUTOFF


def _load(asset_class: str, symbol: str) -> pd.DataFrame:
    from bot.market_intel.quant import csv_path
    df = pd.read_csv(csv_path(asset_class, symbol), usecols=lambda c: c in ("timestamp", "open", "high", "low", "close"))
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").set_index("timestamp")
    return df[df.index < research_cutoff(asset_class)]           # never read sealed rows


def bounds(asset_class: str, symbol: str, horizon: int = 24) -> Dict:
    df = _load(asset_class, symbol)
    if len(df) < 400:
        return {"available": False, "reason": "not enough stored history"}
    return {"available": True, "earliest": df.index[300].isoformat(), "latest": df.index[-horizon - 1].isoformat(),
            "cutoff": research_cutoff(asset_class).isoformat()}


def start(asset_class: str, symbol: str, when: str, horizon: int) -> Dict:
    from bot.market_intel.quant import InvalidRequest, regime
    if not 1 <= int(horizon) <= MAX_HORIZON:
        raise InvalidRequest(f"horizon must be 1..{MAX_HORIZON} hourly bars")
    try:
        t = pd.Timestamp(when)
        t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    except (ValueError, TypeError):
        raise InvalidRequest("invalid date")
    df = _load(asset_class, symbol)
    past = df[df.index <= t]
    if len(past) < 300:
        raise InvalidRequest("choose a later date: at least 300 earlier hourly bars are needed")
    t_obs = past.index[-1]
    after = df[df.index > t_obs]
    if len(after) < horizon:
        raise InvalidRequest("choose an earlier date: the outcome window would reach the sealed holdout / data end")
    window = past.tail(300)
    reg = regime(window, "1h")
    c = window["close"]
    token = signing.dumps({"a": asset_class, "s": symbol, "t": t_obs.isoformat(), "h": int(horizon)}, salt=_SALT)
    return {
        "observed_at": t_obs.isoformat(), "horizon_bars": int(horizon),
        "closes": [[i.isoformat(), float(v)] for i, v in c.tail(168).items()],
        "last_close": float(c.iloc[-1]),
        "change_24h_pct": (c.iloc[-1] / c.iloc[-25] - 1) * 100,
        "change_7d_pct": (c.iloc[-1] / c.iloc[-169] - 1) * 100 if len(c) > 169 else None,
        "regime": reg, "token": token,
        "flat_band_pct": FLAT_BAND_PCT,
        "limitations": [
            "Uses the research CSV as stored today: candles are final values, so this is close to — but not exactly — "
            "what was visible in real time (no revision history).",
            "One example is an anecdote, not evidence of an edge. Many recorded observations, chosen before seeing "
            "outcomes, would be needed even for a descriptive conclusion.",
            "Sealed holdout data is never read: the outcome window must end before "
            f"{research_cutoff(asset_class):%Y-%m-%d %H:%M} UTC.",
        ],
    }


def reveal(asset_class: str, symbol: str, token: str, hypothesis: str) -> Dict:
    from bot.market_intel.quant import InvalidRequest
    if hypothesis not in HYPOTHESES:
        raise InvalidRequest("record a hypothesis first: up, down or flat")
    try:
        payload = signing.loads(token, salt=_SALT, max_age=6 * 3600)
    except signing.BadSignature:
        raise InvalidRequest("invalid or expired observation token")
    if payload["a"] != asset_class or payload["s"] != symbol:
        raise InvalidRequest("token does not belong to this asset")
    t, h = pd.Timestamp(payload["t"]), int(payload["h"])
    df = _load(asset_class, symbol)
    base = float(df.loc[t, "close"])
    nxt = df[df.index > t].head(h)
    end = float(nxt["close"].iloc[-1])
    move = (end / base - 1) * 100
    outcome = "flat" if abs(move) < FLAT_BAND_PCT else ("up" if move > 0 else "down")
    return {
        "observed_at": t.isoformat(), "hypothesis": hypothesis, "outcome": outcome, "matched": outcome == hypothesis,
        "move_pct": move, "max_up_pct": (float(nxt["high"].max()) / base - 1) * 100,
        "max_down_pct": (float(nxt["low"].min()) / base - 1) * 100,
        "path": [[i.isoformat(), float(v)] for i, v in nxt["close"].items()],
        "what_it_shows": "Whether this one observation's direction matched what happened next over the chosen horizon.",
        "what_it_does_not_show": "Whether the reasoning works in general — a single match (or miss) is consistent with luck.",
    }
