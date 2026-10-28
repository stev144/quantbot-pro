# bot/research/funding_watch.py  # claude code changed: 2026-10-28 — new: live funding watch (owner: "build it with the 5 gates")
"""Live funding watch for the crypto funding carry study (bot/research/funding_carry_rule.py).

For each watched coin: would the study's cost-hurdle rule be IN right now? It reads Binance's SETTLED funding of the
last 14 days (public API), computes the same trailing 7-day sum the study uses, and compares it with the same round-
trip cost. DISPLAY ONLY — not a signal, not evidence, no forward test is registered (see the study memo).

Five gates
  G1 software    fail fast: one 3 s ping of the futures API before any request; stop after MAX_CONSECUTIVE_FAILURES
                 coins fail in a row; every coin carries its own status and reason; never raises to the page
  G2 maths       F7 = funding_carry_rule.trailing_sum (same function), hurdle = funding_carry_rule.RT (same constant);
                 annual equivalent = F7 · 365 / 7 (a linear extrapolation, labelled)
  G3 statistics  settled rates only (never Binance's predicted next rate); a FULL 7-day window is required; the latest
                 settlement must be younger than two funding intervals, else STALE (no status shown)
  G4 quant       hurdle = base-tier round trip incl. slippage (0.5 %); 4 h and 8 h funding schedules both handled
  G5 governance  nothing is written to research_data (the crypto holdout seal applies to research files; live funding
                 shown on a page is like the Markets page's live prices); labelled DESCRIPTIVE
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

import pandas as pd

from bot.research.funding_carry_rule import RT, WINDOW, trailing_sum

logger = logging.getLogger(__name__)

WATCH_COINS = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT", "LINK/USDT")
LOOKBACK = pd.Timedelta(days=14)            # two windows: enough for a full 7-day sum on any schedule
MAX_CONSECUTIVE_FAILURES = 2
FAPI_PING = "https://fapi.binance.com/fapi/v1/ping"
CACHE_KEY = "funding_watch_v1"
CACHE_SECONDS = 600                         # funding settles every 4–8 h; 10 minutes never hides a settlement for long
LABEL = ("DESCRIPTIVE — live funding vs the study's cost hurdle. Not a trading signal, not evidence; no forward test "
         "is registered. Study: 2024 was the only year it paid about a T-bill yield on safely collateralised capital.")

ON, BELOW, NEGATIVE, STALE, INSUFFICIENT, UNAVAILABLE = "ON", "BELOW_HURDLE", "NEGATIVE", "STALE", "INSUFFICIENT", "UNAVAILABLE"


def futures_reachable(timeout: float = 3.05) -> bool:
    import requests
    try:
        return requests.get(FAPI_PING, timeout=(timeout, timeout)).status_code == 200
    except Exception as e:
        logger.warning("funding watch: futures API unreachable: %s", e)
        return False


def _default_fetch(symbol: str, since_ms: int) -> pd.DataFrame:
    from bot.engines.derivatives_data import fetch_funding_rate_history
    return fetch_funding_rate_history(symbol, since_ms)


def classify(rates: pd.Series, now: pd.Timestamp) -> Dict:
    """Status of one coin from its settled funding (index = settlement time, UTC; values = rate per settlement)."""
    r = rates.dropna().sort_index()
    if len(r) < 3:
        return {"status": INSUFFICIENT, "reason": f"only {len(r)} settlements in the last {LOOKBACK.days} days"}
    interval = pd.Series(r.index).diff().median()
    last_t = r.index[-1]
    if now - last_t > 2 * interval + pd.Timedelta(minutes=5):
        return {"status": STALE, "reason": f"latest settlement {last_t:%Y-%m-%d %H:%M} UTC is older than two "
                                           f"{interval} intervals", "last_settlement": last_t.isoformat()}
    f7 = trailing_sum(r).iloc[-1]
    if pd.isna(f7):
        return {"status": INSUFFICIENT, "reason": "less than a full 7-day window of settlements"}
    status = ON if f7 >= RT else NEGATIVE if f7 < 0 else BELOW
    return {"status": status, "f7_pct": float(f7 * 100), "annual_equiv_pct": float(f7 * 365 / 7 * 100),
            "hurdle_pct": RT * 100, "times_hurdle": float(f7 / RT), "latest_rate_pct": float(r.iloc[-1] * 100),
            "interval_hours": float(interval / pd.Timedelta(hours=1)), "last_settlement": last_t.isoformat(),
            "n_settlements_7d": int((r.index > last_t - WINDOW).sum())}


def compute_watch(fetch: Optional[Callable[[str, int], pd.DataFrame]] = None, now: Optional[pd.Timestamp] = None,
                  probe: Optional[Callable[[], bool]] = None, coins=WATCH_COINS) -> Dict:
    now = now or pd.Timestamp.now(tz="UTC")
    out = {"label": LABEL, "computed_at": now.isoformat(), "hurdle_pct": RT * 100, "coins": []}
    if not (probe or futures_reachable)():
        out["available"] = False
        out["reason"] = "Binance futures API did not answer within 3 s — check the connection, then reload."
        return out
    fetch = fetch or _default_fetch
    since_ms = int((now - LOOKBACK).timestamp() * 1000)
    fails = 0
    for i, sym in enumerate(coins):
        if fails >= MAX_CONSECUTIVE_FAILURES:
            out["coins"] += [{"symbol": s, "status": UNAVAILABLE, "reason": "not fetched (connection kept failing)"}
                             for s in coins[i:]]
            break
        try:
            df = fetch(sym, since_ms)
            if df is None or df.empty:
                out["coins"].append({"symbol": sym, "status": UNAVAILABLE, "reason": "no funding returned"})
                fails += 1
                continue
            rates = pd.Series(pd.to_numeric(df["funding_rate"], errors="coerce").to_numpy(),
                              index=pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True))
            out["coins"].append({"symbol": sym, **classify(rates[rates.index <= now], now)})
            fails = 0
        except Exception as e:
            logger.warning("funding watch: %s failed: %s", sym, e)
            out["coins"].append({"symbol": sym, "status": UNAVAILABLE, "reason": f"{type(e).__name__}: {str(e)[:120]}"})
            fails += 1
    out["available"] = any(c["status"] in (ON, BELOW, NEGATIVE) for c in out["coins"])
    out["n_on"] = sum(c["status"] == ON for c in out["coins"])
    if not out["available"]:
        out["reason"] = "no coin could be classified right now"
    return out


def cached_watch() -> Dict:
    """Page entry point: 10-minute cache; an unavailable result is cached for one minute only."""
    from django.core.cache import cache
    w = cache.get(CACHE_KEY)
    if w is None:
        w = compute_watch()
        cache.set(CACHE_KEY, w, CACHE_SECONDS if w.get("available") else 60)
    return w
