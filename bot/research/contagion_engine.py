# ═══════════════════════════════════════════════════════════════════════════════
# bot/research/contagion_engine.py
#
# CROSS-ASSET DIVERGENCE REVERSAL ENGINE
#
# Version  : 1.0 (Research Grade)
# Author   : QuantiBot Pro Research Pipeline
#
# ─────────────────────────────────────────────────────────────────────────────
# HYPOTHESIS 7: CROSS-ASSET DIVERGENCE REVERSAL
# ─────────────────────────────────────────────────────────────────────────────
#
# Plain English:
#   When BTC rises strongly but a specific altcoin DOESN'T follow,
#   that altcoin is statistically likely to catch up in the next 2-4 hours.
#
#   When BTC falls hard but a specific altcoin DOESN'T fall as much,
#   that altcoin is statistically likely to pull back in the next 2-4 hours.
#
# The Mechanism:
#   Crypto is a highly correlated universe. BTC is the dominant asset —
#   it accounts for ~50% of total market cap and drives most market moves.
#
#   When BTC moves and an altcoin DOESN'T follow, one of two things is true:
#
#     1. The altcoin has genuine idiosyncratic news that justifies divergence.
#        (Protocol upgrade, partnership, hack, listing)
#        In this case divergence is real and may persist.
#
#     2. The altcoin simply hasn't caught up yet — delayed price discovery
#        due to lower liquidity, fewer market makers, slower arbitrageurs.
#        In this case divergence is temporary and WILL revert.
#
#   Our hypothesis: case 2 is far more common than case 1 on the 1h timeframe.
#   Therefore sustained BTC-altcoin divergence predicts altcoin mean reversion.
#
# Why This Has Edge:
#   This is NOT a single-asset signal. It uses RELATIONSHIPS between assets.
#   Single-asset signals had zero IC in our validator (proven on 50,000 candles).
#   Cross-asset signals contain genuinely new information not in any single coin.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT THIS ENGINE PRODUCES
# ─────────────────────────────────────────────────────────────────────────────
#
# For each altcoin, at each timestamp:
#
#   btc_return_1h          BTC's 1-hour return (merged into altcoin DataFrame)
#   btc_return_3h          BTC's 3-hour cumulative return
#   btc_return_6h          BTC's 6-hour cumulative return
#   divergence_1h          BTC return minus altcoin return this hour
#   divergence_3h          Cumulative divergence over 3 hours
#   divergence_6h          Cumulative divergence over 6 hours
#   divergence_zscore_3h   How unusual is the 3h divergence vs history?
#   divergence_zscore_6h   How unusual is the 6h divergence vs history?
#   divergence_direction   +1 (altcoin lagging BTC up) or -1 (altcoin leading BTC down)
#   divergence_magnitude   Absolute size of divergence (how large is the gap?)
#   catch_up_signal        Composite signal: direction × magnitude × z-score
#   forward_return_1h      What altcoin did next 1h  (IC test label)
#   forward_return_2h      What altcoin did next 2h  (IC test label)
#   forward_return_4h      What altcoin did next 4h  (IC test label)
#   forward_return_12h     What altcoin did next 12h (IC test label)
#
# ─────────────────────────────────────────────────────────────────────────────
# HOW TO USE IN RESEARCH PIPELINE
# ─────────────────────────────────────────────────────────────────────────────
#
#   Step 1: Load all 20 CSVs into a dictionary of DataFrames
#   Step 2: Pass dictionary to ContagionEngine.calculate_all()
#   Step 3: Engine returns altcoin DataFrames enriched with divergence features
#   Step 4: Run feature_validator on each altcoin with forward_return_col set
#           to each horizon (1h, 2h, 4h, 12h)
#   Step 5: If divergence_zscore_3h or catch_up_signal shows IC > 0.05
#           consistently — you have found a genuine cross-asset edge
#
# ─────────────────────────────────────────────────────────────────────────────
# LOOK-AHEAD BIAS WARNING
# ─────────────────────────────────────────────────────────────────────────────
#
# This engine is carefully designed to prevent look-ahead bias.
# Every feature at timestamp T uses ONLY data available at or before T.
# Forward return labels use shift(-N) which correctly accesses future data
# as the LABEL ONLY — never as a feature input.
#
# The most common mistake in divergence research is accidentally using
# future BTC data to compute a "current" divergence feature.
# We prevent this by computing BTC features independently on BTC's own
# timeline, then merging onto altcoin DataFrames using left join on index.
#
# ═══════════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations          # Modern type hints

import logging                              # Structured logging
import warnings                             # Suppress pandas warnings

from typing import Dict, List, Optional, Tuple   # Type hints

import numpy as np                          # Numerical operations
import pandas as pd                         # DataFrame operations
from scipy import stats                     # Spearman IC calculation

warnings.filterwarnings('ignore')           # Suppress non-critical pandas warnings


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

# BTC is the dominant asset — all divergence is measured relative to BTC
# If you ever expand to equities, change this to the benchmark (SPX, QQQ)
#
# claude code changed: was "BTC_USDT" (underscore) — a real bug, found
# during audit. This project's canonical in-memory symbol format is
# "BASE/QUOTE" (slash) everywhere else — bot/fetch_all_symbols.py's own
# SYMBOLS list, the live bot's signal dict contract, every ExchangeAdapter
# — underscore format is ONLY ever a CSV *filename* convention
# (data/BTC_USDT_1h.csv), never the in-memory symbol itself. Mixing the
# two here (BTC in underscore, every altcoin below in slash) was the root
# cause of run_contagion_research()'s file-loading bug: see
# _symbol_to_csv_stem() below for the actual fix.
BTC_SYMBOL: str = "BTC/USDT"

