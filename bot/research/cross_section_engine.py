# ═══════════════════════════════════════════════════════════════════════════════
# bot/research/cross_section_engine.py
#
# INSTITUTIONAL CROSS-SECTIONAL MEAN REVERSION ENGINE
#
# Version  : 1.0 (Research Grade)
# Author   : QuantiBot Pro Research Pipeline
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT IS CROSS-SECTIONAL MEAN REVERSION?
# ─────────────────────────────────────────────────────────────────────────────
#
# Traditional (single-asset) thinking:
#   "BTC dropped 2% — will it recover?"
#
# Cross-sectional thinking:
#   "BTC dropped 2% but ETH only dropped 0.5%.
#    BTC is the weakest coin in the universe RIGHT NOW.
#    Does the weakest coin tend to mean-revert and outperform next hour?"
#
# This is the core hypothesis of cross-sectional mean reversion:
#   Assets that underperform their peers tend to recover.
#   Assets that outperform their peers tend to pull back.
#
# It is NOT about predicting direction of the market.
# It is about predicting RELATIVE performance between assets.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY INSTITUTIONS USE THIS APPROACH
# ─────────────────────────────────────────────────────────────────────────────
#
# 1. MARKET-NEUTRAL: Long the weakest, short the strongest.
#    If crypto crashes 10%, you lose on longs but win on shorts.
#    Net exposure to market direction is near zero.
#
# 2. DIVERSIFIED: 7 assets, signals on every candle.
#    More opportunities, less reliance on any single trade.
#
# 3. STATISTICALLY TESTABLE: The IC of a rank feature is measurable.
#    We can prove or disprove the hypothesis with our validator.
#
# 4. ECONOMICALLY MOTIVATED: Mean reversion has a real mechanism.
#    Retail traders chase momentum, creating temporary mispricings
#    that institutional mean reversion strategies exploit.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT THIS ENGINE PRODUCES (OUTPUT)
# ─────────────────────────────────────────────────────────────────────────────
#
# For each symbol, at each timestamp, we calculate:
#
#   return_1h              Raw 1-hour percentage return
#   return_1h_winsor       Winsorised return (outliers capped)
#   cs_rank                Cross-sectional rank (1=weakest, 7=strongest)
#   cs_rank_norm           Normalised rank (-1 to +1, centred at zero)
#   cs_zscore              Z-score vs universe (std devs from cross-sectional mean)
#   cs_percentile          Percentile within universe (0 to 100)
#   cs_momentum_3h         3-hour rolling cross-sectional momentum
#   cs_momentum_6h         6-hour rolling cross-sectional momentum
#   cs_reversal_signal     Mean reversion signal (+1=long candidate, -1=short)
#   forward_return_1h      What actually happened next (label for IC testing)
#
# ─────────────────────────────────────────────────────────────────────────────
# HOW TO USE THIS IN THE RESEARCH PIPELINE
# ─────────────────────────────────────────────────────────────────────────────
#
#   Step 1: Load all 7 symbol CSVs into a dictionary of DataFrames
#   Step 2: Pass dictionary to CrossSectionEngine.calculate_all()
#   Step 3: Engine returns enriched DataFrames ready for feature_validator
#   Step 4: Run validator with forward_return_col='forward_return_1h'
#   Step 5: If IC > 0.03 consistently — you have a real edge
#
# ═══════════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations          # Allows modern type hints in older Python

import logging                              # Professional structured logging
import warnings                             # Suppress pandas/numpy warnings

from typing import Dict, List, Optional     # Type hints for function signatures

import numpy as np                          # Numerical operations
import pandas as pd                         # DataFrame operations

from bot.instruments import symbols_for_asset_class, ASSET_CLASS_CRYPTO  # claude code changed: new — universe expansion mission, see cointegration_engine.py's identical change for the full rationale

# Suppress noisy pandas warnings that don't affect correctness
warnings.filterwarnings('ignore')


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# claude code changed: was an independent hand-typed 20-symbol list — see
# cointegration_engine.py's identical change for the full rationale. Now
# derived from bot.instruments' single registry, converted to this
# module's pre-existing underscore convention.
UNIVERSE: List[str] = [
    s.replace("/", "_") for s in symbols_for_asset_class(ASSET_CLASS_CRYPTO)
]

