# ============================================================
# bot/research/oos_validator.py
# claude code changed: new file — generic, reusable, leakage-resistant
# Out-of-Sample (OOS) / walk-forward validation infrastructure.
#
# WHY THIS EXISTS (re-verified by inspection, not assumed — Rule 1 of
# this mission): bot/research/walk_forward_engine.py is real, tested,
# and genuinely useful, but it is hard-coupled to the Kalman-pairs
# pipeline — it imports EntryExitEngine/KalmanPositionSizer directly,
# builds folds from calendar YEARS (pd.DateOffset(years=...)), and its
# per-fold parameter re-estimation (half-life OU calibration) is spread
# geometry specific to a Kalman z-score series. Every phase script this
# project has actually run for feature-level research (Phase 2D trade
# flow, 2E derivatives, 2F order-book) instead hand-rolled its own ad
# hoc train/test split — exactly the fragility the platform audit's
# statistical trust review flagged as the weakest remaining link.
#
# THIS MODULE extracts the one genuinely reusable idea from
# walk_forward_engine.py's _generate_folds() — anchored/expanding folds
# built from the DATA'S OWN span, never hardcoded, with the test window
# clipped (never padded) at the data's end — and generalizes it to work
# on any timestamp-indexed DataFrame, independent of asset, strategy, or
# feature. It does NOT replace walk_forward_engine.py (Section 11 of
# this mission: no regression, no deletion of working infrastructure)
# — that engine keeps doing exactly what it already does for Kalman
# pairs. This module is the primitive every OTHER research track (OHLCV
# feature validation, cross-sectional, future ML) can share instead of
# each hand-rolling its own split, which is what phase2d/e/f_analyze.py
# were doing before this.
#
# SCOPE — implemented THIS session vs. designed-for-later (Section 2 of
# the mission explicitly allows this: build the API so future tracks can
# plug in without a redesign, without building all of them now):
#   - Fully implemented: Type A (feature -> future return), including
#     genuine multi-asset / cross-sectional-shaped input (long-format
#     timestamp/asset/feature/label), evaluated with pooled-per-fold IC.
#   - Designed for, not built: Type B (strategy signal -> trade outcome)
#     and Type D (ML fit/predict) share the exact same FoldSpec/
#     WalkForwardConfig/OOSResult primitives and the same fit_fn/
#     predict_fn plug points Type A already uses — a Type B/D evaluator
#     is a thin wrapper around build_folds(), not a redesign. Full
#     long/short cross-sectional PORTFOLIO construction (ranking assets
#     into a tradeable portfolio) is explicitly out of scope per this
#     mission's own Section 10 ("do not build the entire cross-sectional
#     strategy engine").
# ============================================================

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)

METHODOLOGY_VERSION = "oos_validator/1.2.0"   # claude code changed: bumped 1.1.0 -> 1.2.0 for the Type C cross-sectional ranking evaluator (evaluate_cross_sectional_oos) and its own aggregation methodology ("cross_sectional_ranking" branch) — again purely additive; Type A and Type B's own semantics are byte-for-byte unchanged (see their pre-existing, still-green tests). Bump again only on a further real semantic change.


# ═══════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class WalkForwardConfig:
    """
    Every parameter that determines fold boundaries, explicit — Section 4's
    exact requirement ("do not silently assume 70/30 splitting is
    sufficient... the API should make these parameters explicit").

    mode:
        "expanding" — train always starts at the data's first observation
            and grows with every fold (identical semantics to
            walk_forward_engine.py's _generate_folds(), generalized from
            calendar years to a row-count "periods" unit so it works on
            any timestamp frequency, not just hourly Kalman candles).
        "rolling"   — train is a FIXED-size window that slides forward
            with the test window (Section 3's "optional rolling window").

    All window/step/horizon/purge/embargo values are in PERIODS — a count
    of rows in the caller's own sorted, deduplicated timestamp index, not
    calendar time. This matches how this project already expresses
    horizon (forward_return_4h = 4 candles ahead, already an integer bar
    count) and works identically whether the index is hourly candles,
    daily bars, or anything else — calendar-time folding (like
    walk_forward_engine.py's DateOffset(years=...)) only makes sense for
    a SPECIFIC known frequency, which a generic validator cannot assume.
    """
    mode: str = "expanding"                 # "expanding" | "rolling"
    min_train_periods: int = 500            # first fold's train size (expanding) or every fold's train size (rolling)
    test_periods: int = 168                 # length of each OOS test window, in periods (default: 168 = 1 week of hourly bars)
    step_periods: Optional[int] = None      # how far the window advances each fold; None = test_periods (non-overlapping test windows, matches walk_forward_engine.py's own train_end = test_end pattern)
    horizon: int = 1                        # the forward-return label horizon, in periods — THE mechanical reason purge is needed (Section 4)
    purge_periods: Optional[int] = None     # None = horizon (the scientifically-defensible default per Section 4 — see build_folds() docstring for why)
    embargo_periods: int = 0                # additional buffer AFTER purge, before test begins (Section 4's "embargo period where appropriate")
    min_test_periods: int = 24              # a fold whose test window (after purge/embargo) has fewer real rows than this is skipped, not silently kept
    seed: int = 42                          # claude code changed: Section 15 — explicit, always recorded, never hidden randomness

    def resolved_purge(self) -> int:
        return self.horizon if self.purge_periods is None else self.purge_periods

    def resolved_step(self) -> int:
        return self.test_periods if self.step_periods is None else self.step_periods

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["resolved_purge_periods"] = self.resolved_purge()
        d["resolved_step_periods"] = self.resolved_step()
        return d


# ═══════════════════════════════════════════════════════════════════════
# FOLD BOUNDARIES
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class FoldSpec:
    """
    One fold's boundaries, expressed as POSITIONS into the caller's sorted
    unique index (not timestamps directly — build_folds() also resolves
    these to real Timestamps, stored below, so a FoldSpec is self-
    contained and auditable without needing the original index back).

    train_end_pos is EXCLUSIVE and already has purge_periods removed —
    i.e. train_positions = index[train_start_pos:train_end_pos] is the
    exact, already-purged set of rows safe to fit on. test_start_pos is
    where TEST begins, already past both the purge gap and the embargo
    gap. This means a caller never has to re-derive the purge/embargo
    math themselves — the FoldSpec IS the safe-to-use boundary.
    """
    fold_id: int
    train_start_pos: int
    train_end_pos: int          # exclusive, already purge-adjusted
    test_start_pos: int         # already past purge + embargo
    test_end_pos: int           # exclusive

    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp

    n_train: int
    n_test: int
    n_purged: int
    embargo_periods: int

    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "fold_id": self.fold_id,
            "train_start": str(self.train_start), "train_end": str(self.train_end),
            "test_start": str(self.test_start), "test_end": str(self.test_end),
            "n_train": self.n_train, "n_test": self.n_test,
            "n_purged": self.n_purged, "embargo_periods": self.embargo_periods,
            "skipped": self.skipped, "skip_reason": self.skip_reason,
        }


