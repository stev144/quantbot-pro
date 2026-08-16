# ═══════════════════════════════════════════════════════════════════════════════
# bot/research/walk_forward_engine.py
#
# WALK-FORWARD VALIDATION ENGINE
# Dynamic, Pair-Agnostic Out-of-Sample Testing for QuantiBot Pro
# QuantiBot Pro Research Pipeline — Missing Piece 5 of 6
#
# Version  : 1.0 (Research Grade)
# Author   : QuantiBot Pro Research Pipeline
#
# ─────────────────────────────────────────────────────────────────────────────
# WHERE THIS MODULE SITS IN THE PIPELINE
# ─────────────────────────────────────────────────────────────────────────────
#
# FEEDS FROM (reads output of these modules):
#   kalman_filter_engine.py   → research_data/<PAIR>_kalman.csv
#       Any pair's Kalman output — this engine does NOT assume AVAX/ATOM.
#   entry_exit_engine.py      → research_data/<PAIR>_strategy_summary.csv
#       Optional gate: if a pair's Missing Piece 4 summary exists, we use it
#       to decide whether the pair "deserves" walk-forward testing at all.
#
# FEEDS INTO (its output is consumed by):
#   [Missing Piece 6] live_monitor.py   → only pairs that PASS walk-forward
#                                          are eligible for paper trading
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT THIS MODULE DOES
# ─────────────────────────────────────────────────────────────────────────────
#
# entry_exit_engine.py (Missing Piece 4) proved the AVAX/ATOM strategy works
# on the FULL 2020-2025 dataset. That is an IN-SAMPLE result: every parameter
# (entry threshold, Kelly win rate, half-life) was derived by looking at the
# very data the strategy was then tested on. An in-sample result this strong
# is exactly what an over-fit strategy also looks like — the only way to tell
# the difference is to test on data the strategy has never seen.
#
# Walk-forward validation is the institutional answer to that problem:
#   1. Split history into an expanding TRAIN window and a fixed TEST window
#   2. Derive strategy parameters (Kelly win rate, IC, half-life) from TRAIN
#      ONLY — never peek at TEST while deriving them
#   3. Run the strategy on TEST using only what TRAIN taught us
#   4. Slide the window forward and repeat
#   5. Stitch every TEST period together into one continuous out-of-sample
#      (OOS) equity curve — THIS is the honest performance estimate
#
# This module is DYNAMIC: it does not hardcode AVAX/ATOM, does not hardcode
# calendar years, and does not hardcode a single pair. It discovers every
# Kalman CSV sitting in research_data/, works out each pair's own data span,
# builds folds that fit that span, and (optionally) only bothers testing
# pairs that already cleared Missing Piece 4's thresholds. Point it at a
# fresh pair's Kalman CSV tomorrow and it works with zero code changes.
#
# ─────────────────────────────────────────────────────────────────────────────
# HOW THE FOLDS ARE BUILT (ANCHORED / EXPANDING WALK-FORWARD)
# ─────────────────────────────────────────────────────────────────────────────
#
# Given a pair's full data span [data_start, data_end]:
#
#   Fold 1: TRAIN [data_start            → data_start + MIN_TRAIN_YEARS]
#           TEST  [train_end             → train_end + TEST_WINDOW_YEARS]
#   Fold 2: TRAIN [data_start            → fold 1's test_end]   (expanded)
#           TEST  [fold 1's test_end     → + TEST_WINDOW_YEARS]
#   Fold 3: TRAIN [data_start            → fold 2's test_end]   (expanded)
#           TEST  [fold 2's test_end     → data_end]            (final, clipped)
#
# This mirrors exactly the roadmap already logged by entry_exit_engine.py's
# "NEXT STEPS" block (train 2020-2022 → test 2023, train 2020-2023 → test
# 2024, train 2020-2024 → test 2025) — except the boundaries are computed
# from whatever data_start/data_end the pair's own CSV actually contains,
# not hardcoded to AVAX/ATOM's 2020-2025 history.
#
# The TRAIN window always expands (anchored at data_start) because more
# history generally means a better Kelly/IC estimate, and because this is
# how the strategy will actually behave live: it keeps learning from
# everything that has happened so far.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT "DYNAMIC PARAMETERS" MEANS HERE
# ─────────────────────────────────────────────────────────────────────────────
#
# entry_exit_engine.py's position sizer defaults to the AVAX/ATOM-specific
# constants VALIDATED_IC = 0.5245 and VALIDATED_WIN_RATE = 0.69. Those
# numbers are NOT valid for a different pair, and are not even valid for
# AVAX/ATOM's own 2020-2022 subset — they were fit on the full 2020-2025
# sample. For every fold, this engine:
#
#   1. Runs entry_exit_engine's own simulator on the TRAIN slice only
#   2. Reads back the win rate and entry IC that TRAIN actually produced
#   3. Re-estimates the spread's half-life from TRAIN only (Ornstein-
#      Uhlenbeck calibration — same technique used by statistical_edge.py)
#   4. Overrides the position sizer with those TRAIN-derived numbers
#   5. ONLY THEN runs the simulator on TEST
#
# TEST never influences its own parameters. That is the entire point.
#
# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT FILES (per pair, under research_data/walk_forward/<PAIR>/)
# ─────────────────────────────────────────────────────────────────────────────
#
#   <PAIR>_walk_forward_folds.csv        → one row per fold: train/test dates,
#                                           train-derived params, test results
#   <PAIR>_walk_forward_oos_trades.csv   → every OOS trade, stitched across
#                                           all folds, in chronological order
#   <PAIR>_walk_forward_oos_equity.csv   → the stitched OOS equity curve —
#                                           the single most important output
#   walk_forward_leaderboard.csv         → cross-pair summary when run across
#                                           every discovered pair at once
#
# ═══════════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations              # Modern type hints in Python 3.8+

import logging                                  # Structured logging — same format as every other module
import re                                       # Used to pull a pair label out of a Kalman CSV filename
import shutil                                   # Used to clean up per-fold scratch directories
import tempfile                                 # Used to hold sliced train/test CSVs before feeding them to the simulator
import warnings                                 # Suppress pandas non-critical warnings, same as entry_exit_engine.py
from dataclasses import dataclass, field        # Clean fold-result record structure
from pathlib import Path                        # Cross-platform file paths
from typing import Dict, List, Optional, Tuple  # Type annotations

import numpy as np                              # Numerical operations
import pandas as pd                             # DataFrame operations
from scipy import stats                         # linregress for half-life (OU) calibration

# This engine is a CONSUMER of entry_exit_engine.py, not a reimplementation
# of it. Every fold's train and test simulation is run through the exact
# same, already-debugged EntryExitEngine class — walk-forward is just a
# harness that feeds it different date-sliced data with different
# train-derived parameters. Reusing it (instead of copy-pasting its logic)
# means any future fix to entry_exit_engine.py automatically applies here too.
from bot.research.entry_exit_engine import (
    EntryExitEngine,                            # The Missing-Piece-4 simulator we reuse per fold
    KalmanPositionSizer,                        # Kelly sizer — we override its train-derived inputs per fold
    STRATEGY_CAPITAL_USDT,                      # Default capital, kept consistent with Missing Piece 4
    KELLY_SAFETY_FRACTION,                      # Default quarter-Kelly safety factor
    EXIT_TIME_STOP_HOURS,                       # Fallback time stop if a fold's half-life can't be estimated
    VALIDATED_IC,                               # Fallback IC if a fold's TRAIN slice can't produce its own
    VALIDATED_WIN_RATE,                         # Fallback win rate if a fold's TRAIN slice can't produce its own
    VALIDATED_HALF_LIFE,                        # Reference half-life, used to sanity-check each fold's re-estimate
)

warnings.filterwarnings('ignore')               # Suppress non-critical warnings, matching project convention


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING — same format as all other QuantiBot Pro modules
# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)            # Module-level logger

if not logger.handlers:                         # Prevent duplicate handlers on re-import
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT-SPECIFIC CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
# Every threshold below is deliberately looser than Missing Piece 4's
# in-sample thresholds. Out-of-sample performance is EXPECTED to degrade
# relative to in-sample performance — that degradation is normal, not a
# bug. The question walk-forward answers is not "does OOS match IS?" but
# "does OOS still clear the bar for real capital?"

# ── Data discovery ────────────────────────────────────────────────────────────

