"""
bot/research/fx_permutation_test.py

FX DCA block-shuffle permutation test — FX-DCA-PROTOCOL-1-A3 Part 5.

Null model    : BLOCK_SHUFFLE — OHLCV test rows shuffled in contiguous
                blocks of BLOCK_SIZE_CANDLES (48h), preserving each block's
                short-term structure while destroying long-range temporal
                ordering between blocks.

Test statistic: total_realised_pnl — sum of closed-trade P&L (cost-adjusted)
                across all folds.  Win rate is 100% by construction (no stop-
                loss), so it is not a discriminating statistic.  final_equity
                (realised + unrealised MTM) is reported as secondary.

Primary inference: the OVERALL CALIBRATION test — sum of total_realised_pnl
                   across all 5 folds.  Per-fold p-values are also reported.

Stouffer combination: indicative only.  Fold test periods are drawn from the
                   same autocorrelated price series and are NOT independent.
                   The Stouffer p must not be used as the headline result.

Multiple testing (across 5 pairs, per protocol Part 5):
  Bonferroni primary : α_per_pair = 0.01  (family-wise α = 0.05, 5 pairs)
  FDR-BH secondary   : applied to per-pair overall p-values

References: Phipson & Smyth (2010) "+1 correction"; Davison & Hinkley (1997)
"""  # claude code changed: new file — FX-DCA-PROTOCOL-1-A3 Part 5 permutation test

from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats as _scipy_stats

from bot.research.fx_fold_runner import (  # claude code changed: reuse existing simulation; no reimplementation
    FxLadderSpec,
    _simulate_data_derived_next_open,
    compute_equity_stats,
)
from bot.research.permutation_stats import compute_permutation_verdict  # claude code changed: reuse shared p-value math

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

# ── Protocol constants (Part 5 / v3, unchanged) ───────────────────────────────
N_PERMUTATIONS: int = 499             # protocol minimum; smallest p = 1/500 = 0.002  # claude code changed: per protocol Part 5
BLOCK_SIZE_CANDLES: int = 48          # FX DCA avg hold >>144h (3×48h), so adaptive # claude code changed: per protocol Part 5 BLOCK_SHUFFLE
                                       # ceiling applies for all pairs in this study
BONFERRONI_ALPHA_PER_PAIR: float = 0.01   # 5 pairs, family-wise α = 0.05           # claude code changed: per protocol Part 5
N_PAIRS: int = 5                       # EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD # claude code changed: per protocol Part 2


# ── Block shuffle ─────────────────────────────────────────────────────────────

