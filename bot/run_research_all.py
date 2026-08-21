# run_research_all.py
#
# Runs the full research pipeline for ALL 20 symbols simultaneously.
# claude code changed: was "ALL 7 symbols" — stale relative to the actual
# SYMBOLS list below, which has held 20 entries for a while; fixed while
# touching this file for Phase B (P1-2) so the family-size documentation
# added below doesn't contradict the file's own header.
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
from bot.research.feature_validator import FeatureValidator, apply_family_wide_correction
from bot.research.feature_stability_analyzer import FeatureStabilityAnalyzer   # claude code changed: new — Phase B (P1-4)

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


# ── Temporal stability gate ────────────────────────────────────────────────────
# claude code changed: new — Phase B remediation (forensic-audit finding
# P1-4). Applied AFTER family-wide multiple-testing correction, in
# __main__, to every symbol's corrected results.
#
# RULE (documented, not arbitrary): a feature that reached STRONG KEEP or
# KEEP from feature_validator.py's significance/economic tests is
# DOWNGRADED to REVIEW unless feature_stability_analyzer.py's OWN summary
# for that feature shows BOTH:
#   1. trend is not DECAYING or DEAD — a feature whose predictive power is
#      fading or gone in the most recent data is exactly the "did it ever
#      work" vs "does it still work" gap P1-4 is about; passing a
#      significance test computed over the ENTIRE history says nothing
#      about whether the effect survived to the present.
#   2. live_recommendation is TRADE NOW or WATCH, not RESEARCH or AVOID —
#      reuses feature_stability_analyzer.py's OWN already-documented
#      IC_STRONG/IC_MODERATE/IC_WEAK thresholds (see that module's
#      CONFIGURATION section) rather than inventing new ones here, per the
#      remediation brief's instruction that thresholds be "documented and
#      justified", not picked freshly for this specific gate.
# A feature with NO stability summary row at all (analysis failed, or
# insufficient data for even one full year) is likewise downgraded —
# missing evidence is not approval (Rule 4).
#
# This only ever downgrades (STRONG KEEP/KEEP -> REVIEW); it never
# upgrades a feature significance/economic testing already rejected.
# ────────────────────────────────────────────────────────────────────────────

def _apply_stability_gate(corrected_df: pd.DataFrame, stability_summary: pd.DataFrame) -> pd.DataFrame:
    corrected_df = corrected_df.copy()
    corrected_df['stability_gate_passed'] = False
    corrected_df['stability_gate_reason'] = ''

    stability_by_feature = {}
    if stability_summary is not None and not stability_summary.empty:
        for _, row in stability_summary.iterrows():
            stability_by_feature[row['feature']] = row

    for idx, row in corrected_df.iterrows():
        if row['recommendation'] not in ('STRONG KEEP', 'KEEP'):
            continue   # gate only matters for features currently trying to pass

        stab_row = stability_by_feature.get(row['feature'])

        if stab_row is None:
            corrected_df.at[idx, 'recommendation'] = 'REVIEW'
            corrected_df.at[idx, 'confidence_level'] = 50.0
            corrected_df.at[idx, 'stability_gate_reason'] = 'no stability evidence available (missing != approved)'
            continue

        trend = stab_row.get('trend', 'UNKNOWN')
        live_rec = stab_row.get('live_recommendation', 'AVOID')

        if trend in ('DECAYING', 'DEAD'):
            corrected_df.at[idx, 'recommendation'] = 'REVIEW'
            corrected_df.at[idx, 'confidence_level'] = 50.0
            corrected_df.at[idx, 'stability_gate_reason'] = f'temporal trend={trend}'
        elif live_rec not in ('TRADE NOW', 'WATCH'):
            corrected_df.at[idx, 'recommendation'] = 'REVIEW'
            corrected_df.at[idx, 'confidence_level'] = 50.0
            corrected_df.at[idx, 'stability_gate_reason'] = f'stability live_recommendation={live_rec}'
        else:
            corrected_df.at[idx, 'stability_gate_passed'] = True

    return corrected_df


# ── Per-symbol worker ─────────────────────────────────────────────────────────

