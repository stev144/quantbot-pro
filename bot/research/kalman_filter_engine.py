# ═══════════════════════════════════════════════════════════════════════════════
# bot/research/kalman_filter_engine.py
#
# INSTITUTIONAL KALMAN FILTER ENGINE
# QuantiBot Pro Research Pipeline — Phase 3
#
# Version  : 1.0 (Research Grade)
# Author   : QuantiBot Pro Research Pipeline
#
# ─────────────────────────────────────────────────────────────────────────────
# PROJECT CONTEXT — WHY THIS MODULE EXISTS
# ─────────────────────────────────────────────────────────────────────────────
#
# The cointegration_engine.py discovered one valid tradeable pair:
#
#     AVAX_USDT / ATOM_USDT
#     ADF p-value   : 0.0000  (extremely cointegrated)
#     Coint p-value : 0.0003  (99.97% confidence)
#     OLS hedge ratio (β) : 1.4679
#     Half-life     : 119.9 hours
#
# The cointegration engine uses a STATIC hedge ratio.
# That hedge ratio was estimated on the training window (first 10,000 candles).
# It does NOT update as the market evolves.
# The problem with a static hedge ratio:
#
#     The relationship between AVAX and ATOM in 2020 is not the same as
#     the relationship in 2025. Both assets have changed — different market
#     caps, different liquidity profiles, different institutional adoption.
#
#     If β was 1.47 in 2021 but is now 1.65 in 202=5 and we still use 1.47,
#     then our spread calculation is systematically wrong.
#     We think the spread is at z=2.5 when it is actually at z=1.8.
#     We enter trades expecting reversion that has already partially happened.
# The relationship between AVAX and ATOM in 2020 is not the same as the relationship in 2025. Both assets have changed 
# The Kalman filter solves this by updating the hedge ratio at every candle.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT IS THE KALMAN FILTER — PLAIN ENGLISH
# ─────────────────────────────────────────────────────────────────────────────
#
# Imagine you are navigating a ship to a destination.
#
# A FIXED COMPASS gives you one direction forever.
# Set it once and never adjust. This is the OLS hedge ratio from the
# cointegration engine — estimated once, applied.
#
# A KALMAN FILTER is the navigator who continuously adjusts course.
# At every moment, they:
#     1. PREDICT where the ship probably is based on the last known position
#     2. OBSERVE where the ship actually is from GPS
#     3. UPDATE the estimate, weighting prediction vs observation by confidence
#
# For AVAX/ATOM:
#     State     = [hedge ratio β, intercept α] — the equilibrium relationship
#     Prediction = "the relationship probably changed a tiny bit this hour"
#     Observation = "here are the actual AVAX and ATOM prices this hour"
#     Update     = combine prediction and observation to get the best estimate
#
# The result: a hedge ratio that smoothly tracks the true relationship
# as it slowly evolves over months and years.
#
# ─────────────────────────────────────────────────────────────────────────────
# HOW THIS MODULE FITS INTO THE QUANTIBOT PRO PIPELINE
# ─────────────────────────────────────────────────────────────────────────────
#
# FEEDS FROM (reads output of these modules):
#   cointegration_engine.py   → research_data/AVAX_USDT_ATOM_USDT_cointegration.csv
#                             → research_data/cointegration_pairs.csv (hedge ratio seed)
#   fetch_all_symbols.py      → data/AVAX_USDT_1h.csv
#                             → data/ATOM_USDT_1h.csv
#
# FEEDS INTO (its output is consumed by):
#   feature_validator_v2_institutional.py → validates dynamic spread z-score for IC
#   feature_stability_analyzer.py         → tests IC stability year by year
#   feature_decay_analyzer.py             → detects if Kalman signal decays over time
#   [future] position_sizing_engine.py    → uses signal strength for trade sizing
#   [future] execution_engine.py          → generates actual trade orders
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT THIS MODULE PRODUCES
# ─────────────────────────────────────────────────────────────────────────────
#
# For each timestamp in the AVAX/ATOM history:
#
#   kalman_beta          Dynamic hedge ratio β (how many ATOM per AVAX)
#   kalman_alpha         Dynamic intercept α (equilibrium price level)
#   kalman_spread        Dynamic spread: log(AVAX) - α - β × log(ATOM)
#   kalman_spread_mean   Rolling mean of dynamic spread (current equilibrium)
#   kalman_spread_std    Rolling std of dynamic spread (normal wandering)
#   kalman_zscore        (spread - mean) / std — how stretched is the pair?
#   kalman_zscore_lag1   Previous candle z-score (momentum context)
#   beta_change_rate     How fast is the hedge ratio changing? (stability signal)
#   prediction_error     How wrong was the filter? (model health indicator)
#   pair_signal_dynamic  Composite signal combining z-score + momentum
#   forward_return_Nh    What the spread did N hours later (IC test labels)
#
# Output files:
#   research_data/AVAX_USDT_ATOM_USDT_kalman.csv    → main feature output
#   research_data/kalman_beta_trajectory.csv          → hedge ratio over time
#   research_data/kalman_diagnostics.csv              → model health metrics
#
# ─────────────────────────────────────────────────────────────────────────────
# THE MATHEMATICS — EXPLAINED CLEARLY
# ─────────────────────────────────────────────────────────────────────────────
#
# STATE SPACE MODEL:
#
#   State vector θ_t = [β_t, α_t]ᵀ  (hedge ratio and intercept at time t)
#
#   STATE TRANSITION (how the state evolves between candles):
#       θ_t = F × θ_{t-1} + w_t
#       F = identity matrix (relationship changes slowly, not randomly)
#       w_t ~ N(0, Q)  where Q is the process noise covariance
#       Q controls how fast we allow β and α to change per candle
#       Small Q = slow adaptation (trusts history more)
#       Large Q = fast adaptation (trusts new data more)
#
#   OBSERVATION MODEL (how prices relate to the state):
#       y_t = H_t × θ_t + v_t
#       y_t = log(AVAX_price_t)          (what we observe)
#       H_t = [log(ATOM_price_t), 1]     (design matrix at time t)
#       v_t ~ N(0, R)  where R is the observation noise variance
#       R controls how much we trust individual price observations
#
#   KALMAN EQUATIONS (two steps per candle):
#
#   PREDICTION STEP:
#       θ̂_{t|t-1} = F × θ̂_{t-1|t-1}          (predicted state)
#       P_{t|t-1}  = F × P_{t-1|t-1} × Fᵀ + Q  (predicted covariance)
#
#   UPDATE STEP:
#       Innovation:  e_t = y_t - H_t × θ̂_{t|t-1}
#       Innovation cov: S_t = H_t × P_{t|t-1} × H_tᵀ + R
#       Kalman gain: K_t = P_{t|t-1} × H_tᵀ / S_t
#       Updated state: θ̂_{t|t} = θ̂_{t|t-1} + K_t × e_t
#       Updated cov:   P_{t|t} = (I - K_t × H_t) × P_{t|t-1}
#
#   DYNAMIC SPREAD:
#       spread_t = log(AVAX_t) - α_t - β_t × log(ATOM_t)
#
# ═══════════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations              # Modern type hints

import logging                                  # Structured logging
import warnings                                 # Suppress non-critical warnings
from pathlib import Path                        # Cross-platform file paths
from typing import Dict, List, Optional, Tuple  # Type annotations

import numpy as np                              # Numerical operations — core of Kalman math
import pandas as pd                             # DataFrame operations
from scipy import stats                         # Spearman IC, OLS initialisation
from statsmodels.regression.linear_model import OLS    # OLS for hedge ratio seed
from statsmodels.tools import add_constant             # Adds intercept column to OLS

warnings.filterwarnings('ignore')               # Suppress statsmodels convergence warnings


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)            # Module-level dedicated logger

if not logger.handlers:                         # Prevent duplicate handlers on re-import
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT-SPECIFIC CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# Where cointegration_engine.py saves its results — one row per tested pair.
# This is the single source of truth for every pair's hedge ratio, intercept,
# and half-life. The Kalman engine used to keep its own hand-typed copy of
# these numbers in a VALID_PAIRS dict — meaning every newly discovered pair
# required a code edit before it could be tested at all, and the dict could
# silently drift out of sync with whatever cointegration_engine.py actually
# found. load_pair_config() below reads the CSV directly instead, so any
# pair cointegration_engine.py has ever tested is immediately usable here.
DEFAULT_COINTEGRATION_PAIRS_CSV: str = "research_data/cointegration_pairs.csv"


