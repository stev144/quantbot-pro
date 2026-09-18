# fetch_all_symbols.py
#
# Downloads OHLCV data for the tracked symbol universe using the project's
# data_fetcher module.
#
# What this does vs the old version:
#   OLD: ccxt, no pagination, ~500-1000 candles max, no cleaning, no normalisation
#   NEW: project data_fetcher, full pagination, deduplication, NaN removal,
#        rate-limit handling, caching, symbol normalisation
#
# Output: data/{SYMBOL}_1h.csv  (same filenames as before — nothing downstream breaks)

import logging
import sys
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta, timezone

# claude code changed: new — real bug, not introduced by this mission but
# hit while running this script for the first time under a redirected
# (non-console) stdout: Windows' default stdout codec is cp1252, which
# can't encode the ✓/✗ characters this script prints, and crashes with
# UnicodeEncodeError partway through a run. reconfigure() is a no-op on a
# stream that's already UTF-8 capable, so this doesn't change behavior in
# a real UTF-8 terminal — it only fixes the redirected/piped case.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── Use the project data fetcher, not ccxt ──────────────────────────────────
# claude code changed: was `from bot.data_fetcher import get_klines` — that
# was this file's ONLY call site for get_klines(); download_all_symbols()
# below now goes through BinanceKlinesProvider instead (Data-Layer Audit:
# "wire into fetch_all_symbols.py first"), a thin wrapper around
# data_fetcher.get_klines_by_date() — a different function, imported
# lazily inside download_all_symbols() itself to avoid a circular import
# (bot.instruments, which BinanceKlinesProvider depends on, imports this
# module at top level for SYMBOLS/INTERVAL/symbol_to_filename).

# claude code changed: new — the universe is now selected dynamically (see
# bot/universe_selector.py) rather than being a single hand-typed list this
# file alone maintained. Falls back to the original 20-symbol list if the
# selector has never been run yet, so this script still works standalone.
from bot.universe_selector import load_universe_symbols

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

# Symbols in ccxt-style format — data_fetcher normalises these automatically
# BTC/USDT  →  _normalize_symbol()  →  BTCUSDT  (Binance format)

_LEGACY_DEFAULT_SYMBOLS = [
    'BTC/USDT',
    'ETH/USDT',
    'BNB/USDT',
    'SOL/USDT',
    'ADA/USDT',
    'AVAX/USDT',
    'DOT/USDT',
    'MATIC/USDT',
    'ARB/USDT',
    'LINK/USDT',
    'UNI/USDT',
    'AAVE/USDT',
    'XRP/USDT',
    'XLM/USDT',
    'DOGE/USDT',
    'SHIB/USDT',
    'ATOM/USDT',
    'FIL/USDT',
    'APT/USDT',
    'OP/USDT',
]

SYMBOLS = load_universe_symbols() or _LEGACY_DEFAULT_SYMBOLS

INTERVAL = '1h'

# claude code changed: real bug fix. The old approach fetched FORWARD from a
# fixed START_DATE=2020-01-01 up to a 50,000-candle safety ceiling
# (MAX_ALLOWED_CANDLES in data_fetcher.py — a real emergency-protection
# limit, not something to just raise). At 1h resolution, 2020-01-01 to today
# is now well past 50,000 hours, so the forward fetch always exhausted its
# candle budget before reaching the present — confirmed directly against
# the real on-disk data, which stopped at 2025-09-15 regardless of when the
# script was actually run. This gets WORSE every year, not better.
#
# Fixed by switching to a backward-from-now fetch of the most recent
# HISTORY_CANDLES candles. "Most recent N candles" is self-correcting
# forever — it always means the latest data, regardless of how much time
# has passed since this constant was chosen, with no date arithmetic to
# revisit.
#
# claude code changed: Data-Layer Audit — "wire into fetch_all_symbols.py
# first". download_all_symbols() now goes through BinanceKlinesProvider,
# which wraps get_klines_by_date() (start/end), not get_klines() (N-candle
# count) — the SAME forward-pagination function this comment originally
# said the fix moved away from. The critical difference, and why this
# does NOT reintroduce the original bug: the (start, end) window is
# computed FRESH from `now` every run (see download_all_symbols()'s own
# comment) — a ROLLING window, never the old fixed START_DATE=2020-01-01
# whose distance from "now" only ever grew. "Most recent HISTORY_CANDLES
# candles" and "the last {span_hours} hours as of right now" are the same
# self-correcting guarantee, just expressed as a date range instead of a
# candle count — required because BinanceKlinesProvider's contract
# (get_historical_bars(instrument, timeframe, start, end)) is date-range
# shaped for every provider, not Binance-specific candle-count semantics.
#
# HISTORY_YEARS=5 x 365 x 24 = 43,800 candles, comfortably under the
# 50,000 ceiling (leaving ~6,200 candles / ~258 days of headroom) while
# giving far more runway than any current consumer needs — cointegration_engine.py's
# own TRAINING_WINDOW is 10,000 candles (~417 days) and its ZSCORE_WINDOW
# is 504 candles (~3 weeks). The trade-off, stated plainly: routine
# re-fetches no longer carry the full 2020-era history — a one-time deep
# backfill for research needing that specific window would need a
# separate, explicit fetch, not this routine refresh path.
HISTORY_YEARS   = 5
HISTORY_CANDLES = int(HISTORY_YEARS * 365 * 24)   # 43,800 hourly candles