# Winsorisation percentiles — caps extreme returns before ranking
# A flash crash candle would distort all z-scores without this
WINSOR_LOWER: float = 0.01    # Clip bottom 1% of returns upward
WINSOR_UPPER: float = 0.99    # Clip top 99% of returns downward

# claude code changed: new — Phase B remediation (forensic-audit finding
# P1-1). Minimum observations needed before an expanding-window quantile is
# trustworthy enough to use as a clip boundary. Estimating a 1st/99th
# percentile (the tails of the distribution) from too few points is
# unstable — the rule of thumb of roughly 10 observations per percentage
# point of tail mass (10 / 0.01 = 1000) is what this is set from, not
# picked arbitrarily. Below this many prior observations, a row keeps its
# raw (unclipped) return rather than being winsorised against a boundary
# estimated from too little history.
WINSOR_MIN_PERIODS: int = 1000

# Minimum assets needed at any timestamp for valid cross-section
# With only 2 assets, a "rank" carries no information
MIN_ASSETS_FOR_CROSS_SECTION: int = 4

# Rolling momentum windows in hours (each candle = 1 hour)
MOMENTUM_WINDOW_SHORT: int = 3    # 3-hour momentum
MOMENTUM_WINDOW_LONG:  int = 6    # 6-hour momentum

# How many periods ahead to calculate the label (what we predict)
FORWARD_RETURN_PERIODS: int = 1   # Predict 1 hour ahead