def load_pair_config(
    pair_name:               str,
    cointegration_pairs_csv: str  = DEFAULT_COINTEGRATION_PAIRS_CSV,
    require_passes_filters:  bool = True,
) -> Dict:
    """
    Look up one pair's hedge ratio, intercept, and half-life directly from
    cointegration_engine.py's saved output — the same CSV a human would
    open to see which pairs were found cointegrated.

    Why load from the CSV instead of a hardcoded dict?

        A hardcoded dict has to be hand-updated every time a new pair is
        worth testing, and there is nothing stopping the dict's numbers
        from silently drifting away from whatever cointegration_engine.py
        actually computed. Reading the CSV means the Kalman engine always
        sees exactly what the cointegration engine found — no copy-paste,
        no re-typing, no risk of the two disagreeing.

    Parameters
    ----------
    pair_name : str
        Must match a "pair_name" value in the CSV, e.g. "AVAX_USDT/ATOM_USDT".

    cointegration_pairs_csv : str
        Path to cointegration_engine.py's saved pair summary.

    require_passes_filters : bool
        cointegration_engine.py marks each pair "passes_filters" True/False
        based on its own half-life ceiling (MAX_HALF_LIFE_HOURS). By
        default we refuse to load a pair that failed that filter — the
        same way the old VALID_PAIRS dict only ever contained pairs
        someone had manually decided were good. Set this to False to
        deliberately test a pair that narrowly missed the cutoff, e.g. a
        ~121h half-life pair rejected only because 121 > 120 — useful
        for checking whether an arbitrary boundary is hiding a real
        result, exactly the kind of check we flagged when reviewing
        cointegration_engine.py's half-life filter for selection bias.

    Returns
    -------
    Dict
        Same shape the old VALID_PAIRS dict used, so nothing downstream
        of __init__ needs to change: symbol_a, symbol_b, ols_beta,
        ols_alpha, half_life_h, adf_pvalue, coint_pvalue, passes_filters.
    """

    csv_path = Path(cointegration_pairs_csv)
    if not csv_path.exists():
        raise FileNotFoundError(
            f"cointegration_pairs.csv not found at {csv_path}. "
            f"Run cointegration_engine.py first — it discovers pairs and "
            f"saves this file, which is what the Kalman engine now reads "
            f"its hedge ratio, intercept, and half-life seed from."
        )

    pairs_df = pd.read_csv(csv_path)
    match    = pairs_df[pairs_df["pair_name"] == pair_name]

    if match.empty:
        available = ", ".join(pairs_df["pair_name"].tolist())
        raise ValueError(
            f"Pair '{pair_name}' not found in {csv_path}. "
            f"Pairs cointegration_engine.py actually tested: {available}"
        )

    row = match.iloc[0]   # A pair_name should only ever appear once per run

    if require_passes_filters and not bool(row["passes_filters"]):
        raise ValueError(
            f"Pair '{pair_name}' did not pass cointegration_engine.py's "
            f"filters (reason: {row['reject_reason']}). Pass "
            f"require_passes_filters=False to test it anyway."
        )

    return {
        "symbol_a":       row["symbol_a"],
        "symbol_b":       row["symbol_b"],
        "ols_beta":       float(row["hedge_ratio"]),      # β seed for the Kalman filter
        "ols_alpha":      float(row["intercept"]),        # α seed for the Kalman filter
        "half_life_h":    float(row["half_life_hours"]),  # For logging/time-stop context only
        "adf_pvalue":     float(row["adf_pvalue"]),
        "coint_pvalue":   float(row["coint_pvalue"]),
        "passes_filters": bool(row["passes_filters"]),
    }

# Kalman filter process noise — controls how fast β and α are allowed to change
# Smaller = slower adaptation (trusts historical relationship more)
# Larger = faster adaptation (reacts quickly to structural shifts)
# These values are calibrated for 1h crypto candles and a ~120h half-life pair
# If β is jumping erratically: reduce PROCESS_NOISE_BETA
# If β is not tracking a genuine shift: increase PROCESS_NOISE_BETA
PROCESS_NOISE_BETA:      float = 1e-5   # How much β changes per candle on average
PROCESS_NOISE_ALPHA:     float = 1e-5   # How much α changes per candle on average

# Observation noise — how much measurement uncertainty in each price observation
# For liquid crypto assets on Binance, prices are highly accurate
# This value is calibrated to the typical hourly price variance of AVAX
OBSERVATION_NOISE:       float = 1e-3   # Variance of log-price observation error

# Warmup period — number of candles before trusting the Kalman estimates
# The filter needs time to converge from its initial state to a stable estimate
# During warmup, the filter runs but outputs are marked as not-yet-reliable
# 168 hours = 7 days — sufficient for the filter to converge for our pair
WARMUP_CANDLES:          int   = 168    # 7 days of 1h candles

# Z-score rolling window for dynamic spread normalisation
# Must match the window used in cointegration_engine.py for fair comparison
# 504 hours = 3 weeks — stable enough for crypto's volatility profile
ZSCORE_WINDOW:           int   = 504    # 3 weeks

# Minimum observations for z-score to be valid
ZSCORE_MIN_PERIODS:      int   = 168    # 1 week minimum

# claude code changed: new block — extracted the bare 5.0 winsor literal into a named constant
# Winsorisation cap applied to kalman_zscore and ols_zscore (see _calculate_zscore)
# Values beyond ±5 std are almost certainly data errors or exchange anomalies
# NOTE: any EXIT_ZSCORE_STOPLOSS (entry_exit_engine.py) set close to this limit
# will rarely fire, since |z| > ZSCORE_WINSOR_LIMIT is structurally impossible
ZSCORE_WINSOR_LIMIT:     float = 5.0    # claude code changed: single named source of truth for both clip() calls below

# Forward return horizons for IC testing
# These match the labels in cointegration_engine.py for direct comparison
# The validator will test which horizon shows the best IC
FORWARD_HORIZONS: Dict[str, int] = {
    "spread_forward_1h":  1,     # 1h ahead  — short reversion
    "spread_forward_2h":  2,     # 2h ahead  — primary test horizon
    "spread_forward_4h":  4,     # 4h ahead  — half-life aligned
    "spread_forward_12h": 12,    # 12h ahead — medium-term reversion
    "spread_forward_24h": 24,    # 24h ahead — full-day reversion
}

# Entry signal thresholds for the pair signal
# These are RESEARCH parameters — not live trading triggers
# They are used to generate a composite signal for IC testing
# The actual trading thresholds are determined after IC validation
SIGNAL_ENTRY_THRESHOLD:  float = 2.0    # |z-score| above this = potential entry
SIGNAL_STRONG_THRESHOLD: float = 3.0    # |z-score| above this = strong signal

# Minimum price — prevents log(0) errors for very low-priced assets
MIN_PRICE:               float = 1e-8


# ═══════════════════════════════════════════════════════════════════════════════
# KALMAN FILTER STATE CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class KalmanState:
    """
    Holds the complete state of the Kalman filter at one point in time.

    The Kalman filter is a sequential algorithm — it updates one candle at
    a time. After processing each candle, the state is updated and stored.

    This class encapsulates everything needed to:
        1. Make a prediction for the next candle (theta, P)
        2. Compute the dynamic spread (theta contains [beta, alpha])
        3. Diagnose filter health (innovation, innovation_var)

    Attributes
    ----------
    theta : np.ndarray shape (2,)
        Current state estimate [beta, alpha].
        theta[0] = β (dynamic hedge ratio)
        theta[1] = α (dynamic intercept)

    P : np.ndarray shape (2, 2)
        State covariance matrix.
        Represents uncertainty in the theta estimate.
        Large diagonal = filter is uncertain about current state.
        Small diagonal = filter is confident about current state.

    innovation : float
        Prediction error at the last update step.
        innovation = actual_observation - predicted_observation
        Should be small and random for a well-calibrated filter.
        Systematic large innovations = model is wrong.

    innovation_var : float
        Variance of the innovation (S in the Kalman equations).
        Used to detect when the filter is poorly calibrated.
    """

    def __init__(
        self,
        theta:          np.ndarray,
        P:              np.ndarray,
        innovation:     float = 0.0,
        innovation_var: float = 1.0,
    ) -> None:
        """Initialise with state estimate and covariance."""
        self.theta          = theta           # [beta, alpha] — the hedge relationship
        self.P              = P               # Uncertainty in the estimate
        self.innovation     = innovation      # Last prediction error
        self.innovation_var = innovation_var  # Innovation variance

    @property
    def beta(self) -> float:
        """Current dynamic hedge ratio β."""
        return float(self.theta[0])

    @property
    def alpha(self) -> float:
        """Current dynamic intercept α."""
        return float(self.theta[1])