# claude code changed: new — Step 3's explicit freshness requirement. Data
# is only considered fresh if the most recent candle is within this many
# days of "now" at the time the check runs (computed fresh every call,
# never a hardcoded date).
FRESHNESS_MAX_AGE_DAYS = 7

OUTPUT_DIR      = Path('data')

# ── Helpers ──────────────────────────────────────────────────────────────────

def symbol_to_filename(symbol: str) -> str:
    """
    Convert ccxt-style symbol to the CSV filename used by run_research_all.py.

    BTC/USDT  →  BTC_USDT_1h.csv
    """
    return symbol.replace('/', '_') + f'_{INTERVAL}.csv'


def prepare_dataframe_for_csv(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    The caller may hand this either shape:
      - data_fetcher.get_klines()'s own shape: a UTC-aware DatetimeIndex
        called 'timestamp', columns open/high/low/close/volume (+ extra
        Binance cols)
      - claude code changed: BinanceKlinesProvider.get_historical_bars()'s
        CanonicalBars.data shape (Data-Layer Audit — "wire into
        fetch_all_symbols.py first"): 'timestamp' already a plain column,
        not an index. df.reset_index() below is a no-op for this shape in
        every way that matters — it inserts a harmless numeric 'index'
        column that the `keep` filter two lines down silently drops, since
        'index' was never in `keep`. Verified, not assumed: this function
        needed zero changes to accept both shapes correctly.

    run_research_all.py / feature_calculator.py expect:
      - A plain 'timestamp' column (not the index)
      - Only: timestamp, open, high, low, close, volume (+ qav/num_trades/
        taker_base_vol, see the `keep` comment below)

    This function bridges the gap cleanly.
    """
    # Reset index so 'timestamp' becomes a regular column (or, for an
    # already-column-shaped input, adds a harmless 'index' column the
    # `keep` filter below drops — see docstring above).
    result = df.reset_index()

    # Rename index column if it came back as something else
    if 'timestamp' not in result.columns and result.columns[0] != 'timestamp':
        result = result.rename(columns={result.columns[0]: 'timestamp'})

    # Keep only the columns downstream code needs
    # claude code changed: added qav/num_trades/taker_base_vol — Phase 2B
    # Step 1. Binance's raw kline response already includes these (see
    # data_fetcher._build_ohlcv_dataframe's column list); this pipeline
    # discarded them before saving to disk for no real reason. Classified
    # "safe to persist immediately" (stable, documented Binance semantics,
    # purely additive — no existing column's values change). close_time and
    # taker_quote_vol deliberately NOT persisted: close_time is fully
    # redundant with timestamp + the known 1h interval, and taker_quote_vol
    # is a redundant transform of taker_base_vol x price, not new
    # information — not accumulating fields for their own sake.
    keep = [
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'qav', 'num_trades', 'taker_base_vol',
    ]
    existing = [c for c in keep if c in result.columns]
    result = result[existing].copy()

    # Ensure numeric types (data_fetcher already does this but be defensive)
    for col in ['open', 'high', 'low', 'close', 'volume', 'qav', 'taker_base_vol']:  # claude code changed: was only OHLCV — see above
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors='coerce')

    # Final NaN drop — belt and braces
    result.dropna(subset=['open', 'high', 'low', 'close', 'volume'], inplace=True)

    # Remove any duplicate timestamps that might have slipped through pagination
    result.drop_duplicates(subset=['timestamp'], inplace=True)

    # Sort chronologically
    result.sort_values('timestamp', inplace=True)
    result.reset_index(drop=True, inplace=True)

    return result


# ── Main download loop ────────────────────────────────────────────────────────

def download_all_symbols() -> dict:
    """
    Download all symbols and save to CSV.

    Returns a summary dict:
        {symbol: {'success': bool, 'candles': int, 'file': str, 'error': str}}
    """
    OUTPUT_DIR.mkdir(exist_ok=True)

    now = datetime.now(tz=timezone.utc)

    # claude code changed: Data-Layer Audit — "wire into fetch_all_symbols.py
    # first". Lazy imports: bot.instruments imports THIS module
    # (bot.fetch_all_symbols) at top level for SYMBOLS/INTERVAL/
    # symbol_to_filename, so a top-level import here of anything that
    # depends on bot.instruments (BinanceKlinesProvider does, for the
    # Instrument type) would cycle — same reasoning already documented at
    # this file's own top-level get_klines import removal, and the same
    # pattern forex_data_fetcher.py/mt5_data_fetcher.py already use for
    # their own write_provenance_marker() calls.
    #
    # ROLLING WINDOW, NOT A FIXED DATE: BinanceKlinesProvider wraps
    # data_fetcher.get_klines_by_date() (start/end), not get_klines()
    # (most-recent-N-candles) — a genuinely different function shape. To
    # preserve the exact "self-correcting forever" property this file's
    # own HISTORY_CANDLES comment requires (the real bug this script was
    # fixed for was a FIXED historical start date silently falling further
    # and further behind "now" every year), the (start, end) window is
    # computed FRESH from `now` every run via the existing
    # candles_to_wall_clock() conversion, never a stored/hardcoded date —
    # functionally equivalent to "the most recent HISTORY_CANDLES candles,"
    # expressed as a real date range instead of a candle count.
    from bot.binance_klines_provider import BinanceKlinesProvider
    from bot.instruments import candles_to_wall_clock, get_instrument

    span_hours, _unit = candles_to_wall_clock(HISTORY_CANDLES, INTERVAL)
    start = now - timedelta(hours=span_hours)
    provider = BinanceKlinesProvider()

    print('\n' + '=' * 80)
    print(f'DOWNLOADING DATA FOR {len(SYMBOLS)} SYMBOLS')
    print(f'History : most recent {HISTORY_CANDLES:,} candles ({HISTORY_YEARS} years) as of {now.isoformat()}')
    print(f'Window  : {start.isoformat()} -> {now.isoformat()}')
    print(f'Interval: {INTERVAL}')
    print(f'Fetcher : BinanceKlinesProvider (bot/data_fetcher.py via the MarketDataProvider contract)')
    print('=' * 80)

    summary = {}

    for symbol in SYMBOLS:
        print(f'\n[Downloading] {symbol}')
        print(f'  Fetching {start.date()} -> {now.date()} ...')

        try:
            # ── Fetch via BinanceKlinesProvider ──────────────────────────────
            # claude code changed: was get_klines(symbol=symbol, interval=INTERVAL,
            # total_candles=HISTORY_CANDLES, use_cache=True, cache_ttl_seconds=3600)
            # — the direct data_fetcher call. Every symbol in SYMBOLS is
            # derived from THIS file's own SYMBOLS by bot.instruments'
            # _build_crypto_registry(), so get_instrument(symbol) is
            # guaranteed to resolve here — never None for a symbol reached
            # by this loop.
            instrument = get_instrument(symbol)
            bars = provider.get_historical_bars(instrument, INTERVAL, start, now)
            df_raw = bars.data

            if df_raw is None or df_raw.empty:
                raise ValueError('BinanceKlinesProvider returned empty DataFrame')

            # ── Prepare for CSV / downstream modules ─────────────────────────
            df_clean = prepare_dataframe_for_csv(df_raw, symbol)

            if df_clean.empty:
                raise ValueError('DataFrame empty after cleaning')

            # ── Save to CSV ──────────────────────────────────────────────────
            filename = symbol_to_filename(symbol)
            filepath = OUTPUT_DIR / filename
            df_clean.to_csv(filepath, index=False)

            candle_count = len(df_clean)
            last_timestamp = pd.Timestamp(df_clean['timestamp'].iloc[-1])
            if last_timestamp.tzinfo is None:
                last_timestamp = last_timestamp.tz_localize('UTC')
            date_range = f"{df_clean['timestamp'].iloc[0]} → {df_clean['timestamp'].iloc[-1]}"

            # claude code changed: Data-Layer Audit Step 5 — records a real
            # dataset fingerprint alongside this CSV, the crypto-side half
            # of the "shared, not Forex-specific" gap both existing Forex
            # audit reports flagged (Phase 11 / the Multi-Asset Integration
            # report's own note: "this mirrors the exact same gap in the
            # pre-existing crypto run_cross_section_research()"). No
            # venue-attribution bug exists here the way Yahoo-vs-MT5 did
            # (fetch_all_symbols.py is the only crypto acquisition pipeline
            # in this codebase), so bot/instruments.py's
            # _build_crypto_registry() is intentionally left reading its
            # existing hardcoded venue="binance" — this write is purely
            # additive provenance, not a correctness fix. Lazy import: same
            # reasoning as forex_data_fetcher.py/mt5_data_fetcher.py's own
            # equivalent comment — bot.instruments imports this module at
            # top level.
            from bot.instruments import write_provenance_marker
            write_provenance_marker(
                filepath, venue="binance", data_source="fetch_all_symbols",
                source="binance_spot_klines", symbol=symbol, timeframe=INTERVAL,
                start_date=str(df_clean["timestamp"].iloc[0].date()), end_date=str(df_clean["timestamp"].iloc[-1].date()),
                row_count=candle_count,
            )

            # claude code changed: new — Step 3's explicit freshness check.
            # Logs a clear, named warning rather than silently proceeding if
            # a symbol's data is staler than FRESHNESS_MAX_AGE_DAYS — this is
            # exactly the condition that went unnoticed for months under the
            # old forward-pagination bug.
            staleness_days = (now - last_timestamp).total_seconds() / 86400
            is_fresh = staleness_days <= FRESHNESS_MAX_AGE_DAYS
            if not is_fresh:
                warning_msg = (
                    f"{symbol}: data is STALE — most recent candle is "
                    f"{last_timestamp.isoformat()} ({staleness_days:.1f} days old), "
                    f"exceeding the {FRESHNESS_MAX_AGE_DAYS}-day freshness threshold"
                )
                logger.warning(warning_msg)
                print(f'  ⚠ STALE       : {warning_msg}')

            print(f'  ✓ Downloaded  : {candle_count:,} candles')
            print(f'  ✓ Date range  : {date_range}')
            print(f'  ✓ Saved to    : {filepath}')

            summary[symbol] = {
                'success': True,
                'candles': candle_count,
                'file': str(filepath),
                'error': None,
                'last_timestamp': last_timestamp.isoformat(),
                'staleness_days': round(staleness_days, 2),
                'is_fresh': is_fresh,
            }

        except Exception as e:
            print(f'  ✗ Error: {e}')
            summary[symbol] = {
                'success': False,
                'candles': 0,
                'file': None,
                'error': str(e),
                'last_timestamp': None,
                'staleness_days': None,
                'is_fresh': False,
            }

    # ── Print final summary ──────────────────────────────────────────────────
    print('\n' + '=' * 80)
    print('DOWNLOAD COMPLETE')
    print('=' * 80)

    success_count = sum(1 for v in summary.values() if v['success'])
    print(f'\n{success_count}/{len(SYMBOLS)} symbols downloaded successfully\n')

    for symbol, info in summary.items():
        if info['success']:
            print(f'  ✓  {symbol:12}  {info["candles"]:>7,} candles  →  {info["file"]}')
        else:
            print(f'  ✗  {symbol:12}  FAILED: {info["error"]}')

    print()
    return summary


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    download_all_symbols()