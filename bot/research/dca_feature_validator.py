# bot/research/dca_feature_validator.py
"""
DCA Feature Validator — Phase 4, 5, 6.

Validates whether entry-time features (ADX, z_score, ema_spread_pct, …)
predict DCA *cycle* outcomes (profit_usdt, win, fills, candles_held),
which is the G4-correct research question (not generic forward return).

Architecture
------------
Phase 4: Per-(pair, feature) Spearman IC against TARGET_PRIMARY (profit_usdt).
Phase 5: Block permutation p-value — cycles within a pair are serially dependent
         (market regimes persist), so i.i.d. permutation is anti-conservative.
         Shuffle cycles in blocks of BLOCK_SIZE to preserve local serial structure.
Phase 6: Family-wide FDR (Benjamini-Hochberg q=0.05) across all (pair, feature)
         test cells. Gate 5: family is pre-declared in ENTRY_FEATURE_FAMILY.

ADX hypothesis test (Phase 4b):
         Cycles split at ADX=25 (the DCA strategy's internal ADX gate threshold).
         Mann-Whitney U on profit_usdt: low-ADX (< 25) vs high-ADX (≥ 25).
         One-sided: hypothesis is that low-ADX cycles are more profitable.
         Pooled (all pairs) and per-pair.

Governance constraints
----------------------
- IS_PAPER_ONLY = True — all cycle data is from dry-run simulation.
- BLOCK_SIZE = 10 — cycles per permutation block; prevents over-rejection
  from purely serial structure masquerading as feature signal.
- N_PERMUTATIONS = 499 — enough for 0.002 p-floor, below FDR q-threshold.
- No post-hoc feature additions; family fixed at import time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests

from bot.research.dca_cycle_extractor import (
    ENTRY_FEATURE_FAMILY,
    TARGET_BINARY,
    TARGET_DURATION,
    TARGET_FILLS,
    TARGET_PRIMARY,
    AlignmentResult,
    extract_dca_cycles,
    print_alignment_report,
)

logger = logging.getLogger(__name__)

IS_PAPER_ONLY: bool = True           # governance constant — never set False  # claude code changed: new
ADX_GATE_THRESHOLD: float = 25.0     # the DCA strategy's internal ADX gate
BLOCK_SIZE: int = 10                 # cycles per block in block permutation
N_PERMUTATIONS: int = 499            # p-floor = 1/(N+1) ≈ 0.002 < FDR q=0.05
FDR_Q: float = 0.05                  # Benjamini-Hochberg false-discovery rate
MIN_CYCLES_FOR_TEST: int = 30        # minimum matched cycles per pair for valid test


@dataclass
class FeatureTestResult:
    """One (pair, feature) test cell."""
    pair:           str
    feature:        str
    n:              int       # matched cycles with non-NaN feature value
    ic:             float     # Spearman rank correlation with profit_usdt
    perm_pval:      float     # block-permutation p-value (one-tailed, IC direction)
    fdr_rejected:   bool      # True if FDR-adjusted p < q
    fdr_pval_adj:   float     # Benjamini-Hochberg adjusted p-value
    recommendation: str       # "KEEP" or "DELETE"


@dataclass
class ADXHypothesisResult:
    """ADX binary threshold test: low (<25) vs high (≥25) cycle profitability."""
    pair:             str          # "POOLED" for pooled test
    n_low:            int          # cycles with ADX < threshold
    n_high:           int          # cycles with ADX ≥ threshold
    mean_profit_low:  float
    mean_profit_high: float
    mannwhitney_u:    float
    pval_one_sided:   float        # H1: low-ADX cycles more profitable
    effect_direction: str          # "LOW_ADX_BETTER" / "HIGH_ADX_BETTER" / "INCONCLUSIVE"


@dataclass
class DCAValidationResult:
    """Full validation output. Gate 5: provenance + exclusions documented."""
    feature_tests:      List[FeatureTestResult]
    adx_tests:          List[ADXHypothesisResult]
    n_tests_total:      int
    n_fdr_survivors:    int
    survivor_features:  List[str]           # (pair, feature) strings that survived FDR
    cycle_summary:      Dict[str, int]      # pair → matched cycle count
    governance:         Dict[str, object]   # constants baked into the run


# ── Block permutation ─────────────────────────────────────────────────────────

def _block_permutation_pval(
    feature_vals: np.ndarray,
    target_vals:  np.ndarray,
    observed_ic:  float,
    n_perms:      int = N_PERMUTATIONS,
    block_size:   int = BLOCK_SIZE,
    rng:          Optional[np.random.Generator] = None,
) -> float:
    """
    Block-shuffle permutation p-value for Spearman IC.

    Cycles within a pair are serially dependent — market regimes persist
    over many cycles. Simple i.i.d. permutation ignores this structure
    and over-rejects (anti-conservative). Shuffling in blocks of block_size
    preserves local serial correlation while destroying long-range signal.

    H0: IC ≤ 0 (feature has no positive predictive power for profit_usdt).
    p = fraction of permuted ICs ≥ |observed_ic| (absolute, two-tailed pool
    but tested in the observed IC direction).
    """
    if rng is None:
        rng = np.random.default_rng(seed=42)  # deterministic for reproducibility

    n = len(target_vals)
    # Build block indices
    blocks = [list(range(i, min(i + block_size, n))) for i in range(0, n, block_size)]

    count_gte = 0
    for _ in range(n_perms):
        # Shuffle blocks, then flatten
        shuffled_blocks = rng.permutation(len(blocks))
        perm_idx = np.concatenate([blocks[b] for b in shuffled_blocks])
        perm_target = target_vals[perm_idx]
        perm_ic, _ = stats.spearmanr(feature_vals, perm_target)
        if np.isnan(perm_ic):
            continue
        # One-tailed in observed direction
        if observed_ic >= 0 and perm_ic >= observed_ic:
            count_gte += 1
        elif observed_ic < 0 and perm_ic <= observed_ic:
            count_gte += 1

    return (count_gte + 1) / (n_perms + 1)  # claude code changed: new — block permutation p-value


# ── Phase 4: Spearman IC per (pair, feature) ─────────────────────────────────

def _compute_ics(
    cycle_df: pd.DataFrame,
    feature_family: List[str],
) -> List[Dict]:
    """
    For each (pair, feature), compute Spearman IC against TARGET_PRIMARY.
    Returns list of raw result dicts (before permutation and FDR).
    """
    raw_results = []

    for pair in cycle_df["pair"].unique():
        pair_df = cycle_df[cycle_df["pair"] == pair]
        target = pair_df[TARGET_PRIMARY].values.astype(float)

        if len(pair_df) < MIN_CYCLES_FOR_TEST:
            logger.warning(
                "dca_validator: %s — only %d cycles, below minimum %d; skipping",
                pair, len(pair_df), MIN_CYCLES_FOR_TEST,
            )
            continue

        for feat in feature_family:
            if feat not in pair_df.columns:
                logger.debug("dca_validator: feature %s missing for %s", feat, pair)
                continue

            feat_vals = pair_df[feat].values.astype(float)
            mask = ~np.isnan(feat_vals) & ~np.isnan(target)
            n_valid = mask.sum()

            if n_valid < MIN_CYCLES_FOR_TEST:
                logger.debug(
                    "dca_validator: %s/%s — only %d non-NaN cycles; skipping",
                    pair, feat, n_valid,
                )
                continue

            ic, _ = stats.spearmanr(feat_vals[mask], target[mask])
            if np.isnan(ic):
                continue

            raw_results.append({
                "pair": pair, "feature": feat, "n": n_valid,
                "ic": ic,
                "feat_vals": feat_vals[mask],
                "target_vals": target[mask],
            })

    return raw_results  # claude code changed: new — per-(pair,feature) Spearman IC computation


# ── Phase 5: block permutation p-values ──────────────────────────────────────

def _compute_pvals(raw_results: List[Dict]) -> List[Dict]:
    """Add perm_pval to each raw result dict."""
    rng = np.random.default_rng(seed=42)
    for r in raw_results:
        r["perm_pval"] = _block_permutation_pval(
            r["feat_vals"], r["target_vals"], r["ic"],
            n_perms=N_PERMUTATIONS, block_size=BLOCK_SIZE, rng=rng,
        )
    return raw_results  # claude code changed: new — block permutation p-values added to raw results


# ── Phase 6: Family-wide FDR ─────────────────────────────────────────────────

def _apply_fdr(raw_results: List[Dict]) -> List[FeatureTestResult]:
    """
    Apply Benjamini-Hochberg FDR correction across the entire (pair, feature)
    family. Gate 5: family is pre-declared; no post-hoc additions permitted.
    """
    if not raw_results:
        return []

    pvals = np.array([r["perm_pval"] for r in raw_results])
    rejected, pvals_adj, _, _ = multipletests(pvals, alpha=FDR_Q, method="fdr_bh")

    results = []
    for r, rej, padj in zip(raw_results, rejected, pvals_adj):
        rec = "KEEP" if rej else "DELETE"
        results.append(FeatureTestResult(
            pair=r["pair"],
            feature=r["feature"],
            n=r["n"],
            ic=round(r["ic"], 4),
            perm_pval=round(r["perm_pval"], 4),
            fdr_rejected=bool(rej),
            fdr_pval_adj=round(float(padj), 4),
            recommendation=rec,
        ))

    return results  # claude code changed: new — family-wide FDR correction applied


# ── Phase 4b: ADX binary hypothesis test ─────────────────────────────────────

def _adx_hypothesis_test(cycle_df: pd.DataFrame) -> List[ADXHypothesisResult]:
    """
    Mann-Whitney U: low-ADX (< 25) vs high-ADX (≥ 25) cycle profit_usdt.
    One-sided H1: low-ADX cycles produce higher profit_usdt.
    Pooled across all pairs + per-pair.

    Rationale: the DCA strategy applies an ADX < 25 gate internally
    (adx_gate=True path). The simulation runs with adx_gate=False (all ADX
    values). This test asks whether that gate would have helped: are
    cycles that open below ADX=25 more profitable?
    """
    results = []

    def _test_group(df: pd.DataFrame, pair_label: str) -> Optional[ADXHypothesisResult]:
        if "adx" not in df.columns:
            return None
        mask = ~df["adx"].isna() & ~df[TARGET_PRIMARY].isna()
        df_valid = df[mask]
        if len(df_valid) < 20:
            return None

        low  = df_valid[df_valid["adx"] <  ADX_GATE_THRESHOLD][TARGET_PRIMARY].values
        high = df_valid[df_valid["adx"] >= ADX_GATE_THRESHOLD][TARGET_PRIMARY].values

        if len(low) < 10 or len(high) < 10:
            return None

        # Mann-Whitney U one-sided: H1 = low-ADX is stochastically greater
        u_stat, pval_two = stats.mannwhitneyu(low, high, alternative="greater")

        direction = (
            "LOW_ADX_BETTER"  if np.mean(low) > np.mean(high) else
            "HIGH_ADX_BETTER" if np.mean(high) > np.mean(low) else
            "INCONCLUSIVE"
        )

        return ADXHypothesisResult(
            pair=pair_label,
            n_low=len(low),
            n_high=len(high),
            mean_profit_low=round(float(np.mean(low)), 4),
            mean_profit_high=round(float(np.mean(high)), 4),
            mannwhitney_u=round(float(u_stat), 2),
            pval_one_sided=round(float(pval_two), 4),
            effect_direction=direction,
        )

    # Pooled
    pooled = _test_group(cycle_df, "POOLED")
    if pooled:
        results.append(pooled)

    # Per-pair
    for pair in cycle_df["pair"].unique():
        per = _test_group(cycle_df[cycle_df["pair"] == pair], pair)
        if per:
            results.append(per)

    return results  # claude code changed: new — ADX binary hypothesis test (Mann-Whitney)


# ── Main entry point ─────────────────────────────────────────────────────────

def validate_dca_features(
    alignment: Optional[AlignmentResult] = None,
) -> DCAValidationResult:
    """
    Run the full DCA feature validation pipeline (Phases 4–6 + ADX test).

    Parameters
    ----------
    alignment : AlignmentResult from extract_dca_cycles(), or None to run
                extraction automatically.

    Returns
    -------
    DCAValidationResult with feature_tests, adx_tests, FDR summary.
    """
    if not IS_PAPER_ONLY:
        raise RuntimeError(
            "IS_PAPER_ONLY is False — this validator must only run on paper/dry-run cycle data."
        )

    if alignment is None:
        logger.info("dca_validator: running extractor first…")
        alignment = extract_dca_cycles()

    cycle_df = alignment.cycle_df
    if cycle_df.empty:
        logger.warning("dca_validator: empty cycle_df — no tests to run")
        return DCAValidationResult(
            feature_tests=[], adx_tests=[], n_tests_total=0,
            n_fdr_survivors=0, survivor_features=[],
            cycle_summary={}, governance=_governance_record(),
        )

    logger.info(
        "dca_validator: %d matched cycles across %d pairs",
        len(cycle_df), cycle_df["pair"].nunique(),
    )

    # Phase 4 — ICs
    logger.info("dca_validator: Phase 4 — computing Spearman ICs…")
    raw = _compute_ics(cycle_df, alignment.feature_family)
    logger.info("dca_validator: %d (pair, feature) cells with sufficient data", len(raw))

    # Phase 5 — block permutation p-values
    logger.info("dca_validator: Phase 5 — block permutation (%d perms per cell)…", N_PERMUTATIONS)
    raw = _compute_pvals(raw)

    # Phase 6 — FDR correction
    logger.info("dca_validator: Phase 6 — family-wide FDR (BH q=%.2f)…", FDR_Q)
    feature_tests = _apply_fdr(raw)

    # ADX hypothesis test
    logger.info("dca_validator: Phase 4b — ADX binary hypothesis test…")
    adx_tests = _adx_hypothesis_test(cycle_df)

    survivors    = [f"{r.pair}::{r.feature}" for r in feature_tests if r.recommendation == "KEEP"]
    cycle_counts = cycle_df.groupby("pair").size().to_dict()

    logger.info(
        "dca_validator: DONE — %d tests, %d FDR survivors, %d ADX test groups",
        len(feature_tests), len(survivors), len(adx_tests),
    )

    return DCAValidationResult(
        feature_tests=feature_tests,
        adx_tests=adx_tests,
        n_tests_total=len(feature_tests),
        n_fdr_survivors=len(survivors),
        survivor_features=survivors,
        cycle_summary={str(k): int(v) for k, v in cycle_counts.items()},
        governance=_governance_record(),
    )  # claude code changed: new — DCA feature validator main entry point


def _governance_record() -> Dict[str, object]:
    return {
        "IS_PAPER_ONLY":       IS_PAPER_ONLY,
        "ADX_GATE_THRESHOLD":  ADX_GATE_THRESHOLD,
        "BLOCK_SIZE":          BLOCK_SIZE,
        "N_PERMUTATIONS":      N_PERMUTATIONS,
        "FDR_Q":               FDR_Q,
        "MIN_CYCLES_FOR_TEST": MIN_CYCLES_FOR_TEST,
        "feature_family":      list(ENTRY_FEATURE_FAMILY),
        "target_primary":      TARGET_PRIMARY,
        "run_date":            str(pd.Timestamp.now(tz="UTC").date()),
    }  # claude code changed: new — governance constants snapshot for G5 provenance


# ── Report printer ────────────────────────────────────────────────────────────

def print_validation_report(result: DCAValidationResult) -> None:
    """Print human-readable DCA feature validation report."""
    print("\n" + "=" * 72)
    print("DCA FEATURE VALIDATOR — RESULTS")
    print("=" * 72)
    gov = result.governance
    print(f"Run date        : {gov.get('run_date', 'N/A')}")
    print(f"Cycles          : {sum(result.cycle_summary.values())} total  "
          f"({', '.join(f'{p}={n}' for p, n in sorted(result.cycle_summary.items()))})")
    print(f"Tests total     : {result.n_tests_total}")
    print(f"FDR survivors   : {result.n_fdr_survivors}  (BH q={gov.get('FDR_Q', 0.05):.2f})")
    print(f"Block perm N    : {gov.get('N_PERMUTATIONS', 0)}")
    print(f"Block size      : {gov.get('BLOCK_SIZE', 0)}")
    print()

    # Feature-level summary: sort by abs(IC), show worst to best
    if result.feature_tests:
        print(f"{'Pair':<12} {'Feature':<24} {'N':>5} {'IC':>7} {'perm_p':>8} {'fdr_p':>8} {'Rec':>8}")
        print("-" * 75)
        for r in sorted(result.feature_tests, key=lambda x: abs(x.ic), reverse=True):
            flag = " *** KEEP" if r.recommendation == "KEEP" else ""
            print(f"{r.pair:<12} {r.feature:<24} {r.n:>5} {r.ic:>7.4f} "
                  f"{r.perm_pval:>8.4f} {r.fdr_pval_adj:>8.4f} {r.recommendation:>8}{flag}")
    else:
        print("No feature tests ran (insufficient cycle data).")

    print()
    print("ADX BINARY HYPOTHESIS TEST  (H1: low-ADX < 25 cycles more profitable)")
    print("-" * 72)
    if result.adx_tests:
        print(f"{'Pair':<12} {'N_low':>7} {'N_high':>7} {'Mean_low':>10} {'Mean_high':>10} {'MW_U':>10} {'p(one)':>8} {'Direction':<24}")
        for a in result.adx_tests:
            sig = " *" if a.pval_one_sided < 0.05 else ""
            print(f"{a.pair:<12} {a.n_low:>7} {a.n_high:>7} "
                  f"{a.mean_profit_low:>10.4f} {a.mean_profit_high:>10.4f} "
                  f"{a.mannwhitney_u:>10.2f} {a.pval_one_sided:>8.4f}  {a.effect_direction}{sig}")
    else:
        print("No ADX test groups (insufficient data or ADX not computed).")

    print("=" * 72)
    if result.n_fdr_survivors == 0:
        print("CONCLUSION: 0 FDR survivors — no entry-time feature predicts DCA cycle")
        print("            profitability at q=0.05. ADX gate hypothesis untested at")
        print("            family-corrected threshold.")
    else:
        print(f"CONCLUSION: {result.n_fdr_survivors} FDR survivor(s):")
        for s in result.survivor_features:
            print(f"  {s}")
    print("=" * 72)
