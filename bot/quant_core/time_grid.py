# ============================================================
# bot/quant_core/time_grid.py
# claude code changed: new file — Stage 2D Phase B, Stage 2C §2 and §16.
#
# One canonical hourly UTC grid keyed on candle OPEN time. Candle k covers
# [o_k, o_k + 1h); its close (the information time) is o_k + 1h.
#
#   * every series is reindexed onto the complete grid;
#   * identical duplicate rows are removed, conflicting duplicates FAIL;
#   * a missing candle stays missing (NaN) — it never becomes a consecutive
#     observation, and nothing is forward/backward filled or interpolated;
#   * rolling windows are time windows, and lag pairs never span a gap.
#
# What happens AROUND a gap beyond "keep it missing" (maximum tolerated gap,
# Kalman re-seed rule) is an open owner decision (Stage 2C §16, g_max); it is
# therefore a required argument wherever it matters, never a hidden default.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
import pandas as pd

CANDLE = pd.Timedelta(hours=1)


class GridError(ValueError):
    """The input cannot be placed on the canonical grid (fail closed)."""


@dataclass(frozen=True)
class GridFrame:
    frame: pd.DataFrame            # complete hourly grid, index = candle open time (UTC)
    n_missing: int                 # grid rows with no observation
    n_duplicates_removed: int      # identical duplicate rows dropped

    @property
    def missing_index(self) -> pd.DatetimeIndex:
        return self.frame.index[self.frame.isna().all(axis=1)]


def _as_utc_index(idx: pd.Index) -> pd.DatetimeIndex:
    di = pd.DatetimeIndex(idx)
    if di.tz is None:
        raise GridError("timestamps are timezone-naive; the canonical grid requires explicit UTC")
    return di.tz_convert("UTC")


def to_hourly_grid(df: pd.DataFrame) -> GridFrame:
    """Place `df` (DatetimeIndex = candle OPEN time, tz-aware) on the complete
    hourly UTC grid between its first and last timestamp."""
    if df.empty:
        raise GridError("empty input")
    idx = _as_utc_index(df.index)
    if (idx != idx.floor("h")).any():
        raise GridError("timestamps are not aligned to hour boundaries (open-time grid required)")
    data = df.copy()
    data.index = idx
    n_before = len(data)
    dup_mask = data.index.duplicated(keep=False)
    if dup_mask.any():
        dups = data[dup_mask]
        # identical duplicates (all columns equal, NaN == NaN) are harmless; conflicting ones are not
        conflicting = dups.groupby(level=0).apply(lambda g: len(g.drop_duplicates()) > 1)
        if conflicting.any():
            first = conflicting[conflicting].index[0]
            raise GridError(f"conflicting duplicate rows for candle {first}")
        data = data[~data.index.duplicated(keep="first")]
    n_removed = n_before - len(data)
    data = data.sort_index()
    grid = pd.date_range(data.index[0], data.index[-1], freq=CANDLE, tz="UTC")
    framed = data.reindex(grid)
    n_missing = int(framed.isna().all(axis=1).sum())
    return GridFrame(framed, n_missing, n_removed)


def close_time(open_time: pd.Timestamp) -> pd.Timestamp:
    """Information time of a candle: its close = open + 1h."""
    return open_time + CANDLE


def align_on_grid(**series: pd.Series) -> pd.DataFrame:
    """Join several grid series on the union of their complete hourly grids.
    No filling: a value missing in either input stays missing."""
    frames = []
    for name, s in series.items():
        g = to_hourly_grid(s.to_frame(name)).frame
        frames.append(g)
    out = pd.concat(frames, axis=1, join="outer")
    full = pd.date_range(out.index.min(), out.index.max(), freq=CANDLE, tz="UTC")
    return out.reindex(full)


def assert_complete_grid(index: pd.Index) -> None:
    """Raise unless `index` is a gap-free, strictly increasing hourly UTC grid
    (missing values are allowed as NaN, missing ROWS are not)."""
    di = _as_utc_index(index)
    if len(di) > 1 and not ((di[1:] - di[:-1]) == CANDLE).all():   # unit-independent (ns/us/ms)
        raise GridError("index is not a complete hourly grid (rows missing or irregular)")


def lag_pairs(series: pd.Series) -> pd.DataFrame:
    """(x_t, x_{t-1}) pairs for t where BOTH values exist on adjacent grid
    candles. A gap never produces a pair, so AR(1)/ADF-type regressions cannot
    span a missing candle."""
    assert_complete_grid(series.index)
    out = pd.DataFrame({"x": series, "x_lag": series.shift(1)})
    return out.dropna()


def segments(series: pd.Series) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    """Contiguous runs of non-missing values on the grid, as (first, last)
    candle open times."""
    assert_complete_grid(series.index)
    present = series.notna().to_numpy()
    runs, start = [], None
    for i, p in enumerate(present):
        if p and start is None:
            start = i
        if not p and start is not None:
            runs.append((series.index[start], series.index[i - 1]))
            start = None
    if start is not None:
        runs.append((series.index[start], series.index[-1]))
    return runs


def max_gap_in_window(series: pd.Series, window_hours: int) -> pd.Series:
    """Longest run of missing candles inside each trailing window (current
    candle included). Used to apply an owner-declared gap tolerance."""
    assert_complete_grid(series.index)
    missing = series.isna().astype(int).to_numpy()
    run = np.zeros(len(missing), dtype=int)
    for i, m in enumerate(missing):
        run[i] = (run[i - 1] + 1) if (m and i > 0) else (1 if m else 0)
    run_s = pd.Series(run, index=series.index)
    return run_s.rolling(window_hours, min_periods=1).max().astype(int)


def rolling_zscore(series: pd.Series, *, window_hours: int, min_periods: int, clip: float,
                   max_gap_hours: int) -> pd.Series:
    """Causal z-score on the grid: (x_t - mean)/sd over the trailing
    `window_hours` candles ending at t (current candle included, ddof=1).
    NaN where fewer than `min_periods` observations exist in the window, where
    the window contains a run of more than `max_gap_hours` missing candles
    (owner-declared tolerance), or where sd == 0."""
    assert_complete_grid(series.index)
    if window_hours < 2 or min_periods < 2 or min_periods > window_hours:
        raise ValueError("require 2 <= min_periods <= window_hours")
    if not (clip > 0) or max_gap_hours < 0:
        raise ValueError("clip must be > 0 and max_gap_hours >= 0")
    roll = series.rolling(window_hours, min_periods=min_periods)
    mean, sd = roll.mean(), roll.std(ddof=1)
    z = (series - mean) / sd.where(sd > 0)
    too_gappy = max_gap_in_window(series, window_hours) > max_gap_hours
    z[too_gappy] = np.nan
    return z.clip(-clip, clip)