def process_single_symbol(symbol: str) -> dict:
    """
    Per-symbol research pipeline — the PARALLEL half only.
    Called in parallel for all symbols via multiprocessing.Pool.

    Steps:
        1. Read CSV
        2. Calculate 35+ features
        3. Save observations
        4. Validate features — RAW pass only, no correction applied

    claude code changed: was 5 steps, the last of which
    (validate_all_features_institutional() + save
    {symbol}_validated_features.csv) applied multiple-testing correction
    scoped to only THIS symbol's ~16-19 features — the specific bug
    forensic-audit finding P1-2 flagged (20 independent corrections, each
    blind to the other 19 symbols' features). Correction now happens ONCE,
    after every symbol's raw results are collected, in __main__ below —
    see apply_family_wide_correction()'s docstring in feature_validator.py
    for the full reasoning. This function now stops after producing the
    RAW (uncorrected) per-feature results; saving
    {symbol}_validated_features.csv and printing pass/fail counts moved to
    __main__, since both need the family-wide-corrected numbers, not the
    provisional per-symbol ones.
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

        # ── STEP 4: Validate features (RAW — no correction yet) ────────────────
        # FIX 1: was validator.validate_all_features() — method does not exist
        # Correct method: validate_all_features_raw(df, forward_return_col)
        #
        # FIX 2: was 'foward_return_4h' (typo) — correct column is 'forward_return_4h'
        print(f'\n[{symbol}] STEP 4: Validate features (raw pass)')
        validator = FeatureValidator(min_observations=30, alpha=0.05)
        raw_results_df = validator.validate_all_features_raw(
            df_features,
            forward_return_col='forward_return_4h',   # FIX: typo corrected
        )
        print(f'  ✓ Tested   : {len(raw_results_df)} features (uncorrected — family-wide correction runs after all symbols finish)')

        # ── STEP 5: Temporal stability analysis ─────────────────────────────────
        # claude code changed: new — Phase B remediation (forensic-audit
        # finding P1-4). feature_stability_analyzer.py existed but nothing
        # in this orchestrator ever called it — a feature could reach
        # KEEP/STRONG KEEP via feature_validator.py's significance test
        # alone, with its temporal stability never actually checked despite
        # this module existing specifically to check it. Runs here
        # (parallel-safe, independent per symbol) so its summary is ready
        # for the gate applied after family-wide correction in __main__.
        print(f'\n[{symbol}] STEP 5: Temporal stability analysis')
        stability_analyzer = FeatureStabilityAnalyzer(forward_col='forward_return_4h')
        stability_result = stability_analyzer.analyze(df_features, symbol)
        stability_summary = stability_result.get('summary', pd.DataFrame())
        print(f'  ✓ Stability summary: {len(stability_summary)} features assessed')

        print(f'\n[{symbol}] ✓ RAW PASS COMPLETE — family-wide correction + stability gate pending')

        return {
            'symbol'            : symbol,
            'success'           : True,
            'candles'           : len(df),
            'raw_results_df'    : raw_results_df,
            'stability_summary' : stability_summary,
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
    print(f'RUNNING RESEARCH FOR {len(SYMBOLS)} SYMBOLS SIMULTANEOUSLY')
    print('=' * 80)
    print(f'\nSymbols : {", ".join(SYMBOLS)}')
    print(f'Running all {len(SYMBOLS)} in parallel (one CPU core per symbol)')

    # Ensure output folder exists
    Path('research_data').mkdir(exist_ok=True)

    # ── Run all symbols in parallel (raw pass only — no correction yet) ──────
    with Pool(processes=min(20, os.cpu_count())) as pool:
        results = pool.map(process_single_symbol, SYMBOLS)

    # ── Family-wide multiple-testing correction ───────────────────────────────
    # claude code changed: new — Phase B (P1-2). Runs ONCE, sequentially, in
    # the main process, across every symbol's raw results together — this
    # is the actual fix. See apply_family_wide_correction()'s docstring in
    # feature_validator.py for the full reasoning behind this scope.
    print('\n' + '=' * 80)
    print('FAMILY-WIDE MULTIPLE-TESTING CORRECTION')
    print('=' * 80)

    raw_by_symbol = {
        r['symbol']: r['raw_results_df'] for r in results if r['success']
    }
    per_symbol_corrected, pooled_corrected = apply_family_wide_correction(
        raw_by_symbol, alpha=0.05
    )

    # Save the pooled file — the auditable record of exactly what the
    # family-wide correction was actually run across.
    pooled_path = 'research_data/all_symbols_validated_features_pooled.csv'
    pooled_corrected.to_csv(pooled_path, index=False)
    print(f'  ✓ Saved pooled (family-wide, corrected) results to: {pooled_path}')
    print(f'  ✓ Family size: {len(pooled_corrected)} (symbol, feature) pairs across {len(raw_by_symbol)} symbols')

    # ── Apply the temporal stability gate ─────────────────────────────────────
    # claude code changed: new — Phase B (P1-4). See _apply_stability_gate()
    # for the documented rule. Runs after family-wide correction so it's
    # gating the FINAL recommendation, not a provisional one.
    stability_by_symbol = {
        r['symbol']: r['stability_summary'] for r in results if r['success']
    }
    gated_count = 0
    for symbol in list(per_symbol_corrected.keys()):
        before = per_symbol_corrected[symbol]['recommendation'].isin(['STRONG KEEP', 'KEEP']).sum()
        per_symbol_corrected[symbol] = _apply_stability_gate(
            per_symbol_corrected[symbol], stability_by_symbol.get(symbol, pd.DataFrame())
        )
        after = per_symbol_corrected[symbol]['recommendation'].isin(['STRONG KEEP', 'KEEP']).sum()
        gated_count += (before - after)
    print(f'  ✓ Temporal stability gate: {gated_count} feature(s) downgraded to REVIEW (failed significance survived, stability did not)')

    # ── Save per-symbol validated_features.csv (now family-corrected + stability-gated) ────────
    summaries = {}
    for symbol, corrected_df in per_symbol_corrected.items():
        val_file = f'research_data/{symbol}_validated_features.csv'
        corrected_df.to_csv(val_file, index=False)

        strong_keep = int((corrected_df['recommendation'] == 'STRONG KEEP').sum())
        keep        = int((corrected_df['recommendation'] == 'KEEP').sum())
        review      = int((corrected_df['recommendation'] == 'REVIEW').sum())
        delete      = int((corrected_df['recommendation'] == 'DELETE').sum())
        summaries[symbol] = {
            'features_tested': len(corrected_df),
            'strong_keep': strong_keep, 'keep': keep,
            'review': review, 'delete': delete,
        }

        passing = corrected_df[corrected_df['recommendation'].isin(['STRONG KEEP', 'KEEP'])]
        if len(passing) > 0:
            print(f'\n[{symbol}] Features that passed family-wide correction ({len(passing)}):')
            for _, row in passing.iterrows():
                print(f'    [{row["recommendation"]:11}] {row["feature"]}  IC={row["ic_overall"]:+.4f}')

    # ── Final summary ─────────────────────────────────────────────────────────
    print('\n' + '=' * 80)
    print('FINAL SUMMARY')
    print('=' * 80)

    success_count = sum(1 for r in results if r['success'])
    print(f'\n{success_count}/{len(SYMBOLS)} symbols completed successfully\n')

    for result in results:
        symbol = result['symbol']
        if result['success']:
            s = summaries[symbol]
            print(
                f'  ✓  {symbol:12}  '
                f'{result["candles"]:>7,} candles  |  '
                f'Tested: {s["features_tested"]}  |  '
                f'STRONG KEEP: {s["strong_keep"]}  '
                f'KEEP: {s["keep"]}  '
                f'REVIEW: {s["review"]}  '
                f'DELETE: {s["delete"]}'
            )
        else:
            print(f'  ✗  {symbol:12}  FAILED: {result["error"]}')

    print('\n' + '=' * 80)
    print('ALL COMPLETE')
    print('=' * 80)
    print('\nFiles saved to research_data/:')
    for symbol in SYMBOLS:
        print(f'  ✓  research_data/{symbol}_observations.csv')
        print(f'  ✓  research_data/{symbol}_validated_features.csv (family-wide corrected)')
    print(f'  ✓  {pooled_path}')
    print()