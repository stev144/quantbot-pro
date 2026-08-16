# ═══════════════════════════════════════════════════════════════════════════════
# bot/research/feature_evolution_report.py
#
# FEATURE EVOLUTION REPORT
# Module 3 of 3 — Temporal Research Suite
#
# Version  : 1.0 (Research Grade)
# Author   : QuantiBot Pro Research Pipeline
#
# ─────────────────────────────────────────────────────────────────────────────
# PURPOSE
# ─────────────────────────────────────────────────────────────────────────────
#
# This is the master comparator that answers the most important questions
# in your entire research pipeline:
#
#   "Which features are alive in TODAY's market?"
#   "Which features worked years ago but are dead now?"
#   "Which features are GAINING edge as crypto becomes institutional?"
#   "Which features only work in bull markets?"
#   "Which features only work in bear markets?"
#   "Which features survive crashes?"
#   "What should I actually trade with in 2025-2026?"
#
# It does this by reading the outputs from:
#   Module 1 (FeatureStabilityAnalyzer) — yearly + rolling stability data
#   Module 2 (FeatureDecayAnalyzer)     — decay trends + regime breakdowns
#
# And producing a single master report that compares ALL features
# across ALL symbols with a clear trading recommendation for each.
#
# ─────────────────────────────────────────────────────────────────────────────
# THE CORE INSIGHT THIS MODULE DELIVERS
# ─────────────────────────────────────────────────────────────────────────────
#
# Without this module, you would have to manually read dozens of CSV files
# and try to compare features across years and symbols in your head.
#
# With this module, you get one clean answer:
#
#   LIVE FEATURES (deploy these):
#     catch_up_signal     — IC gaining 2023→2026, EMERGING in institutional era
#     cs_reversal_signal  — STABLE across all years, survives crashes
#     divergence_zscore_3h — GAINING since 2024, regime-adaptive
#
#   DEAD FEATURES (remove these):
#     rsi                 — DECAYING, dead since 2024
#     macd                — DECAYING, broke down after FTX
#     ema_12              — DEAD, no signal in 2024-2026
#
#   REGIME-SPECIFIC (only trade in correct conditions):
#     atr_ratio           — works in volatile regimes only
#     volume_ratio        — works in bull markets only
#     efficiency          — works in ranging markets only
#
# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT FILES
# ─────────────────────────────────────────────────────────────────────────────
#
#   research_data/temporal/evolution_master_report.txt
#       → Human-readable master report with all findings
#
#   research_data/temporal/evolution_live_features.csv
#       → Features recommended for live trading (IC > 0.03 in 2024-2026)
#
#   research_data/temporal/evolution_dead_features.csv
#       → Features to remove from pipeline (IC < 0.01 in 2024-2026)
#
#   research_data/temporal/evolution_regime_specific.csv
#       → Features that work only in specific market conditions
#
#   research_data/temporal/evolution_full_matrix.csv
#       → Complete IC matrix: every feature × every year × every symbol
#
# ═══════════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations          # Enables modern type hints in Python 3.8+

import logging                              # Structured logging throughout
import warnings                             # Suppress non-critical pandas warnings
from pathlib import Path                    # Cross-platform file path handling
from typing import Dict, List, Optional, Tuple   # Type annotations for clarity

import numpy as np                          # Numerical operations and statistics
import pandas as pd                         # DataFrame operations for all data
from scipy import stats                     # Spearman IC calculation

# Import the two upstream modules — this report reads their outputs
# We import defensively so the report can run even if one module failed
try:
    from bot.research.feature_stability_analyzer import (
        FeatureStabilityAnalyzer,           # Provides year + rolling stability data
        SYMBOLS,                            # Default symbol list
        YEAR_WINDOWS,                       # Calendar year definitions
        YEAR_CONTEXT,                       # Human-readable year descriptions
    )
    STABILITY_AVAILABLE = True              # Flag: upstream module is importable
except ImportError:
    STABILITY_AVAILABLE = False             # Fall back to standalone mode
    SYMBOLS = [                             # Define locally if import fails
        "BTC_USDT", "ETH_USDT", "BNB_USDT", "XRP_USDT",
        "ADA_USDT", "DOGE_USDT", "SOL_USDT",
    ]
    YEAR_WINDOWS = {                        # Replicate year windows locally
        "2020": ("2020-01-01", "2020-12-31"),
        "2021": ("2021-01-01", "2021-12-31"),
        "2022": ("2022-01-01", "2022-12-31"),
        "2023": ("2023-01-01", "2023-12-31"),
        "2024": ("2024-01-01", "2024-12-31"),
        "2025": ("2025-01-01", "2025-12-31"),
        "2026": ("2026-01-01", "2026-12-31"),
    }
    YEAR_CONTEXT = {                        # Replicate context descriptions locally
        "2020": "COVID crash + DeFi summer",
        "2021": "Bull market peak",
        "2022": "Luna + FTX — bear market",
        "2023": "Recovery",
        "2024": "BTC ETF — institutional entry",
        "2025": "Full institutional era",
        "2026": "Current year",
    }

try:
    from bot.research.feature_decay_analyzer import (
        FeatureDecayAnalyzer,               # Provides decay trends + regime data
        REGIME_WINDOWS,                     # Specific regime window definitions
    )
    DECAY_AVAILABLE = True                  # Flag: upstream module is importable
except ImportError:
    DECAY_AVAILABLE = False                 # Fall back to standalone mode
    REGIME_WINDOWS = {}                     # Empty dict if import fails

warnings.filterwarnings('ignore')           # Suppress non-critical warnings


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)        # Module-level logger

if not logger.handlers:                     # Prevent duplicate handlers on re-import
    logging.basicConfig(
        level=logging.INFO,                 # Show INFO and above
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",        # Human-readable timestamp
    )


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# IC threshold definitions — these determine the classification of each feature
IC_STRONG:   float = 0.05    # IC above this = strong predictive power
IC_MODERATE: float = 0.03    # IC above this = moderate predictive power
IC_WEAK:     float = 0.01    # IC above this = weak but present signal
IC_DEAD:     float = 0.01    # IC below this = no signal, feature is useless

# Years considered "recent" for live trading recommendations
# A feature must show edge in these years to be deployed in live markets
RECENT_YEARS: List[str] = ["2024", "2025", "2026"]

# Years considered "early" — the retail era before institutional adoption
# Features that only worked here are retail-era artifacts, now dead
EARLY_YEARS: List[str] = ["2020", "2021"]

# The institutional era starts here — BTC ETF approved January 2024
# Features must show edge from this point to be relevant to current market
INSTITUTIONAL_ERA_START: str = "2024"

# Minimum fraction of symbols a feature must show IC on to be considered robust
# Example: if a feature shows IC on 5 of 7 symbols = 71% → robust
# If only 2 of 7 symbols = 29% → symbol-specific, not robust
MIN_SYMBOL_COVERAGE: float = 0.50           # Feature must work on at least 50% of symbols

# Forward return column to validate against
DEFAULT_FORWARD_COL: str = "forward_return_4h"   # 4h ahead prediction