def build_folds(index: pd.DatetimeIndex, config: WalkForwardConfig) -> List[FoldSpec]:
    """
    THE generic fold-boundary primitive (Section 3). Pure function of an
    index + config — no data, no asset, no strategy. Generalizes
    walk_forward_engine.py's _generate_folds() (anchored/expanding,
    test window clipped never padded at data end, skip-below-minimum)
    to also support rolling windows and explicit purge/embargo.

    PURGE (Section 4): a training observation at position i, whose label
    is computed by looking `horizon` periods AHEAD (this project's own
    forward_return_Nh convention), actually encodes information about
    rows [i, i+horizon]. If i is close enough to train_end that
    i+horizon lands inside TEST, that training row's label leaks real
    information about test-period prices. Purging removes exactly the
    last `resolved_purge()` rows of train — the scientifically-defensible
    default is purge_periods = horizon, because that is the exact number
    of rows whose label window can overlap the train/test boundary; any
    less under-purges, any more is unnecessarily conservative without a
    stated reason.

    EMBARGO (Section 4): purging handles the KNOWN, mechanical overlap
    from the label horizon. Embargo is an ADDITIONAL buffer for residual
    serial correlation the label horizon doesn't fully capture (e.g. a
    feature itself built from a rolling window) — off by default (0),
    since this project's frozen feature families (Phase 2D/E/F) already
    document their own lookback windows explicitly rather than needing a
    blanket embargo; a caller with a feature that has its own additional
    lookback should set embargo_periods to that lookback.

    Never shuffles, never randomly splits — folds are built strictly in
    chronological position order (Section 3: "Do not shuffle
    observations").
    """
    if config.mode not in ("expanding", "rolling"):
        raise ValueError(f"WalkForwardConfig.mode must be 'expanding' or 'rolling', got {config.mode!r}")

    idx = index.sort_values()
    if not idx.is_unique:
        raise ValueError("build_folds() requires a de-duplicated index — duplicate timestamps make position-based purge/embargo ambiguous")

    n = len(idx)
    purge = config.resolved_purge()
    step = config.resolved_step()
    if purge < 0 or config.embargo_periods < 0 or step <= 0 or config.test_periods <= 0:
        raise ValueError("purge_periods/embargo_periods must be >= 0 and test_periods/step_periods must be > 0")

    folds: List[FoldSpec] = []
    fold_id = 1

    if config.mode == "expanding":
        raw_train_end = config.min_train_periods
    else:
        raw_train_end = config.min_train_periods

    while True:
        raw_train_start = 0 if config.mode == "expanding" else max(0, raw_train_end - config.min_train_periods)
        purged_train_end = max(raw_train_start, raw_train_end - purge)
        test_start_pos = raw_train_end + config.embargo_periods
        test_end_pos = min(test_start_pos + config.test_periods, n)

        if test_start_pos >= n:
            break   # no more data to test on — stop, do not pad

        n_test = test_end_pos - test_start_pos
        n_train = purged_train_end - raw_train_start

        skipped, skip_reason = False, ""
        if n_test < config.min_test_periods:
            skipped, skip_reason = True, f"test window has {n_test} periods, below min_test_periods={config.min_test_periods}"
        elif n_train <= 0:
            skipped, skip_reason = True, f"train window is empty after purge ({n_train} periods) — min_train_periods too small relative to purge_periods={purge}"

        # claude code changed: train_end_pos is EXCLUSIVE (train rows are
        # [train_start_pos, train_end_pos)), so the last real train row's
        # timestamp lives at position train_end_pos - 1, not train_end_pos —
        # indexing at train_end_pos itself gives the FIRST row after train
        # (off-by-one, caught by test_never_shuffles_chronological_order
        # asserting train_end < test_start, which failed because both
        # resolved to the same timestamp under purge=0/embargo=0).
        last_train_pos = purged_train_end - 1 if purged_train_end > raw_train_start else raw_train_start
        folds.append(FoldSpec(
            fold_id=fold_id,
            train_start_pos=raw_train_start, train_end_pos=purged_train_end,
            test_start_pos=test_start_pos, test_end_pos=test_end_pos,
            train_start=idx[raw_train_start], train_end=idx[last_train_pos],
            test_start=idx[test_start_pos], test_end=idx[test_end_pos - 1],
            n_train=max(n_train, 0), n_test=n_test, n_purged=purge, embargo_periods=config.embargo_periods,
            skipped=skipped, skip_reason=skip_reason,
        ))
        fold_id += 1
        raw_train_end = raw_train_end + step   # claude code changed: expanding AND rolling both advance train_end by step; expanding grows train (start fixed at 0), rolling slides it (start = train_end - min_train_periods)

    return folds


def assert_temporal_disjoint(train_positions: Sequence[int], test_positions: Sequence[int]) -> None:
    """
    Section 5: "fail loudly when an operation attempts to fit using TEST
    data." A defensive assertion, used internally by evaluate_feature_oos()
    and exposed for direct use by any future Type B/D evaluator built on
    top of this module.
    """
    overlap = set(train_positions) & set(test_positions)
    if overlap:
        raise RuntimeError(
            f"LEAKAGE: {len(overlap)} position(s) appear in both train and test "
            f"({sorted(overlap)[:5]}{'...' if len(overlap) > 5 else ''}) — refusing to fit."
        )
    if train_positions and test_positions and max(train_positions) >= min(test_positions):
        raise RuntimeError(
            f"LEAKAGE: a train position ({max(train_positions)}) is >= the earliest test "
            f"position ({min(test_positions)}) — train must be strictly chronologically before test."
        )


# ═══════════════════════════════════════════════════════════════════════
# PER-FOLD EVALUATION RESULT + AGGREGATE OOSResult (Section 6)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class FoldEvalResult:
    """
    One fold's real evaluation output — Section 6's hard requirement that
    fold-level detail is MANDATORY, never collapsed straight to one
    aggregate number. `fitted_params` is whatever the caller's fit_fn
    returned (e.g. {"mean": ..., "std": ...} for the default scaler) —
    stored so a reviewer can see exactly what was learned from train and
    confirm it came only from train.
    """
    fold: FoldSpec
    n_train_obs: int
    n_test_obs: int
    ic: Optional[float]           # Spearman IC between prediction and forward label, on TEST only — Type A (feature_to_return) only
    ic_pvalue: Optional[float]
    mean_label: Optional[float]   # test-window mean of the label — lets a reviewer see regime shifts fold-to-fold (Section 17 Case D) — Type A only
    fitted_params: Dict = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str = ""
    # claude code changed: new fields — Type B (strategy_to_outcome) mission.
    # ic/ic_pvalue/mean_label are structurally specific to Type A's
    # feature-ranking evaluation and don't fit trade-outcome metrics
    # (win_rate, profit_factor, sharpe, drawdown, ...) without abusing
    # their meaning. Rather than invent a second, parallel FoldEvalResult
    # hierarchy (explicitly disallowed — "do not create an unnecessary
    # parallel result hierarchy"), this ONE dataclass gains two generic,
    # OPTIONAL fields: `metrics` (a scalar-metric bag, evaluation-type-
    # specific) and `trades` (the fold's own evaluated — i.e.
    # test-entry-only — trade list, kept for pooled aggregation and audit).
    # Both default to empty, so every existing Type A construction site
    # and every existing Type A test is byte-for-byte unaffected.
    metrics: Dict = field(default_factory=dict)
    trades: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            **self.fold.to_dict(),
            "n_train_obs": self.n_train_obs, "n_test_obs": self.n_test_obs,
            "ic": self.ic, "ic_pvalue": self.ic_pvalue, "mean_label": self.mean_label,
            "fitted_params": self.fitted_params,
            "metrics": self.metrics, "n_trades": len(self.trades),
            "eval_skipped": self.skipped, "eval_skip_reason": self.skip_reason,
        }


