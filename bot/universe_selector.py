# ============================================================
# bot/universe_selector.py
# claude code changed: new file — dynamic pairs-trading universe
# selection (20 -> 50 symbols).
#
# WHY THIS EXISTS: the symbol universe used to be one hand-typed list
# (bot/fetch_all_symbols.py's SYMBOLS) that bot/research/cointegration_engine.py,
# cross_section_engine.py, and others each separately hardcoded their own
# copy of — a real, already-documented drift risk (see bot/instruments.py's
# own module docstring). "Most liquid 50 symbols" is also not a fact that
# stays true forever — liquidity rankings genuinely drift — so this is
# built to be RE-RUN periodically, not a one-time list to hand-edit.
#
# This script makes exactly one kind of real network call: ccxt's public
# fetch_tickers()/fetch_ohlcv() against Binance spot — no credentials, no
# live capital, matches this project's own "no mocking, real public ccxt
# endpoints" testing convention.
#
# Output: data/universe_selection.json — {selected_at, quote, target_size,
# min_years_used, symbols: [...], liquid_but_excluded_for_history: [...]}.
# bot/fetch_all_symbols.py reads this file (falling back to its own
# historical 20-symbol default if it doesn't exist yet), and
# bot/instruments.py derives from fetch_all_symbols.SYMBOLS as it always
# has — so this one file is now the actual root of the whole universe,
# with everything downstream picking up a re-run automatically.
# ============================================================

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import ccxt

logger = logging.getLogger(__name__)

# claude code changed: new — deliberately a plain, easy-to-extend set
# rather than a regex/heuristic. Stablecoins are trivially "cointegrated"
# with each other by construction (they're all pegged to the same $1),
# which would pollute pair-selection results with meaningless "matches";
# wrapped/liquid-staking derivatives are excluded for the same reason —
# WBTC/BTC or stETH/ETH aren't a real cointegration relationship, they're
# the same asset wearing two tickers.
EXCLUDED_BASE_ASSETS = {
    # Stablecoins (fiat-pegged)
    "USDT", "USDC", "DAI", "TUSD", "FDUSD", "BUSD", "USDP", "PYUSD",
    "USDD", "GUSD", "USDE", "FRAX", "LUSD", "USTC", "EURI", "AEUR",
    # Wrapped / liquid-staking derivatives (1:1 or near-1:1 trackers of another real asset)
    "WBTC", "WETH", "WBETH", "STETH", "WSTETH", "CBETH", "RETH", "SFRXETH", "BETH",
    # claude code changed: new — real bug found on the first successful 50-symbol
    # run: EUR/USDT ranked liquid enough to qualify (#18) and has ample history,
    # but it's Binance's fiat Euro market, not a crypto asset — a forex pair has
    # no place in a crypto pairs-trading universe on a crypto trading platform.
    # Listed defensively alongside other fiat tickers Binance lists spot markets
    # for, in case liquidity rankings shift and one of these surfaces later.
    "EUR", "GBP", "TRY", "AUD", "BRL", "RUB", "UAH", "ZAR", "NGN", "COP", "ARS",
}

OUTPUT_PATH = Path("data") / "universe_selection.json"

DEFAULT_TARGET_SIZE = 50
DEFAULT_PRIMARY_MIN_YEARS = 5.0
DEFAULT_FALLBACK_MIN_YEARS = 4.0
DEFAULT_QUOTE = "USDT"
# claude code changed: new — how much slack to allow between a candidate's
# actual daily-candle count and the theoretically-expected count for the
# requested window, before treating it as "has a real gap, disqualify."
# 0.95 tolerates a handful of missing days (a real exchange outage/halt)
# without accepting a symbol that only listed partway through the window.
CONTINUITY_TOLERANCE = 0.95
# claude code changed: new — allowed slack between a symbol's first real
# daily candle and the requested cutoff date, to absorb exchange listing-
# date granularity/timezone rounding without being so strict that a
# symbol listed 3 days "late" relative to an exact year-boundary gets
# wrongly disqualified.
LISTING_DATE_SLACK_DAYS = 30


