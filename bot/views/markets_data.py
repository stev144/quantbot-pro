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
from django.utils.safestring import mark_safe  # claude code changed: 2026-10-21 — sparkline SVG was auto-escaped (raw markup shown on /markets/ and /markets/watchlist/)

logger = logging.getLogger(__name__)

_ROOT       = Path(settings.BASE_DIR)
_CRYPTO_DIR = _ROOT / "data"
_FOREX_DIR  = _ROOT / "data" / "forex"

_CACHE_KEY     = "markets_list_v1"
_CACHE_TTL     = 300   # 5 minutes

_SPARK_BARS    = 24    # close values used for sparkline
_TAIL_BARS     = 72    # claude code changed: 2026-10-21 — recent closes kept for the live forex 24 h change (covers a weekend)

# claude code changed: 2026-10-21 — LIVE display prices (Binance 24 h ticker, MT5 ticks). Memory cache only: these
# prices are never written to data/ — the D-8 crypto holdout and the v7 / QC-2 FX holdouts stay sealed.
_LIVE_CACHE_KEY = "markets_live_v1"
_LIVE_TTL       = 30    # seconds

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
    return mark_safe(  # claude code changed: 2026-10-21 — safe by construction: only rounded floats and two fixed colours, no user input
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg" aria-hidden="true" style="overflow:visible;display:block">'
        f'<polyline points="{pt_str}" fill="none" stroke="{color}" '
        f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )


