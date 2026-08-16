# run_research_all.py
#
# Runs the full research pipeline for ALL 7 symbols simultaneously.
#
# Fixes applied vs original:
#   1. calculator.calculate_features()       → calculator.calculate_all_features()
#   2. validator.validate_all_features()     → validator.validate_all_features_institutional()
#   3. 'foward_return_4h' typo              → 'forward_return_4h'  (column now matches calculator output)
#   4. CSV timestamp column handling        → reset_index() when data_fetcher returns DatetimeIndex
#   5. Symbol passed to feature calculator  → symbol name passed correctly for logging
#   6. Validated features count             → reads from 'feature' column, not broken list comp

import os
import pandas as pd
from pathlib import Path
from multiprocessing import Pool

from bot.research.feature_calculator import FeatureCalculator
from bot.research.feature_validator import FeatureValidator

# ── Symbols ───────────────────────────────────────────────────────────────────

SYMBOLS = [
    'BTC_USDT',
    'ETH_USDT',
    'BNB_USDT',
    'SOL_USDT',
    'ADA_USDT',
    'AVAX_USDT',
    'DOT_USDT',
    'MATIC_USDT',
    'ARB_USDT',
    'LINK_USDT',
    'UNI_USDT',
    'AAVE_USDT',
    'XRP_USDT',
    'XLM_USDT',
    'DOGE_USDT',
    'SHIB_USDT',
    'ATOM_USDT',
    'FIL_USDT',
    'APT_USDT',
    'OP_USDT',
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_csv(symbol: str) -> pd.DataFrame:
    """
    Load symbol CSV and return a clean DataFrame ready for the feature calculator.

    The feature calculator expects:
      - Columns: open, high, low, close, volume
      - Either a DatetimeIndex OR a plain 'timestamp' column

    fetch_all_symbols.py saves wth a plain 'timestamp' column, so we set it as
    the index here to match what data_fetcher would return natively.
    """
    csv_file = f'data/{symbol}_1h.csv'
    df = pd.read_csv(csv_file)

    # Parse timestamp and set as index (feature calculator is happy either way,
    # but setting it avoids ambiguity in stability window filtering)
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        df.set_index('timestamp', inplace=True)

    # Ensure numeric OHLCV columns
    for col in ['open', 'high', 'low', 'close', 'volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df.dropna(subset=['open', 'high', 'low', 'close', 'volume'], inplace=True)
    df.sort_index(inplace=True)

    return df


# ── Per-symbol worker ─────────────────────────────────────────────────────────

def process_single_symbol(symbol: str) -> dict:
    """
    Full research pipeline for ONE symbol.
    Called in parallel for all 7 symbols.

    Steps:
        1. Read CSV
        2. Calculate 35+ features
        3. Save observations
        4. Validate features (institutional pipeline)
        5. Save validation results
    """

    print(f'\n{"="*80}')
    print(f'PROCESSING {symbol}')
    print(f'{"="*80}')

    try:
        # ── STEP 1: Read CSV ──────────────────────────────────────────────────
        print(f'\n[{symbol}] STEP 1: Read CSV')
        df = _load_csv(symbol)
        print(f'  ✓ Loaded {len(df):,} candles')

        if len(df) < 200:
            raise ValueError(
                f'Only {len(df)} candles — run fetch_all_symbols.py first to download full history'
            )

        # ── STEP 2: Calculate features ────────────────────────────────────────
        # FIX: was calculator.calculate_features() — method does not exist
        # Correct method: calculate_all_features(df, symbol, timeframe)
        print(f'\n[{symbol}] STEP 2: Calculate features')
        calculator = FeatureCalculator(min_data_required=100)
        df_features = calculator.calculate_all_features(
            df,
            symbol=symbol,
            timeframe='1h',
        )
        feature_cols = [
            c for c in df_features.columns
            if c not in ['open', 'high', 'low', 'close', 'volume']
        ]
        print(f'  ✓ Added {len(feature_cols)} feature columns (total {len(df_features.columns)} columns)')

        # ── STEP 3: Save observations CSV ─────────────────────────────────────
        print(f'\n[{symbol}] STEP 3: Save to observations.csv')
        obs_file = f'research_data/{symbol}_observations.csv'

        # Reset index so timestamp is saved as a plain column
        df_features.reset_index().to_csv(obs_file, index=False)
        print(f'  ✓ Saved to: {obs_file}')

        # ── STEP 4: Validate features ─────────────────────────────────────────
        # FIX 1: was validator.validate_all_features() — method does not exist
        # Correct method: validate_all_features_institutional(df, forward_return_col)
        #
        # FIX 2: was 'foward_return_4h' (typo) — correct column is 'forward_return_4h'
        print(f'\n[{symbol}] STEP 4: Validate features')
        validator = FeatureValidator(min_observations=30, alpha=0.05)
        results_df = validator.validate_all_features_institutional(
            df_features,
            forward_return_col='forward_return_4h',   # FIX: typo corrected
        )

        # Count results
        # FIX: was reading from a list comp on results (which was a DataFrame)
        # Now reads 'feature' column from the returned DataFrame correctly
        total_tested   = len(results_df)
        strong_keep    = (results_df['recommendation'] == 'STRONG KEEP').sum()
        keep           = (results_df['recommendation'] == 'KEEP').sum()
        review         = (results_df['recommendation'] == 'REVIEW').sum()
        delete         = (results_df['recommendation'] == 'DELETE').sum()

        print(f'  ✓ Tested   : {total_tested} features')
        print(f'  ✓ STRONG KEEP: {strong_keep}')
        print(f'  ✓ KEEP       : {keep}')
        print(f'  ⚠ REVIEW     : {review}')
        print(f'  ✗ DELETE     : {delete}')

        # ── STEP 5: Save validation results ───────────────────────────────────
        print(f'\n[{symbol}] STEP 5: Save validation results')
        val_file = f'research_data/{symbol}_validated_features.csv'
        results_df.to_csv(val_file, index=False)
        print(f'  ✓ Saved to: {val_file}')

        # Print features that passed (STRONG KEEP + KEEP)
        passing = results_df[results_df['recommendation'].isin(['STRONG KEEP', 'KEEP'])]
        print(f'\n[{symbol}] Features that passed ({len(passing)}):')
        for _, row in passing.iterrows():
            print(f'    [{row["recommendation"]:11}] {row["feature"]}  IC={row["ic_overall"]:+.4f}')

        print(f'\n[{symbol}] ✓ COMPLETE!')

        return {
            'symbol'                  : symbol,
            'success'                 : True,
            'candles'                 : len(df),
            'features_tested'         : total_tested,
            'strong_keep'             : int(strong_keep),
            'keep'                    : int(keep),
            'review'                  : int(review),
            'delete'                  : int(delete),
        }

    except Exception as e:
        print(f'\n[{symbol}] ✗ ERROR: {e}')
        return {
            'symbol' : symbol,
            'success': False,
            'error'  : str(e),
        }


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':

    print('\n' + '=' * 80)
    print('RUNNING RESEARCH FOR 7 SYMBOLS SIMULTANEOUSLY')
    print('=' * 80)
    print(f'\nSymbols : {", ".join(SYMBOLS)}')
    print(f'Running all 7 in parallel (one CPU core per symbol)')

    # Ensure output folder exists
    Path('research_data').mkdir(exist_ok=True)

    # ── Run all symbols in parallel ───────────────────────────────────────────
    with Pool(processes=min(20, os.cpu_count())) as pool:
        results = pool.map(process_single_symbol, SYMBOLS)

    # ── Final summary ─────────────────────────────────────────────────────────
    print('\n' + '=' * 80)
    print('FINAL SUMMARY')
    print('=' * 80)

    success_count = sum(1 for r in results if r['success'])
    print(f'\n{success_count}/{len(SYMBOLS)} symbols completed successfully\n')

    for result in results:
        if result['success']:
            print(
                f'  ✓  {result["symbol"]:12}  '
                f'{result["candles"]:>7,} candles  |  '
                f'Tested: {result["features_tested"]}  |  '
                f'STRONG KEEP: {result["strong_keep"]}  '
                f'KEEP: {result["keep"]}  '
                f'REVIEW: {result["review"]}  '
                f'DELETE: {result["delete"]}'
            )
        else:
            print(f'  ✗  {result["symbol"]:12}  FAILED: {result["error"]}')

    print('\n' + '=' * 80)
    print('ALL COMPLETE')
    print('=' * 80)
    print('\nFiles saved to research_data/:')
    for symbol in SYMBOLS:
        print(f'  ✓  research_data/{symbol}_observations.csv')
        print(f'  ✓  research_data/{symbol}_validated_features.csv')
    print()