@dataclass
class UniverseSelectionResult:
    selected_at: str
    quote: str
    target_size: int
    requested_primary_min_years: float
    requested_fallback_min_years: float
    min_years_used: float
    used_fallback: bool
    symbols: List[str]                              # canonical "BASE/QUOTE" form, e.g. "BTC/USDT" — ranked by liquidity, most liquid first
    liquid_but_excluded_for_history: List[Dict] = field(default_factory=list)   # symbols that ranked highly enough to matter but failed the longevity check
    n_candidates_considered: int = 0
    n_candidates_history_checked: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)


def _is_excluded(base_asset: str) -> bool:
    return base_asset.upper() in EXCLUDED_BASE_ASSETS


def _rank_candidates_by_liquidity(exchange, quote: str) -> List[Dict]:
    """
    Real fetch_tickers() call. Only compares pairs on the SAME quote
    currency (per the mission's own explicit requirement) — never mixes
    e.g. BTC/USDT volume with BTC/USDC volume for the same base asset,
    which would double-count or misrank a base asset traded across
    multiple stablecoin quotes.
    """
    exchange.load_markets()
    tickers = exchange.fetch_tickers()

    candidates = []
    for symbol, ticker in tickers.items():
        if not symbol.endswith(f"/{quote}"):
            continue
        base = symbol.split("/")[0]
        if _is_excluded(base):
            continue
        market = exchange.markets.get(symbol)
        if market is None or market.get("spot") is False:
            continue   # exclude anything ccxt surfaces that isn't a real spot market (futures/margin-only listings)
        quote_volume = ticker.get("quoteVolume")
        if quote_volume is None:
            continue
        candidates.append({"symbol": symbol, "base": base, "quote_volume": float(quote_volume)})

    candidates.sort(key=lambda c: c["quote_volume"], reverse=True)
    return candidates