# Altcoins — the assets we test the hypothesis on
# BTC is excluded because you cannot diverge from yourself
#
# claude code changed: fixed a real typo found during audit — 'ETH/USDT]'
# had a stray trailing ']' baked into the string itself, so it could never
# match any real symbol. This list is otherwise verified identical to
# bot/fetch_all_symbols.py's own SYMBOLS list (minus BTC/USDT, excluded
# here since BTC is the reference asset, not an altcoin) — same 19 symbols,
# same order, same canonical slash format.
ALTCOIN_SYMBOLS: List[str] = [
    'ETH/USDT',
    'BNB/USDT',
    'SOL/USDT',
    'ADA/USDT',
    'AVAX/USDT',
    'DOT/USDT',
    'MATIC/USDT',
    'ARB/USDT',
    'LINK/USDT',
    'UNI/USDT',
    'AAVE/USDT',
    'XRP/USDT',
    'XLM/USDT',
    'DOGE/USDT',
    'SHIB/USDT',
    'ATOM/USDT',
    'FIL/USDT',
    'APT/USDT',
    'OP/USDT',
]

# Divergence rolling windows (in 1h candles)
# We test multiple windows and let the validator tell us which has best IC
DIVERGENCE_WINDOWS: List[int] = [1, 3, 6, 12]   # 1h, 3h, 6h, 12h divergence

# Z-score lookback window for normalising divergence vs its own history
# 168 = 7 days of 1h candles — gives stable mean/std estimates
ZSCORE_LOOKBACK: int = 168    # 7 days

# Minimum lookback for z-score calculation
# Need at least this many rows before computing a meaningful z-score
ZSCORE_MIN_PERIODS: int = 48  # 2 days minimum

# Forward return horizons to generate as labels
# The validator tests each one — we don't assume which horizon has edge
FORWARD_HORIZONS: Dict[str, int] = {
    "forward_return_1h":  1,     # 1 hour ahead
    "forward_return_2h":  2,     # 2 hours ahead
    "forward_return_4h":  4,     # 4 hours ahead
    "forward_return_12h": 12,    # 12 hours ahead
}

# Winsorisation for divergence features
# Prevents extreme divergence events (exchange outages, flash crashes)
# from dominating the z-score distribution
DIVERGENCE_WINSOR_LOWER: float = 0.01   # Bottom 1%
DIVERGENCE_WINSOR_UPPER: float = 0.99   # Top 99%

# claude code changed: new — Phase 1 remediation (Research Agent architecture
# report finding). Minimum prior observations needed before an expanding-
# window quantile is a stable clip boundary — same value and same rationale
# as cross_section_engine.py's WINSOR_MIN_PERIODS, mirrored here for the
# identical causal-winsorization fix applied below.
DIVERGENCE_WINSOR_MIN_PERIODS: int = 1000

# Minimum data required before the engine processes a symbol
MIN_CANDLES: int = 500


def _symbol_to_csv_stem(symbol: str) -> str:
    """
    claude code changed: new — the actual fix for the confirmed bug found
    during audit. Every data/*.csv file on disk is named with underscores
    (data/BNB_USDT_1h.csv), but this engine's canonical symbol format is
    slash-based ("BNB/USDT", matching bot/fetch_all_symbols.py's own
    SYMBOLS list and this project's in-memory convention everywhere else).
    Building a path directly as f"{symbol}_{interval}.csv" without this
    conversion turns the '/' into a path separator — Path("data") /
    "BNB/USDT_1h.csv" silently resolves to "data/BNB/USDT_1h.csv" (a
    nonexistent subdirectory) instead of "data/BNB_USDT_1h.csv". Confirmed
    empirically: every altcoin failed to load via run_contagion_research()
    before this fix existed.

    Mirrors bot/fetch_all_symbols.py's symbol_to_filename() — same
    slash-to-underscore conversion, kept as a small local function here
    (not imported across research modules) since that one hardcodes its
    own module-level INTERVAL rather than taking one as an argument, and
    this project's research scripts are each meant to run standalone.
    """
    return symbol.replace("/", "_")   # Need at least 500 candles for rolling features


