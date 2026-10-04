# ============================================================
# bot/research/regime_conditional_ic.py
#
# REGIME-CONDITIONAL INFORMATION COEFFICIENT (Phases 7, 8, 9, 10)
#
# claude code changed: new file — Regime-Conditional Quantitative Research
# mission. This is the "critical deliverable" Phase 7 names: not one
# pooled IC, but overall + per-regime IC, with real stability metrics
# (Phase 8), one explicitly-defined FDR family per feature/horizon
# (Phase 9), and a real interaction test (Phase 10) — not separate
# significant/non-significant p-values interpreted as proof regimes differ.
#
# FDR FAMILY DEFINITION (Phase 9's explicit requirement — documented here,
# not left implicit): for ONE (feature_col, forward_return_col) pair, the
# family is {overall} union every regime cell, across every requested
# taxonomy dimension (trend_state alone, volatility_state alone, the
# combined regime_label cross), that has >= min_obs observations. All
# raw p-values in that family are corrected TOGETHER, once, via
# statsmodels.stats.multitest.multipletests (Benjamini-Hochberg) — the
# SAME method and library already used identically in
# cointegration_engine.py's _apply_fdr_correction() and
# cross_sectional_permutation_test.py's run_topk_sweep(), never a second,
# incompatible correction scheme. A caller testing multiple DIFFERENT
# features/horizons must call this once per feature/horizon and treat each
# call as its own separate family (this module does not, and should not,
# silently pool unrelated features into one family — see this mission's
# forensic report for the separately-documented, still-open gap of "no
# FDR correction across multiple hypothesis_name choices," which this
# module does not attempt to solve, since that requires an orchestrator-
# level policy decision, not a statistics-module change).
#
# WHY BLOCK-IC, NOT A ROLLING WINDOW, FOR STABILITY (Phase 8): a
# rolling-window Spearman correlation via pandas .rolling().apply() is
# O(n * window) and, on this project's research-scale panels (Phase 20's
# performance concern), a real, avoidable cost. Splitting each regime's
# OWN chronological subsequence into a small number of contiguous blocks
# and computing one IC per block is O(n) and answers the same real
# question Phase 8 asks ("is the IC consistently positive, not just
# positive on average") — a regime whose block ICs are all small and
# positive is more trustworthy than one whose block ICs swing between
# strongly positive and strongly negative, even with an identical mean.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from scipy import stats
from bot.quant_core.inference import IID_PVALUE_BASIS   # claude code changed: F-02
from bot.quant_core.inference import (CONFIRMATORY_B, CONFIRMATORY_BASIS, INFERENCE_METHOD_VERSION,
                                      block_permutation_p, confirmatory_ic_test, d10_block_length)   # claude code changed: D-2/D-10
import re, zlib   # claude code changed: D-2 — horizon parsing, stable per-cell seeds
from statsmodels.stats.multitest import multipletests

from bot.research.regime_labels import MIN_REGIME_OBS

DEFAULT_N_BLOCKS = 10
DEFAULT_ALPHA = 0.05


def _spearman_ic(feature: np.ndarray, forward_return: np.ndarray) -> "tuple[Optional[float], Optional[float]]":
    valid = np.isfinite(feature) & np.isfinite(forward_return)
    if valid.sum() < 10:
        return None, None
    ic, pvalue = stats.spearmanr(feature[valid], forward_return[valid])
    if ic is None or np.isnan(ic):
        return None, None
    return float(ic), float(pvalue)