# Minimum observations for any single year to be included
MIN_OBS_PER_YEAR: int = 500                 # Need at least 500 candles per year

# Annualisation factor for Sharpe on 1h candles
# 1h candles: 24 hours × 365 days = 8,760 candles per year
ANNUALISATION_FACTOR: float = np.sqrt(8_760)

# Columns to exclude from feature testing — these are labels, metadata, or raw OHLCV
EXCLUDE_COLS = {
    'symbol', 'timeframe', 'timestamp', 'market_regime',
    'forward_return_1h', 'forward_return_4h', 'forward_return_24h',
    'forward_return_2h', 'forward_return_12h',
    'win_1h', 'win_4h', 'win_24h',
    'close', 'open', 'high', 'low', 'volume',
    'realized_vol', 'adx', 'volatility_state',
    'index', 'Unnamed: 0',
}

# Report separator line for readability
SEP = "=" * 70                              # Long separator
SEP_SHORT = "-" * 60                        # Short separator


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE EVOLUTION REPORT CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class FeatureEvolutionReport:
    """
    Master comparator producing the definitive feature evolution report.

    This is the final layer of the temporal research suite.
    It synthesises outputs from Modules 1 and 2 into actionable intelligence:

        Which features to TRADE NOW in live markets (2024-2026 signal)
        Which features to REMOVE from the pipeline (dead in current market)
        Which features are REGIME-SPECIFIC (deploy only in correct conditions)
        Which features are EMERGING (gaining edge as market becomes institutional)

    Two modes of operation:

    Mode 1 — SYNTHESIS (recommended):
        Reads pre-computed CSV files from Modules 1 and 2.
        Fast — results are already computed, just needs to compare them.
        Use this when stability and decay analysis have already been run.

    Mode 2 — STANDALONE:
        Loads raw observation CSVs and computes everything from scratch.
        Slower — runs full IC computation for each year and feature.
        Use this when running the report for the first time.
    """

    def __init__(
        self,
        forward_col:     str   = DEFAULT_FORWARD_COL,    # What we predict
        output_dir:      str   = "research_data/temporal",  # Where to save
        min_obs:         int   = MIN_OBS_PER_YEAR,        # Min candles per window
        alpha:           float = 0.05,                    # Significance threshold
        min_coverage:    float = MIN_SYMBOL_COVERAGE,     # Min symbol coverage
    ) -> None:
        """
        Initialise the report generator.

        All parameters have research-grade defaults.
        Override for sensitivity testing — e.g. stricter min_coverage.
        """

        self.forward_col  = forward_col      # Forward return label column
        self.output_dir   = Path(output_dir) # Output directory as Path object
        self.min_obs      = min_obs          # Minimum candles per year window
        self.alpha        = alpha            # Statistical significance cutoff
        self.min_coverage = min_coverage     # Minimum symbol coverage fraction

        # Create output directory if it does not exist
        self.output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("FeatureEvolutionReport initialised")
        logger.info(f"  Forward return  : {forward_col}")
        logger.info(f"  Output dir      : {output_dir}")
        logger.info(f"  Alpha           : {alpha}")
        logger.info(f"  Min coverage    : {min_coverage:.0%} of symbols")
        logger.info(f"  Recent years    : {RECENT_YEARS}")
        logger.info(f"  Institutional   : from {INSTITUTIONAL_ERA_START}")


    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC: generate() — MAIN ENTRY POINT
    # ─────────────────────────────────────────────────────────────────────────

    def generate(
        self,
        observations_dir: str        = "research_data",   # Where observation CSVs live
        temporal_dir:     str        = "research_data/temporal",  # Pre-computed data
        symbols:          List[str]  = None,               # Which symbols to include
        use_precomputed:  bool       = True,               # Use Module 1+2 outputs?
    ) -> Dict[str, pd.DataFrame]:
        """
        Generate the full feature evolution report.

        Parameters
        ----------
        observations_dir : str
            Folder containing {SYMBOL}_observations.csv files.
            Used in standalone mode when use_precomputed=False.

        temporal_dir : str
            Folder containing pre-computed stability and decay CSVs.
            Used when use_precomputed=True (faster).

        symbols : List[str]
            Symbols to include. Default: all 7.

        use_precomputed : bool
            If True: read Module 1+2 CSV outputs (fast).
            If False: compute IC from scratch on raw observation files (slow).

        Returns
        -------
        Dict[str, pd.DataFrame]
            "live"          : features recommended for live trading
            "dead"          : features to remove from pipeline
            "regime"        : regime-specific features with conditions
            "full_matrix"   : complete IC matrix for all features × years
            "cross_symbol"  : features that work across multiple symbols
        """

        symbols = symbols or SYMBOLS        # Default to all 7 symbols

        logger.info(SEP)
        logger.info("FEATURE EVOLUTION REPORT — GENERATING")
        logger.info(SEP)
        logger.info(f"  Symbols   : {symbols}")
        logger.info(f"  Mode      : {'precomputed' if use_precomputed else 'standalone'}")

        # ── Step 1: Load or compute IC data ───────────────────────────────────
        if use_precomputed:
            # Fast path: read CSV files already produced by Modules 1 and 2
            ic_matrix = self._load_precomputed(temporal_dir, symbols)
        else:
            # Slow path: compute IC from scratch on raw observation files
            ic_matrix = self._compute_from_scratch(
                observations_dir, symbols
            )

        # If no data was loaded or computed — abort early
        if ic_matrix.empty:
            logger.error(
                "No IC data available. Run feature_stability_analyzer first, "
                "or set use_precomputed=False to compute from scratch."
            )
            return {}

        logger.info(
            f"  IC matrix loaded: "
            f"{ic_matrix['feature'].nunique()} features × "
            f"{ic_matrix['year'].nunique()} years × "
            f"{ic_matrix['symbol'].nunique()} symbols"
        )

        # ── Step 2: Build the full IC pivot matrix ────────────────────────────
        # Creates a wide DataFrame: rows=features, columns=year × symbol
        # This is the core data structure all subsequent analysis builds from
        full_matrix = self._build_full_matrix(ic_matrix, symbols)

        # ── Step 3: Classify every feature's evolution trend ─────────────────
        # GAINING / STABLE / DECAYING / DEAD / EMERGING / VOLATILE
        classified = self._classify_all_features(ic_matrix, symbols)

        # ── Step 4: Separate into live, dead, and regime-specific ─────────────
        live_features   = self._extract_live_features(classified)
        dead_features   = self._extract_dead_features(classified)
        regime_features = self._extract_regime_features(classified, ic_matrix)

        # ── Step 5: Cross-symbol robustness check ─────────────────────────────
        # Features that work on multiple symbols are more robust than
        # features that only show IC on one symbol
        cross_symbol = self._check_cross_symbol_robustness(classified, symbols)

        # ── Step 6: Build and save the master report ──────────────────────────
        report_text = self._build_master_report(
            live_features, dead_features, regime_features,
            cross_symbol, classified, symbols
        )

        # Save all outputs to disk
        self._save_all_outputs(
            report_text, live_features, dead_features,
            regime_features, full_matrix, cross_symbol
        )

        # Print report to terminal
        print(report_text)

        logger.info(SEP)
        logger.info("FEATURE EVOLUTION REPORT — COMPLETE")
        logger.info(SEP)

        return {
            "live":         live_features,
            "dead":         dead_features,
            "regime":       regime_features,
            "full_matrix":  full_matrix,
            "cross_symbol": cross_symbol,
        }


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1A: LOAD PRE-COMPUTED DATA
    # ─────────────────────────────────────────────────────────────────────────

    def _load_precomputed(
        self,
        temporal_dir: str,
        symbols:      List[str],
    ) -> pd.DataFrame:
        """
        Load IC data from pre-computed CSV files (Module 1 outputs).

        Reads the {SYMBOL}_stability_yearly.csv files that
        FeatureStabilityAnalyzer already produced. This is much faster
        than recomputing IC from scratch on raw observation data.

        Returns a long-format DataFrame:
            symbol | feature | year | ic | sharpe | win_rate | verdict
        """

        temporal_path = Path(temporal_dir)   # Convert string to Path object
        all_data:     List[pd.DataFrame] = []  # Collect data from each symbol

        logger.info("Loading pre-computed stability data...")

        for symbol in symbols:

            # Look for the yearly stability CSV from Module 1
            yearly_path = temporal_path / f"{symbol}_stability_yearly.csv"

            if not yearly_path.exists():
                logger.warning(
                    f"  {symbol}: stability CSV not found at {yearly_path}. "
                    f"Run feature_stability_analyzer first."
                )
                continue

            try:
                df = pd.read_csv(yearly_path)   # Load the pre-computed data
                all_data.append(df)              # Add to collection
                logger.info(
                    f"  {symbol}: loaded "
                    f"{df['feature'].nunique()} features × "
                    f"{df['year'].nunique()} years"
                )
            except Exception as e:
                logger.error(f"  {symbol}: failed to load — {e}")
                continue

        # If no pre-computed data found — return empty DataFrame
        if not all_data:
            logger.warning("No pre-computed data found.")
            return pd.DataFrame()

        # Combine all symbols into one long-format DataFrame
        combined = pd.concat(all_data, ignore_index=True)

        # Rename columns to standard names if needed
        # Module 1 uses 'ic' and 'window' — we need 'year' for consistency
        if "window" in combined.columns and "year" not in combined.columns:
            combined = combined.rename(columns={"window": "year"})

        return combined


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1B: COMPUTE FROM SCRATCH
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_from_scratch(
        self,
        observations_dir: str,
        symbols:          List[str],
    ) -> pd.DataFrame:
        """
        Compute IC from scratch on raw observation CSV files.

        Used when Module 1 has not been run yet.
        Slower than loading pre-computed data but fully self-contained.

        For each symbol and each year:
            1. Load the observations CSV
            2. Slice to the year window
            3. Compute Spearman IC for every feature vs forward return
            4. Store result with symbol, feature, year labels
        """

        logger.info("Computing IC from scratch on raw observation files...")

        obs_path_root = Path(observations_dir)   # Root folder for observation CSVs
        all_rows:     List[Dict] = []             # Collect one row per feature/year

        for symbol in symbols:

            # Path to this symbol's full observations CSV
            obs_path = obs_path_root / f"{symbol}_observations.csv"

            if not obs_path.exists():
                logger.warning(f"  {symbol}: observations not found at {obs_path}")
                continue

            try:
                # Load the full observations DataFrame
                df = pd.read_csv(obs_path)

                # Parse timestamp to DatetimeIndex for year slicing
                df = self._parse_timestamp(df, symbol)
                if df is None:
                    continue

                # Check forward return column exists
                if self.forward_col not in df.columns:
                    logger.warning(
                        f"  {symbol}: '{self.forward_col}' not found. "
                        f"Available: {[c for c in df.columns if 'forward' in c]}"
                    )
                    continue

                # Get all testable feature columns
                features = self._get_features(df)
                if not features:
                    logger.warning(f"  {symbol}: no testable features")
                    continue

                logger.info(
                    f"  {symbol}: {len(df):,} candles, "
                    f"{len(features)} features"
                )

                # Process each calendar year separately
                for year, (start_str, end_str) in YEAR_WINDOWS.items():

                    # Slice data to this calendar year
                    start  = pd.Timestamp(start_str, tz="UTC")
                    end    = pd.Timestamp(end_str,   tz="UTC")
                    yr_df  = df.loc[(df.index >= start) & (df.index <= end)]
                    n_obs  = len(yr_df)

                    # Skip years with insufficient data
                    if n_obs < self.min_obs:
                        continue

                    # Get forward return for this year
                    fwd = yr_df[self.forward_col]

                    # Compute IC for each feature in this year
                    for feature in features:

                        feat  = yr_df[feature]

                        # Remove NaN from both arrays simultaneously
                        # Critical: NaN in returns produces spurious IC values
                        valid = (
                            feat.notna() & np.isfinite(feat) &
                            fwd.notna()  & np.isfinite(fwd)
                        )

                        n_valid = valid.sum()
                        if n_valid < 100:    # Need minimum for reliable IC
                            continue

                        fc = feat[valid].values    # Clean feature values
                        fw = fwd[valid].values     # Clean forward return values

                        # Spearman IC: rank correlation between feature and return
                        # Spearman is robust to outliers — essential for crypto data
                        ic, pvalue = stats.spearmanr(fc, fw)

                        # Sharpe: risk-adjusted return trading this feature alone
                        # We "trade" in the direction of the IC sign
                        ic_sign = np.sign(ic) if not np.isnan(ic) else 1.0
                        strat   = fw * ic_sign * np.sign(fc)  # Simulated strategy returns
                        sharpe  = (
                            (np.mean(strat) / (np.std(strat) + 1e-10))
                            * ANNUALISATION_FACTOR
                        ) if np.std(strat) > 0 else 0.0

                        # Win rate: fraction of candles where direction was correct
                        correct = (
                            ((fc > 0) & (fw > 0)) |   # Both positive
                            ((fc < 0) & (fw < 0))     # Both negative
                        )
                        win_rate = correct.mean()

                        # Store this feature/year result
                        all_rows.append({
                            "symbol":    symbol,                 # Which coin
                            "feature":   feature,               # Which feature
                            "year":      year,                  # Which year
                            "context":   YEAR_CONTEXT.get(year, ""),  # Year description
                            "n_obs":     int(n_valid),          # How many valid candles
                            "ic":        round(float(ic), 6) if not np.isnan(ic) else np.nan,
                            "sharpe":    round(float(sharpe), 4),     # Sharpe contribution
                            "win_rate":  round(float(win_rate), 4),   # Direction accuracy
                            "pvalue":    round(float(pvalue), 6),     # Statistical significance
                            "significant": pvalue < self.alpha,       # Is IC statistically real?
                        })

            except Exception as e:
                logger.error(f"  {symbol}: failed — {e}")
                continue

        if not all_rows:
            return pd.DataFrame()

        return pd.DataFrame(all_rows)   # Return long-format IC table


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2: BUILD FULL IC MATRIX
    # ─────────────────────────────────────────────────────────────────────────

    def _build_full_matrix(
        self,
        ic_matrix: pd.DataFrame,
        symbols:   List[str],
    ) -> pd.DataFrame:
        """
        Build a wide pivot table showing IC for every feature × year × symbol.

        This is the master data structure behind the entire report.
        Each row is one feature. Each column group is one year, with
        one sub-column per symbol.

        Example output columns:
            feature | ic_2020_BTC | ic_2020_ETH | ic_2021_BTC | ... | ic_2026_SOL

        This makes it trivially easy to see at a glance:
            - Does this feature work across all symbols or just one?
            - Is it consistent year-over-year?
            - In which year did it peak or die?
        """

        if ic_matrix.empty:
            return pd.DataFrame()

        # Get all unique features across all symbols and years
        all_features = sorted(ic_matrix["feature"].unique())
        rows = []

        for feature in all_features:

            row = {"feature": feature}    # Start each row with the feature name

            # Add IC for each year × symbol combination
            for year in YEAR_WINDOWS.keys():

                for symbol in symbols:

                    # Filter to this specific feature / year / symbol
                    mask = (
                        (ic_matrix["feature"] == feature) &
                        (ic_matrix["year"]    == year)    &
                        (ic_matrix["symbol"]  == symbol)
                    )

                    matches = ic_matrix[mask]

                    if matches.empty:
                        # No data for this combination — NaN
                        row[f"ic_{year}_{symbol[:3]}"] = np.nan
                    else:
                        # Store IC value (take first match if duplicates exist)
                        ic_val = matches["ic"].iloc[0]
                        row[f"ic_{year}_{symbol[:3]}"] = (
                            round(float(ic_val), 4)
                            if not pd.isna(ic_val) else np.nan
                        )

            rows.append(row)

        return pd.DataFrame(rows)    # Return the wide pivot matrix


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3: CLASSIFY ALL FEATURES
    # ─────────────────────────────────────────────────────────────────────────

    def _classify_all_features(
        self,
        ic_matrix: pd.DataFrame,
        symbols:   List[str],
    ) -> pd.DataFrame:
        """
        Classify every feature's evolution trend across all symbols.

        For each feature, we compute:
            mean_ic_early    — average |IC| in 2020-2021 (retail era)
            mean_ic_recent   — average |IC| in 2024-2026 (institutional era)
            trend            — GAINING / STABLE / DECAYING / DEAD / EMERGING
            live_rec         — TRADE NOW / WATCH / RESEARCH / AVOID
            n_symbols        — how many symbols show this feature
            n_sig_recent     — how many symbols show significant IC in recent years

        The classification logic:
            DEAD     → recent |IC| < 0.01 (no signal whatsoever)
            EMERGING → early |IC| < 0.02 AND recent |IC| > 0.03 (new signal)
            GAINING  → recent |IC| > early |IC| × 1.3 (strengthening)
            DECAYING → early |IC| > recent |IC| × 1.3 (weakening)
            STABLE   → IC consistent across all years (± 0.01)
            VOLATILE → IC jumps around with no clear trend
        """

        all_features = sorted(ic_matrix["feature"].unique())  # All unique features
        rows = []

        for feature in all_features:

            # Filter to this feature only
            feat_data = ic_matrix[ic_matrix["feature"] == feature]

            # ── Compute mean IC by era across all symbols ──────────────────────
            # Early era: retail-dominated 2020-2021
            early_mask  = feat_data["year"].isin(EARLY_YEARS)
            early_ics   = feat_data.loc[early_mask, "ic"].dropna()
            mean_early  = float(early_ics.abs().mean()) if len(early_ics) > 0 else 0.0

            # Recent era: institutional 2024-2026
            recent_mask = feat_data["year"].isin(RECENT_YEARS)
            recent_ics  = feat_data.loc[recent_mask, "ic"].dropna()
            mean_recent = float(recent_ics.abs().mean()) if len(recent_ics) > 0 else 0.0

            # All years combined
            all_ics     = feat_data["ic"].dropna()
            mean_all    = float(all_ics.abs().mean()) if len(all_ics) > 0 else 0.0
            std_all     = float(all_ics.std())         if len(all_ics) > 1 else 0.0

            # ── Count symbol coverage ──────────────────────────────────────────
            # How many symbols show IC for this feature?
            n_symbols = feat_data["symbol"].nunique()

            # How many symbols show statistically significant IC in recent years?
            recent_sig_mask = recent_mask & feat_data["significant"].fillna(False)
            n_sig_recent    = feat_data.loc[recent_sig_mask, "symbol"].nunique()

            # ── Year-by-year IC for display ────────────────────────────────────
            year_ics = {}
            for year in YEAR_WINDOWS.keys():
                yr_mask  = feat_data["year"] == year   # Filter to this year
                yr_ics   = feat_data.loc[yr_mask, "ic"].dropna()
                year_ics[year] = float(yr_ics.mean()) if len(yr_ics) > 0 else np.nan

            # ── Classify trend ─────────────────────────────────────────────────
            trend = self._classify_trend(
                mean_recent, mean_early, std_all, all_ics.tolist()
            )

            # ── Live trading recommendation ────────────────────────────────────
            # Based ONLY on recent years (2024-2026) — the current market
            live_rec = self._make_live_recommendation(
                mean_recent, n_sig_recent, len(symbols)
            )

            # ── Bull/bear regime check ─────────────────────────────────────────
            # Does the feature work better in bull or bear conditions?
            bull_ics = feat_data[
                feat_data["year"].isin(["2020", "2021"])
            ]["ic"].dropna()
            bear_ics = feat_data[
                feat_data["year"].isin(["2022"])
            ]["ic"].dropna()

            bull_mean = float(bull_ics.abs().mean()) if len(bull_ics) > 0 else 0.0
            bear_mean = float(bear_ics.abs().mean()) if len(bear_ics) > 0 else 0.0

            # Classify regime preference
            if bull_mean > bear_mean * 1.5 and bull_mean > IC_MODERATE:
                regime_pref = "BULL_ONLY"       # Only works in bull markets
            elif bear_mean > bull_mean * 1.5 and bear_mean > IC_MODERATE:
                regime_pref = "BEAR_ONLY"       # Only works in bear markets
            elif bull_mean > IC_MODERATE and bear_mean > IC_MODERATE:
                regime_pref = "ALL_WEATHER"     # Works in both conditions
            else:
                regime_pref = "NONE"            # Works in neither

            # ── Build result row ───────────────────────────────────────────────
            row = {
                "feature":        feature,            # Feature name
                "trend":          trend,               # GAINING/STABLE/DECAYING/DEAD
                "live_rec":       live_rec,            # TRADE NOW/WATCH/RESEARCH/AVOID
                "regime_pref":    regime_pref,         # BULL_ONLY/BEAR_ONLY/ALL_WEATHER
                "mean_ic_early":  round(mean_early, 6),    # Retail era IC
                "mean_ic_recent": round(mean_recent, 6),   # Institutional era IC
                "mean_ic_all":    round(mean_all, 6),      # All-time IC
                "std_ic":         round(std_all, 6),       # IC consistency
                "n_symbols":      n_symbols,               # Symbol coverage
                "n_sig_recent":   n_sig_recent,            # Recent significant symbols
                "pct_coverage":   round(n_symbols / max(len(symbols), 1), 2),
            }

            # Add IC for each year as separate columns
            for year, ic_val in year_ics.items():
                row[f"ic_{year}"] = round(ic_val, 6) if not pd.isna(ic_val) else np.nan

            rows.append(row)

        # Sort by recent IC — most relevant features at top
        classified_df = pd.DataFrame(rows)
        classified_df = classified_df.sort_values(
            "mean_ic_recent", ascending=False
        )

        return classified_df


    def _classify_trend(
        self,
        mean_recent: float,
        mean_early:  float,
        std_all:     float,
        all_ics:     List[float],
    ) -> str:
        """
        Classify a feature's IC trend into one of six categories.

        The categories are designed to directly answer the trading question:
        "Should I use this feature in live trading today?"
        """

        # DEAD: essentially no signal in recent years
        # These features are generating noise, not information
        if mean_recent < IC_DEAD:
            return "DEAD"

        # EMERGING: was silent before 2023, gaining signal since
        # This is the most exciting category — new signals often
        # correspond to new market structure (e.g. institutional era)
        if mean_early < 0.015 and mean_recent > IC_MODERATE:
            return "EMERGING"

        # GAINING: recent IC meaningfully higher than early IC
        # The signal is strengthening over time — deploy with confidence
        if mean_recent > mean_early * 1.3 and mean_recent > IC_MODERATE:
            return "GAINING"

        # DECAYING: early IC meaningfully higher than recent IC
        # The signal worked in old market structure but is dying
        # Dangerous to deploy in live trading
        if mean_early > IC_MODERATE and mean_recent < mean_early * 0.7:
            return "DECAYING"

        # STABLE: IC consistent across years (low variance)
        # These are the most reliable features — regime-agnostic signals
        if std_all < 0.02 and mean_recent > IC_WEAK:
            return "STABLE"

        # VOLATILE: IC jumps around with no discernible trend
        # These may be regime-specific — investigate regime breakdown
        return "VOLATILE"


    def _make_live_recommendation(
        self,
        mean_recent:  float,
        n_sig_recent: int,
        n_symbols:    int,
    ) -> str:
        """
        Make a live trading recommendation based only on recent performance.

        The recommendation is deliberately conservative:
            TRADE NOW  → strong recent IC AND works across multiple symbols
            WATCH      → moderate recent IC, worth monitoring
            RESEARCH   → weak recent IC, needs more investigation
            AVOID      → no recent IC, do not use in live trading
        """

        # Strong recent IC + multiple symbol confirmation
        if mean_recent >= IC_STRONG and n_sig_recent >= 2:
            return "TRADE NOW"

        # Moderate recent IC with at least one symbol confirmation
        if mean_recent >= IC_MODERATE and n_sig_recent >= 1:
            return "WATCH"

        # Weak but non-zero recent IC — worth monitoring
        if mean_recent >= IC_WEAK:
            return "RESEARCH"

        # No meaningful signal in recent period
        return "AVOID"


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4: EXTRACT FEATURE CATEGORIES
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_live_features(
        self,
        classified: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Extract features recommended for immediate live trading.

        Criteria:
            live_rec = "TRADE NOW"
            OR (live_rec = "WATCH" AND trend = "GAINING" or "EMERGING")

        These features show evidence of edge in the current institutional
        crypto market (2024-2026). They should be prioritised for
        full institutional validation and backtesting.
        """

        # Primary: explicitly recommended for live trading
        trade_now = classified["live_rec"] == "TRADE NOW"

        # Secondary: watch-list features that are actively gaining
        watch_gaining = (
            (classified["live_rec"] == "WATCH") &
            (classified["trend"].isin(["GAINING", "EMERGING"]))
        )

        live_mask = trade_now | watch_gaining    # Combine both conditions
        live_df   = classified[live_mask].copy()

        # Sort by recent IC strength — most powerful features first
        live_df = live_df.sort_values("mean_ic_recent", ascending=False)

        logger.info(f"  Live features identified: {len(live_df)}")

        return live_df


    def _extract_dead_features(
        self,
        classified: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Extract features that should be removed from the pipeline.

        Criteria:
            trend = "DEAD" → no signal at all in recent years
            OR (trend = "DECAYING" AND mean_ic_recent < IC_WEAK)

        These features are consuming computational resources and potentially
        introducing noise into composite signals. They should be deleted
        from feature_calculator.py to keep the pipeline clean.
        """

        # Completely dead: no signal anywhere
        dead_mask    = classified["trend"] == "DEAD"

        # Decaying and now effectively dead in current market
        decay_dead   = (
            (classified["trend"] == "DECAYING") &
            (classified["mean_ic_recent"] < IC_WEAK)
        )

        remove_mask  = dead_mask | decay_dead
        dead_df      = classified[remove_mask].copy()

        # Sort by how dead they are — most useless first
        dead_df = dead_df.sort_values("mean_ic_recent", ascending=True)

        logger.info(f"  Dead features identified: {len(dead_df)}")

        return dead_df


    def _extract_regime_features(
        self,
        classified: pd.DataFrame,
        ic_matrix:  pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Extract features that work only in specific market conditions.

        These features are not dead — they just need to be deployed
        selectively. A feature that only works in bear markets should
        only be active when the regime detector classifies the market as BEAR.

        This is the foundation of regime-adaptive strategy deployment:
        right feature + right regime = better IC than any single feature alone.
        """

        # Features that are volatile but show regime preference
        regime_mask = (
            (classified["trend"] == "VOLATILE") &
            (classified["regime_pref"].isin(["BULL_ONLY", "BEAR_ONLY"]))
        )

        # Also include stable features with clear regime preference
        stable_regime = (
            (classified["trend"] == "STABLE") &
            (classified["regime_pref"] != "NONE")
        )

        combined_mask = regime_mask | stable_regime
        regime_df     = classified[combined_mask].copy()

        # Add deployment condition column
        regime_df["deploy_condition"] = regime_df["regime_pref"].map({
            "BULL_ONLY":   "Deploy only when: regime_detector = TRENDING or RECOVERY",
            "BEAR_ONLY":   "Deploy only when: regime_detector = VOLATILE or CRASH",
            "ALL_WEATHER": "Deploy in all regimes — no restriction needed",
        })

        logger.info(f"  Regime-specific features identified: {len(regime_df)}")

        return regime_df


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 5: CROSS-SYMBOL ROBUSTNESS
    # ─────────────────────────────────────────────────────────────────────────

    def _check_cross_symbol_robustness(
        self,
        classified: pd.DataFrame,
        symbols:    List[str],
    ) -> pd.DataFrame:
        """
        Identify features that show IC across multiple symbols.

        Why robustness matters:
            A feature that shows IC on BTC only might be a statistical
            fluke specific to BTC's price history. But a feature that shows
            IC on BTC, ETH, SOL, ADA, and XRP simultaneously is much more
            likely to represent a genuine market structural relationship.

        Features with high symbol coverage (>50% of symbols) are:
            More likely to be robust genuine signals
            More suitable for the cross-sectional engine
            Less likely to be data-mining artifacts

        We use pct_coverage from the classified DataFrame:
            >= 0.7 → HIGH: works on 5+ of 7 symbols — highly robust
            >= 0.5 → MEDIUM: works on 3-4 symbols — moderately robust
            < 0.5  → LOW: works on 1-2 symbols — symbol-specific
        """

        # Add robustness classification
        cross_df = classified.copy()

        cross_df["robustness"] = pd.cut(
            cross_df["pct_coverage"],         # Coverage fraction
            bins=[0, 0.29, 0.49, 0.69, 1.0], # Boundary thresholds
            labels=["VERY_LOW", "LOW", "MEDIUM", "HIGH"],  # Category labels
            include_lowest=True,              # Include 0.0 in first bin
        )

        # Sort: most robust first, then by recent IC
        cross_df = cross_df.sort_values(
            ["pct_coverage", "mean_ic_recent"],
            ascending=[False, False]
        )

        # Log highly robust features
        high_robust = cross_df[cross_df["robustness"] == "HIGH"]
        if not high_robust.empty:
            logger.info(
                f"  Highly robust features (70%+ symbol coverage): "
                f"{len(high_robust)}"
            )
            for _, row in high_robust.iterrows():
                logger.info(
                    f"    {row['feature']:<30} "
                    f"coverage={row['pct_coverage']:.0%} "
                    f"recent_IC={row['mean_ic_recent']:+.4f}"
                )

        return cross_df


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 6: BUILD MASTER REPORT TEXT
    # ─────────────────────────────────────────────────────────────────────────

    def _build_master_report(
        self,
        live_features:   pd.DataFrame,
        dead_features:   pd.DataFrame,
        regime_features: pd.DataFrame,
        cross_symbol:    pd.DataFrame,
        classified:      pd.DataFrame,
        symbols:         List[str],
    ) -> str:
        """
        Build the complete master report as a formatted text string.

        This is the human-readable output that answers all the key questions:
            What is alive in today's market?
            What should be removed?
            What is regime-specific?
            What works across multiple coins?
        """

        lines = []    # Collect all lines before joining

        # ── Report header ──────────────────────────────────────────────────────
        lines.append(SEP)
        lines.append("FEATURE EVOLUTION REPORT — QUANTIBOT PRO")
        lines.append(f"Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"Symbols analysed: {', '.join(symbols)}")
        lines.append(f"Forward return: {self.forward_col}")
        lines.append(SEP)

        # ── Executive summary ──────────────────────────────────────────────────
        lines.append("\nEXECUTIVE SUMMARY")
        lines.append(SEP_SHORT)

        # Count features in each category
        total      = len(classified)
        n_live     = len(live_features)
        n_dead     = len(dead_features)
        n_regime   = len(regime_features)
        n_gaining  = (classified["trend"] == "GAINING").sum()
        n_emerging = (classified["trend"] == "EMERGING").sum()
        n_decaying = (classified["trend"] == "DECAYING").sum()

        lines.append(f"  Total features analysed : {total}")
        lines.append(f"  TRADE NOW (live)        : {n_live}")
        lines.append(f"  AVOID (dead/decaying)   : {n_dead}")
        lines.append(f"  Regime-specific         : {n_regime}")
        lines.append(f"  GAINING (strengthening) : {n_gaining}")
        lines.append(f"  EMERGING (new signals)  : {n_emerging}")
        lines.append(f"  DECAYING (dying)        : {n_decaying}")

        # ── Section 1: Live features ───────────────────────────────────────────
        lines.append(f"\n{SEP}")
        lines.append("SECTION 1 — FEATURES TO TRADE NOW")
        lines.append("These features show IC > 0.03 in the institutional era (2024-2026)")
        lines.append("Deploy these in your live pipeline with confidence")
        lines.append(SEP_SHORT)

        if live_features.empty:
            lines.append("  No features qualify for immediate live trading.")
            lines.append("  This means your current feature set is not suited")
            lines.append("  to the current institutional crypto market.")
            lines.append("  → Priority: add funding rate, open interest, and")
            lines.append("    cross-asset divergence features (see recommendations below)")
        else:
            lines.append(
                f"  {'Feature':<28} {'Trend':<10} {'IC_2024':>8} "
                f"{'IC_2025':>8} {'IC_2026':>8} {'Coverage':>10}"
            )
            lines.append("  " + "-" * 75)
            for _, row in live_features.iterrows():
                ic_2024 = row.get("ic_2024", np.nan)
                ic_2025 = row.get("ic_2025", np.nan)
                ic_2026 = row.get("ic_2026", np.nan)
                lines.append(
                    f"  {row['feature']:<28} "
                    f"{row['trend']:<10} "
                    f"{self._fmt_ic(ic_2024):>8} "
                    f"{self._fmt_ic(ic_2025):>8} "
                    f"{self._fmt_ic(ic_2026):>8} "
                    f"{row['pct_coverage']:>9.0%}"
                )

        # ── Section 2: Dead features ───────────────────────────────────────────
        lines.append(f"\n{SEP}")
        lines.append("SECTION 2 — FEATURES TO REMOVE FROM PIPELINE")
        lines.append("These features have no predictive power in the current market")
        lines.append("Remove them from feature_calculator.py to reduce noise")
        lines.append(SEP_SHORT)

        if dead_features.empty:
            lines.append("  No features classified as dead — all features retain some signal")
        else:
            lines.append(
                f"  {'Feature':<28} {'Trend':<12} "
                f"{'IC_Early':>10} {'IC_Recent':>10}"
            )
            lines.append("  " + "-" * 65)
            for _, row in dead_features.iterrows():
                lines.append(
                    f"  {row['feature']:<28} "
                    f"{row['trend']:<12} "
                    f"{self._fmt_ic(row['mean_ic_early']):>10} "
                    f"{self._fmt_ic(row['mean_ic_recent']):>10}"
                )

        # ── Section 3: Regime-specific features ───────────────────────────────
        lines.append(f"\n{SEP}")
        lines.append("SECTION 3 — REGIME-SPECIFIC FEATURES")
        lines.append("These features work only in specific market conditions")
        lines.append("Deploy them selectively using the regime detector output")
        lines.append(SEP_SHORT)

        if regime_features.empty:
            lines.append("  No clearly regime-specific features identified")
        else:
            lines.append(
                f"  {'Feature':<28} {'Regime':<12} {'Deploy When'}"
            )
            lines.append("  " + "-" * 70)
            for _, row in regime_features.iterrows():
                lines.append(
                    f"  {row['feature']:<28} "
                    f"{row['regime_pref']:<12} "
                    f"{row.get('deploy_condition', 'See regime_pref')}"
                )

        # ── Section 4: Cross-symbol robustness ────────────────────────────────
        lines.append(f"\n{SEP}")
        lines.append("SECTION 4 — CROSS-SYMBOL ROBUSTNESS")
        lines.append("Features working across 70%+ of symbols are most reliable")
        lines.append("These are ideal inputs for the cross-sectional engine")
        lines.append(SEP_SHORT)

        high_robust = cross_symbol[cross_symbol["robustness"] == "HIGH"]
        if high_robust.empty:
            lines.append("  No features show high cross-symbol robustness (70%+)")
            lines.append("  Most signals are symbol-specific — not suitable for")
            lines.append("  cross-sectional ranking without further validation")
        else:
            lines.append(
                f"  {'Feature':<28} {'Coverage':>10} "
                f"{'IC Recent':>10} {'Trend':<12}"
            )
            lines.append("  " + "-" * 65)
            for _, row in high_robust.iterrows():
                lines.append(
                    f"  {row['feature']:<28} "
                    f"{row['pct_coverage']:>9.0%} "
                    f"{self._fmt_ic(row['mean_ic_recent']):>10} "
                    f"{row['trend']:<12}"
                )

        # ── Section 5: Year-by-year IC trajectory ─────────────────────────────
        lines.append(f"\n{SEP}")
        lines.append("SECTION 5 — FULL IC TRAJECTORY (all features, all years)")
        lines.append("Sorted by recent IC strength — most relevant to current market first")
        lines.append(SEP_SHORT)

        # Header row
        year_cols = list(YEAR_WINDOWS.keys())
        header = f"  {'Feature':<28}"
        for year in year_cols:
            header += f" {year:>8}"
        header += f"  {'Trend':<12} {'Rec'}"
        lines.append(header)
        lines.append("  " + "-" * (28 + len(year_cols) * 9 + 25))

        # One row per feature
        for _, row in classified.iterrows():
            line = f"  {row['feature']:<28}"
            for year in year_cols:
                ic_val = row.get(f"ic_{year}", np.nan)
                line  += f" {self._fmt_ic(ic_val):>8}"
            line += f"  {row['trend']:<12} {row['live_rec']}"
            lines.append(line)

        # ── Section 6: Recommendations ────────────────────────────────────────
        lines.append(f"\n{SEP}")
        lines.append("SECTION 6 — RESEARCH RECOMMENDATIONS")
        lines.append("Actions to improve pipeline relevance to current market")
        lines.append(SEP_SHORT)

        lines.append("""
  Based on the IC trajectory analysis, here are the highest-priority
  actions to make your research pipeline relevant to 2025-2026 crypto:

  1. ADD FUNDING RATE FEATURES (Priority: CRITICAL)
     Funding rates are the #1 predictor in institutional crypto markets.
     When funding > 0.1% per 8h, longs are overleveraged → bearish signal.
     Add to feature_calculator.py:
       df['funding_rate']        — raw 8h perpetual funding rate
       df['funding_zscore']      — funding vs 7-day history
       df['funding_signal']      — -1 × funding_zscore (mean reversion)

  2. ADD OPEN INTEREST CHANGE (Priority: HIGH)
     OI rising during a move = confirmation. OI falling = exhaustion.
     Add to feature_calculator.py:
       df['oi_change_1h']        — hourly change in open interest
       df['oi_price_divergence'] — price up but OI down = weak move

  3. ADD TIME-OF-DAY FEATURES (Priority: HIGH)
     London open (7-8 UTC) and NY open (13-14 UTC) have genuine patterns.
     Add to feature_calculator.py:
       df['hour_of_day']         — 0 to 23
       df['is_london_open']      — 1 if hour in [7,8,9,10]
       df['is_ny_overlap']       — 1 if hour in [13,14,15,16]
       df['is_weekend']          — 1 if Saturday or Sunday

  4. RELAX HALF-LIFE FILTER FOR 4H TESTING (Priority: MEDIUM)
     29 cointegrated pairs failed only because HL > 120h.
     On 4h candles, HL=200h becomes 50 candles — perfectly tradeable.
     Download 4h data and re-run cointegration_engine.py.

  5. VALIDATE AVAX/ATOM SPREAD IMMEDIATELY (Priority: HIGH)
     This is your only ✓ VALID cointegration pair.
     Run: validator.validate_all_features_institutional(
       spread_df, forward_return_col='spread_forward_4h'
     )
     If pair_signal shows IC > 0.03 — you have your first live edge.
        """)

        lines.append(SEP)

        return "\n".join(lines)    # Join all lines into final report string


    def _fmt_ic(self, ic_val) -> str:
        """
        Format an IC value for display in the report table.

        Shows +/- sign, 4 decimal places, or N/A if missing.
        """
        if pd.isna(ic_val) or ic_val is None:
            return "  N/A "          # Placeholder for missing data
        return f"{float(ic_val):+.4f}"    # Signed format: +0.0312 or -0.0156


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 7: SAVE ALL OUTPUTS
    # ─────────────────────────────────────────────────────────────────────────

    def _save_all_outputs(
        self,
        report_text:     str,
        live_features:   pd.DataFrame,
        dead_features:   pd.DataFrame,
        regime_features: pd.DataFrame,
        full_matrix:     pd.DataFrame,
        cross_symbol:    pd.DataFrame,
    ) -> None:
        """
        Save all report outputs to the temporal directory.

        Files saved:
            evolution_master_report.txt  — human-readable full report
            evolution_live_features.csv  — features to deploy now
            evolution_dead_features.csv  — features to remove
            evolution_regime_specific.csv — conditional deployment features
            evolution_full_matrix.csv    — complete IC matrix
            evolution_cross_symbol.csv   — robustness assessment
        """

        # Master text report — the most important output for human review
        txt_path = self.output_dir / "evolution_master_report.txt"
        txt_path.write_text(report_text, encoding="utf-8")
        logger.info(f"  Saved: {txt_path}")

        # Live features CSV — input to live trading pipeline
        if not live_features.empty:
            p = self.output_dir / "evolution_live_features.csv"
            live_features.to_csv(p, index=False)
            logger.info(f"  Saved: {p}")

        # Dead features CSV — features to remove from pipeline
        if not dead_features.empty:
            p = self.output_dir / "evolution_dead_features.csv"
            dead_features.to_csv(p, index=False)
            logger.info(f"  Saved: {p}")

        # Regime-specific features CSV — conditional deployment guide
        if not regime_features.empty:
            p = self.output_dir / "evolution_regime_specific.csv"
            regime_features.to_csv(p, index=False)
            logger.info(f"  Saved: {p}")

        # Full IC matrix — complete audit trail for all features × years
        if not full_matrix.empty:
            p = self.output_dir / "evolution_full_matrix.csv"
            full_matrix.to_csv(p, index=False)
            logger.info(f"  Saved: {p}")

        # Cross-symbol robustness — which features generalise across coins
        if not cross_symbol.empty:
            p = self.output_dir / "evolution_cross_symbol.csv"
            cross_symbol.to_csv(p, index=False)
            logger.info(f"  Saved: {p}")


    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_timestamp(
        self, df: pd.DataFrame, symbol: str
    ) -> Optional[pd.DataFrame]:
        """Parse timestamp column and set as UTC-aware DatetimeIndex."""

        df = df.copy()

        # Try common timestamp column names in order of preference
        for col in ['timestamp', 'index', 'Unnamed: 0']:
            if col in df.columns:
                try:
                    df[col] = pd.to_datetime(df[col], utc=True)
                    df.set_index(col, inplace=True)
                    break
                except Exception:
                    continue

        # Validate we now have a DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            logger.warning(f"  {symbol}: could not create DatetimeIndex")
            return None

        # Ensure UTC timezone is attached
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

        df.sort_index(inplace=True)    # Ensure chronological order
        return df

    def _get_features(self, df: pd.DataFrame) -> List[str]:
        """Return list of testable numeric feature column names."""

        return [
            c for c in df.columns
            if c not in EXCLUDE_COLS                                  # Not excluded
            and df[c].dtype in [np.float64, np.float32,              # Is numeric
                                 np.int64, np.int32]
            and not df[c].isna().all()                                # Not all NaN
        ]


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR — runs all three modules in correct sequence
# ═══════════════════════════════════════════════════════════════════════════════

def run_full_temporal_suite(
    observations_dir: str       = "research_data",      # Raw observation CSVs
    temporal_dir:     str       = "research_data/temporal",  # Output directory
    forward_col:      str       = "forward_return_4h",  # What we predict
    symbols:          List[str] = None,                 # Which symbols
    run_stability:    bool      = True,                 # Run Module 1?
    run_decay:        bool      = True,                 # Run Module 2?
    run_evolution:    bool      = True,                 # Run Module 3?
) -> Dict:
    """
    Run the complete temporal research suite in sequence.

    Sequence:
        Module 1 (FeatureStabilityAnalyzer) → yearly + rolling IC data
        Module 2 (FeatureDecayAnalyzer)     → visual decay + regime breakdown
        Module 3 (FeatureEvolutionReport)   → master comparison report

    Module 3 reads Module 1's CSV outputs, so Module 1 must run first.
    Module 2 is independent and can run in any order.

    Run from project root:
        python -m bot.research.feature_evolution_report

    Parameters
    ----------
    observations_dir : str
        Folder containing {SYMBOL}_observations.csv files.

    temporal_dir : str
        Folder for all temporal analysis outputs.

    forward_col : str
        Which forward return column to validate features against.

    symbols : List[str]
        Symbols to process. Default: all 7.

    run_stability : bool
        Whether to run Module 1. Set False to skip if already run.

    run_decay : bool
        Whether to run Module 2. Set False to skip if already run.

    run_evolution : bool
        Whether to run Module 3 (the master report).

    Returns
    -------
    Dict with keys: "stability", "decay", "evolution"
    """

    symbols = symbols or SYMBOLS    # Default to all 7 symbols
    results = {}                    # Collect outputs from each module

    logger.info(SEP)
    logger.info("TEMPORAL RESEARCH SUITE — FULL RUN")
    logger.info(SEP)
    logger.info(f"  Symbols  : {symbols}")
    logger.info(f"  Forward  : {forward_col}")
    logger.info(f"  Output   : {temporal_dir}")

    # ── Module 1: Feature Stability Analyzer ──────────────────────────────────
    if run_stability and STABILITY_AVAILABLE:
        logger.info("\n[MODULE 1] Running FeatureStabilityAnalyzer...")
        stability_results = {}

        analyzer = FeatureStabilityAnalyzer(
            forward_col=forward_col,
            output_dir=temporal_dir,
        )

        for symbol in symbols:
            obs_path = Path(observations_dir) / f"{symbol}_observations.csv"
            if obs_path.exists():
                try:
                    df = pd.read_csv(obs_path)
                    stability_results[symbol] = analyzer.analyze(df, symbol)
                except Exception as e:
                    logger.error(f"  {symbol}: stability failed — {e}")

        results["stability"] = stability_results
        logger.info("[MODULE 1] Complete")

    elif run_stability and not STABILITY_AVAILABLE:
        logger.warning(
            "[MODULE 1] FeatureStabilityAnalyzer not available. "
            "Check bot/research/feature_stability_analyzer.py"
        )

    # ── Module 2: Feature Decay Analyzer ──────────────────────────────────────
    if run_decay and DECAY_AVAILABLE:
        logger.info("\n[MODULE 2] Running FeatureDecayAnalyzer...")
        decay_results = {}

        decay_analyzer = FeatureDecayAnalyzer(
            forward_col=forward_col,
            output_dir=temporal_dir,
        )

        for symbol in symbols:
            obs_path = Path(observations_dir) / f"{symbol}_observations.csv"
            if obs_path.exists():
                try:
                    df = pd.read_csv(obs_path)
                    decay_results[symbol] = decay_analyzer.analyze(df, symbol)
                except Exception as e:
                    logger.error(f"  {symbol}: decay failed — {e}")

        results["decay"] = decay_results
        logger.info("[MODULE 2] Complete")

    elif run_decay and not DECAY_AVAILABLE:
        logger.warning(
            "[MODULE 2] FeatureDecayAnalyzer not available. "
            "Check bot/research/feature_decay_analyzer.py"
        )

    # ── Module 3: Feature Evolution Report ────────────────────────────────────
    if run_evolution:
        logger.info("\n[MODULE 3] Running FeatureEvolutionReport...")

        report = FeatureEvolutionReport(
            forward_col=forward_col,
            output_dir=temporal_dir,
        )

        # Use precomputed data from Module 1 if it was just run
        use_precomputed = run_stability and STABILITY_AVAILABLE

        evolution_results = report.generate(
            observations_dir=observations_dir,
            temporal_dir=temporal_dir,
            symbols=symbols,
            use_precomputed=use_precomputed,
        )

        results["evolution"] = evolution_results
        logger.info("[MODULE 3] Complete")

    logger.info(SEP)
    logger.info("TEMPORAL RESEARCH SUITE — ALL MODULES COMPLETE")
    logger.info(f"All outputs saved to: {temporal_dir}")
    logger.info(SEP)

    return results


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    """
    Run the complete temporal research suite.

    Command:
        python -m bot.research.feature_evolution_report

    This runs all three modules in sequence:
        1. FeatureStabilityAnalyzer — yearly + rolling IC
        2. FeatureDecayAnalyzer     — visual decay + regime breakdown
        3. FeatureEvolutionReport   — master comparison report

    All outputs are saved to research_data/temporal/
    """

    run_full_temporal_suite(
        observations_dir="research_data",        # Where observation CSVs are
        temporal_dir="research_data/temporal",   # Where to save all outputs
        forward_col="forward_return_4h",         # What we predict
        symbols=SYMBOLS,                         # All 7 symbols
        run_stability=True,                      # Run Module 1
        run_decay=True,                          # Run Module 2
        run_evolution=True,                      # Run Module 3
    )