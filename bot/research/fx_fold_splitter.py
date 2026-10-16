"""
bot/research/fx_fold_splitter.py
Deterministic train/holdout + walk-forward fold splitter for FX DCA research.

Implements the split specification from FX-DCA-PROTOCOL-1-A1 (Part 4):
  - Primary split: 70% calibration / 48-candle embargo / 30% holdout (sealed)
  - Walk-forward: 5 expanding folds within calibration, each with its own
    48-candle embargo between train end and test start
  - Strategy parameters are FIXED across all folds — no refitting

Usage:
    from bot.research.fx_fold_splitter import make_fx_splits
    splits = make_fx_splits(df)
    # splits["calibration"] → DataFrame (26,175 rows)
    # splits["holdout"]     → DataFrame (11,238 rows)  ← SEALED until ready
    # splits["folds"]       → list of 5 dicts, each with "train"/"test" DataFrames
"""
# claude code changed: new file — 2026-10-16; implements FX-DCA-PROTOCOL-1-A1 Part 4 fold spec

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import pandas as pd


# ── Protocol constants (FX-DCA-PROTOCOL-1-A1 Part 4) ────────────────────────
_HOLDOUT_FRAC = 0.30           # last 30 % → holdout (sealed)
_CALIBRATION_FRAC = 0.70       # first 70 % minus embargo → calibration
_EMBARGO_CANDLES = 48          # mandatory gap at every split boundary
_N_FOLDS = 5                   # expanding-window folds within calibration
_MIN_INITIAL_TRAIN = 5_000     # minimum first-fold training window


@dataclass(frozen=True)
class FxFold:
    """One walk-forward fold: train slice, embargo slice, test slice."""
    fold_num: int               # 1-indexed
    train: pd.DataFrame
    embargo: pd.DataFrame
    test: pd.DataFrame

    @property
    def train_size(self) -> int:
        return len(self.train)  # claude code changed: convenience property

    @property
    def test_size(self) -> int:
        return len(self.test)   # claude code changed: convenience property


@dataclass(frozen=True)
class FxSplits:
    """Complete split result for one pair's dataset."""
    calibration: pd.DataFrame    # training universe; folds run within this
    calibration_embargo: pd.DataFrame  # 48-candle gap before holdout — never used in simulation
    holdout: pd.DataFrame        # SEALED until calibration analysis is complete
    folds: List[FxFold]          # 5 expanding walk-forward folds within calibration
    total_candles: int
    protocol_id: str