# Where the pipeline's Kalman CSVs and Missing-Piece-4 summaries live
RESEARCH_DATA_DIR: str = "research_data"

# Every Kalman engine output ends in this suffix, e.g. AVAX_USDT_ATOM_USDT_kalman.csv
KALMAN_FILE_SUFFIX: str = "_kalman.csv"

# Where this engine writes its own outputs — kept separate from Missing
# Piece 4's outputs so nothing gets overwritten or confused with them
WALK_FORWARD_OUTPUT_DIR: str = "research_data/walk_forward"

# ── Fold construction ─────────────────────────────────────────────────────────

# Minimum history (in years) reserved for the FIRST training window before
# any out-of-sample testing begins. Matches the project's own stated roadmap
# (train on the first two years before testing year three).
MIN_TRAIN_YEARS: int = 2

# Length (in years) of each out-of-sample test window. A full year matches
# the project's own roadmap and covers a full seasonal / regime cycle for
# crypto pairs (bull, bear, and sideways conditions each show up within a year).
TEST_WINDOW_YEARS: int = 1

# A fold's TRAIN slice must produce at least this many completed trades
# before we trust the win rate / IC it derived. Fewer trades than this and
# the "win rate" is closer to a coin flip than a statistically meaningful
# estimate — we skip sizing a live fold off of noise.
MIN_TRAIN_TRADES: int = 15

# A fold's TEST slice must contain at least this many candles to be worth
# testing at all — otherwise a test window is too short to draw any
# conclusion from (e.g. a pair whose data ends mid-year).
MIN_TEST_CANDLES: int = 24 * 30    # ~30 days of hourly candles

# ── Out-of-sample pass/fail thresholds ────────────────────────────────────────
# These are intentionally more lenient than Missing Piece 4's in-sample
# thresholds (60% win rate, Sharpe > 1.0, profit factor > 1.5). Some
# performance decay from in-sample to out-of-sample is expected and normal;
# these thresholds define how much decay is still acceptable for a strategy
# we would trust with real capital.

OOS_WIN_RATE_MIN:      float = 0.55    # vs. 60% in-sample — allow modest decay
OOS_SHARPE_MIN:        float = 0.75    # vs. 1.0 in-sample  — allow modest decay
OOS_PROFIT_FACTOR_MIN: float = 1.30    # vs. 1.5 in-sample  — allow modest decay
OOS_MAX_DRAWDOWN_PCT:  float = -0.20   # unchanged — risk tolerance does not loosen out-of-sample

# Walk-Forward Efficiency (WFE) = combined OOS Sharpe / in-sample Sharpe.
# This is the industry-standard single number for "how much of the
# in-sample edge survived contact with unseen data." Below 0.5 means the
# strategy lost more than half its edge out-of-sample — a classic
# over-fitting signature even if the raw OOS numbers still look positive.
WFE_MIN: float = 0.50

# ── Half-life (OU) re-calibration ─────────────────────────────────────────────

# Assumed candle spacing in hours, used to convert the OU decay coefficient
# into a half-life expressed in hours. Matches the Kalman engine's hourly
# candles used everywhere else in this pipeline.
CANDLE_HOURS: float = 1.0

# If a fold's TRAIN slice produces a non-mean-reverting (or statistically
# insignificant) half-life estimate, fall back to this multiple of the
# module-level EXIT_TIME_STOP_HOURS default rather than passing a bogus
# time stop into the simulator.
HALF_LIFE_FALLBACK_HOURS: float = EXIT_TIME_STOP_HOURS / 2.0

# ── Half-life sanity band ─────────────────────────────────────────────────────
# A lag-1 OU regression on a noisy, Kalman-filtered hourly spread is
# sensitive to candle-to-candle estimation noise — it can report a very
# fast "half-life" that reflects noise decay, not the real structural
# mean-reversion speed. That failure mode showed up in practice: real
# AVAX/ATOM fold data produced ~2.5h half-lives against a 119.9h full-
# sample reference, silently collapsing the time stop to ~5h and forcing
# nearly every trade to exit before genuine reversion had a chance to
# happen. Reject estimates outside this band around VALIDATED_HALF_LIFE
# rather than trusting the regression blindly.
HALF_LIFE_SANITY_MIN_HOURS: float = 24.0                          # Below one day is implausible for this pair's spread
HALF_LIFE_SANITY_MAX_RATIO: float = 5.0                            # More than 5x the full-sample reference is also suspect


