# bot/market_intel/quant.py
"""Deterministic market metrics for the Market Intelligence workspace.   # claude code changed: 2026-10-21 — new

Reuses: BinanceKlinesProvider (live crypto candles), the research CSVs (data/*_1h.csv, data/forex/*_1h.csv — read only),
RegimeDetector, PositionSizer, the venue cost table and the MT5 spread history. Every number returned here is computed
in Python from identified data; every block says where its data came from and how fresh it is.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from django.conf import settings

ASSET_CLASSES = ("crypto", "forex")
TIMEFRAMES = {"1h": 1, "4h": 4, "1d": 24}          # hours per bar
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,12}_[A-Z0-9]{2,6}$")
_ROOT = Path(settings.BASE_DIR)
_CRYPTO_DIR = _ROOT / "data"
_FOREX_DIR = _ROOT / "data" / "forex"
ANALYSIS_BARS = 300                                 # bars of the selected timeframe used for regime/evidence


class InvalidRequest(ValueError):
    """Bad asset class / symbol / timeframe / parameter — mapped to HTTP 400/404 by the views."""


# ── validation ──────────────────────────────────────────────────────────────────────────────────────────────
def csv_path(asset_class: str, symbol: str) -> Path:
    return (_CRYPTO_DIR if asset_class == "crypto" else _FOREX_DIR) / f"{symbol}_1h.csv"


def validate(asset_class: str, symbol: str, timeframe: str = "1h") -> Tuple[str, str, str]:
    if asset_class not in ASSET_CLASSES:
        raise InvalidRequest(f"unsupported asset class {asset_class!r}")
    symbol = (symbol or "").upper()
    if not SYMBOL_RE.match(symbol) or not csv_path(asset_class, symbol).exists():
        raise LookupError(f"unknown {asset_class} symbol {symbol!r}")
    if timeframe not in TIMEFRAMES:
        raise InvalidRequest(f"unsupported timeframe {timeframe!r}; use one of {', '.join(TIMEFRAMES)}")
    return asset_class, symbol, timeframe


def display_symbol(symbol: str) -> str:
    return symbol.replace("_", "/")


# ── bars ────────────────────────────────────────────────────────────────────────────────────────────────────
def _read_csv_bars(path: Path, n_rows: int) -> pd.DataFrame:
    """Last n_rows OHLC(V) rows of a 1h CSV (seek near the end; the files are multi-MB)."""
    import io
    with open(path, "rb") as fh:
        header = fh.readline().decode("utf-8")
        fh.seek(0, 2)
        size = fh.tell()
        start = max(len(header.encode()), size - 160 * (n_rows + 5))
        fh.seek(start)
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
    if start > len(header.encode()):
        lines = lines[1:]   # partial first row after the seek
    df = pd.read_csv(io.StringIO(header + "\n".join([l for l in lines if l.strip()][-n_rows:]) + "\n"))
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp").drop_duplicates("timestamp").set_index("timestamp")


def _resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe == "1h":
        return df
    rule = {"4h": "4h", "1d": "1D"}[timeframe]
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    return df.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["close"])


def freshness(last_bar_open: pd.Timestamp, timeframe: str, source: str, now: Optional[pd.Timestamp] = None,
              asset_class: Optional[str] = None) -> Dict:
    """LIVE only for a live source whose last CLOSED bar is at most one bar old; never for saved data.
    claude code changed: 2026-10-25 — forex data that reach the weekend/holiday close are MARKET CLOSED, not
    DELAYED/STALE (owner FX calendar via market_profile.forex_session_status); data that stopped earlier stay STALE."""
    now = now or pd.Timestamp.now(tz="UTC")
    bar = pd.Timedelta(hours=TIMEFRAMES[timeframe])
    age = now - (last_bar_open + bar)
    if source.endswith("_LIVE") and age <= bar + pd.Timedelta(minutes=5):
        state = "LIVE"
    elif age <= bar + pd.Timedelta(hours=6):   # scales with the timeframe: an in-progress daily bar is not "stale"
        state = "DELAYED"
    else:
        state = "STALE"
    out = {"state": state, "age_hours": round(age.total_seconds() / 3600, 2), "last_closed_bar_open": last_bar_open.isoformat()}
    if asset_class == "forex" and state != "LIVE":
        from bot.quant_core.market_profile import explained_by_closure, forex_session_status
        session = forex_session_status(now)
        if explained_by_closure(last_bar_open + bar, session):
            out.update(state="MARKET CLOSED", reopens=session["reopens"], closed_since=session["closed_since"])
    return out


def load_bars(asset_class: str, symbol: str, timeframe: str, n_bars: int = ANALYSIS_BARS) -> Tuple[pd.DataFrame, Dict]:
    """Closed OHLC(V) bars of the timeframe, newest last, plus {source, freshness, note}.

    crypto: live Binance klines (the still-forming candle is dropped); falls back to the research CSV (stale).
    forex:  the hourly MT5 CSV (refreshed hourly by the scheduled task) resampled to the timeframe.
    """
    hours = TIMEFRAMES[timeframe]
    now = pd.Timestamp.now(tz="UTC")
    meta = {"source": "", "note": ""}
    df = pd.DataFrame()
    if asset_class == "crypto":
        try:
            from bot.views.dashboard import _resolve_dashboard_instrument
            from bot.binance_klines_provider import BinanceKlinesProvider
            bars = BinanceKlinesProvider().get_recent_bars(_resolve_dashboard_instrument(symbol), timeframe, n_bars + 1)
            live = bars.data.copy()
            if not live.empty:
                live["timestamp"] = pd.to_datetime(live["timestamp"], utc=True)
                live = live.set_index("timestamp").sort_index()
                live = live[live.index + pd.Timedelta(hours=hours) <= now]          # closed bars only
                df = live[["open", "high", "low", "close"] + (["volume"] if "volume" in live else [])].astype(float)
                meta.update(source="BINANCE_LIVE", note="Binance public klines; the still-forming candle is excluded")
        except Exception as exc:   # offline / provider error: fall back below, and say so
            meta["note"] = f"live Binance candles unavailable ({type(exc).__name__}); showing saved research data"
    if df.empty:
        raw = _read_csv_bars(csv_path(asset_class, symbol), n_bars * hours + 48)
        cols = [c for c in ("open", "high", "low", "close", "volume") if c in raw.columns]
        df = _resample(raw[cols].astype(float), timeframe)
        if timeframe != "1h":
            df = df[df.index + pd.Timedelta(hours=hours) <= raw.index[-1] + pd.Timedelta(hours=1)]   # complete resampled bars only
        if asset_class == "forex":
            meta.update(source="MT5_CSV", note="hourly MT5 candles saved by the scheduled refresh (not tick-live)")
        else:
            meta.update(source="RESEARCH_CSV", note=(meta["note"] or "") + " — crypto research data is sealed at 2026-10-04 (D-8)")
    df = df.tail(n_bars)
    if df.empty:
        return df, {**meta, "freshness": {"state": "UNAVAILABLE"}}
    meta["freshness"] = freshness(df.index[-1], timeframe, meta["source"], now, asset_class)   # claude code changed: 2026-10-25 — asset class for MARKET CLOSED
    meta["bars"] = int(len(df))
    meta["first_bar"] = df.index[0].isoformat()
    meta["volume_kind"] = ("exchange traded volume" if asset_class == "crypto" else "broker tick volume (not traded volume)") \
        if "volume" in df.columns else None
    return df, meta


# ── overview ────────────────────────────────────────────────────────────────────────────────────────────────
def _pct(a, b):
    return None if not b else (a / b - 1) * 100


def overview(asset_class: str, symbol: str, timeframe: str, bars: pd.DataFrame, meta: Dict) -> Dict:
    from bot.views.markets_data import get_live_quotes
    try:
        live = (get_live_quotes() or {}).get(asset_class, {}).get(symbol)
    except Exception:
        live = None
    hourly, hmeta = (bars, meta) if (timeframe == "1h" and len(bars) > 720) else load_bars(asset_class, symbol, "1h", 24 * 31)
    last_close = float(hourly["close"].iloc[-1]) if len(hourly) else None
    price = live["price"] if live else last_close
    changes = {}
    for label, h in (("1h", 1), ("24h", 24), ("7d", 168), ("30d", 720)):
        if len(hourly) > h:
            changes[label] = _pct(price, float(hourly["close"].iloc[-1 - h]))
    session = {"state": "24/7 market", "is_open": True}
    if asset_class == "forex":
        from bot.quant_core.market_profile import FOREX_PROFILE
        is_open = bool(FOREX_PROFILE.calendar.is_trading(pd.DatetimeIndex([pd.Timestamp.now(tz="UTC").floor("h")]))[0])
        session = {"state": "market open" if is_open else "market closed (weekend / holiday session break)", "is_open": is_open}
    return {
        "symbol": symbol, "display": display_symbol(symbol), "asset_class": asset_class,
        "price": price,
        "price_source": (live or {}).get("source") or meta.get("source"),
        "price_as_of": (live or {}).get("as_of") or (hourly.index[-1].isoformat() if len(hourly) else None),
        "price_state": "LIVE" if live and (pd.Timestamp.now(tz="UTC") - pd.Timestamp(live["as_of"])) < pd.Timedelta(hours=2)
                       else (meta.get("freshness") or {}).get("state", "UNAVAILABLE"),
        "bid": (live or {}).get("bid"), "ask": (live or {}).get("ask"),
        "changes_pct": changes,
        "changes_basis": "live price vs hourly closes N hours earlier" if live else "last hourly close vs hourly closes N hours earlier",
        "session": session,
        "timeframe": timeframe,
        "bars_meta": meta,
    }


# ── regime ──────────────────────────────────────────────────────────────────────────────────────────────────
_REGIME_TEXT = {
    "TRENDING_UP": ("Prices have been moving persistently upward on this timeframe.",
                    "Trend-following ideas have the wind behind them, but late entries risk buying an extended move; "
                    "a trend ends without warning, so the stop still matters."),
    "TRENDING_DOWN": ("Prices have been moving persistently downward on this timeframe.",
                      "Buying dips is fighting the measured direction; mean-reversion longs need extra confirmation."),
    "RANGING": ("Price is moving sideways without established directional persistence.",
                "Breakout trades may need additional confirmation; range edges are hypotheses, not guaranteed reversal points."),
    "HIGH_VOLATILITY": ("Recent price swings are large relative to their own history.",
                        "Position sizes based on normal volatility would carry more risk than intended; this project's live "
                        "router does not trade in this regime."),
}
_AGREEMENT = {"HIGH": "3 of 3 indicators agree", "MEDIUM": "2 of 3 indicators agree", "LOW": "only 1 indicator supports it"}


def regime(bars: pd.DataFrame, timeframe: str) -> Dict:
    from bot.engines.regime_detector import RegimeDetector
    det = RegimeDetector()
    if len(bars) < det.min_candles:
        return {"available": False, "reason": f"needs at least {det.min_candles} closed {timeframe} bars, have {len(bars)}"}
    r = det.detect(bars.iloc[-200:].copy())
    what, meaning = _REGIME_TEXT.get(r.regime, ("Unclassified.", ""))
    return {
        "available": True, "regime": r.regime, "agreement": r.confidence,
        "agreement_text": _AGREEMENT.get(r.confidence, r.confidence),
        "agreement_note": "indicator agreement count (ADX, EMA spread, Bollinger width) — not a calibrated probability",
        "adx": r.adx, "atr_ratio": r.atr_ratio, "summary": r.summary,
        "what": what, "meaning": meaning,
        "as_of": bars.index[-1].isoformat(), "timeframe": timeframe, "bars_used": int(min(len(bars), 200)),
        "method": "bot.engines.regime_detector.RegimeDetector (the same classifier the bot and backtester use)",
        "evidence_class": "descriptive — classifies recent bars; it does not forecast",
    }


# ── evidence metrics ────────────────────────────────────────────────────────────────────────────────────────
def _metric(key, label, value, unit, measured, sample, meaning, limitation, kind="descriptive"):
    return {"key": key, "label": label, "value": None if value is None or (isinstance(value, float) and not math.isfinite(value)) else value,
            "unit": unit, "measured": measured, "sample": sample, "meaning": meaning, "limitation": limitation, "kind": kind}


def evidence(bars: pd.DataFrame, timeframe: str, meta: Dict) -> Dict:
    if len(bars) < 60:
        return {"available": False, "reason": f"only {len(bars)} bars"}
    c = bars["close"]
    lr = np.log(c).diff().dropna()
    per_year = 24 * 365 / TIMEFRAMES[timeframe]
    n = len(lr)
    win = min(50, len(bars))
    recent = lr.tail(min(30, n))
    vol_now = float(recent.std() * math.sqrt(per_year) * 100)
    roll = lr.rolling(30).std().dropna() * math.sqrt(per_year) * 100
    vol_pct = float((roll < vol_now).mean() * 100) if len(roll) > 20 else None
    ema20, ema50 = c.ewm(span=20, adjust=False).mean(), c.ewm(span=50, adjust=False).mean()
    hi, lo = float(bars["high"].tail(win).max()), float(bars["low"].tail(win).min())
    last = float(c.iloc[-1])
    pos = (last - lo) / (hi - lo) * 100 if hi > lo else None
    ac1 = float(lr.autocorr(1)) if n > 30 else None
    se = 1 / math.sqrt(n) if n else None
    tr = pd.concat([bars["high"] - bars["low"], (bars["high"] - c.shift()).abs(), (bars["low"] - c.shift()).abs()], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1])
    sample = f"{n} {timeframe} returns, {bars.index[0]:%Y-%m-%d %H:%M} → {bars.index[-1]:%Y-%m-%d %H:%M} UTC"
    m = [
        _metric("ret_window", f"Return over last {min(24, n)} bars", _pct(last, float(c.iloc[-1 - min(24, n)])), "%",
                "simple % change of the close", f"last {min(24, n)} {timeframe} bars", "how far price has travelled recently",
                "a single window says nothing about the next one"),
        _metric("vol_now", "Realised volatility (annualised)", vol_now, "%",
                "std of log returns over the last 30 bars × √(bars per year)", f"30 {timeframe} bars",
                "typical size of moves right now, on a yearly scale", "backward-looking; volatility clusters and can jump"),
        _metric("vol_rank", "Volatility rank", vol_pct, "percentile",
                "share of 30-bar windows in the sample with lower volatility than now", sample,
                "above ~80 = unusually turbulent for this asset; below ~20 = unusually calm", "rank within this sample only"),
        _metric("ema_spread", "EMA20 vs EMA50 spread", _pct(float(ema20.iloc[-1]), float(ema50.iloc[-1])), "%",
                "(EMA20 / EMA50 − 1)", "exponential weights over 20 / 50 bars", "positive = short-term average above long-term (up-trend tilt)",
                "lags price; flips late at turning points"),
        _metric("above_ema50", "Bars closing above EMA50", float((c.tail(win) > ema50.tail(win)).mean() * 100), "% of bars",
                "share of the last 50 closes above EMA50", f"last {win} bars", "persistence of the trend side",
                "a count, not a test of significance"),
        _metric("range_pos", "Position in recent range", pos, "% of range",
                f"(close − {win}-bar low) / ({win}-bar high − {win}-bar low)", f"last {win} bars",
                "0 = at the range low, 100 = at the range high",
                "range edges are observed extremes — hypotheses, not guaranteed support/resistance"),
        _metric("range_hi", f"{win}-bar high", hi, "price", "highest high", f"last {win} bars", "upper edge of the recent range",
                "an observed extreme, not a level price must respect"),
        _metric("range_lo", f"{win}-bar low", lo, "price", "lowest low", f"last {win} bars", "lower edge of the recent range",
                "an observed extreme, not a level price must respect"),
        _metric("drawdown_from_high", "Distance from range high", _pct(last, hi), "%", "close vs the range high", f"last {win} bars",
                "size of the current pullback from the recent peak", "depends on the window length chosen"),
        _metric("atr14", "ATR(14)", atr14, "price", "average true range over 14 bars", f"14 {timeframe} bars",
                "typical bar-to-bar travel; used to scale stops", "assumes the near future resembles the last 14 bars"),
        _metric("autocorr1", "Lag-1 return autocorrelation", ac1, "ρ",
                "correlation of each return with the previous one", sample,
                f"positive = momentum-like, negative = mean-reverting-like; |ρ| below ~{2 * se:.3f} (2 standard errors) is indistinguishable from 0"
                if se else "", "descriptive only — not a validated predictive signal on this project"),
    ]
    if "volume" in bars.columns and bars["volume"].tail(30).mean() > 0:
        v = bars["volume"]
        m.append(_metric("volume_ratio", "Volume vs 30-bar average", float(v.iloc[-1] / v.tail(30).mean()), "×",
                         "last bar volume / mean of last 30 bars", "30 bars", "above 1 = more activity than usual",
                         meta.get("volume_kind") or "volume"))
    return {"available": True, "metrics": m, "sample": sample, "atr14": atr14,
            "note": "All values are computed in Python from the bars named above. None of them has been validated out of sample "
                    "as a trading signal on this platform (CRRP-2026-10-02)."}


# ── risk scenario ───────────────────────────────────────────────────────────────────────────────────────────
def _quote_to_usd(asset_class: str, symbol: str) -> Tuple[float, str]:
    quote = symbol.split("_")[1]
    if quote in ("USD", "USDT"):
        return 1.0, "quote currency is USD/USDT"
    from bot.views.markets_data import get_markets_list
    fx = {q["symbol"]: q for q in get_markets_list().get("forex", []) if q.get("available")}
    if f"{quote}_USD" in fx:
        return float(fx[f"{quote}_USD"]["price"]), f"{quote}/USD last close"
    if f"USD_{quote}" in fx:
        return 1.0 / float(fx[f"USD_{quote}"]["price"]), f"1 / USD/{quote} last close"
    raise InvalidRequest(f"no {quote}/USD conversion available")


def _cost_model(asset_class: str, symbol: str, price: float) -> Dict:
    if asset_class == "crypto":
        from bot.config.execution_costs import get_venue_execution_costs
        c = get_venue_execution_costs("binance")
        return {"fee_rate": c["fee_rate"], "slippage_rate": c["slippage_rate"], "spread_price": 0.0,
                "labels": {"fee": "Binance taker fee 0.10 % per side (configured model, not your account tier)",
                           "slippage": "0.05 % per side (configured estimate)", "spread": "inside the slippage estimate"},
                "measured": False}
    spread_px, label = 0.0, "unavailable"
    sp = _ROOT / "data" / "forex" / "spreads" / f"{symbol}_1h_spread.csv"
    if sp.exists():
        s = pd.read_csv(sp).tail(24 * 30)
        point = float(s["point"].iloc[-1])
        med = float(s["spread_points"].median())
        spread_px = med * point
        label = f"median MT5 bar spread over the last 30 days = {med:.0f} points (bar minimum — optimistic)"
    return {"fee_rate": 0.0, "slippage_rate": 0.0, "spread_price": spread_px,
            "labels": {"fee": "commission unavailable from MT5 (charged as 0 — may understate cost)", "slippage": "not modelled (0)",
                       "spread": label}, "measured": bool(spread_px)}


def risk_scenario(asset_class: str, symbol: str, *, side: str, entry: float, stop: float, target: float,
                  balance: float, risk_pct: float, atr: Optional[float] = None) -> Dict:
    """User-specified trade scenario. Every input is shown back as an assumption; nothing is executed."""
    from bot.config.risk import MAX_RISK_PCT
    from bot.risk.position_sizer import PositionSizer
    side = side.upper()
    if side not in ("LONG", "SHORT"):
        raise InvalidRequest("side must be LONG or SHORT")
    for name, v in (("entry", entry), ("stop", stop), ("target", target), ("balance", balance), ("risk_pct", risk_pct)):
        if v is None or not math.isfinite(v) or v <= 0:
            raise InvalidRequest(f"{name} must be a positive number")
    if risk_pct > 100:
        raise InvalidRequest("risk_pct is a percentage (e.g. 1 for 1 %)")
    if (side == "LONG" and not (stop < entry < target)) or (side == "SHORT" and not (target < entry < stop)):
        raise InvalidRequest("for LONG use stop < entry < target; for SHORT use target < entry < stop")
    q2usd, conv = _quote_to_usd(asset_class, symbol)
    capped = min(risk_pct / 100, MAX_RISK_PCT)
    sizer = PositionSizer(risk_pct=capped)
    qty = float(sizer.calculate(balance, entry * q2usd, stop * q2usd))
    if qty <= 0:
        raise InvalidRequest("the position sizer returned 0 for these inputs")
    cost = _cost_model(asset_class, symbol, entry)
    sign = 1 if side == "LONG" else -1

    def leg_costs(exit_px):
        fees = (entry + exit_px) * qty * cost["fee_rate"] * q2usd
        slip = (entry + exit_px) * qty * cost["slippage_rate"] * q2usd
        spread = cost["spread_price"] * qty * q2usd
        return fees, slip, spread

    out = {}
    for name, px in (("target", target), ("stop", stop)):
        gross = sign * (px - entry) * qty * q2usd
        fees, slip, spread = leg_costs(px)
        out[name] = {"exit_price": px, "gross_usd": gross, "fees_usd": fees, "slippage_usd": slip, "spread_usd": spread,
                     "net_usd": gross - fees - slip - spread}
    risk_usd = abs(out["stop"]["gross_usd"])
    rr_gross = out["target"]["gross_usd"] / risk_usd if risk_usd else None
    rr_net = out["target"]["net_usd"] / abs(out["stop"]["net_usd"]) if out["stop"]["net_usd"] else None
    res = {
        "assumptions": {"side": side, "entry": entry, "stop": stop, "target": target, "balance_usd": balance,
                        "risk_pct_requested": risk_pct, "risk_pct_applied": capped * 100,
                        "risk_cap_note": f"capped at the platform maximum {MAX_RISK_PCT * 100:.1f} %" if risk_pct / 100 > MAX_RISK_PCT else "",
                        "quote_to_usd": q2usd, "conversion": conv},
        "quantity": qty, "quantity_unit": f"{symbol.split('_')[0]} units",
        "lots": qty / 100_000 if asset_class == "forex" else None,
        "notional_usd": qty * entry * q2usd,
        "planned_risk_usd": risk_usd, "rr_gross": rr_gross, "rr_net": rr_net,
        "outcomes": out, "cost_labels": cost["labels"], "costs_measured": cost["measured"],
        "sizer": "bot.risk.position_sizer.PositionSizer (fixed-fractional, floors quantity, hard cap) — the bot's own sizer",
        "note": "Scenario only: nothing is sent to any exchange. Gross excludes all costs; net subtracts the cost estimates above.",
    }
    if atr:
        res["stop_in_atr"] = abs(entry - stop) / atr
    return res


def adverse_move_context(bars: pd.DataFrame, distance_pct: float, horizon: int = 24) -> Optional[Dict]:
    """How often, in this sample, the market moved against a position by at least `distance_pct` within `horizon` bars."""
    if len(bars) < horizon + 30 or distance_pct <= 0:
        return None
    c, lo, hi = bars["close"].to_numpy(), bars["low"].to_numpy(), bars["high"].to_numpy()
    n = len(c) - horizon
    down = sum(1 for i in range(n) if (lo[i + 1:i + 1 + horizon].min() / c[i] - 1) * 100 <= -distance_pct)
    up = sum(1 for i in range(n) if (hi[i + 1:i + 1 + horizon].max() / c[i] - 1) * 100 >= distance_pct)
    return {"horizon_bars": horizon, "distance_pct": distance_pct, "samples": n,
            "long_stop_hit_rate_pct": down / n * 100, "short_stop_hit_rate_pct": up / n * 100,
            "note": "overlapping windows from the same sample — descriptive frequency, not an independent probability"}
