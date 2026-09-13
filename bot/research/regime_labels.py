# ============================================================
# bot/research/regime_labels.py
#
# REGIME-CONDITIONAL RESEARCH — CANONICAL REGIME TAXONOMY (Phase 2)
#
# claude code changed: new file — Regime-Conditional Quantitative Research
# mission. This is the ONE canonical, versioned source of "what regime was
# the market in at timestamp t" for RESEARCH use (statistical conditioning),
# deliberately separate from, but REUSING what's provably correct in, four
# pre-existing "regime" surfaces found during this mission's forensic pass:
#
#   - bot.engines.regime_detector.RegimeDetector — the LIVE/backtest engine.
#     Proven causal (every indicator uses only trailing rolling/ewm windows),
#     but architecturally a POINT-IN-TIME classifier: detect(df) returns one
#     RegimeResult for df's LAST row. Producing a full historical label
#     series by calling it once per row in an expanding-window loop is
#     O(n^2) — confirmed too slow at native hourly resolution during this
#     session's earlier ad hoc regime-conditioning check (had to fall back
#     to daily resolution + forward-fill).
#
#   - bot.backtesting.regime_precomputer.precompute_regime_results() — a
#     SECOND forensic finding, not part of bot/research/ or bot/research_lab/
#     (missed in the initial Phase 0 sweep of those two directories; found
#     when checking bot/tests/ for existing regime coverage). This already
#     solves the exact O(n^2) problem above: it vectorizes RegimeDetector's
#     four indicator formulas (ADX, ATR ratio, EMA spread, BB width) in one
#     pass over the whole DataFrame, and has its own dedicated equivalence
#     test (RegimePrecomputerEquivalenceTest) proving it produces BYTE-
#     IDENTICAL RegimeResults to calling detect() per-candle. Its own
#     docstring warns that its four formulas are "copied verbatim... any
#     drift... would silently make backtests disagree with the live bot" —
#     i.e. it is already the designated single source of truth for these
#     indicator values, and a second independent reimplementation here
#     would be exactly the kind of drift risk that warning exists to
#     prevent. THIS MODULE THEREFORE CALLS precompute_regime_results()
#     directly for its ADX/ATR-ratio values (see _compute_adx_and_atr_ratio
#     below) rather than re-deriving them — the only part reimplemented
#     independently here is EMA fast/slow (a single, low-risk `.ewm().mean()`
#     line, not the multi-step Wilder-smoothing math the warning is about).
#
#     What regime_precomputer.py does NOT give this module: its final
#     `regime` field is RegimeDetector's own MUTUALLY EXCLUSIVE 4-state
#     classification, where HIGH_VOLATILITY always OVERRIDES and replaces
#     any trend classification — a trend+volatility combination like
#     "TRENDING_UP + HIGH_VOLATILITY" (which Phase 7 of this mission
#     explicitly names as a combination to test) can never occur in that
#     scheme by construction. The mission's Phase 2 taxonomy needs trend
#     state and volatility state as two INDEPENDENT, combinable dimensions
#     — a genuinely different taxonomy shape, not a relabeling of the live
#     one — so this module reuses the live engine's proven indicator VALUES
#     while applying its own, separate classification logic on top of them.
#
#   - bot.research.feature_validator.MarketRegimeDetector — vectorized
#     (O(n), cheap) but a DIFFERENT taxonomy (CRASH/RECOVERY/TRENDING/
#     VOLATILE/RANGING, no up/down split) that stays local to that file's
#     own IC-by-regime report and never propagates downstream.
#
#   - bot.research.feature_decay_analyzer.REGIME_WINDOWS — not a classifier
#     at all: hand-typed CALENDAR date ranges tied to crypto market
#     narrative ("luna_crash", "ftx_crash", ...). Meaningless for Forex,
#     and silently drops any window with too few observations (a `continue`
#     with no reported reason) — the root cause identified during this
#     mission's forensic investigation for why a "regime breakdown" report
#     can come back sparse/empty with no explanation.
#
# This module produces the mission's requested MINIMUM taxonomy:
#   trend_state:      TRENDING_UP | TRENDING_DOWN | RANGING
#   volatility_state:  LOW_VOLATILITY | NORMAL_VOLATILITY | HIGH_VOLATILITY
#   regime_label:      "{trend_state}_{volatility_state}" (their cross —
#                       9 combined cells, bounded, per the mission's own
#                       "avoid unnecessary combinatorial explosion" rule)
#
# DESIGN DECISIONS (Phase 2's required documentation):
#   - Input features: ADX (trend strength), EMA fast/slow direction (trend
#     direction), ATR-vs-its-own-trailing-baseline ratio (volatility level).
#     Same formulas as bot.engines.regime_detector, vectorized across the
#     whole series in one pass instead of recomputed per-row.
#   - Lookback: ADX period 14, EMA 20/50, ATR period 14 vs a 50-period
#     baseline — identical defaults to the live engine, for continuity/
#     comparability, NOT re-tuned for either asset class (re-tuning risks
#     curve-fitting the regime boundaries to a specific result, which the
#     mission's absolute rules forbid).
#   - Timestamp behavior: every value at row t is computed from rows
#     [t - lookback, t] only (trailing rolling/ewm windows, .shift(1) for
#     "previous close" — never .shift(-n) or a centered window). Verified
#     directly by test_regime_labels.py's prefix-invariance check, not
#     merely asserted here.
#   - Parameters are FIXED, not learned/optimized — see RegimeTaxonomyConfig.
#     A future taxonomy version may change these, but only via an explicit
#     version bump, never a silent in-place edit (Phase 2's versioning
#     requirement).
#   - Minimum sample requirement: 30 observations per regime cell, matching
#     feature_validator.py's own existing MarketRegimeDetector-adjacent
#     convention (feature_validator.py:884, `if regime_mask.sum() > 30`) —
#     reused rather than inventing a second, different floor.
#   - Warmup rows (fewer than `min_periods` observations available) get
#     trend_state/volatility_state = NaN, never a default regime — the
#     mission's explicit rule "missing regime labels must never silently
#     become a valid regime."
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from bot.backtesting.regime_precomputer import precompute_regime_results
from bot.engines.regime_detector import RegimeDetector