@dataclass
class OOSResult:
    """
    Section 6's structured result object. Fold-level results are always
    present (`folds`) — `aggregate` is a convenience summary computed
    FROM them, never a replacement for them. Carries every provenance
    field Section 16 (reproducibility) and this project's existing
    governance model (HypothesisFamily/ResearchExperiment) require, so a
    caller has everything needed for record_research_trial() without
    re-deriving it.
    """
    evaluation_type: str                    # "feature_to_return" today; "strategy_to_outcome"/"cross_sectional_ml" reserved for future evaluators built on the same primitives
    config: WalkForwardConfig
    folds: List[FoldEvalResult]
    methodology_version: str = METHODOLOGY_VERSION
    asset_universe: List[str] = field(default_factory=list)
    feature_name: str = ""
    label_name: str = ""
    strategy_name: str = ""      # claude code changed: new — Type B provenance (Section 16: "strategy identity/version"); "" for Type A, unaffected
    strategy_version: str = ""   # claude code changed: new — deliberately its OWN field, not repurposed feature_name/label_name, which would read as actively wrong for a strategy result to anyone auditing the ledger later
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    data_fingerprint: Optional[str] = None
    random_seed: int = 42
    n_folds_total: int = 0
    n_folds_evaluated: int = 0
    n_folds_skipped: int = 0

    @property
    def aggregate(self) -> Dict:
        # claude code changed: dispatch added for the Type B mission — the
        # IC branch below is BYTE-FOR-BYTE UNCHANGED from before this
        # mission (same code, same order of operations), so every existing
        # feature_to_return result and test is unaffected. Only a NEW
        # branch was added for "strategy_to_outcome", since Section 10 of
        # that mission explicitly forbids reusing the IC-mean-style
        # aggregation for trade-outcome metrics (win_rate/profit_factor
        # must be recomputed from POOLED trades, not averaged per-fold —
        # see _aggregate_strategy_outcome()'s own docstring for the full
        # per-metric methodology).
        if self.evaluation_type == "strategy_to_outcome":
            return _aggregate_strategy_outcome(self.folds)
        if self.evaluation_type == "cross_sectional_ranking":
            return _aggregate_cross_sectional(self.folds)

        valid = [f for f in self.folds if not f.skipped and f.ic is not None and not np.isnan(f.ic)]
        ics = np.array([f.ic for f in valid])
        if len(ics) == 0:
            return {
                "n_folds_with_ic": 0, "mean_ic": None, "std_ic": None, "ic_t_stat": None,
                "pct_folds_positive_ic": None, "min_fold_ic": None, "max_fold_ic": None,
            }
        # claude code changed: a one-sample t-test across FOLD-level ICs, not a
        # p-value over pooled raw observations — folds are the unit of
        # replication here (Section 6's "aggregate metrics" should summarize
        # fold-to-fold consistency, not re-derive per-observation significance,
        # which feature_validator.py's block-permutation test already owns for
        # the single-split case).
        t_stat, t_p = (stats.ttest_1samp(ics, 0.0) if len(ics) >= 2 else (None, None))
        return {
            "n_folds_with_ic": int(len(ics)),
            "mean_ic": float(np.mean(ics)), "std_ic": float(np.std(ics, ddof=1)) if len(ics) >= 2 else 0.0,
            "ic_t_stat": float(t_stat) if t_stat is not None else None,
            "ic_t_pvalue": float(t_p) if t_p is not None else None,
            "pct_folds_positive_ic": float(np.mean(ics > 0)),
            "min_fold_ic": float(np.min(ics)), "max_fold_ic": float(np.max(ics)),
        }

    def to_dict(self) -> Dict:
        return {
            "evaluation_type": self.evaluation_type,
            "methodology_version": self.methodology_version,
            "run_id": self.run_id,
            "config": self.config.to_dict(),
            "asset_universe": self.asset_universe,
            "feature_name": self.feature_name, "label_name": self.label_name,
            "strategy_name": self.strategy_name, "strategy_version": self.strategy_version,
            "data_fingerprint": self.data_fingerprint,
            "random_seed": self.random_seed,
            "n_folds_total": self.n_folds_total,
            "n_folds_evaluated": self.n_folds_evaluated,
            "n_folds_skipped": self.n_folds_skipped,
            "aggregate": self.aggregate,
            "folds": [f.to_dict() for f in self.folds],
        }


# ═══════════════════════════════════════════════════════════════════════
# DEFAULT FIT/PREDICT — train-only standardization (a real, minimal
# example of "parameters learned from train, applied to test")
# ═══════════════════════════════════════════════════════════════════════

def _default_fit_fn(train_feature: np.ndarray) -> Dict:
    mean = float(np.nanmean(train_feature))
    std = float(np.nanstd(train_feature))
    return {"mean": mean, "std": std if std > 1e-12 else 1.0}


def _default_predict_fn(test_feature: np.ndarray, fitted_params: Dict) -> np.ndarray:
    return (test_feature - fitted_params["mean"]) / fitted_params["std"]


# ═══════════════════════════════════════════════════════════════════════
# TYPE A EVALUATOR: feature -> forward return
# ═══════════════════════════════════════════════════════════════════════

