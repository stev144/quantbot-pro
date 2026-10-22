# bot/research/dca_wf_validator.py
"""
DCA Walk-Forward Strategy Validator — Type B OOS.

Tests whether the DCA strategy's cycle-level P&L holds up across
non-overlapping time folds. Complements the feature-level Type A validation
in dca_feature_validator.py.

Design
------
No "training" fold: the ladder spec is fixed from the calibration period
(same as run_ranging_paper_simulation). Walk-forward means:
  "does the strategy, configured as-is, produce positive P&L across
   independent time windows?"

Folds: N non-overlapping windows of FOLD_CANDLES each, stepping from fold 1
test start.  WARMUP_CANDLES are prepended to each fold for ADX/ATR indicator
warmup and are excluded from fold P&L accounting.

Statistics
----------
Primary  : binomial sign test on fold P&Ls (k wins out of N eligible folds,
           H0: p = 0.5, one-sided H1: p > 0.5).
Secondary: one-sample t-test on fold P&Ls (H0: mean = 0).
Both computed per-pair and pooled across all pairs.

Governance
----------
IS_PAPER_ONLY = True — constant, never set to False.
Results are descriptive / evidence-gathering only.
QC-2 holdout is sealed until 2027-01-17; this validator uses the same
pre-holdout simulation range as the DCA Engine page and does NOT consume
any new sealed data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

logger = logging.getLogger(__name__)

# ── G5: paper-only sentinel ───────────────────────────────────────────────────
IS_PAPER_ONLY: bool = True  # claude code changed: governance constant — never False

# ── fold configuration ────────────────────────────────────────────────────────
FOLD_CANDLES:      int = 4_380   # ~6 months of 1h candles
WARMUP_CANDLES:    int = 100     # indicator warmup prepended to each fold
MIN_CYCLES_FOLD:   int = 3       # fold excluded from stats if fewer cycles
ALPHA:             float = 0.05  # significance threshold

# ── FX seals: strip every row at/after the earliest active FX holdout boundary ──
from bot.research.fx_holdout_guard import FX_RESEARCH_DATA_CUTOFF  # claude code changed: 2026-10-21 — single source; v7 (2026-10-16 08:00, EUR/USD+USD/CHF) precedes QC-2 (2026-10-19)
QC2_HOLDOUT_START = FX_RESEARCH_DATA_CUTOFF  # claude code changed: kept name for callers; now the stricter combined cutoff

# ── simulation parameters (mirror QC-1 paper engine constants) ────────────────
_INITIAL_USDT       = 3_000.0  # $100 × 30× leverage
_PROFIT_ACTIVATION  = 0.005    # 0.5%
_TRAILING_PCT       = 0.002    # 0.2%
_MAX_CYCLE_CANDLES  = 72
_MAE_HORIZON        = 6        # QC-1 spec horizon


# ── result dataclasses ────────────────────────────────────────────────────────

@dataclass
class FoldResult:
    """Metrics for one walk-forward test fold."""
    fold_num:     int
    pair:         str
    start_ts:     str
    end_ts:       str
    n_cycles:     int
    n_wins:       int
    win_rate:     float          # fraction 0–1
    total_pnl:    float          # sum of cycle profit_usdt in this fold
    avg_pnl:      float          # mean cycle P&L
    eligible:     bool           # True if n_cycles >= MIN_CYCLES_FOLD


@dataclass
class PairWFResult:
    """Walk-forward result for one pair."""
    pair:          str
    n_folds:       int           # total folds generated
    n_eligible:    int           # folds with >= MIN_CYCLES_FOLD cycles
    fold_results:  List[FoldResult]
    # Binomial sign test on eligible fold P&Ls
    n_pos_folds:   int           # folds with total_pnl > 0
    binom_pval:    float         # one-sided p-value (H1: p > 0.5)
    binom_sig:     bool          # True if binom_pval < ALPHA
    # One-sample t-test on eligible fold P&Ls (H0: mean = 0)
    ttest_stat:    float
    ttest_pval:    float         # two-sided; divide by 2 for one-sided
    ttest_sig:     bool          # two-sided p < ALPHA
    mean_fold_pnl: float
    std_fold_pnl:  float


@dataclass
class DCAWalkForwardResult:
    """Full walk-forward validation result across all QC pairs."""
    pair_results:     List[PairWFResult]
    # Pooled statistics across all pairs (all eligible folds combined)
    n_pairs:          int
    n_eligible_total: int        # total eligible folds across all pairs
    n_pos_total:      int        # folds with positive P&L
    pooled_binom_pval: float
    pooled_binom_sig:  bool
    pooled_ttest_stat: float
    pooled_ttest_pval: float
    pooled_ttest_sig:  bool
    pooled_mean_pnl:   float
    pooled_std_pnl:    float
    is_paper_only:     bool = True  # G5: always True


# ── fold builder ──────────────────────────────────────────────────────────────

def _build_folds(
    sim_df: pd.DataFrame,
    fold_candles: int = FOLD_CANDLES,
) -> List[Tuple[int, int]]:
    """
    Divide sim_df into non-overlapping test windows of fold_candles each.

    Returns list of (start_idx, end_idx) index pairs into sim_df.
    Partial folds at the end are discarded.
    """  # claude code changed: new — non-overlapping fold index builder
    n = len(sim_df)
    folds = []
    i = 0
    while i + fold_candles <= n:
        folds.append((i, i + fold_candles))
        i += fold_candles
    return folds


# ── per-fold simulation ───────────────────────────────────────────────────────

def _run_fold(
    sim_df: pd.DataFrame,
    fold_start: int,
    fold_end: int,
    spec,
    pair_spec,
    fold_num: int,
    pair: str,
    warmup: int = WARMUP_CANDLES,
) -> FoldResult:
    """
    Run simulation on one fold window.

    Prepends WARMUP_CANDLES of data before fold_start for indicator warmup.
    Only cycles whose entry_candle falls within [warmup, warmup+fold_candles)
    are counted in fold metrics.
    """  # claude code changed: new — per-fold simulation runner with warmup isolation
    from bot.research.fx_fold_runner import _simulate_data_derived_next_open  # deferred

    # Slice with warmup prepended
    actual_start = max(0, fold_start - warmup)
    warmup_used  = fold_start - actual_start
    fold_df      = sim_df.iloc[actual_start:fold_end].copy()

    start_ts = str(sim_df.index[fold_start])
    end_ts   = str(sim_df.index[fold_end - 1])

    # Cost parameters from ForexInstrumentSpec
    spread_pips    = getattr(pair_spec, "spread_typical_pips", None) or 0.0
    commission_lot = getattr(pair_spec, "commission_per_lot", None)
    swap_long_pips = getattr(pair_spec, "swap_long", None) or 0.0

    try:
        trades_raw, _ = _simulate_data_derived_next_open(
            df=fold_df,
            spec=spec,
            initial_usdt=_INITIAL_USDT,
            profit_activation_pct=_PROFIT_ACTIVATION,
            trailing_pct=_TRAILING_PCT,
            spread_pips=spread_pips if spread_pips else None,
            commission_per_lot=commission_lot,
            swap_long_pips=swap_long_pips if swap_long_pips else None,
            swap_short_pips=None,
            lot_size=100_000.0,
            max_cycle_candles=_MAX_CYCLE_CANDLES,
        )
    except Exception as exc:
        logger.warning("dca_wf: fold %d/%s simulation failed: %s", fold_num, pair, exc)
        return FoldResult(
            fold_num=fold_num, pair=pair, start_ts=start_ts, end_ts=end_ts,
            n_cycles=0, n_wins=0, win_rate=0.0, total_pnl=0.0,
            avg_pnl=0.0, eligible=False,
        )

    # Only include closed cycles that entered after the warmup window
    fold_trades = [
        t for t in trades_raw
        if t.get("exit_reason") != "DATA_END_STILL_OPEN"
    ]
    # The simulation loop uses integer candle indices; entry_candle is
    # relative to fold_df[0]. Warmup occupies [0, warmup_used), fold window
    # occupies [warmup_used, warmup_used + fold_candles).
    # We use entry_time to exclude warmup-period entries.
    fold_open_ts  = pd.Timestamp(start_ts)
    fold_trades   = [
        t for t in fold_trades
        if pd.Timestamp(t["entry_time"]) >= fold_open_ts
    ]

    n_cycles = len(fold_trades)
    if n_cycles == 0:
        return FoldResult(
            fold_num=fold_num, pair=pair, start_ts=start_ts, end_ts=end_ts,
            n_cycles=0, n_wins=0, win_rate=0.0, total_pnl=0.0,
            avg_pnl=0.0, eligible=False,
        )

    profits   = [float(t["profit_usdt"]) for t in fold_trades]
    n_wins    = sum(1 for p in profits if p > 0)
    total_pnl = sum(profits)

    return FoldResult(
        fold_num=fold_num,
        pair=pair,
        start_ts=start_ts,
        end_ts=end_ts,
        n_cycles=n_cycles,
        n_wins=n_wins,
        win_rate=n_wins / n_cycles,
        total_pnl=total_pnl,
        avg_pnl=total_pnl / n_cycles,
        eligible=(n_cycles >= MIN_CYCLES_FOLD),
    )  # claude code changed: new — per-fold result builder


# ── sign test + t-test helpers ────────────────────────────────────────────────

def _sign_test(pnls: List[float]) -> Tuple[int, float]:
    """
    One-sided binomial sign test: H1 = majority of folds are positive.

    Returns (n_positive, p_value).
    """  # claude code changed: new — binomial sign test on fold P&Ls; uses binomtest (scipy 1.7+)
    n_pos = sum(1 for p in pnls if p > 0)
    n     = len(pnls)
    if n == 0:
        return 0, 1.0
    # one-sided: P(X >= n_pos) under H0 (p=0.5)
    result = scipy_stats.binomtest(n_pos, n, p=0.5, alternative="greater")
    return n_pos, float(result.pvalue)


def _ttest(pnls: List[float]) -> Tuple[float, float]:
    """One-sample t-test on fold P&Ls (H0: mean = 0, two-sided)."""  # claude code changed: new
    if len(pnls) < 2:
        return float("nan"), 1.0
    stat, pval = scipy_stats.ttest_1samp(pnls, popmean=0.0)
    return float(stat), float(pval)


# ── main validator ────────────────────────────────────────────────────────────

def validate_dca_walk_forward(
    pairs: Optional[List[str]] = None,
    fold_candles: int = FOLD_CANDLES,
    _sim_df_override: Optional[Dict[str, pd.DataFrame]] = None,  # injectable for tests
) -> DCAWalkForwardResult:
    """
    Run walk-forward validation across QC portfolio pairs.

    Parameters
    ----------
    pairs : subset of QC_PORTFOLIO_SPECS to process (default = all)
    fold_candles : number of 1h candles per test fold (default ~6 months)
    _sim_df_override : {pair: df} to inject pre-sliced DataFrames (tests only)

    Returns
    -------
    DCAWalkForwardResult with per-pair and pooled statistics.
    """  # claude code changed: new — main Type B OOS walk-forward validator
    from bot.research.fx_fold_splitter import make_fx_splits
    from bot.research.forex_instrument_spec import QC_PORTFOLIO_SPECS, QC_PORTFOLIO_CSV_FILES
    from bot.research.fx_fold_runner import (
        compute_wilder_adx,
        compute_causal_atr_series,
        build_ladder_spec_from_analysis,
    )
    from bot.research.fx_movement_analysis import analyse_pair
    from bot.paper_trading.ranging_paper_engine import _ROOT  # reuse ROOT path

    DIRECTION = "LONG"
    pair_results: List[PairWFResult] = []
    all_eligible_pnls: List[float]   = []

    target_pairs = list(QC_PORTFOLIO_SPECS.keys()) if pairs is None else pairs

    for pair in target_pairs:
        pair_spec = QC_PORTFOLIO_SPECS.get(pair)
        if pair_spec is None:
            logger.warning("dca_wf: %s not in QC_PORTFOLIO_SPECS, skipping", pair)
            continue

        try:
            # ── load data ──────────────────────────────────────────────────
            if _sim_df_override and pair in _sim_df_override:
                sim_df = _sim_df_override[pair]
                # build a minimal spec from the full override df
                analysis = analyse_pair(
                    sim_df.reset_index(drop=False),
                    pip_size=pair_spec.pip_size,
                    pair=pair,
                )
                spec = build_ladder_spec_from_analysis(
                    analysis, DIRECTION, pair_spec.pip_size, pair,
                    mae_horizon=_MAE_HORIZON,
                )
            else:
                csv_file = QC_PORTFOLIO_CSV_FILES[pair]
                df_full = pd.read_csv(
                    _ROOT / "data" / "forex" / csv_file,
                    parse_dates=["timestamp"],
                )
                df_full = df_full.sort_values("timestamp").reset_index(drop=True)
                df_full = df_full[pd.to_datetime(df_full["timestamp"], utc=True) < QC2_HOLDOUT_START].reset_index(drop=True)  # claude code changed: enforce QC-2 seal + freeze split boundaries against hourly appends
                splits  = make_fx_splits(df_full)

                # spec from calibration data only (same as paper engine)
                analysis = analyse_pair(
                    splits.calibration.reset_index(drop=True),
                    pip_size=pair_spec.pip_size,
                    pair=pair,
                )
                spec = build_ladder_spec_from_analysis(
                    analysis, DIRECTION, pair_spec.pip_size, pair,
                    mae_horizon=_MAE_HORIZON,
                )

                # sim range: fold 1 test start → QC2_HOLDOUT_START (exclusive)  # claude code changed: was "end of holdout" — data now capped at QC-2 seal
                fold1_test_start = splits.folds[0].test["timestamp"].iloc[0]
                sim_df = df_full[df_full["timestamp"] >= fold1_test_start].copy()
                sim_df["timestamp"] = pd.to_datetime(sim_df["timestamp"], utc=True)
                sim_df = sim_df.set_index("timestamp")

            if len(sim_df) < fold_candles + WARMUP_CANDLES:
                logger.warning(
                    "dca_wf: %s has only %d sim candles (< %d + %d warmup), skipping",
                    pair, len(sim_df), fold_candles, WARMUP_CANDLES,
                )
                continue

            # ── build folds and run ────────────────────────────────────────
            fold_indices = _build_folds(sim_df, fold_candles=fold_candles)
            logger.info("dca_wf: %s — %d folds (%d candles each)", pair, len(fold_indices), fold_candles)

            fold_results: List[FoldResult] = []
            for fold_num, (fstart, fend) in enumerate(fold_indices, start=1):
                fr = _run_fold(
                    sim_df=sim_df,
                    fold_start=fstart,
                    fold_end=fend,
                    spec=spec,
                    pair_spec=pair_spec,
                    fold_num=fold_num,
                    pair=pair,
                )
                fold_results.append(fr)
                logger.debug(
                    "dca_wf:   fold %d  cycles=%d  pnl=%.4f  eligible=%s",
                    fold_num, fr.n_cycles, fr.total_pnl, fr.eligible,
                )

            # ── statistics on eligible folds ──────────────────────────────
            eligible = [fr for fr in fold_results if fr.eligible]
            pnls     = [fr.total_pnl for fr in eligible]
            n_pos, binom_pval = _sign_test(pnls)
            tstat, tpval      = _ttest(pnls)
            mean_pnl = float(np.mean(pnls)) if pnls else float("nan")
            std_pnl  = float(np.std(pnls, ddof=1)) if len(pnls) >= 2 else float("nan")

            all_eligible_pnls.extend(pnls)

            pair_results.append(PairWFResult(
                pair=pair,
                n_folds=len(fold_results),
                n_eligible=len(eligible),
                fold_results=fold_results,
                n_pos_folds=n_pos,
                binom_pval=binom_pval,
                binom_sig=(binom_pval < ALPHA),
                ttest_stat=tstat,
                ttest_pval=tpval,
                ttest_sig=(tpval < ALPHA),
                mean_fold_pnl=mean_pnl,
                std_fold_pnl=std_pnl,
            ))

            logger.info(
                "dca_wf: %s — %d/%d eligible folds positive, binom p=%.4f, t-test p=%.4f",
                pair, n_pos, len(eligible), binom_pval, tpval,
            )

        except Exception as exc:
            logger.error("dca_wf: failed for %s: %s", pair, exc, exc_info=True)

    # ── pooled statistics ─────────────────────────────────────────────────────
    n_eligible_total = len(all_eligible_pnls)
    n_pos_total, pooled_binom_pval = _sign_test(all_eligible_pnls)
    pooled_tstat, pooled_tpval     = _ttest(all_eligible_pnls)
    pooled_mean = float(np.mean(all_eligible_pnls)) if all_eligible_pnls else float("nan")
    pooled_std  = float(np.std(all_eligible_pnls, ddof=1)) if len(all_eligible_pnls) >= 2 else float("nan")

    logger.info(
        "dca_wf: POOLED — %d eligible folds, %d positive, binom p=%.4f, t-test p=%.4f",
        n_eligible_total, n_pos_total, pooled_binom_pval, pooled_tpval,
    )

    return DCAWalkForwardResult(
        pair_results=pair_results,
        n_pairs=len(pair_results),
        n_eligible_total=n_eligible_total,
        n_pos_total=n_pos_total,
        pooled_binom_pval=pooled_binom_pval,
        pooled_binom_sig=(pooled_binom_pval < ALPHA),
        pooled_ttest_stat=pooled_tstat,
        pooled_ttest_pval=pooled_tpval,
        pooled_ttest_sig=(pooled_tpval < ALPHA),
        pooled_mean_pnl=pooled_mean,
        pooled_std_pnl=pooled_std,
    )  # claude code changed: new — full Type B OOS walk-forward validator entry point


# ── console report ────────────────────────────────────────────────────────────

def print_wf_report(result: DCAWalkForwardResult) -> None:
    """Print a human-readable walk-forward validation report."""
    print("\n" + "=" * 72)
    print("DCA WALK-FORWARD VALIDATOR — TYPE B OOS REPORT")
    print("=" * 72)
    print(f"IS_PAPER_ONLY : {result.is_paper_only}  (governance constant)")
    print(f"Fold size     : {FOLD_CANDLES:,} candles (~6 months)")
    print(f"Warmup        : {WARMUP_CANDLES} candles (excluded from fold metrics)")
    print(f"Min cycles    : {MIN_CYCLES_FOLD} per fold to be eligible")
    print(f"Alpha         : {ALPHA}")
    print()

    for pr in result.pair_results:
        sig_b = "SIG" if pr.binom_sig else "ns"
        sig_t = "SIG" if pr.ttest_sig else "ns"
        print(f"  {pr.pair:<10}  folds={pr.n_folds}  eligible={pr.n_eligible}  "
              f"pos={pr.n_pos_folds}  mean_pnl={pr.mean_fold_pnl:+.4f}  "
              f"binom_p={pr.binom_pval:.4f}[{sig_b}]  t_p={pr.ttest_pval:.4f}[{sig_t}]")
        for fr in pr.fold_results:
            elig_str = "" if fr.eligible else " [INELIGIBLE]"
            print(f"    fold {fr.fold_num:02d}  {fr.start_ts[:10]}→{fr.end_ts[:10]}  "
                  f"cycles={fr.n_cycles}  wins={fr.n_wins}  pnl={fr.total_pnl:+.4f}{elig_str}")

    print()
    print("POOLED ACROSS ALL PAIRS")
    print(f"  Eligible folds : {result.n_eligible_total}")
    print(f"  Positive folds : {result.n_pos_total}")
    print(f"  Mean fold P&L  : {result.pooled_mean_pnl:+.4f}")
    print(f"  Std fold P&L   : {result.pooled_std_pnl:.4f}")
    sig_b = "SIG" if result.pooled_binom_sig else "ns"
    sig_t = "SIG" if result.pooled_ttest_sig else "ns"
    print(f"  Binomial p     : {result.pooled_binom_pval:.4f}  [{sig_b}]")
    print(f"  T-test p       : {result.pooled_ttest_pval:.4f}  [{sig_t}]")
    print("=" * 72)  # claude code changed: new — console report printer
