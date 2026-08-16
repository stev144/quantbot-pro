# fetch_all_symbols.py
#
# Downloads OHLCV data for all 7 symbols using the project's data_fetcher module.
#
# What this does vs the old version:
#   OLD: ccxt, no pagination, ~500-1000 candles max, no cleaning, no normalisation
#   NEW: project data_fetcher, full pagination, 2020→today (~45,000 candles),
#        deduplication, NaN removal, rate-limit handling, caching, symbol normalisation
#
# Output: data/{SYMBOL}_1h.csv  (same filenames as before — nothing downstream breaks)

import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

# ── Use the project data fetcher, not ccxt ──────────────────────────────────
from bot.data_fetcher import get_klines_by_date

# ── Configuration ────────────────────────────────────────────────────────────

# Symbols in ccxt-style format — data_fetcher normalises these automatically
# BTC/USDT  →  _normalize_symbol()  →  BTCUSDT  (Binance format)

SYMBOLS = [
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

INTERVAL        = '1h'
START_DATE      = datetime(2020, 1, 1, tzinfo=timezone.utc)   # Full history from 2020
END_DATE        = datetime.now(tz=timezone.utc)               # Up to right now
MAX_CANDLES     = 50_000   # Safety ceiling — data_fetcher will paginate automatically
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
    keep = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
    existing = [c for c in keep if c in result.columns]
    result = result[existing].copy()

    # Ensure numeric types (data_fetcher already does this but be defensive)
    for col in ['open', 'high', 'low', 'close', 'volume']:
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

    print('\n' + '=' * 80)
    print('DOWNLOADING DATA FOR 7 SYMBOLS')
    print(f'Period  : {START_DATE.date()} → {END_DATE.date()}')
    print(f'Interval: {INTERVAL}')
    print(f'Fetcher : project data_fetcher (paginated, cleaned, normalised)')
    print('=' * 80)

    summary = {}

    for symbol in SYMBOLS:
        print(f'\n[Downloading] {symbol}')
        print(f'  Fetching {START_DATE.date()} → {END_DATE.date()} ...')

        try:
            # ── Fetch via project data_fetcher ───────────────────────────────
            # get_klines_by_date handles:
            #   • Symbol normalisation  (BTC/USDT → BTCUSDT)
            #   • Pagination            (loops until all candles retrieved)
            #   • Rate-limit handling   (429 backoff, weight monitoring)
            #   • Deduplication         (seen_timestamps set)
            #   • NaN removal           (_build_ohlcv_dataframe)
            #   • Sorting               (chronological order)
            #   • Caching               (avoids re-downloading unchanged data)
            df_raw = get_klines_by_date(
                symbol=symbol,           # data_fetcher normalises this automatically
                interval=INTERVAL,
                start=START_DATE,
                end=END_DATE,
                max_candles=MAX_CANDLES,
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
            date_range   = f"{df_clean['timestamp'].iloc[0]} → {df_clean['timestamp'].iloc[-1]}"

            print(f'  ✓ Downloaded  : {candle_count:,} candles')
            print(f'  ✓ Date range  : {date_range}')
            print(f'  ✓ Saved to    : {filepath}')

            summary[symbol] = {
                'success': True,
                'candles': candle_count,
                'file': str(filepath),
                'error': None,
            }

        except Exception as e:
            print(f'  ✗ Error: {e}')
            summary[symbol] = {
                'success': False,
                'candles': 0,
                'file': None,
                'error': str(e),
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