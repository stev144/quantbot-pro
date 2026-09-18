# ============================================================
# bot/forex_data_fetcher.py
#
# Forex Multi-Asset Integration — Milestone B.
#
# claude code changed: new file. The Forex equivalent of
# fetch_all_symbols.py — structurally mirrors it (SYMBOLS/INTERVAL
# constants, a universe-selection JSON with a documented fallback list,
# symbol_to_filename(), a fetch loop that writes cleaned CSVs) but is
# NOT a copy-paste: data acquisition is legitimately asset-specific
# (the architecture audit's own conclusion — see the approved plan),
# so this file owns its own provider integration rather than trying to
# force bot/data_fetcher.py's Binance-REST-shaped code to also speak a
# second, unrelated API.
#
# Provider: Yahoo Finance's public chart endpoint (the same data
# `yfinance` wraps), called directly via `requests` — already an
# installed dependency, matching bot/data_fetcher.py's own existing
# direct-REST-call convention. Confirmed reachable from this machine
# and returning real hourly OHLCV for major FX pairs via a live probe
# during the design phase.
#
# Two real facts confirmed during that same probe, both load-bearing
# here:
#   - FX volume is reported as 0 by this provider — not missing data,
#     just not a meaningful/centralized quantity the way crypto volume
#     is. Never treated as a data-quality failure; documented, not
#     silently dropped.
#   - A real ~49-hour gap appears every week (Friday close -> Sunday
#     reopen). This is an EXPECTED calendar gap, not corrupt data —
#     detected and logged, never fabricated/filled.
#
# Written CSVs live under data/forex/, a directory segregated from
# data/*.csv (crypto) so Forex data can never collide with or be
# silently confused for crypto data — see bot/instruments.py's
# resolve_ohlcv_path() FOREX branch, which points here.
# ============================================================

import json
import logging
import sys
from datetime import datetime, timedelta, timezone  # claude code changed: timedelta added — Data-Layer Audit "wire forex_data_fetcher.py too" needs it for the rolling (start, end) window
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── Configuration ────────────────────────────────────────────────────────────

INTERVAL = "1h"

# claude code changed: new — a small, documented set of major FX pairs,
# used only when no universe selection has ever been persisted (mirrors
# fetch_all_symbols.py's own _LEGACY_DEFAULT_SYMBOLS pattern exactly).
# This is NOT a permanent hardcoded universe — see load_forex_universe_symbols().
_FOREX_DEFAULT_SYMBOLS: List[str] = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CAD",
    "USD/CHF",
    "NZD/USD",
    "EUR/GBP",
]

_UNIVERSE_PATH = Path("data/forex_universe_selection.json")

OUTPUT_DIR = Path("data/forex")

# claude code changed: new — Yahoo's public chart endpoint restricts
# intraday (<1d) history to roughly the last 730 days. This is a real,
# documented provider limit (not a guess), the direct Forex analogue of
# fetch_all_symbols.py's own documented Binance 50,000-candle ceiling
# reasoning. Kept a few days under the wall rather than exactly at it.
HISTORY_DAYS = 729

FRESHNESS_MAX_AGE_DAYS = 7   # claude code changed: same convention as fetch_all_symbols.py's own freshness gate

_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_YAHOO_USER_AGENT = "Mozilla/5.0"   # claude code changed: Yahoo's public endpoint rejects requests with no User-Agent header


def load_forex_universe_symbols(path: Path = _UNIVERSE_PATH) -> Optional[List[str]]:
    """
    claude code changed: new — mirrors bot.universe_selector.load_universe_symbols()
    exactly: returns None (never raises) if no selection has ever been
    persisted, so SYMBOLS below can fall back cleanly. Kept deliberately
    simple (no liquidity-ranking algorithm like crypto's universe_selector.py)
    because the major-FX-pair universe is not subject to the same
    listing/delisting churn a ~100-coin exchange universe is — but it is
    still a real, reproducible, auditable JSON file, never a value only
    ever typed once into Python and forgotten.
    """
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    return data.get("symbols")