def _check_history(exchange, symbol: str, min_years: float, page_limit: int = 1000, max_pages: int = 4) -> Optional[Dict]:
    """
    Real fetch_ohlcv() call(s), daily candles, checking for continuous
    history back `min_years` from *today* (computed fresh every call —
    never a hardcoded historical date, per the mission's own requirement).
    Returns a small report dict on success, None on failure.

    claude code changed: real bug found by actually running this against
    live Binance data — a single fetch_ohlcv call requesting ~1826 daily
    candles (5 years) silently came back capped at exactly 1000, Binance's
    real per-request kline limit (ccxt does not raise for this — it just
    returns however many the exchange actually sends). That made EVERY
    symbol fail the continuity check, including BTC/USDT and ETH/USDT,
    which obviously have far more than 5 years of real history — a
    completely broken "0/50 qualified" result on the first real run.
    Fixed by paginating forward from `since` in `page_limit`-sized pages
    (1000, matching Binance's real cap) up to `max_pages` (4 = up to 4000
    daily candles — comfortably more than 5 years' ~1826 needed), rather
    than trusting one oversized `limit` value to be honored.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=min_years * 365.25)
    since_ms = int(cutoff.timestamp() * 1000)
    expected_days = min_years * 365.25

    all_candles: List = []
    cursor_ms = since_ms
    try:
        for _ in range(max_pages):
            page = exchange.fetch_ohlcv(symbol, timeframe="1d", since=cursor_ms, limit=page_limit)
            if not page:
                break
            all_candles.extend(page)
            if len(page) < page_limit:
                break   # exchange returned fewer than a full page — we've reached the most recent data
            cursor_ms = page[-1][0] + 1   # advance past the last candle returned, to avoid re-fetching it
            if cursor_ms >= int(now.timestamp() * 1000):
                break
    except Exception as e:
        logger.warning(f"{symbol}: history check failed ({e})")
        return None

    if not all_candles:
        return None

    actual_days = len(all_candles)
    if actual_days < expected_days * CONTINUITY_TOLERANCE:
        return None   # either listed too recently, or has a real gap in the middle

    first_candle_dt = datetime.fromtimestamp(all_candles[0][0] / 1000, tz=timezone.utc)
    if first_candle_dt > cutoff + timedelta(days=LISTING_DATE_SLACK_DAYS):
        return None   # first real candle is meaningfully later than the requested cutoff — not actually old enough

    return {
        "first_candle": first_candle_dt.isoformat(),
        "n_daily_candles": actual_days,
        "requested_min_years": min_years,
    }


def select_universe(
    exchange=None,
    target_size: int = DEFAULT_TARGET_SIZE,
    quote: str = DEFAULT_QUOTE,
    primary_min_years: float = DEFAULT_PRIMARY_MIN_YEARS,
    fallback_min_years: float = DEFAULT_FALLBACK_MIN_YEARS,
    rate_limit_sleep_seconds: float = 0.25,
) -> UniverseSelectionResult:
    """
    THE real selection routine. Ranks by liquidity, excludes stablecoins/
    wrapped assets, then walks the ranked list checking real continuous
    history — stopping once `target_size` qualifying symbols are found
    (not exhaustively checking every candidate on the exchange, which
    would be needlessly slow and hit rate limits for no benefit once
    enough real candidates have already qualified).

    Falls back from `primary_min_years` to `fallback_min_years` only if
    the primary threshold can't fill the target size — both outcomes are
    recorded explicitly in the result, never silently.
    """
    if exchange is None:
        exchange = ccxt.binance({"enableRateLimit": True})

    ranked = _rank_candidates_by_liquidity(exchange, quote)

    def _fill_at(min_years: float):
        selected: List[str] = []
        excluded_for_history: List[Dict] = []
        checked = 0
        for candidate in ranked:
            if len(selected) >= target_size:
                break
            checked += 1
            report = _check_history(exchange, candidate["symbol"], min_years)
            time.sleep(rate_limit_sleep_seconds)   # claude code changed: extra throttle on top of ccxt's own enableRateLimit — fetch_ohlcv per candidate adds up fast across ~50-150 candidates
            if report is not None:
                selected.append(candidate["symbol"])
            else:
                excluded_for_history.append({"symbol": candidate["symbol"], "quote_volume": candidate["quote_volume"]})
        return selected, excluded_for_history, checked

    selected, excluded, checked = _fill_at(primary_min_years)
    used_fallback = False
    min_years_used = primary_min_years

    if len(selected) < target_size:
        logger.warning(
            f"Only {len(selected)}/{target_size} symbols qualified at {primary_min_years} years — "
            f"falling back to {fallback_min_years} years"
        )
        selected, excluded, checked = _fill_at(fallback_min_years)
        used_fallback = True
        min_years_used = fallback_min_years

    result = UniverseSelectionResult(
        selected_at=datetime.now(timezone.utc).isoformat(),
        quote=quote,
        target_size=target_size,
        requested_primary_min_years=primary_min_years,
        requested_fallback_min_years=fallback_min_years,
        min_years_used=min_years_used,
        used_fallback=used_fallback,
        symbols=selected[:target_size],
        liquid_but_excluded_for_history=excluded,
        n_candidates_considered=len(ranked),
        n_candidates_history_checked=checked,
    )
    return result


def save_universe_selection(result: UniverseSelectionResult, path: Path = OUTPUT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(result.to_dict(), f, indent=2)


def load_universe_symbols(path: Path = OUTPUT_PATH) -> Optional[List[str]]:
    """Used by bot/fetch_all_symbols.py — returns None if no selection has ever been persisted, so callers can fall back cleanly."""
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    return data.get("symbols")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    print("=" * 80)
    print(f"UNIVERSE SELECTION — target {DEFAULT_TARGET_SIZE} symbols, quote={DEFAULT_QUOTE}")
    print("=" * 80)
    result = select_universe()
    save_universe_selection(result)
    print(f"\nSelected {len(result.symbols)}/{result.target_size} symbols "
          f"(min_years_used={result.min_years_used}, fallback={result.used_fallback})")
    for i, s in enumerate(result.symbols, 1):
        print(f"  {i:2d}. {s}")
    if result.liquid_but_excluded_for_history:
        print(f"\n{len(result.liquid_but_excluded_for_history)} liquid candidates excluded for insufficient history:")
        for ex in result.liquid_but_excluded_for_history[:20]:
            print(f"  - {ex['symbol']}")
    print(f"\nSaved to {OUTPUT_PATH}")
