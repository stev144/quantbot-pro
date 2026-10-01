"""
Phase 2D ledger retrofit — Hardening Phase 2, next-mission item 1.

# claude code changed: new file. Phase 2D (trade-flow, 180d BTC / 113d
# partial ETH) completed and was reported to the user as closed-negative,
# but was never recorded in ResearchExperiment — exactly the "we tested
# it and forgot it" risk the Research Trial Ledger exists to prevent.
#
# Re-inspecting the original run while retrofitting (Hardening Mission
# Rule A/B — inspect before recording, re-verify before claiming) found a
# real, previously-undisclosed methodology imprecision: phase2d_analyze.py
# built its FDR correction pool from EVERY numeric column
# FeatureCalculator/DerivativesEngine happened to compute (32 features x
# 2 symbols = 64 pairs/horizon), not from the 5 pre-declared trade-flow
# candidates alone (delta, cvd_slope_24h, buy_sell_ratio, trade_intensity,
# avg_trade_size) that were the actual, stated hypothesis family. This
# script does NOT re-run any statistical test — the raw, per-feature
# block-permutation p-values in phase2d_cache/phase2d_validation_results.csv
# are untouched, immutable facts already computed once. It re-applies
# apply_family_wide_correction() (unchanged) to exactly the pre-declared
# 5-feature subset of those SAME raw p-values, and reports both the
# originally-run (wide-pool) and the correctly-scoped result side by
# side — fixing a family-SCOPE bug, not the methodology itself.
"""
import sys, os, json
sys.path.insert(0, ".")
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

import pandas as pd

from bot.research.feature_validator import FeatureValidator, apply_family_wide_correction
from bot.research_lab.data_fingerprint import DatasetIdentity
from bot.research_lab.trial_service import freeze_family_before_testing, record_research_trial

DECLARED_FAMILY = ["delta", "cvd_slope_24h", "buy_sell_ratio", "trade_intensity", "avg_trade_size"]
SYMBOLS = ["BTC/USDT", "ETH/USDT"]
HORIZONS = ["forward_return_1h", "forward_return_4h", "forward_return_12h"]