def evaluate_feature_oos(
    df: pd.DataFrame,
    feature_col: str,
    label_col: str,
    timestamp_col: str,
    config: WalkForwardConfig,
    asset_col: Optional[str] = None,
    fit_fn: Optional[Callable[[np.ndarray], Dict]] = None,
    predict_fn: Optional[Callable[[np.ndarray, Dict], np.ndarray]] = None,
    feature_name: str = "",
    label_name: str = "",
    data_fingerprint: Optional[str] = None,
) -> OOSResult:
    """
    THE fully-implemented Type A evaluator (Section 2). Chronological
    folds only (build_folds()), fit_fn sees ONLY the fold's purged TRAIN
    rows, predict_fn is applied to TEST, score = Spearman IC between the
    prediction and the real forward-return label on TEST — the same rank-
    correlation convention this project's own feature_validator.py
    already uses, so an OOS IC here is comparable to a Phase 2D/E/F
    single-split IC.

    Cross-sectional-shaped input (Section 10): pass `asset_col` for
    long-format data (one row per timestamp x asset). Fold boundaries are
    computed ONCE off the shared, deduplicated timestamp axis — never
    per-asset — matching how a real cross-sectional evaluation must work
    (every asset shares the same train/test calendar boundary). Within
    each fold, ALL assets' rows falling in that fold's train/test
    timestamp range are pooled together for fit/score, giving a genuine
    pooled cross-sectional-and-time-series IC per fold. This does not
    build ranking/portfolio construction (explicitly out of scope,
    Section 10) — it proves the data SHAPE and fold-sharing work, which
    is the part a future ranking/portfolio evaluator would otherwise have
    to redesign from scratch if this module only ever supported one
    asset column.

    LEAKAGE ENFORCEMENT (Section 5): fit_fn is called with a numpy array
    sliced from ONLY the purged-train row positions; assert_temporal_disjoint()
    runs before every fold's fit/predict pair. There is no code path here
    that lets predict-time information reach fit_fn.
    """
    fit_fn = fit_fn or _default_fit_fn
    predict_fn = predict_fn or _default_predict_fn

    working = df[[timestamp_col, feature_col, label_col] + ([asset_col] if asset_col else [])].copy()
    working[timestamp_col] = pd.to_datetime(working[timestamp_col])

    unique_ts = pd.DatetimeIndex(sorted(working[timestamp_col].unique()))
    folds = build_folds(unique_ts, config)

    ts_to_pos = {ts: i for i, ts in enumerate(unique_ts)}
    working["_pos"] = working[timestamp_col].map(ts_to_pos)

    fold_results: List[FoldEvalResult] = []
    for fold in folds:
        if fold.skipped:
            fold_results.append(FoldEvalResult(
                fold=fold, n_train_obs=0, n_test_obs=0, ic=None, ic_pvalue=None,
                mean_label=None, skipped=True, skip_reason=fold.skip_reason,
            ))
            continue

        train_mask = (working["_pos"] >= fold.train_start_pos) & (working["_pos"] < fold.train_end_pos)
        test_mask = (working["_pos"] >= fold.test_start_pos) & (working["_pos"] < fold.test_end_pos)

        assert_temporal_disjoint(
            working.loc[train_mask, "_pos"].unique().tolist(),
            working.loc[test_mask, "_pos"].unique().tolist(),
        )

        train_feat = working.loc[train_mask, feature_col].to_numpy(dtype=float)
        test_feat = working.loc[test_mask, feature_col].to_numpy(dtype=float)
        test_label = working.loc[test_mask, label_col].to_numpy(dtype=float)

        if len(train_feat) == 0 or len(test_feat) == 0:
            fold_results.append(FoldEvalResult(
                fold=fold, n_train_obs=len(train_feat), n_test_obs=len(test_feat),
                ic=None, ic_pvalue=None, mean_label=None,
                skipped=True, skip_reason="empty train or test slice after masking",
            ))
            continue

        fitted_params = fit_fn(train_feat)          # claude code changed: fit_fn receives ONLY the purged-train slice — never test
        prediction = predict_fn(test_feat, fitted_params)

        valid = ~(np.isnan(prediction) | np.isnan(test_label))
        if valid.sum() < 3:
            fold_results.append(FoldEvalResult(
                fold=fold, n_train_obs=len(train_feat), n_test_obs=len(test_feat),
                ic=None, ic_pvalue=None, mean_label=float(np.nanmean(test_label)) if len(test_label) else None,
                fitted_params=fitted_params, skipped=True,
                skip_reason=f"fewer than 3 valid (non-NaN) prediction/label pairs in test ({int(valid.sum())})",
            ))
            continue

        ic, ic_p = stats.spearmanr(prediction[valid], test_label[valid])
        fold_results.append(FoldEvalResult(
            fold=fold, n_train_obs=len(train_feat), n_test_obs=len(test_feat),
            ic=float(ic) if not np.isnan(ic) else None,
            ic_pvalue=float(ic_p) if not np.isnan(ic_p) else None,
            mean_label=float(np.nanmean(test_label)),
            fitted_params=fitted_params,
        ))

    n_evaluated = sum(1 for f in fold_results if not f.skipped)
    return OOSResult(
        evaluation_type="feature_to_return",
        config=config,
        folds=fold_results,
        asset_universe=sorted(working[asset_col].unique().tolist()) if asset_col else [],
        feature_name=feature_name or feature_col,
        label_name=label_name or label_col,
        data_fingerprint=data_fingerprint,
        random_seed=config.seed,
        n_folds_total=len(fold_results),
        n_folds_evaluated=n_evaluated,
        n_folds_skipped=len(fold_results) - n_evaluated,
    )


# ═══════════════════════════════════════════════════════════════════════
# TYPE B EVALUATOR: strategy -> trade outcome
#
# claude code changed: new section — "Build Type B Strategy->Trade-Outcome
# OOS Evaluator" mission. Everything above this line (WalkForwardConfig,
# FoldSpec, build_folds(), assert_temporal_disjoint(), OOSResult's fold
# list / provenance fields / to_dict()) is REUSED UNCHANGED — Type B adds
# no new fold-boundary logic of its own, per that mission's hard
# constraint "Do not duplicate fold-generation logic."
#
# ARCHITECTURAL DECISION, stated explicitly (Section 1 of that mission:
# "if an architectural conflict is discovered, stop and report it before
# making a broad redesign" — this is that report, resolved via a small,
# additive extension rather than a parallel framework):
#
#   bot/backtesting/backtester.py's Backtester class does NOT accept an
#   injectable strategy — StrategyRouter.__init__ hardcodes
#   MovingAverageStrategy()/MeanReversionStrategy() internally, with no
#   parameter-override or strategy-injection point. That means Backtester
#   is not itself "strategy-agnostic" at the object level; it always runs
#   THIS project's one production regime-routed pipeline.
#
#   Rather than fork or generalize Backtester (out of scope, and risky —
#   it's live-shared, well-tested code this mission does not ask to
#   touch), evaluate_strategy_oos() below is written against a MINIMAL,
#   documented trade-dict contract instead of against Backtester
#   directly. A caller supplies `run_strategy_fn(df_slice, fitted_params)
#   -> List[trade dict]` — for THIS project's existing strategies, that
#   function is a thin adapter (bot/research/strategy_oos_adapters.py)
#   that wraps Backtester; a future non-Backtester strategy (a
#   cointegration model, an ML predictor, a live IBKR strategy) would
#   supply its own adapter without touching this module at all. This is
#   what makes the EVALUATOR generic while still proving a real
#   integration with an existing, non-Kalman strategy (Section 14).
#
# TRADE-DICT CONTRACT (what run_strategy_fn must return per trade):
#   entry_time   : pd.Timestamp — when the position was opened
#   exit_time    : pd.Timestamp — when the position was closed
#   profit       : float — NET P&L in account currency (after fees)
#   gross_profit : float, optional — P&L before fees (fees = gross - net)
#   r_multiple   : float, optional — profit normalised by dollar risk
#   direction/exit_reason : optional, passed through for audit only
#
# BOUNDARY-TRADE POLICY (Section 7's "do not make this decision
# silently" — documented HERE, tested in test_strategy_oos_evaluator.py):
#   A trade is scored in fold N if and only if entry_time falls inside
#   [fold.test_start, fold.test_end). Any position still open at
#   fold.test_end is EXPLICITLY FORCE-CLOSED, mark-to-market, at that
#   boundary — never carried into the next fold. For the Backtester
#   adapter this is not reimplemented: Backtester's own _force_close()
#   already does exactly this whenever it reaches the last row of
#   whatever DataFrame it's given, and evaluate_strategy_oos() always
#   hands it a slice that ends exactly at fold.test_end. No position,
#   fitted parameter, or piece of state is ever carried from one fold's
#   TEST run into another's — every fold is a fully independent
#   simulation (Section 12.H, "fold independence").
#
# WARMUP CONTEXT vs. PURGE — a second explicit decision:
#   fit_fn (calibration) sees ONLY the fold's purged TRAIN rows —
#   identical rule to Type A. A SEPARATE, additional slice of real,
#   unpurged price history immediately before test_start ("warmup
#   context", sized by `warmup_periods`) is also handed to
#   run_strategy_fn, purely so stateless, backward-looking technical
#   indicators (RSI/EMA/ADX/regime detection) are not cold-started at
#   test_start — exactly the real price history a live bot would already
#   have by that point in time. This is NOT purged, because purge exists
#   to stop a LABEL's forward-looking window from reaching into TEST; an
#   indicator computed causally at time t from real prices at or before t
#   carries no information about anything after t, so there is nothing to
#   purge against. Warmup context is never used for fit_fn/calibration —
#   only the purged TRAIN slice is. Trades opened during the warmup
#   portion (before test_start) are discarded from that fold's scored
#   results — they exist only to prime state, never to be evaluated.
# ═══════════════════════════════════════════════════════════════════════

