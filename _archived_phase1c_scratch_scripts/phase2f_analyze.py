"""
Phase 2F — Order-Book/L2 Microstructure Research Gate.

# claude code changed: new file. Narrowed frozen family (Phase 2F Step 2's
# feasibility conclusion): depth_imbalance_2pct, depth_concentration — both
# derived ONLY from Binance's real bookDepth archive (fixed-percentage-band
# cumulative depth/notional, USDT-margined perpetual futures), causally
# aligned via bot/research/orderbook_depth_engine.py (unchanged, already
# tested — bot/tests/test_orderbook_depth_engine.py). Reuses the exact same
# FeatureCalculator / FeatureValidator / apply_family_wide_correction
# infrastructure as Phase 2D/2E — no new statistical machinery.
"""
import sys
import os
import json
import warnings
from pathlib import Path

sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

import numpy as np
import pandas as pd

from bot.research.orderbook_depth_engine import OrderBookDepthEngine, fetch_book_depth_archive
from bot.research.feature_calculator import FeatureCalculator
from bot.research.derivatives_engine import DerivativesEngine
from bot.research.feature_validator import FeatureValidator, apply_family_wide_correction
from bot.research_lab.data_fingerprint import DatasetIdentity
from bot.research_lab.trial_service import freeze_family_before_testing, record_research_trial

CACHE_DIR_2D = Path("phase2d_cache")   # claude code changed: reuse Phase 2D/2E's cached BTC/ETH OHLCV — same fixed window, no re-acquisition
CACHE_DIR = Path("phase2f_cache")
CACHE_DIR.mkdir(exist_ok=True)

FROZEN_FAMILY = ["depth_imbalance_2pct", "depth_concentration"]
HORIZONS = ["forward_return_1h", "forward_return_4h", "forward_return_12h"]
SYMBOLS = ["BTC/USDT", "ETH/USDT"]


def _load_ohlcv(symbol: str) -> pd.DataFrame:
    safe = symbol.replace("/", "_")
    path = CACHE_DIR_2D / f"{safe}_ohlcv.pkl"
    if path.exists():
        return pd.read_pickle(path)
    raise FileNotFoundError(f"{path} not found — Phase 2D/2E's cached OHLCV is required as the fixed research window")


def _acquire_depth(symbol: str, dates: list) -> pd.DataFrame:
    import time
    safe = symbol.replace("/", "_")
    chunks = []
    coverage_rows = []
    for date in dates:
        cache_path = CACHE_DIR / f"{safe}_{date}.pkl"
        if cache_path.exists():
            day_df = pd.read_pickle(cache_path)
        else:
            # claude code changed: small courtesy delay between fresh
            # downloads only (never for cache hits) — repeated resumes hit
            # a DNS-resolution failure at the same request count each time,
            # consistent with local/CDN rate-limiting rather than random
            # flakiness; pacing requests avoids re-triggering it.
            time.sleep(0.75)
            day_df = fetch_book_depth_archive(symbol, date)
            day_df.to_pickle(cache_path)
        coverage_rows.append({"date": date, "n_snapshots": day_df["timestamp"].nunique() if not day_df.empty else 0})
        if not day_df.empty:
            chunks.append(day_df)
    combined = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
    coverage_df = pd.DataFrame(coverage_rows)
    return combined, coverage_df


def _coverage_row(symbol: str, feature: str, df: pd.DataFrame) -> dict:
    total = len(df)
    valid = df[feature].notna()
    n_valid = int(valid.sum())
    if n_valid == 0:
        return {"symbol": symbol, "feature": feature, "total_candles": total, "valid_observations": 0,
                "first_valid_ts": None, "last_valid_ts": None, "coverage_pct": 0.0, "continuity": "NO VALID DATA"}
    first_ts, last_ts = df.loc[valid, "timestamp"].min(), df.loc[valid, "timestamp"].max()
    active_window = df[(df["timestamp"] >= first_ts) & (df["timestamp"] <= last_ts)]
    active_valid_pct = active_window[feature].notna().mean() * 100
    longest_gap_candles = 0
    if n_valid < len(active_window):
        gap_run = 0
        for is_valid in active_window[feature].notna():
            gap_run = 0 if is_valid else gap_run + 1
            longest_gap_candles = max(longest_gap_candles, gap_run)
    continuity = "CONTINUOUS (after startup warmup)" if active_valid_pct >= 99.0 else \
                 f"CONTAINS GAPS ({active_valid_pct:.1f}% valid within active window, longest gap {longest_gap_candles} candles)"
    return {"symbol": symbol, "feature": feature, "total_candles": total, "valid_observations": n_valid,
            "first_valid_ts": str(first_ts), "last_valid_ts": str(last_ts),
            "coverage_pct": round(n_valid / total * 100, 2), "continuity": continuity}