def save_forex_universe_selection(symbols: List[str], path: Path = _UNIVERSE_PATH) -> None:
    """claude code changed: new — writes a reproducible, timestamped, auditable universe selection, mirroring universe_selector.py's save_universe_selection()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "selected_at": datetime.now(timezone.utc).isoformat(),
        "provider": "yahoo_finance",
        "target_size": len(symbols),
        "symbols": symbols,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


SYMBOLS: List[str] = load_forex_universe_symbols() or _FOREX_DEFAULT_SYMBOLS


def symbol_to_filename(symbol: str) -> str:
    """claude code changed: new — same convention as fetch_all_symbols.py's own symbol_to_filename(): "EUR/USD" -> "EUR_USD_1h.csv"."""
    return symbol.replace("/", "_") + f"_{INTERVAL}.csv"


def _to_yahoo_ticker(canonical_symbol: str) -> str:
    """claude code changed: new — "EUR/USD" -> "EURUSD=X", Yahoo's real FX ticker convention (confirmed live during the design probe)."""
    base, _, quote = canonical_symbol.partition("/")
    if not base or not quote:
        raise ValueError(f"'{canonical_symbol}' is not a valid BASE/QUOTE forex symbol")
    return f"{base}{quote}=X"


class ForexProviderError(RuntimeError):
    """claude code changed: new — raised for a genuine provider/response problem (bad payload, HTTP error), distinct from a network-connectivity failure (requests.RequestException), so callers/health-checks can tell "the code is broken" apart from "the network/provider is unreachable right now" — the exact distinction the mission's health-check phase requires."""


def get_forex_klines(symbol: str, interval: str = INTERVAL, history_days: int = HISTORY_DAYS) -> pd.DataFrame:
    """
    claude code changed: new — THE provider seam. Fetches real historical
    OHLCV for one FX pair from Yahoo's public chart endpoint. Returns a
    DataFrame with columns [timestamp, open, high, low, close, volume],
    UTC-aware timestamps, sorted, deduplicated. Raises requests.RequestException
    for a real connectivity failure (network/timeout/DNS) and
    ForexProviderError for a malformed/error response — kept as two
    distinct exception types on purpose, so a health check (or any other
    caller) can tell "provider unreachable" apart from "provider responded
    but something is actually wrong."
    """
    ticker = _to_yahoo_ticker(symbol)
    url = _YAHOO_CHART_URL.format(ticker=ticker)
    params = {"interval": interval, "range": f"{history_days}d"}
    headers = {"User-Agent": _YAHOO_USER_AGENT}

    response = requests.get(url, params=params, headers=headers, timeout=15)
    response.raise_for_status()
    payload = response.json()

    chart = payload.get("chart", {})
    if chart.get("error"):
        raise ForexProviderError(f"Yahoo chart API returned an error for {ticker}: {chart['error']}")
    result = chart.get("result")
    if not result:
        raise ForexProviderError(f"Yahoo chart API returned no result for {ticker} — empty/malformed payload")

    entry = result[0]
    timestamps = entry.get("timestamp")
    quote = entry.get("indicators", {}).get("quote", [{}])[0]
    if not timestamps or not quote:
        raise ForexProviderError(f"Yahoo chart API returned no timestamp/quote data for {ticker}")

    df = pd.DataFrame({
        "timestamp": pd.to_datetime(timestamps, unit="s", utc=True),
        "open": quote.get("open"),
        "high": quote.get("high"),
        "low": quote.get("low"),
        "close": quote.get("close"),
        # claude code changed: FX volume is legitimately 0/not meaningful
        # from this provider (confirmed live during the design probe) —
        # kept as a real column (schema compatibility with feature_calculator.py's
        # hard OHLCV requirement) rather than dropped, but callers must
        # not interpret it as a liquidity signal. See module docstring.
        "volume": quote.get("volume", [0] * len(timestamps)),
    })

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["open", "high", "low", "close"], inplace=True)
    df["volume"] = df["volume"].fillna(0)
    df.drop_duplicates(subset=["timestamp"], inplace=True)
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def detect_gaps(df: pd.DataFrame, interval: str = INTERVAL) -> List[Dict]:
    """
    claude code changed: new — real gap detection for a market that
    legitimately closes (unlike crypto's 24/7 assumption baked into other
    engines' comments). Returns a list of {start, end, duration_hours,
    likely_weekend} records for every gap wider than 1.5x the expected
    candle spacing. `likely_weekend` is True when the gap starts on a
    Friday/Saturday — an honest heuristic label, not a claim of a modeled
    trading calendar (bot.instruments.py's own TRADING_DAYS_PER_YEAR
    comment makes the same "convention, not a modeled calendar"
    distinction). Never fills or fabricates a gap — purely observational,
    for data-quality reporting.
    """
    expected_hours = {"1h": 1.0, "4h": 4.0, "1d": 24.0}.get(interval)
    if expected_hours is None or len(df) < 2:
        return []

    gaps = []
    ts = df["timestamp"]
    for i in range(1, len(ts)):
        delta_hours = (ts.iloc[i] - ts.iloc[i - 1]).total_seconds() / 3600.0
        if delta_hours > expected_hours * 1.5:
            gaps.append({
                "start": ts.iloc[i - 1].isoformat(),
                "end": ts.iloc[i].isoformat(),
                "duration_hours": round(delta_hours, 2),
                "likely_weekend": ts.iloc[i - 1].weekday() in (4, 5),   # Friday=4, Saturday=5
            })
    return gaps