def _block_ic_series(sub_df: pd.DataFrame, feature_col: str, forward_return_col: str, n_blocks: int) -> np.ndarray:
    """Splits sub_df (assumed already restricted to one regime cell) into
    up to n_blocks CONTIGUOUS chronological chunks (sub_df must already be
    sorted by time — the caller's responsibility, documented on the public
    function) and returns one IC per block with a usable sample."""
    if len(sub_df) < 20:
        return np.array([])
    # claude code changed: real bug fix — np.array_split(sub_df, ...) does
    # NOT preserve DataFrame type (it coerces via np.asanyarray first,
    # splitting the underlying 2D array), so block[feature_col] then
    # fails with an IndexError. Splitting an index array and slicing with
    # .iloc keeps each block a real DataFrame.
    n_blocks_used = max(1, min(n_blocks, len(sub_df) // 10))
    boundaries = np.array_split(np.arange(len(sub_df)), n_blocks_used)
    ics = []
    for positions in boundaries:
        if len(positions) == 0:
            continue
        block = sub_df.iloc[positions[0]:positions[-1] + 1]
        ic, _ = _spearman_ic(block[feature_col].to_numpy(), block[forward_return_col].to_numpy())
        if ic is not None:
            ics.append(ic)
    return np.array(ics)


@dataclass
class RegimeICCell:
    """One regime cell's full IC + stability report — the mission's exact
    Phase 7/8 deliverable shape."""
    dimension: str    # "OVERALL" | "trend_state" | "volatility_state" | "regime_label"
    regime: str        # "ALL" for the overall row
    n_obs: int
    sufficient_sample: bool
    ic: Optional[float]
    ic_pvalue: Optional[float]
    ic_pvalue_block: Optional[float] = None   # claude code changed: D-2 — the DECISION p (block permutation, block = max(4h, 24), B = 999)
    ic_pvalue_fdr: Optional[float] = None
    passes_fdr: Optional[bool] = None
    block_ic_mean: Optional[float] = None
    block_ic_median: Optional[float] = None
    block_ic_std: Optional[float] = None
    positive_ic_fraction: Optional[float] = None
    n_blocks: int = 0
    ic_ci_low: Optional[float] = None
    ic_ci_high: Optional[float] = None

    def to_dict(self) -> Dict:
        return {
            "dimension": self.dimension, "regime": self.regime,
            "n_obs": self.n_obs, "sufficient_sample": self.sufficient_sample,
            "ic": round(self.ic, 6) if self.ic is not None else None,
            "ic_pvalue": self.ic_pvalue, "ic_pvalue_fdr": self.ic_pvalue_fdr, "passes_fdr": self.passes_fdr,
            "pvalue_basis": IID_PVALUE_BASIS,   # claude code changed: F-02 — analytic Spearman p assumes independence (descriptive)
            "ic_pvalue_block": self.ic_pvalue_block, "decision_pvalue_basis": CONFIRMATORY_BASIS,   # claude code changed: D-2
            "block_ic_mean": round(self.block_ic_mean, 6) if self.block_ic_mean is not None else None,
            "block_ic_median": round(self.block_ic_median, 6) if self.block_ic_median is not None else None,
            "block_ic_std": round(self.block_ic_std, 6) if self.block_ic_std is not None else None,
            "positive_ic_fraction": self.positive_ic_fraction,
            "n_blocks": self.n_blocks,
            "ic_ci_low": self.ic_ci_low, "ic_ci_high": self.ic_ci_high,
        }


def _horizon_from_col(forward_return_col: str) -> int:   # claude code changed: D-2 — delegates to the shared parser
    from bot.quant_core.inference import horizon_from_label
    return horizon_from_label(forward_return_col)


def _cell_seed(seed: int, dimension: str, regime) -> int:   # claude code changed: D-2 — reproducible, recorded
    return (int(seed) * 1_000_003 + zlib.crc32(f"{dimension}|{regime}".encode())) % (2 ** 32)


def _build_cell(dimension: str, regime: str, sub_df: pd.DataFrame, feature_col: str, forward_return_col: str, min_obs: int, n_blocks: int,
                horizon: int = 1, n_perm: int = CONFIRMATORY_B, seed: int = 0) -> RegimeICCell:   # claude code changed: D-2 — horizon/n_perm/seed
    n_obs = len(sub_df)
    sufficient = n_obs >= min_obs
    ic, pvalue = _spearman_ic(sub_df[feature_col].to_numpy(), sub_df[forward_return_col].to_numpy()) if sufficient else (None, None)
    p_block = None
    if sufficient:   # claude code changed: D-2 — confirmatory block permutation on the chronologically ordered cell
        ordered = sub_df.sort_index()
        res = confirmatory_ic_test(ordered[feature_col].to_numpy(), ordered[forward_return_col].to_numpy(), horizon,
                                   seed=_cell_seed(seed, dimension, regime), n_perm=n_perm)
        p_block = res["p_value"]

    block_ics = _block_ic_series(sub_df.sort_index(), feature_col, forward_return_col, n_blocks) if sufficient else np.array([])
    block_mean = float(np.mean(block_ics)) if len(block_ics) else None
    block_median = float(np.median(block_ics)) if len(block_ics) else None
    block_std = float(np.std(block_ics, ddof=1)) if len(block_ics) >= 2 else None
    positive_frac = float(np.mean(block_ics > 0)) if len(block_ics) else None

    ci_low = ci_high = None
    if len(block_ics) >= 2 and block_std is not None:
        margin = 1.96 * block_std / np.sqrt(len(block_ics))
        ci_low, ci_high = block_mean - margin, block_mean + margin

    return RegimeICCell(
        dimension=dimension, regime=regime, n_obs=n_obs, sufficient_sample=sufficient,
        ic=ic, ic_pvalue=pvalue, ic_pvalue_block=p_block,   # claude code changed: D-2
        block_ic_mean=block_mean, block_ic_median=block_median, block_ic_std=block_std,
        positive_ic_fraction=positive_frac, n_blocks=len(block_ics),
        ic_ci_low=ci_low, ic_ci_high=ci_high,
    )


@dataclass
class RegimeConditionalICReport:
    feature_col: str
    forward_return_col: str
    fdr_family_definition: str
    cells: List[RegimeICCell] = field(default_factory=list)
    n_tested: int = 0
    n_significant_raw: int = 0
    n_surviving_fdr: int = 0
    alpha: float = DEFAULT_ALPHA

    def to_dict(self) -> Dict:
        return {
            "feature_col": self.feature_col, "forward_return_col": self.forward_return_col,
            "fdr_family_definition": self.fdr_family_definition,
            "alpha": self.alpha,
            "n_tested": self.n_tested, "n_significant_raw": self.n_significant_raw, "n_surviving_fdr": self.n_surviving_fdr,
            "cells": [c.to_dict() for c in self.cells],
        }

    def cell(self, dimension: str, regime: str) -> Optional[RegimeICCell]:
        for c in self.cells:
            if c.dimension == dimension and c.regime == regime:
                return c
        return None


def compute_regime_conditional_ic(
    df: pd.DataFrame,
    feature_col: str,
    forward_return_col: str,
    regime_dimensions: Sequence[str],
    min_obs: int = MIN_REGIME_OBS,
    alpha: float = DEFAULT_ALPHA,
    n_blocks: int = DEFAULT_N_BLOCKS,
    horizon_candles: Optional[int] = None,   # claude code changed: D-2 — label horizon (parsed from forward_return_col if None; fails closed)
    n_perm: int = CONFIRMATORY_B,            # claude code changed: D-3 — B = 999
    seed: int = 0,                           # claude code changed: D-2 — recorded for reproducibility
) -> RegimeConditionalICReport:
    """
    Parameters
    ----------
    df : DataFrame already containing feature_col, forward_return_col, and
        every column named in regime_dimensions (e.g. the caller has
        already .join()'d bot.research.regime_labels.compute_regime_labels()
        output onto their feature panel). A DatetimeIndex is required so
        block_ic_series can sort chronologically within each regime cell.
    regime_dimensions : e.g. ["trend_state", "volatility_state", "regime_label"]
        Each is tested as its OWN set of cells — Phase 7's "don't explode
        combinations": trend alone (<=3 cells) + vol alone (<=3 cells) +
        the combined cross (<=9 cells) = at most 15 regime cells, plus the
        1 overall row, all in ONE family for this feature/horizon.
    """
    for col in (feature_col, forward_return_col, *regime_dimensions):
        if col not in df.columns:
            raise ValueError(f"compute_regime_conditional_ic requires column {col!r}")

    working = df[[feature_col, forward_return_col, *regime_dimensions]].copy()
    horizon = int(horizon_candles) if horizon_candles is not None else _horizon_from_col(forward_return_col)   # claude code changed: D-2
    kw = dict(horizon=horizon, n_perm=n_perm, seed=seed)
    cells: List[RegimeICCell] = [_build_cell("OVERALL", "ALL", working, feature_col, forward_return_col, min_obs, n_blocks, **kw)]

    for dimension in regime_dimensions:
        valid = working.dropna(subset=[dimension])
        for regime in sorted(valid[dimension].unique()):
            sub_df = valid[valid[dimension] == regime]
            cells.append(_build_cell(dimension, regime, sub_df, feature_col, forward_return_col, min_obs, n_blocks, **kw))

    # claude code changed: FDR applied ONCE across every cell in this
    # family that produced a real p-value (sufficient sample) — a cell
    # skipped for insufficient sample is never silently treated as
    # "not significant," it's excluded from the family entirely, exactly
    # like every other engine in this codebase treats an untestable cell.
    # claude code changed: D-2 — the family correction runs on the BLOCK-PERMUTATION p (decision), never the IID p.
    testable = [c for c in cells if c.ic_pvalue_block is not None]
    if testable:
        raw_pvalues = [c.ic_pvalue_block for c in testable]
        reject, adjusted, _, _ = multipletests(raw_pvalues, alpha=alpha, method="fdr_bh")
        for cell, adj_p, passed in zip(testable, adjusted, reject):
            cell.ic_pvalue_fdr = float(adj_p)
            cell.passes_fdr = bool(passed)

    n_significant_raw = sum(1 for c in testable if c.ic_pvalue_block < alpha)   # claude code changed: D-2
    n_surviving_fdr = sum(1 for c in testable if c.passes_fdr)

    return RegimeConditionalICReport(
        feature_col=feature_col, forward_return_col=forward_return_col,
        fdr_family_definition=(
            f"one family = every regime cell (across dimensions {list(regime_dimensions)}, "
            f"plus the OVERALL row) tested for feature={feature_col!r}, horizon={forward_return_col!r} — "
            f"{len(testable)} cells with sufficient sample corrected together via Benjamini-Hochberg FDR on "
            f"block-permutation p-values (D-2: block = {d10_block_length(horizon)}, B = {n_perm}, seed = {seed}, "
            f"{INFERENCE_METHOD_VERSION})"
        ),
        cells=cells, n_tested=len(testable), n_significant_raw=n_significant_raw, n_surviving_fdr=n_surviving_fdr,
        alpha=alpha,
    )


def test_regime_interaction(
    df: pd.DataFrame, feature_col: str, forward_return_col: str, regime_col: str, min_obs_per_group: int = 30,
    horizon_candles: Optional[int] = None,   # claude code changed: D-2 — label horizon (parsed if None; fails closed)
    n_perm: int = CONFIRMATORY_B,            # claude code changed: D-3 — B = 999
    seed: int = 0,                           # claude code changed: D-2 — recorded
) -> Dict:
    """
    Phase 10 — does the feature-return relationship ACTUALLY differ by regime, tested directly as a
    nested-model comparison:
        restricted: forward_return ~ feature + C(regime)
        full:       forward_return ~ feature * C(regime)

    claude code changed: owner decision D-2 — the DECISION p is now a Freedman–Lane block permutation test
    (the standard permutation test of a partial effect): residuals of the RESTRICTED model are permuted in
    contiguous chronological blocks of max(4h, 24) candles (D-10) and added back to its fitted values; the
    F statistic is recomputed for each of B replicates; p = (k+1)/(B+1). The classical ANOVA F-test p
    (iid, homoskedastic errors — invalid under overlapping labels, F-02) is kept ONLY as a descriptive field.
    """
    clean = df[[feature_col, forward_return_col, regime_col]].dropna().sort_index()
    clean = clean.rename(columns={feature_col: "_feature", forward_return_col: "_fwd_ret", regime_col: "_regime"})

    group_counts = clean["_regime"].value_counts()
    usable_groups = group_counts[group_counts >= min_obs_per_group].index
    clean = clean[clean["_regime"].isin(usable_groups)]

    if clean["_regime"].nunique() < 2 or len(clean) < min_obs_per_group * 2:
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "reason": f"need >= 2 regimes with >= {min_obs_per_group} observations each; got {clean['_regime'].nunique()} usable regime(s), n={len(clean)}",
            "f_statistic": None, "p_value": None, "interaction_significant": None, "n_obs": len(clean),
        }

    # Outside the try: an unknown horizon is a caller error and must raise (fail closed), not read as MODEL_FAILED.
    horizon = int(horizon_candles) if horizon_candles is not None else _horizon_from_col(forward_return_col)
    block = d10_block_length(horizon)
    try:
        y = clean["_fwd_ret"].to_numpy(float)
        x = clean["_feature"].to_numpy(float)
        dummies = pd.get_dummies(clean["_regime"], drop_first=True, dtype=float).to_numpy()
        Xr = np.column_stack([np.ones(len(y)), x, dummies])
        Xf = np.column_stack([Xr, dummies * x[:, None]])
        if len(y) < 2 * block:
            return {"status": "INSUFFICIENT_SAMPLE", "reason": f"need >= 2 blocks of {block}; n = {len(y)}",
                    "f_statistic": None, "p_value": None, "interaction_significant": None, "n_obs": len(clean)}
        Qr, _ = np.linalg.qr(Xr)
        Qf, _ = np.linalg.qr(Xf)
        df1, df2 = Xf.shape[1] - Xr.shape[1], len(y) - Xf.shape[1]
        if df1 < 1 or df2 < 1:
            raise ValueError(f"degenerate design: df1={df1}, df2={df2}")

        def f_stat(v):
            rss_r = float(np.sum((v - Qr @ (Qr.T @ v)) ** 2))
            rss_f = float(np.sum((v - Qf @ (Qf.T @ v)) ** 2))
            return ((rss_r - rss_f) / df1) / (rss_f / df2)

        f_obs = f_stat(y)
        fitted_r = Qr @ (Qr.T @ y)
        resid_r = y - fitted_r
        nb = int(np.ceil(len(y) / block))
        blocks = [resid_r[i * block:(i + 1) * block] for i in range(nb)]
        rng = np.random.default_rng(seed)
        k = 0
        for _ in range(n_perm):
            e = np.concatenate([blocks[i] for i in rng.permutation(nb)])[:len(y)]
            k += f_stat(fitted_r + e) >= f_obs
        p_value = (k + 1) / (n_perm + 1)
        from scipy.stats import f as f_dist
        p_iid = float(f_dist.sf(f_obs, df1, df2))
    except Exception as e:
        return {
            "status": "MODEL_FAILED", "reason": f"{type(e).__name__}: {e}",
            "f_statistic": None, "p_value": None, "interaction_significant": None, "n_obs": len(clean),
        }

    return {
        "status": "OK",
        "f_statistic": float(f_obs), "p_value": float(p_value),
        "interaction_significant": bool(p_value < 0.05),
        "pvalue_basis": "FREEDMAN_LANE_BLOCK_PERMUTATION_D2",   # claude code changed: D-2 — the decision p
        "block": block, "n_perm": int(n_perm), "seed": int(seed), "method_version": INFERENCE_METHOD_VERSION,
        "p_value_iid_anova": p_iid, "iid_basis": IID_PVALUE_BASIS,   # descriptive only (F-02)
        "n_obs": len(clean), "n_regimes_tested": int(clean["_regime"].nunique()),
        "regimes_tested": sorted(clean["_regime"].unique().tolist()),
    }