REGIME_TAXONOMY_VERSION = "1.0.0"

TREND_UP = "TRENDING_UP"
TREND_DOWN = "TRENDING_DOWN"
TREND_RANGE = "RANGING"
TREND_STATES = (TREND_UP, TREND_DOWN, TREND_RANGE)

VOL_LOW = "LOW_VOLATILITY"
VOL_NORMAL = "NORMAL_VOLATILITY"
VOL_HIGH = "HIGH_VOLATILITY"
VOL_STATES = (VOL_LOW, VOL_NORMAL, VOL_HIGH)

# claude code changed: new — the bounded set of combined regime cells this
# taxonomy version supports. 3 trend x 3 vol = 9, matching Phase 7's own
# "do not explode the number of combinations unnecessarily" instruction.
COMBINED_REGIME_LABELS = tuple(f"{t}_{v}" for t in TREND_STATES for v in VOL_STATES)

MIN_REGIME_OBS = 30  # claude code changed: new — reused from feature_validator.py's existing precedent, not a second, drifting floor


@dataclass(frozen=True)
class RegimeTaxonomyConfig:
    """claude code changed: new. Every threshold explicit and named — no
    magic numbers buried in the computation below. version must be bumped
    whenever any field here changes (Phase 2's versioning requirement);
    nothing in this module does that automatically, by design — a version
    bump is a deliberate, reviewed act, not a side effect of a parameter
    tweak."""
    version: str = REGIME_TAXONOMY_VERSION
    adx_period: int = 14
    adx_trend_threshold: float = 25.0     # same as bot.engines.regime_detector's institutional-standard default
    ema_fast: int = 20
    ema_slow: int = 50
    atr_period: int = 14
    atr_baseline_period: int = 50
    vol_low_threshold: float = 0.8        # atr_ratio at/below this -> LOW_VOLATILITY
    vol_high_threshold: float = 1.7       # atr_ratio at/above this -> HIGH_VOLATILITY (matches regime_detector's atr_extreme_multiplier)
    min_periods: int = 60                 # matches RegimeDetector.min_candles — rows before this many valid observations get NaN labels, never a guessed regime


