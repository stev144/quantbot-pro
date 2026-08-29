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
from bot.data_fetcher import get_klines

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
# HISTORY_CANDLES candles via data_fetcher.get_klines() (which already
# exists and is already used elsewhere in this project, e.g.
# bot/views/dashboard.py) instead of get_klines_by_date()'s forward
# pagination. "Most recent N candles" is self-correcting forever — it
# always means the latest data, regardless of how much time has passed
# since this constant was chosen, with no date arithmetic to revisit.
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
    The data_fetcher returns a DataFrame with:
      - A UTC-aware DatetimeIndex called 'timestamp'
      - Columns: open, high, low, close, volume  (+ extra Binance cols)

    run_research_all.py / feature_calculator.py expect:
      - A plain 'timestamp' column (not the index)
      - Only: timestamp, open, high, low, close, volume

    This function bridges the gap cleanly.
    """
    # Reset index so 'timestamp' becomes a regular column
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

    print('\n' + '=' * 80)
    print(f'DOWNLOADING DATA FOR {len(SYMBOLS)} SYMBOLS')
    print(f'History : most recent {HISTORY_CANDLES:,} candles ({HISTORY_YEARS} years) as of {now.isoformat()}')
    print(f'Interval: {INTERVAL}')
    print(f'Fetcher : project data_fetcher (paginated, cleaned, normalised)')
    print('=' * 80)

    summary = {}

    for symbol in SYMBOLS:
        print(f'\n[Downloading] {symbol}')
        print(f'  Fetching the latest {HISTORY_CANDLES:,} candles ...')

        try:
            # ── Fetch via project data_fetcher ───────────────────────────────
            # claude code changed: was get_klines_by_date(start=START_DATE,
            # end=END_DATE, max_candles=...) — forward pagination from a fixed
            # historical start, which is exactly what caused the staleness
            # bug (see HISTORY_CANDLES's comment above). get_klines() fetches
            # the most recent N candles backward from now instead — already
            # used elsewhere in this project (bot/views/dashboard.py) — and
            # handles the same pagination/rate-limit/dedup/caching concerns.
            df_raw = get_klines(
                symbol=symbol,           # data_fetcher normalises this automatically
                interval=INTERVAL,
                total_candles=HISTORY_CANDLES,
                use_cache=True,          # Cache to avoid re-downloading on re-runs
                cache_ttl_seconds=3600,  # Cache valid for 1 hour
            )

            if df_raw is None or df_raw.empty:
                raise ValueError('data_fetcher returned empty DataFrame')

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