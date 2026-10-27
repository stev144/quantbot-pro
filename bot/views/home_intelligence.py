# claude code changed: 2026-10-26 — new file; home page "Quantitative Market Intelligence" (owner brief 2026-10-26).
# Replaces bot/views/market_pulse.py (same day, never committed). Every number here is either computed from a named
# source with its timestamp, or returned as {"available": False, "reason": ...}. Nothing in this module changes the
# regime detector, the strategies, the router or any research output; it only reads them.
#
# Sources
#   live crypto     markets_data.get_live_quotes()  Binance 24 h ticker, 30 s memory cache (one request)
#   live forex      markets_data.get_live_quotes()  MT5 ticks; 24 h change vs the CSV close ~24 h earlier
#   universe        market_intelligence_data.get_market_regime_overview()  RegimeDetector on 300 live 1 h candles per
#                   coin; slow (one request per coin), so it runs in a background thread and is cached (snapshot)
#   selected asset  the dashboard's own candles + RegimeDetector + regime_precomputer (per-candle history)
#   research        terminal_data.get_research_state(), evidence registry, research zones, FX holdout guard

import logging
import threading

import numpy as np
import pandas as pd
from django.conf import settings
from django.core.cache import cache

from bot.engines.regime_detector import RegimeDetector
from bot.research.cross_section_engine import MIN_ASSETS_FOR_CROSS_SECTION
from bot.views import markets_data as md

logger = logging.getLogger(__name__)

REGIMES = ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "HIGH_VOLATILITY")
REGIME_WORDS = {
    "TRENDING_UP": "Trending up",
    "TRENDING_DOWN": "Trending down",
    "RANGING": "Ranging",
    "HIGH_VOLATILITY": "High volatility",
}
FRESH_QUOTE_HOURS = 2            # a coin with no trade in the last 2 h is excluded from the cross-section (and counted)
METALS = {"XAU", "XAG", "XPT", "XPD"}
EXTREME_Z = 2.0                  # display threshold for "relative extreme" (|z| >= 2); not a signal
ADVERSE_MOVE_PCT = -10.0         # display threshold for "large 24 h fall"
CORR_HIGH = 0.7                  # display threshold for "highly correlated pair"
MIN_CORR_OBS = 100               # fewer synchronised hourly returns than this → correlation not reported
HISTORY_BARS = 168               # selected-asset regime history: last 7 days of 1 h candles

SNAPSHOT_KEY = "home_universe_snapshot_v3"   # claude code changed: 2026-10-27 — v2 added per-coin risk & cost metrics, v3 per-forex-pair metrics
SNAPSHOT_LOCK_KEY = "home_universe_snapshot_lock_v1"
SNAPSHOT_REFRESH_SECONDS = 15 * 60
SNAPSHOT_KEEP_SECONDS = 6 * 3600
SNAPSHOT_LOCK_SECONDS = 15 * 60
# the last snapshot is also kept on disk (git-ignored bot/cache/*.pkl), so a server restart — which empties the
# in-memory cache — shows the previous snapshot with its age instead of "computing" for ~2 minutes
SNAPSHOT_FILE = settings.BASE_DIR / "bot" / "cache" / "home_universe_snapshot.pkl"


def regime_in_words(regime: str) -> str:
    return REGIME_WORDS.get(regime or "", (regime or "Unknown").replace("_", " ").capitalize())


def _ts(value):
    """ISO string / Timestamp → UTC pd.Timestamp (a datetime, so the template's |date filter works), or None."""
    if value is None or value == "":
        return None
    t = pd.Timestamp(value)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _is_default_result(result) -> bool:
    """RegimeDetector._default_result() (too few candles, failed detection) returns RANGING/LOW with "reason:" in the
    summary — it is "insufficient data", not a ranging market, and is shown as such."""
    return "reason:" in (getattr(result, "summary", "") or "")


