"""
Phase 2E ledger retrofit — Hardening Phase 2, next-mission item 1.

# claude code changed: new file. Unlike Phase 2D, re-inspecting
# phase2e_analyze.py while retrofitting confirmed its correction pool was
# ALREADY exactly the 7 declared derivatives features x 2 symbols = 14
# pairs/horizon (42 total) — no scope bug here. This script records the
# already-computed, already-correctly-scoped results as-is; it does not
# recompute anything.
"""
import sys, os
sys.path.insert(0, ".")
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

import pandas as pd

from bot.research_lab.data_fingerprint import DatasetIdentity
from bot.research_lab.trial_service import freeze_family_before_testing, record_research_trial

DECLARED_FAMILY = [
    "funding_rate", "funding_rate_zscore", "funding_rate_change", "funding_rate_cumulative_24h",
    "oi_change_1h", "oi_change_24h", "oi_price_interaction",
]
SYMBOLS = ["BTC/USDT", "ETH/USDT"]
HORIZONS = ["forward_return_1h", "forward_return_4h", "forward_return_12h"]


def main():
    results = pd.read_csv("phase2e_cache/phase2e_frozen_family_results.csv")
    replication = pd.read_csv("phase2e_cache/phase2e_replication.csv")
    decisions = pd.read_csv("phase2e_cache/phase2e_decisions.csv")
    coverage = pd.read_csv("phase2e_cache/phase2e_coverage.csv")

    assert set(results["feature"].unique()) == set(DECLARED_FAMILY), \
        "Phase 2E's stored results no longer match the declared family — investigate before recording"
    assert len(results) == len(DECLARED_FAMILY) * len(SYMBOLS) * len(HORIZONS), \
        f"expected {len(DECLARED_FAMILY) * len(SYMBOLS) * len(HORIZONS)} rows, got {len(results)}"

    n_pass = int(results["passes_fdr"].sum())
    verdict = "SUPPORTED" if n_pass > 0 else "REJECTED"
    print(f"Confirmed: correction pool was already exactly the declared {len(DECLARED_FAMILY)}-feature family "
          f"({len(DECLARED_FAMILY) * len(SYMBOLS)} pairs/horizon) — no re-scoping needed.")
    print(f"{n_pass}/{len(results)} pass FDR. Verdict: {verdict}")

    hypothesis_family = freeze_family_before_testing(
        name="phase2e-derivatives", feature_family=DECLARED_FAMILY, assets=SYMBOLS,
        venue="binance", timeframe="1h", horizons=HORIZONS,
    )
    print(f"HYPOTHESIS FAMILY FROZEN: id={hypothesis_family.pk} n_hypotheses={hypothesis_family.n_hypotheses}")

    # claude code changed: real dataset row counts — funding coverage
    # 96-100%, OI coverage ~16.5% (Binance's real 30-day OI history cap),
    # exactly as measured in Step 3's coverage audit at the time.
    dataset_identities = [
        DatasetIdentity(source="binance_futures_funding_and_oi", symbol="BTC/USDT", venue="binance",
                         timeframe="1h", start_date="2026-02-25", end_date="2026-08-23",
                         row_count=int(coverage[(coverage.symbol == "BTC/USDT")]["valid_observations"].sum())),
        DatasetIdentity(source="binance_futures_funding_and_oi", symbol="ETH/USDT", venue="binance",
                         timeframe="1h", start_date="2026-02-25", end_date="2026-08-23",
                         row_count=int(coverage[(coverage.symbol == "ETH/USDT")]["valid_observations"].sum())),
    ]

    import subprocess
    try:
        code_version = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=5).decode().strip()
    except Exception:
        code_version = "unknown"

    experiment = record_research_trial(
        hypothesis_family=hypothesis_family,
        hypothesis_text=(
            "Does funding-rate/open-interest positioning information (funding_rate, "
            "funding_rate_zscore, funding_rate_change, funding_rate_cumulative_24h, oi_change_1h, "
            "oi_change_24h, oi_price_interaction) predict BTC/USDT and ETH/USDT forward returns "
            "(1h/4h/12h) beyond OHLCV?"
        ),
        dataset_identities=dataset_identities,
        statistical_results={
            "retrofit_note": "RETROFITTED into the ledger after the fact (Hardening Phase 2). "
                              "Correction pool confirmed to already exactly match the declared family — "
                              "no re-scoping was needed here (unlike Phase 2D).",
            "frozen_family_results": results.to_dict(orient="records"),
            "replication": replication.to_dict(orient="records"),
            "decisions": decisions.to_dict(orient="records"),
            "coverage": coverage.to_dict(orient="records"),
            "n_pass": n_pass, "n_family": len(results),
            "oi_coverage_note": "OI-derived features (oi_change_1h/24h, oi_price_interaction) had "
                                 "real ~16.5% coverage, capped by Binance's real ~30-day OI history "
                                 "limit against the 180-day OHLCV window — not a data failure.",
            "oos_status": "NOT_REACHED", "walk_forward_status": "NOT_REACHED", "cost_status": "NOT_JUSTIFIED",
        },
        verdict=verdict,
        code_version=code_version,
        structured_spec={
            "phase": "2E", "engine": "bot.research.derivatives_engine.DerivativesEngine",
            "retrofit": True, "original_analysis_script": "phase2e_analyze.py",
        },
    )
    print(f"\nRESEARCH TRIAL RECORDED: ResearchExperiment id={experiment.pk} verdict={experiment.verdict} "
          f"status={experiment.status} hypothesis_family_id={experiment.hypothesis_family_id}", flush=True)


if __name__ == "__main__":
    main()
