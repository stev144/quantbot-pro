"""
Phase 2E — Derivatives Information Research Gate.

# claude code changed: new file. Re-tests whether the EXISTING, unchanged
# derivatives feature family (bot/research/derivatives_engine.py) carries
# statistically defensible predictive information, using the exact same
# reused infrastructure as Phase 2D (FeatureCalculator, DerivativesEngine,
# FeatureValidator, apply_family_wide_correction) — no new statistical
# machinery, no new feature computation, no TradeFlowEngine involvement
# (out of scope — trade flow already has its own closed finding).
#
# FROZEN family (Phase 2E Step 1 — decided before this script inspects any
# result): funding_rate, funding_rate_zscore, funding_rate_change,
# funding_rate_cumulative_24h, oi_change_1h, oi_change_24h,
# oi_price_interaction. open_interest_amount (the raw level) is NOT
# pre-specified anywhere in Phase 2C/2D/the capability registry — reported
# separately, labeled exploratory, excluded from the correction family.
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
import ccxt

from bot.engines.derivatives_data import fetch_funding_rate_history, fetch_open_interest_history
from bot.research.feature_calculator import FeatureCalculator
from bot.research.derivatives_engine import DerivativesEngine
from bot.research.feature_validator import FeatureValidator, apply_family_wide_correction

CACHE_DIR = Path("phase2d_cache")   # claude code changed: reuse Phase 2D's cache dir — same BTC ohlcv/funding/oi pkls, no reason to duplicate them under a new directory
OUT_DIR = Path("phase2e_cache")
OUT_DIR.mkdir(exist_ok=True)

FROZEN_FAMILY = [
    "funding_rate", "funding_rate_zscore", "funding_rate_change", "funding_rate_cumulative_24h",
    "oi_change_1h", "oi_change_24h", "oi_price_interaction",
]
EXPLORATORY_ONLY = ["open_interest_amount"]   # claude code changed: not pre-specified anywhere — reported, never corrected-for, never promoted
HORIZONS = ["forward_return_1h", "forward_return_4h", "forward_return_12h"]
SYMBOLS = ["BTC/USDT", "ETH/USDT"]


def _with_retry(fn, *args, max_attempts=4, **kwargs):
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < max_attempts:
                print(f"  transient error (attempt {attempt}/{max_attempts}): {e}", flush=True)
                import time
                time.sleep(2.0 * attempt)
    raise last_err


def _acquire_ohlcv(symbol: str, since_ms: int, n_candles: int) -> pd.DataFrame:
    ex = ccxt.binance({"timeout": 30000, "enableRateLimit": True})
    all_rows = []
    cursor = since_ms
    while len(all_rows) < n_candles:
        batch = _with_retry(ex.fetch_ohlcv, symbol, timeframe="1h", since=cursor, limit=1000)
        if not batch:
            break
        all_rows.extend(batch)
        last_ts = batch[-1][0]
        if last_ts <= cursor:
            break
        cursor = last_ts + 3600_000
        if len(batch) < 1000:
            break
    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.drop_duplicates(subset=["timestamp"], inplace=True)
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def _load_symbol_raw(symbol: str, manifest: dict):
    safe = symbol.replace("/", "_")
    ohlcv_path = CACHE_DIR / f"{safe}_ohlcv.pkl"
    funding_path = CACHE_DIR / f"{safe}_funding.pkl"
    oi_path = CACHE_DIR / f"{safe}_oi.pkl"

    if ohlcv_path.exists() and funding_path.exists() and oi_path.exists():
        print(f"{symbol}: loading ohlcv/funding/oi from cache", flush=True)
        return pd.read_pickle(ohlcv_path), pd.read_pickle(funding_path), pd.read_pickle(oi_path)

    print(f"{symbol}: not cached — fetching ohlcv/funding/oi fresh (lightweight calls only, "
          f"no raw aggTrades archive downloads)", flush=True)
    start_date, end_date = manifest["start_date"], manifest["end_date"]
    since_ms = int(pd.Timestamp(start_date, tz="UTC").timestamp() * 1000)
    n_candles_target = manifest["n_days_target"] * 24 + 24

    ohlcv = _with_retry(_acquire_ohlcv, symbol, since_ms, n_candles_target)
    funding_df = _with_retry(fetch_funding_rate_history, symbol, since_ms=since_ms)
    oi_df = _with_retry(fetch_open_interest_history, symbol, days=30)

    ohlcv.to_pickle(ohlcv_path)
    funding_df.to_pickle(funding_path)
    oi_df.to_pickle(oi_path)
    return ohlcv, funding_df, oi_df


def _build_observations(symbol: str, ohlcv: pd.DataFrame, funding_df, oi_df) -> pd.DataFrame:
    fc = FeatureCalculator(min_data_required=100)
    base = fc.calculate_all_features(ohlcv.copy(), symbol=symbol, timeframe="1h")

    de = DerivativesEngine()
    deriv = de.calculate_all(ohlcv.copy(), funding_df, oi_df)

    base["timestamp"] = pd.to_datetime(base["timestamp"], utc=True)
    deriv["timestamp"] = pd.to_datetime(deriv["timestamp"], utc=True)

    deriv_cols = ["timestamp", "forward_return_2h", "forward_return_12h"] + FROZEN_FAMILY + EXPLORATORY_ONLY
    merged = base.merge(deriv[deriv_cols], on="timestamp", how="left")
    merged["symbol"] = symbol
    return merged


def _coverage_row(symbol: str, family: str, feature: str, df: pd.DataFrame) -> dict:
    total = len(df)
    valid = df[feature].notna()
    n_valid = int(valid.sum())
    if n_valid == 0:
        return {"symbol": symbol, "family": family, "feature": feature, "total_candles": total,
                "valid_observations": 0, "first_valid_ts": None, "last_valid_ts": None,
                "coverage_pct": 0.0, "continuity": "NO VALID DATA"}

    first_ts = df.loc[valid, "timestamp"].min()
    last_ts = df.loc[valid, "timestamp"].max()
    coverage_pct = n_valid / total * 100

    # claude code changed: continuity check — within [first_valid, last_valid],
    # what fraction of candles are actually valid? A pure warmup-then-continuous
    # feature (funding/OI's normal shape) should be ~100% within its own active
    # window; anything less means real internal gaps, not just startup NaN.
    active_window = df[(df["timestamp"] >= first_ts) & (df["timestamp"] <= last_ts)]
    active_valid_pct = active_window[feature].notna().mean() * 100
    continuity = "CONTINUOUS (after startup warmup)" if active_valid_pct >= 99.0 else \
                 f"CONTAINS GAPS ({active_valid_pct:.1f}% valid within its own active window)"

    return {
        "symbol": symbol, "family": family, "feature": feature, "total_candles": total,
        "valid_observations": n_valid, "first_valid_ts": str(first_ts), "last_valid_ts": str(last_ts),
        "coverage_pct": round(coverage_pct, 2), "continuity": continuity,
    }


def main():
    manifest = json.loads((CACHE_DIR / "manifest.json").read_text())
    print(f"MANIFEST (reused from Phase 2D — same fixed window, no re-acquisition): {manifest}", flush=True)

    observations = {}
    for symbol in SYMBOLS:
        ohlcv, funding_df, oi_df = _load_symbol_raw(symbol, manifest)
        obs = _build_observations(symbol, ohlcv, funding_df, oi_df)
        observations[symbol] = obs
        obs.to_pickle(OUT_DIR / f"{symbol.replace('/', '_')}_observations.pkl")
        print(f"{symbol}: {len(obs)} total candles built", flush=True)

    # ══════════════════════ STEP 3 — COVERAGE AUDIT ══════════════════════
    print(f"\n{'='*100}\nSTEP 3 — COVERAGE AUDIT\n{'='*100}", flush=True)
    coverage_rows = []
    for symbol in SYMBOLS:
        df = observations[symbol]
        for feature in FROZEN_FAMILY:
            family = "funding" if feature.startswith("funding_") else "open_interest"
            coverage_rows.append(_coverage_row(symbol, family, feature, df))
        for feature in EXPLORATORY_ONLY:
            coverage_rows.append(_coverage_row(symbol, "exploratory", feature, df))
    coverage_df = pd.DataFrame(coverage_rows)
    coverage_df.to_csv(OUT_DIR / "phase2e_coverage.csv", index=False)
    print(coverage_df.to_string(index=False), flush=True)

    # ══════════════════ STEP 4/5 — FROZEN STAT TEST + FAMILY-WIDE FDR ══════════════════
    print(f"\n{'='*100}\nSTEP 4/5 — FROZEN FAMILY STATISTICAL TEST + EXPLICIT CORRECTION FAMILY\n{'='*100}", flush=True)
    print(f"Correction family = {len(FROZEN_FAMILY)} features x {len(SYMBOLS)} symbols x {len(HORIZONS)} horizons "
          f"= {len(FROZEN_FAMILY) * len(SYMBOLS) * len(HORIZONS)} hypothesis tests.", flush=True)
    print(f"Frozen family: {FROZEN_FAMILY}", flush=True)
    print(f"Exploratory only (excluded from correction): {EXPLORATORY_ONLY}", flush=True)

    all_pooled = []
    exploratory_rows = []
    for horizon in HORIZONS:
        print(f"\n--- HORIZON: {horizon} ---", flush=True)
        raw_by_symbol = {}
        for symbol in SYMBOLS:
            df = observations[symbol]
            slim_cols = ["timestamp", "close", "realized_vol", "adx"] + FROZEN_FAMILY + [horizon]
            slim = df[[c for c in slim_cols if c in df.columns]].copy()
            validator = FeatureValidator(min_observations=30, alpha=0.05)
            raw = validator.validate_all_features_raw(slim, horizon)
            raw_by_symbol[symbol] = raw

            # Exploratory feature — computed and reported, NEVER pooled into the correction family.
            slim_expl = df[[c for c in ["timestamp", "close", "realized_vol", "adx"] + EXPLORATORY_ONLY + [horizon] if c in df.columns]].copy()
            validator_expl = FeatureValidator(min_observations=30, alpha=0.05)
            raw_expl = validator_expl.validate_all_features_raw(slim_expl, horizon)
            raw_expl["horizon"] = horizon
            raw_expl["symbol"] = symbol
            raw_expl["family_scope"] = "EXPLORATORY - NOT CORRECTED"
            exploratory_rows.append(raw_expl)

        per_symbol, pooled = apply_family_wide_correction(raw_by_symbol, alpha=0.05)
        pooled["horizon"] = horizon
        all_pooled.append(pooled)

        print(pooled[["symbol", "feature", "ic_overall", "ic_rank", "p_value_block_permutation",
                       "p_fdr", "passes_fdr", "recommendation"]].to_string(index=False), flush=True)

    full_pooled = pd.concat(all_pooled, ignore_index=True)
    full_pooled.to_csv(OUT_DIR / "phase2e_frozen_family_results.csv", index=False)

    exploratory_df = pd.concat(exploratory_rows, ignore_index=True)
    exploratory_df.to_csv(OUT_DIR / "phase2e_exploratory_results.csv", index=False)

    n_family = len(full_pooled)
    n_pass = int(full_pooled["passes_fdr"].sum())
    print(f"\nFROZEN FAMILY TOTAL: {n_pass}/{n_family} (symbol, feature, horizon) triples pass FDR at alpha=0.05.", flush=True)

    # ══════════════════════ STEP 6 — BTC/ETH REPLICATION ══════════════════════
    print(f"\n{'='*100}\nSTEP 6 — REPLICATION CLASSIFICATION (sign consistency, both symbols, per feature/horizon)\n{'='*100}", flush=True)
    replication_rows = []
    for horizon in HORIZONS:
        for feature in FROZEN_FAMILY:
            sub = full_pooled[(full_pooled["horizon"] == horizon) & (full_pooled["feature"] == feature)]
            btc_row = sub[sub["symbol"] == "BTC/USDT"]
            eth_row = sub[sub["symbol"] == "ETH/USDT"]
            if btc_row.empty or eth_row.empty:
                continue
            btc_ic = float(btc_row["ic_overall"].iloc[0])
            eth_ic = float(eth_row["ic_overall"].iloc[0])
            btc_pass = bool(btc_row["passes_fdr"].iloc[0])
            eth_pass = bool(eth_row["passes_fdr"].iloc[0])
            same_sign = (np.sign(btc_ic) == np.sign(eth_ic)) and btc_ic != 0 and eth_ic != 0

            if btc_pass and eth_pass:
                classification = "REPLICATED (both pass FDR)" if same_sign else "INCONSISTENT (both pass FDR, opposite sign)"
            elif btc_pass and not eth_pass:
                classification = "BTC-only"
            elif eth_pass and not btc_pass:
                classification = "ETH-only"
            else:
                classification = "NEITHER (fails FDR on both)"

            replication_rows.append({
                "horizon": horizon, "feature": feature,
                "btc_ic": btc_ic, "eth_ic": eth_ic, "same_sign": same_sign,
                "btc_passes_fdr": btc_pass, "eth_passes_fdr": eth_pass,
                "classification": classification,
            })
    replication_df = pd.DataFrame(replication_rows)
    replication_df.to_csv(OUT_DIR / "phase2e_replication.csv", index=False)
    print(replication_df.to_string(index=False), flush=True)

    # ══════════════════════ STEP 7 — STRICT DECISION GATE ══════════════════════
    print(f"\n{'='*100}\nSTEP 7 — DECISION GATE (per feature, across all horizons)\n{'='*100}", flush=True)
    IC_ECONOMICALLY_MEANINGFUL = 0.05   # matches feature_validator.py's own IC_ACCEPTABLE threshold — no new threshold invented
    decisions = []
    for feature in FROZEN_FAMILY:
        sub = full_pooled[full_pooled["feature"] == feature]
        any_pass = bool(sub["passes_fdr"].any())
        max_abs_ic = float(sub["ic_overall"].abs().max())
        if not any_pass:
            decision = "FAIL"
            reason = "No (symbol, horizon) combination survives family-wide FDR correction."
        elif max_abs_ic < IC_ECONOMICALLY_MEANINGFUL:
            decision = "FAIL"
            reason = f"Survives FDR somewhere but IC magnitude ({max_abs_ic:.4f}) is below the economically-meaningful threshold ({IC_ECONOMICALLY_MEANINGFUL})."
        else:
            rep_rows = replication_df[replication_df["feature"] == feature]
            replicated = bool((rep_rows["classification"] == "REPLICATED (both pass FDR)").any())
            decision = "PASS" if replicated else "FAIL"
            reason = "Survives FDR with meaningful IC AND replicates across both assets." if replicated else \
                      "Survives FDR with meaningful IC on at most one asset only — not cross-asset replicated, not pre-specified as asset-specific."
        decisions.append({"feature": feature, "decision": decision, "max_abs_ic_any_horizon": round(max_abs_ic, 4), "reason": reason})

    decisions_df = pd.DataFrame(decisions)
    decisions_df.to_csv(OUT_DIR / "phase2e_decisions.csv", index=False)
    print(decisions_df.to_string(index=False), flush=True)

    n_pass_features = int((decisions_df["decision"] == "PASS").sum())
    print(f"\nFEATURES PASSING STEP 7: {n_pass_features}/{len(FROZEN_FAMILY)}", flush=True)
    if n_pass_features == 0:
        print("STEP 8: NOTHING PASSES — STOPPING per Phase 2E Step 8. No walk-forward will be run.", flush=True)
    else:
        print("STEP 8: at least one candidate passed — walk-forward would be considered (not run automatically).", flush=True)


if __name__ == "__main__":
    main()