# ═══════════════════════════════════════════════════════════════════════════
# Cross-section (pure; known-answer tested)
# ═══════════════════════════════════════════════════════════════════════════
def cross_section(returns: dict) -> dict:
    """Breadth, dispersion, z-scores and ranks of one cross-section {symbol: return in %}.

    Same z definition as bot/research/cross_section_engine.py: z_i = (r_i - mean(r)) / std(r), sample std (ddof=1),
    zero variance → unavailable, at least MIN_ASSETS_FOR_CROSS_SECTION assets. Raw returns (the research engine's causal
    winsorisation needs each coin's history and is not applied to a single snapshot). Rank 1 = highest return; ties
    broken alphabetically by symbol, so the order is deterministic."""
    clean = {s: float(r) for s, r in returns.items() if r is not None and np.isfinite(float(r))}
    n = len(clean)
    if n < MIN_ASSETS_FOR_CROSS_SECTION:
        return {"available": False, "n": n,
                "reason": f"{n} valid observation(s); at least {MIN_ASSETS_FOR_CROSS_SECTION} are needed"}
    r = pd.Series(clean)
    mean, std = float(r.mean()), float(r.std(ddof=1))
    if not np.isfinite(std) or std == 0:
        return {"available": False, "n": n, "reason": "every return is identical (zero variance); z-scores undefined"}
    order = sorted(clean, key=lambda s: (-clean[s], s))
    n_up, n_down = int((r > 0).sum()), int((r < 0).sum())
    return {
        "available": True,
        "n": n,
        "mean_pct": mean,
        "std_pct": std,
        "median_pct": float(r.median()),
        "iqr_pct": float(r.quantile(0.75) - r.quantile(0.25)),
        "n_up": n_up,
        "n_down": n_down,
        "n_flat": n - n_up - n_down,
        "breadth_pct": n_up / n * 100,
        "rows": [{"symbol": s, "return_pct": clean[s], "z": (clean[s] - mean) / std, "rank": i + 1}
                 for i, s in enumerate(order)],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Crypto and forex pulse (page load; live-quote cache)
# ═══════════════════════════════════════════════════════════════════════════
def crypto_pulse(live: dict, listing: dict, now: pd.Timestamp) -> dict:
    """Cross-section of the platform's crypto universe (every coin on the Markets page) on Binance's rolling 24 h
    change. Coins without a quote, or with no trade in FRESH_QUOTE_HOURS, are excluded and counted."""
    universe = [q for q in listing.get("crypto", []) if q.get("available")]
    names = {q["symbol"]: q for q in universe}
    live_c = live.get("crypto", {})
    fresh, stale, missing = {}, [], []
    for q in universe:
        v = live_c.get(q["symbol"])
        if not v or v.get("change_pct") is None:
            missing.append(q["symbol"])
        elif (now - pd.Timestamp(v["as_of"])).total_seconds() > FRESH_QUOTE_HOURS * 3600:
            stale.append(q["symbol"])
        else:
            fresh[q["symbol"]] = v["change_pct"]
    out = cross_section(fresh)
    out.update({
        "n_universe": len(universe),
        "n_stale": len(stale),
        "stale_symbols": [s.replace("_USDT", "") for s in stale],
        "n_missing": len(missing),
        "status": live.get("status", {}).get("crypto", ""),
        "fetched_at": _ts(live.get("fetched_at")),
    })
    if not out["available"] and not fresh:
        out["reason"] = live.get("status", {}).get("crypto") or out["reason"]
    for row in out.get("rows", []):
        q, v = names.get(row["symbol"], {}), live_c.get(row["symbol"], {})
        row.update({"display": q.get("display_symbol", row["symbol"].replace("_", "/")), "name": q.get("display_name", ""),
                    "quote_volume": v.get("quote_volume"), "as_of": _ts(v.get("as_of"))})
    if out["available"]:
        rows = out["rows"]
        out["top"], out["bottom"] = rows[0], rows[-1]
        out["n_extreme_pos"] = sum(1 for x in rows if x["z"] >= EXTREME_Z)
        out["n_extreme_neg"] = sum(1 for x in rows if x["z"] <= -EXTREME_Z)
        out["n_adverse"] = sum(1 for x in rows if x["return_pct"] <= ADVERSE_MOVE_PCT)
        out["mood"] = "Broad advance" if out["breadth_pct"] >= 60 else "Broad decline" if out["breadth_pct"] <= 40 else "Mixed"
    return out


def currency_vs_usd(pair_returns: dict) -> dict:
    """{currency: % change vs USD} from {pair: % change}. Each currency appears once (only its USD pair is used), so no
    currency is double-counted the way an average over every pair it trades in would be. XXX/USD → its change;
    USD/XXX → 1/(1 + r) - 1 (the exact inverse). Crosses without USD and metals are not used."""
    from bot.templatetags.coin_extras import split_pair
    out = {}
    for sym, pct in pair_returns.items():
        pair = split_pair(sym)
        if pct is None or not pair or METALS & set(pair):
            continue
        base, quote = pair
        r = float(pct) / 100
        if quote == "USD" and base != "USD":
            out[base] = r * 100
        elif base == "USD" and quote != "USD" and r > -1:
            out[quote] = (1 / (1 + r) - 1) * 100
    return out


def forex_pulse(live: dict, listing: dict, now: pd.Timestamp) -> dict:
    from bot.templatetags.coin_extras import CURRENCIES
    session = live.get("forex_session") or {}
    listed = [q for q in listing.get("forex", []) if q.get("available")]
    live_fx = live.get("forex", {})
    pair_returns = {s: v.get("change_pct") for s, v in live_fx.items() if v.get("change_pct") is not None}
    vs_usd = currency_vs_usd(pair_returns)
    out = cross_section(vs_usd)
    out.update({
        "open": bool(session.get("is_open")),
        "session_reason": session.get("reason", ""),
        "n_pairs_listed": len(listed),
        "n_pairs_live": len(live_fx),
        "n_pairs_not_live": len(listed) - len(live_fx),
        "n_currencies": len(vs_usd),
        "status": live.get("status", {}).get("forex", ""),
        "as_of": max((_ts(v["as_of"]) for v in live_fx.values() if v.get("as_of")), default=None),
    })
    if not out["available"] and not live_fx:
        out["reason"] = live.get("status", {}).get("forex") or out["reason"]
    for row in out.get("rows", []):
        row["name"] = CURRENCIES.get(row["symbol"], (None, row["symbol"]))[1]
    if out["available"]:
        out["strongest"], out["weakest"] = out["rows"][0], out["rows"][-1]
    if pair_returns:
        moves = sorted(((s, float(c)) for s, c in pair_returns.items()), key=lambda m: (-m[1], m[0]))
        out["top_pair"] = {"symbol": moves[0][0], "display": moves[0][0].replace("_", "/"), "return_pct": moves[0][1]}
        out["bottom_pair"] = {"symbol": moves[-1][0], "display": moves[-1][0].replace("_", "/"), "return_pct": moves[-1][1]}
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Universe snapshot: regime distribution, volatility, correlation (background)
# ═══════════════════════════════════════════════════════════════════════════
def compute_universe_snapshot() -> dict:
    """RegimeDetector on every coin (the /market/ page's own loop) plus volatility and correlation from the same
    candles (served from data_fetcher's 5-minute disk cache the loop has just filled). Slow: run in the background."""
    from bot.data_fetcher import get_klines
    from bot.views.market_intelligence_data import CANDLES_PER_SYMBOL, _to_klines_symbol, get_market_regime_overview
    started = pd.Timestamp.now(tz="UTC")
    ov = get_market_regime_overview()
    if not ov.get("available"):
        return {"available": False, "computed_at": started.isoformat(), "reason": ov.get("reason", "no coin classified")}
    detector = RegimeDetector()
    counts = {k: 0 for k in REGIMES}
    regimes, atr, insufficient = {}, [], 0
    for row in ov["rows"]:
        if "reason:" in (row.get("summary") or ""):
            insufficient += 1
            regimes[row["symbol"]] = "INSUFFICIENT_DATA"
            continue
        counts[row["regime"]] = counts.get(row["regime"], 0) + 1
        regimes[row["symbol"]] = row["regime"]
        atr.append(row["atr_ratio"])
    n_classified = sum(counts.values())
    atr_s = pd.Series(atr, dtype=float)

    from bot.market_intel.asset_metrics import CANDLES_NEEDED, asset_metrics   # claude code changed: 2026-10-27
    closes, metrics = {}, {}
    for sym, reg in regimes.items():
        if reg == "INSUFFICIENT_DATA":
            continue
        try:
            # claude code changed: 2026-10-27 — one 31-day fetch; the correlation still uses the same last CANDLES_PER_SYMBOL
            df = get_klines(_to_klines_symbol(sym), interval="1h", total_candles=CANDLES_NEEDED)
            if df is not None and not df.empty:
                closes[sym] = pd.to_numeric(df["close"], errors="coerce").tail(CANDLES_PER_SYMBOL)
                metrics[sym] = asset_metrics(df.apply(pd.to_numeric, errors="coerce"), "crypto", "qav")
        except Exception:
            continue
    correlation = correlation_summary(closes)
    latest_candle = max((s.index.max() for s in closes.values() if len(s)), default=None)

    return {
        "available": True,
        "computed_at": started.isoformat(),
        "candles_per_symbol": ov.get("candles_per_symbol"),
        "latest_candle": latest_candle.isoformat() if latest_candle is not None else None,
        "n_universe": ov["n_symbols"] + ov["n_errors"],
        "n_classified": n_classified,
        "n_insufficient": insufficient,
        "n_unknown": ov["n_errors"],
        "counts": counts,
        "regimes": regimes,
        "volatility": {
            "available": len(atr_s) > 0,
            "median": float(atr_s.median()) if len(atr_s) else None,
            "q25": float(atr_s.quantile(0.25)) if len(atr_s) else None,
            "q75": float(atr_s.quantile(0.75)) if len(atr_s) else None,
            "n_above_own_average": int((atr_s > 1).sum()),
            "n_extreme": int((atr_s >= detector.atr_extreme_multiplier).sum()),
            "extreme_threshold": detector.atr_extreme_multiplier,
            "n": int(len(atr_s)),
        },
        "correlation": correlation,
        "metrics": metrics,   # claude code changed: 2026-10-27 — {symbol: asset_metrics(...)}
        "fx_metrics": forex_metrics(q["symbol"] for q in md.get_markets_list().get("forex", []) if q.get("available")),   # claude code changed: 2026-10-27
        "fetch_aborted": ov.get("fetch_aborted", False),
    }


def correlation_summary(closes: dict) -> dict:
    """Mean pairwise Pearson correlation of hourly log returns on synchronised timestamps. Coins covering < 90% of the
    hours are dropped first, then any hour a remaining coin is missing. Not reported below MIN_CORR_OBS returns."""
    if len(closes) < 3:
        return {"available": False, "reason": f"{len(closes)} coin(s) with candles; at least 3 are needed"}
    # the window is anchored on the most common latest candle: a delisted coin's weeks-old candles must not stretch it
    ends = pd.Series([s.index.max() for s in closes.values() if len(s)])
    end = ends.mode().max()
    length = int(pd.Series([len(s) for s in closes.values()]).median())
    px = pd.DataFrame(closes).sort_index()
    px = px.loc[end - pd.Timedelta(hours=length - 1):end]
    px = px.loc[:, px.notna().mean() >= 0.9].dropna()
    px = px[(px > 0).all(axis=1)]
    rets = np.log(px).diff().dropna()
    if rets.shape[1] < 3 or len(rets) < MIN_CORR_OBS:
        return {"available": False,
                "reason": f"{len(rets)} synchronised hourly returns across {rets.shape[1]} coins; at least {MIN_CORR_OBS} "
                          f"returns and 3 coins are needed"}
    c = rets.corr().values
    vals = c[np.triu_indices(c.shape[0], 1)]
    vals = vals[np.isfinite(vals)]
    return {
        "available": len(vals) > 0,
        "mean": float(vals.mean()) if len(vals) else None,
        "median": float(np.median(vals)) if len(vals) else None,
        "share_high": float((vals > CORR_HIGH).mean()) if len(vals) else None,
        "high_threshold": CORR_HIGH,
        "n_coins": int(rets.shape[1]),
        "n_pairs": int(len(vals)),
        "n_obs": int(len(rets)),
        "window_start": rets.index[0].isoformat(),
        "window_end": rets.index[-1].isoformat(),
    }


def _save_snapshot_file(snap: dict) -> None:
    import pickle
    try:
        SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SNAPSHOT_FILE.with_suffix(".tmp")
        tmp.write_bytes(pickle.dumps(snap, protocol=pickle.HIGHEST_PROTOCOL))
        tmp.replace(SNAPSHOT_FILE)
    except OSError:
        logger.warning("home_intelligence: could not write %s", SNAPSHOT_FILE)


def _load_snapshot_file(now: pd.Timestamp):
    """The saved snapshot if it is readable and younger than SNAPSHOT_KEEP_SECONDS, else None."""
    import pickle
    try:
        snap = pickle.loads(SNAPSHOT_FILE.read_bytes())
        age = (now - pd.Timestamp(snap["computed_at"])).total_seconds()
        return snap if 0 <= age <= SNAPSHOT_KEEP_SECONDS else None
    except Exception:
        return None


def _refresh_snapshot():
    try:
        snap = compute_universe_snapshot()
        cache.set(SNAPSHOT_KEY, snap, SNAPSHOT_KEEP_SECONDS)
        if snap.get("available"):
            _save_snapshot_file(snap)
    except Exception:
        logger.exception("home_intelligence: universe snapshot failed")
        cache.set(SNAPSHOT_KEY, {"available": False, "computed_at": pd.Timestamp.now(tz="UTC").isoformat(),
                                 "reason": "the last refresh failed (see server log)"}, SNAPSHOT_KEEP_SECONDS)
    finally:
        cache.delete(SNAPSHOT_LOCK_KEY)


def get_universe_snapshot(now: pd.Timestamp = None, start_refresh: bool = True) -> dict:
    """The cached snapshot, with its age. When it is missing or older than SNAPSHOT_REFRESH_SECONDS a refresh starts in
    a background thread (one at a time: cache.add is the lock); the page never waits for it."""
    now = now or pd.Timestamp.now(tz="UTC")
    snap = cache.get(SNAPSHOT_KEY)
    if snap is None:
        snap = _load_snapshot_file(now)
        if snap is not None:
            cache.set(SNAPSHOT_KEY, snap, SNAPSHOT_KEEP_SECONDS)
    age = (now - pd.Timestamp(snap["computed_at"])).total_seconds() if snap else None
    due = snap is None or age > SNAPSHOT_REFRESH_SECONDS or (snap.get("available") and "fx_metrics" not in snap)   # claude code changed: 2026-10-27 — a pre-metrics snapshot refreshes at once
    refreshing = bool(cache.get(SNAPSHOT_LOCK_KEY))
    if due and start_refresh and getattr(settings, "HOME_UNIVERSE_SNAPSHOT_BACKGROUND", True):
        if cache.add(SNAPSHOT_LOCK_KEY, True, SNAPSHOT_LOCK_SECONDS):
            threading.Thread(target=_refresh_snapshot, name="home-universe-snapshot", daemon=True).start()
        refreshing = True
    if snap is None:
        return {"available": False, "refreshing": refreshing,
                "reason": "being computed in the background (about 1–3 minutes after the first visit); reload shortly"
                          if refreshing else "not computed yet"}
    snap = dict(snap)
    snap["computed_at"], snap["latest_candle"] = _ts(snap.get("computed_at")), _ts(snap.get("latest_candle"))
    if (snap.get("correlation") or {}).get("available"):
        snap["correlation"] = dict(snap["correlation"], window_start=_ts(snap["correlation"]["window_start"]),
                                   window_end=_ts(snap["correlation"]["window_end"]))
    snap["age_minutes"] = int(age // 60)
    snap["refreshing"] = refreshing
    if snap.get("available"):
        c = snap["counts"]
        n = snap["n_classified"] + snap["n_insufficient"] + snap["n_unknown"]
        snap["distribution"] = [
            {"key": k.lower().replace("_", "-"), "label": label, "n": v, "pct": v / n * 100 if n else 0}
            for k, label, v in [(r, REGIME_WORDS[r], c.get(r, 0)) for r in REGIMES]
            + [("UNKNOWN", "Insufficient data / not fetched", snap["n_insufficient"] + snap["n_unknown"])]
        ]
    return snap


# ═══════════════════════════════════════════════════════════════════════════
# Regime intelligence for the selected asset
# ═══════════════════════════════════════════════════════════════════════════
def regime_intelligence(df: pd.DataFrame, result, detector: RegimeDetector = None,
                        now: pd.Timestamp = None) -> dict:
    """Explains `result` (RegimeDetector.detect(df), already computed by the view) input by input, using the detector's
    own thresholds and rules, and adds the per-candle history from regime_precomputer (identical to calling detect()
    on every prefix of df). Changes nothing in the detector."""
    detector = detector or RegimeDetector()
    now = now or pd.Timestamp.now(tz="UTC")
    if df is None or df.empty or _is_default_result(result):
        return {"available": False, "regime": result.regime,
                "reason": (result.summary.split("reason:")[-1].strip() if _is_default_result(result) else "no candles")
                .replace("_", " ")}
    d = df.copy()
    for col in ("open", "high", "low", "close"):
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d.dropna(subset=["high", "low", "close"])
    direction = detector._get_ema_direction(d)

    ema_trending = result.ema_spread_pct >= detector.ema_spread_threshold
    bb_ranging = result.bb_width_pct <= detector.bb_narrow_threshold
    trend_votes = int(result.adx_trending) + int(ema_trending) + int(not bb_ranging)
    inputs = [
        {"name": "Trend strength", "measure": "ADX (14)", "value": f"{result.adx:.1f}",
         "rule": f"≥ {detector.adx_trend_threshold:g} counts as a trend", "votes": "trend" if result.adx_trending else "range"},
        {"name": "Trend structure", "measure": f"EMA {detector.ema_fast}/{detector.ema_slow} gap, % of price",
         "value": f"{result.ema_spread_pct:.2f}%", "rule": f"≥ {detector.ema_spread_threshold:g}% counts as a trend",
         "votes": "trend" if ema_trending else "range"},
        {"name": "Compression", "measure": f"Bollinger width ({detector.bb_period}, {detector.bb_std:g}σ), % of price",
         "value": f"{result.bb_width_pct:.2f}%", "rule": f"≤ {detector.bb_narrow_threshold:g}% counts as ranging",
         "votes": "range" if bb_ranging else "trend"},
    ]
    if result.volatility_extreme:
        rule_applied = (f"ATR ratio {result.atr_ratio:.2f} ≥ {detector.atr_extreme_multiplier:g}: the volatility override "
                        f"applies before any vote")
        expected = "HIGH_VOLATILITY"
    elif trend_votes >= 2:
        rule_applied = f"{trend_votes} of 3 inputs point to a trend; EMA {detector.ema_fast} vs EMA {detector.ema_slow} gives the direction ({direction})"
        expected = {"UP": "TRENDING_UP", "DOWN": "TRENDING_DOWN"}.get(direction, "RANGING")
    else:
        rule_applied = f"{3 - trend_votes} of 3 inputs point to a range"
        expected = "RANGING"

    history = _regime_history(d, detector, result)
    last_ts = pd.Timestamp(d.index[-1])
    last_ts = last_ts.tz_localize("UTC") if last_ts.tzinfo is None else last_ts
    return {
        "available": True,
        "regime": result.regime,
        "regime_words": regime_in_words(result.regime),
        "regime_key": result.regime.lower().replace("_", "-"),
        "agreement": result.confidence,
        "agreement_meaning": {"HIGH": "all 3 inputs agree" if not result.volatility_extreme else "volatility override",
                              "MEDIUM": "2 of 3 inputs agree", "LOW": "inputs disagree"}.get(result.confidence, ""),
        "trend_votes": trend_votes,
        "direction": direction,
        "inputs": inputs,
        "atr_ratio": result.atr_ratio,
        "atr_threshold": detector.atr_extreme_multiplier,
        "volatility_extreme": result.volatility_extreme,
        "volatility_words": ("Above the high-volatility threshold" if result.volatility_extreme
                             else "Above its own 50-hour average" if result.atr_ratio > 1
                             else "At or below its own 50-hour average"),
        "rule_applied": rule_applied,
        "consistent": expected == result.regime,
        "last_candle": last_ts,
        "candle_forming": last_ts + pd.Timedelta(hours=1) > now,
        "candle_age_minutes": int((now - last_ts).total_seconds() // 60),
        "n_candles": len(d),
        "history": history,
    }


def _regime_history(d: pd.DataFrame, detector: RegimeDetector, result) -> dict:
    from bot.backtesting.regime_precomputer import precompute_regime_results
    try:
        results = precompute_regime_results(d, detector)
    except Exception as exc:
        return {"available": False, "reason": f"history unavailable: {type(exc).__name__}"}
    tail = list(zip(d.index[-HISTORY_BARS:], (r.regime for r in results[-HISTORY_BARS:])))
    if not tail:
        return {"available": False, "reason": "no candles"}
    segments = []
    for ts, reg in tail:
        if segments and segments[-1]["regime"] == reg:
            segments[-1]["bars"] += 1
            segments[-1]["end"] = ts
        else:
            segments.append({"regime": reg, "start": ts, "end": ts, "bars": 1})
    for s in segments:
        s["pct"] = s["bars"] / len(tail) * 100
        s["key"] = s["regime"].lower().replace("_", "-")
        s["words"] = regime_in_words(s["regime"])
    transitions = [{"at": segments[i]["start"], "from": regime_in_words(segments[i - 1]["regime"]),
                    "to": regime_in_words(segments[i]["regime"])} for i in range(len(segments) - 1, 0, -1)][:6]
    return {
        "available": True,
        "bars": len(tail),
        "segments": segments,
        "transitions": transitions,
        "n_transitions": len(segments) - 1,
        "current_run_bars": segments[-1]["bars"],
        "current_run_capped": len(segments) == 1,
        "matches_current": results[-1].regime == result.regime,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Strategy evidence and research integrity
# ═══════════════════════════════════════════════════════════════════════════
def strategy_evidence(results: dict, symbol: str) -> dict:
    """The selected symbol's in-sample backtest, stated for what it is. 0 trades is "no trades", not a measured zero;
    under the research-lab minimum (30) it is "insufficient"; only above it are expectancy etc. shown."""
    from bot.research.validated_feature_registry import STRATEGY_VERDICTS
    from bot.research_lab.verdict import MIN_BACKTEST_TRADES
    verdicts = [{"name": v.strategy_name, "verdict": v.research_verdict, "eligible": v.production_eligible}
                for v in STRATEGY_VERDICTS.values()]
    n = int((results or {}).get("total_trades") or 0)
    if n == 0:
        state = "NO_TRADES_GATED" if not any(v["eligible"] for v in verdicts) else "NO_TRADES"
    elif n < MIN_BACKTEST_TRADES:
        state = "INSUFFICIENT"
    else:
        state = "MEASURED"
    out = {"symbol": symbol, "n_trades": n, "state": state, "min_trades": MIN_BACKTEST_TRADES, "strategies": verdicts,
           "classification": "In-sample · descriptive · not claim-eligible"}
    if state == "MEASURED":
        out.update({k: (results or {}).get(src) for k, src in [
            ("expectancy_r", "avg_r_multiple"), ("expectancy_usd", "expectancy"), ("profit_factor", "profit_factor"),
            ("win_rate", "win_rate"), ("max_drawdown_pct", "max_drawdown")]})
    return out


def research_integrity(user=None) -> dict:
    """Evidence-registry classes, the research reset point, active sealed windows (from the code constants that enforce
    them) and — for a signed-in user — their own Research Lab experiments."""
    out = {}
    try:
        from bot.research.evidence_registry import load_registry
        reg = load_registry()
        classes = {}
        for a in reg.get("artifacts", []):
            classes[a.get("class", "UNVERIFIED")] = classes.get(a.get("class", "UNVERIFIED"), 0) + 1
        order = ["CANONICAL", "LEGACY_DESCRIPTIVE", "UNVERIFIED", "SUPERSEDED", "INVALIDATED"]
        out["registry"] = {"available": True, "reset_point": reg.get("reset_point", ""),
                           "classes": [{"name": k.replace("_", " ").title(), "key": k, "n": classes.get(k, 0)}
                                       for k in order + sorted(set(classes) - set(order))],
                           "n": sum(classes.values())}
    except Exception as exc:
        out["registry"] = {"available": False, "reason": f"evidence registry unreadable: {type(exc).__name__}"}

    sealed = []
    try:
        from bot.quant_core.research_zones import ResearchZones
        z = ResearchZones()
        sealed.append({"name": "Crypto forward holdout (D-8)", "since": z.F, "evaluation": z.E, "note": f"evaluated once; extended while < {z.min_holdout_trades} trades"})
    except Exception:
        pass
    try:
        from bot.research.fx_holdout_guard import QC2_HOLDOUT_START, V7_HOLDOUT_START
        sealed.append({"name": "FX v7 holdout (EUR/USD, USD/CHF)", "since": V7_HOLDOUT_START, "evaluation": None,
                       "note": "see its pre-registration for the opening rule"})
        sealed.append({"name": "QC-2 forward holdout (EUR/CAD)", "since": QC2_HOLDOUT_START, "evaluation": None,
                       "note": "see QC2_PRE_REGISTRATION_2026-10-19.md"})
    except Exception:
        pass
    out["sealed"] = sealed

    out["experiments"] = None
    if user is not None and getattr(user, "is_authenticated", False):
        from django.db.models import Count
        from bot.research_lab.models import ResearchExperiment
        qs = ResearchExperiment.objects.filter(student=user)
        by_status = dict(qs.values_list("status").annotate(n=Count("pk")))
        latest = qs.order_by("-created_at").first()
        out["experiments"] = {"n": sum(by_status.values()), "by_status": sorted(by_status.items()),
                              "latest": latest}
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Research watchlist
# ═══════════════════════════════════════════════════════════════════════════
def research_watchlist(crypto: dict, snapshot: dict, limit: int = 8) -> list:
    """Coins at |z| >= EXTREME_Z in today's cross-section, largest |z| first, each with its own separate dimensions
    (return, z, rank, regime from the snapshot, 24 h volume, data status). No combined score; not a signal."""
    if not crypto.get("available"):
        return []
    rows = [r for r in crypto["rows"] if r["extreme"]]
    rows.sort(key=lambda r: (-abs(r["z"]), r["symbol"]))
    return [dict(r, compact=r["symbol"].replace("_", "")) for r in rows[:limit]]


def annotate_regimes(crypto: dict, snapshot: dict) -> None:
    """Adds each crypto row's regime from the universe snapshot (its words match the distribution's labels, which the
    relative-value regime filter relies on) and the |z| >= EXTREME_Z flag."""
    regimes = (snapshot or {}).get("regimes", {}) if (snapshot or {}).get("available") else {}
    for r in crypto.get("rows", []):
        reg = regimes.get(r["symbol"])
        r["regime"] = reg
        r["regime_words"] = (regime_in_words(reg) if reg and reg != "INSUFFICIENT_DATA"
                             else "Insufficient data / not fetched" if reg else "Not in snapshot")
        r["extreme"] = abs(r["z"]) >= EXTREME_Z


def quote_metrics(symbol: str, asset_class: str, snapshot: dict, live: dict):   # claude code changed: 2026-10-27 — new
    """Risk & cost metrics of one market (from the background snapshot's candles) plus its live spread, or None while
    the snapshot has not computed it. Shared by the home page and the Markets page."""
    from bot.market_intel.asset_metrics import spread
    snap = snapshot or {}
    if not snap.get("available"):
        return None
    m = (snap.get("fx_metrics") if asset_class == "forex" else snap.get("metrics") or {}) or {}
    m = m.get(symbol)
    if not m:
        return None
    q = (live or {}).get(asset_class, {}).get(symbol, {})
    return dict(m, **spread(q.get("bid"), q.get("ask"), m["vol_7d_pct"], m["bars_per_year"]))


def annotate_metrics(crypto: dict, snapshot: dict, live: dict) -> None:   # claude code changed: 2026-10-27 — new
    """Adds each crypto row's risk & cost metrics (from the snapshot's candles) and its live spread."""
    for r in crypto.get("rows", []):
        r["metrics"] = quote_metrics(r["symbol"], "crypto", snapshot, live)


def forex_metrics(pairs) -> dict:   # claude code changed: 2026-10-27 — new; computed in the background snapshot
    """{pair: asset_metrics(...)} from each pair's hourly CSV (refreshed hourly from MT5)."""
    from bot.market_intel.asset_metrics import CANDLES_NEEDED, asset_metrics
    from bot.mt5_data_fetcher import symbol_to_filename
    out = {}
    for sym in sorted(pairs):
        try:
            path = settings.BASE_DIR / "data" / "forex" / symbol_to_filename(sym.replace("_", "/"))
            d = pd.read_csv(path).tail(CANDLES_NEEDED * 2)
            d.index = pd.to_datetime(d.pop("timestamp"), utc=True)
            # no RVOL: the stored tick volume changes scale ~3x in the week of 2026-10-12 for every pair at once (the
            # candles come from more than one MT5 feed), so a 24 h vs 29-day volume ratio would compare two brokers
            out[sym] = dict(asset_metrics(d, "forex", None), pair=sym.replace("_", "/"),
                            rvol_note="n/a: the stored MT5 tick volume mixes more than one broker feed (scale changes "
                                      "~3x from the week of 12 Oct), so recent vs past volume is not comparable")
        except Exception as exc:
            logger.warning("home_intelligence: forex metrics for %s failed: %s", sym, exc)
    return out


def forex_pairs_section(live: dict, snapshot: dict) -> dict:   # claude code changed: 2026-10-27 — new (owner: show real pairs)
    """Cross-section of the live forex PAIRS on their 24 h change (rank 1 = largest rise), each with its own risk &
    cost metrics. Pairs share currencies (EUR is in many of them), so they are not independent observations."""
    live_fx = live.get("forex", {})
    returns = {s: v.get("change_pct") for s, v in live_fx.items() if v.get("change_pct") is not None}
    out = cross_section(returns)
    for r in out.get("rows", []):
        r["display"] = r["symbol"].replace("_", "/")
        r["metrics"] = quote_metrics(r["symbol"], "forex", snapshot, live)
    return out


def get_home_intelligence(df, regime_result, backtest_results, symbol, user=None) -> dict:
    """Everything the home page needs beyond the existing terminal_data sections. Never raises for a missing source."""
    now = pd.Timestamp.now(tz="UTC")
    try:
        live = md.get_live_quotes()
        listing = md.get_markets_list()
    except Exception as exc:   # an unreachable source must not break the home page
        live, listing = {"status": {"crypto": f"UNAVAILABLE: {type(exc).__name__}",
                                    "forex": f"UNAVAILABLE: {type(exc).__name__}"}}, {}
    crypto = crypto_pulse(live, listing, now)
    forex = forex_pulse(live, listing, now)
    snapshot = get_universe_snapshot(now)
    annotate_regimes(crypto, snapshot)
    annotate_metrics(crypto, snapshot, live)    # claude code changed: 2026-10-27
    forex["pairs"] = forex_pairs_section(live, snapshot)   # claude code changed: 2026-10-27 — real pairs, like the Markets page
    return {
        "now": now,
        "crypto": crypto,
        "forex": forex,
        "snapshot": snapshot,
        "regime": regime_intelligence(df, regime_result, now=now),
        "evidence": strategy_evidence(backtest_results, symbol),
        "integrity": research_integrity(user),
        "watchlist": research_watchlist(crypto, snapshot),
        "extreme_z": EXTREME_Z,
        "adverse_move_pct": ADVERSE_MOVE_PCT,
        "min_assets": MIN_ASSETS_FOR_CROSS_SECTION,
        "atr_extreme": RegimeDetector().atr_extreme_multiplier,
    }