def main():
    manifest = json.loads((CACHE_DIR_2D / "manifest.json").read_text())
    start_date, end_date = manifest["start_date"], manifest["end_date"]
    dates = pd.date_range(start_date, end_date, freq="D").strftime("%Y-%m-%d").tolist()
    print(f"MANIFEST (reused fixed window from Phase 2D/2E): start={start_date} end={end_date} ({len(dates)} days)", flush=True)

    # claude code changed: Hardening Phase 2, Priority 1 — the family is
    # frozen HERE, before a single bookDepth file is downloaded or a
    # single statistical test runs. freeze_family_before_testing() never
    # returns a mutable-scope family (bot/research_lab/trial_service.py),
    # so nothing below this line can silently widen what's being tested.
    hypothesis_family = freeze_family_before_testing(
        name="phase2f-orderbook-depth", feature_family=FROZEN_FAMILY, assets=SYMBOLS,
        venue="binance", timeframe="1h", horizons=HORIZONS,
    )
    print(f"HYPOTHESIS FAMILY FROZEN: id={hypothesis_family.pk} "
          f"n_hypotheses={hypothesis_family.n_hypotheses} frozen_at={hypothesis_family.frozen_at}", flush=True)

    observations = {}
    raw_coverage = {}
    dataset_identities = []
    for symbol in SYMBOLS:
        print(f"\n{symbol}: acquiring bookDepth ({len(dates)} days, cached per-day, resumable)...", flush=True)
        ohlcv = _load_ohlcv(symbol)
        ohlcv["timestamp"] = pd.to_datetime(ohlcv["timestamp"], utc=True)
        candle_index = pd.DatetimeIndex(ohlcv["timestamp"])

        depth_df, day_coverage = _acquire_depth(symbol, dates)
        raw_coverage[symbol] = day_coverage
        n_days_with_data = int((day_coverage["n_snapshots"] > 0).sum())
        n_snapshots = depth_df["timestamp"].nunique() if not depth_df.empty else 0
        print(f"{symbol}: {n_days_with_data}/{len(dates)} days had real bookDepth data "
              f"({n_snapshots} total snapshots)", flush=True)

        # claude code changed: Hardening Mission §5 — one DatasetIdentity
        # per symbol, capturing exactly what would need to differ for two
        # researchers to have genuinely tested different data (source,
        # window, and the row count actually acquired — not just the
        # window requested, since a day with a 404/empty archive file
        # still "requests" the same range but acquires fewer rows).
        dataset_identities.append(DatasetIdentity(
            source="binance_futures_bookDepth_archive", symbol=symbol, venue="binance",
            timeframe="1h", start_date=start_date, end_date=end_date, row_count=n_snapshots,
        ))

        engine = OrderBookDepthEngine()
        depth_features = engine.compute_features(depth_df, candle_index)

        fc = FeatureCalculator(min_data_required=100)
        base = fc.calculate_all_features(ohlcv.copy(), symbol=symbol, timeframe="1h")

        # claude code changed: real bug found running this script end to
        # end — FeatureCalculator only computes forward_return_1h/4h/24h,
        # NOT forward_return_12h (that horizon only exists via
        # DerivativesEngine._add_forward_returns(), the same gap
        # phase2e_analyze.py already had to work around). Missed here on
        # the first pass; HORIZONS below includes forward_return_12h, so
        # validate_all_features_raw() raised KeyError on it. Merging in
        # DerivativesEngine's forward_return_12h (funding/oi=None — not
        # needed, only the label computation is used here) unchanged,
        # same pattern phase2e_analyze.py already established.
        deriv = DerivativesEngine().calculate_all(ohlcv.copy(), None, None)
        deriv["timestamp"] = pd.to_datetime(deriv["timestamp"], utc=True)
        base["timestamp"] = pd.to_datetime(base["timestamp"], utc=True)
        base = base.merge(deriv[["timestamp", "forward_return_12h"]], on="timestamp", how="left")

        # claude code changed: same datetime64-resolution fix as
        # orderbook_depth_engine.py's internal merge_asof — align both
        # sides to the same unit before merging, never a value change.
        base["timestamp"] = base["timestamp"].dt.as_unit("us")
        depth_features = depth_features.copy()
        depth_features["timestamp"] = depth_features["timestamp"].dt.as_unit("us")

        merged = base.merge(depth_features, on="timestamp", how="left")
        merged["symbol"] = symbol
        observations[symbol] = merged
        merged.to_pickle(CACHE_DIR / f"{symbol.replace('/', '_')}_observations.pkl")

    # ══════════════════════ STEP 4 — COVERAGE AUDIT ══════════════════════
    print(f"\n{'='*100}\nSTEP 4 — DATA QUALITY / COVERAGE AUDIT\n{'='*100}", flush=True)
    coverage_rows = []
    for symbol in SYMBOLS:
        df = observations[symbol]
        for feature in FROZEN_FAMILY:
            coverage_rows.append(_coverage_row(symbol, feature, df))
        dup_ts = df["timestamp"].duplicated().sum()
        print(f"{symbol}: {len(df)} total candles, requested window {start_date}..{end_date}, "
              f"actual OHLCV window {df['timestamp'].min()}..{df['timestamp'].max()}, duplicate timestamps: {dup_ts}", flush=True)
    coverage_df = pd.DataFrame(coverage_rows)
    coverage_df.to_csv(CACHE_DIR / "phase2f_coverage.csv", index=False)
    print(coverage_df.to_string(index=False), flush=True)

    # ══════════════ STEP 7/8 — UNIVARIATE VALIDATION + EXPLICIT FDR FAMILY ══════════════
    print(f"\n{'='*100}\nSTEP 7/8 — FROZEN L2 FAMILY STATISTICAL TEST + EXPLICIT CORRECTION FAMILY\n{'='*100}", flush=True)
    print(f"Correction family = {len(FROZEN_FAMILY)} features x {len(SYMBOLS)} symbols x {len(HORIZONS)} horizons "
          f"= {len(FROZEN_FAMILY) * len(SYMBOLS) * len(HORIZONS)} hypothesis tests. NOT pooled with OHLCV/trade-flow/derivatives families.", flush=True)

    all_pooled = []
    for horizon in HORIZONS:
        print(f"\n--- HORIZON: {horizon} ---", flush=True)
        raw_by_symbol = {}
        for symbol in SYMBOLS:
            df = observations[symbol]
            slim_cols = ["timestamp", "close", "realized_vol", "adx"] + FROZEN_FAMILY + [horizon]
            slim = df[[c for c in slim_cols if c in df.columns]].copy()
            validator = FeatureValidator(min_observations=30, alpha=0.05)
            raw_by_symbol[symbol] = validator.validate_all_features_raw(slim, horizon)

        per_symbol, pooled = apply_family_wide_correction(raw_by_symbol, alpha=0.05)
        pooled["horizon"] = horizon
        all_pooled.append(pooled)
        print(pooled[["symbol", "feature", "ic_overall", "ic_rank", "p_value_block_permutation",
                       "p_fdr", "passes_fdr", "recommendation"]].to_string(index=False), flush=True)

    full_pooled = pd.concat(all_pooled, ignore_index=True)
    full_pooled.to_csv(CACHE_DIR / "phase2f_frozen_family_results.csv", index=False)
    n_family = len(full_pooled)
    n_pass = int(full_pooled["passes_fdr"].sum())
    print(f"\nFROZEN L2 FAMILY TOTAL: {n_pass}/{n_family} (symbol, feature, horizon) triples pass FDR at alpha=0.05.", flush=True)

    # ══════════════════════ STEP 9 — REPLICATION ══════════════════════
    print(f"\n{'='*100}\nSTEP 9 — BTC/ETH REPLICATION CLASSIFICATION\n{'='*100}", flush=True)
    replication_rows = []
    for horizon in HORIZONS:
        for feature in FROZEN_FAMILY:
            sub = full_pooled[(full_pooled["horizon"] == horizon) & (full_pooled["feature"] == feature)]
            btc_row, eth_row = sub[sub["symbol"] == "BTC/USDT"], sub[sub["symbol"] == "ETH/USDT"]
            if btc_row.empty or eth_row.empty:
                continue
            btc_ic, eth_ic = float(btc_row["ic_overall"].iloc[0]), float(eth_row["ic_overall"].iloc[0])
            btc_pass, eth_pass = bool(btc_row["passes_fdr"].iloc[0]), bool(eth_row["passes_fdr"].iloc[0])
            same_sign = (np.sign(btc_ic) == np.sign(eth_ic)) and btc_ic != 0 and eth_ic != 0
            if btc_pass and eth_pass:
                classification = "REPLICATED" if same_sign else "INCONSISTENT"
            elif btc_pass:
                classification = "BTC_ONLY"
            elif eth_pass:
                classification = "ETH_ONLY"
            else:
                classification = "NEITHER"
            replication_rows.append({"horizon": horizon, "feature": feature, "btc_ic": btc_ic, "eth_ic": eth_ic,
                                      "same_sign": same_sign, "btc_passes_fdr": btc_pass, "eth_passes_fdr": eth_pass,
                                      "classification": classification})
    replication_df = pd.DataFrame(replication_rows)
    replication_df.to_csv(CACHE_DIR / "phase2f_replication.csv", index=False)
    print(replication_df.to_string(index=False), flush=True)

    # ══════════════════════ STEP 12 — STRICT DECISION GATE ══════════════════════
    print(f"\n{'='*100}\nSTEP 12 — DECISION GATE\n{'='*100}", flush=True)
    IC_ECONOMICALLY_MEANINGFUL = 0.05
    decisions = []
    for feature in FROZEN_FAMILY:
        sub = full_pooled[full_pooled["feature"] == feature]
        any_pass = bool(sub["passes_fdr"].any())
        max_abs_ic = float(sub["ic_overall"].abs().max())
        cov = coverage_df[coverage_df["feature"] == feature]
        insufficient_coverage = bool((cov["coverage_pct"] < 50).any())

        if insufficient_coverage and not any_pass:
            decision, reason = "INCONCLUSIVE", "Coverage below 50% on at least one symbol AND no FDR survivor — genuine data limitation, not just a null result."
        elif not any_pass:
            decision, reason = "FAIL", "No (symbol, horizon) combination survives family-wide FDR correction."
        elif max_abs_ic < IC_ECONOMICALLY_MEANINGFUL:
            decision, reason = "FAIL", f"Survives FDR somewhere but IC magnitude ({max_abs_ic:.4f}) is below the economically-meaningful threshold ({IC_ECONOMICALLY_MEANINGFUL})."
        else:
            rep_rows = replication_df[replication_df["feature"] == feature]
            replicated = bool((rep_rows["classification"] == "REPLICATED").any())
            decision = "PASS" if replicated else "FAIL"
            reason = "Survives FDR with meaningful IC AND replicates across both assets." if replicated else \
                      "Survives FDR with meaningful IC on at most one asset — not cross-asset replicated."
        decisions.append({"feature": feature, "decision": decision, "max_abs_ic_any_horizon": round(max_abs_ic, 4), "reason": reason})

    decisions_df = pd.DataFrame(decisions)
    decisions_df.to_csv(CACHE_DIR / "phase2f_decisions.csv", index=False)
    print(decisions_df.to_string(index=False), flush=True)

    n_pass_features = int((decisions_df["decision"] == "PASS").sum())
    print(f"\nFEATURES PASSING STEP 12: {n_pass_features}/{len(FROZEN_FAMILY)}", flush=True)
    # claude code changed: Hardening Mission §6 — NOT RUN is a distinct,
    # explicit outcome from FAIL; stored as such below, never conflated.
    oos_status = "NOT_REACHED"
    walk_forward_status = "NOT_REACHED"
    cost_status = "NOT_JUSTIFIED"
    if n_pass_features == 0:
        print("STEP 10/11 (incremental info / OOS): NOT RUN — no univariate candidate cleared Step 12.", flush=True)
        print("STEP 13 (walk-forward): NOT JUSTIFIED.", flush=True)
        print("STEP 14 (cost test): NOT JUSTIFIED.", flush=True)
    else:
        print("STEP 10 (incremental info vs OHLCV baseline) would now run for the passing candidate(s).", flush=True)
        oos_status = walk_forward_status = cost_status = "PENDING_MANUAL_REVIEW"

    # claude code changed: Hardening Phase 2, Priority 1 — the actual
    # wiring this mission asked for: one real, persisted ResearchExperiment
    # row, referencing the family frozen at the top of this run, carrying
    # a real per-symbol dataset fingerprint and the complete Step 4-12
    # evidence trail. code_version = current git SHA, same convention
    # orchestrator.py's get_code_version() already established.
    import subprocess
    try:
        code_version = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
    except Exception:
        code_version = "unknown"

    overall_verdict = "SUPPORTED" if n_pass_features > 0 else "REJECTED"
    experiment = record_research_trial(
        hypothesis_family=hypothesis_family,
        hypothesis_text=(
            "Does observable Binance futures order-book depth (bookDepth archive: depth_imbalance_2pct, "
            "depth_concentration) contain information about BTC/USDT and ETH/USDT forward returns "
            "(1h/4h/12h) not already captured by OHLCV?"
        ),
        dataset_identities=dataset_identities,
        statistical_results={
            "coverage": coverage_df.to_dict(orient="records"),
            "frozen_family_results": full_pooled.to_dict(orient="records"),
            "replication": replication_df.to_dict(orient="records"),
            "decisions": decisions_df.to_dict(orient="records"),
            "n_pass_step12": n_pass_features,
            "oos_status": oos_status,
            "walk_forward_status": walk_forward_status,
            "cost_status": cost_status,
        },
        verdict=overall_verdict,
        code_version=code_version,
        structured_spec={
            "phase": "2F", "engine": "bot.research.orderbook_depth_engine.OrderBookDepthEngine",
            "manifest": manifest,
        },
    )
    print(f"\nRESEARCH TRIAL RECORDED: ResearchExperiment id={experiment.pk} "
          f"verdict={experiment.verdict} status={experiment.status} "
          f"hypothesis_family_id={experiment.hypothesis_family_id}", flush=True)


if __name__ == "__main__":
    main()