def download_all_forex_symbols() -> dict:
    """
    claude code changed: new — the Forex equivalent of fetch_all_symbols.py's
    download_all_symbols(). Same shape (per-symbol try/except, a summary
    dict, a freshness check), same CSV cleanliness guarantees (dedup,
    sorted, UTC), written to data/forex/ instead of data/ so Forex data
    is never silently mixed with or mistaken for crypto data.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=timezone.utc)

    # claude code changed: Data-Layer Audit — "wire forex_data_fetcher.py
    # too". Lazy imports: bot.instruments imports THIS module at top level
    # for SYMBOLS/INTERVAL/symbol_to_filename (same reasoning as this
    # file's own write_provenance_marker import below); YahooForexProvider
    # itself imports bot.instruments too, so a top-level import of it here
    # would cycle the same way. Rolling window, not a fixed date — same
    # reasoning as bot/fetch_all_symbols.py's own "wire into
    # fetch_all_symbols.py first" step: "most recent HISTORY_DAYS days" is
    # computed fresh from `now` every run, functionally identical to
    # get_forex_klines(symbol)'s own default history_days=HISTORY_DAYS
    # behavior, just expressed as a real date range because
    # get_historical_bars()'s contract is date-range shaped for every
    # provider, not Yahoo-specific day-count semantics.
    from bot.instruments import get_instrument
    from bot.yahoo_forex_provider import YahooForexProvider

    start = now - timedelta(days=HISTORY_DAYS)
    provider = YahooForexProvider()

    print("\n" + "=" * 80)
    print(f"DOWNLOADING FOREX DATA FOR {len(SYMBOLS)} PAIRS")
    print(f"History : most recent {HISTORY_DAYS} days as of {now.isoformat()}")
    print(f"Interval: {INTERVAL}")
    print("Fetcher : YahooForexProvider (bot/forex_data_fetcher.py via the MarketDataProvider contract)")
    print("=" * 80)

    summary = {}

    for symbol in SYMBOLS:
        print(f"\n[Downloading] {symbol}")
        try:
            # claude code changed: was get_forex_klines(symbol) directly.
            # Every symbol in SYMBOLS is derived from THIS file's own
            # SYMBOLS by bot.instruments' _build_forex_registry(), so
            # get_instrument(symbol) is guaranteed to resolve here.
            instrument = get_instrument(symbol)
            bars = provider.get_historical_bars(instrument, INTERVAL, start, now)
            df = bars.data
            if df.empty:
                raise ForexProviderError("provider returned an empty DataFrame")

            gaps = detect_gaps(df)
            weekend_gaps = [g for g in gaps if g["likely_weekend"]]
            other_gaps = [g for g in gaps if not g["likely_weekend"]]

            filename = symbol_to_filename(symbol)
            filepath = OUTPUT_DIR / filename
            df.to_csv(filepath, index=False)

            candle_count = len(df)

            # claude code changed: Data-Layer Audit Step 1 — records that
            # THIS fetcher wrote filepath, so bot/instruments.py's
            # _build_forex_registry() reports venue="yahoo_finance" from a
            # real marker rather than a hardcoded literal, and can tell this
            # file apart from one mt5_data_fetcher.py later overwrites at the
            # exact same path. Lazy import: bot.instruments imports this
            # module at top level, so a top-level import here would cycle.
            #
            # claude code changed: Data-Layer Audit Step 5 — now also passes
            # the real dataset identity (source/symbol/timeframe/date-range/
            # row_count), so a real sha256 fingerprint gets embedded too —
            # closing the "no data fingerprint recorded" gap
            # FOREX_CROSS_SECTIONAL_RESEARCH_VALIDATION_AUDIT.md's Phase 11
            # flagged as open. source="yahoo_finance_chart_api" matches the
            # DatasetIdentity.source YahooForexProvider already uses for the
            # in-memory CanonicalBars path, so a disk-written file and an
            # in-memory fetch of the identical request produce comparable
            # identities.
            from bot.instruments import write_provenance_marker
            write_provenance_marker(
                filepath, venue="yahoo_finance", data_source="forex_data_fetcher",
                source="yahoo_finance_chart_api", symbol=symbol, timeframe=INTERVAL,
                start_date=str(df["timestamp"].iloc[0].date()), end_date=str(df["timestamp"].iloc[-1].date()),
                row_count=candle_count,
            )
            last_timestamp = df["timestamp"].iloc[-1]
            staleness_days = (now - last_timestamp).total_seconds() / 86400
            is_fresh = staleness_days <= FRESHNESS_MAX_AGE_DAYS
            if not is_fresh:
                logger.warning(f"{symbol}: data is STALE — most recent candle is {last_timestamp.isoformat()} ({staleness_days:.1f} days old)")

            print(f"  Downloaded    : {candle_count:,} candles")
            print(f"  Date range    : {df['timestamp'].iloc[0]} -> {last_timestamp}")
            print(f"  Weekend gaps  : {len(weekend_gaps)} (expected calendar closures)")
            if other_gaps:
                print(f"  ⚠ Other gaps  : {len(other_gaps)} (not weekend-shaped — worth investigating)")
            print(f"  Saved to      : {filepath}")

            summary[symbol] = {
                "success": True,
                "candles": candle_count,
                "file": str(filepath),
                "error": None,
                "last_timestamp": last_timestamp.isoformat(),
                "staleness_days": round(staleness_days, 2),
                "is_fresh": is_fresh,
                "weekend_gaps": len(weekend_gaps),
                "other_gaps": len(other_gaps),
            }
        except requests.RequestException as e:
            print(f"  ✗ Connectivity error: {e}")
            summary[symbol] = {"success": False, "candles": 0, "file": None, "error": f"CONNECTIVITY: {e}"}
        except ForexProviderError as e:
            print(f"  ✗ Provider error: {e}")
            summary[symbol] = {"success": False, "candles": 0, "file": None, "error": f"PROVIDER: {e}"}
        except Exception as e:
            print(f"  ✗ Error: {e}")
            summary[symbol] = {"success": False, "candles": 0, "file": None, "error": str(e)}

    success_count = sum(1 for v in summary.values() if v["success"])
    print("\n" + "=" * 80)
    print(f"DOWNLOAD COMPLETE — {success_count}/{len(SYMBOLS)} pairs downloaded successfully")
    print("=" * 80)
    return summary


if __name__ == "__main__":
    download_all_forex_symbols()