def _read_csv_tail(path, n: int = 120) -> pd.DataFrame:  # claude code changed: 2026-10-21 — new
    """Last `n` data rows of an OHLC CSV (timestamp + close), sorted. Every quote field uses at most the last
    _TAIL_BARS rows, so parsing the whole multi-year file was wasted work; the result is identical."""
    import io
    with open(path, "rb") as fh:   # seek near the end: read ~64 KB instead of the whole multi-MB file
        header = fh.readline().decode("utf-8")
        fh.seek(0, 2)
        size = fh.tell()
        start = max(len(header.encode("utf-8")), size - 65536)
        fh.seek(start)
        chunk = fh.read().decode("utf-8", errors="replace").splitlines()
    if start > len(header.encode("utf-8")):
        chunk = chunk[1:]   # first line is a partial row after the seek
    rows = [r for r in chunk if r.strip()][-n:]
    df = pd.read_csv(io.StringIO(header + "\n".join(rows) + "\n"), usecols=["timestamp", "close"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp")


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
        tail = df.tail(_TAIL_BARS)   # claude code changed: 2026-10-21 — recent closes for the live 24 h change (forex)
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
            "price_decimals": decimals,                                               # claude code changed: 2026-10-21
            "as_of": pd.Timestamp(df["timestamp"].iloc[-1]).isoformat(),               # claude code changed: 2026-10-21 — last CSV candle time
            "tail": [[pd.Timestamp(t).isoformat(), float(c)] for t, c in zip(tail["timestamp"], tail["close"])],   # claude code changed: 2026-10-21
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
            df = _read_csv_tail(p)   # claude code changed: 2026-10-21 — tail only (was a full 30-45k-row parse per file, ~55 s cold for ~220 files)
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
            df = _read_csv_tail(p)   # claude code changed: 2026-10-21 — tail only (was a full 30-45k-row parse per file, ~55 s cold for ~220 files)
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


def card_quotes(asset_class: str, user=None) -> dict:  # claude code changed: 2026-10-22 — new; shared by every page that shows Markets cards
    """{symbol: quote} for one asset class — the exact quotes the Markets tab renders — each tagged with
    `in_watchlist` for `user` (False for anonymous users). Copies, so the cached list is never mutated."""
    quotes = {q["symbol"]: dict(q) for q in get_markets_list().get(asset_class, []) if q.get("available")}
    watchlisted = set()
    if user is not None and getattr(user, "is_authenticated", False):
        from bot.journal.models import WatchlistItem
        watchlisted = set(WatchlistItem.objects.filter(user=user).values_list("symbol", flat=True))
    for q in quotes.values():
        q["in_watchlist"] = q["symbol"] in watchlisted
    return quotes


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


def _change_vs_24h(tail: list, price: float, now: pd.Timestamp):  # claude code changed: 2026-10-21 — new
    """% change of `price` vs the last CSV close at or before now − 24 h; None when the tail does not reach back."""
    target = now - pd.Timedelta(hours=24)
    base = None
    for t, c in tail:
        if pd.Timestamp(t) <= target:
            base = c
    return None if not base else (price - base) / base * 100


def get_live_quotes(force_refresh: bool = False) -> dict:  # claude code changed: 2026-10-21 — new
    """Live display prices for every Markets card: crypto from Binance's all-symbol 24 h ticker (one request),
    forex from the running MT5 terminal's ticks (mid = (bid + ask) / 2; 24 h change vs the CSV close ~24 h earlier).
    Each source reports its own status; a failure falls back to the CSV price on the page (labelled stale)."""
    if not force_refresh:
        cached = cache.get(_LIVE_CACHE_KEY)
        if cached is not None:
            return cached
    from bot.data_fetcher import get_all_24hr_tickers
    listing = get_markets_list()
    now = pd.Timestamp.now(tz="UTC")
    out = {"fetched_at": now.isoformat(), "crypto": {}, "forex": {}, "status": {}}

    crypto_syms = {q["symbol"].replace("_", ""): q["symbol"] for q in listing.get("crypto", []) if q.get("available")}
    tickers = get_all_24hr_tickers()
    for t in tickers:
        sym = crypto_syms.get(t.get("symbol"))
        if not sym:
            continue
        try:
            price = float(t["lastPrice"])
            if price <= 0:
                continue
            out["crypto"][sym] = {"price": price, "change_pct": float(t["priceChangePercent"]),
                                  "as_of": pd.Timestamp(int(t["closeTime"]), unit="ms", tz="UTC").isoformat(),
                                  "source": "BINANCE_LIVE",
                                  "quote_volume": float(t.get("quoteVolume") or 0)}   # claude code changed: 2026-10-26 — 24 h USDT volume, home page research watchlist
        except (KeyError, TypeError, ValueError):
            continue
    halted = sorted(s for s, v in out["crypto"].items()
                    if (now - pd.Timestamp(v["as_of"])).total_seconds() > 2 * 3600)   # in the ticker list but no recent trade (e.g. Binance status BREAK)
    out["status"]["crypto"] = (f"LIVE ({len(out['crypto']) - len(halted)}/{len(crypto_syms)} trading"
                               + (f"; not trading: {', '.join(s.replace('_USDT', '') for s in halted)})" if halted else ")")
                               if out["crypto"] else "UNAVAILABLE: Binance ticker request failed")

    fx = {q["symbol"]: q for q in listing.get("forex", []) if q.get("available")}
    try:
        from bot.mt5_data_fetcher import get_mt5_live_ticks
        ticks = get_mt5_live_ticks([s.replace("_", "/") for s in fx])
        for canonical, tk in ticks.items():
            sym = canonical.replace("/", "_")
            mid = (tk["bid"] + tk["ask"]) / 2
            out["forex"][sym] = {"price": mid, "bid": tk["bid"], "ask": tk["ask"],
                                 "change_pct": _change_vs_24h(fx[sym].get("tail", []), mid, now),
                                 "as_of": tk["time_utc"], "source": "MT5_LIVE"}
        out["status"]["forex"] = (f"LIVE ({len(out['forex'])}/{len(fx)} pairs)" if out["forex"]
                                  else "UNAVAILABLE: MT5 returned no ticks")
    except Exception as exc:   # terminal closed / package missing: the page keeps the CSV price, labelled stale
        out["status"]["forex"] = f"UNAVAILABLE: {exc}"[:200]
    # claude code changed: 2026-10-25 — forex session state (owner calendar), so a weekend price is shown as
    # MARKET CLOSED rather than STALE; independent of whether MT5 is reachable.
    from bot.quant_core.market_profile import forex_session_status
    out["forex_session"] = forex_session_status(now)
    if not out["forex_session"]["is_open"]:
        out["status"]["forex"] = "MARKET CLOSED (weekend / holiday) · " + out["status"].get("forex", "")

    cache.set(_LIVE_CACHE_KEY, out, _LIVE_TTL)
    return out