def _replay_fold_bookkeeping(trades_chronological: List[Dict], initial_balance: float) -> Dict:
    """
    claude code changed: new. A small, mechanical REPLAY of Backtester's
    own peak/drawdown/loss-streak formulas (bot/backtesting/backtester.py
    _process_exit()/_force_close()) — not a new formula, a re-application
    of the existing one to a trade list that's already been isolated
    post-hoc (fold-scoped, test-entry-only). Needed because Backtester
    computes these INCREMENTALLY inside its own live simulation loop, and
    Type B's fold-scoped trade list is assembled AFTER that loop has run
    (see evaluate_strategy_oos()) — there is no way to "reuse" an
    incremental accumulator without replaying the sequence it accumulates
    over.
    """
    balance = initial_balance
    peak = initial_balance
    max_dd = 0.0
    consec_losses = 0
    max_consec_losses = 0
    for t in trades_chronological:
        net = t.get("profit", 0.0) or 0.0
        balance += net
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        if net < 0:
            consec_losses += 1
            max_consec_losses = max(max_consec_losses, consec_losses)
        else:
            consec_losses = 0
    return {"final_balance": balance, "max_drawdown": round(max_dd, 4), "max_consecutive_losses": max_consec_losses}


def _compute_trade_metrics(trades_chronological: List[Dict], initial_balance: float, period_start, period_end) -> Dict:
    """
    claude code changed: new. Computes the standard trade-outcome metric
    set for ONE fold's already-isolated trade list, by REUSING (not
    duplicating) bot.backtesting.backtester's own, already independently-
    verified formulas (bot/tests/test_analytics_independent_verification.py)
    — win_rate/expectancy/profit_factor/avg_r via Backtester._build_results()
    itself (constructing a throwaway instance and injecting state, the
    SAME pattern that test file's own
    BacktesterMetricsIndependentVerificationTest._make_backtester_with_trades()
    already establishes as this project's convention for exercising that
    private method), and Sharpe/Sortino via the module-level
    calculate_sharpe_ratio()/calculate_sortino_ratio() pure functions
    directly. This import is deliberately local (not top-of-file) — see
    the module's Type B section docstring: oos_validator.py's Type A path
    has zero Backtester dependency, and Type B's dependency is confined to
    this one metrics helper, not the fold/purge/embargo machinery.

    KNOWN LIMITATION, documented rather than approximated (Section 8:
    "clearly distinguish gross vs net... do not add metrics the existing
    analytics layer cannot calculate correctly"): Trade.to_dict() (the
    Backtester adapter's own trade shape) does not expose the PRE-slippage
    reference price for entry, so a per-fold "total_slippage_cost" dollar
    figure cannot be exactly reconstructed from an already-isolated trade
    list — entry_price/exit_price ARE the actual post-slippage fills, so
    every P&L figure here is already realistic and slippage-inclusive,
    but the slippage COST cannot be separately itemised without modifying
    Backtester/Trade to expose it, which this mission deliberately avoids
    (out of scope — "reuse existing... backtester", not fork it). Reported
    as `total_slippage_cost: None` rather than a fabricated number.
    """
    from bot.backtesting.backtester import Backtester, calculate_sharpe_ratio, calculate_sortino_ratio

    if not trades_chronological:
        years_elapsed = None
    else:
        years_elapsed = max((period_end - period_start).days / 365.25, 1e-6)

    bookkeeping = _replay_fold_bookkeeping(trades_chronological, initial_balance)

    bt = Backtester(df=pd.DataFrame(), initial_balance=initial_balance)
    bt.df = pd.DataFrame(index=pd.DatetimeIndex([period_start, period_end]))
    bt.closed_trades = trades_chronological
    bt.max_drawdown = bookkeeping["max_drawdown"]
    bt.max_consec_losses = bookkeeping["max_consecutive_losses"]
    bt.balance = bookkeeping["final_balance"]
    bt.total_fees = sum((t.get("gross_profit", t.get("profit", 0.0)) - t.get("profit", 0.0)) for t in trades_chronological)
    bt.total_slippage = 0.0   # claude code changed: not reconstructable post-hoc — see docstring above; excluded from the reported dict below, not silently reported as a real zero

    built = bt._build_results()

    return {
        "n_trades": built["total_trades"], "wins": built["wins"], "losses": built["losses"],
        "win_rate_pct": built["win_rate"], "expectancy": built["expectancy"],
        "profit_factor": built["profit_factor"], "avg_r_multiple": built["avg_r_multiple"],
        "sharpe_ratio": calculate_sharpe_ratio(trades_chronological, years_elapsed=years_elapsed),
        "sortino_ratio": calculate_sortino_ratio(trades_chronological, years_elapsed=years_elapsed),
        "max_drawdown_pct": built["max_drawdown"],
        "max_consecutive_losses": built["max_consecutive_losses"],
        "gross_return": round(sum(t.get("gross_profit", t.get("profit", 0.0)) for t in trades_chronological), 2),
        "net_return": round(sum(t.get("profit", 0.0) for t in trades_chronological), 2),
        "fees_paid": round(bt.total_fees, 2),
        "total_slippage_cost": None,   # claude code changed: known, documented limitation — see docstring
        "final_balance": round(bookkeeping["final_balance"], 2),
    }