def block_shuffle_ohlcv(
    df: pd.DataFrame,
    block_size: int,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """
    Shuffle the OHLCV columns of df in contiguous blocks of `block_size` rows,
    keeping the DatetimeIndex in its original chronological position.

    All OHLC columns are shuffled in lockstep so every shuffled row is still
    an internally consistent candle (open/high/low/close from the SAME original
    candle — no synthetic candle is created by mixing columns from different
    candles).  Volume is shuffled the same way if present.

    The convention is identical to permutation_test_engine.py's _block_shuffle:
    index stays fixed, content moves.  ATR will be recomputed from the
    shuffled OHLCV by _simulate_data_derived_next_open.
    """  # claude code changed: new — block-shuffle OHLCV while preserving DatetimeIndex
    n = len(df)
    if n == 0 or block_size <= 0:
        return df.copy()
    n_blocks = math.ceil(n / block_size)
    block_bounds = [
        (i * block_size, min((i + 1) * block_size, n)) for i in range(n_blocks)
    ]
    order = rng.permutation(n_blocks)
    shuffled_positions = np.concatenate([
        np.arange(start, end) for start, end in (block_bounds[i] for i in order)
    ])
    shuffled = df.copy()
    for col in ("open", "high", "low", "close", "volume"):
        if col in shuffled.columns:
            shuffled[col] = df[col].to_numpy()[shuffled_positions]
    return shuffled


# ── Prepare a fold's test_df for simulation ────────────────────────────────────

def _prepare_sim_df(test_df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert a test fold DataFrame (with 'timestamp' column or DatetimeIndex)
    into the DatetimeIndex form expected by _simulate_data_derived_next_open.
    """  # claude code changed: new — mirrors run_fx_fold()'s own preparation step
    sim_df = test_df.copy()
    if "timestamp" in sim_df.columns:
        sim_df["timestamp"] = pd.to_datetime(sim_df["timestamp"], utc=True)
        sim_df = sim_df.set_index("timestamp")
    elif not isinstance(sim_df.index, pd.DatetimeIndex):
        raise ValueError("test_df must have a 'timestamp' column or DatetimeIndex")
    return sim_df


def _extract_cost_fields(pair_spec) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """Extract (spread, commission, swap_long, swap_short) from a ForexInstrumentSpec."""  # claude code changed: new
    return (
        getattr(pair_spec, "spread_typical_pips", None),
        getattr(pair_spec, "commission_per_lot", None),
        getattr(pair_spec, "swap_long", None),
        getattr(pair_spec, "swap_short", None),
    )


# ── Single permutation for one fold ───────────────────────────────────────────

def _run_one_permuted_fold(
    sim_df: pd.DataFrame,          # already DatetimeIndex, ready for simulation
    spec: FxLadderSpec,
    spread: Optional[float],
    commission: Optional[float],
    swap_long: Optional[float],
    swap_short: Optional[float],
    block_size: int,
    rng: np.random.Generator,
) -> Dict:
    """
    Run ONE permuted simulation on a single fold's test data with a fixed spec.

    Returns dict with total_realised_pnl and final_equity.
    """  # claude code changed: new — single-fold inner loop for permutation test
    shuffled_df = block_shuffle_ohlcv(sim_df, block_size, rng)
    trades, mtm = _simulate_data_derived_next_open(
        shuffled_df, spec,
        spread_pips=spread,
        commission_per_lot=commission,
        swap_long_pips=swap_long,
        swap_short_pips=swap_short,
    )
    closed = [t for t in trades if t.get("exit_reason") != "DATA_END_STILL_OPEN"]
    total_realised = sum(t["profit_usdt"] for t in closed) if closed else 0.0
    eq = compute_equity_stats(mtm, trades)
    return {
        "total_realised_pnl": total_realised,
        "n_closed": len(closed),
        "final_equity": eq["final_equity"],
        "max_drawdown": eq["max_drawdown"],
    }


# ── Stouffer combination (indicative only) ────────────────────────────────────

def _stouffer_combine(p_values: List[float]) -> Tuple[float, float]:
    """
    Combine per-fold one-sided p-values via Stouffer's Z-method.

    INDICATIVE ONLY: fold test periods share the same autocorrelated price
    series and are not statistically independent.  This combined p must not
    be used as the headline result (protocol Part 5).

    Returns (z_combined, p_combined).  NaN if any input p is NaN.
    """  # claude code changed: new — Stouffer combination, marked indicative only per protocol
    clean = [p for p in p_values if p is not None and not math.isnan(p)]
    if not clean:
        return float("nan"), float("nan")
    z_scores = [_scipy_stats.norm.ppf(1.0 - p) for p in clean]
    z_combined = sum(z_scores) / math.sqrt(len(z_scores))
    p_combined = float(1.0 - _scipy_stats.norm.cdf(z_combined))
    return z_combined, p_combined


# ── FDR-BH correction ─────────────────────────────────────────────────────────

def compute_fdr_bh(p_values: List[float], alpha: float = 0.05) -> List[bool]:
    """
    Benjamini-Hochberg FDR correction for a list of p-values.

    Returns a list of booleans (True = reject H0 at FDR level alpha).
    NaN p-values are never rejected.
    """  # claude code changed: new — BH FDR secondary correction per protocol Part 5
    n = len(p_values)
    if n == 0:
        return []
    indexed = [(i, p) for i, p in enumerate(p_values)]
    reject = [False] * n
    valid = [(i, p) for i, p in indexed if p is not None and not math.isnan(p)]
    if not valid:
        return reject
    valid_sorted = sorted(valid, key=lambda x: x[1])
    for rank, (i, p) in enumerate(valid_sorted, start=1):
        if p <= alpha * rank / n:
            reject[i] = True
    # BH is step-down: once we find the largest k with p_k <= alpha*k/n,
    # all hypotheses with rank <= k are rejected.
    last_reject = -1
    for rank, (i, _) in enumerate(valid_sorted, start=1):
        if reject[i]:
            last_reject = rank
    for rank, (i, _) in enumerate(valid_sorted, start=1):
        if rank <= last_reject:
            reject[i] = True
    return reject


# ── Main permutation test runner ──────────────────────────────────────────────

def run_fx_calibration_permutation(
    folds,              # list of (train_df, test_df, fold_num) or FxFold objects
    real_fold_results: List[Dict],  # run_fx_fold() outputs (for specs + real P&Ls)
    pair: str,
    direction: str,
    pip_size: float,
    pair_spec,
    n_permutations: int = N_PERMUTATIONS,
    block_size: int = BLOCK_SIZE_CANDLES,
    random_seed: Optional[int] = None,
) -> Dict:
    """
    Block-shuffle permutation test for one (pair, direction) across all 5
    calibration folds.

    Parameters
    ----------
    folds              : fold objects with .test and .fold_num attributes (from
                         make_fx_splits().folds)
    real_fold_results  : outputs of run_fx_fold() for each fold in `folds`,
                         in the same order — provides the per-fold FxLadderSpec
    pair               : e.g. "EUR/USD"
    direction          : "LONG" or "SHORT"
    pip_size           : instrument pip size
    pair_spec          : ForexInstrumentSpec from MT5_SPECS
    n_permutations     : number of shuffles; protocol minimum = 499
    block_size         : candles per shuffle block; default 48 (48h)
    random_seed        : for reproducibility; None = non-deterministic

    Returns
    -------
    dict with:
      pair, direction, n_permutations, block_size, random_seed
      real_total_pnl                  — sum of closed-trade P&L across all folds
      real_per_fold_pnl               — list of per-fold closed-trade P&L
      overall_p_value                 — PRIMARY: empirical p for real_total_pnl
      overall_percentile              — fraction of shuffles real beat
      per_fold_p_values               — list of 5 per-fold p-values
      per_fold_percentiles            — list of 5 per-fold percentiles
      per_fold_distributions          — list of 5 arrays of permuted P&Ls
      stouffer_z, stouffer_p          — indicative only; NOT primary
      bonferroni_significant          — overall_p_value < BONFERRONI_ALPHA_PER_PAIR
      permuted_total_distribution     — 1-D array of summed permuted P&Ls
      secondary_final_equity_p        — p-value for final_equity test statistic
    """  # claude code changed: new — primary calibration permutation test runner
    rng = np.random.default_rng(random_seed)
    spread, commission, swap_long, swap_short = _extract_cost_fields(pair_spec)

    # Prepare fold test DFs (DatetimeIndex) and extract fixed specs
    sim_dfs: List[pd.DataFrame] = []
    specs: List[FxLadderSpec] = []
    for fold, fold_result in zip(folds, real_fold_results):
        test_df = fold.test  # FxFold.test attribute
        sim_dfs.append(_prepare_sim_df(test_df))
        specs.append(fold_result["spec"])

    # Real test statistics
    real_per_fold_pnl: List[float] = []
    real_per_fold_feq: List[float] = []
    for fold_result in real_fold_results:
        closed = [t for t in fold_result["trades"] if t.get("exit_reason") != "DATA_END_STILL_OPEN"]
        real_per_fold_pnl.append(sum(t["profit_usdt"] for t in closed) if closed else 0.0)
        real_per_fold_feq.append(fold_result["summary"].get("final_equity", 0.0))
    real_total_pnl = sum(real_per_fold_pnl)
    real_total_feq = sum(real_per_fold_feq)

    n_folds = len(sim_dfs)

    # Permuted distributions
    per_fold_permuted_pnl: List[List[float]] = [[] for _ in range(n_folds)]
    per_fold_permuted_feq: List[List[float]] = [[] for _ in range(n_folds)]
    permuted_total_pnl: List[float] = []
    permuted_total_feq: List[float] = []

    logger.info(f"FX permutation test — {pair} {direction}")
    logger.info(f"  Folds: {n_folds}  |  Permutations: {n_permutations}  |  Block: {block_size}h")
    logger.info(f"  Real total realised P&L: {real_total_pnl:+.4f} USD")

    for perm_i in range(1, n_permutations + 1):
        fold_pnl_sum = 0.0
        fold_feq_sum = 0.0
        for fold_idx in range(n_folds):
            result = _run_one_permuted_fold(
                sim_dfs[fold_idx], specs[fold_idx],
                spread, commission, swap_long, swap_short,
                block_size, rng,
            )
            per_fold_permuted_pnl[fold_idx].append(result["total_realised_pnl"])
            per_fold_permuted_feq[fold_idx].append(result["final_equity"])
            fold_pnl_sum += result["total_realised_pnl"]
            fold_feq_sum += result["final_equity"]
        permuted_total_pnl.append(fold_pnl_sum)
        permuted_total_feq.append(fold_feq_sum)

        if perm_i % 50 == 0 or perm_i == n_permutations:
            logger.info(f"  Permutation {perm_i}/{n_permutations} done")

    perm_arr = np.array(permuted_total_pnl, dtype=float)
    perm_feq_arr = np.array(permuted_total_feq, dtype=float)

    # Overall p-values (one-sided: "real beats shuffled", Phipson & Smyth +1 correction)
    overall_p = float((np.sum(perm_arr >= real_total_pnl) + 1) / (len(perm_arr) + 1))
    overall_pct = float(np.mean(perm_arr <= real_total_pnl))
    secondary_feq_p = float((np.sum(perm_feq_arr >= real_total_feq) + 1) / (len(perm_feq_arr) + 1))

    # Per-fold p-values
    per_fold_p: List[float] = []
    per_fold_pct: List[float] = []
    for fold_idx in range(n_folds):
        fold_dist = np.array(per_fold_permuted_pnl[fold_idx], dtype=float)
        real_f = real_per_fold_pnl[fold_idx]
        p = float((np.sum(fold_dist >= real_f) + 1) / (len(fold_dist) + 1))
        pct = float(np.mean(fold_dist <= real_f))
        per_fold_p.append(p)
        per_fold_pct.append(pct)

    # Stouffer (indicative only)
    stouffer_z, stouffer_p = _stouffer_combine(per_fold_p)

    # Bonferroni significance flag (primary per protocol)
    bonferroni_sig = overall_p < BONFERRONI_ALPHA_PER_PAIR

    return {
        "pair": pair,
        "direction": direction,
        "n_permutations": n_permutations,
        "block_size": block_size,
        "random_seed": random_seed,
        # Real test statistics
        "real_total_pnl": real_total_pnl,
        "real_per_fold_pnl": real_per_fold_pnl,
        "real_total_feq": real_total_feq,
        # Primary result (overall calibration)
        "overall_p_value": overall_p,
        "overall_percentile": overall_pct,
        "bonferroni_significant": bonferroni_sig,
        "bonferroni_alpha": BONFERRONI_ALPHA_PER_PAIR,
        # Secondary: final equity
        "secondary_final_equity_p": secondary_feq_p,
        # Per-fold results
        "per_fold_p_values": per_fold_p,
        "per_fold_percentiles": per_fold_pct,
        "per_fold_distributions": [np.array(d) for d in per_fold_permuted_pnl],
        # Stouffer (indicative only — not primary)
        "stouffer_z": stouffer_z,
        "stouffer_p": stouffer_p,
        "stouffer_note": "INDICATIVE ONLY — fold test periods not independent (same autocorrelated series)",
        # Raw distribution (for auditing / FDR across pairs)
        "permuted_total_distribution": perm_arr,
        "permuted_total_feq_distribution": perm_feq_arr,
    }  # claude code changed: new — full calibration permutation verdict


def print_permutation_verdict(verdict: Dict) -> None:
    """Print a human-readable permutation test result."""  # claude code changed: new — readable output
    pair = verdict["pair"]
    direction = verdict["direction"]
    n = verdict["n_permutations"]
    p_floor = 1.0 / (n + 1)
    sep = "=" * 72
    print(f"\n{sep}")
    print(f"  FX PERMUTATION TEST — {pair} {direction}")
    print(f"  FX-DCA-PROTOCOL-1-A3 Part 5 — BLOCK_SHUFFLE  |  N={n}  |  block={verdict['block_size']}h")
    print(f"  Smallest possible p: {p_floor:.4f}  (1 / {n+1})")
    print(sep)
    print(f"\n  Test statistic: total_realised_pnl (sum of closed trades, all folds)")
    print(f"  Real value    : {verdict['real_total_pnl']:+.4f} USD")
    print(f"  Beats         : {verdict['overall_percentile']:.1%} of {n} shuffled replicas")
    print(f"  Overall p     : {verdict['overall_p_value']:.4f}  [PRIMARY]")
    print(f"  Bonferroni    : α = {verdict['bonferroni_alpha']}  →  {'SIGNIFICANT' if verdict['bonferroni_significant'] else 'not significant'}")
    print(f"\n  Secondary (final_equity sum):")
    print(f"    p = {verdict['secondary_final_equity_p']:.4f}")
    print(f"\n  Per-fold p-values (total_realised_pnl):")
    for i, (p, pct, real_f) in enumerate(zip(
        verdict["per_fold_p_values"],
        verdict["per_fold_percentiles"],
        verdict["real_per_fold_pnl"],
    ), start=1):
        flag = " *" if p < BONFERRONI_ALPHA_PER_PAIR else ""
        print(f"    Fold {i}: real={real_f:+.4f} USD  beats {pct:.1%}  p={p:.4f}{flag}")
    print(f"\n  Stouffer (INDICATIVE ONLY — folds not independent):")
    print(f"    z = {verdict['stouffer_z']:.3f}   p = {verdict['stouffer_p']:.4f}")
    print(f"    {verdict['stouffer_note']}")
    print(sep)
