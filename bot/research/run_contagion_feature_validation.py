# run_contagion_feature_validation.py
# claude code changed: new file — contagion feature validation runner.
#
# Full institutional validation pass on contagion_engine.py's output — the
# "next step" contagion_engine.py itself prints at the end of every run:
#   validator.validate_all_features_institutional(df=altcoin_df,
#       forward_return_col='forward_return_2h')
#
# Follows run_research_all.py's exact pattern (raw pass in parallel, ONE
# family-wide FDR correction across every symbol afterward) rather than
# calling validate_all_features_institutional() once per symbol as the
# hint text literally says — that would repeat the exact per-symbol-
# correction-blind-to-the-rest-of-the-family bug forensic-audit finding
# P1-2 already fixed for the main feature set (see
# apply_family_wide_correction()'s docstring in feature_validator.py).
# The (symbol, feature) family here is the ~90 contagion CSVs together,
# not each one independently.
#
# Does NOT run FeatureStabilityAnalyzer / the temporal stability gate
# run_research_all.py applies — that analyzer expects columns
# (realized_vol, adx, market_regime-relevant OHLCV features) that
# FeatureCalculator produces and contagion_engine.py does not; regime
# detection on a contagion CSV alone already degrades to 100% "ranging"
# for the same reason (confirmed on a timing run before this script was
# written). Treat this run's output as significance + economic-magnitude
# filtered, NOT stability-gated — a real gap vs. the main pipeline's rigor,
# left open rather than silently faked.

import os
import pandas as pd
from pathlib import Path
from multiprocessing import Pool

from bot.research.feature_validator import FeatureValidator, apply_family_wide_correction
from bot.instruments import ASSET_CLASS_CRYPTO

FORWARD_RETURN_COL = "forward_return_2h"   # claude code changed: new — contagion_engine.py's own "start with 2h" recommendation
CONTAGION_DIR = Path("research_data")   # claude code changed: new
OUTPUT_DIR = Path("research_data/contagion_validation")   # claude code changed: new — separate subdir so this run's output never collides with run_research_all.py's own *_validated_features.csv naming for the same symbols


def _contagion_symbols() -> list:
    return sorted(
        p.name[: -len("_contagion.csv")]
        for p in CONTAGION_DIR.glob("*_contagion.csv")
    )


def process_single_symbol(symbol: str) -> dict:
    try:
        df = pd.read_csv(CONTAGION_DIR / f"{symbol}_contagion.csv")

        validator = FeatureValidator(
            min_observations=30, alpha=0.05,
            timeframe="1h", asset_class=ASSET_CLASS_CRYPTO,
        )
        raw_results_df = validator.validate_all_features_raw(
            df, forward_return_col=FORWARD_RETURN_COL,
        )

        return {
            "symbol": symbol, "success": True,
            "rows": len(df), "raw_results_df": raw_results_df,
        }
    except Exception as e:
        return {"symbol": symbol, "success": False, "error": str(e)}


if __name__ == "__main__":
    symbols = _contagion_symbols()
    print(f"\n{'='*80}\nCONTAGION FEATURE VALIDATION — {len(symbols)} symbols, "
          f"forward_return_col={FORWARD_RETURN_COL}\n{'='*80}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with Pool(processes=min(8, os.cpu_count())) as pool:
        results = pool.map(process_single_symbol, symbols)

    failed = [r for r in results if not r["success"]]
    for r in failed:
        print(f"  ✗ {r['symbol']:14} FAILED: {r['error']}")

    raw_by_symbol = {r["symbol"]: r["raw_results_df"] for r in results if r["success"]}
    print(f"\n{len(raw_by_symbol)}/{len(symbols)} symbols completed raw validation")

    print(f"\n{'='*80}\nFAMILY-WIDE MULTIPLE-TESTING CORRECTION\n{'='*80}")
    per_symbol_corrected, pooled_corrected = apply_family_wide_correction(
        raw_by_symbol, alpha=0.05
    )

    pooled_path = OUTPUT_DIR / "contagion_validated_features_pooled.csv"
    pooled_corrected.to_csv(pooled_path, index=False)
    print(f"  Saved pooled (family-wide corrected) results to: {pooled_path}")
    print(f"  Family size: {len(pooled_corrected)} (symbol, feature) pairs "
          f"across {len(raw_by_symbol)} symbols")

    for symbol, corrected_df in per_symbol_corrected.items():
        corrected_df.to_csv(OUTPUT_DIR / f"{symbol}_contagion_validated.csv", index=False)

    strong_keep = pooled_corrected[pooled_corrected["recommendation"] == "STRONG KEEP"]
    keep = pooled_corrected[pooled_corrected["recommendation"] == "KEEP"]

    print(f"\n{'='*80}\nFINAL SUMMARY\n{'='*80}")
    print(f"  STRONG KEEP: {len(strong_keep)}")
    print(f"  KEEP       : {len(keep)}")
    print(f"  REVIEW     : {(pooled_corrected['recommendation'] == 'REVIEW').sum()}")
    print(f"  DELETE     : {(pooled_corrected['recommendation'] == 'DELETE').sum()}")

    passing = pd.concat([strong_keep, keep]).sort_values("ic_overall", key=abs, ascending=False)
    if len(passing) > 0:
        print(f"\nFeatures surviving family-wide FDR correction + economic significance "
              f"({len(passing)}), ranked by |IC|:")
        for _, row in passing.head(40).iterrows():
            print(f"    [{row['recommendation']:11}] {row['symbol']:14} {row['feature']:30} "
                  f"IC={row['ic_overall']:+.4f}")
    else:
        print("\nNo features survived family-wide correction — the per-symbol quick-screen "
              "ICs did not hold up once corrected against the full ~90-symbol family.")