def _compute_adx_and_atr_ratio(df: pd.DataFrame, config: RegimeTaxonomyConfig) -> tuple:
    """claude code changed: new — delegates to
    bot.backtesting.regime_precomputer.precompute_regime_results(), the
    already-tested, production-exact vectorization of RegimeDetector's ADX
    and ATR-ratio formulas (see this module's header comment for why a
    second independent reimplementation was deliberately avoided). Returns
    (adx, atr_ratio) as two float Series aligned to df's index, rounded to
    2 decimals by the precomputer itself — plenty of precision for this
    module's coarser bucket thresholds (25.0 / 0.8 / 1.7)."""
    detector = RegimeDetector(
        adx_period=config.adx_period,
        adx_trend_threshold=config.adx_trend_threshold,
        atr_period=config.atr_period,
        atr_avg_period=config.atr_baseline_period,
        min_candles=config.min_periods,
    )
    results = precompute_regime_results(df, detector)
    adx = pd.Series([r.adx for r in results], index=df.index, dtype=float)
    atr_ratio = pd.Series([r.atr_ratio for r in results], index=df.index, dtype=float)
    return adx, atr_ratio


def compute_regime_labels(df: pd.DataFrame, config: RegimeTaxonomyConfig = RegimeTaxonomyConfig()) -> pd.DataFrame:
    """
    Vectorized, causal, timestamp-aligned regime labeling (Phase 2/Phase 4
    of the mission's execution order).

    Parameters
    ----------
    df : DataFrame with a DatetimeIndex (UTC) and open/high/low/close
        columns — the same canonical OHLCV contract every other engine in
        this research arsenal already consumes (bot.instruments'
        resolve_ohlcv_path() output shape).

    Returns
    -------
    DataFrame, same index as df, with columns:
        trend_state       : "TRENDING_UP" | "TRENDING_DOWN" | "RANGING" | NaN (warmup)
        volatility_state   : "LOW_VOLATILITY" | "NORMAL_VOLATILITY" | "HIGH_VOLATILITY" | NaN (warmup)
        regime_label       : "{trend_state}_{volatility_state}" | NaN (warmup)
        adx                : the raw ADX value backing trend_state (for audit)
        atr_ratio          : the raw ATR ratio value backing volatility_state (for audit)
        taxonomy_version    : config.version, repeated on every row (so a
                              caller who persists only this DataFrame can
                              still tell which taxonomy version produced it)
    """
    required = {"high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"compute_regime_labels requires columns {sorted(required)}, missing {sorted(missing)}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("compute_regime_labels requires a DatetimeIndex")

    adx, atr_ratio = _compute_adx_and_atr_ratio(df, config)

    ema_fast = df["close"].ewm(span=config.ema_fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=config.ema_slow, adjust=False).mean()

    is_trending = adx >= config.adx_trend_threshold
    is_up = ema_fast > ema_slow
    is_down = ema_fast < ema_slow

    trend_state = pd.Series(TREND_RANGE, index=df.index, dtype=object)
    trend_state[is_trending & is_up] = TREND_UP
    trend_state[is_trending & is_down] = TREND_DOWN
    # claude code changed: is_trending & neither up nor down (EMAs exactly
    # equal — a genuine, if rare, floating-point tie) falls through to the
    # RANGING default above, matching RegimeDetector's own "transitional
    # state" handling for the identical case.

    volatility_state = pd.Series(VOL_NORMAL, index=df.index, dtype=object)
    volatility_state[atr_ratio <= config.vol_low_threshold] = VOL_LOW
    volatility_state[atr_ratio >= config.vol_high_threshold] = VOL_HIGH

    # claude code changed: warmup handling — the mission's explicit rule
    # "missing regime labels must never silently become a valid regime."
    # A row is warmup if it's among the first min_periods rows (matching
    # RegimeDetector.min_candles' own "not enough data yet" convention —
    # precompute_regime_results() itself fills early rows with neutral
    # defaults rather than NaN, exactly like RegimeDetector._default_result
    # does for a single live call with too little history, so position is
    # the reliable warmup signal here, not an ADX/ATR NaN check) OR this
    # module's own EMA fast/slow came back NaN (a genuine gap in the
    # underlying OHLCV itself, e.g. a missing close price at this exact row).
    warmup = pd.Series(np.arange(len(df)) < config.min_periods, index=df.index)
    input_nan = ema_fast.isna() | ema_slow.isna()
    invalid = warmup | input_nan

    trend_state = trend_state.mask(invalid, other=np.nan)
    volatility_state = volatility_state.mask(invalid, other=np.nan)

    regime_label = pd.Series(np.nan, index=df.index, dtype=object)
    valid_rows = ~invalid
    regime_label[valid_rows] = trend_state[valid_rows].astype(str) + "_" + volatility_state[valid_rows].astype(str)

    return pd.DataFrame({
        "trend_state": trend_state,
        "volatility_state": volatility_state,
        "regime_label": regime_label,
        "adx": adx,
        "atr_ratio": atr_ratio,
        "taxonomy_version": config.version,
    }, index=df.index)


def assert_no_lookahead(df: pd.DataFrame, config: RegimeTaxonomyConfig, truncate_at: int) -> bool:
    """
    claude code changed: new — the practical, evidence-based causality
    check Phase 3/19 requires, not merely an assertion in a docstring.
    Computes labels on the FULL series and on a PREFIX truncated at
    `truncate_at` rows, then checks the prefix's own labels are byte-
    identical between the two runs. If a future row could ever influence
    a past label, truncating the future would change at least one
    overlapping label — this only passes if that never happens.

    Returns True if no look-ahead is detected (labels agree everywhere
    the two runs overlap), False otherwise. Raises if truncate_at is too
    small to produce any valid (non-warmup) rows to compare.
    """
    if truncate_at <= config.min_periods:
        raise ValueError(f"truncate_at={truncate_at} must exceed config.min_periods={config.min_periods} to produce any comparable rows")

    full = compute_regime_labels(df, config)
    prefix = compute_regime_labels(df.iloc[:truncate_at], config)

    compare_cols = ["trend_state", "volatility_state", "regime_label"]
    full_prefix = full.iloc[:truncate_at][compare_cols]
    prefix_vals = prefix[compare_cols]

    # NaN != NaN under == , so compare with fillna sentinel first
    a = full_prefix.fillna("__NAN__")
    b = prefix_vals.fillna("__NAN__")
    return bool((a.values == b.values).all())


@dataclass
class RegimeSampleSummary:
    """claude code changed: new — Phase 3's exact required per-regime
    report shape: Regime / Observations / Percentage / First timestamp /
    Last timestamp / Transitions / Average duration, plus an explicit
    sufficient_sample flag so a downstream reader never has to re-derive
    whether a regime's statistics are trustworthy."""
    regime: str
    observations: int
    percentage: float
    first_timestamp: Optional[pd.Timestamp]
    last_timestamp: Optional[pd.Timestamp]
    transitions_in: int
    avg_duration_periods: Optional[float]
    sufficient_sample: bool
    status: str  # "OK" | "INSUFFICIENT_SAMPLE" | "MISSING"

    def to_dict(self) -> dict:
        return {
            "regime": self.regime,
            "observations": self.observations,
            "percentage": round(self.percentage, 4),
            "first_timestamp": str(self.first_timestamp) if self.first_timestamp is not None else None,
            "last_timestamp": str(self.last_timestamp) if self.last_timestamp is not None else None,
            "transitions_in": self.transitions_in,
            "avg_duration_periods": round(self.avg_duration_periods, 2) if self.avg_duration_periods is not None else None,
            "sufficient_sample": self.sufficient_sample,
            "status": self.status,
        }


def summarize_regime_labels(
    labels: pd.Series,
    candidate_regimes: Optional[tuple] = None,
    min_obs: int = MIN_REGIME_OBS,
) -> pd.DataFrame:
    """
    Phase 3's per-dataset regime report. `labels` is a single label Series
    (e.g. trend_state, volatility_state, or the combined regime_label) —
    call once per taxonomy dimension you want reported separately, per
    Phase 7's "don't explode combinations" guidance (trend alone, vol
    alone, and the combined cross are three separate, bounded reports,
    not one giant table).

    A regime named in `candidate_regimes` but never observed in `labels`
    gets an explicit MISSING row (0 observations) — the mission's rule
    "missing regime labels must never silently become a valid regime"
    extends to reporting: an absent regime must be visibly absent, not
    just missing from the output table with no explanation.
    """
    valid = labels.dropna()
    n_total = len(valid)
    observed_regimes = sorted(valid.unique().tolist())
    regimes = sorted(set(candidate_regimes or []) | set(observed_regimes))

    # Run-length encoding once, reused for every regime's transition/duration stats
    run_id = (labels != labels.shift()).cumsum()

    rows = []
    for regime in regimes:
        mask = valid == regime
        n_obs = int(mask.sum())
        if n_obs == 0:
            rows.append(RegimeSampleSummary(
                regime=regime, observations=0, percentage=0.0,
                first_timestamp=None, last_timestamp=None,
                transitions_in=0, avg_duration_periods=None,
                sufficient_sample=False, status="MISSING",
            ))
            continue

        idx = valid.index[mask]
        # transitions INTO this regime: runs of this regime, counted once per run
        regime_runs = run_id[labels == regime]
        n_runs = regime_runs.nunique() if len(regime_runs) else 0
        avg_duration = (n_obs / n_runs) if n_runs else None

        sufficient = n_obs >= min_obs
        rows.append(RegimeSampleSummary(
            regime=regime, observations=n_obs,
            percentage=(n_obs / n_total * 100.0) if n_total else 0.0,
            first_timestamp=idx.min(), last_timestamp=idx.max(),
            transitions_in=int(n_runs), avg_duration_periods=avg_duration,
            sufficient_sample=sufficient,
            status="OK" if sufficient else "INSUFFICIENT_SAMPLE",
        ))

    return pd.DataFrame([r.to_dict() for r in rows])


def label_regime_episodes(labels: pd.Series) -> pd.DataFrame:
    """
    claude code changed: new — contiguous-episode extraction, required by
    regime_conditional_pairs.py (Phase 4/5). Cointegration/ADF testing
    assumes a genuinely time-ordered, unbroken process; naively
    concatenating every non-adjacent row that shares a regime label would
    splice together unrelated time periods and manufacture artificial
    discontinuities at each splice boundary — a real methodological risk,
    not a hypothetical one. This instead returns each CONTIGUOUS run of a
    regime as its own episode (start, end, regime, length), so a caller
    can test cointegration within one coherent time window at a time
    rather than across a Frankenstein-spliced series.
    """
    valid = labels.dropna()
    if valid.empty:
        return pd.DataFrame(columns=["regime", "start", "end", "length"])

    run_id = (valid != valid.shift()).cumsum()
    episodes = []
    for _, group in valid.groupby(run_id):
        episodes.append({
            "regime": group.iloc[0],
            "start": group.index[0],
            "end": group.index[-1],
            "length": len(group),
        })
    return pd.DataFrame(episodes)