# ═══════════════════════════════════════════════════════════════════════════════
# CONTAGION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class ContagionEngine:
    """
    Cross-asset divergence reversal feature engine.

    Tests Hypothesis 7:
        When BTC moves and an altcoin doesn't follow,
        the altcoin tends to catch up in the next 2-4 hours.

    This engine does NOT generate buy/sell signals.
    It generates research features that the feature_validator tests for IC.

    The research-first principle:
        Build the feature → validate the IC → only then consider trading it.

    Architecture
    ────────────
    This engine is intentionally separate from CrossSectionEngine because:

        CrossSectionEngine  →  compares assets AGAINST EACH OTHER at each timestamp
        ContagionEngine     →  measures how each asset RELATES TO BTC over time

    They produce complementary information. The best signals will likely
    combine features from both engines — the validator will tell us.
    """

    def __init__(
        self,
        btc_symbol:           str              = BTC_SYMBOL,
        altcoin_symbols:      List[str]        = None,
        divergence_windows:   List[int]        = None,
        zscore_lookback:      int              = ZSCORE_LOOKBACK,
        zscore_min_periods:   int              = ZSCORE_MIN_PERIODS,
        forward_horizons:     Dict[str, int]   = None,
        winsor_min_periods:   int              = DIVERGENCE_WINSOR_MIN_PERIODS,   # claude code changed: new — Phase 1 causal-winsorization fix
    ) -> None:
        """
        Initialise the engine with research configuration.

        All parameters have sensible defaults based on our research design.
        They can be overridden for sensitivity analysis — testing whether
        results change when you use a 24h z-score lookback vs 168h, etc.

        Parameters
        ----------
        btc_symbol : str
            The dominant asset all divergence is measured relative to.

        altcoin_symbols : List[str]
            Assets to test the hypothesis on. Default: all 6 altcoins.

        divergence_windows : List[int]
            Rolling windows (hours) for cumulative divergence. Default: [1,3,6,12].

        zscore_lookback : int
            Rolling window for divergence z-score. Default: 168h (7 days).

        zscore_min_periods : int
            Minimum rows before computing z-score. Default: 48h (2 days).

        forward_horizons : Dict[str, int]
            Forward return label names and their periods. Default: 1h,2h,4h,12h.
        """

        self.btc_symbol         = btc_symbol
        self.altcoin_symbols    = altcoin_symbols or ALTCOIN_SYMBOLS
        self.divergence_windows = divergence_windows or DIVERGENCE_WINDOWS
        self.zscore_lookback    = zscore_lookback
        self.zscore_min_periods = zscore_min_periods
        self.forward_horizons   = forward_horizons or FORWARD_HORIZONS
        self.winsor_min_periods = winsor_min_periods   # claude code changed: new — Phase 1 causal-winsorization fix

        logger.info("ContagionEngine initialised")
        logger.info(f"  Hypothesis       : 7 — Cross-Asset Divergence Reversal")
        logger.info(f"  Dominant asset   : {btc_symbol}")
        logger.info(f"  Altcoins         : {self.altcoin_symbols}")
        logger.info(f"  Divergence windows: {self.divergence_windows}h")
        logger.info(f"  Z-score lookback : {zscore_lookback}h ({zscore_lookback//24} days)")
        logger.info(f"  Forward horizons : {list(self.forward_horizons.keys())}")


    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC: calculate_all()
    # ─────────────────────────────────────────────────────────────────────────

    def calculate_all(
        self,
        data: Dict[str, pd.DataFrame],
    ) -> Dict[str, pd.DataFrame]:
        """
        Main entry point. Run the full divergence reversal pipeline.

        Pipeline steps:
            1  Validate inputs — check BTC and altcoins are present
            2  Extract BTC returns — the reference series
            3  Calculate BTC multi-window features — raw material for divergence
            4  For each altcoin:
               4a  Calculate altcoin returns
               4b  Merge BTC features into altcoin DataFrame
               4c  Calculate divergence at each window
               4d  Winsorise divergence (outlier control)
               4e  Calculate divergence z-scores (normalise vs history)
               4f  Calculate divergence direction and magnitude
               4g  Calculate composite catch-up signal
               4h  Calculate forward return labels (what we predict)
            5  Log summary

        Parameters
        ----------
        data : Dict[str, pd.DataFrame]
            Symbol → OHLCV DataFrame with DatetimeIndex and 'close' column.

        Returns
        -------
        Dict[str, pd.DataFrame]
            Same dictionary. Altcoin DataFrames enriched with divergence
            features. BTC DataFrame enriched with its own volatility features.
        """

        logger.info("=" * 70)
        logger.info("CONTAGION ENGINE — HYPOTHESIS 7 — STARTING")
        logger.info("=" * 70)

        # ── Step 1: Validate ──────────────────────────────────────────────────
        data = self._validate_inputs(data)

        # ── Step 2: Extract BTC return series ─────────────────────────────────
        # BTC is the reference asset — all divergence is relative to BTC
        btc_df = data[self.btc_symbol].copy()
        btc_returns = self._calculate_asset_returns(btc_df, self.btc_symbol)
        btc_df["return_1h"] = btc_returns
        data[self.btc_symbol] = btc_df

        # ── Step 3: Calculate BTC multi-window features ────────────────────────
        # Cumulative BTC returns over each divergence window
        # These get merged into every altcoin DataFrame
        btc_features = self._calculate_btc_features(btc_df)
        logger.info(
            f"  BTC features calculated: {list(btc_features.columns)}"
        )

        # ── Step 4: Process each altcoin ───────────────────────────────────────
        for symbol in self.altcoin_symbols:

            if symbol not in data:
                logger.warning(f"  {symbol}: not in data dictionary, skipping")
                continue

            logger.info(f"\n  Processing {symbol}...")
            alt_df = data[symbol].copy()

            # 4a: Calculate altcoin returns
            alt_df["return_1h"] = self._calculate_asset_returns(
                alt_df, symbol
            )

            # 4b: Merge BTC features into altcoin DataFrame
            # Critical: left join on DatetimeIndex ensures no look-ahead bias
            # BTC data at timestamp T is available to altcoin model at timestamp T
            alt_df = self._merge_btc_features(alt_df, btc_features, symbol)

            # 4c: Calculate divergence at each rolling window
            # divergence = BTC_return - altcoin_return
            # Positive divergence: BTC went up more (altcoin lagging)
            # Negative divergence: altcoin went up more (altcoin leading)
            alt_df = self._calculate_divergence(alt_df, symbol)

            # 4d: Winsorise divergence features
            # Prevents exchange outages or flash crashes from dominating statistics
            alt_df = self._winsorise_divergence(alt_df, symbol)

            # 4e: Calculate divergence z-scores
            # How unusual is today's divergence relative to the past 7 days?
            # Z-score of +2 means BTC-altcoin gap is 2 std devs above normal
            alt_df = self._calculate_zscore(alt_df, symbol)

            # 4f: Calculate divergence direction and magnitude
            # Direction: which way is the divergence pointing?
            # Magnitude: how large is the gap in absolute terms?
            alt_df = self._calculate_direction_magnitude(alt_df, symbol)

            # 4g: Calculate composite catch-up signal
            # Combines direction × magnitude × z-score into one research signal
            # This is what the validator tests for IC against forward returns
            alt_df = self._calculate_catchup_signal(alt_df, symbol)

            # 4h: Calculate forward return labels
            # What did this altcoin actually do 1h, 2h, 4h, 12h later?
            # These are the Y variables for IC calculation
            alt_df = self._calculate_forward_returns(alt_df, symbol)

            # Store the enriched altcoin DataFrame
            data[symbol] = alt_df

        # ── Step 5: Log summary ───────────────────────────────────────────────
        self._log_summary(data)

        logger.info("CONTAGION ENGINE — COMPLETE")
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
        Validate that BTC and altcoin DataFrames are present and usable.

        This engine cannot run without BTC — it is the reference asset.
        If BTC is missing, we raise immediately rather than producing
        silent NaN features that would mislead the validator.
        """

        logger.info("Step 1: Validating inputs...")

        # BTC is mandatory — no BTC = no divergence features
        if self.btc_symbol not in data:
            raise ValueError(
                f"ContagionEngine requires {self.btc_symbol} in the data dictionary. "
                f"Available symbols: {list(data.keys())}"
            )

        btc_df = data[self.btc_symbol]

        # BTC must have a DatetimeIndex for timestamp alignment
        if not isinstance(btc_df.index, pd.DatetimeIndex):
            raise ValueError(
                f"{self.btc_symbol} must have a DatetimeIndex. "
                f"Got: {type(btc_df.index).__name__}"
            )

        # BTC must have close prices
        if "close" not in btc_df.columns:
            raise ValueError(
                f"{self.btc_symbol} missing 'close' column. "
                f"Available: {list(btc_df.columns)}"
            )

        # BTC must have enough history for rolling features
        if len(btc_df) < MIN_CANDLES:
            raise ValueError(
                f"{self.btc_symbol} has only {len(btc_df)} candles. "
                f"Need at least {MIN_CANDLES}."
            )

        logger.info(
            f"  {self.btc_symbol}: ✓ ({len(btc_df):,} candles, "
            f"{btc_df.index[0]} → {btc_df.index[-1]})"
        )

        # Validate each altcoin — skip rather than crash on individual failures
        valid_altcoins = []
        for symbol in self.altcoin_symbols:

            if symbol not in data:
                logger.warning(f"  {symbol}: SKIPPED — not in data dictionary")
                continue

            df = data[symbol]

            if not isinstance(df.index, pd.DatetimeIndex):
                logger.warning(f"  {symbol}: SKIPPED — no DatetimeIndex")
                continue

            if "close" not in df.columns:
                logger.warning(f"  {symbol}: SKIPPED — no close column")
                continue

            if len(df) < MIN_CANDLES:
                logger.warning(
                    f"  {symbol}: SKIPPED — only {len(df)} candles "
                    f"(need {MIN_CANDLES})"
                )
                continue

            logger.info(f"  {symbol}: ✓ ({len(df):,} candles)")
            valid_altcoins.append(symbol)

        # Update altcoin list to only include valid symbols
        self.altcoin_symbols = valid_altcoins

        if not self.altcoin_symbols:
            raise ValueError(
                "No valid altcoin DataFrames found. "
                "Check that all altcoin CSVs are loaded correctly."
            )

        logger.info(
            f"  Validation complete: BTC + {len(self.altcoin_symbols)} altcoins"
        )

        return data


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2: CALCULATE ASSET RETURNS
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_asset_returns(
        self,
        df:     pd.DataFrame,
        symbol: str,
    ) -> pd.Series:
        """
        Calculate simple 1-hour percentage returns.

        Formula: return_1h = (close_t / close_t-1) - 1

        The first row is always NaN — no previous close exists.
        This is correct and expected.

        We use percentage returns (not log returns) because:
        - Cross-asset comparison is cleaner with percentage returns
        - Divergence subtraction is more intuitive: 2% - 0.5% = 1.5% gap
        - Log returns are better for compounding, not for 1h comparisons
        """

        return df["close"].pct_change()


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3: CALCULATE BTC MULTI-WINDOW FEATURES
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_btc_features(
        self,
        btc_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Calculate BTC's cumulative returns at each divergence window.

        These features get merged into every altcoin DataFrame.
        They represent BTC's "direction and strength" over N hours.

        Features produced:
            btc_return_1h   BTC's 1-hour return (raw, this candle)
            btc_return_3h   BTC's cumulative return over 3 hours
            btc_return_6h   BTC's cumulative return over 6 hours
            btc_return_12h  BTC's cumulative return over 12 hours

        Why cumulative returns and not just the 1h return?
            A 1h return shows the most recent candle only.
            3h/6h/12h cumulative returns show the SUSTAINED TREND.

            If BTC has been up 3% over 6 hours but ETH has only moved 0.5%,
            the sustained divergence is a much stronger signal than
            a single-candle divergence of 0.5%.

        Look-ahead safety:
            All features use only data at or before timestamp T.
            rolling(N).sum() at time T uses the N candles ending at T.
            No future BTC data leaks into any calculation.
        """

        features = pd.DataFrame(index=btc_df.index)

        # Raw 1-hour return — the most recent candle's return
        features["btc_return_1h"] = btc_df["return_1h"]

        # Cumulative returns over each window
        # rolling(N).sum() computes: return_t + return_t-1 + ... + return_t-N+1
        # This is an approximation of the N-period compounded return
        # Accurate enough for 1h candles where individual returns are small
        for window in self.divergence_windows:
            if window > 1:   # Skip 1h — already captured as raw return above
                col = f"btc_return_{window}h"
                features[col] = btc_df["return_1h"].rolling(
                    window=window, min_periods=1
                ).sum()

        return features


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4b: MERGE BTC FEATURES INTO ALTCOIN DATAFRAME
    # ─────────────────────────────────────────────────────────────────────────

    def _merge_btc_features(
        self,
        alt_df:       pd.DataFrame,
        btc_features: pd.DataFrame,
        symbol:       str,
    ) -> pd.DataFrame:
        """
        Merge BTC's return features into the altcoin DataFrame.

        Why a left join on DatetimeIndex?
            Left join means we keep ALL altcoin timestamps.
            If BTC data is missing at some timestamps, those merge as NaN.
            This is safer than inner join which would silently drop rows.

        Look-ahead bias prevention:
            BTC's return_1h at timestamp T represents the candle that
            CLOSED at T. This information is genuinely available at T.
            No future information is being used.

            The forward return labels (added later) are the ONLY place
            future data appears, and they are clearly marked as labels,
            not features.
        """

        # Left join: keep all altcoin rows, attach BTC features where available
        alt_df = alt_df.join(btc_features, how="left", rsuffix="_btc")

        logger.info(
            f"    {symbol}: BTC features merged "
            f"({len(btc_features.columns)} BTC columns added)"
        )

        return alt_df


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4c: CALCULATE DIVERGENCE
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_divergence(
        self,
        alt_df: pd.DataFrame,
        symbol: str,
    ) -> pd.DataFrame:
        """
        Calculate BTC-altcoin return divergence at each time window.

        Formula:
            divergence_Nh = btc_return_Nh - altcoin_return_Nh

        Interpretation:
            divergence_3h = +0.03 means:
                BTC rose 3% over the past 3 hours
                This altcoin rose LESS than BTC (lagging)
                → Hypothesis: altcoin will catch up (positive forward return expected)

            divergence_3h = -0.03 means:
                This altcoin rose 3% MORE than BTC over 3 hours (leading)
                → Hypothesis: altcoin will give back the outperformance
                  (negative forward return expected)

        Features produced for each window N:
            divergence_Nh   BTC cumulative return minus altcoin cumulative return

        The altcoin's own cumulative return is computed inline here.
        """

        # Calculate altcoin cumulative returns at each window
        for window in self.divergence_windows:

            # Altcoin cumulative return over this window
            if window == 1:
                # 1-hour divergence: single candle comparison
                alt_cumret = alt_df["return_1h"]
                btc_cumret = alt_df["btc_return_1h"]
            else:
                # Multi-hour divergence: rolling sum comparison
                alt_cumret = alt_df["return_1h"].rolling(
                    window=window, min_periods=1
                ).sum()
                btc_col    = f"btc_return_{window}h"
                btc_cumret = alt_df.get(btc_col, pd.Series(np.nan, index=alt_df.index))

            # Divergence: positive = altcoin lagged BTC, negative = altcoin led BTC
            # The hypothesis predicts CONVERGENCE — divergence should decrease
            # So positive divergence → positive altcoin forward return (catch up)
            # And negative divergence → negative altcoin forward return (give back)
            div_col = f"divergence_{window}h"
            alt_df[div_col] = btc_cumret - alt_cumret

        logger.info(
            f"    {symbol}: divergence calculated for windows "
            f"{self.divergence_windows}h"
        )

        return alt_df


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4d: WINSORISE DIVERGENCE
    # ─────────────────────────────────────────────────────────────────────────

    def _winsorise_divergence(
        self,
        alt_df: pd.DataFrame,
        symbol: str,
    ) -> pd.DataFrame:
        """
        Cap extreme divergence values to prevent outliers contaminating z-scores.

        When might extreme divergence occur legitimately?
            Exchange outage: Binance goes down for 2 hours
            → Altcoin prices freeze while BTC moves freely
            → Artificial divergence of 5-10% appears
            → This is data noise, not a genuine signal

            Flash crash: A whale dumps 50,000 ETH in 5 minutes
            → ETH drops 15% in one candle, BTC barely moves
            → Divergence spikes massively
            → This IS a genuine event but is so extreme it's unrepresentative

        Winsorisation clips these extremes to the 1st/99th percentile,
        keeping the directional information while removing the extreme magnitude.

        claude code changed: fixed a real look-ahead leakage bug found during
        the Research Agent architecture study (Phase 1) — the original
        implementation computed lower/upper clip bounds from
        `alt_df[div_col].dropna().quantile(...)`, i.e. the ENTIRE column,
        so row t's clip boundary was estimated using divergence values from
        rows AFTER t. Identical shape to the bug already fixed in
        cross_section_engine.py's `_winsorise_returns()` this session. Fixed
        the same way: a causal expanding-window quantile, shifted by one row
        so row t's boundary only ever sees rows [0..t-1], with early rows
        (fewer than `winsor_min_periods` prior observations) kept at their
        raw, unclipped value via `combine_first` rather than skipped
        entirely — matching cross_section_engine.py's fallback exactly.
        """

        for window in self.divergence_windows:

            div_col = f"divergence_{window}h"

            if div_col not in alt_df.columns:
                continue

            raw = alt_df[div_col]   # claude code changed: keep raw series for causal winsorization + fallback

            # claude code changed: causal expanding-window bounds — shift(1)
            # excludes row t itself, so the boundary used to clip row t is
            # estimated only from strictly-prior rows, never future ones.
            lower_bound = (
                raw.expanding(min_periods=self.winsor_min_periods)
                .quantile(DIVERGENCE_WINSOR_LOWER)
                .shift(1)
            )   # claude code changed: causal boundary, not whole-series quantile
            upper_bound = (
                raw.expanding(min_periods=self.winsor_min_periods)
                .quantile(DIVERGENCE_WINSOR_UPPER)
                .shift(1)
            )   # claude code changed: causal boundary, not whole-series quantile

            # claude code changed: row-wise clip against each row's own
            # causal boundary; rows with no boundary yet (not enough prior
            # history) fall back to the raw, unclipped value.
            winsorised = raw.clip(lower=lower_bound, upper=upper_bound)
            alt_df[div_col] = winsorised.combine_first(raw)   # claude code changed: early rows keep raw value

        logger.info(f"    {symbol}: divergence winsorised (causal expanding window)")   # claude code changed: log message reflects the fix

        return alt_df


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4e: CALCULATE DIVERGENCE Z-SCORES
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_zscore(
        self,
        alt_df: pd.DataFrame,
        symbol: str,
    ) -> pd.DataFrame:
        """
        Normalise divergence relative to its own recent history.

        Why z-score the divergence?
            Raw divergence of 0.02 (2%) means different things in different markets:
            - In a calm ranging market: 2% divergence is EXTREME — very unusual
            - In a volatile crash: 2% divergence is NORMAL — happens every hour

            Z-scoring against the past 7 days of divergence history tells us:
            "How unusual is today's divergence compared to recent normal?"

            A z-score of +2.5 means: today's divergence is 2.5 standard deviations
            above the recent average. That is genuinely extreme. Much stronger signal.

        Formula:
            zscore_Nh = (divergence_Nh - rolling_mean(divergence_Nh, 168h))
                        / rolling_std(divergence_Nh, 168h)

        Features produced:
            divergence_zscore_1h
            divergence_zscore_3h
            divergence_zscore_6h
            divergence_zscore_12h
        """

        for window in self.divergence_windows:

            div_col    = f"divergence_{window}h"
            zscore_col = f"divergence_zscore_{window}h"

            if div_col not in alt_df.columns:
                continue

            # Rolling mean of divergence over the lookback window
            rolling_mean = alt_df[div_col].rolling(
                window=self.zscore_lookback,
                min_periods=self.zscore_min_periods,
            ).mean()

            # Rolling standard deviation of divergence
            rolling_std = alt_df[div_col].rolling(
                window=self.zscore_lookback,
                min_periods=self.zscore_min_periods,
            ).std()

            # Z-score: how many standard deviations from recent mean?
            # replace(0, np.nan) prevents division by zero in low-vol periods
            alt_df[zscore_col] = (
                (alt_df[div_col] - rolling_mean)
                / rolling_std.replace(0, np.nan)
            )

            # Winsorise z-scores too — z > 5 or z < -5 are almost certainly errors
            #
            # claude code changed: fixed the same look-ahead leakage bug as
            # _winsorise_divergence() above (Phase 1, Research Agent
            # architecture study) — this used to compute z_lower/z_upper
            # from `alt_df[zscore_col].dropna().quantile(...)`, the whole
            # column, so early rows' clip bounds were estimated using future
            # z-score values. Same causal expanding-window fix, same
            # combine_first fallback for rows without enough prior history.
            raw_z = alt_df[zscore_col]   # claude code changed: keep raw series for causal winsorization + fallback
            z_lower_bound = (
                raw_z.expanding(min_periods=self.winsor_min_periods)
                .quantile(0.005)
                .shift(1)
            )   # claude code changed: causal boundary, not whole-series quantile
            z_upper_bound = (
                raw_z.expanding(min_periods=self.winsor_min_periods)
                .quantile(0.995)
                .shift(1)
            )   # claude code changed: causal boundary, not whole-series quantile
            winsorised_z = raw_z.clip(lower=z_lower_bound, upper=z_upper_bound)
            alt_df[zscore_col] = winsorised_z.combine_first(raw_z)   # claude code changed: early rows keep raw value

        logger.info(
            f"    {symbol}: z-scores calculated "
            f"(lookback={self.zscore_lookback}h)"
        )

        return alt_df


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4f: DIRECTION AND MAGNITUDE
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_direction_magnitude(
        self,
        alt_df: pd.DataFrame,
        symbol: str,
    ) -> pd.DataFrame:
        """
        Decompose divergence into direction and magnitude.

        Why separate direction from magnitude?
            The validator tests IC — correlation between feature and label.
            A raw divergence feature of -0.03 and +0.03 are opposite directions
            but the same magnitude. Splitting them gives the validator
            separate features to work with, which can reveal more nuanced patterns.

        Direction feature:
            +1  = altcoin is lagging BTC (BTC up more, or BTC down less)
                  Hypothesis: altcoin will catch up → positive forward return
            -1  = altcoin is leading BTC (altcoin up more, or altcoin down less)
                  Hypothesis: altcoin will give back → negative forward return
             0  = no divergence

        Magnitude feature:
            Absolute value of 3h divergence z-score
            Tells us HOW EXTREME the divergence is regardless of direction
            High magnitude + extreme z-score = strongest signal

        We use the 3h window as the primary window for these features.
        The validator will confirm whether 3h is better than 1h or 6h.
        """

        # Primary divergence column for direction/magnitude calculation
        primary_div = "divergence_3h"
        primary_z   = "divergence_zscore_3h"

        if primary_div in alt_df.columns:

            # Direction: sign of divergence
            # np.sign returns +1, 0, or -1
            alt_df["divergence_direction"] = np.sign(alt_df[primary_div])

            # Magnitude: absolute divergence z-score
            # High magnitude = more extreme divergence = stronger expected reversion
            if primary_z in alt_df.columns:
                alt_df["divergence_magnitude"] = alt_df[primary_z].abs()
            else:
                alt_df["divergence_magnitude"] = alt_df[primary_div].abs()

        logger.info(f"    {symbol}: direction and magnitude calculated")

        return alt_df


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4g: COMPOSITE CATCH-UP SIGNAL
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_catchup_signal(
        self,
        alt_df: pd.DataFrame,
        symbol: str,
    ) -> pd.DataFrame:
        """
        Calculate the composite catch-up signal.

        This is the primary feature the validator will test for IC.
        It combines divergence direction, magnitude, and z-score into
        a single number that expresses the expected catch-up strength.

        Formula:
            catch_up_signal = divergence_zscore_3h

        Why just the z-score?
            The z-score already encodes:
                Direction: positive z = altcoin lagging (expected catch up)
                Magnitude: larger z = larger divergence = stronger expected reversion
                Historical context: z-score of 2 is unusual, z of 0.5 is normal

            Using the z-score directly is the cleanest single feature.
            The validator will compute IC between this and forward returns.

            If the hypothesis is correct:
                Positive catch_up_signal → positive altcoin forward return
                IC will be positive

            We also provide a windowed version (6h) as a secondary signal.
            The validator tests both and tells us which window is more predictive.

        Additional signals:
            catch_up_signal_1h   Shorter-window version (noisier but more reactive)
            catch_up_signal_6h   Longer-window version (smoother but slower)
        """

        # Primary catch-up signal: 3h divergence z-score
        # Positive: altcoin lagging BTC → expected to catch up
        # Negative: altcoin leading BTC → expected to give back
        if "divergence_zscore_3h" in alt_df.columns:
            alt_df["catch_up_signal"] = alt_df["divergence_zscore_3h"]

        # Short-window version: more reactive, more noise
        if "divergence_zscore_1h" in alt_df.columns:
            alt_df["catch_up_signal_1h"] = alt_df["divergence_zscore_1h"]

        # Long-window version: smoother, captures more sustained divergence
        if "divergence_zscore_6h" in alt_df.columns:
            alt_df["catch_up_signal_6h"] = alt_df["divergence_zscore_6h"]

        # Extended-window version: captures very sustained divergence
        if "divergence_zscore_12h" in alt_df.columns:
            alt_df["catch_up_signal_12h"] = alt_df["divergence_zscore_12h"]

        # Interaction: magnitude × direction (how extreme AND which way?)
        # This tests whether EXTREME divergence predicts stronger reversion
        if all(c in alt_df.columns for c in ["divergence_direction", "divergence_magnitude"]):
            alt_df["catch_up_signal_extreme"] = (
                alt_df["divergence_direction"] *
                alt_df["divergence_magnitude"]
            )

        logger.info(f"    {symbol}: catch-up signal calculated")

        return alt_df


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4h: FORWARD RETURN LABELS
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_forward_returns(
        self,
        alt_df: pd.DataFrame,
        symbol: str,
    ) -> pd.DataFrame:
        """
        Calculate what this altcoin actually did N hours later.

        These are the Y variables — the labels the validator correlates
        all divergence features against to compute IC.

        Formula:
            forward_return_Nh at time T = sum of returns from T+1 to T+N

        Implementation:
            We use the cumulative return shifted back by N periods.
            shift(-N) moves each value N rows earlier so the row at time T
            receives the value that was originally at time T+N.

        Look-ahead bias:
            These labels USE future data intentionally — that is their purpose.
            They are NEVER used as inputs to any feature calculation.
            They are clearly named "forward_return" to prevent confusion.
            The validator only reads them as the correlation target.

        The last N rows will be NaN for each N-period label.
        This is correct — no future exists beyond the last candle.
        The validator filters NaN labels automatically.
        """

        for label_name, periods in self.forward_horizons.items():

            # Compute N-period forward cumulative return
            # Method: rolling sum of future returns using negative shift
            # At time T: forward_return = return_T+1 + return_T+2 + ... + return_T+N
            # shift(-1) moves return at T+1 to row T
            # rolling(N).sum() then sums the next N returns starting from T+1

            # Correct implementation:
            # 1. shift(-1) to get return that happens AFTER current candle
            # 2. rolling(N).sum() would give us PAST N returns of the shifted series
            # Instead: use shift(-N) on the N-period cumulative return

            # Simplest correct approach: shift the cumulative return backward
            # cumret at T+N = what was the cumulative return ending at T+N?
            # We shift the rolling sum backward N periods so row T sees it
            n_period_cumret = alt_df["return_1h"].rolling(
                window=periods, min_periods=periods
            ).sum()

            # shift(-periods) pulls the value N periods ahead to the current row
            alt_df[label_name] = n_period_cumret.shift(-periods)

        logger.info(
            f"    {symbol}: forward return labels added "
            f"({list(self.forward_horizons.keys())})"
        )

        return alt_df


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 5: SUMMARY
    # ─────────────────────────────────────────────────────────────────────────

    def _log_summary(
        self,
        data: Dict[str, pd.DataFrame],
    ) -> None:
        """
        Log a clean summary of what was produced for each altcoin.

        Shows the researcher at a glance:
            - How many rows each altcoin has
            - How many divergence features were generated
            - Average catch-up signal value (should be near zero — no bias)
            - Standard deviation of catch-up signal (spread of opportunity)
        """

        logger.info("\nStep 5: Summary")
        logger.info("-" * 70)

        for symbol in self.altcoin_symbols:

            if symbol not in data:
                continue

            df = data[symbol]

            # Count divergence features
            div_cols  = [c for c in df.columns if c.startswith("divergence_")]
            sig_cols  = [c for c in df.columns if c.startswith("catch_up_")]
            fwd_cols  = [c for c in df.columns if c.startswith("forward_return_")]

            # Catch-up signal statistics
            if "catch_up_signal" in df.columns:
                sig_mean = df["catch_up_signal"].mean()
                sig_std  = df["catch_up_signal"].std()
                sig_nan  = df["catch_up_signal"].isna().sum()
                sig_info = (
                    f"signal mean={sig_mean:+.4f} "
                    f"std={sig_std:.4f} "
                    f"NaN={sig_nan}"
                )
            else:
                sig_info = "catch_up_signal not found"

            logger.info(
                f"  {symbol:12} | {len(df):>7,} rows | "
                f"{len(div_cols)} div_features | "
                f"{len(sig_cols)} signals | "
                f"{len(fwd_cols)} labels | "
                f"{sig_info}"
            )

        logger.info("-" * 70)
        logger.info("Features ready for feature_validator.")
        logger.info(
            "Recommended validator call for each altcoin:\n"
            "  validator.validate_all_features_institutional(\n"
            "      df=altcoin_df,\n"
            "      forward_return_col='forward_return_2h'  # Start with 2h\n"
            "  )\n"
            "Also test: forward_return_1h, forward_return_4h, forward_return_12h"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# QUICK IC REPORTER
# ═══════════════════════════════════════════════════════════════════════════════

class DivergenceICReporter:
    """
    Quick IC calculator for divergence features.

    Runs a simplified IC check without the full institutional validator.
    Useful for rapid hypothesis screening before committing to full validation.

    This is NOT a replacement for the full feature_validator.
    It is a fast pre-screening tool.
    """

    @staticmethod
    def report(
        data:            Dict[str, pd.DataFrame],
        altcoin_symbols: List[str] = None,
        forward_col:     str       = "forward_return_2h",
    ) -> pd.DataFrame:
        """
        Calculate Spearman IC for all divergence features across all altcoins.

        Parameters
        ----------
        data : Dict[str, pd.DataFrame]
            Enriched altcoin DataFrames from ContagionEngine.calculate_all().

        altcoin_symbols : List[str]
            Which altcoins to include. Default: all ALTCOIN_SYMBOLS.

        forward_col : str
            Which forward return label to correlate against. Default: 2h.

        Returns
        -------
        pd.DataFrame
            Summary table with IC, p-value, and sample size for each
            feature × symbol combination. Sorted by absolute IC descending.
        """

        symbols = altcoin_symbols or ALTCOIN_SYMBOLS
        results = []

        for symbol in symbols:

            if symbol not in data:
                continue

            df = data[symbol]

            # Need forward return column to compute IC
            if forward_col not in df.columns:
                logger.warning(
                    f"  {symbol}: {forward_col} not found, skipping IC report"
                )
                continue

            fwd = df[forward_col]

            # Test IC for each divergence and catch-up feature
            feature_cols = (
                [c for c in df.columns if c.startswith("divergence_")] +
                [c for c in df.columns if c.startswith("catch_up_")]
            )

            for feature in feature_cols:

                feat = df[feature]

                # Remove NaNs from both series simultaneously
                valid = feat.notna() & fwd.notna()
                n     = valid.sum()

                if n < 100:   # Need minimum observations for meaningful IC
                    continue

                # Spearman rank correlation — same method as feature_validator
                ic, pvalue = stats.spearmanr(
                    feat[valid].values,
                    fwd[valid].values,
                )

                results.append({
                    "symbol":  symbol,
                    "feature": feature,
                    "ic":      round(float(ic), 6),
                    "pvalue":  round(float(pvalue), 6),
                    "n":       int(n),
                    "significant": pvalue < 0.05,
                })

        if not results:
            logger.warning("No IC results computed. Check data and forward_col.")
            return pd.DataFrame()

        # Build results DataFrame and sort by absolute IC
        results_df = pd.DataFrame(results)
        results_df["abs_ic"] = results_df["ic"].abs()
        results_df = results_df.sort_values("abs_ic", ascending=False)
        results_df = results_df.drop(columns=["abs_ic"])

        return results_df


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_contagion_research(
    data_dir:   str = "data",
    output_dir: str = "research_data",
    interval:   str = "1h",
) -> Dict[str, pd.DataFrame]:
    """
    Standalone runner. Loads CSVs, runs engine, saves enriched output,
    and prints a quick IC report for rapid hypothesis screening.

    Run from project root:
        python -m bot.research.contagion_engine

    Parameters
    ----------
    data_dir   : Folder containing symbol CSVs. Default "data".
    output_dir : Folder to save enriched CSVs. Default "research_data".
    interval   : Candle interval in filenames. Default "1h".

    Returns
    -------
    Dict[str, pd.DataFrame] — enriched DataFrames ready for full validation.
    """

    from pathlib import Path

    logger.info("=" * 70)
    logger.info("CONTAGION ENGINE — HYPOTHESIS 7 — STANDALONE RUN")
    logger.info("=" * 70)

    # ── Load all 7 symbol CSVs ────────────────────────────────────────────────
    all_symbols = [BTC_SYMBOL] + ALTCOIN_SYMBOLS
    data: Dict[str, pd.DataFrame] = {}

    for symbol in all_symbols:

        # claude code changed: was f"{symbol}_{interval}.csv" directly on
        # the slash-format symbol — see _symbol_to_csv_stem()'s docstring
        # for the real bug this fixes.
        csv_path = Path(data_dir) / f"{_symbol_to_csv_stem(symbol)}_{interval}.csv"

        if not csv_path.exists():
            logger.warning(f"  {symbol}: not found at {csv_path}")
            continue

        try:
            df = pd.read_csv(csv_path)

            # Parse timestamp and set as DatetimeIndex
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                df.set_index("timestamp", inplace=True)

            # Ensure numeric OHLCV columns
            for col in ["open", "high", "low", "close", "volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            df.dropna(subset=["close"], inplace=True)
            df.sort_index(inplace=True)

            data[symbol] = df
            logger.info(f"  Loaded {symbol}: {len(df):,} candles")

        except Exception as e:
            logger.error(f"  Failed to load {symbol}: {e}")

    # ── Run the engine ────────────────────────────────────────────────────────
    engine       = ContagionEngine()
    enriched     = engine.calculate_all(data)

    # ── Quick IC report before saving ─────────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("QUICK IC REPORT — forward_return_2h")
    logger.info("=" * 70)

    reporter   = DivergenceICReporter()
    ic_results = reporter.report(enriched, forward_col="forward_return_2h")

    if not ic_results.empty:
        # Print top 20 results by absolute IC
        top = ic_results.head(20)
        logger.info(
            f"\n{'Symbol':12} {'Feature':35} {'IC':>8} {'p-value':>10} "
            f"{'N':>8} {'Sig':>5}"
        )
        logger.info("-" * 80)
        for _, row in top.iterrows():
            sig_marker = "✓" if row["significant"] else " "
            logger.info(
                f"  {row['symbol']:12} {row['feature']:35} "
                f"{row['ic']:>+8.4f} {row['pvalue']:>10.6f} "
                f"{row['n']:>8,} {sig_marker:>5}"
            )

        # Highlight any features with IC > 0.03
        strong = ic_results[ic_results["ic"].abs() > 0.03]
        if not strong.empty:
            logger.info(
                f"\n  ⭐ {len(strong)} features with |IC| > 0.03 — "
                f"worth running through full validator!"
            )
        else:
            logger.info(
                "\n  No features exceeded IC > 0.03 threshold on this quick screen."
            )

    # ── Save enriched CSVs ────────────────────────────────────────────────────
    Path(output_dir).mkdir(exist_ok=True)

    for symbol in ALTCOIN_SYMBOLS:
        if symbol not in enriched:
            continue
        # claude code changed: same fix as the input-loading path above —
        # was f"{symbol}_contagion.csv" directly on the slash-format symbol.
        out = Path(output_dir) / f"{_symbol_to_csv_stem(symbol)}_contagion.csv"
        enriched[symbol].reset_index().to_csv(out, index=False)
        logger.info(f"  Saved: {out}")

    # Save IC report
    if not ic_results.empty:
        ic_path = Path(output_dir) / "hypothesis_7_ic_report.csv"
        ic_results.to_csv(ic_path, index=False)
        logger.info(f"  IC report saved: {ic_path}")

    logger.info(
        "\nNext step: run full feature_validator on each altcoin CSV "
        "with forward_return_col='forward_return_2h'"
    )

    return enriched


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_contagion_research(
        data_dir="data",
        output_dir="research_data",
        interval="1h",
    )