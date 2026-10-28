# ============================================================
# bot/views/market_intelligence_data.py
# claude code changed: new file — Market Intelligence data layer. Same
# rule as terminal_data.py/research_lab_data.py/feature_intelligence_data.py:
# real data or an explicit {"available": False, "reason": ...} marker.
#
# bot/views/dashboard.py::live_regime_monitor() already does real,
# correct regime detection (bot/engines/regime_detector.py's
# RegimeDetector, the same class the live bot and backtester use) but
# only for one symbol per request. This module reuses the identical
# detect()-on-recent-candles logic, looped across the real symbol
# universe (data/*.csv) for one page render, rather than duplicating a
# second detector implementation.
# ============================================================

import os
import glob
import logging   # claude code changed: 2026-10-28

logger = logging.getLogger(__name__)   # claude code changed: 2026-10-28

from django.conf import settings

from bot.data_fetcher import binance_reachable, get_klines   # claude code changed: 2026-10-07 — fail fast when offline
from bot.engines.regime_detector import RegimeDetector
from bot.views.terminal_data import get_cross_sectional_dispersion

DATA_DIR = os.path.join(settings.BASE_DIR, "data")

# Enough candles to clear RegimeDetector.min_candles=60 with headroom,
# short enough to keep a ~20-symbol loop's total fetch time reasonable
# for a synchronous page render (live_regime_monitor uses 1440 candles,
# but that view only ever looks at one symbol per request).
CANDLES_PER_SYMBOL = 300
# claude code changed: 2026-10-28 — institutional metrics (bot/quant_metrics): 1,000 hourly candles (one Binance
# request; the variance-ratio test needs T ≥ 10·q = 240 and benefits from more) and 400 daily candles (365-day own-
# history volatility percentile + 20-day window). RegimeDetector still sees exactly the last 300 hourly candles.
QM_HOURLY_CANDLES = 1000
QM_DAILY_CANDLES = 400
# claude code changed: 2026-10-07 — the universe is ~155 symbols now (not ~20); offline, every symbol cost ~19 s
# of retries (~50 min for one page). Stop live fetching after this many consecutive failures.
MAX_CONSECUTIVE_FETCH_FAILURES = 3


def discover_symbol_universe():
    """Real: every data/*_1h.csv (bot/fetch_all_symbols.py's output), e.g. 'AAVE_USDT'."""
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*_1h.csv")))
    return [os.path.basename(p).replace("_1h.csv", "") for p in files]


def _to_klines_symbol(underscore_symbol):
    # "AAVE_USDT" -> "AAVEUSDT" — bot.data_fetcher.get_klines expects the
    # exchange-style ticker without the underscore (matches how
    # bot/views/dashboard.py already calls it).
    return underscore_symbol.replace("_", "")


def _quant_metrics(sym, hourly):   # claude code changed: 2026-10-28 — new
    """Institutional metrics for one crypto symbol; None on any failure (logged), never a partial guess."""
    from bot.quant_metrics.api import symbol_metrics
    try:
        daily = get_klines(_to_klines_symbol(sym), interval="1d", total_candles=QM_DAILY_CANDLES)
        return symbol_metrics(sym, hourly, daily if daily is not None and not daily.empty else None, "crypto")
    except Exception as e:
        logger.warning("quant metrics failed for %s: %s", sym, e)
        return None