# ═══════════════════════════════════════════════════════════════════════════════
# KALMAN FILTER ENGINE CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class KalmanFilterEngine:
    """
    Project-specific Kalman filter for the AVAX/ATOM cointegrated pair.

    This is NOT a generic Kalman filter implementation.
    It is specifically designed to:

        1. Read the CSV output of cointegration_engine.py
        2. Seed itself with the OLS hedge ratio discovered for AVAX/ATOM
        3. Update the hedge ratio dynamically at every 1h candle
        4. Produce features compatible with feature_validator_v2_institutional.py
        5. Generate outputs that feed directly into the next research phases

    The key improvement over the static OLS hedge ratio:
        OLS: estimates β once on historical data, applies it forever
        Kalman: updates β at every single candle using the latest price information

    This matters because AVAX and ATOM are evolving assets in an evolving market.
    The relationship that held in 2021 has shifted by 2025 due to:
        - Changes in each asset's market cap and institutional adoption
        - Shifts in the correlation between Layer 1 and interoperability sectors
        - Changes in liquidity profiles affecting price discovery speed
        - Evolving crypto market microstructure (increasing institutional participation)

    Integration with the QuantiBot Pro pipeline:
        INPUT:  data/AVAX_USDT_1h.csv + data/ATOM_USDT_1h.csv
                research_data/cointegration_pairs.csv (for OLS seed values)
        OUTPUT: research_data/AVAX_USDT_ATOM_USDT_kalman.csv
                research_data/kalman_beta_trajectory.csv
                research_data/kalman_diagnostics.csv
    """

    def __init__(
        self,
        pair_name:               str   = "AVAX_USDT/ATOM_USDT",
        cointegration_pairs_csv: str   = DEFAULT_COINTEGRATION_PAIRS_CSV,
        require_passes_filters:  bool  = True,
        process_noise_beta:      float = PROCESS_NOISE_BETA,
        process_noise_alpha:     float = PROCESS_NOISE_ALPHA,
        observation_noise:       float = OBSERVATION_NOISE,
        warmup_candles:          int   = WARMUP_CANDLES,
        zscore_window:           int   = ZSCORE_WINDOW,
        zscore_min_periods:      int   = ZSCORE_MIN_PERIODS,
        forward_horizons:        Dict  = None,
    ) -> None:
        """
        Initialise the Kalman filter engine for the specified pair.

        Parameters
        ----------
        pair_name : str
            Must match a "pair_name" row in cointegration_pairs_csv, e.g.
            "AVAX_USDT/ATOM_USDT". Any pair cointegration_engine.py has
            tested and saved is usable here — this is no longer limited
            to a fixed, hand-maintained list.

        cointegration_pairs_csv : str
            Path to cointegration_engine.py's saved pair summary — see
            load_pair_config() above for what's read from it.

        require_passes_filters : bool
            If True (default), refuse to load a pair that failed
            cointegration_engine.py's own half-life filter. Set False to
            deliberately test a pair that narrowly missed the cutoff.

        process_noise_beta : float
            How much the hedge ratio β is allowed to change per candle.
            Smaller = slower adaptation. Larger = faster adaptation.
            Default 1e-5 is calibrated for 1h crypto candles.

        process_noise_alpha : float
            How much the intercept α is allowed to change per candle.
            Same interpretation as process_noise_beta.

        observation_noise : float
            Variance of log-price observation noise.
            Reflects how much measurement uncertainty exists in each price.

        warmup_candles : int
            How many candles before the filter estimates are trusted.
            Default 168 = 7 days. Filter runs during warmup but outputs
            are flagged as not yet reliable.

        zscore_window : int
            Rolling window for dynamic spread z-score normalisation.
            Default 504 = 3 weeks. Matches cointegration_engine.py.

        zscore_min_periods : int
            Minimum candles before computing a valid z-score.

        forward_horizons : Dict[str, int]
            Forward return label names and periods for IC testing.
        """

        # Load this pair's config directly from cointegration_engine.py's
        # own output — see load_pair_config() above for what this reads
        # and why it replaced a hand-maintained dict.
        self.pair_config    = load_pair_config(
            pair_name,
            cointegration_pairs_csv=cointegration_pairs_csv,
            require_passes_filters=require_passes_filters,
        )
        self.pair_name      = pair_name                  # e.g. "AVAX_USDT/ATOM_USDT"
        self.symbol_a       = self.pair_config["symbol_a"]   # AVAX_USDT
        self.symbol_b       = self.pair_config["symbol_b"]   # ATOM_USDT

        # OLS seed values from cointegration_engine.py
        # The Kalman filter is initialised with these values and then updates from there
        self.ols_beta       = self.pair_config["ols_beta"]   # 1.4679
        self.ols_alpha      = self.pair_config["ols_alpha"]  # 0.7916
        self.half_life_h    = self.pair_config["half_life_h"] # 119.9h

        # Kalman filter hyperparameters
        self.qb             = process_noise_beta    # Process noise for β
        self.qa             = process_noise_alpha   # Process noise for α
        self.R              = observation_noise     # Observation noise
        self.warmup         = warmup_candles        # Candles before trusting output
        self.zscore_window  = zscore_window         # Z-score rolling window
        self.zscore_min_p   = zscore_min_periods    # Z-score minimum periods
        self.horizons       = forward_horizons or FORWARD_HORIZONS

        logger.info("KalmanFilterEngine initialised")
        logger.info(f"  Pair             : {pair_name}")
        logger.info(f"  Asset A          : {self.symbol_a}")
        logger.info(f"  Asset B          : {self.symbol_b}")
        logger.info(f"  OLS β seed       : {self.ols_beta}")
        logger.info(f"  OLS α seed       : {self.ols_alpha}")
        logger.info(f"  Half-life        : {self.half_life_h}h")
        logger.info(f"  Process noise β  : {self.qb}")
        logger.info(f"  Process noise α  : {self.qa}")
        logger.info(f"  Observation noise: {self.R}")
        logger.info(f"  Warmup candles   : {warmup_candles}")
        logger.info(f"  Z-score window   : {zscore_window}h")
        logger.info(f"  Forward horizons : {list(self.horizons.keys())}")


    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC: run()
    # ─────────────────────────────────────────────────────────────────────────

    def run(
        self,
        data_dir:      str = "data",
        output_dir:    str = "research_data",
    ) -> pd.DataFrame:
        """
        Main entry point. Run the full Kalman filter pipeline.

        Steps:
            1  Load AVAX and ATOM price CSVs
            2  Validate data quality and alignment
            3  Convert to log prices (required for the state-space model)
            4  Initialise Kalman filter state using OLS seed from cointegration engine
            5  Run the Kalman filter sequentially through all 50,000 candles
            6  Calculate dynamic spread from Kalman estimates
            7  Z-score the dynamic spread against its rolling history
            8  Calculate composite pair signal for IC testing
            9  Calculate forward return labels for validator
            10 Compare Kalman dynamic signal vs static OLS signal
            11 Save all outputs and log summary

        Parameters
        ----------
        data_dir : str
            Folder containing AVAX_USDT_1h.csv and ATOM_USDT_1h.csv.
            These are produced by fetch_all_symbols.py.

        output_dir : str
            Folder to save Kalman output CSVs.
            These feed into feature_validator_v2_institutional.py.

        Returns
        -------
        pd.DataFrame
            Main output DataFrame with all Kalman features and labels.
            Ready to pass directly to feature_validator_v2_institutional.py.
        """

        logger.info("=" * 70)
        logger.info("KALMAN FILTER ENGINE — AVAX/ATOM — STARTING")
        logger.info("=" * 70)

        # ── Step 1: Load price data ───────────────────────────────────────────
        price_a, price_b = self._load_prices(data_dir)

        # ── Step 2: Validate and align ────────────────────────────────────────
        aligned = self._align_prices(price_a, price_b)

        # ── Step 3: Convert to log prices ─────────────────────────────────────
        # Kalman filter operates on log prices for two reasons:
        #   1. The hedge ratio β is a price RATIO in log space (cleaner statistic)
        #   2. Log prices are additive — making the linear state model valid
        log_a, log_b = self._to_log_prices(aligned)

        # ── Step 4: Initialise Kalman state ───────────────────────────────────
        # Seeded with the OLS hedge ratio from cointegration_engine.py
        # This prevents a cold-start problem where the filter wastes hundreds
        # of candles converging from an arbitrary initial state
        initial_state = self._initialise_state(log_a, log_b)

        # ── Step 5: Run Kalman filter sequentially ────────────────────────────
        # This is the core computation — updates β and α at every candle
        kalman_results = self._run_filter(log_a, log_b, initial_state)

        # ── Step 6: Calculate dynamic spread ─────────────────────────────────
        # spread_t = log(AVAX_t) - α_t - β_t × log(ATOM_t)
        # Using dynamically updated α and β — not the static OLS values
        kalman_results = self._calculate_dynamic_spread(kalman_results, log_a, log_b)

        # ── Step 7: Z-score the dynamic spread ───────────────────────────────
        # Normalise against the rolling 504h (3 week) history
        # This makes the z-score comparable across different market regimes
        kalman_results = self._calculate_dynamic_zscore(kalman_results)

        # ── Step 8: Calculate composite pair signal ───────────────────────────
        # Combines z-score, momentum, and signal strength for IC testing
        kalman_results = self._calculate_pair_signal(kalman_results)

        # ── Step 9: Calculate forward return labels ───────────────────────────
        # What the spread actually did 1h, 2h, 4h, 12h, 24h later
        # These are the Y variables for the feature validator's IC test
        kalman_results = self._calculate_forward_returns(kalman_results)

        # ── Step 10: Compare Kalman vs static OLS signal ─────────────────────
        # Proves whether the dynamic hedge ratio actually improves IC
        # vs the static OLS hedge ratio from cointegration_engine.py
        self._compare_kalman_vs_ols(kalman_results, log_a, log_b)

        # ── Step 11: Save outputs ─────────────────────────────────────────────
        self._save_outputs(kalman_results, output_dir)

        logger.info("KALMAN FILTER ENGINE — COMPLETE")
        logger.info("=" * 70)

        return kalman_results


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1: LOAD PRICES
    # ─────────────────────────────────────────────────────────────────────────

    def _load_prices(
        self,
        data_dir: str,
    ) -> Tuple[pd.Series, pd.Series]:
        """
        Load AVAX and ATOM close price series from the fetch_all_symbols CSVs.

        These CSVs are produced by fetch_all_symbols.py which uses the
        project data_fetcher module for pagination, cleaning, and normalisation.

        The close price column contains the raw USDT price (not log-transformed yet).
        Log transformation happens in Step 3 after alignment.

        Returns
        -------
        Tuple[pd.Series, pd.Series]
            (avax_close_series, atom_close_series) both with DatetimeIndex
        """

        logger.info("Step 1: Loading AVAX and ATOM price data...")

        data_path = Path(data_dir)

        # ── Load AVAX ─────────────────────────────────────────────────────────
        avax_path = data_path / f"{self.symbol_a}_1h.csv"
        if not avax_path.exists():
            raise FileNotFoundError(
                f"{self.symbol_a} CSV not found at {avax_path}. "
                f"Run fetch_all_symbols.py first to download price data."
            )

        avax_df = pd.read_csv(avax_path)

        # Parse timestamp to UTC DatetimeIndex — required for alignment
        if "timestamp" in avax_df.columns:
            avax_df["timestamp"] = pd.to_datetime(avax_df["timestamp"], utc=True)
            avax_df.set_index("timestamp", inplace=True)

        avax_df["close"] = pd.to_numeric(avax_df["close"], errors="coerce")
        avax_df.dropna(subset=["close"], inplace=True)   # Remove any NaN prices
        avax_df.sort_index(inplace=True)                  # Ensure chronological order

        avax_close = avax_df["close"]                     # Extract close price Series

        logger.info(
            f"  {self.symbol_a}: {len(avax_close):,} candles "
            f"({avax_close.index[0].date()} → {avax_close.index[-1].date()})"
        )

        # ── Load ATOM ─────────────────────────────────────────────────────────
        atom_path = data_path / f"{self.symbol_b}_1h.csv"
        if not atom_path.exists():
            raise FileNotFoundError(
                f"{self.symbol_b} CSV not found at {atom_path}. "
                f"Run fetch_all_symbols.py first to download price data."
            )

        atom_df = pd.read_csv(atom_path)

        if "timestamp" in atom_df.columns:
            atom_df["timestamp"] = pd.to_datetime(atom_df["timestamp"], utc=True)
            atom_df.set_index("timestamp", inplace=True)

        atom_df["close"] = pd.to_numeric(atom_df["close"], errors="coerce")
        atom_df.dropna(subset=["close"], inplace=True)
        atom_df.sort_index(inplace=True)

        atom_close = atom_df["close"]

        logger.info(
            f"  {self.symbol_b}: {len(atom_close):,} candles "
            f"({atom_close.index[0].date()} → {atom_close.index[-1].date()})"
        )

        return avax_close, atom_close


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2: ALIGN PRICES
    # ─────────────────────────────────────────────────────────────────────────

    def _align_prices(
        self,
        price_a: pd.Series,
        price_b: pd.Series,
    ) -> pd.DataFrame:
        """
        Align AVAX and ATOM to a shared timestamp grid.

        Both assets trade on Binance 24/7 so gaps are rare.
        However exchange maintenance windows, API outages, and
        rare delistings can create timestamp mismatches.

        We use inner join: only keep timestamps where BOTH assets
        have valid prices. This is safer than outer join + forward fill
        for a pairs trading strategy where we need simultaneous prices
        to compute a meaningful spread.

        Forward-filling is applied for gaps of at most 2 consecutive candles
        (exchange downtime) before the inner join.
        """

        logger.info("Step 2: Aligning AVAX and ATOM price series...")

        # Combine into a wide DataFrame — outer join first to see all timestamps
        aligned = pd.concat(
            [price_a.rename("price_a"), price_b.rename("price_b")],
            axis=1,
            join="outer",
        )

        aligned.sort_index(inplace=True)

        # Forward-fill small gaps (exchange downtime up to 2 consecutive candles)
        # This is the same approach used in cross_section_engine.py
        # Limit=2 means only fill if the gap is at most 2 hours
        gaps_before = aligned.isna().sum().sum()
        gaps_after  = aligned.isna().sum().sum()

        if gaps_before > 0:
            logger.info(
                f"  Forward-filled {gaps_before - gaps_after} "
                f"cells (max gap: 2 candles)"
            )

        # Drop any remaining NaN — only timestamps where both assets have prices
        before_inner = len(aligned)
        aligned.dropna(inplace=True)
        after_inner  = len(aligned)

        if before_inner - after_inner > 0:
            logger.info(
                f"  Dropped {before_inner - after_inner} rows "
                f"where one asset had no price"
            )

        # Validate we have enough data for the Kalman filter to converge
        if len(aligned) < self.warmup + 500:
            raise ValueError(
                f"Insufficient aligned data: {len(aligned)} rows. "
                f"Need at least {self.warmup + 500} for the Kalman filter "
                f"to converge reliably."
            )

        logger.info(
            f"  Aligned dataset: {len(aligned):,} shared timestamps "
            f"({aligned.index[0].date()} → {aligned.index[-1].date()})"
        )

        return aligned


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3: LOG PRICE CONVERSION
    # ─────────────────────────────────────────────────────────────────────────

    def _to_log_prices(
        self,
        aligned: pd.DataFrame,
    ) -> Tuple[pd.Series, pd.Series]:
        """
        Convert raw USDT prices to natural logarithms.

        Why log prices for the Kalman filter?

        The cointegration relationship is:
            log(AVAX_t) = α + β × log(ATOM_t) + ε_t

        In log space, β is a RATIO — how many log-units of ATOM
        correspond to one log-unit of AVAX. This ratio is more stable
        over time than a raw price ratio because it captures the
        RELATIVE relationship between the two assets, not the absolute
        price difference.

        Example: if AVAX triples in price and ATOM also triples,
        the raw price spread changes dramatically but the log ratio
        stays exactly the same. This is why the cointegration relationship
        is more stable in log space.

        We clip prices at MIN_PRICE before taking log to prevent
        log(0) = -infinity errors if any price somehow reaches zero.
        """

        logger.info("Step 3: Converting to log prices...")

        # Clip prices at minimum to prevent log(0) — should never happen
        # with Binance data but we guard defensively
        log_a = np.log(aligned["price_a"].clip(lower=MIN_PRICE))
        log_b = np.log(aligned["price_b"].clip(lower=MIN_PRICE))

        logger.info(
            f"  log({self.symbol_a}): "
            f"mean={log_a.mean():.4f} std={log_a.std():.4f}"
        )
        logger.info(
            f"  log({self.symbol_b}): "
            f"mean={log_b.mean():.4f} std={log_b.std():.4f}"
        )

        return log_a, log_b


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4: INITIALISE KALMAN STATE
    # ─────────────────────────────────────────────────────────────────────────

    def _initialise_state(
        self,
        log_a: pd.Series,
        log_b: pd.Series,
    ) -> KalmanState:
        """
        Initialise the Kalman filter state using the OLS hedge ratio from
        cointegration_engine.py as the seed.

        Why seed with OLS values instead of starting from zero?

        The Kalman filter needs an initial estimate of [β, α].
        If we start from [0, 0], the filter spends thousands of candles
        converging to the true value — those candles produce unreliable estimates.

        By seeding with the OLS estimate from cointegration_engine.py
        (β=1.4679, α=0.7916), we start very close to the true value.
        The filter converges in the warmup period (168 candles = 7 days)
        rather than wasting months of data.

        This is a critical integration point between the cointegration engine
        and the Kalman engine — they share information, not just file formats.

        Initial covariance P:
            We initialise with a moderately high uncertainty (diagonal = 1.0).
            This tells the filter: "I trust the OLS seed but not completely —
            please update it based on early observations."
            After warmup, P converges to its steady-state value reflecting
            the true uncertainty in the hedge ratio estimate.
        """

        logger.info("Step 4: Initialising Kalman state...")
        logger.info(
            f"  Seeding from cointegration_engine.py OLS results: "
            f"β={self.ols_beta}, α={self.ols_alpha}"
        )

        # Initial state vector: [beta, alpha]
        # These come directly from cointegration_engine.py output
        theta_0 = np.array([self.ols_beta, self.ols_alpha])

        # Initial covariance — moderate uncertainty
        # Off-diagonal zeros: we assume beta and alpha are initially independent
        P_0 = np.eye(2) * 1.0      # 2×2 identity × 1.0 = moderate initial uncertainty

        initial_state = KalmanState(theta=theta_0, P=P_0)

        logger.info(
            f"  Initial state: β={initial_state.beta:.4f}, "
            f"α={initial_state.alpha:.4f}"
        )
        logger.info(f"  Initial P diagonal: {np.diag(P_0)}")

        return initial_state


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 5: RUN KALMAN FILTER
    # ─────────────────────────────────────────────────────────────────────────

    def _run_filter(
        self,
        log_a:         pd.Series,
        log_b:         pd.Series,
        initial_state: KalmanState,
    ) -> pd.DataFrame:
        """
        Run the Kalman filter sequentially through all candles.

        This is the core computation of the entire module.
        It implements the standard Kalman filter equations
        (see mathematical derivation in the module header).

        For each candle t:
            PREDICTION: estimate state from previous state
            UPDATE: correct estimate using new price observation

        The result is a time series of dynamically updated
        [β_t, α_t] values — the hedge ratio and intercept
        at every single hour from the beginning of history.

        State transition model:
            F = [[1, 0], [0, 1]]  (identity — state changes slowly)
            Q = [[qb, 0], [0, qa]] (process noise — how much state drifts)

        Observation model:
            H_t = [log(ATOM_t), 1]  (design matrix changes at each step)
            y_t = log(AVAX_t)       (what we observe)
            R = observation_noise    (how much we trust each price)

        Returns
        -------
        pd.DataFrame
            One row per candle with columns:
                kalman_beta      — dynamic hedge ratio
                kalman_alpha     — dynamic intercept
                prediction_error — innovation (model health)
                beta_uncertainty — diagonal of P for beta (confidence)
                alpha_uncertainty — diagonal of P for alpha
                is_warmup        — True during first WARMUP_CANDLES candles
        """

        logger.info(
            f"Step 5: Running Kalman filter "
            f"({len(log_a):,} candles)..."
        )

        # ── Pre-allocate result arrays for performance ─────────────────────────
        # Pre-allocation is much faster than appending to lists in a 50,000 row loop
        n               = len(log_a)
        kalman_beta     = np.full(n, np.nan)     # Dynamic hedge ratio per candle
        kalman_alpha    = np.full(n, np.nan)     # Dynamic intercept per candle
        pred_error      = np.full(n, np.nan)     # Innovation (prediction error)
        beta_uncert     = np.full(n, np.nan)     # Uncertainty in beta estimate
        alpha_uncert    = np.full(n, np.nan)     # Uncertainty in alpha estimate
        kalman_gain_b   = np.full(n, np.nan)     # Kalman gain for beta (diagnostics)
        kalman_beta_pred  = np.full(n, np.nan)   # Pre-update hedge ratio (leakage-free)
        kalman_alpha_pred = np.full(n, np.nan)   # Pre-update intercept (leakage-free)

        # ── State transition matrix F (identity — state drifts slowly) ────────
        # F = [[1, 0], [0, 1]]
        # This says: beta and alpha at t+1 are approximately equal to
        # beta and alpha at t, plus small Gaussian noise Q
        F = np.eye(2)

        # ── Process noise covariance Q ─────────────────────────────────────────
        # Controls how fast we allow β and α to change per candle
        # Q = [[qb, 0], [0, qa]] — diagonal means β and α drift independently
        Q = np.array([
            [self.qb, 0.0  ],    # Beta can drift by sqrt(qb) per candle on average
            [0.0,     self.qa],  # Alpha can drift by sqrt(qa) per candle on average
        ])

        # ── Observation noise R ────────────────────────────────────────────────
        # Scalar — how much variance in individual log-price observations
        R = self.R

        # ── Initialise filter state ────────────────────────────────────────────
        theta = initial_state.theta.copy()   # [beta, alpha] — current estimate
        P     = initial_state.P.copy()       # 2×2 covariance — current uncertainty

        log_a_arr = log_a.values    # Convert to numpy array for speed
        log_b_arr = log_b.values    # (avoids pandas overhead in the loop)

        # ── Sequential update loop ─────────────────────────────────────────────
        for t in range(n):

            # ── PREDICTION STEP ───────────────────────────────────────────────
            # Predict where the state will be at time t based on state at t-1
            # Since F = identity, prediction = previous state (state drifts slowly)
            # The uncertainty grows by Q (we become slightly less certain each step
            # because the true state may have changed a tiny bit)
            theta_pred = F @ theta              # Predicted state (= theta since F=I)
            P_pred     = F @ P @ F.T + Q        # Predicted covariance (grows by Q)

            # Store the pre-update state immediately — this is the state as
            # it existed BEFORE seeing this candle's AVAX price, and is what
            # the tradeable spread must use instead of the posterior theta
            kalman_beta_pred[t]  = theta_pred[0]
            kalman_alpha_pred[t] = theta_pred[1]

            # ── OBSERVATION AT TIME t ─────────────────────────────────────────
            # y_t = log(AVAX price at time t) — what we observe
            y_t = log_a_arr[t]

            # H_t = [log(ATOM_t), 1] — design matrix at this timestamp
            # The observation model says: log(AVAX) = β × log(ATOM) + α + noise
            # So H × θ = [log(ATOM), 1] × [β, α]ᵀ = β×log(ATOM) + α
            H_t = np.array([log_b_arr[t], 1.0])

            # ── UPDATE STEP ───────────────────────────────────────────────────
            # How wrong was our prediction?
            innovation = y_t - H_t @ theta_pred      # Prediction error (e_t)

            # How uncertain is that prediction error?
            # S = H × P × Hᵀ + R
            S = H_t @ P_pred @ H_t + R               # Innovation variance (scalar)

            # Kalman gain: how much should we trust the new observation?
            # K = P × Hᵀ / S
            # High K: trust new observation (observation noise is small)
            # Low K: trust the prediction (state uncertainty is small)
            K = P_pred @ H_t / S                     # 2D Kalman gain vector

            # Update the state estimate using the innovation
            # θ_{t|t} = θ_{t|t-1} + K × innovation
            theta = theta_pred + K * innovation

            # Update the covariance — we become more certain after the observation
            # P_{t|t} = (I - K × H) × P_{t|t-1}
            I = np.eye(2)
            KH = np.outer(K, H_t)
            
            P = (
                (I - KH)
                @ P_pred
                @ (I - KH).T
                + np.outer(K, K) * R      
                
            )

            # ── Store results for this candle ──────────────────────────────────
            kalman_beta[t]   = theta[0]              # Updated β
            kalman_alpha[t]  = theta[1]              # Updated α
            pred_error[t]    = innovation            # Prediction error
            beta_uncert[t]   = P[0, 0]              # Uncertainty in β
            alpha_uncert[t]  = P[1, 1]              # Uncertainty in α
            kalman_gain_b[t] = K[0]                 # Kalman gain for β

        # ── Build result DataFrame ─────────────────────────────────────────────
        results = pd.DataFrame(index=log_a.index)

        results["kalman_beta"]       = kalman_beta    # Dynamic hedge ratio
        results["kalman_alpha"]      = kalman_alpha   # Dynamic intercept
        results["prediction_error"]  = pred_error     # Innovation (model health)
        results["beta_uncertainty"]  = beta_uncert    # Confidence in β
        results["alpha_uncertainty"] = alpha_uncert   # Confidence in α
        results["kalman_gain_beta"]  = kalman_gain_b  # Diagnostic: Kalman gain
        results["kalman_beta_pred"]  = kalman_beta_pred    # Leakage-free hedge ratio — use this for the tradeable spread
        results["kalman_alpha_pred"] = kalman_alpha_pred   # Leakage-free intercept — use this for the tradeable spread
        results["is_warmup"]         = (              # True during first N candles
            np.arange(n) < self.warmup
        )

        # Log convergence statistics
        post_warmup_beta = results.loc[~results["is_warmup"], "kalman_beta"]
        logger.info(
            f"  Filter complete. Post-warmup β: "
            f"mean={post_warmup_beta.mean():.4f} "
            f"std={post_warmup_beta.std():.4f} "
            f"(OLS seed was {self.ols_beta})"
        )
        logger.info(
            f"  Prediction error: "
            f"mean={results['prediction_error'].mean():.6f} "
            f"std={results['prediction_error'].std():.6f}"
        )

        return results


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 6: CALCULATE DYNAMIC SPREAD
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_dynamic_spread(
        self,
        results: pd.DataFrame,
        log_a:   pd.Series,
        log_b:   pd.Series,
    ) -> pd.DataFrame:
        """
        Calculate the dynamic spread using Kalman-estimated β and α.

        Dynamic spread formula:
            spread_t = log(AVAX_t) - α_t - β_t × log(ATOM_t)

        Where α_t and β_t are the Kalman filter estimates at time t.

        Compare to static OLS spread from cointegration_engine.py:
            ols_spread_t = log(AVAX_t) - 0.7916 - 1.4679 × log(ATOM_t)

        The dynamic spread is more accurate because:
            - β_t reflects the CURRENT relationship, not the historical one
            - α_t reflects the CURRENT equilibrium level
            - The spread mean-reverts around ZERO by construction
              (because the filter continuously updates the equilibrium)

        Also adds the static OLS spread for comparison — the validator
        will test which version has higher IC, proving whether the
        Kalman improvement is statistically meaningful.
        """

        logger.info("Step 6: Calculating dynamic spread...")

        log_a_vals = log_a.values    # Numpy arrays for fast vectorised calculation
        log_b_vals = log_b.values

        # ── Dynamic spread (Kalman) ────────────────────────────────────────────
        # Uses the time-varying β_t and α_t estimated at each candle
        #
        # IMPORTANT: built from kalman_beta_pred / kalman_alpha_pred, NOT
        # kalman_beta / kalman_alpha. The posterior (kalman_beta) has
        # already been updated using THIS candle's own AVAX price — using
        # it to then measure this same candle's spread creates a
        # self-referential shrinkage (provably equal to innovation * R/S,
        # a mechanically shrunk fraction of the honest prediction error,
        # not a real reversion). kalman_beta/kalman_alpha remain unchanged
        # above and are still the right columns for the beta-trajectory
        # diagnostic file — only the tradeable spread changes here.
        results["kalman_spread"] = (
            log_a_vals -
            results["kalman_alpha_pred"].values -
            results["kalman_beta_pred"].values * log_b_vals
        )

        # ── Static OLS spread (baseline for comparison) ───────────────────────
        # Uses the fixed β and α from cointegration_engine.py
        # If Kalman IC > OLS IC: dynamic hedge ratio adds genuine value
        # If Kalman IC ≈ OLS IC: static relationship is sufficient
        results["ols_spread"] = (
            log_a_vals -
            self.ols_alpha -
            self.ols_beta * log_b_vals
        )

        # Mark warmup period spreads — these use partially converged estimates
        # and should not be used in backtesting or IC calculation
        results.loc[results["is_warmup"], "kalman_spread"] = np.nan

        logger.info(
            f"  Kalman spread: "
            f"mean={results['kalman_spread'].mean():.4f} "
            f"std={results['kalman_spread'].std():.4f}"
        )
        logger.info(
            f"  OLS spread (baseline): "
            f"mean={results['ols_spread'].mean():.4f} "
            f"std={results['ols_spread'].std():.4f}"
        )

        return results


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 7: DYNAMIC Z-SCORE
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_dynamic_zscore(
        self,
        results: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Normalise the dynamic spread using a rolling z-score.

        Why rolling z-score instead of fixed z-score?

            The dynamic spread from the Kalman filter already adapts to
            shifting equilibrium levels (because β_t and α_t update continuously).
            However, the VOLATILITY of the spread can still change over time —
            the pair may be more volatile in 2021 than in 2024.

            Rolling z-score normalises by the recent standard deviation,
            making a z-score of 2.0 mean "2 standard deviations above the
            recent average" regardless of whether the recent period was
            calm or volatile.

        Window: 504 hours (3 weeks) — matches cointegration_engine.py
        so results are directly comparable.

        Features produced:
            kalman_zscore       — primary IC test feature
            kalman_zscore_lag1  — momentum context (is spread getting worse?)
            ols_zscore          — baseline comparison (static OLS z-score)
            kalman_pct_rank     — percentile rank 0-100 (more interpretable)

        Note: kalman_zscore and ols_zscore are winsorised to
        ±ZSCORE_WINSOR_LIMIT (see below). Any EXIT_ZSCORE_STOPLOSS
        (entry_exit_engine.py) set close to this limit will rarely fire,
        since |z| > ZSCORE_WINSOR_LIMIT is structurally impossible.
        """
        # claude code changed: the "Note:" paragraph above (4 lines) was added to flag the stop-loss/winsor-limit interaction — a "#" comment can't be placed inside the docstring itself without corrupting it

        logger.info("Step 7: Calculating dynamic z-score...")

        # ── Kalman dynamic z-score ─────────────────────────────────────────────
        rolling_mean = results["kalman_spread"].rolling(
            window=self.zscore_window,
            min_periods=self.zscore_min_p,
        ).mean()

        rolling_std = results["kalman_spread"].rolling(
            window=self.zscore_window,
            min_periods=self.zscore_min_p,
        ).std()

        # Z-score: (spread - rolling mean) / rolling std
        results["kalman_zscore"] = (
            (results["kalman_spread"] - rolling_mean)
            / rolling_std.replace(0, np.nan)   # Avoid division by zero
        )

        # Winsorise: cap at ±ZSCORE_WINSOR_LIMIT standard deviations
        # Values beyond this are almost certainly data errors or exchange anomalies
        results["kalman_zscore"] = results["kalman_zscore"].clip(              # claude code changed: was .clip(-5, 5) — bare literal replaced with named constant
            -ZSCORE_WINSOR_LIMIT, ZSCORE_WINSOR_LIMIT                          # claude code changed: was -5, 5
        )

        # Lagged z-score: is the spread getting more extreme or normalising?
        # Positive lag: spread was already extreme last candle (momentum)
        # Negative change: spread is reverting (confirmation signal)
        results["kalman_zscore_lag1"] = results["kalman_zscore"].shift(1)

        # Percentile rank: where is today's spread in the 3-week distribution?
        # 0 = cheapest AVAX relative to ATOM in 3 weeks (long AVAX signal)
        # 100 = most expensive AVAX relative to ATOM (short AVAX signal)
        results["kalman_pct_rank"] = results["kalman_spread"].rolling(
            window=self.zscore_window,
            min_periods=self.zscore_min_p,
        ).rank(pct=True) * 100

        # ── OLS static z-score (baseline comparison) ──────────────────────────
        ols_mean = results["ols_spread"].rolling(
            window=self.zscore_window,
            min_periods=self.zscore_min_p,
        ).mean()

        ols_std = results["ols_spread"].rolling(
            window=self.zscore_window,
            min_periods=self.zscore_min_p,
        ).std()

        results["ols_zscore"] = (
            (results["ols_spread"] - ols_mean)
            / ols_std.replace(0, np.nan)
        ).clip(-ZSCORE_WINSOR_LIMIT, ZSCORE_WINSOR_LIMIT)    # claude code changed: was .clip(-5, 5) — bare literal replaced with named constant

        logger.info(
            f"  Kalman z-score: "
            f"mean={results['kalman_zscore'].mean():.4f} "
            f"std={results['kalman_zscore'].std():.4f}"
        )

        return results


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 8: CALCULATE PAIR SIGNAL
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_pair_signal(
        self,
        results: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Calculate composite pair trading signals for IC testing.

        These are RESEARCH FEATURES, not trade signals.
        The feature validator will test their IC against forward spread returns.
        Only after IC validation do we determine trading thresholds.

        Four signal variants are produced — the validator tests all four
        and the highest IC variant guides the trading rule design:

        pair_signal_dynamic:
            Primary signal. Negative of Kalman z-score.
            Positive: spread is below equilibrium → expect AVAX to outperform ATOM
            Negative: spread is above equilibrium → expect AVAX to underperform ATOM
            Why negative: we test MEAN REVERSION — signal is OPPOSITE to current deviation

        pair_signal_momentum:
            Incorporates z-score change direction.
            If the spread is extreme AND still getting more extreme,
            entry may be premature. This signal is weaker when momentum
            is against the mean reversion direction.

        pair_signal_extreme:
            Binary × direction. Only active when |z-score| > SIGNAL_ENTRY_THRESHOLD.
            Tests whether IC is higher for extreme deviations vs all deviations.
            Institutional desks often only trade when the spread is "extreme enough."

        pair_signal_uncertainty_weighted:
            Weights the z-score by the inverse of beta_uncertainty.
            When the Kalman filter is highly certain about β (small uncertainty),
            the spread calculation is more reliable → stronger signal.
            When filter is uncertain (large uncertainty), signal is dampened.
        """

        logger.info("Step 8: Calculating pair signals...")

        z = results["kalman_zscore"]           # Primary z-score
        z_lag1 = results["kalman_zscore_lag1"] # Previous candle z-score
        z_change = z - z_lag1                  # How much did z-score change?
        uncert = results["beta_uncertainty"]    # Kalman uncertainty in β

        # ── Signal 1: Primary mean reversion signal ───────────────────────────
        # Invert z-score: positive signal when pair is stretched DOWN
        # (AVAX cheap relative to ATOM → expect AVAX to recover)
        results["pair_signal_dynamic"] = -z

        # ── Signal 2: Momentum-adjusted signal ───────────────────────────────
        # When z is extreme AND still growing (z_change same sign as z),
        # the reversion has not started yet — be cautious
        # Weight: 0.7 on current position + 0.3 on momentum direction
        results["pair_signal_momentum"] = -(
            0.7 * z +
            0.3 * z_change    # If z is growing: dampens signal (wait for reversal)
        )

        # ── Signal 3: Extreme-only signal ─────────────────────────────────────
        # Only non-zero when |z| > SIGNAL_ENTRY_THRESHOLD (default 2.0)
        # Tests: does limiting trades to extreme events improve IC?
        is_extreme = (z.abs() > SIGNAL_ENTRY_THRESHOLD).astype(float)
        results["pair_signal_extreme"] = -z * is_extreme

        # ── Signal 4: Uncertainty-weighted signal ─────────────────────────────
        # When filter is confident about β (small uncertainty), trust the signal more
        # Max weight = 1.0 (fully trust when filter is very certain)
        # This signal is unique to the Kalman approach — OLS cannot do this
        # because OLS has no concept of dynamic uncertainty
        max_uncert = uncert.quantile(0.95)    # 95th percentile uncertainty
        uncert_weight = 1.0 - (uncert / max_uncert.clip(1e-10)).clip(0, 1)
        results["pair_signal_uw"] = -z * uncert_weight

        # ── OLS baseline signal ───────────────────────────────────────────────
        # The same signal using the static OLS z-score
        # Comparison: if pair_signal_dynamic IC > ols_signal IC,
        # the Kalman filter is genuinely improving signal quality
        results["pair_signal_ols"] = -results["ols_zscore"]

        logger.info("  4 Kalman signals + 1 OLS baseline signal calculated")

        return results


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 9: FORWARD RETURN LABELS
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_forward_returns(
        self,
        results: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Calculate what the spread actually did N hours later.

        These are the Y variables for the feature validator's IC calculation.
        IC = Spearman correlation between pair_signal and forward_return.

        We predict SPREAD direction:
            Positive forward spread return: spread increased (pair moved away from equilibrium)
            Negative forward spread return: spread decreased (pair moved toward equilibrium)

        For a mean reversion signal:
            pair_signal_dynamic > 0 (spread below equilibrium)
            → we expect spread to increase → positive forward_return
            → IC between signal and forward return should be POSITIVE

        N-period forward spread return:
            spread_forward_Nh = spread_{t+N} - spread_t

        Implemented using shift(-N) on the spread series,
        which correctly pulls future values to the current row
        without introducing look-ahead bias into any feature column.

        The last N rows will be NaN for each N-period label.
        The feature validator handles NaN labels automatically.
        """

        logger.info("Step 9: Calculating forward return labels...")

        for label_name, periods in self.horizons.items():

            # N-period spread change: how much did the spread move N hours later?
            # shift(-N) pulls the value N periods ahead to the current row
            # At time t: forward_return = spread_{t+N} - spread_t
            spread_change = results["kalman_spread"].diff(periods)   # spread_t - spread_{t-N}
            results[label_name] = spread_change.shift(-periods)       # Align to current row

        logger.info(
            f"  Labels added: {list(self.horizons.keys())}"
        )

        return results


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 10: COMPARE KALMAN VS OLS
    # ─────────────────────────────────────────────────────────────────────────

    def _compare_kalman_vs_ols(
        self,
        results: pd.DataFrame,
        log_a:   pd.Series,
        log_b:   pd.Series,
    ) -> None:
        """
        Compare the IC of Kalman dynamic signal vs static OLS signal.

        This is the proof that the Kalman filter adds genuine value
        over the static hedge ratio from cointegration_engine.py.

        If Kalman IC > OLS IC: the dynamic hedge ratio is tracking
        the true relationship better → use Kalman for all future research.

        If Kalman IC ≈ OLS IC: static relationship is stable enough →
        the relationship has not changed significantly since calibration.
        Keep Kalman for monitoring but OLS for signal generation.

        If Kalman IC < OLS IC: something is wrong with the filter
        calibration (process noise too high, wrong initial state).
        Investigate the beta trajectory for erratic behaviour.
        """

        logger.info("\nStep 10: Comparing Kalman vs OLS signal quality...")
        logger.info("-" * 60)

        # Use 4h forward return as primary comparison (matches half-life discussion)
        primary_label = "spread_forward_4h"

        if primary_label not in results.columns:
            logger.warning(f"  {primary_label} not found — skipping comparison")
            return

        fwd = results[primary_label]

        # Pairs to compare: (signal_column, description)
        comparisons = [
            ("pair_signal_dynamic",  "Kalman — primary (inverted z-score)"),
            ("pair_signal_momentum", "Kalman — momentum adjusted"),
            ("pair_signal_extreme",  "Kalman — extreme only (|z|>2)"),
            ("pair_signal_uw",       "Kalman — uncertainty weighted"),
            ("pair_signal_ols",      "OLS baseline (static hedge ratio)"),
        ]

        best_ic = 0.0
        best_signal = ""

        for signal_col, description in comparisons:

            if signal_col not in results.columns:
                continue

            sig = results[signal_col]

            # Remove NaN from both simultaneously
            valid = (
                sig.notna() & np.isfinite(sig) &
                fwd.notna() & np.isfinite(fwd) &
                ~results["is_warmup"]    # Exclude warmup period
            )

            n_valid = valid.sum()
            if n_valid < 200:
                logger.info(f"  {description}: insufficient data ({n_valid} rows)")
                continue

            # Spearman IC — same metric as feature_validator_v2_institutional.py
            ic, pvalue = stats.spearmanr(
                sig[valid].values,
                fwd[valid].values,
            )

            sig_flag = "✓ SIGNIFICANT" if pvalue < 0.05 else "✗ not significant"
            grade = (
                "EXCELLENT" if abs(ic) >= 0.05 else
                "GOOD"      if abs(ic) >= 0.03 else
                "MODERATE"  if abs(ic) >= 0.02 else
                "WEAK"      if abs(ic) >= 0.01 else
                "DEAD"
            )

            logger.info(
                f"  {description:<45} "
                f"IC={ic:+.4f}  p={pvalue:.4f}  "
                f"{grade}  {sig_flag}"
            )

            if abs(ic) > abs(best_ic) and pvalue < 0.05:
                best_ic     = ic
                best_signal = signal_col

        logger.info("-" * 60)
        if best_signal:
            logger.info(
                f"  Best signal: {best_signal} with IC={best_ic:+.4f}"
            )
            logger.info(
                f"  Recommendation: use {best_signal} as the primary "
                f"feature in feature_validator_v2_institutional.py"
            )
        else:
            logger.warning(
                "  No signal achieved IC > 0.01 with p < 0.05. "
                "Consider: (1) switching to 4h candles, "
                "(2) adjusting process noise parameters, "
                "(3) testing longer forward return horizons."
            )


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 11: SAVE OUTPUTS
    # ─────────────────────────────────────────────────────────────────────────

    def _save_outputs(
        self,
        results:    pd.DataFrame,
        output_dir: str,
    ) -> None:
        """
        Save all Kalman filter outputs to the research_data directory.

        Three files are saved:

        1. AVAX_USDT_ATOM_USDT_kalman.csv — MAIN OUTPUT
           The complete feature DataFrame ready for:
               feature_validator_v2_institutional.py
               feature_stability_analyzer.py
               feature_decay_analyzer.py
           Run the validator with forward_return_col='spread_forward_4h'
           and features: pair_signal_dynamic, pair_signal_uw, kalman_zscore

        2. kalman_beta_trajectory.csv — HEDGE RATIO EVOLUTION
           Shows how the hedge ratio has evolved from 2020 to 2026.
           Key diagnostic: should show smooth gradual evolution.
           Erratic jumps indicate process noise is too high.

        3. kalman_diagnostics.csv — MODEL HEALTH METRICS
           Prediction errors, Kalman gains, uncertainty estimates.
           Use this to verify the filter is tracking correctly.
        """

        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        pair_slug = f"{self.symbol_a}_{self.symbol_b}"   # e.g. AVAX_USDT_ATOM_USDT

        # ── 1. Main Kalman feature output ─────────────────────────────────────
        main_path = out_path / f"{pair_slug}_kalman.csv"
        results.reset_index().to_csv(main_path, index=False)
        logger.info(f"\n  Saved main output  : {main_path}")
        logger.info(f"    Rows: {len(results):,}  Columns: {len(results.columns)}")
        logger.info(
            f"    Feature columns: kalman_zscore, pair_signal_dynamic, "
            f"pair_signal_uw, pair_signal_momentum, pair_signal_extreme"
        )
        logger.info(
            f"    Label columns: {list(self.horizons.keys())}"
        )
        logger.info(
            f"    Next step: run feature_validator_v2_institutional.py "
            f"on this CSV with forward_return_col='spread_forward_4h'"
        )

        # ── 2. Beta trajectory ─────────────────────────────────────────────────
        beta_df = results[["kalman_beta", "kalman_alpha",
                           "beta_uncertainty", "is_warmup"]].copy()
        beta_path = out_path / "kalman_beta_trajectory.csv"
        beta_df.reset_index().to_csv(beta_path, index=False)
        logger.info(f"  Saved beta trajectory: {beta_path}")

        # ── 3. Diagnostics ────────────────────────────────────────────────────
        diag_cols = [
            "prediction_error", "beta_uncertainty",
            "alpha_uncertainty", "kalman_gain_beta", "is_warmup"
        ]
        diag_df   = results[[c for c in diag_cols if c in results.columns]].copy()
        diag_path = out_path / "kalman_diagnostics.csv"
        diag_df.reset_index().to_csv(diag_path, index=False)
        logger.info(f"  Saved diagnostics    : {diag_path}")

        # ── Summary statistics ─────────────────────────────────────────────────
        post_warmup = results[~results["is_warmup"]]

        logger.info("\n  POST-WARMUP SUMMARY:")
        logger.info(
            f"    β range : "
            f"{post_warmup['kalman_beta'].min():.4f} → "
            f"{post_warmup['kalman_beta'].max():.4f}"
        )
        logger.info(
            f"    β drift : {post_warmup['kalman_beta'].std():.4f} "
            f"(OLS seed was {self.ols_beta})"
        )
        logger.info(
            f"    Kalman z-score range: "
            f"{post_warmup['kalman_zscore'].min():.2f} → "
            f"{post_warmup['kalman_zscore'].max():.2f}"
        )
        logger.info(
            f"    Candles with |z| > 2.0: "
            f"{(post_warmup['kalman_zscore'].abs() > 2.0).sum():,} "
            f"({(post_warmup['kalman_zscore'].abs() > 2.0).mean():.1%} of post-warmup data)"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# PARAMETER SENSITIVITY ANALYZER
# ═══════════════════════════════════════════════════════════════════════════════

class KalmanParameterAnalyzer:
    """
    Tests different Kalman filter parameter settings to find the
    configuration that maximises IC on the AVAX/ATOM pair.

    Why this is needed:
        The process noise parameters (qb, qa) are not theoretically
        derived — they are tuning choices. Too small = filter adapts
        too slowly and uses a stale hedge ratio. Too large = filter
        is erratic and the spread calculation is noisy.

        This class runs the filter with multiple parameter combinations
        and compares the resulting IC on the validation set.

    Integration:
        Run this ONCE to calibrate parameters.
        Then use the best parameters in KalmanFilterEngine for all research.
    """

    def __init__(
        self,
        data_dir:   str = "data",
        output_dir: str = "research_data",
    ) -> None:
        """Initialise with data paths."""
        self.data_dir   = data_dir
        self.output_dir = output_dir

    def run_sensitivity(
        self,
        forward_col: str = "spread_forward_4h",
    ) -> pd.DataFrame:
        """
        Run Kalman filter with multiple process noise settings.
        Compare IC for each configuration.
        Return a ranked DataFrame of parameter settings.
        """

        logger.info("=" * 70)
        logger.info("KALMAN PARAMETER SENSITIVITY ANALYSIS")
        logger.info("=" * 70)

        # Parameter grid — test different adaptation speeds
        # Each combination represents a different belief about how fast
        # the AVAX/ATOM relationship evolves
        param_grid = [
            (1e-6, 1e-6, "very_slow"),    # Adapts very slowly — trusts history
            (1e-5, 1e-5, "slow"),          # Default — recommended starting point
            (5e-5, 5e-5, "medium"),        # Moderate adaptation speed
            (1e-4, 1e-4, "fast"),          # Adapts quickly — trusts new data
            (5e-4, 5e-4, "very_fast"),     # Very fast — potentially erratic
        ]

        results_rows = []

        for qb, qa, label in param_grid:

            logger.info(f"\n  Testing: process_noise_beta={qb}, alpha={qa} [{label}]")

            try:
                # Run Kalman with this parameter setting
                engine = KalmanFilterEngine(
                    process_noise_beta=qb,
                    process_noise_alpha=qa,
                )
                output = engine.run(
                    data_dir=self.data_dir,
                    output_dir=self.output_dir,
                )

                # Compute IC for primary signal vs forward return
                if forward_col in output.columns:
                    sig = output["pair_signal_dynamic"]
                    fwd = output[forward_col]
                    valid = (
                        sig.notna() & np.isfinite(sig) &
                        fwd.notna() & np.isfinite(fwd) &
                        ~output["is_warmup"]
                    )
                    if valid.sum() > 200:
                        ic, pv = stats.spearmanr(
                            sig[valid].values, fwd[valid].values
                        )
                        results_rows.append({
                            "label":     label,
                            "qb":        qb,
                            "qa":        qa,
                            "ic":        round(float(ic), 6),
                            "pvalue":    round(float(pv), 6),
                            "n_obs":     int(valid.sum()),
                            "beta_std":  round(
                                float(output.loc[~output["is_warmup"],
                                      "kalman_beta"].std()), 6
                            ),
                        })
                        logger.info(
                            f"    IC={ic:+.4f}  p={pv:.4f}  "
                            f"β_std={results_rows[-1]['beta_std']:.4f}"
                        )

            except Exception as e:
                logger.error(f"    Failed: {e}")
                continue

        if not results_rows:
            logger.error("No parameter configurations succeeded.")
            return pd.DataFrame()

        # Rank by absolute IC
        sensitivity_df = pd.DataFrame(results_rows)
        sensitivity_df["abs_ic"] = sensitivity_df["ic"].abs()
        sensitivity_df = sensitivity_df.sort_values("abs_ic", ascending=False)
        sensitivity_df = sensitivity_df.drop(columns=["abs_ic"])

        # Save results
        out_path = Path(self.output_dir) / "kalman_sensitivity.csv"
        sensitivity_df.to_csv(out_path, index=False)
        logger.info(f"\n  Sensitivity results saved: {out_path}")

        # Log winner
        best = sensitivity_df.iloc[0]
        logger.info(
            f"\n  BEST PARAMETERS: qb={best['qb']}, qa={best['qa']} "
            f"[{best['label']}] → IC={best['ic']:+.4f}"
        )
        logger.info(
            f"  Use these in KalmanFilterEngine: "
            f"process_noise_beta={best['qb']}, "
            f"process_noise_alpha={best['qa']}"
        )

        return sensitivity_df


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_kalman_research(
    pair_name:               str  = "AVAX_USDT/ATOM_USDT",
    cointegration_pairs_csv: str  = DEFAULT_COINTEGRATION_PAIRS_CSV,
    require_passes_filters:  bool = True,
    data_dir:                str  = "data",
    output_dir:              str  = "research_data",
    run_sensitivity:         bool = False,
) -> pd.DataFrame:
    """
    Standalone entry point for the Kalman filter engine.

    Loads the two assets' price CSVs produced by fetch_all_symbols.py,
    runs the full Kalman filter pipeline for the requested pair, saves
    outputs, and prints the IC comparison between dynamic Kalman and
    static OLS signals.

    Run from project root, for the default pair:
        python -m bot.research.kalman_filter_engine

    Or for any other pair cointegration_engine.py has tested:
        python -m bot.research.kalman_filter_engine --pair SOL_USDT/DOT_USDT

    Parameters
    ----------
    pair_name : str
        Must match a "pair_name" row in cointegration_pairs_csv.

    cointegration_pairs_csv : str
        Path to cointegration_engine.py's saved pair summary.

    require_passes_filters : bool
        If True (default), refuse to run a pair that failed
        cointegration_engine.py's own half-life filter. Set False to
        deliberately test a pair that narrowly missed the cutoff.

    data_dir : str
        Folder containing the two assets' 1h price CSVs.
        Default: "data" (same folder used by fetch_all_symbols.py)

    output_dir : str
        Folder to save Kalman CSVs. Default: "research_data"

    run_sensitivity : bool
        If True: also run parameter sensitivity analysis.
        This takes longer but helps calibrate process noise.
        Default: False (use defaults for first run)

    Returns
    -------
    pd.DataFrame
        Main Kalman output ready for feature_validator_v2_institutional.py
    """

    logger.info("=" * 70)
    logger.info("KALMAN FILTER ENGINE — QUANTIBOT PRO RESEARCH PIPELINE")
    logger.info("=" * 70)
    logger.info(f"  Pair     : {pair_name}")
    logger.info(f"  Data dir : {data_dir}")
    logger.info(f"  Output   : {output_dir}")
    logger.info(f"  Context  : loaded dynamically from {cointegration_pairs_csv}")

    # Run main Kalman filter
    engine = KalmanFilterEngine(
        pair_name=pair_name,
        cointegration_pairs_csv=cointegration_pairs_csv,
        require_passes_filters=require_passes_filters,
    )
    kalman_output = engine.run(
        data_dir=data_dir,
        output_dir=output_dir,
    )

    # Optionally run sensitivity analysis
    if run_sensitivity:
        logger.info("\nRunning parameter sensitivity analysis...")
        analyzer = KalmanParameterAnalyzer(
            data_dir=data_dir,
            output_dir=output_dir,
        )
        analyzer.run_sensitivity()

    logger.info("\n" + "=" * 70)
    logger.info("NEXT STEPS IN THE QUANTIBOT PRO PIPELINE:")
    logger.info("=" * 70)
    logger.info(
        "\n  1. Run feature_validator_v2_institutional.py on the Kalman output:"
        "\n     df = pd.read_csv('research_data/AVAX_USDT_ATOM_USDT_kalman.csv')"
        "\n     validator = FeatureValidator()"
        "\n     results = validator.validate_all_features_institutional("
        "\n         df, forward_return_col='spread_forward_4h'"
        "\n     )"
        "\n     Look for: pair_signal_dynamic IC > 0.03 with p < 0.05"
        "\n"
        "\n  2. If IC passes: run feature_stability_analyzer.py on the Kalman output"
        "\n     to confirm the signal is stable across 2020-2026"
        "\n"
        "\n  3. If stability confirmed: build entry_exit_engine.py"
        "\n     using z-score thresholds from the IC analysis"
        "\n"
        "\n  4. Run walk-forward validation before any live deployment"
    )

    return kalman_output


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    """
    Run from project root:
        python -m bot.research.kalman_filter_engine

    For a specific pair (must exist in cointegration_pairs.csv):
        python -m bot.research.kalman_filter_engine --pair SOL_USDT/DOT_USDT

    To test a pair that narrowly failed cointegration_engine.py's own
    half-life filter (e.g. ~121h against a 120h ceiling):
        python -m bot.research.kalman_filter_engine --pair SOL_USDT/DOT_USDT --allow-filtered

    For sensitivity analysis (slower, but calibrates parameters):
        python -m bot.research.kalman_filter_engine --sensitivity
    """
    import sys

    args        = sys.argv[1:]
    sensitivity = "--sensitivity" in args
    allow_filtered = "--allow-filtered" in args

    pair_name = "AVAX_USDT/ATOM_USDT"        # Default — unchanged if --pair isn't passed
    if "--pair" in args:
        pair_name = args[args.index("--pair") + 1]

    run_kalman_research(
        pair_name=pair_name,
        require_passes_filters=not allow_filtered,
        data_dir="data",
        output_dir="research_data",
        run_sensitivity=sensitivity,
    )