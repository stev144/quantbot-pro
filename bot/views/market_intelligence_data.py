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

from django.conf import settings

from bot.data_fetcher import get_klines
from bot.engines.regime_detector import RegimeDetector
from bot.views.terminal_data import get_cross_sectional_dispersion

DATA_DIR = os.path.join(settings.BASE_DIR, "data")

# Enough candles to clear RegimeDetector.min_candles=60 with headroom,
# short enough to keep a ~20-symbol loop's total fetch time reasonable
# for a synchronous page render (live_regime_monitor uses 1440 candles,
# but that view only ever looks at one symbol per request).
CANDLES_PER_SYMBOL = 300


def discover_symbol_universe():
    """Real: every data/*_1h.csv (bot/fetch_all_symbols.py's output), e.g. 'AAVE_USDT'."""
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*_1h.csv")))
    return [os.path.basename(p).replace("_1h.csv", "") for p in files]


def _to_klines_symbol(underscore_symbol):
    # "AAVE_USDT" -> "AAVEUSDT" — bot.data_fetcher.get_klines expects the
    # exchange-style ticker without the underscore (matches how
    # bot/views/dashboard.py already calls it).
    return underscore_symbol.replace("_", "")


def get_market_regime_overview(limit=None):
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

    detector = RegimeDetector()
    rows = []
    errors = {}
    for sym in symbols:
        try:
            df = get_klines(_to_klines_symbol(sym), interval="1h", total_candles=CANDLES_PER_SYMBOL)
            if df is None or df.empty:
                errors[sym] = "no data returned"
                continue
            result = detector.detect(df)
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
    }


def get_market_intelligence():
    return {
        "regime_overview": get_market_regime_overview(),
        "cross_section": get_cross_sectional_dispersion(),
    }