# ═══════════════════════════════════════════════════════════════════════════════
# FOLD RESULT DATACLASS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FoldResult:
    """
    Stores everything about one walk-forward fold: the train window that
    derived its parameters, and the test window that was scored honestly
    out-of-sample using only those train-derived parameters.

    A fold is the walk-forward equivalent of a single TradeRecord in
    entry_exit_engine.py — one row of this becomes one row of the final
    <PAIR>_walk_forward_folds.csv report.
    """

    # ── Fold identification ────────────────────────────────────────────────────
    fold_id:            int              # Sequential fold number (1, 2, 3...)
    pair_name:           str              # Which pair this fold belongs to

    # ── Window boundaries ──────────────────────────────────────────────────────
    train_start:        pd.Timestamp     # First candle used to derive this fold's parameters
    train_end:           pd.Timestamp     # Last candle used to derive this fold's parameters (exclusive of test)
    test_start:          pd.Timestamp     # First candle scored out-of-sample
    test_end:            pd.Timestamp     # Last candle scored out-of-sample

    # ── Train-derived parameters (what TEST was NOT allowed to see) ───────────
    train_n_trades:      int   = 0        # Trades the simulator produced on TRAIN alone
    train_win_rate:      float = np.nan   # Win rate measured on TRAIN alone
    train_entry_ic:      float = np.nan   # Entry IC measured on TRAIN alone
    train_sharpe:        float = np.nan   # Sharpe measured on TRAIN alone (for WFE denominator)
    half_life_hours:     float = np.nan   # OU-calibrated half-life, from TRAIN spread only
    used_fallback_params: bool = False    # True if TRAIN was too thin to trust and defaults were used

    # ── Out-of-sample test results (the honest, unseen-data result) ───────────
    test_n_trades:       int   = 0        # Completed trades during the OOS window
    test_win_rate:        float = np.nan   # OOS win rate
    test_sharpe:          float = np.nan   # OOS Sharpe
    test_profit_factor:   float = np.nan   # OOS profit factor
    test_max_drawdown:    float = np.nan   # OOS max drawdown (fold-local, not cumulative across folds)
    test_total_pnl_usdt:  float = np.nan   # OOS total dollar P&L for this fold alone
    test_entry_ic:        float = np.nan   # OOS entry IC (sanity check against train_entry_ic)

    # ── Fold-level verdict ─────────────────────────────────────────────────────
    skipped:             bool = False     # True if this fold was skipped (insufficient train or test data)
    skip_reason:          str  = ""        # Why, if skipped

    def to_dict(self) -> Dict:
        """Convert this fold record into a flat dictionary for DataFrame construction."""
        return {
            "fold_id":              self.fold_id,
            "pair_name":             self.pair_name,
            "train_start":           self.train_start,
            "train_end":             self.train_end,
            "test_start":            self.test_start,
            "test_end":              self.test_end,
            "train_n_trades":        self.train_n_trades,
            "train_win_rate":        round(self.train_win_rate, 4) if not pd.isna(self.train_win_rate) else np.nan,
            "train_entry_ic":        round(self.train_entry_ic, 4) if not pd.isna(self.train_entry_ic) else np.nan,
            "train_sharpe":          round(self.train_sharpe, 4) if not pd.isna(self.train_sharpe) else np.nan,
            "half_life_hours":       round(self.half_life_hours, 1) if not pd.isna(self.half_life_hours) else np.nan,
            "used_fallback_params":  self.used_fallback_params,
            "test_n_trades":         self.test_n_trades,
            "test_win_rate":         round(self.test_win_rate, 4) if not pd.isna(self.test_win_rate) else np.nan,
            "test_sharpe":           round(self.test_sharpe, 4) if not pd.isna(self.test_sharpe) else np.nan,
            "test_profit_factor":    round(self.test_profit_factor, 4) if not pd.isna(self.test_profit_factor) else np.nan,
            "test_max_drawdown":     round(self.test_max_drawdown, 4) if not pd.isna(self.test_max_drawdown) else np.nan,
            "test_total_pnl_usdt":   round(self.test_total_pnl_usdt, 2) if not pd.isna(self.test_total_pnl_usdt) else np.nan,
            "test_entry_ic":         round(self.test_entry_ic, 4) if not pd.isna(self.test_entry_ic) else np.nan,
            "skipped":               self.skipped,
            "skip_reason":           self.skip_reason,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# PAIR DISCOVERY — makes this engine dynamic instead of AVAX/ATOM-specific
# ═══════════════════════════════════════════════════════════════════════════════

def discover_pairs(research_data_dir: str = RESEARCH_DATA_DIR) -> Dict[str, Path]:
    """
    Scan research_data/ for every Kalman engine output and return a mapping
    of pair_label → path to its Kalman CSV.

    This is what makes the walk-forward engine dynamic: it never assumes
    AVAX/ATOM is the only pair. Any pair that has been through
    kalman_filter_engine.py (i.e. has a *_kalman.csv sitting in
    research_data/) is automatically discoverable here.

    Returns
    -------
    Dict[str, Path]
        e.g. {"AVAX_USDT_ATOM_USDT": Path("research_data/AVAX_USDT_ATOM_USDT_kalman.csv")}
    """

    data_dir = Path(research_data_dir)              # Resolve the research data directory
    pairs: Dict[str, Path] = {}                      # Accumulator: pair label → CSV path

    if not data_dir.exists():                        # Guard against a missing directory entirely
        logger.warning(f"  research_data directory not found: {data_dir}")
        return pairs                                  # Nothing to discover — return empty

    for csv_path in sorted(data_dir.glob(f"*{KALMAN_FILE_SUFFIX}")):   # Every Kalman output file
        pair_label = csv_path.name[: -len(KALMAN_FILE_SUFFIX)]         # Strip the "_kalman.csv" suffix
        pairs[pair_label] = csv_path                                    # Record pair label → its CSV path

    logger.info(f"  Discovered {len(pairs)} pair(s) with Kalman output in {data_dir}")
    for label in pairs:                               # Log each discovered pair for visibility
        logger.info(f"    - {label}")

    return pairs                                       # Hand back the discovered pairs


def pair_deserves_testing(
    pair_label: str,
    research_data_dir: str = RESEARCH_DATA_DIR,
) -> Tuple[bool, str]:
    """
    Decide whether a pair "deserves" walk-forward testing by checking for a
    Missing-Piece-4 (entry_exit_engine.py) strategy summary and, if found,
    verifying it cleared the in-sample thresholds.

    This ties walk-forward directly into the pipeline: it is the gate
    between Missing Piece 4 (does the idea work at all, in-sample?) and
    Missing Piece 5 (does it survive unseen data?). A pair that failed
    Missing Piece 4 has no business consuming a walk-forward run.

    If no summary file is found for the pair (e.g. entry_exit_engine.py's
    current output naming is pair-agnostic — "AVAX_ATOM_strategy_summary.csv"
    regardless of which pair was actually run), we do not hard-fail; we log
    a warning and allow the walk-forward run to proceed anyway, since the
    absence of a gate file is a pipeline wiring gap, not evidence the pair
    is unfit.

    Returns
    -------
    Tuple[bool, str]
        (True/False whether the pair should be tested, human-readable reason)
    """

    data_dir = Path(research_data_dir)                                  # Resolve research data directory

    # Missing Piece 4 currently names its summary using the SYMBOL_A/SYMBOL_B
    # short names (e.g. "AVAX_ATOM_strategy_summary.csv"), not the full
    # "<SYMBOL_A>_<SYMBOL_B>_kalman.csv" pair label used for Kalman files.
    # We try a couple of plausible naming conventions rather than assuming
    # one, since this module must not depend on entry_exit_engine.py being
    # re-run per pair with any particular naming scheme.
    candidate_names = [
        f"{pair_label}_strategy_summary.csv",                            # exact pair-label match
        pair_label.replace("_USDT", "").replace("__", "_") + "_strategy_summary.csv",  # short-name match
    ]

    summary_path: Optional[Path] = None                                 # Will hold the first match found
    for name in candidate_names:                                        # Try each candidate filename
        candidate = data_dir / name                                     # Build the full candidate path
        if candidate.exists():                                          # Found a real summary file
            summary_path = candidate                                     # Remember it
            break                                                        # Stop looking — first match wins

    if summary_path is None:                                            # No Missing Piece 4 summary found at all
        reason = (
            f"No entry_exit_engine.py summary found for {pair_label} — "
            f"testing anyway, but this pair has not been gated by Missing Piece 4"
        )
        logger.warning(f"  {reason}")                                    # Surface this clearly, not silently
        return True, reason                                              # Allow testing; absence isn't disqualifying

    summary = pd.read_csv(summary_path)                                 # Load the Missing Piece 4 summary
    if summary.empty:                                                    # Malformed/empty summary file
        return True, f"Summary file {summary_path.name} was empty — testing anyway"

    s = summary.iloc[0]                                                 # Single row of summary statistics

    # Re-apply the exact same thresholds entry_exit_engine.py printed in its
    # own verdict block, so "deserves testing" means the same thing here
    # that it meant when Missing Piece 4 declared PASS.
    checks = {
        "win_rate":       s.get("win_rate", 0)       >= 0.60,
        "sharpe_ratio":   s.get("sharpe_ratio", 0)    >= 1.0,
        "profit_factor":  s.get("profit_factor", 0)   >= 1.5,
        "max_drawdown_pct": s.get("max_drawdown_pct", -1) >= -0.20,
    }

    if all(checks.values()):                                            # Every Missing Piece 4 threshold cleared
        return True, f"Missing Piece 4 summary ({summary_path.name}) passed all thresholds"

    failed = [k for k, v in checks.items() if not v]                     # Which thresholds it failed
    reason = f"Missing Piece 4 summary ({summary_path.name}) failed: {failed}"
    return False, reason                                                 # Does NOT deserve testing


# ═══════════════════════════════════════════════════════════════════════════════
# WALK-FORWARD ENGINE CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class WalkForwardEngine:
    """
    Runs anchored, expanding-window walk-forward validation for a SINGLE
    pair's Kalman output, deriving every fold's strategy parameters from
    that fold's own train slice, and scoring every fold's test slice
    strictly out-of-sample.

    This class is intentionally pair-agnostic: nothing inside it refers to
    AVAX or ATOM by name. It takes a Kalman CSV path and a pair label as
    plain arguments, exactly like entry_exit_engine.py's EntryExitEngine.
    """

    def __init__(
        self,
        min_train_years:    int   = MIN_TRAIN_YEARS,
        test_window_years:  int   = TEST_WINDOW_YEARS,
        min_train_trades:   int   = MIN_TRAIN_TRADES,
        min_test_candles:   int   = MIN_TEST_CANDLES,
        capital_usdt:        float = STRATEGY_CAPITAL_USDT,
        kelly_safety:         float = KELLY_SAFETY_FRACTION,
        output_dir:           str   = WALK_FORWARD_OUTPUT_DIR,
        resample_hours:        Optional[int] = None,   # claude code changed: new — threaded into the full-history loader; folds are then sliced from already-resampled data
    ) -> None:
        """
        Initialise the walk-forward engine with fold-construction and
        capital parameters. Every parameter has a project-derived default
        and can be overridden per call for experimentation.
        """

        self.min_train_years   = min_train_years        # Years reserved for the first anchor train window
        self.test_window_years = test_window_years       # Years per out-of-sample test window
        self.min_train_trades  = min_train_trades         # Trades required before trusting a fold's train stats
        self.resample_hours    = resample_hours              # claude code changed: new
        # claude code changed: was `min_test_candles` used unscaled — MIN_TEST_CANDLES
        # (24*30, its own comment says "~30 days of hourly candles") is fundamentally
        # an HOURS threshold expressed as a 1h-candle count. If resample_hours widens
        # each candle, the same default would silently demand 4x too many real days
        # of test data, skipping every fold. Scale it down by the same factor so
        # "~30 real days" stays the actual requirement regardless of candle width.
        self.min_test_candles  = (                          # Candles required before a test window is worth scoring
            max(1, min_test_candles // resample_hours) if resample_hours else min_test_candles
        )
        self.capital_usdt      = capital_usdt               # Capital used for every fold's simulation
        self.kelly_safety      = kelly_safety                # Quarter-Kelly safety factor, passed straight through
        self.output_dir        = Path(output_dir)             # Root directory for this engine's outputs
        self.output_dir.mkdir(parents=True, exist_ok=True)     # Ensure it exists before we try to write to it

        logger.info("WalkForwardEngine initialised")
        logger.info(f"  Min train window   : {min_train_years} year(s), anchored at data start")
        logger.info(f"  Test window        : {test_window_years} year(s) per fold")
        logger.info(f"  Min train trades   : {min_train_trades} (below this, fold uses fallback params)")
        logger.info(f"  Capital per fold   : ${capital_usdt:,.0f}")


    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC: run()
    # ─────────────────────────────────────────────────────────────────────────

    def run(
        self,
        kalman_csv:  str,
        pair_name:   str,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict]:
        """
        Run the complete walk-forward validation for one pair.

        Parameters
        ----------
        kalman_csv : str
            Path to that pair's Kalman filter output CSV.
        pair_name : str
            Human-readable pair label, used for logging and output filenames.

        Returns
        -------
        Tuple of four values:
            fold_results   : one row per fold (train/test dates, params, OOS results)
            oos_trades     : every out-of-sample trade, stitched across all folds
            oos_equity     : the stitched out-of-sample equity curve
            verdict        : dict summary of the pass/fail verdict (also printed here —
                              callers should use this returned dict rather than calling
                              _print_verdict() again themselves, which would print twice)
        """

        logger.info("=" * 70)
        logger.info(f"WALK-FORWARD ENGINE — {pair_name}")
        logger.info("QuantiBot Pro — Missing Piece 5 of 6")
        logger.info("=" * 70)

        pair_output_dir = self.output_dir / pair_name              # This pair's own output subfolder
        pair_output_dir.mkdir(parents=True, exist_ok=True)          # Create it if it doesn't exist yet

        # ── Step 1: Load the full Kalman dataset for this pair ────────────────
        # We reuse entry_exit_engine's own loader (via a throwaway instance)
        # instead of re-implementing CSV loading here. This guarantees the
        # exact same warmup-removal and NaN/inf-row cleaning that Missing
        # Piece 4 already relies on, so walk-forward and the full-sample run
        # are always looking at identically cleaned data.
        scratch_dir = pair_output_dir / "_loader_scratch"            # Disposable dir, only used to init the loader
        loader = EntryExitEngine(                                     # claude code changed: added resample_hours=
            capital_usdt=self.capital_usdt, output_dir=str(scratch_dir),
            resample_hours=self.resample_hours,                       # claude code changed: new — df_full (and every fold sliced from it) comes out already at this candle width
        )
        df_full = loader._load_kalman_data(kalman_csv)               # Load + clean the full history
        loader._validate_columns(df_full)                            # Confirm all required Kalman columns exist
        shutil.rmtree(scratch_dir, ignore_errors=True)                # Clean up the throwaway scratch directory

        if df_full.empty:                                            # Nothing usable — bail out early
            logger.warning(f"  {pair_name}: no usable candles after cleaning — skipping entirely")
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        # ── Step 2: Build the fold boundaries dynamically ─────────────────────
        folds = self._generate_folds(df_full)                        # List of (train_start, train_end, test_start, test_end)
        logger.info(f"Step 2: Generated {len(folds)} fold(s) from "
                    f"{df_full.index.min().date()} → {df_full.index.max().date()}")

        # ── Step 3: Run each fold — train derives params, test scores honestly
        fold_results: List[FoldResult] = []                          # One FoldResult per fold, in order
        oos_trade_frames: List[pd.DataFrame] = []                     # Every fold's OOS trade log, to be stitched

        for i, (train_start, train_end, test_start, test_end) in enumerate(folds, start=1):
            logger.info(f"\n{'─'*70}")
            logger.info(f"  FOLD {i}/{len(folds)}")
            logger.info(f"  TRAIN : {train_start.date()} → {train_end.date()}")
            logger.info(f"  TEST  : {test_start.date()} → {test_end.date()}")
            logger.info(f"{'─'*70}")

            result, oos_trades = self._run_single_fold(
                fold_id      = i,
                pair_name    = pair_name,
                df_full      = df_full,
                train_start  = train_start,
                train_end    = train_end,
                test_start   = test_start,
                test_end     = test_end,
                pair_output_dir = pair_output_dir,
            )
            fold_results.append(result)                               # Record this fold's summary row
            if oos_trades is not None and not oos_trades.empty:        # Only stitch in real trades
                oos_trades = oos_trades.copy()                         # Avoid mutating the original frame
                oos_trades["fold_id"] = i                              # Tag every trade with its originating fold
                oos_trade_frames.append(oos_trades)                    # Queue it for the combined OOS log

        # ── Step 4: Build the fold results table ───────────────────────────────
        fold_df = pd.DataFrame([r.to_dict() for r in fold_results])   # One row per fold, flattened

        # ── Step 5: Stitch every fold's OOS trades into one continuous log ────
        if oos_trade_frames:                                           # At least one fold produced OOS trades
            oos_trades_df = pd.concat(oos_trade_frames, ignore_index=True)
            oos_trades_df = oos_trades_df.sort_values("entry_timestamp").reset_index(drop=True)
        else:                                                          # No fold produced a single OOS trade
            oos_trades_df = pd.DataFrame()
            logger.warning(f"  {pair_name}: zero out-of-sample trades across all folds")

        # ── Step 6: Build the stitched OOS equity curve ────────────────────────
        oos_equity_df = self._build_oos_equity_curve(oos_trades_df)    # Cumulative P&L across the whole OOS journey

        # ── Step 7: Save all outputs ────────────────────────────────────────────
        self._save_outputs(pair_name, pair_output_dir, fold_df, oos_trades_df, oos_equity_df)

        # ── Step 8: Print the verdict AND capture it ────────────────────────────
        # _print_verdict() both prints the report and returns a verdict dict.
        # We do both here, once, and hand the dict back to the caller — the
        # caller must NOT call _print_verdict() again itself, or the same
        # report will print to the terminal twice.
        verdict = self._print_verdict(pair_name, fold_df, oos_trades_df, oos_equity_df)

        return fold_df, oos_trades_df, oos_equity_df, verdict


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2: BUILD FOLD BOUNDARIES
    # ─────────────────────────────────────────────────────────────────────────

    def _generate_folds(
        self,
        df_full: pd.DataFrame,
    ) -> List[Tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
        """
        Build anchored, expanding walk-forward fold boundaries from the
        pair's ACTUAL data span — never from hardcoded calendar years.

        The train window always starts at data_start and expands with each
        fold; the test window is a fixed-length slice immediately following
        the current train window. The final fold's test window is clipped
        to data_end rather than padded, so no fold ever tests on data that
        doesn't exist.

        Returns
        -------
        List of (train_start, train_end, test_start, test_end) tuples,
        one per fold, in chronological order.
        """

        data_start = df_full.index.min()                              # Earliest candle in this pair's history
        data_end   = df_full.index.max()                               # Latest candle in this pair's history

        folds: List[Tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]] = []

        train_end = data_start + pd.DateOffset(years=self.min_train_years)  # First anchor train window's end

        while train_end < data_end:                                    # Keep building folds while data remains
            test_start = train_end                                     # Test begins exactly where train ends
            test_end   = min(                                          # Test window is fixed-length, but clipped
                test_start + pd.DateOffset(years=self.test_window_years),
                data_end,
            )

            n_test_candles = len(df_full.loc[test_start:test_end])      # How many candles actually fall in this window
            if n_test_candles >= self.min_test_candles:                 # Only keep folds with a meaningful test window
                folds.append((data_start, train_end, test_start, test_end))
            else:
                logger.info(
                    f"    Skipping final partial window "
                    f"({test_start.date()} → {test_end.date()}, "
                    f"only {n_test_candles} candles) — below minimum {self.min_test_candles}"
                )

            train_end = test_end                                        # Next fold's train window expands to include this test window

        return folds                                                     # Hand back every valid fold boundary


    # ─────────────────────────────────────────────────────────────────────────
    # RUN ONE FOLD (TRAIN → DERIVE PARAMS → TEST OUT-OF-SAMPLE)
    # ─────────────────────────────────────────────────────────────────────────

    def _run_single_fold(
        self,
        fold_id:          int,
        pair_name:         str,
        df_full:            pd.DataFrame,
        train_start:         pd.Timestamp,
        train_end:            pd.Timestamp,
        test_start:            pd.Timestamp,
        test_end:               pd.Timestamp,
        pair_output_dir:         Path,
    ) -> Tuple[FoldResult, Optional[pd.DataFrame]]:
        """
        Execute one walk-forward fold end to end:
            1. Slice TRAIN and TEST from the full dataset
            2. Simulate TRAIN to derive win rate / entry IC
            3. Re-calibrate half-life from TRAIN's spread only
            4. Simulate TEST using ONLY the parameters TRAIN produced
            5. Return the fold's summary record and its OOS trade log
        """

        result = FoldResult(                                           # Start with a blank result, filled in below
            fold_id=fold_id, pair_name=pair_name,
            train_start=train_start, train_end=train_end,
            test_start=test_start, test_end=test_end,
        )

        fold_dir = pair_output_dir / f"fold_{fold_id:02d}"              # Per-fold scratch/output directory
        fold_dir.mkdir(parents=True, exist_ok=True)                     # Ensure it exists

        # ── Slice the data ──────────────────────────────────────────────────────
        train_df = df_full.loc[train_start:train_end]                   # Everything TRAIN is allowed to see
        test_df  = df_full.loc[test_start:test_end]                     # Everything TEST will be scored on

        if len(train_df) < self.min_train_trades or len(test_df) < self.min_test_candles:
            result.skipped     = True                                   # Not enough data to trust this fold at all
            result.skip_reason = (
                f"train candles={len(train_df)}, test candles={len(test_df)} — "
                f"below minimum thresholds"
            )
            logger.warning(f"    Fold {fold_id} skipped: {result.skip_reason}")
            return result, None                                         # No OOS trades to report for a skipped fold

        # ── Re-calibrate half-life from TRAIN's spread only ───────────────────
        half_life_hours = self._estimate_half_life_hours(train_df["kalman_spread"])
        if pd.isna(half_life_hours) or half_life_hours <= 0:            # OU calibration failed or non-mean-reverting
            half_life_hours = HALF_LIFE_FALLBACK_HOURS                  # Fall back to a safe project default
            logger.warning(
                f"    Fold {fold_id}: half-life estimate invalid — "
                f"falling back to {half_life_hours:.0f}h"
            )
        result.half_life_hours = half_life_hours                        # Record whichever value we ended up using

        # ── Simulate TRAIN to derive win rate / entry IC ───────────────────────
        train_csv = fold_dir / "train_slice_kalman.csv"                  # Where the sliced TRAIN data will live
        self._write_slice_csv(train_df, train_csv)                       # Write it out in the format the simulator expects

        train_engine = self._build_engine(
            capital_usdt      = self.capital_usdt,                       # Same capital baseline as every other run
            exit_time_stop_h  = int(round(2 * half_life_hours)),          # Time stop derives from THIS fold's half-life
            validated_ic       = VALIDATED_IC,                            # Bootstrap default — TRAIN hasn't taught us anything yet
            validated_win_rate  = VALIDATED_WIN_RATE,                      # Bootstrap default, same reasoning
            output_dir           = str(fold_dir / "train_run"),            # Keep train's own outputs isolated
        )
        train_trades, train_summary, _ = train_engine.run(kalman_csv=str(train_csv))

        if train_summary.empty or train_summary.iloc[0]["n_total_trades"] < self.min_train_trades:
            # TRAIN itself didn't produce enough trades to trust — this fold
            # is not skipped outright (TEST may still be scored), but it
            # falls back to the project's module-level validated defaults
            # rather than a noisy, low-sample-size estimate.
            result.used_fallback_params = True                           # Flag this fold as using fallback params
            train_win_rate = VALIDATED_WIN_RATE                          # Fall back to the project default
            train_entry_ic = VALIDATED_IC                                 # Fall back to the project default
            train_n_trades  = 0 if train_summary.empty else int(train_summary.iloc[0]["n_total_trades"])
            train_sharpe     = np.nan                                     # No reliable train Sharpe to report
            logger.warning(
                f"    Fold {fold_id}: only {train_n_trades} TRAIN trades — "
                f"using module defaults (IC={VALIDATED_IC}, win_rate={VALIDATED_WIN_RATE}) instead"
            )
        else:
            s = train_summary.iloc[0]                                     # TRAIN's own summary row
            train_n_trades  = int(s["n_total_trades"])                     # How many trades TRAIN itself produced
            train_win_rate   = float(s["win_rate"])                         # TRAIN's own win rate — feeds TEST's Kelly sizing
            train_sharpe      = float(s["sharpe_ratio"])                     # TRAIN's own Sharpe — feeds WFE later
            # TRAIN's entry IC can itself be NaN (e.g. thin sample) — fall
            # back to the project default rather than propagating NaN into
            # the position sizer for TEST.
            train_entry_ic = float(s["entry_ic"]) if not pd.isna(s["entry_ic"]) else VALIDATED_IC

        result.train_n_trades = train_n_trades                             # Record for the fold report
        result.train_win_rate  = train_win_rate                             # Record for the fold report
        result.train_entry_ic   = train_entry_ic                             # Record for the fold report
        result.train_sharpe      = train_sharpe                              # Record for the fold report

        # ── Simulate TEST using ONLY what TRAIN derived ────────────────────────
        test_csv = fold_dir / "test_slice_kalman.csv"                       # Where the sliced TEST data will live
        self._write_slice_csv(test_df, test_csv)                            # Write it out for the simulator

        test_engine = self._build_engine(
            capital_usdt       = self.capital_usdt,                         # Same capital baseline
            exit_time_stop_h   = int(round(2 * half_life_hours)),            # Same TRAIN-derived time stop
            validated_ic        = train_entry_ic,                            # TRAIN-derived — TEST never sees its own IC
            validated_win_rate   = train_win_rate,                            # TRAIN-derived — TEST never sees its own win rate
            output_dir            = str(fold_dir / "test_run"),               # Keep test's own outputs isolated
        )
        test_trades, test_summary, _ = test_engine.run(kalman_csv=str(test_csv))

        if not test_summary.empty:                                          # TEST produced at least one trade
            s = test_summary.iloc[0]                                        # TEST's own summary row
            result.test_n_trades       = int(s["n_total_trades"])            # How many trades occurred out-of-sample
            result.test_win_rate        = float(s["win_rate"])                # OOS win rate
            result.test_sharpe           = float(s["sharpe_ratio"])            # OOS Sharpe
            result.test_profit_factor     = float(s["profit_factor"])           # OOS profit factor
            result.test_max_drawdown       = float(s["max_drawdown_pct"])        # OOS max drawdown
            result.test_total_pnl_usdt      = float(s["total_pnl_usdt"])          # OOS dollar P&L
            result.test_entry_ic             = float(s["entry_ic"]) if not pd.isna(s["entry_ic"]) else np.nan
        else:                                                                # No trades fired during this test window
            result.skip_reason = "TEST window produced zero trades (not skipped, just quiet)"
            logger.info(f"    Fold {fold_id}: zero trades in TEST window — normal in quiet regimes")

        return result, test_trades                                           # Hand back this fold's summary + OOS trades


    # ─────────────────────────────────────────────────────────────────────────
    # HALF-LIFE (ORNSTEIN-UHLENBECK) RE-CALIBRATION
    # ─────────────────────────────────────────────────────────────────────────

    def _estimate_half_life_hours(self, spread: pd.Series) -> float:
        """
        Re-estimate the spread's mean-reversion half-life from a TRAIN slice
        only, using the same Ornstein-Uhlenbeck calibration methodology as
        statistical_edge.py elsewhere in this pipeline.

        The OU process:    d(spread) = λ (μ - spread) dt + noise
        Discretised:        Δspread_t = a + b · spread_{t-1} + ε_t
        Mean-reversion speed:  b  (must be negative for genuine reversion)
        Half-life (in candles): -ln(2) / b
        Half-life (in hours):    half-life (in candles) × CANDLE_HOURS

        This is deliberately re-computed per fold, per pair — the whole
        point of walk-forward is that a spread's reversion speed measured
        on 2020-2022 data should NOT be assumed to still hold in 2024, and
        a different pair's spread should never borrow AVAX/ATOM's number.

        Returns
        -------
        float
            Half-life in hours, or np.nan if the spread shows no
            statistically meaningful mean reversion on this slice.
        """

        spread_clean = spread.replace([np.inf, -np.inf], np.nan).dropna()   # Guard against inf/NaN contaminating the fit
        if len(spread_clean) < 30:                                          # Too few points for a trustworthy regression
            return np.nan

        lagged   = spread_clean.shift(1).dropna()                           # spread_{t-1}
        delta    = spread_clean.diff().dropna()                             # Δspread_t = spread_t - spread_{t-1}
        aligned  = pd.concat([lagged, delta], axis=1, join="inner")         # Align indices between the two series
        aligned.columns = ["lagged", "delta"]                               # Name the columns for clarity

        if len(aligned) < 30:                                               # Still too few points after alignment
            return np.nan

        # Ordinary least squares: Δspread = a + b · spread_{t-1}
        # scipy.stats.linregress gives us the slope (b), its p-value, and
        # the intercept in one call — no need for a heavier stats dependency.
        fit = stats.linregress(aligned["lagged"], aligned["delta"])
        b_coefficient = fit.slope                                           # Mean-reversion speed coefficient
        p_value       = fit.pvalue                                          # Statistical significance of that speed

        if b_coefficient >= 0 or p_value > 0.05:                            # Not mean-reverting, or not significantly so
            return np.nan                                                   # Caller falls back to a safe default

        # claude code changed: was `half_life_candles * CANDLE_HOURS` (CANDLE_HOURS
        # hardcoded 1.0, assuming native 1h candles). train_df's candles are now
        # self.resample_hours wide when resampling is active (see
        # WalkForwardEngine.__init__) — using the fixed 1.0 here would silently
        # understate the recalibrated half-life by that same factor, badly
        # distorting the derived time-stop (2 x half-life) for every fold.
        half_life_candles = -np.log(2) / b_coefficient                      # Standard OU half-life formula
        candle_hours       = self.resample_hours if self.resample_hours else CANDLE_HOURS
        half_life_hours    = half_life_candles * candle_hours                # Convert candles → hours

        # ── Sanity band ────────────────────────────────────────────────────────
        # A statistically significant, negative b_coefficient is NOT the same
        # thing as a trustworthy half-life — a lag-1 regression on a noisy
        # hourly spread can easily fit fast noise decay and report a
        # half-life of a few hours even when it's "significant" by p-value
        # alone. Reject anything wildly outside a sane band around the
        # full-sample reference rather than handing a corrupted time stop
        # to the simulator; the caller falls back to a safe default instead.
        too_fast = half_life_hours < HALF_LIFE_SANITY_MIN_HOURS
        too_slow = half_life_hours > VALIDATED_HALF_LIFE * HALF_LIFE_SANITY_MAX_RATIO
        if too_fast or too_slow:
            logger.warning(
                f"    Half-life estimate {half_life_hours:.1f}h is outside the "
                f"sane band [{HALF_LIFE_SANITY_MIN_HOURS:.0f}h, "
                f"{VALIDATED_HALF_LIFE * HALF_LIFE_SANITY_MAX_RATIO:.0f}h] around the "
                f"{VALIDATED_HALF_LIFE:.0f}h reference — rejecting as likely noise, "
                f"not a real structural reversion speed"
            )
            return np.nan                                                      # Caller falls back to a safe default

        return float(half_life_hours)                                        # Hand back the re-calibrated half-life


    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS: BUILD A CONFIGURED ENGINE / WRITE A DATA SLICE
    # ─────────────────────────────────────────────────────────────────────────

    def _build_engine(
        self,
        capital_usdt:       float,
        exit_time_stop_h:    int,
        validated_ic:          float,
        validated_win_rate:     float,
        output_dir:              str,
    ) -> EntryExitEngine:
        """
        Construct an EntryExitEngine configured for one fold's train or test
        run, with its Kelly position sizer overridden to use THIS fold's
        train-derived parameters instead of entry_exit_engine.py's
        AVAX/ATOM-specific module-level defaults.

        We do not modify entry_exit_engine.py to add these as constructor
        arguments — EntryExitEngine.sizer is a plain public attribute, so
        we simply replace it after construction with a KalmanPositionSizer
        built from this fold's own numbers. Every other piece of
        EntryExitEngine (entry/exit rules, cost model, exit logic) is left
        completely untouched and fully shared with Missing Piece 4.
        """

        engine = EntryExitEngine(                                           # Standard engine construction
            capital_usdt          = capital_usdt,                            # This fold's capital baseline
            exit_time_stop_hours    = exit_time_stop_h,                        # This fold's re-calibrated time stop
            kelly_safety              = self.kelly_safety,                       # Project-standard quarter-Kelly safety
            output_dir                  = output_dir,                             # Keep this run's files isolated
        )

        # Override the sizer with THIS fold's train-derived IC and win rate,
        # instead of the AVAX/ATOM-specific VALIDATED_IC / VALIDATED_WIN_RATE
        # constants EntryExitEngine's __init__ would otherwise default to.
        engine.sizer = KalmanPositionSizer(
            capital_usdt         = capital_usdt,                              # Same capital as the engine itself
            kelly_safety           = self.kelly_safety,                          # Same safety factor as the engine itself
            validated_ic             = validated_ic,                              # THIS fold's train-derived IC
            validated_win_rate         = validated_win_rate,                          # THIS fold's train-derived win rate
        )

        return engine                                                          # Hand back the fully configured engine


    def _write_slice_csv(self, df_slice: pd.DataFrame, path: Path) -> None:
        """
        Write a date-sliced piece of a pair's Kalman DataFrame back out to
        CSV in the exact shape entry_exit_engine.py's loader expects
        (a "timestamp" column, not just a DatetimeIndex).

        This is how train/test slices are handed to EntryExitEngine without
        needing to change its file-based `run(kalman_csv=...)` interface.
        """

        out = df_slice.reset_index()                                          # Turn the DatetimeIndex back into a column
        if "timestamp" not in out.columns and out.columns[0] != "timestamp":  # Defensive rename if index name differs
            out = out.rename(columns={out.columns[0]: "timestamp"})           # Ensure the loader finds "timestamp"
        out.to_csv(path, index=False)                                          # Write the slice out for the simulator to read


    # ─────────────────────────────────────────────────────────────────────────
    # BUILD THE STITCHED OUT-OF-SAMPLE EQUITY CURVE
    # ─────────────────────────────────────────────────────────────────────────

    def _build_oos_equity_curve(self, oos_trades: pd.DataFrame) -> pd.DataFrame:
        """
        Stitch every fold's out-of-sample trades into ONE continuous equity
        curve, in chronological order, as if the strategy had simply been
        running live and re-calibrating itself at each fold boundary.

        This stitched curve — not any single fold in isolation — is the
        honest performance estimate walk-forward validation exists to
        produce. A strategy that looks great in fold 1 but collapses in
        fold 3 will show that plainly here, even though each fold's own
        summary might individually look acceptable.
        """

        if oos_trades.empty:                                                   # No OOS trades across any fold
            return pd.DataFrame()

        cumulative_usdt = self.capital_usdt                                     # Start from the same capital baseline
        rows = []                                                                # Accumulator for equity curve rows

        for _, trade in oos_trades.iterrows():                                  # Walk every OOS trade in chronological order
            cumulative_usdt += trade["net_pnl_usdt"]                             # Add this trade's realised P&L
            rows.append({
                "timestamp":        trade["exit_timestamp"],                     # When this OOS trade closed
                "fold_id":           trade["fold_id"],                            # Which fold produced this trade
                "trade_id":            trade["trade_id"],                          # That fold's internal trade id
                "net_pnl_usdt":          trade["net_pnl_usdt"],                      # This trade's P&L
                "cumulative_usdt":         cumulative_usdt,                            # Running OOS total
                "cumulative_return":         cumulative_usdt / self.capital_usdt - 1.0,  # % return from OOS start
            })

        equity_df = pd.DataFrame(rows)                                           # Assemble the full equity curve
        equity_df["rolling_peak"] = equity_df["cumulative_usdt"].cummax()         # Running peak, for drawdown calc
        equity_df["drawdown_pct"] = (                                            # Standard drawdown formula
            (equity_df["cumulative_usdt"] - equity_df["rolling_peak"]) / equity_df["rolling_peak"]
        )

        return equity_df                                                          # Hand back the stitched OOS equity curve


    # ─────────────────────────────────────────────────────────────────────────
    # SAVE OUTPUTS
    # ─────────────────────────────────────────────────────────────────────────

    def _save_outputs(
        self,
        pair_name:       str,
        pair_output_dir:  Path,
        fold_df:           pd.DataFrame,
        oos_trades_df:       pd.DataFrame,
        oos_equity_df:         pd.DataFrame,
    ) -> None:
        """Persist the fold report, stitched OOS trade log, and OOS equity curve to disk."""

        if not fold_df.empty:                                                     # Only write if we actually have folds
            p = pair_output_dir / f"{pair_name}_walk_forward_folds.csv"           # Per-fold summary report
            fold_df.to_csv(p, index=False)                                        # Save it
            logger.info(f"  Saved fold report   : {p}")

        if not oos_trades_df.empty:                                               # Only write if OOS trades exist
            p = pair_output_dir / f"{pair_name}_walk_forward_oos_trades.csv"      # Stitched OOS trade log
            oos_trades_df.to_csv(p, index=False)                                  # Save it
            logger.info(f"  Saved OOS trades    : {p}")

        if not oos_equity_df.empty:                                               # Only write if an equity curve exists
            p = pair_output_dir / f"{pair_name}_walk_forward_oos_equity.csv"      # Stitched OOS equity curve
            oos_equity_df.to_csv(p, index=False)                                  # Save it
            logger.info(f"  Saved OOS equity    : {p}")


    # ─────────────────────────────────────────────────────────────────────────
    # PRINT VERDICT
    # ─────────────────────────────────────────────────────────────────────────

    def _print_verdict(
        self,
        pair_name:       str,
        fold_df:           pd.DataFrame,
        oos_trades_df:       pd.DataFrame,
        oos_equity_df:         pd.DataFrame,
    ) -> Dict:
        """
        Print a clean, readable walk-forward verdict, and return it as a
        dictionary so the multi-pair leaderboard runner can aggregate it
        across every pair tested in one pipeline pass.
        """

        sep = "=" * 70                                                             # Visual separator, matches Missing Piece 4's style

        print(f"\n{sep}")
        print(f"WALK-FORWARD VALIDATION — {pair_name}")
        print("QuantiBot Pro — Missing Piece 5 of 6")
        print(sep)

        n_folds         = len(fold_df)                                             # Total folds attempted
        n_folds_skipped  = int(fold_df["skipped"].sum()) if not fold_df.empty else 0  # Folds with insufficient data
        n_folds_scored    = n_folds - n_folds_skipped                                 # Folds that actually produced a result

        print(f"\n  Folds attempted  : {n_folds}")
        print(f"  Folds skipped    : {n_folds_skipped}  (insufficient train/test data)")
        print(f"  Folds scored     : {n_folds_scored}")

        if oos_trades_df.empty:                                                     # No out-of-sample trades at all
            print(f"\n  ✗ NO OUT-OF-SAMPLE TRADES PRODUCED — cannot validate this pair")
            print(sep)
            return {"pair_name": pair_name, "passed": False, "reason": "zero OOS trades"}

        # ── Combined out-of-sample statistics, pooled across every fold ────────
        n_total   = len(oos_trades_df)                                              # Total OOS trades across all folds
        n_winners = (oos_trades_df["net_pnl_pct"] > 0).sum()                         # OOS winning trades
        win_rate  = n_winners / n_total if n_total > 0 else 0.0                      # Combined OOS win rate

        gross_profit = oos_trades_df[oos_trades_df["net_pnl_pct"] > 0]["net_pnl_pct"].sum()   # Sum of OOS winners
        gross_loss   = abs(oos_trades_df[oos_trades_df["net_pnl_pct"] <= 0]["net_pnl_pct"].sum())  # Sum of OOS losers
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.inf      # Combined OOS profit factor

        avg_pnl_pct = oos_trades_df["net_pnl_pct"].mean()                             # Mean OOS trade return
        pnl_std      = oos_trades_df["net_pnl_pct"].std()                              # Std dev of OOS trade returns
        span_days     = (
            oos_trades_df["exit_timestamp"].max() - oos_trades_df["entry_timestamp"].min()
        ).days                                                                          # Total OOS calendar span
        trades_per_year = n_total / (span_days / 365.25) if span_days > 0 else n_total  # Annualisation factor
        oos_sharpe = (avg_pnl_pct / pnl_std) * np.sqrt(trades_per_year) if pnl_std > 0 else 0.0  # Combined OOS Sharpe

        max_drawdown = oos_equity_df["drawdown_pct"].min() if not oos_equity_df.empty else 0.0    # Combined OOS drawdown

        # ── Walk-Forward Efficiency: how much in-sample edge survived? ────────
        # Reference in-sample Sharpe = the average Sharpe TRAIN produced
        # across all scored folds. Using each fold's own train Sharpe (rather
        # than Missing Piece 4's single full-sample number) keeps the
        # comparison apples-to-apples: "the edge each fold thought it had"
        # vs. "the edge that fold's test window actually delivered."
        train_sharpes = fold_df.loc[~fold_df["skipped"], "train_sharpe"].dropna()     # Every scored fold's train Sharpe
        is_sharpe_reference = train_sharpes.mean() if len(train_sharpes) > 0 else np.nan  # Their average

        if not pd.isna(is_sharpe_reference) and is_sharpe_reference > 0:              # Reference Sharpe is usable
            wfe = oos_sharpe / is_sharpe_reference                                     # Walk-Forward Efficiency ratio
        else:
            wfe = np.nan                                                              # Cannot compute a meaningful ratio

        print(f"\n{'─'*70}")
        print("  COMBINED OUT-OF-SAMPLE RESULTS (stitched across all folds)")
        print(f"{'─'*70}")
        print(f"  Total OOS trades  : {n_total:,}")
        print(f"  OOS win rate      : {win_rate:.1%}  (target: ≥ {OOS_WIN_RATE_MIN:.0%})")
        print(f"  OOS profit factor : {profit_factor:.2f}  (target: ≥ {OOS_PROFIT_FACTOR_MIN})")
        print(f"  OOS Sharpe        : {oos_sharpe:.2f}  (target: ≥ {OOS_SHARPE_MIN})")
        print(f"  OOS max drawdown  : {max_drawdown:.2%}  (target: ≥ {OOS_MAX_DRAWDOWN_PCT:.0%})")
        print(f"  Walk-Forward Eff. : {wfe:.2f}  (target: ≥ {WFE_MIN})  "
              f"[OOS Sharpe / avg TRAIN Sharpe = {oos_sharpe:.2f} / {is_sharpe_reference:.2f}]"
              if not pd.isna(is_sharpe_reference) else
              f"  Walk-Forward Eff. : n/a  (no scored fold produced a usable train Sharpe)")

        # ── Verdict ──────────────────────────────────────────────────────────
        print(f"\n{sep}")
        print("  VERDICT")
        print(sep)

        passed, failed = [], []                                                       # Threshold checklist, same style as Missing Piece 4

        (passed if win_rate >= OOS_WIN_RATE_MIN else failed).append(
            f"OOS win rate {win_rate:.1%} {'≥' if win_rate >= OOS_WIN_RATE_MIN else '<'} {OOS_WIN_RATE_MIN:.0%}"
        )
        (passed if oos_sharpe >= OOS_SHARPE_MIN else failed).append(
            f"OOS Sharpe {oos_sharpe:.2f} {'≥' if oos_sharpe >= OOS_SHARPE_MIN else '<'} {OOS_SHARPE_MIN}"
        )
        (passed if profit_factor >= OOS_PROFIT_FACTOR_MIN else failed).append(
            f"OOS profit factor {profit_factor:.2f} {'≥' if profit_factor >= OOS_PROFIT_FACTOR_MIN else '<'} {OOS_PROFIT_FACTOR_MIN}"
        )
        (passed if max_drawdown >= OOS_MAX_DRAWDOWN_PCT else failed).append(
            f"OOS max drawdown {max_drawdown:.1%} "
            f"{'within' if max_drawdown >= OOS_MAX_DRAWDOWN_PCT else 'exceeds'} {OOS_MAX_DRAWDOWN_PCT:.0%}"
        )
        if not pd.isna(wfe):                                                           # Only check WFE if it was computable
            (passed if wfe >= WFE_MIN else failed).append(
                f"Walk-Forward Efficiency {wfe:.2f} {'≥' if wfe >= WFE_MIN else '<'} {WFE_MIN}"
            )

        for item in passed:                                                            # Print every threshold that cleared
            print(f"  ✓ {item}")
        for item in failed:                                                             # Print every threshold that missed
            print(f"  ✗ {item}")

        all_passed = len(failed) == 0                                                   # Overall pass requires zero failures

        if all_passed:
            print(f"\n  ✓✓ WALK-FORWARD PASSED")
            print(f"  {pair_name} survived out-of-sample testing — eligible for Missing Piece 6")
            print(f"  Next: python -m bot.research.live_monitor")
        else:
            print(f"\n  ✗ WALK-FORWARD DID NOT PASS")
            print(f"  {pair_name} is NOT ready for live monitoring or real capital")
            print(f"  Review which folds degraded most in "
                  f"{pair_name}_walk_forward_folds.csv before re-tuning")

        print(sep)

        # Return a flat dict — this is what feeds the cross-pair leaderboard
        return {
            "pair_name":        pair_name,
            "n_folds":            n_folds,
            "n_folds_scored":       n_folds_scored,
            "oos_trades":             n_total,
            "oos_win_rate":             round(win_rate, 4),
            "oos_sharpe":                 round(oos_sharpe, 4),
            "oos_profit_factor":            round(profit_factor, 4) if not np.isinf(profit_factor) else 999.0,
            "oos_max_drawdown":               round(max_drawdown, 4),
            "walk_forward_efficiency":          round(wfe, 4) if not pd.isna(wfe) else np.nan,
            "passed":                              all_passed,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE RUNNERS
# ═══════════════════════════════════════════════════════════════════════════════

def run_walk_forward_for_pair(
    kalman_csv:  str,
    pair_name:    str,
    output_dir:    str   = WALK_FORWARD_OUTPUT_DIR,
    capital:        float = STRATEGY_CAPITAL_USDT,
    resample_hours:  Optional[int] = None,   # claude code changed: new — see WalkForwardEngine.__init__
) -> Dict:
    """
    Run walk-forward validation for exactly one pair.

    Run from project root:
        python -m bot.research.walk_forward_engine --pair AVAX_USDT_ATOM_USDT

    (or call this function directly with a specific Kalman CSV path)

    Returns
    -------
    Dict
        The pair's verdict summary, suitable for aggregation into a leaderboard.
    """

    engine = WalkForwardEngine(                                             # Build the engine with project defaults
        capital_usdt=capital, output_dir=output_dir,
        resample_hours=resample_hours,                                     # claude code changed: new
    )
    # engine.run() already prints the verdict once internally (Step 8) and
    # returns it as the 4th value — calling _print_verdict() again here
    # would print the exact same report a second time, so we don't.
    fold_df, oos_trades_df, oos_equity_df, verdict = engine.run(kalman_csv=kalman_csv, pair_name=pair_name)
    return verdict                                                            # Hand the verdict back to the caller


def run_walk_forward_pipeline(
    research_data_dir:          str  = RESEARCH_DATA_DIR,
    output_dir:                   str  = WALK_FORWARD_OUTPUT_DIR,
    require_missing_piece4_pass:   bool = True,
    capital:                         float = STRATEGY_CAPITAL_USDT,
) -> pd.DataFrame:
    """
    The fully dynamic entry point: discover every pair with a Kalman CSV in
    research_data/, optionally gate each one on its own Missing Piece 4
    result, walk-forward test every pair that deserves it, and produce a
    single cross-pair leaderboard.

    This is what makes the module "dynamic such that it can test any pair
    that deserves to be tested from our data pipeline" — point it at
    research_data/ after running kalman_filter_engine.py and
    entry_exit_engine.py on any number of pairs (AVAX/ATOM today, forex or
    equities pairs tomorrow) and it tests every one of them automatically.

    Run from project root:
        python -m bot.research.walk_forward_engine

    Parameters
    ----------
    research_data_dir : str
        Where to look for *_kalman.csv files.
    output_dir : str
        Where to write per-pair walk-forward results and the leaderboard.
    require_missing_piece4_pass : bool
        If True (default), only walk-forward test pairs whose Missing
        Piece 4 summary already cleared its own thresholds. Set to False
        to test every discovered pair regardless of in-sample performance
        (useful for research/curiosity, not for deployment decisions).
    capital : float
        Capital used for every fold's simulation, across every pair.

    Returns
    -------
    pd.DataFrame
        The cross-pair leaderboard — one row per pair tested.
    """

    logger.info("=" * 70)
    logger.info("WALK-FORWARD PIPELINE — DISCOVERING PAIRS")
    logger.info("QuantiBot Pro — Missing Piece 5 of 6")
    logger.info("=" * 70)

    pairs = discover_pairs(research_data_dir)                                # Every pair with a Kalman CSV on disk
    if not pairs:                                                             # Nothing to test at all
        logger.warning("  No pairs discovered — run kalman_filter_engine.py first")
        return pd.DataFrame()

    leaderboard_rows: List[Dict] = []                                         # One row per pair tested, for the leaderboard

    for pair_name, kalman_path in pairs.items():                              # Walk-forward test every discovered pair
        if require_missing_piece4_pass:                                       # Gate on Missing Piece 4, if requested
            deserves_it, reason = pair_deserves_testing(pair_name, research_data_dir)
            if not deserves_it:                                               # Pair failed its in-sample thresholds
                logger.info(f"\n  Skipping {pair_name}: {reason}")
                leaderboard_rows.append({
                    "pair_name": pair_name, "passed": False,
                    "n_folds": 0, "n_folds_scored": 0, "oos_trades": 0,
                    "oos_win_rate": np.nan, "oos_sharpe": np.nan,
                    "oos_profit_factor": np.nan, "oos_max_drawdown": np.nan,
                    "walk_forward_efficiency": np.nan,
                })
                continue                                                       # Move on to the next discovered pair
            else:
                logger.info(f"\n  {pair_name} deserves testing: {reason}")

        verdict = run_walk_forward_for_pair(                                   # Run the full walk-forward for this pair
            kalman_csv = str(kalman_path),
            pair_name   = pair_name,
            output_dir   = output_dir,
            capital       = capital,
        )
        leaderboard_rows.append(verdict)                                       # Add this pair's verdict to the leaderboard

    leaderboard_df = pd.DataFrame(leaderboard_rows)                            # Assemble the full cross-pair leaderboard

    leaderboard_path = Path(output_dir) / "walk_forward_leaderboard.csv"       # Where the leaderboard gets saved
    Path(output_dir).mkdir(parents=True, exist_ok=True)                       # Ensure the output directory exists
    leaderboard_df.to_csv(leaderboard_path, index=False)                       # Save the leaderboard to disk

    logger.info("\n" + "=" * 70)
    logger.info("WALK-FORWARD PIPELINE — LEADERBOARD")
    logger.info("=" * 70)
    logger.info(f"\n{leaderboard_df.to_string(index=False)}")                  # Print the leaderboard for quick review
    logger.info(f"\n  Saved leaderboard  : {leaderboard_path}")

    n_passed = int(leaderboard_df["passed"].sum()) if "passed" in leaderboard_df.columns else 0
    logger.info(f"\n  {n_passed}/{len(leaderboard_df)} pair(s) passed walk-forward validation")

    if n_passed > 0:                                                            # At least one pair is ready to progress
        logger.info(
            "\n  NEXT STEPS IN THE QUANTIBOT PRO PIPELINE:"
            "\n  Missing Piece 6 — Live Monitoring"
            "\n  python -m bot.research.live_monitor"
            "\n"
            "\n  Then: paper trade the passing pair(s) for 6 weeks before real capital"
        )

    return leaderboard_df                                                       # Hand back the full leaderboard


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    """
    Run from project root:
        python -m bot.research.walk_forward_engine

    Runs the fully dynamic multi-pair pipeline by default: discovers every
    pair in research_data/, gates each on its Missing Piece 4 result, and
    walk-forward tests every pair that deserves it.
    """
    run_walk_forward_pipeline(
        research_data_dir            = RESEARCH_DATA_DIR,
        output_dir                     = WALK_FORWARD_OUTPUT_DIR,
        require_missing_piece4_pass     = True,
        capital                           = STRATEGY_CAPITAL_USDT,
    )