def get_market_regime_overview(limit=None, with_metrics=False):   # claude code changed: 2026-10-28 — with_metrics: /market/ only
    """
    Real: current RegimeDetector classification for every symbol in the
    universe, computed fresh (not cached) on the most recent
    CANDLES_PER_SYMBOL candles — same class, same method the live bot,
    backtester, and live_regime_monitor all already use.
    """
    symbols = discover_symbol_universe()
    if not symbols:
        return {"available": False, "reason": "No data/*_1h.csv found — run bot/fetch_all_symbols.py first"}
    if limit:
        symbols = symbols[:limit]

    if not binance_reachable():   # claude code changed: 2026-10-07 — fail fast when offline: one ~3 s probe instead of ~19 s per symbol
        return {"available": False, "candles_per_symbol": CANDLES_PER_SYMBOL, "n_errors": 0, "errors": {},
                "reason": "Live market data unreachable — the Binance API did not answer within 3 s. Check the internet "
                          "connection (some networks/carriers block Binance even when other sites work), then reload."}
    detector = RegimeDetector()
    qm = {}   # claude code changed: 2026-10-28 — symbol -> bot.quant_metrics SymbolMetrics
    rows = []
    errors = {}
    consecutive_failures = 0   # claude code changed: 2026-10-07 — fail fast when offline
    aborted_reason = ""   # claude code changed: 2026-10-07 — fail fast when offline
    for k, sym in enumerate(symbols):
        if consecutive_failures >= MAX_CONSECUTIVE_FETCH_FAILURES:   # claude code changed: 2026-10-07 — fail fast when offline: circuit breaker
            aborted_reason = (f"live fetching stopped after {consecutive_failures} consecutive failures "
                              f"({len(symbols) - k} symbols not fetched) — Binance is unreachable from this network right now "
                              f"(connection dropped, or Binance blocked/unrouted by the network or carrier)")
            for rest in symbols[k:]:
                errors[rest] = "not fetched (live fetching stopped)"
            break
        try:
            df = get_klines(_to_klines_symbol(sym), interval="1h",
                            total_candles=QM_HOURLY_CANDLES if with_metrics else CANDLES_PER_SYMBOL)   # claude code changed: 2026-10-28
            if df is None or df.empty:
                errors[sym] = "no data returned"
                consecutive_failures += 1   # claude code changed: 2026-10-07 — fail fast when offline
                continue
            consecutive_failures = 0   # claude code changed: 2026-10-07 — fail fast when offline
            result = detector.detect(df.tail(CANDLES_PER_SYMBOL))   # claude code changed: 2026-10-28 — unchanged regime input (last 300)
            if with_metrics:   # claude code changed: 2026-10-28
                qm[sym] = _quant_metrics(sym, df)
            rows.append({
                "symbol": sym,
                "regime": result.regime,
                "confidence": result.confidence,
                "adx": round(result.adx, 2),
                "atr_ratio": round(result.atr_ratio, 2),
                "ema_spread_pct": round(result.ema_spread_pct, 3),
                "bb_width_pct": round(result.bb_width_pct, 3),
                "volatility_extreme": result.volatility_extreme,
                "summary": result.summary,
            })
        except Exception as e:
            errors[sym] = str(e)[:120]
            consecutive_failures += 1   # claude code changed: 2026-10-07 — fail fast when offline

    # claude code changed: 2026-10-28 — universe ranks (momentum, liquidity) across every symbol computed above
    from bot.quant_metrics.api import rank_universe
    rank_universe({k: v for k, v in qm.items() if v is not None})
    for r in rows:
        m = qm.get(r["symbol"])
        if with_metrics:
            r["qm"] = m.model_dump() if m is not None else None

    regime_counts = {}
    for r in rows:
        regime_counts[r["regime"]] = regime_counts.get(r["regime"], 0) + 1

    return {
        "available": bool(rows),
        "rows": sorted(rows, key=lambda r: r["symbol"]),
        "n_symbols": len(rows),
        "n_errors": len(errors),
        "errors": errors,
        "regime_counts": regime_counts,
        "candles_per_symbol": CANDLES_PER_SYMBOL,
        "fetch_aborted": bool(aborted_reason),   # claude code changed: 2026-10-07 — fail fast when offline
        "aborted_reason": aborted_reason,   # claude code changed: 2026-10-07 — fail fast when offline
        "reason": aborted_reason or "no symbol could be classified",   # claude code changed: 2026-10-07 — fail fast when offline (shown when nothing classified)
    }


REGIME_OVERVIEW_CACHE_KEY = "market_regime_overview_qm_v1"   # claude code changed: 2026-10-28


def get_market_intelligence():
    from django.core.cache import cache   # claude code changed: 2026-10-28 — 2 fetches/symbol now (hourly + daily)
    from bot.data_fetcher import DEFAULT_CACHE_TTL_SECONDS
    overview = cache.get(REGIME_OVERVIEW_CACHE_KEY)
    if overview is None:
        overview = get_market_regime_overview(with_metrics=True)
        if overview.get("available"):   # never cache an offline / empty result
            cache.set(REGIME_OVERVIEW_CACHE_KEY, overview, DEFAULT_CACHE_TTL_SECONDS)   # same freshness as the kline cache
    return {
        "regime_overview": overview,   # claude code changed: 2026-10-28 — was computed on every request
        "cross_section": get_cross_sectional_dispersion(),
    }
