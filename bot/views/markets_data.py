# bot/views/markets_data.py
# claude code changed: new — normalized market quote layer for the Markets
# discovery screen. Reads data exclusively from existing on-disk CSVs
# (data/*_1h.csv for crypto, data/forex/*_1h.csv for forex). No live
# network calls on page render — avoids blocking the server thread.
# Returns a normalized Quote schema (see _normalize_quote) or an explicit
# unavailable sentinel, never fabricated prices.

import os
import glob
import logging
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_ROOT       = Path(settings.BASE_DIR)
_CRYPTO_DIR = _ROOT / "data"
_FOREX_DIR  = _ROOT / "data" / "forex"

_CACHE_KEY     = "markets_list_v1"
_CACHE_TTL     = 300   # 5 minutes

_SPARK_BARS    = 24    # close values used for sparkline

# ── Human-readable display names for the most common crypto symbols ──────────
_CRYPTO_NAMES = {
    "BTC_USDT": "Bitcoin",    "ETH_USDT": "Ethereum",   "BNB_USDT": "BNB",
    "SOL_USDT": "Solana",     "ADA_USDT": "Cardano",    "XRP_USDT": "XRP",
    "DOT_USDT": "Polkadot",   "DOGE_USDT": "Dogecoin",  "AVAX_USDT": "Avalanche",
    "LINK_USDT": "Chainlink", "ATOM_USDT": "Cosmos",    "MATIC_USDT": "Polygon",
    "LTC_USDT": "Litecoin",   "UNI_USDT": "Uniswap",   "NEAR_USDT": "NEAR",
    "FIL_USDT": "Filecoin",   "SHIB_USDT": "Shiba Inu","AAVE_USDT": "Aave",
}


def _format_symbol(raw: str, asset_class: str) -> str:
    """'BTC_USDT' → 'BTC/USDT', 'EUR_USD' → 'EUR/USD'."""
    return raw.replace("_", "/")


def _sparkline_svg(values: list, width: int = 80, height: int = 28) -> str:
    """Returns an inline <svg> polyline sparkline from close values.
    Color is green if last > first, red otherwise. Returns '' for < 2 pts."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        hi = lo + 1e-10
    n = len(values)
    step = width / (n - 1)
    pts = []
    for i, v in enumerate(values):
        x = round(i * step, 1)
        y = round(height - (v - lo) / (hi - lo) * (height - 2) - 1, 1)
        pts.append(f"{x},{y}")
    color = "#3fb87f" if values[-1] >= values[0] else "#e0564c"
    pt_str = " ".join(pts)
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg" aria-hidden="true" style="overflow:visible;display:block">'
        f'<polyline points="{pt_str}" fill="none" stroke="{color}" '
        f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )


def _quote_from_df(df: pd.DataFrame, symbol: str, asset_class: str) -> dict:
    """Build a normalized quote from a loaded DataFrame. Returns unavailable dict on error."""
    try:
        df = df.dropna(subset=["close"])
        if len(df) < 2:
            return {"available": False, "symbol": symbol, "asset_class": asset_class,
                    "reason": "insufficient data"}
        last  = float(df["close"].iloc[-1])
        prev  = float(df["close"].iloc[-min(25, len(df))])  # ~24h ago if 1h bars
        change_pct = (last - prev) / prev * 100 if prev != 0 else 0.0
        spark_vals = df["close"].tail(_SPARK_BARS).tolist()
        # Price formatting: forex pairs typically < 10, crypto can be > 10k
        decimals = 5 if asset_class == "forex" else (2 if last > 100 else 6)
        return {
            "available": True,
            "symbol": symbol,
            "display_symbol": _format_symbol(symbol, asset_class),
            "display_name": _CRYPTO_NAMES.get(symbol, ""),
            "asset_class": asset_class,
            "price": round(last, decimals),
            "change_pct": round(change_pct, 2),
            "positive": change_pct >= 0,
            "sparkline": _sparkline_svg(spark_vals),
            "source": "csv",
        }
    except Exception as exc:
        logger.warning("markets_data: quote failed for %s: %s", symbol, exc)
        return {"available": False, "symbol": symbol, "asset_class": asset_class,
                "reason": str(exc)}


def _load_crypto_quotes() -> list:
    """Reads data/*_1h.csv, returns one quote per file (sorted by symbol)."""
    paths = sorted(_CRYPTO_DIR.glob("*_USDT_1h.csv"))
    quotes = []
    for p in paths:
        sym = p.stem.replace("_1h", "")   # "BTC_USDT_1h" → "BTC_USDT"
        try:
            df = pd.read_csv(p, usecols=["timestamp", "close"],
                             parse_dates=["timestamp"]).sort_values("timestamp")
            quotes.append(_quote_from_df(df, sym, "crypto"))
        except Exception as exc:
            logger.warning("markets_data: failed to read %s: %s", p.name, exc)
            quotes.append({"available": False, "symbol": sym, "asset_class": "crypto",
                           "reason": str(exc)})
    return quotes


def _load_forex_quotes() -> list:
    """Reads data/forex/*_1h.csv, returns one quote per file (sorted by pair)."""
    paths = sorted(p for p in _FOREX_DIR.glob("*_1h.csv")
                   if "5m" not in p.name and "15m" not in p.name)
    quotes = []
    for p in paths:
        sym = p.stem.replace("_1h", "")   # "EUR_USD_1h" → "EUR_USD"
        try:
            df = pd.read_csv(p, usecols=["timestamp", "close"],
                             parse_dates=["timestamp"]).sort_values("timestamp")
            quotes.append(_quote_from_df(df, sym, "forex"))
        except Exception as exc:
            logger.warning("markets_data: failed to read %s: %s", p.name, exc)
            quotes.append({"available": False, "symbol": sym, "asset_class": "forex",
                           "reason": str(exc)})
    return quotes


def get_markets_list(force_refresh: bool = False) -> dict:
    """
    Returns {'crypto': [...], 'forex': [...]} with normalized quote dicts.
    Results are cached for _CACHE_TTL seconds. Unavailable asset classes
    return empty lists alongside an explicit 'unavailable' entry.
    """
    if not force_refresh:
        cached = cache.get(_CACHE_KEY)
        if cached is not None:
            return cached

    crypto = _load_crypto_quotes()
    forex  = _load_forex_quotes()

    result = {
        "crypto": crypto,
        "forex":  forex,
        # Honest unavailable entries for asset classes with no data source
        "equities":    [{"available": False, "reason": "No equity data source configured"}],
        "etfs":        [{"available": False, "reason": "No ETF data source configured"}],
        "indices":     [{"available": False, "reason": "No index data source configured"}],
        "commodities": [{"available": False, "reason": "No commodity data source configured"}],
        "n_crypto": len([q for q in crypto if q.get("available")]),
        "n_forex":  len([q for q in forex  if q.get("available")]),
    }
    cache.set(_CACHE_KEY, result, _CACHE_TTL)
    return result


def get_watchlist_quotes(symbols_by_class: dict) -> list:
    """
    Returns quote dicts for a specific list of {asset_class: [symbol, ...]} pairs.
    Used by the watchlist page — avoids loading the entire universe.
    """
    all_data = get_markets_list()
    result = []
    for asset_class, syms in symbols_by_class.items():
        pool = {q["symbol"]: q for q in all_data.get(asset_class, []) if "symbol" in q}
        for sym in syms:
            if sym in pool:
                result.append(pool[sym])
            else:
                result.append({"available": False, "symbol": sym,
                               "asset_class": asset_class, "reason": "not found"})
    return result