def main():
    original = pd.read_csv("phase2d_cache/phase2d_validation_results.csv")
    original_family_only = original[original["feature"].isin(DECLARED_FAMILY)].copy()

    print(f"Original run's actual correction pool: {original['feature'].nunique()} features "
          f"x {len(SYMBOLS)} symbols = {original['feature'].nunique() * len(SYMBOLS)} pairs/horizon "
          f"(wider than the {len(DECLARED_FAMILY)}-feature declared family).", flush=True)

    # ── Re-apply the correction to EXACTLY the declared family, from the
    # SAME already-computed raw p-values — no test re-run. ──────────────
    rescoped_rows = []
    for horizon in HORIZONS:
        raw_by_symbol = {}
        for symbol in SYMBOLS:
            subset = original_family_only[
                (original_family_only["symbol"] == symbol) & (original_family_only["horizon"] == horizon)
            ].copy()
            # claude code changed: apply_family_wide_correction() inserts
            # its OWN 'symbol' column (matching validate_all_features_raw()'s
            # un-tagged single-symbol output shape) — drop the columns this
            # already-pooled CSV added so the reconstructed input matches
            # that expected shape exactly.
            subset = subset.drop(columns=["symbol", "horizon"])
            raw_by_symbol[symbol] = subset
        per_symbol, pooled = apply_family_wide_correction(raw_by_symbol, alpha=0.05)
        pooled["horizon"] = horizon
        rescoped_rows.append(pooled)

    rescoped = pd.concat(rescoped_rows, ignore_index=True)
    rescoped.to_csv("phase2d_cache/phase2d_rescoped_family_results.csv", index=False)

    print("\n--- CORRECTLY-SCOPED (5-feature declared family) RESULTS ---")
    print(rescoped[["symbol", "feature", "horizon", "ic_overall", "p_value_block_permutation", "p_fdr", "passes_fdr"]].to_string(index=False))

    n_rescoped_family = len(rescoped)
    n_rescoped_pass = int(rescoped["passes_fdr"].sum())
    print(f"\nCORRECTLY-SCOPED FAMILY TOTAL: {n_rescoped_pass}/{n_rescoped_family} pass FDR at alpha=0.05.")

    n_original_family_pass = int(original_family_only["passes_fdr"].sum())
    print(f"ORIGINAL WIDE-POOL correction (as actually run): {n_original_family_pass}/{len(original_family_only)} "
          f"of the 5 declared features passed (corrected against the wider 64-pair pool).")

    flips = []
    for _, row in rescoped.iterrows():
        orig_row = original_family_only[
            (original_family_only["symbol"] == row["symbol"]) & (original_family_only["feature"] == row["feature"])
            & (original_family_only["horizon"] == row["horizon"])
        ]
        if not orig_row.empty and bool(orig_row["passes_fdr"].iloc[0]) != bool(row["passes_fdr"]):
            flips.append((row["symbol"], row["feature"], row["horizon"], bool(orig_row["passes_fdr"].iloc[0]), bool(row["passes_fdr"])))
    print(f"\nDECISIONS THAT FLIPPED between wide-pool and correctly-scoped correction: {len(flips)}")
    for f in flips:
        print(f"  {f}")

    verdict = "SUPPORTED" if n_rescoped_pass > 0 else "REJECTED"
    print(f"\nFINAL, CORRECTLY-SCOPED VERDICT: {verdict}")

    # ── Ledger the retrofit ──────────────────────────────────────────────
    hypothesis_family = freeze_family_before_testing(
        name="phase2d-tradeflow", feature_family=DECLARED_FAMILY, assets=SYMBOLS,
        venue="binance", timeframe="1h", horizons=HORIZONS,
    )
    print(f"\nHYPOTHESIS FAMILY FROZEN: id={hypothesis_family.pk} n_hypotheses={hypothesis_family.n_hypotheses}", flush=True)

    dataset_identities = [
        DatasetIdentity(source="binance_futures_aggTrades_archive", symbol="BTC/USDT", venue="binance",
                         timeframe="1h", start_date="2026-02-25", end_date="2026-08-23", row_count=4320),
        DatasetIdentity(source="binance_futures_aggTrades_archive", symbol="ETH/USDT", venue="binance",
                         timeframe="1h", start_date="2026-02-25", end_date="2026-08-23", row_count=2712),
    ]

    import subprocess
    try:
        code_version = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=5).decode().strip()
    except Exception:
        code_version = "unknown"

    experiment = record_research_trial(
        hypothesis_family=hypothesis_family,
        hypothesis_text=(
            "Does candle-aligned signed trade-flow (delta, cvd_slope_24h, buy_sell_ratio, "
            "trade_intensity, avg_trade_size, from Binance aggTrades) predict BTC/USDT and "
            "ETH/USDT forward returns (1h/4h/12h) beyond OHLCV?"
        ),
        dataset_identities=dataset_identities,
        statistical_results={
            "retrofit_note": (
                "RETROFITTED into the ledger after the fact (Hardening Phase 2). Original run's "
                "FDR correction pool was WIDER than this declared family (32 features x 2 symbols "
                "= 64 pairs/horizon, not the 5 declared trade-flow candidates) — a real family-scope "
                "imprecision found while retrofitting, not a new test. The correctly-scoped "
                "correction (rescoped_family_results, from the SAME already-computed raw "
                "block-permutation p-values, no test re-run) is the authoritative result below."
            ),
            "rescoped_family_results": rescoped.to_dict(orient="records"),
            "original_wide_pool_correction_pool_size_per_horizon": int(original["feature"].nunique() * len(SYMBOLS)),
            "decisions_that_flipped_between_wide_pool_and_rescoped": [
                {"symbol": f[0], "feature": f[1], "horizon": f[2], "wide_pool_passed_fdr": f[3], "rescoped_passed_fdr": f[4]}
                for f in flips
            ],
            "n_pass_rescoped": n_rescoped_pass,
            "n_family_rescoped": n_rescoped_family,
            "eth_coverage_pct": 63,
            "eth_coverage_note": "ETH acquisition stopped at 113/180 days by deliberate user instruction, not a data failure — see conversation record.",
            "oos_status": "NOT_REACHED",
            "walk_forward_status": "NOT_REACHED",
            "cost_status": "NOT_JUSTIFIED",
        },
        verdict=verdict,
        code_version=code_version,
        structured_spec={
            "phase": "2D", "engine": "bot.research.trade_flow_engine.TradeFlowEngine",
            "retrofit": True, "original_analysis_script": "phase2d_analyze.py",
        },
    )
    print(f"\nRESEARCH TRIAL RECORDED: ResearchExperiment id={experiment.pk} verdict={experiment.verdict} "
          f"status={experiment.status} hypothesis_family_id={experiment.hypothesis_family_id}", flush=True)


if __name__ == "__main__":
    main()