def _aggregate_strategy_outcome(folds: List[FoldEvalResult]) -> Dict:
    """
    claude code changed: new — Section 10's explicit, per-metric
    aggregation methodology for Type B. Every choice below is deliberate,
    not a default:

    - n_trades, wins, losses, fees_paid, gross_return, net_return:
      SUMMED across folds (additive dollar/count quantities). net_return
      assumes each fold starts from the SAME fixed `fold_initial_balance`
      (not compounded fold-to-fold) — the same "$X capital per fold, then
      stitch results" convention bot/research/walk_forward_engine.py
      already uses for its own combined OOS results, reused here rather
      than inventing a different one.
    - win_rate / profit_factor / expectancy / avg_r_multiple: RECOMPUTED
      from the POOLED trade list across all evaluated folds — never a
      mean of per-fold percentages/ratios (Section 10's explicit warning:
      a fold with 3 trades and a fold with 300 must not be weighted
      equally, and a mean of profit-factor RATIOS is not a meaningful
      statistic when denominators differ, or are zero, across folds).
    - sharpe_ratio / sortino_ratio: computed ONCE from the pooled
      r_multiple list via the exact same pure functions used per-fold,
      annualised using the SUM of each evaluated fold's own real test-
      period span (not the wall-clock first-to-last span, which would
      incorrectly count embargo dead-zones as "years traded").
    - max_drawdown_pct / max_consecutive_losses: the WORST (maximum)
      single-fold value, explicitly labelled as such. These are
      path-dependent statistics of one continuous equity curve; since
      each fold restarts from fresh capital (not a single chained
      curve), there is no single "combined equity curve" to compute a
      true aggregate drawdown from. Reporting the worst observed fold is
      a real, conservative, honestly-labelled statistic — synthesising a
      fake combined curve would not be (Section 10: "if a metric cannot
      be meaningfully aggregated,... leave the aggregate absent rather
      than inventing a misleading statistic").
    - total_slippage_cost: left absent (None) — see
      _compute_trade_metrics()'s docstring.
    """
    from bot.backtesting.backtester import calculate_sharpe_ratio, calculate_sortino_ratio

    evaluated = [f for f in folds if not f.skipped]
    pooled_trades: List[Dict] = []
    for f in evaluated:
        pooled_trades.extend(f.trades)

    fold_dds = [f.metrics.get("max_drawdown_pct") for f in evaluated if f.metrics.get("max_drawdown_pct") is not None]
    fold_streaks = [f.metrics.get("max_consecutive_losses") for f in evaluated if f.metrics.get("max_consecutive_losses") is not None]
    total_test_years = sum(
        max((f.fold.test_end - f.fold.test_start).days / 365.25, 1e-6) for f in evaluated
    ) if evaluated else None

    if not pooled_trades:
        return {
            "n_folds_evaluated": len(evaluated), "n_folds_with_trades": 0, "n_trades": 0,
            "win_rate_pct": None, "profit_factor": None, "expectancy": None, "avg_r_multiple": None,
            "sharpe_ratio": None, "sortino_ratio": None,
            "worst_fold_max_drawdown_pct": max(fold_dds) if fold_dds else None,
            "worst_fold_max_consecutive_losses": max(fold_streaks) if fold_streaks else None,
            "total_net_return": 0.0, "total_gross_return": 0.0, "total_fees_paid": 0.0,
            "total_slippage_cost": None,
        }

    total = len(pooled_trades)
    wins = sum(1 for t in pooled_trades if (t.get("profit", 0.0) or 0.0) > 0)
    losses = total - wins
    profits = [t.get("profit", 0.0) or 0.0 for t in pooled_trades]
    gross_profit = sum(p for p in profits if p > 0)
    gross_loss = abs(sum(p for p in profits if p < 0))
    r_values = [t.get("r_multiple", 0.0) or 0.0 for t in pooled_trades]

    return {
        "n_folds_evaluated": len(evaluated),
        "n_folds_with_trades": sum(1 for f in evaluated if len(f.trades) > 0),
        "n_trades": total, "wins": wins, "losses": losses,
        "win_rate_pct": round((wins / total) * 100, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "expectancy": round(sum(profits) / total, 4),
        "avg_r_multiple": round(sum(r_values) / total, 4),
        "sharpe_ratio": calculate_sharpe_ratio(pooled_trades, years_elapsed=total_test_years),
        "sortino_ratio": calculate_sortino_ratio(pooled_trades, years_elapsed=total_test_years),
        "worst_fold_max_drawdown_pct": max(fold_dds) if fold_dds else None,
        "worst_fold_max_consecutive_losses": max(fold_streaks) if fold_streaks else None,
        "total_net_return": round(sum(profits), 2),
        "total_gross_return": round(sum(t.get("gross_profit", t.get("profit", 0.0)) for t in pooled_trades), 2),
        "total_fees_paid": round(sum(f.metrics.get("fees_paid", 0.0) for f in evaluated), 2),
        "total_slippage_cost": None,
    }


def evaluate_strategy_oos(
    df: pd.DataFrame,
    run_strategy_fn: Callable[[pd.DataFrame, Dict], List[Dict]],
    config: WalkForwardConfig,
    fit_fn: Optional[Callable[[pd.DataFrame], Dict]] = None,
    warmup_periods: Optional[int] = None,
    fold_initial_balance: float = 10_000.0,
    strategy_name: str = "",
    strategy_version: str = "",
    data_fingerprint: Optional[str] = None,
) -> OOSResult:
    """
    THE Type B evaluator (strategy -> trade outcome). `df` must have a
    sorted, de-duplicated DatetimeIndex (OHLCV shape — matches
    Backtester's own required input, unlike Type A's timestamp-COLUMN
    convention, which matches this project's feature-table CSVs; both are
    real, pre-existing shapes in this codebase and neither should be
    forced onto the other's data).

    Per fold:
      1. fit_fn(purged TRAIN slice) -> fitted_params (or {} if fit_fn is
         None — many of this project's existing strategies, e.g.
         MeanReversionStrategy, have no data-fitted parameters at all;
         forcing a fake calibration step onto a fixed-parameter strategy
         would misrepresent it, so fit_fn is genuinely optional).
      2. run_strategy_fn(warmup_context + TEST slice, fitted_params) ->
         raw trade dicts (see module docstring for the contract and the
         warmup-context/purge distinction).
      3. Trades are filtered to entry_time in [test_start, test_end) —
         anything opened during warmup context is discarded, never scored.
      4. Fold-level metrics computed via _compute_trade_metrics() (reuses
         Backtester's own formulas, never reimplements them).

    LEAKAGE ENFORCEMENT: fit_fn is called with a DataFrame sliced from
    ONLY [fold.train_start_pos, fold.train_end_pos) — the same purged
    range Type A uses, via the same build_folds() call. assert_temporal_disjoint()
    runs on every fold's train/test positions before fit_fn/run_strategy_fn
    are invoked, identical to Type A.
    """
    if warmup_periods is None:
        from bot.backtesting.backtester import INDICATOR_LOOKBACK_CANDLES
        warmup_periods = INDICATOR_LOOKBACK_CANDLES   # claude code changed: reuse Backtester's own real constant rather than picking a new magic number

    idx = df.index.sort_values()
    if not idx.is_unique:
        raise ValueError("evaluate_strategy_oos() requires a de-duplicated DatetimeIndex")
    df = df.loc[idx]

    folds = build_folds(idx, config)

    fold_results: List[FoldEvalResult] = []
    for fold in folds:
        if fold.skipped:
            fold_results.append(FoldEvalResult(
                fold=fold, n_train_obs=0, n_test_obs=0, ic=None, ic_pvalue=None, mean_label=None,
                skipped=True, skip_reason=fold.skip_reason,
            ))
            continue

        train_positions = list(range(fold.train_start_pos, fold.train_end_pos))
        test_positions = list(range(fold.test_start_pos, fold.test_end_pos))
        assert_temporal_disjoint(train_positions, test_positions)

        train_slice = df.iloc[fold.train_start_pos:fold.train_end_pos]
        fitted_params = fit_fn(train_slice) if fit_fn is not None else {}   # claude code changed: fit_fn receives ONLY the purged-train slice — never warmup, never test

        warmup_start_pos = max(0, fold.test_start_pos - warmup_periods)
        sim_slice = df.iloc[warmup_start_pos:fold.test_end_pos]   # claude code changed: real, unpurged price history strictly before test_start — see module docstring for why this is safe (backward-looking only, never a forward-label vector)

        if len(sim_slice) < 2:
            fold_results.append(FoldEvalResult(
                fold=fold, n_train_obs=len(train_slice), n_test_obs=0, ic=None, ic_pvalue=None, mean_label=None,
                skipped=True, skip_reason="sim slice (warmup + test) has fewer than 2 rows",
            ))
            continue

        raw_trades = run_strategy_fn(sim_slice, fitted_params) or []

        # claude code changed: fail loudly on a malformed run_strategy_fn
        # return, rather than silently tolerating it — this contract
        # ("every returned trade is already closed") is what makes fold
        # independence structural (see the Type B section docstring's
        # "BOUNDARY-TRADE POLICY"): a trade without exit_time is either an
        # adapter bug or an attempt to carry an open position past this
        # call's return, and the evaluator has no state to carry it INTO —
        # better to raise here than let it silently corrupt downstream
        # metrics (e.g. bookkeeping replay proceeding with an implicit
        # exit_time of None).
        for t in raw_trades:
            if t.get("entry_time") is None or t.get("exit_time") is None:
                raise ValueError(
                    f"run_strategy_fn returned a trade missing entry_time/exit_time "
                    f"(fold {fold.fold_id}): {t!r} — every trade must already be closed; "
                    f"the evaluator carries no open-position state across folds by design."
                )

        evaluated_trades = [
            t for t in raw_trades
            if fold.test_start <= pd.Timestamp(t["entry_time"]) < fold.test_end
        ]
        evaluated_trades.sort(key=lambda t: pd.Timestamp(t["entry_time"]))   # claude code changed: chronological order required by _replay_fold_bookkeeping()'s sequential balance/drawdown replay

        metrics = _compute_trade_metrics(evaluated_trades, fold_initial_balance, fold.test_start, fold.test_end)

        fold_results.append(FoldEvalResult(
            fold=fold, n_train_obs=len(train_slice), n_test_obs=len(df.iloc[fold.test_start_pos:fold.test_end_pos]),
            ic=None, ic_pvalue=None, mean_label=None,
            fitted_params=fitted_params, metrics=metrics, trades=evaluated_trades,
        ))

    n_evaluated = sum(1 for f in fold_results if not f.skipped)
    return OOSResult(
        evaluation_type="strategy_to_outcome",
        config=config,
        folds=fold_results,
        strategy_name=strategy_name, strategy_version=strategy_version,
        data_fingerprint=data_fingerprint,
        random_seed=config.seed,
        n_folds_total=len(fold_results),
        n_folds_evaluated=n_evaluated,
        n_folds_skipped=len(fold_results) - n_evaluated,
    )


# ═══════════════════════════════════════════════════════════════════════
# TYPE C EVALUATOR: cross-sectional ranking -> long/short portfolio
#
# claude code changed: new section. Reuses Type A's own long-format
# cross-sectional data shape (timestamp/asset/feature/forward_return —
# exactly evaluate_feature_oos()'s asset_col convention) and turns it
# into an actual tradeable ranking portfolio: rank all assets by a
# feature at each TEST timestamp, hold an equal-weight long-top-K /
# short-bottom-K portfolio for one period, realize the ALREADY-KNOWN
# forward_return_col (the same causal label convention Type A already
# uses), net of an explicit turnover cost. This is deliberately the
# SMALL, disciplined version of "cross-sectional strategy OOS" — real
# long/short ranking mechanics, not a full portfolio optimizer.
#
# SCOPE DECISIONS, stated explicitly rather than silently chosen:
#   - Rebalance frequency = every TEST timestamp (the fold's own
#     granularity). A caller wanting a lower-frequency rebalance should
#     resample/thin their input df upstream — a separate rebalance-
#     schedule abstraction was judged out of scope for this pass (the
#     mission's own "small, rigorously tested... more valuable than
#     large... ambiguous" principle).
#   - Cost model: FULL turnover every period (the entire long+short
#     book is assumed closed and reopened each rebalance) — the
#     conservative, standard assumption when the holding period equals
#     the rebalance interval exactly, which is true here by
#     construction. `cost_rate` is a round-trip fraction of notional.
#   - No warmup-context concept (unlike Type B): Type C, like Type A,
#     consumes PRECOMPUTED feature values directly rather than
#     computing them online inside a stateful simulation loop, so there
#     is no indicator state to prime across a fold boundary.
#   - fit_fn (optional) sees only the purged TRAIN long-format rows,
#     identical rule to Type A/B, and is used to derive cross-sectional
#     normalization parameters (e.g. train mean/std of the feature) —
#     never to see TEST-period values.
# ═══════════════════════════════════════════════════════════════════════

def _default_cross_sectional_fit_fn(train_df: pd.DataFrame, feature_col: str) -> Dict:
    return {"mean": float(train_df[feature_col].mean()), "std": float(train_df[feature_col].std()) or 1.0}


def _rank_and_form_portfolio(
    period_df: pd.DataFrame, feature_col: str, forward_return_col: str,
    fitted_params: Dict, top_k: int, long_short: bool,
) -> Optional[Dict]:
    """
    One rebalance period's portfolio construction and realized return —
    a pure function of that period's own cross-sectional slice (every
    asset's feature + already-known forward_return at this ONE
    timestamp). Standardizes the feature using TRAIN-derived mean/std
    (fitted_params) purely for a stable ranking scale — the RANK itself
    (not the standardized value) is what selects the long/short legs, so
    this is not sensitive to the scaling choice, only to relative order.
    """
    valid = period_df.dropna(subset=[feature_col, forward_return_col])
    if len(valid) < 2 * top_k:
        return None   # not enough assets this period to form both legs cleanly

    mean, std = fitted_params.get("mean", 0.0), fitted_params.get("std", 1.0) or 1.0
    valid = valid.copy()
    valid["_z"] = (valid[feature_col] - mean) / std
    ranked = valid.sort_values("_z", ascending=False)

    long_leg = ranked.iloc[:top_k]
    short_leg = ranked.iloc[-top_k:] if long_short else None

    long_return = float(long_leg[forward_return_col].mean())
    short_return = float(short_leg[forward_return_col].mean()) if long_short else 0.0
    gross_return = (long_return - short_return) if long_short else long_return

    return {
        "n_long": len(long_leg), "n_short": len(short_leg) if long_short else 0,
        "long_return": long_return, "short_return": short_return, "gross_return": gross_return,
    }


def _compute_cross_sectional_fold_metrics(period_records: List[Dict], cost_rate: float, initial_balance: float, periods_per_year: Optional[float]) -> Dict:
    """
    claude code changed: new. Builds a REAL, sequential compounding
    equity curve from one fold's own period-by-period portfolio returns
    (a genuinely continuous series within a fold, unlike Type B's fresh-
    capital-per-trade convention) — so max_drawdown here is a true,
    meaningful statistic, not a "worst single trade" proxy.
    """
    if not period_records:
        return {"n_periods": 0}

    for r in period_records:
        r["cost"] = round(cost_rate * 2, 8)   # claude code changed: full round-trip turnover assumed every period — see module docstring
        r["net_return"] = r["gross_return"] - r["cost"]

    net_returns = np.array([r["net_return"] for r in period_records])
    balance = initial_balance
    peak = initial_balance
    max_dd = 0.0
    equity_curve = [balance]
    for r in net_returns:
        balance *= (1.0 + r)
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        equity_curve.append(balance)

    mean_r, std_r = float(np.mean(net_returns)), float(np.std(net_returns, ddof=1)) if len(net_returns) >= 2 else 0.0
    sharpe = (mean_r / std_r * np.sqrt(periods_per_year)) if std_r > 1e-12 and periods_per_year else None
    hit_rate = float(np.mean(net_returns > 0))

    return {
        "n_periods": len(period_records), "mean_net_return": round(mean_r, 6), "std_net_return": round(std_r, 6),
        "sharpe_ratio": round(sharpe, 4) if sharpe is not None else None, "hit_rate_pct": round(hit_rate * 100, 2),
        "total_compounded_return_pct": round((balance / initial_balance - 1) * 100, 4),
        "max_drawdown_pct": round(max_dd, 4), "final_balance": round(balance, 2),
        "total_cost_paid_pct": round(sum(r["cost"] for r in period_records) * 100, 4),
    }


def _aggregate_cross_sectional(folds: List[FoldEvalResult]) -> Dict:
    """
    claude code changed: new — same Section-10-style discipline as Type
    B's _aggregate_strategy_outcome(): rate-style metrics (mean return,
    Sharpe, hit rate) are RECOMPUTED from the POOLED period-return series
    across all evaluated folds, never averaged per-fold; drawdown is the
    WORST single fold (path-dependent, no single chained curve across
    independently-capitalized folds), explicitly labelled as such.
    """
    evaluated = [f for f in folds if not f.skipped]
    pooled_periods: List[Dict] = []
    for f in evaluated:
        pooled_periods.extend(f.trades)

    fold_dds = [f.metrics.get("max_drawdown_pct") for f in evaluated if f.metrics.get("max_drawdown_pct") is not None]

    if not pooled_periods:
        return {
            "n_folds_evaluated": len(evaluated), "n_periods": 0, "mean_net_return": None,
            "sharpe_ratio": None, "hit_rate_pct": None, "worst_fold_max_drawdown_pct": max(fold_dds) if fold_dds else None,
        }

    net_returns = np.array([r["net_return"] for r in pooled_periods])
    mean_r, std_r = float(np.mean(net_returns)), float(np.std(net_returns, ddof=1)) if len(net_returns) >= 2 else 0.0
    total_periods_per_year = sum(
        max((f.fold.test_end - f.fold.test_start).days / 365.25, 1e-6) for f in evaluated
    )
    periods_per_year = len(pooled_periods) / total_periods_per_year if total_periods_per_year > 0 else None
    sharpe = (mean_r / std_r * np.sqrt(periods_per_year)) if std_r > 1e-12 and periods_per_year else None

    return {
        "n_folds_evaluated": len(evaluated), "n_periods": len(pooled_periods),
        "mean_net_return": round(mean_r, 6), "std_net_return": round(std_r, 6),
        "sharpe_ratio": round(sharpe, 4) if sharpe is not None else None,
        "hit_rate_pct": round(float(np.mean(net_returns > 0)) * 100, 2),
        "worst_fold_max_drawdown_pct": max(fold_dds) if fold_dds else None,
        "total_cost_paid_pct": round(sum(r["cost"] for r in pooled_periods) * 100, 4),
    }


def evaluate_cross_sectional_oos(
    df: pd.DataFrame,
    timestamp_col: str,
    asset_col: str,
    feature_col: str,
    forward_return_col: str,
    config: WalkForwardConfig,
    top_k: int = 3,
    long_short: bool = True,
    cost_rate: float = 0.0,
    fit_fn: Optional[Callable[[pd.DataFrame], Dict]] = None,
    fold_initial_balance: float = 10_000.0,
    strategy_name: str = "",
    strategy_version: str = "",
    data_fingerprint: Optional[str] = None,
) -> OOSResult:
    """
    THE Type C evaluator (cross-sectional ranking -> long/short
    portfolio). `df` is the SAME long-format shape evaluate_feature_oos()
    already accepts via its asset_col parameter: one row per
    timestamp x asset, with a feature column and an already-known
    forward_return_col (the realized return over exactly one rebalance
    period — the caller's own causal label, matching this project's
    forward_return_Nh convention).

    Per fold:
      1. fit_fn(purged TRAIN long-format rows) -> fitted_params (default:
         train mean/std of feature_col, used only to stabilize the
         ranking scale — never to see TEST-period values).
      2. For every TEST timestamp: rank all assets with valid data at
         that instant by (feature - fitted mean)/std, form an equal-
         weight top_k long / bottom_k short portfolio (long_short=False
         for a long-only book), realize forward_return_col, net of a
         full-round-trip cost_rate.
      3. A real, compounding per-fold equity curve is built from that
         fold's own period-by-period net returns (_compute_cross_sectional_fold_metrics).

    LEAKAGE ENFORCEMENT: fit_fn is called with ONLY the purged
    [train_start_pos, train_end_pos) slice of the shared, deduplicated
    timestamp axis; assert_temporal_disjoint() runs before every fold's
    fit/portfolio-construction pair, identical to Type A/B.
    """
    working = df[[timestamp_col, asset_col, feature_col, forward_return_col]].copy()
    working[timestamp_col] = pd.to_datetime(working[timestamp_col])

    unique_ts = pd.DatetimeIndex(sorted(working[timestamp_col].unique()))
    folds = build_folds(unique_ts, config)
    ts_to_pos = {ts: i for i, ts in enumerate(unique_ts)}
    working["_pos"] = working[timestamp_col].map(ts_to_pos)

    fold_results: List[FoldEvalResult] = []
    for fold in folds:
        if fold.skipped:
            fold_results.append(FoldEvalResult(
                fold=fold, n_train_obs=0, n_test_obs=0, ic=None, ic_pvalue=None, mean_label=None,
                skipped=True, skip_reason=fold.skip_reason,
            ))
            continue

        train_mask = (working["_pos"] >= fold.train_start_pos) & (working["_pos"] < fold.train_end_pos)
        test_mask = (working["_pos"] >= fold.test_start_pos) & (working["_pos"] < fold.test_end_pos)
        assert_temporal_disjoint(
            working.loc[train_mask, "_pos"].unique().tolist(),
            working.loc[test_mask, "_pos"].unique().tolist(),
        )

        train_slice = working.loc[train_mask]
        fitted_params = fit_fn(train_slice) if fit_fn is not None else _default_cross_sectional_fit_fn(train_slice, feature_col)

        test_slice = working.loc[test_mask]
        period_records = []
        for ts, period_df in test_slice.groupby(timestamp_col, sort=True):
            record = _rank_and_form_portfolio(period_df, feature_col, forward_return_col, fitted_params, top_k, long_short)
            if record is not None:
                record["timestamp"] = ts
                period_records.append(record)

        periods_per_year_this_fold = len(period_records) / max((fold.test_end - fold.test_start).days / 365.25, 1e-6) if period_records else None
        metrics = _compute_cross_sectional_fold_metrics(period_records, cost_rate, fold_initial_balance, periods_per_year_this_fold)

        if not period_records:
            fold_results.append(FoldEvalResult(
                fold=fold, n_train_obs=len(train_slice), n_test_obs=len(test_slice), ic=None, ic_pvalue=None, mean_label=None,
                fitted_params=fitted_params, skipped=True, skip_reason="no timestamp in this fold's TEST window had enough assets to form both portfolio legs",
            ))
            continue

        fold_results.append(FoldEvalResult(
            fold=fold, n_train_obs=len(train_slice), n_test_obs=len(test_slice),
            ic=None, ic_pvalue=None, mean_label=None,
            fitted_params=fitted_params, metrics=metrics, trades=period_records,
        ))

    n_evaluated = sum(1 for f in fold_results if not f.skipped)
    return OOSResult(
        evaluation_type="cross_sectional_ranking",
        config=config,
        folds=fold_results,
        asset_universe=sorted(working[asset_col].unique().tolist()),
        feature_name=feature_col, label_name=forward_return_col,
        strategy_name=strategy_name, strategy_version=strategy_version,
        data_fingerprint=data_fingerprint,
        random_seed=config.seed,
        n_folds_total=len(fold_results),
        n_folds_evaluated=n_evaluated,
        n_folds_skipped=len(fold_results) - n_evaluated,
    )