# ═══════════════════════════════════════════════════════════════════════════════
# CROSS SECTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class CrossSectionEngine:
    """
    Institutional-grade cross-sectional mean reversion feature engine.

    This engine does NOT generate buy/sell signals.
    It generates RESEARCH FEATURES that feature_validator can test.

    Separation of responsibilities:
        Feature generation  →  this engine
        Feature validation  →  feature_validator_v2_institutional.py
        Signal execution    →  future execution engine (not built yet)

    This enforces the institutional principle:
        Prove the edge exists BEFORE building a strategy around it.
    """

    def __init__(
        self,
        winsor_lower:   float = WINSOR_LOWER,
        winsor_upper:   float = WINSOR_UPPER,
        min_assets:     int   = MIN_ASSETS_FOR_CROSS_SECTION,
        momentum_short: int   = MOMENTUM_WINDOW_SHORT,
        momentum_long:  int   = MOMENTUM_WINDOW_LONG,
        winsor_min_periods: int = WINSOR_MIN_PERIODS,   # claude code changed: new — Phase B (P1-1)
    ) -> None:
        """
        Store configuration. No data processing in the constructor.
        Data only flows when calculate_all() is called.
        """

        self.winsor_lower   = winsor_lower     # Lower clip boundary
        self.winsor_upper   = winsor_upper     # Upper clip boundary
        self.min_assets     = min_assets       # Minimum universe size
        self.momentum_short = momentum_short   # Short momentum window
        self.momentum_long  = momentum_long    # Long momentum window
        self.winsor_min_periods = winsor_min_periods   # claude code changed: new — Phase B (P1-1)

        logger.info("CrossSectionEngine initialised")
        logger.info(f"  Universe        : {len(UNIVERSE)} symbols")
        logger.info(f"  Winsorisation   : [{winsor_lower:.0%}, {winsor_upper:.0%}]")
        logger.info(f"  Min assets      : {min_assets}")
        logger.info(f"  Momentum windows: {momentum_short}h / {momentum_long}h")
        logger.info(f"  Forward return  : {FORWARD_RETURN_PERIODS}h")


    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC: calculate_all()
    # ─────────────────────────────────────────────────────────────────────────

    def calculate_all(
        self,
        data: Dict[str, pd.DataFrame],
    ) -> Dict[str, pd.DataFrame]:
        """
        Main entry point. Runs the full cross-sectional pipeline.

        Orchestrates all steps in the correct order:
            1  Validate inputs
            2  Calculate raw 1-hour returns per asset
            3  Winsorise returns (outlier control)
            4  Build shared timestamp matrix (align all assets)
            5  Calculate cross-sectional features at each timestamp
            6  Calculate rolling momentum features
            7  Calculate mean reversion signal
            8  Calculate forward return labels (what we predict)
            9  Merge features back into each symbol DataFrame
            10 Log summary statistics

        Parameters
        ----------
        data : Dict[str, pd.DataFrame]
            Symbol → OHLCV DataFrame. Must have DatetimeIndex and 'close'.

        Returns
        -------
        Dict[str, pd.DataFrame]
            Same dictionary enriched with cross-sectional feature columns.
        """

        logger.info("=" * 70)
        logger.info("CROSS-SECTIONAL MEAN REVERSION ENGINE — STARTING")
        logger.info("=" * 70)

        # Step 1: Refuse bad inputs at the door
        data = self._validate_inputs(data)

        # Step 2: Raw percentage return per asset
        data = self._calculate_returns(data)

        # Step 3: Cap extreme returns before they distort z-scores
        data = self._winsorise_returns(data)

        # Step 4: Align all 7 assets to the same timestamp grid
        returns_matrix = self._build_returns_matrix(data)

        # Step 5: Rank, z-score, percentile at every timestamp
        cs_features = self._calculate_cross_sectional_features(returns_matrix)

        # Step 6: Rolling average rank over 3h and 6h
        cs_features = self._calculate_momentum_features(cs_features)

        # Step 7: Composite mean reversion signal (inverted z-score + momentum)
        cs_features = self._calculate_reversal_signal(cs_features)

        # Step 8: What actually happened next — the Y variable for IC testing
        cs_features = self._calculate_forward_returns(cs_features, returns_matrix)

        # Step 9: Put each symbol's features back into its own DataFrame
        data = self._merge_features_back(data, cs_features)

        # Step 10: Print what was produced
        self._log_summary(data)

        logger.info("CROSS-SECTIONAL ENGINE — COMPLETE")
        logger.info("=" * 70)

        return data


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1: VALIDATE INPUTS
    # ─────────────────────────────────────────────────────────────────────────

    def _validate_inputs(
        self,
        data: Dict[str, pd.DataFrame],
    ) -> Dict[str, pd.DataFrame]:
        """
        Validate every DataFrame before processing.

        Institutional principle: validate at the boundary.
        Garbage in = garbage out. We refuse garbage at the door.

        Checks:
            - Dictionary is not empty
            - Each DataFrame has a DatetimeIndex
            - Each DataFrame has a 'close' column
            - Each DataFrame has minimum 50 rows
            - Removes failing symbols rather than crashing
        """

        logger.info("Step 1: Validating inputs...")

        # Reject completely empty input
        if not data:
            raise ValueError(
                "CrossSectionEngine received an empty data dictionary. "
                "Pass at least 4 symbol DataFrames."
            )

        valid_data: Dict[str, pd.DataFrame] = {}

        for symbol, df in data.items():

            # Skip empty DataFrames
            if df is None or df.empty:
                logger.warning(f"  {symbol}: SKIPPED — empty DataFrame")
                continue

            # Require DatetimeIndex for time-series alignment in Step 4
            if not isinstance(df.index, pd.DatetimeIndex):
                logger.warning(
                    f"  {symbol}: SKIPPED — index is {type(df.index).__name__}, "
                    f"need DatetimeIndex. Set timestamp as index first."
                )
                continue

            # Require 'close' column for return calculation in Step 2
            if "close" not in df.columns:
                logger.warning(
                    f"  {symbol}: SKIPPED — missing 'close' column. "
                    f"Available: {list(df.columns)}"
                )
                continue

            # Require minimum rows for rolling momentum features
            min_rows = max(self.momentum_long * 10, 50)
            if len(df) < min_rows:
                logger.warning(
                    f"  {symbol}: SKIPPED — only {len(df)} rows, "
                    f"need {min_rows} minimum."
                )
                continue

            # Symbol passed — work on a copy, never mutate input
            logger.info(f"  {symbol}: ✓ ({len(df):,} rows)")
            valid_data[symbol] = df.copy()

        # Need minimum assets for cross-section to be meaningful
        if len(valid_data) < self.min_assets:
            raise ValueError(
                f"Only {len(valid_data)} valid symbols. "
                f"Need at least {self.min_assets}."
            )

        logger.info(f"  {len(valid_data)}/{len(data)} symbols passed validation")
        return valid_data


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2: RAW RETURNS
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_returns(
        self,
        data: Dict[str, pd.DataFrame],
    ) -> Dict[str, pd.DataFrame]:
        """
        Calculate simple 1-hour percentage returns.

        Formula: return_1h = (close_t / close_t-1) - 1

        Why percentage return and not log return?
            For cross-sectional ranking, percentage return is standard.
            Log returns are used for portfolio-level statistics later.

        First row is always NaN — no previous price exists.
        This is correct behaviour.
        """

        logger.info("Step 2: Calculating 1-hour returns...")

        for symbol, df in data.items():

            # pct_change() = (current / previous) - 1 for every row
            df["return_1h"] = df["close"].pct_change()

            data[symbol] = df

            logger.info(
                f"  {symbol}: mean={df['return_1h'].mean():.5f} "
                f"std={df['return_1h'].std():.5f}"
            )

        return data


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3: WINSORISATION
    # ─────────────────────────────────────────────────────────────────────────

    def _winsorise_returns(
        self,
        data: Dict[str, pd.DataFrame],
    ) -> Dict[str, pd.DataFrame]:
        """
        Cap extreme return outliers before cross-sectional ranking.

        Why winsorise?
            A 40% flash crash on one coin would make it the most extreme
            outlier in every z-score for hours, distorting all features.
            Winsorisation caps it at the 1st percentile value — it still
            reads as "very negative" but doesn't dominate the statistics.

        We keep the raw return column for transparency.
        Winsorised return is a separate column used in all downstream features.

        claude code changed: Phase B remediation (forensic-audit finding
        P1-1). This used to compute ONE pair of clip boundaries from
        `df["return_1h"].dropna()` — the symbol's ENTIRE history — and
        apply that same pair to every row, including rows from years
        before the boundary was "known". A row near the start of a
        symbol's history was being winsorised using knowledge of that
        symbol's most extreme future moves (e.g. a 2022-01 candle's clip
        bound was informed by a crash that hadn't happened yet). Every
        downstream cross-sectional feature (cs_rank, cs_zscore,
        cs_percentile, cs_momentum_*, cs_reversal_signal — all built from
        return_1h_winsor) inherited this look-ahead.

        Fixed to a causal expanding window: each row's clip boundary is
        estimated ONLY from returns strictly BEFORE that row (`.shift(1)`
        — the boundary used to clip row t is the quantile of rows
        [0..t-1], never including row t itself or anything later). This
        is deliberately the strict interpretation ("row t's bound must not
        even see row t's own realised return") rather than an
        inclusive-of-today expanding window, so there is no ambiguity
        about whether a row's own value could have influenced its own
        clip boundary.

        Before `winsor_min_periods` prior observations exist (see that
        constant's docstring for why 1000), there isn't enough history to
        estimate a stable 1st/99th percentile — those early rows keep
        their raw, unclipped return rather than being clipped against a
        boundary estimated from too few points.
        """

        logger.info("Step 3: Winsorising returns (causal expanding window)...")

        for symbol, df in data.items():

            returns = df["return_1h"]

            # Expanding quantile through row t-1 only (shift(1) excludes
            # row t itself) — strictly historical information at time t.
            lower_bound = (
                returns.expanding(min_periods=self.winsor_min_periods)
                .quantile(self.winsor_lower)
                .shift(1)
            )
            upper_bound = (
                returns.expanding(min_periods=self.winsor_min_periods)
                .quantile(self.winsor_upper)
                .shift(1)
            )

            # Row-wise clip against that row's own (causal) boundary.
            # Rows with no boundary yet (NaN — not enough prior history)
            # fall back to the raw, unclipped return via combine_first.
            winsorised = returns.clip(lower=lower_bound, upper=upper_bound)
            df["return_1h_winsor"] = winsorised.combine_first(returns)

            data[symbol] = df

            n_clipped = int(((df["return_1h_winsor"] != returns) & returns.notna()).sum())
            n_unbounded = int(lower_bound.isna().sum())
            logger.info(
                f"  {symbol}: {n_clipped:,} rows clipped, "
                f"{n_unbounded:,} rows too early for a stable boundary "
                f"(kept raw)"
            )

        return data


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4: BUILD RETURNS MATRIX
    # ─────────────────────────────────────────────────────────────────────────

    def _build_returns_matrix(
        self,
        data: Dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        """
        Build a wide matrix: rows = timestamps, columns = symbols.

        Why is this needed?
            Each symbol lives in its own DataFrame.
            To rank BTC against ETH against SOL at the SAME timestamp,
            we need all 7 returns on the same row.

            Wide format example:
            timestamp   | BTC_USDT | ETH_USDT | SOL_USDT | ...
            2024-01-01  |  +0.020  |  +0.010  |  -0.010  | ...
            2024-01-02  |  -0.010  |  +0.030  |  +0.020  | ...

        Uses outer join to keep all timestamps.
        Forward-fills small gaps (up to 2 periods) for exchange downtime.
        """

        logger.info("Step 4: Building returns matrix...")

        # Extract winsorised return series from each symbol, named by symbol
        series_list = [
            df["return_1h_winsor"].rename(symbol)
            for symbol, df in data.items()
        ]

        # Concatenate side by side — axis=1 adds as columns, not rows
        # join="outer" keeps all timestamps from all symbols
        returns_matrix = pd.concat(series_list, axis=1, join="outer")

        # Sort chronologically — required for rolling calculations
        returns_matrix.sort_index(inplace=True)

        # Forward-fill small gaps from exchange downtime
        # limit=2 means only fill up to 2 consecutive missing values
        returns_matrix.ffill(limit=2, inplace=True)

        logger.info(f"  Matrix shape: {returns_matrix.shape} (timestamps × symbols)")
        logger.info(f"  Range: {returns_matrix.index[0]} → {returns_matrix.index[-1]}")
        logger.info(f"  NaN cells: {returns_matrix.isna().sum().sum():,}")

        return returns_matrix


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 5: CROSS-SECTIONAL FEATURES
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_cross_sectional_features(
        self,
        returns_matrix: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        At each timestamp, compare each asset against the full universe.

        Features produced:

        cs_rank (1 to 7):
            1 = weakest coin this hour (fell most or rose least)
            7 = strongest coin this hour
            Mean reversion hypothesis: rank 1 tends to outperform next hour

        cs_rank_norm (-1 to +1):
            Normalised rank centred at zero
            -1 = absolute weakest, +1 = absolute strongest, 0 = median
            Formula: (rank - median_rank) / median_rank

        cs_zscore (approx -3 to +3):
            How many standard deviations from the cross-sectional mean?
            Z = (return_i - mean_universe) / std_universe
            Most statistically clean measure of relative strength/weakness

        cs_percentile (0 to 100):
            Percentile rank within universe
            0 = weakest, 100 = strongest, 50 = median
            More interpretable for non-technical audiences
        """

        logger.info("Step 5: Calculating cross-sectional features...")

        symbols = returns_matrix.columns.tolist()

        # ── cs_rank ─────────────────────────────────────────────────────────
        # rank() across columns at each row
        # ascending=True: lowest return = rank 1
        # na_option="keep": NaN stays NaN — don't rank missing data
        cs_rank = returns_matrix.rank(
            axis=1, method="average", na_option="keep", ascending=True
        )
        cs_rank.columns = [f"{s}__cs_rank" for s in symbols]

        # ── cs_rank_norm ─────────────────────────────────────────────────────
        # Normalise rank to [-1, +1] using the median rank as centre
        # median_rank = (N+1)/2 where N = number of valid assets at that timestamp
        n_valid     = returns_matrix.notna().sum(axis=1)   # Valid asset count per row
        median_rank = (n_valid + 1) / 2                    # Median rank at each timestamp

        # Get raw rank without renamed columns for calculation
        raw_rank = returns_matrix.rank(
            axis=1, method="average", na_option="keep", ascending=True
        )

        # Subtract median, divide by median — result is [-1, +1] approximately
        # .sub() and .div() with axis=0 broadcast the Series across all columns
        cs_rank_norm = raw_rank.sub(median_rank, axis=0).div(median_rank, axis=0)
        cs_rank_norm.columns = [f"{s}__cs_rank_norm" for s in symbols]

        # ── cs_zscore ────────────────────────────────────────────────────────
        # Standard z-score but calculated ACROSS assets at each timestamp
        # Not across time — across the universe at this single moment
        row_mean = returns_matrix.mean(axis=1)    # Universe mean return at t
        row_std  = returns_matrix.std(axis=1)     # Universe std of returns at t

        cs_zscore = (
            returns_matrix
            .sub(row_mean, axis=0)                          # Centre at zero
            .div(row_std.replace(0, np.nan), axis=0)        # Scale by std
        )
        cs_zscore.columns = [f"{s}__cs_zscore" for s in symbols]

        # ── cs_percentile ────────────────────────────────────────────────────
        # Fractional rank (0-1) multiplied by 100 to give 0-100 percentile
        cs_percentile = returns_matrix.rank(
            axis=1, method="average", na_option="keep", ascending=True, pct=True
        ) * 100
        cs_percentile.columns = [f"{s}__cs_percentile" for s in symbols]

        # Combine all features into one wide DataFrame
        cs_features = pd.concat(
            [cs_rank, cs_rank_norm, cs_zscore, cs_percentile],
            axis=1,
        )

        logger.info(f"  Generated {len(cs_features.columns)} cross-sectional columns")

        return cs_features


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 6: ROLLING MOMENTUM
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_momentum_features(
        self,
        cs_features: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Rolling average of cross-sectional rank over 3h and 6h windows.

        What does this measure?
            If BTC has been rank 1 or 2 (weakest) for 6 consecutive hours,
            that is a STRONGER mean reversion signal than being weak for
            just 1 hour. Sustained weakness creates greater snap-back potential.

        Conversely, sustained strength (rank 6-7 for 6h) is a stronger
        short candidate for mean reversion.

        The validator will tell us which window (3h vs 6h) has better IC.
        We build both and let the data decide.
        """

        logger.info("Step 6: Calculating rolling momentum features...")

        for symbol in UNIVERSE:

            rank_norm_col = f"{symbol}__cs_rank_norm"

            # Skip if this symbol was filtered out during validation
            if rank_norm_col not in cs_features.columns:
                logger.warning(f"  {symbol}: rank_norm missing, skipping momentum")
                continue

            # 3-hour rolling mean of normalised rank
            # min_periods=2 means compute with at least 2 valid rows
            cs_features[f"{symbol}__cs_momentum_3h"] = (
                cs_features[rank_norm_col]
                .rolling(window=self.momentum_short, min_periods=2)
                .mean()
            )

            # 6-hour rolling mean of normalised rank
            # Captures more sustained relative weakness/strength
            cs_features[f"{symbol}__cs_momentum_6h"] = (
                cs_features[rank_norm_col]
                .rolling(window=self.momentum_long, min_periods=3)
                .mean()
            )

        logger.info("  cs_momentum_3h and cs_momentum_6h added for each symbol")

        return cs_features


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 7: MEAN REVERSION SIGNAL
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_reversal_signal(
        self,
        cs_features: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Composite mean reversion research signal.

        This is NOT a trade signal.
        It is a single numeric feature combining z-score and momentum
        that the validator will test for IC against forward returns.

        Formula:
            reversal_signal = -(0.6 × cs_zscore + 0.4 × cs_momentum_6h)

        Why the negative sign?
            We are testing MEAN REVERSION — the opposite of the current move.
            Asset with z-score = -2 (fallen hard) → signal = +2 (long candidate)
            Asset with z-score = +2 (risen hard)  → signal = -2 (short candidate)

        Weights (0.6 / 0.4):
            Current position gets more weight than recent trend.
            These are starting-point weights — the validator will show
            whether this combination has predictive power.
            If IC is poor, we adjust the weights and retest.

        Output:
            Positive = potential long candidate (underperformer may recover)
            Negative = potential short candidate (outperformer may pull back)
        """

        logger.info("Step 7: Calculating mean reversion signal...")

        for symbol in UNIVERSE:

            zscore_col   = f"{symbol}__cs_zscore"
            momentum_col = f"{symbol}__cs_momentum_6h"

            if zscore_col not in cs_features.columns:
                logger.warning(f"  {symbol}: cs_zscore missing, skipping signal")
                continue

            if momentum_col not in cs_features.columns:
                logger.warning(f"  {symbol}: cs_momentum_6h missing, skipping signal")
                continue

            # Invert: weak assets get positive signal (long candidates)
            # Strong assets get negative signal (short candidates)
            cs_features[f"{symbol}__cs_reversal_signal"] = -(
                0.6 * cs_features[zscore_col] +     # Current cross-sectional position
                0.4 * cs_features[momentum_col]      # Recent trend direction
            )

        logger.info(
            "  cs_reversal_signal calculated "
            "(formula: -(0.6 × zscore + 0.4 × momentum_6h))"
        )

        return cs_features


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 8: FORWARD RETURN LABELS
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_forward_returns(
        self,
        cs_features: pd.DataFrame,
        returns_matrix: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Calculate what actually happened 1 hour later.

        This is the Y variable — the label the validator correlates
        all our features against to compute IC.

        Formula:
            forward_return_1h at time t = return_1h at time t+1

        Implementation:
            shift(-1) moves each value one row earlier.
            Row at timestamp t gets the value that was originally at t+1.
            The last row becomes NaN — no future data exists.

        This NaN on the last row is correct.
        The validator filters NaN labels automatically.
        """

        logger.info("Step 8: Calculating forward return labels...")

        for symbol in UNIVERSE:

            if symbol not in returns_matrix.columns:
                logger.warning(f"  {symbol}: not in returns matrix, skipping")
                continue

            # shift(-1): pull the next row's value into the current row
            # At t=10:00, forward_return_1h = return that happened at 11:00
            cs_features[f"{symbol}__forward_return_1h"] = (
                returns_matrix[symbol].shift(-1)
            )

        logger.info("  forward_return_1h added for each symbol")

        return cs_features


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 9: MERGE BACK
    # ─────────────────────────────────────────────────────────────────────────

    def _merge_features_back(
        self,
        data: Dict[str, pd.DataFrame],
        cs_features: pd.DataFrame,
    ) -> Dict[str, pd.DataFrame]:
        """
        Merge cross-sectional features back into each symbol's DataFrame.

        cs_features is wide: BTC_USDT__cs_rank, ETH_USDT__cs_rank, etc.

        For each symbol:
            1. Extract only that symbol's columns
            2. Strip the symbol prefix from column names
               BTC_USDT__cs_rank → cs_rank
            3. Join into that symbol's DataFrame on the shared DatetimeIndex

        Result: each symbol's DataFrame gets clean columns:
            cs_rank, cs_rank_norm, cs_zscore, cs_percentile,
            cs_momentum_3h, cs_momentum_6h, cs_reversal_signal,
            forward_return_1h
        """

        logger.info("Step 9: Merging features back into symbol DataFrames...")

        for symbol, df in data.items():

            # Find all columns for this symbol
            prefix = f"{symbol}__"
            symbol_cols = [c for c in cs_features.columns if c.startswith(prefix)]

            if not symbol_cols:
                logger.warning(f"  {symbol}: no features found, skipping merge")
                continue

            # Extract and rename: remove the "SYMBOL__" prefix
            symbol_features = cs_features[symbol_cols].copy()
            symbol_features.columns = [
                c.replace(prefix, "") for c in symbol_features.columns
            ]

            # Left join onto the symbol's DataFrame using the shared DatetimeIndex
            # how="left" preserves all original rows — never drops existing data
            df = df.join(symbol_features, how="left")
            data[symbol] = df

            logger.info(f"  {symbol}: {len(symbol_cols)} features merged")

        return data


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 10: SUMMARY
    # ─────────────────────────────────────────────────────────────────────────

    def _log_summary(
        self,
        data: Dict[str, pd.DataFrame],
    ) -> None:
        """
        Log a clean summary of what was produced.

        Signal mean should be near zero — the universe is balanced.
        Signal std shows the spread of opportunities across candles.
        """

        logger.info("Step 10: Summary")
        logger.info("-" * 70)

        for symbol, df in data.items():

            cs_cols = [c for c in df.columns if c.startswith("cs_")]

            if "cs_reversal_signal" in df.columns:
                sig_mean = df["cs_reversal_signal"].mean()
                sig_std  = df["cs_reversal_signal"].std()
                sig_info = f"signal mean={sig_mean:+.4f} std={sig_std:.4f}"
            else:
                sig_info = "signal not calculated"

            logger.info(
                f"  {symbol:12} | {len(df):>7,} rows | "
                f"{len(cs_cols)} cs_features | {sig_info}"
            )

        logger.info("-" * 70)
        logger.info(
            "Ready for feature_validator. "
            "Use forward_return_col='forward_return_1h'"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_cross_section_research(
    data_dir:   str = "data",
    output_dir: str = "research_data",
    interval:   str = "1h",
) -> Dict[str, pd.DataFrame]:
    """
    Standalone runner. Loads CSVs, runs engine, saves enriched output.

    Run from project root:
        python -m bot.research.cross_section_engine

    Parameters
    ----------
    data_dir   : Folder containing symbol CSVs. Default "data".
    output_dir : Folder to save enriched CSVs. Default "research_data".
    interval   : Candle interval in filenames. Default "1h".

    Returns
    -------
    Dict[str, pd.DataFrame] — enriched data ready for feature_validator.
    """

    from pathlib import Path

    logger.info("=" * 70)
    logger.info("CROSS-SECTIONAL RESEARCH — STANDALONE RUN")
    logger.info("=" * 70)

    # ── Load all 7 CSVs ──────────────────────────────────────────────────────
    data: Dict[str, pd.DataFrame] = {}

    for symbol in UNIVERSE:

        csv_path = Path(data_dir) / f"{symbol}_{interval}.csv"

        if not csv_path.exists():
            logger.warning(f"  {symbol}: not found at {csv_path}")
            continue

        try:
            df = pd.read_csv(csv_path)

            # Parse timestamp and set as DatetimeIndex — required by the engine
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                df.set_index("timestamp", inplace=True)

            # Ensure numeric OHLCV
            for col in ["open", "high", "low", "close", "volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            df.dropna(subset=["close"], inplace=True)   # Drop missing prices
            df.sort_index(inplace=True)                  # Chronological order

            data[symbol] = df
            logger.info(f"  Loaded {symbol}: {len(df):,} candles")

        except Exception as e:
            logger.error(f"  Failed to load {symbol}: {e}")

    # ── Run engine ───────────────────────────────────────────────────────────
    engine        = CrossSectionEngine()
    enriched_data = engine.calculate_all(data)

    # ── Save enriched CSVs ───────────────────────────────────────────────────
    Path(output_dir).mkdir(exist_ok=True)

    for symbol, df in enriched_data.items():
        out = Path(output_dir) / f"{symbol}_cross_section.csv"
        df.reset_index().to_csv(out, index=False)        # Save timestamp as column
        logger.info(f"  Saved: {out}")

    logger.info(
        "Complete. Next: run feature_validator on these CSVs "
        "with forward_return_col='forward_return_1h'"
    )

    return enriched_data


# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_cross_section_research(
        data_dir="data",
        output_dir="research_data",
        interval="1h",
    )