def make_fx_splits(
    df: pd.DataFrame,
    protocol_id: str = "FX-DCA-PROTOCOL-1-A1",
) -> FxSplits:
    """
    Split a sorted 1h FX candle DataFrame per the protocol specification.

    Parameters
    ----------
    df : pd.DataFrame
        Must have a 'timestamp' column and be sorted ascending (earliest first).
        Caller is responsible for having already validated the data (no dups, no
        OHLC violations) — this function enforces no data contract beyond sorting.
    protocol_id : str
        Stored in the returned FxSplits for provenance; do not change unless
        running under a different protocol version.

    Returns
    -------
    FxSplits
        All indices are position-based (df is reset-indexed internally before
        slicing so callers need not worry about the original index).

    Raises
    ------
    ValueError
        If the DataFrame is too short to produce a valid split.
    """  # claude code changed: new — FX-DCA-PROTOCOL-1-A1 Part 4 deterministic split
    df = df.sort_values("timestamp").reset_index(drop=True)  # defensive sort
    n = len(df)

    if n < _MIN_INITIAL_TRAIN + _EMBARGO_CANDLES * 2 + 100:
        raise ValueError(
            f"DataFrame too short ({n} rows) to produce a valid split. "
            f"Minimum ~{_MIN_INITIAL_TRAIN + _EMBARGO_CANDLES * 2 + 100} rows required."
        )

    # ── Primary split ────────────────────────────────────────────────────────
    # holdout = last 30 %; embargo = 48 candles immediately before holdout
    # calibration = everything before the embargo
    holdout_size = round(n * _HOLDOUT_FRAC)
    embargo_end = n - holdout_size          # first holdout row index
    embargo_start = embargo_end - _EMBARGO_CANDLES  # first embargo row index
    calibration_size = embargo_start       # rows [0, embargo_start)

    if calibration_size < _MIN_INITIAL_TRAIN + _EMBARGO_CANDLES:
        raise ValueError(
            f"Calibration period ({calibration_size} rows) too short for walk-forward folds."
        )

    cal = df.iloc[:calibration_size].copy()
    cal_embargo = df.iloc[embargo_start:embargo_end].copy()
    holdout = df.iloc[embargo_end:].copy()

    # ── Walk-forward folds within calibration ────────────────────────────────
    # Fold test slice size = floor((cal_size - min_train) / n_folds)
    # Folds 1–(N-1): fixed test_size; fold N: remainder (slightly smaller or equal)
    # Each fold has a 48-candle embargo between its train end and test start.
    cal_n = calibration_size
    test_slice = (cal_n - _MIN_INITIAL_TRAIN) // _N_FOLDS  # claude code changed: per protocol arithmetic

    if test_slice < _EMBARGO_CANDLES + 1:
        raise ValueError(
            f"Test slice size ({test_slice}) is smaller than the embargo ({_EMBARGO_CANDLES}). "
            "Cannot create valid folds — insufficient calibration data."
        )

    folds: List[FxFold] = []
    test_start = _MIN_INITIAL_TRAIN + _EMBARGO_CANDLES  # first test candle of fold 1

    for fold_num in range(1, _N_FOLDS + 1):
        train_end = test_start - _EMBARGO_CANDLES  # last non-embargo train row (exclusive)
        fold_embargo_start = train_end
        fold_embargo_end = test_start

        if fold_num < _N_FOLDS:
            test_end = test_start + test_slice
        else:
            test_end = cal_n  # fold 5: use remaining calibration candles

        if test_end > cal_n:
            test_end = cal_n

        fold_train = cal.iloc[:train_end].copy()
        fold_embargo = cal.iloc[fold_embargo_start:fold_embargo_end].copy()
        fold_test = cal.iloc[test_start:test_end].copy()

        folds.append(FxFold(
            fold_num=fold_num,
            train=fold_train,
            embargo=fold_embargo,
            test=fold_test,
        ))

        # Advance: next fold's test starts after this fold's test ends
        test_start = test_end + _EMBARGO_CANDLES  # claude code changed: embargo before next fold's test

        if test_start >= cal_n:
            break  # guard: stop if we've exhausted calibration

    return FxSplits(
        calibration=cal,
        calibration_embargo=cal_embargo,
        holdout=holdout,
        folds=folds,
        total_candles=n,
        protocol_id=protocol_id,
    )


def print_split_report(splits: FxSplits, pair: str = "") -> None:
    """Print a human-readable split summary for verification."""  # claude code changed: new — for verify/audit use
    label = f"[{pair}] " if pair else ""
    print(f"\n{label}FX Split Report — {splits.protocol_id}")
    print(f"  Total candles  : {splits.total_candles:,}")
    print(f"  Calibration    : {len(splits.calibration):,} candles  "
          f"({splits.calibration['timestamp'].iloc[0]} → {splits.calibration['timestamp'].iloc[-1]})")
    print(f"  Cal embargo    : {len(splits.calibration_embargo):,} candles  "
          f"({splits.calibration_embargo['timestamp'].iloc[0]} → {splits.calibration_embargo['timestamp'].iloc[-1]})")
    print(f"  Holdout        : {len(splits.holdout):,} candles  "
          f"({splits.holdout['timestamp'].iloc[0]} → {splits.holdout['timestamp'].iloc[-1]})  ← SEALED")
    print(f"\n  Walk-forward folds (within calibration):")
    print(f"  {'Fold':>4}  {'Train':>8}  {'Emb':>4}  {'Test':>7}  {'Train start':>20}  {'Test start':>20}  {'Test end':>20}")
    for f in splits.folds:
        ts = f.train["timestamp"].iloc[-1] if len(f.train) else "—"
        te_start = f.test["timestamp"].iloc[0] if len(f.test) else "—"
        te_end = f.test["timestamp"].iloc[-1] if len(f.test) else "—"
        print(f"  {f.fold_num:>4}  {f.train_size:>8,}  {len(f.embargo):>4}  {f.test_size:>7,}  {str(ts)[:20]:>20}  {str(te_start)[:20]:>20}  {str(te_end)[:20]:>20}")
    # Verify arithmetic
    total_accounted = (len(splits.calibration) + len(splits.calibration_embargo)
                       + len(splits.holdout))
    ok = "OK" if total_accounted == splits.total_candles else f"MISMATCH (got {total_accounted})"
    print(f"\n  Arithmetic check: {total_accounted:,} = {splits.total_candles:,}  [{ok}]")
