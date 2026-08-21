# ═══════════════════════════════════════════════════════════════════════════════
# bot/research/cointegration_engine.py
#
# INSTITUTIONAL COINTEGRATION ENGINE
#
# Version  : 1.0 (Research Grade)
# Author   : Steph Quant Technologies Research Pipeline
# claude code changed: rebrand from QuantBot Pro to Steph Quant Technologies
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT IS COINTEGRATION?
# ─────────────────────────────────────────────────────────────────────────────
# Plain English:
#   Two assets are cointegrated when they share a long-run equilibrium
#   relationship. They can diverge in the short term, but they are
#   statistically bound to return to each other over time.
#
#   Think of two dogs on the same leash walking in a park.
#   Each dog wanders independently moment to moment.
#   But they can never go too far apart — the leash always pulls them back.
#
#   In crypto:
#     ETH and BNB are both smart-contract platforms
#     They compete for the same capital flows
#     When ETH rises and BNB doesn't — the leash tightens
#     Arbitrageurs and capital rotation pull BNB back toward ETH
#
# Why Cointegration Is Different From Correlation:
#   Correlation measures whether two assets move in the SAME DIRECTION.
#   Cointegration measures whether two assets share a LONG-RUN EQUILIBRIUM.
#
#   Two assets can be:
#     High correlation + no cointegration → both trend up but diverge permanently
#     Low correlation + cointegrated      → wander independently but always revert
#
#   For TRADING, cointegration is far more valuable than correlation.
#   It tells you the spread WILL revert — not just that they tend to move together.
#
# ─────────────────────────────────────────────────────────────────────────────
# THE TRADING HYPOTHESIS
# ─────────────────────────────────────────────────────────────────────────────
#
#   If ETH and BNB are cointegrated:
#
#     1. Calculate the "spread" = ETH_price - (hedge_ratio × BNB_price)
#        The hedge ratio tells us how many BNB units offset one ETH unit
#
#     2. Z-score the spread against its historical mean
#        A z-score of +2.5 means the spread is 2.5 std devs above normal
#        This means ETH is unusually EXPENSIVE relative to BNB right now
#
#     3. Mean reversion prediction:
#        Spread is too high → ETH will fall OR BNB will rise (or both)
#        Spread is too low  → ETH will rise OR BNB will fall (or both)
#
#   This is MARKET-NEUTRAL — we don't care which direction crypto goes.
#   We only care about the RELATIVE movement between the two assets.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHAT THIS ENGINE PRODUCES
# ─────────────────────────────────────────────────────────────────────────────
#
# For each cointegrated pair (e.g. ETH/BNB):
#]
#   hedge_ratio          How many units of asset B offset one unit of asset A
#   spread               Price spread: A_price - (hedge_ratio × B_price)
#   spread_mean          Rolling mean of spread (the equilibrium level)
#   spread_std           Rolling std of spread (how much it normally wanders)
#   spread_zscore        (spread - mean) / std — how far from equilibrium?
#   spread_zscore_lag1   Previous candle's z-score (momentum context)
#   spread_pct_rank      Percentile rank of current spread (0-100)
#   half_life            How many hours does this spread take to revert?
#   pair_signal          Composite signal: direction + magnitude + half-life weight
#   forward_return_Nh    What the spread did N hours later (IC test label)
#
# Pair testing results saved to:
#   research_data/cointegration_pairs.csv
# from __future__ import annotations

# Per-pair feature CSVs saved to:
#   research_data/{SYMBOL_A}_{SYMBOL_B}_cointegration.csv
#
# ─────────────────────────────────────────────────────────────────────────────
# STATISTICAL TESTS USED
# ─────────────────────────────────────────────────────────────────────────────
#
# Engle-Granger Two-Step Test:
#   Step 1: Run OLS regression of asset A on asset B to get hedge ratio
#   Step 2: Run ADF (Augmented Dickey-Fuller) test on the residuals
#           If residuals are stationary (ADF p-value < 0.05) → cointegrated
#
# Half-Life (Ornstein-Uhlenbeck):
#   Measures how long the spread takes to revert halfway to its mean
#   Formula: half_life = -log(2) / lambda
#   Where lambda comes from regressing spread changes on spread levels
#   Short half-life → fast reversion → more tradeable
#   Long half-life → slow reversion → position ties up capital too long
#
# ADF Critical Values:
#   p < 0.01 → Strong cointegration (99% confidence)
#   p < 0.05 → Cointegration (95% confidence) — our threshold
#   p > 0.05 → No cointegration — skip this pair
#
# ─────────────────────────────────────────────────────────────────────────────
# LOOK-AHEAD BIAS PREVENTION
# ─────────────────────────────────────────────────────────────────────────────
#
# The hedge ratio is estimated on a TRAINING WINDOW of historical data.
# It is then FIXED and applied to out-of-sample data.
# Rolling re-estimation uses only past data at each timestamp.
# Forward return labels are clearly separated from feature calculations.
#
# ═══════════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations          # Modern type hints

import logging                              # Structured logging
import warnings                             # Suppress statistical warnings
import itertools                            # Generate all pair combinations

from typing import Dict, List, Optional, Tuple   # Type hints

import numpy as np                          # Numerical operations
import pandas as pd                         # DataFrame operations
from scipy import stats                     # OLS regression, Spearman IC

# Statistical tests for cointegration
from statsmodels.tsa.stattools import adfuller, coint   # ADF test, cointegration test
from statsmodels.regression.linear_model import OLS     # OLS for hedge ratio
from statsmodels.tools import add_constant              # Add intercept to OLS

warnings.filterwarnings('ignore')           # Suppress statsmodels convergence warnings


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

# All 7 symbols in the research universe
# Every possible pair combination will be tested for cointegration
# 7 symbols → 21 unique pairs (7 choose 2 = 21)
UNIVERSE: List[str] = [
    'BTC_USDT',
    'ETH_USDT',
    'BNB_USDT',
    'SOL_USDT',
    'ADA_USDT',
    'AVAX_USDT',
    'DOT_USDT',
    'MATIC_USDT',
    'ARB_USDT',
    'LINK_USDT',
    'UNI_USDT',
    'AAVE_USDT',
    'XRP_USDT',
    'XLM_USDT',
    'DOGE_USDT',
    'SHIB_USDT',
    'ATOM_USDT',
    'FIL_USDT',
    'APT_USDT',
    'OP_USDT',
]

# Cointegration test significance threshold
# Pairs with ADF p-value below this threshold are considered cointegrated
# 0.05 = 95% confidence that the spread is stationary (mean-reverting)
COINT_PVALUE_THRESHOLD: float = 0.05

# Training window for hedge ratio estimation
# We estimate the hedge ratio on the FIRST N candles
# Then apply it to the remaining data (out-of-sample)
# 10,000 candles ≈ 417 days of 1h data — enough for stable estimation
TRAINING_WINDOW: int = 10_000

# Rolling window for spread z-score calculation
# 504 = 3 weeks of 1h candles — enough for stable mean/std
# Shorter window reacts faster but is noisier
# Longer window is more stable but slower to adapt to regime changes
ZSCORE_WINDOW: int = 504      # 3 weeks

# Minimum periods required before computing z-score
# Need at least 2 weeks of data before trusting the z-score
ZSCORE_MIN_PERIODS: int = 336  # 2 weeks

# Half-life filter — maximum acceptable half-life in hours
# Spreads that take too long to revert tie up capital unproductively
# 120 hours = 5 days — if reversion takes longer than this, skip the pair
MAX_HALF_LIFE_HOURS: int = 120   # 5 days

# Minimum half-life in hours
# Spreads that revert in under 2 hours are too fast for 1h candle trading
# The entry and exit would need sub-hourly data
MIN_HALF_LIFE_HOURS: float = 2.0  # 2 hours

# Forward return horizons for the spread (what we predict)
# The validator tests each horizon to find the best trading window
FORWARD_HORIZONS: Dict[str, int] = {
    "spread_forward_1h":  1,     # 1 hour ahead
    "spread_forward_2h":  2,     # 2 hours ahead
    "spread_forward_4h":  4,     # 4 hours ahead
    "spread_forward_12h": 12,    # 12 hours ahead
    "spread_forward_24h": 24,    # 24 hours ahead
}

# Minimum price for log transformation
# Prices below this are treated as data errors and excluded
MIN_PRICE: float = 1e-8

# Minimum data required per symbol before testing
MIN_CANDLES: int = 1_000   # At least 1,000 candles


# ═══════════════════════════════════════════════════════════════════════════════
# PAIR RESULT DATACLASS
# ═══════════════════════════════════════════════════════════════════════════════

class PairResult:
    """
    Stores the complete result for one tested pair.

    Contains:
        - Identification (which pair)
        - Test results (is it cointegrated? how confident?)
        - Hedge ratio (how many B units per A unit)
        - Half-life (how fast does the spread revert?)
        - Whether this pair passes all filters and is worth trading
    """

    def __init__(
        self,
        symbol_a:        str,
        symbol_b:        str,
        coint_pvalue:    float,
        hedge_ratio:     float,
        intercept:       float,
        half_life:       float,
        adf_pvalue:      float,
        is_cointegrated: bool,
        passes_filters:  bool,
        reject_reason:   str = "",
    ) -> None:

        self.symbol_a        = symbol_a         # First asset in pair
        self.symbol_b        = symbol_b         # Second asset in pair
        self.pair_name       = f"{symbol_a}/{symbol_b}"   # Human-readable name
        self.coint_pvalue    = coint_pvalue      # Engle-Granger p-value
        self.hedge_ratio     = hedge_ratio       # β: A = α + β×B + ε
        self.intercept       = intercept         # α: equilibrium level offset
        self.half_life       = half_life         # Hours for spread to revert 50%
        self.adf_pvalue      = adf_pvalue        # ADF test p-value on residuals
        self.is_cointegrated = is_cointegrated   # Did it pass the coint test?
        self.passes_filters  = passes_filters    # Did it pass half-life filters?
        self.reject_reason   = reject_reason     # Why was it rejected (if applicable)

    def to_dict(self) -> Dict:
        """Convert to dictionary for DataFrame construction."""
        return {
            "pair_name":       self.pair_name,
            "symbol_a":        self.symbol_a,
            "symbol_b":        self.symbol_b,
            "coint_pvalue":    round(self.coint_pvalue, 6),
            "adf_pvalue":      round(self.adf_pvalue, 6),
            "hedge_ratio":     round(self.hedge_ratio, 6),
            "intercept":       round(self.intercept, 6),
            "half_life_hours": round(self.half_life, 2),
            "is_cointegrated": self.is_cointegrated,
            "passes_filters":  self.passes_filters,
            "reject_reason":   self.reject_reason,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# COINTEGRATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class CointegrationEngine:
    """
    Institutional-grade cointegration research engine.

    Tests all 21 possible pairs from our 7-symbol universe for cointegration.
    For pairs that pass statistical tests and half-life filters, generates
    spread features ready for the feature_validator to test for IC.

    Research-first philosophy:
        Test cointegration → calculate spread → validate IC → then trade.
        Never assume cointegration. Always prove it statistically.

    This engine does NOT generate buy/sell signals.
    It generates RESEARCH FEATURES for the validator to assess.

    Architecture note:
        This engine works on PRICE LEVELS (not returns).
        Cointegration is a property of price levels, not returns.
        The spread is measured in price units.
        The z-score normalises the spread for the validator.
    """

    def __init__(
        self,
        universe:              List[str]        = None,
        coint_threshold:       float            = COINT_PVALUE_THRESHOLD,
        training_window:       int              = TRAINING_WINDOW,
        zscore_window:         int              = ZSCORE_WINDOW,
        zscore_min_periods:    int              = ZSCORE_MIN_PERIODS,
        max_half_life:         int              = MAX_HALF_LIFE_HOURS,
        min_half_life:         float            = MIN_HALF_LIFE_HOURS,
        forward_horizons:      Dict[str, int]   = None,
    ) -> None:
        """
        Initialise with research configuration.

        All parameters have sensible research defaults.
        They can be overridden for sensitivity analysis.
        """

        self.universe           = universe or UNIVERSE
        self.coint_threshold    = coint_threshold     # p-value cutoff for cointegration
        self.training_window    = training_window     # Candles for hedge ratio estimation
        self.zscore_window      = zscore_window       # Rolling window for z-score
        self.zscore_min_periods = zscore_min_periods  # Min candles before z-score valid
        self.max_half_life      = max_half_life       # Max acceptable half-life (hours)
        self.min_half_life      = min_half_life       # Min acceptable half-life (hours)
        self.forward_horizons   = forward_horizons or FORWARD_HORIZONS

        # Results storage — populated during run_all()
        self.pair_results: List[PairResult] = []      # All pair test results
        self.valid_pairs:  List[PairResult] = []      # Pairs that passed all filters

        # Generate all unique pair combinations from the universe
        # itertools.combinations gives us (A,B) but not (B,A) — no duplicates
        # 7 symbols → 21 unique pairs
        self.all_pairs: List[Tuple[str, str]] = list(
            itertools.combinations(self.universe, 2)
        )

        logger.info("CointegrationEngine initialised")
        logger.info(f"  Universe         : {len(self.universe)} symbols")
        logger.info(f"  Pairs to test    : {len(self.all_pairs)}")
        logger.info(f"  Coint threshold  : p < {coint_threshold}")
        logger.info(f"  Training window  : {training_window:,} candles")
        logger.info(f"  Z-score window   : {zscore_window}h")
        logger.info(f"  Half-life filter : {min_half_life}h – {max_half_life}h")
        logger.info(f"  Forward horizons : {list(self.forward_horizons.keys())}")


    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC: run_all()
    # ─────────────────────────────────────────────────────────────────────────

    def run_all(
        self,
        data: Dict[str, pd.DataFrame],
    ) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
        """
        Main entry point. Run the full cointegration research pipeline.

        Steps:
            1  Validate inputs and extract price series
            2  Test all 21 pairs for cointegration
            3  Filter pairs by half-life and statistical quality
            4  For valid pairs: calculate spread features
            5  For valid pairs: calculate spread z-scores
            6  For valid pairs: calculate half-life-weighted signal
            7  For valid pairs: calculate forward return labels
            8  Save pair summary and log results

        Parameters
        ----------
        data : Dict[str, pd.DataFrame]
            Symbol → OHLCV DataFrame with DatetimeIndex and 'close' column.

        Returns
        -------
        Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]
            - pairs_df: Summary of all 21 tested pairs with test results
            - spread_data: Dict of pair_name → enriched spread DataFrame
              Each spread DataFrame is ready for feature_validator
        """

        logger.info("=" * 70)
        logger.info("COINTEGRATION ENGINE — STARTING")
        logger.info(f"Testing {len(self.all_pairs)} pairs across "
                    f"{len(self.universe)} symbols")
        logger.info("=" * 70)

        # ── Step 1: Validate and extract price series ─────────────────────────
        prices = self._validate_and_extract_prices(data)

        # ── Step 2: Test all pairs for cointegration ──────────────────────────
        logger.info(f"\nStep 2: Testing {len(self.all_pairs)} pairs...")
        logger.info("-" * 70)

        for symbol_a, symbol_b in self.all_pairs:

            # Skip if either symbol is missing from price data
            if symbol_a not in prices or symbol_b not in prices:
                logger.warning(
                    f"  {symbol_a}/{symbol_b}: SKIPPED — "
                    f"price data missing for one or both symbols"
                )
                continue

            # Test this pair for cointegration
            result = self._test_pair(
                symbol_a, symbol_b,
                prices[symbol_a], prices[symbol_b],
            )

            self.pair_results.append(result)

            # Log result
            status = "✓ COINTEGRATED" if result.is_cointegrated else "✗ not cointegrated"
            filter_status = "✓ VALID" if result.passes_filters else f"✗ {result.reject_reason}"
            logger.info(
                f"  {result.pair_name:20} | "
                f"p={result.coint_pvalue:.4f} | "
                f"β={result.hedge_ratio:.4f} | "
                f"HL={result.half_life:.1f}h | "
                f"{status} | {filter_status}"
            )

        # ── Step 3: Identify valid pairs ──────────────────────────────────────
        self.valid_pairs = [r for r in self.pair_results if r.passes_filters]

        logger.info(f"\nPair testing complete:")
        logger.info(f"  Total pairs tested  : {len(self.pair_results)}")
        logger.info(
            f"  Cointegrated        : "
            f"{sum(1 for r in self.pair_results if r.is_cointegrated)}"
        )
        logger.info(f"  Passed all filters  : {len(self.valid_pairs)}")

        if not self.valid_pairs:
            logger.warning(
                "No pairs passed all filters. Consider:\n"
                "  - Relaxing coint_threshold (currently "
                f"{self.coint_threshold})\n"
                "  - Relaxing half-life filters\n"
                "  - Using a different timeframe or universe"
            )

        # ── Steps 4-7: Build spread features for valid pairs ──────────────────
        spread_data: Dict[str, pd.DataFrame] = {}

        for result in self.valid_pairs:

            logger.info(f"\nBuilding spread features: {result.pair_name}")

            price_a = prices[result.symbol_a]
            price_b = prices[result.symbol_b]

            # Step 4: Calculate the spread
            spread_df = self._calculate_spread(result, price_a, price_b)

            # Step 5: Calculate z-score of spread
            spread_df = self._calculate_spread_zscore(spread_df, result)

            # Step 6: Calculate half-life weighted signal
            spread_df = self._calculate_pair_signal(spread_df, result)

            # Step 7: Calculate forward return labels
            spread_df = self._calculate_forward_returns(spread_df, result)

            # Store enriched spread DataFrame
            pair_key = f"{result.symbol_a}_{result.symbol_b}"
            spread_data[pair_key] = spread_df

        # ── Step 8: Build summary and log ─────────────────────────────────────
        pairs_df = self._build_pairs_summary()
        self._log_summary(pairs_df, spread_data)

        logger.info("\nCOINTEGRATION ENGINE — COMPLETE")
        logger.info("=" * 70)

        return pairs_df, spread_data


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1: VALIDATE AND EXTRACT PRICES
    # ─────────────────────────────────────────────────────────────────────────

    def _validate_and_extract_prices(
        self,
        data: Dict[str, pd.DataFrame],
    ) -> Dict[str, pd.Series]:
        """
        Validate all DataFrames and extract aligned close price Series.

        Why use log prices for cointegration?
            Cointegration tests work on the relationship between price levels.
            Log prices are preferred because:
            1. The hedge ratio becomes a ratio (not a difference) — more stable
            2. Log prices grow additively like returns — statistically cleaner
            3. The spread in log space = log(A/B^β) = log relative value
            Formula: log_price = log(close)

        We align all price series to the same timestamps using an outer join
        and forward-fill small gaps before testing.
        """

        logger.info("Step 1: Validating inputs and extracting prices...")

        prices: Dict[str, pd.Series] = {}

        for symbol in self.universe:

            if symbol not in data:
                logger.warning(f"  {symbol}: not in data, will skip pairs involving it")
                continue

            df = data[symbol]

            # Validate DatetimeIndex
            if not isinstance(df.index, pd.DatetimeIndex):
                logger.warning(f"  {symbol}: no DatetimeIndex, skipping")
                continue

            # Validate close column
            if "close" not in df.columns:
                logger.warning(f"  {symbol}: no close column, skipping")
                continue

            # Validate minimum candle count
            if len(df) < MIN_CANDLES:
                logger.warning(
                    f"  {symbol}: only {len(df)} candles "
                    f"(need {MIN_CANDLES}), skipping"
                )
                continue

            # Extract close prices and convert to log prices
            # clip(lower=MIN_PRICE) prevents log(0) errors
            close = df["close"].clip(lower=MIN_PRICE)
            log_price = np.log(close)

            prices[symbol] = log_price
            logger.info(f"  {symbol}: ✓ ({len(log_price):,} candles)")

        logger.info(
            f"  Extracted {len(prices)}/{len(self.universe)} price series"
        )

        return prices


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2: TEST A SINGLE PAIR
    # ─────────────────────────────────────────────────────────────────────────

    def _test_pair(
        self,
        symbol_a: str,
        symbol_b: str,
        price_a:  pd.Series,
        price_b:  pd.Series,
    ) -> PairResult:
        """
        Run the full Engle-Granger cointegration test on one pair.

        Engle-Granger Two-Step Procedure:
        ───────────────────────────────────
        Step 1: OLS regression of log(A) on log(B)
                log(A_t) = α + β × log(B_t) + ε_t
                β = hedge ratio (how many B units per A unit)
                The residuals ε_t are the "spread" — the deviation from equilibrium

        Step 2: ADF test on the residuals
                If residuals are stationary → A and B are cointegrated
                Stationary means the spread always reverts to zero (mean-reversion)

        Half-Life Calculation (Ornstein-Uhlenbeck):
        ─────────────────────────────────────────────
        If spread is cointegrated, how FAST does it revert?
        We model the spread as an Ornstein-Uhlenbeck (OU) process:
            Δspread_t = λ × spread_{t-1} + ε_t

        The half-life is: HL = -log(2) / λ
        Where λ is estimated by regressing Δspread on spread_{t-1}

        λ is negative for mean-reverting processes (spread always reverts)
        Large negative λ → fast reversion → short half-life → more tradeable
        """

        # ── Align price series to common timestamps ───────────────────────────
        # Some symbols may have different trading hours or missing candles
        # Inner join keeps only timestamps where BOTH assets have valid data
        aligned = pd.concat([price_a, price_b], axis=1, join="inner")
        aligned.columns = ["price_a", "price_b"]
        aligned.dropna(inplace=True)

        # Need sufficient data for reliable estimation
        if len(aligned) < MIN_CANDLES:
            return PairResult(
                symbol_a=symbol_a, symbol_b=symbol_b,
                coint_pvalue=1.0, hedge_ratio=0.0, intercept=0.0,
                half_life=np.inf, adf_pvalue=1.0,
                is_cointegrated=False, passes_filters=False,
                reject_reason=f"insufficient aligned data ({len(aligned)} rows)",
            )

        # ── Use training window only for all estimation ───────────────────────
        # Hedge ratio and cointegration tests use ONLY training data
        # This prevents look-ahead bias — we don't use future data to
        # calibrate the parameters we apply to future data
        train_end = min(self.training_window, len(aligned))
        train     = aligned.iloc[:train_end]

        # ── Step 1: OLS regression to estimate hedge ratio ────────────────────
        # Regress log(A) on log(B) with an intercept
        # β (hedge_ratio) tells us: for every 1 unit of log(B), how much log(A)?
        # In practice: if BNB moves 1%, how much does ETH move due to cointegration?
        try:
            X = add_constant(train["price_b"].values)   # Add intercept column
            y = train["price_a"].values                  # Dependent variable

            ols_result = OLS(y, X).fit()                 # Fit OLS regression

            intercept   = float(ols_result.params[0])    # α: equilibrium offset
            hedge_ratio = float(ols_result.params[1])    # β: hedge ratio

        except Exception as e:
            return PairResult(
                symbol_a=symbol_a, symbol_b=symbol_b,
                coint_pvalue=1.0, hedge_ratio=0.0, intercept=0.0,
                half_life=np.inf, adf_pvalue=1.0,
                is_cointegrated=False, passes_filters=False,
                reject_reason=f"OLS failed: {e}",
            )

        # ── Calculate residuals (the spread) on training data ─────────────────
        # spread = log(A) - α - β × log(B)
        # This is the deviation from the long-run equilibrium relationship
        spread_train = (
            train["price_a"] -
            intercept -
            hedge_ratio * train["price_b"]
        )

        # ── Step 2: ADF test on residuals ─────────────────────────────────────
        # Tests whether the spread is stationary (mean-reverting)
        # H0 (null hypothesis): spread has a unit root (NOT stationary)
        # If we reject H0 (p < threshold): spread IS stationary → cointegrated
        try:
            adf_result = adfuller(
                spread_train.dropna(),
                maxlag=20,          # Check lags up to 20 periods
                autolag="AIC",      # Use AIC to select optimal lag
                regression="c",     # Include constant in ADF regression
            )
            adf_pvalue = float(adf_result[1])    # Extract p-value

        except Exception as e:
            return PairResult(
                symbol_a=symbol_a, symbol_b=symbol_b,
                coint_pvalue=1.0, hedge_ratio=hedge_ratio, intercept=intercept,
                half_life=np.inf, adf_pvalue=1.0,
                is_cointegrated=False, passes_filters=False,
                reject_reason=f"ADF test failed: {e}",
            )

        # ── Engle-Granger cointegration test (confirmation) ───────────────────
        # statsmodels provides a direct cointegration test
        # We use it as a second confirmation alongside our manual ADF
        try:
            _, coint_pvalue, _ = coint(
                train["price_a"],
                train["price_b"],
                trend="c",          # Include constant in test regression
            )
            coint_pvalue = float(coint_pvalue)
        except Exception:
            coint_pvalue = adf_pvalue   # Fall back to ADF p-value

        # ── Determine cointegration ───────────────────────────────────────────
        is_cointegrated = adf_pvalue < self.coint_threshold

        if not is_cointegrated:
            return PairResult(
                symbol_a=symbol_a, symbol_b=symbol_b,
                coint_pvalue=coint_pvalue, hedge_ratio=hedge_ratio,
                intercept=intercept, half_life=np.inf,
                adf_pvalue=adf_pvalue,
                is_cointegrated=False, passes_filters=False,
                reject_reason=f"ADF p={adf_pvalue:.4f} > {self.coint_threshold}",
            )

        # ── Half-life estimation (Ornstein-Uhlenbeck) ─────────────────────────
        # Only calculate half-life for cointegrated pairs
        half_life = self._estimate_half_life(spread_train)

        # ── Apply half-life filters ───────────────────────────────────────────
        passes_filters = True
        reject_reason  = ""

        if half_life > self.max_half_life:
            passes_filters = False
            reject_reason  = (
                f"half-life {half_life:.1f}h > max {self.max_half_life}h "
                f"(spread reverts too slowly)"
            )
        elif half_life < self.min_half_life:
            passes_filters = False
            reject_reason  = (
                f"half-life {half_life:.1f}h < min {self.min_half_life}h "
                f"(spread reverts too fast for 1h candles)"
            )

        return PairResult(
            symbol_a=symbol_a, symbol_b=symbol_b,
            coint_pvalue=coint_pvalue, hedge_ratio=hedge_ratio,
            intercept=intercept, half_life=half_life,
            adf_pvalue=adf_pvalue,
            is_cointegrated=True, passes_filters=passes_filters,
            reject_reason=reject_reason,
        )


    # ─────────────────────────────────────────────────────────────────────────
    # HALF-LIFE ESTIMATION
    # ─────────────────────────────────────────────────────────────────────────

    def _estimate_half_life(
        self,
        spread: pd.Series,
    ) -> float:
        """
        Estimate the half-life of mean reversion using the OU process.

        The Ornstein-Uhlenbeck model describes a mean-reverting process:
            dS_t = λ(μ - S_t)dt + σdW_t

        In discrete time (1h candles):
            ΔS_t = λ × S_{t-1} + ε_t

        Where:
            λ = mean-reversion speed (must be negative for mean reversion)
            μ = long-run equilibrium (zero for demeaned spread)
            σ = volatility of the spread
            W_t = Brownian motion (random noise)

        Half-life formula:
            HL = -log(2) / λ

        Interpretation:
            HL = 10 hours means: if the spread is 100 units above equilibrium,
            it will be at 50 units above equilibrium after 10 hours on average.

        Returns np.inf if estimation fails or λ is not negative.
        """

        try:
            # Spread changes (first differences)
            spread_lag  = spread.shift(1)    # Spread at t-1
            spread_diff = spread.diff()      # ΔSpread = spread_t - spread_{t-1}

            # Align and drop NaNs
            valid = spread_lag.notna() & spread_diff.notna()
            y = spread_diff[valid].values    # Dependent variable: ΔSpread
            X = add_constant(spread_lag[valid].values)  # Independent: spread_{t-1}

            # OLS regression: ΔS = α + λ × S_{t-1} + ε
            ou_result = OLS(y, X).fit()
            lambda_   = float(ou_result.params[1])   # Mean-reversion speed

            # λ must be negative for mean reversion
            # If λ is positive the spread is explosive, not reverting
            if lambda_ >= 0:
                return np.inf   # Not mean-reverting

            # Half-life in candle periods (same units as input data)
            half_life = -np.log(2) / lambda_

            # Sanity check: half-life should be positive and finite
            if half_life <= 0 or not np.isfinite(half_life):
                return np.inf

            return float(half_life)

        except Exception:
            return np.inf   # Return infinity on any estimation failure


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4: CALCULATE SPREAD
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_spread(
        self,
        result:  PairResult,
        price_a: pd.Series,
        price_b: pd.Series,
    ) -> pd.DataFrame:
        """
        Calculate the spread between asset A and the hedge of asset B.

        Formula:
            spread_t = log(price_A_t) - α - β × log(price_B_t)

        Where α (intercept) and β (hedge_ratio) were estimated on training data.

        The spread represents how far asset A has deviated from its
        "fair value" relative to asset B at any given timestamp.

        Positive spread: A is above fair value relative to B
            → A expected to fall OR B expected to rise (or both)

        Negative spread: A is below fair value relative to B
            → A expected to rise OR B expected to fall (or both)

        The spread is in log-price units. For small values,
        it approximates a percentage return.
        """

        # Align both price series to common timestamps
        aligned = pd.concat([price_a, price_b], axis=1, join="outer")
        aligned.columns = ["price_a", "price_b"]

        # Forward-fill small gaps (up to 3 periods) — exchange downtime
        aligned.ffill(limit=3, inplace=True)
        aligned.dropna(inplace=True)

        # Calculate spread using TRAINING-estimated parameters
        # Note: we use the same α and β for ALL time periods (in-sample + out-of-sample)
        # This is the correct approach — parameters are fixed at training time
        aligned["spread"] = (
            aligned["price_a"] -
            result.intercept -
            result.hedge_ratio * aligned["price_b"]
        )

        # Add price columns for reference
        aligned["log_price_a"] = aligned["price_a"]
        aligned["log_price_b"] = aligned["price_b"]

        # Add pair metadata
        aligned["pair_name"]   = result.pair_name
        aligned["hedge_ratio"] = result.hedge_ratio
        aligned["half_life"]   = result.half_life

        logger.info(
            f"  {result.pair_name}: spread calculated "
            f"({len(aligned):,} rows, "
            f"mean={aligned['spread'].mean():.4f}, "
            f"std={aligned['spread'].std():.4f})"
        )

        return aligned


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 5: Z-SCORE THE SPREAD
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_spread_zscore(
        self,
        spread_df: pd.DataFrame,
        result:    PairResult,
    ) -> pd.DataFrame:
        """
        Normalise the spread using a rolling z-score.

        Why rolling z-score and not fixed z-score?
            Fixed z-score: uses mean/std from the entire history
                Problem: the equilibrium level may shift over time
                (regime changes, fundamental changes in one asset)

            Rolling z-score: uses mean/std from the past N candles only
                Better: adapts to slowly shifting equilibrium levels
                Catches: structural breaks and regime changes
                Trade-off: slower to stabilise, sensitive to window choice

        Formula:
            z_t = (spread_t - rolling_mean_t) / rolling_std_t

        Where rolling_mean and rolling_std use a 504-candle (3-week) window.

        Features produced:
            spread_mean         Rolling mean of spread (current equilibrium estimate)
            spread_std          Rolling std of spread (normal wandering range)
            spread_zscore       How far from equilibrium in std dev terms
            spread_zscore_lag1  Previous candle's z-score (momentum context)
            spread_pct_rank     Percentile rank 0-100 (more intuitive than z-score)
        """

        # Rolling mean — the current estimate of equilibrium
        spread_df["spread_mean"] = spread_df["spread"].rolling(
            window=self.zscore_window,
            min_periods=self.zscore_min_periods,
        ).mean()

        # Rolling std — how much the spread normally wanders
        spread_df["spread_std"] = spread_df["spread"].rolling(
            window=self.zscore_window,
            min_periods=self.zscore_min_periods,
        ).std()

        # Z-score: (current - mean) / std
        # Positive z: spread is above equilibrium (A expensive relative to B)
        # Negative z: spread is below equilibrium (A cheap relative to B)
        spread_df["spread_zscore"] = (
            (spread_df["spread"] - spread_df["spread_mean"])
            / spread_df["spread_std"].replace(0, np.nan)
        )

        # Winsorise z-scores — cap at ±5 standard deviations
        # Z-scores beyond ±5 are almost certainly data errors or exchange outages
        spread_df["spread_zscore"] = spread_df["spread_zscore"].clip(-5, 5)

        # Lagged z-score — previous candle's z-score
        # Captures momentum in the spread: is it getting more extreme or reverting?
        spread_df["spread_zscore_lag1"] = spread_df["spread_zscore"].shift(1)

        # Percentile rank: where is today's spread in the distribution of past N days?
        # 0 = at the bottom of recent range (A very cheap vs B)
        # 100 = at the top of recent range (A very expensive vs B)
        # 50 = exactly at the median
        spread_df["spread_pct_rank"] = spread_df["spread"].rolling(
            window=self.zscore_window,
            min_periods=self.zscore_min_periods,
        ).rank(pct=True) * 100

        logger.info(
            f"  {result.pair_name}: z-score calculated "
            f"(window={self.zscore_window}h, "
            f"mean z={spread_df['spread_zscore'].mean():.4f})"
        )

        return spread_df


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 6: PAIR SIGNAL
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_pair_signal(
        self,
        spread_df: pd.DataFrame,
        result:    PairResult,
    ) -> pd.DataFrame:
        """
        Calculate the composite pair trading signal.

        This is NOT a trade signal. It is a research feature for the validator.

        The signal combines:
            - Spread z-score (how far from equilibrium?)
            - Z-score momentum (is deviation growing or shrinking?)
            - Half-life weight (fast-reverting pairs get stronger signals)

        Formula:
            pair_signal = -spread_zscore × half_life_weight

        Why the negative sign?
            We are testing MEAN REVERSION.
            A large positive z-score means A is ABOVE fair value → expect it to FALL
            So positive z → negative expected return for spread → negative signal
            (We expect the spread to decrease, meaning A falls relative to B)

        Half-life weight:
            pair_signal_hl_weighted = pair_signal / sqrt(half_life)
            Pairs with shorter half-life get proportionally stronger signals
            because they are more reliably and quickly mean-reverting.

        Multiple signal variants are produced — the validator tests all of them.
        """

        # Primary signal: negative of z-score (mean reversion direction)
        # Positive signal: spread is below equilibrium → expect spread to rise
        # Negative signal: spread is above equilibrium → expect spread to fall
        spread_df["pair_signal"] = -spread_df["spread_zscore"]

        # Half-life weighted signal
        # sqrt(half_life) gives proportional weight reduction for slow reverters
        # A 4-hour half-life pair gets 2× more signal weight than a 16-hour pair
        if result.half_life > 0 and np.isfinite(result.half_life):
            hl_weight = 1.0 / np.sqrt(result.half_life)
            spread_df["pair_signal_hl_weighted"] = (
                spread_df["pair_signal"] * hl_weight
            )
        else:
            spread_df["pair_signal_hl_weighted"] = spread_df["pair_signal"]

        # Momentum-adjusted signal
        # Incorporates whether the z-score is growing or shrinking
        # z_change = current z - previous z
        # If z_change is large positive: deviation is growing → stronger signal
        # If z_change is large negative: deviation is shrinking → weaker signal
        z_change = spread_df["spread_zscore"] - spread_df["spread_zscore_lag1"]
        spread_df["pair_signal_momentum"] = (
            -spread_df["spread_zscore"] +
            0.3 * -z_change   # Stronger signal if deviation is still growing
        )

        # Extreme signal flag: |z| > 2 standard deviations
        # Institutional desks often only trade when spread is "extreme enough"
        # This binary flag lets the validator test whether IC is higher
        # for extreme events vs moderate deviations
        spread_df["pair_signal_extreme"] = (
            spread_df["spread_zscore"].abs() > 2.0
        ).astype(int) * spread_df["pair_signal"]

        logger.info(
            f"  {result.pair_name}: pair signal calculated "
            f"(4 signal variants)"
        )

        return spread_df


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 7: FORWARD RETURN LABELS
    # ─────────────────────────────────────────────────────────────────────────

    def _calculate_forward_returns(
        self,
        spread_df: pd.DataFrame,
        result:    PairResult,
    ) -> pd.DataFrame:
        """
        Calculate forward spread returns at multiple horizons.

        These are the Y variables — what the spread actually did
        N hours after each timestamp.

        We predict SPREAD DIRECTION, not individual asset direction.
        A positive forward spread return means the spread increased
        (A became MORE expensive relative to B than equilibrium).
        A negative forward spread return means the spread decreased
        (A became CHEAPER relative to B — moved toward equilibrium).

        For a MEAN REVERSION signal:
            We expect negative pair_signal to predict negative forward spread
            (spread continues falling back to equilibrium)
            And positive pair_signal to predict positive forward spread
            (spread continues rising back to equilibrium)

        The IC between pair_signal and spread_forward tells us
        exactly how predictive the signal is at each horizon.
        """

        for label_name, periods in self.forward_horizons.items():

            # Forward spread return: how did the spread change N periods later?
            # N-period cumulative spread return
            spread_change = spread_df["spread"].diff(periods)   # spread_t+N - spread_t
            spread_df[label_name] = spread_change.shift(-periods)  # Align to current row

        logger.info(
            f"  {result.pair_name}: forward labels added "
            f"({list(self.forward_horizons.keys())})"
        )

        return spread_df


    # ─────────────────────────────────────────────────────────────────────────
    # STEP 8: BUILD SUMMARY AND LOG
    # ─────────────────────────────────────────────────────────────────────────

    def _build_pairs_summary(self) -> pd.DataFrame:
        """
        Build a summary DataFrame of all pair test results.

        This is the research output that tells the researcher:
            - Which pairs are cointegrated
            - Their hedge ratios and half-lives
            - Which passed the tradeable filters
            - Why others were rejected
        """

        if not self.pair_results:
            return pd.DataFrame()

        rows = [r.to_dict() for r in self.pair_results]
        df   = pd.DataFrame(rows)

        # Sort: valid pairs first, then by half-life (fastest reverters first)
        df = df.sort_values(
            ["passes_filters", "half_life_hours"],
            ascending=[False, True],
        )

        return df

    def _log_summary(
        self,
        pairs_df:    pd.DataFrame,
        spread_data: Dict[str, pd.DataFrame],
    ) -> None:
        """Log a clean research summary."""

        logger.info("\n" + "=" * 70)
        logger.info("COINTEGRATION RESEARCH SUMMARY")
        logger.info("=" * 70)

        if pairs_df.empty:
            logger.info("  No pairs tested.")
            return

        # Summary counts
        n_tested  = len(pairs_df)
        n_coint   = pairs_df["is_cointegrated"].sum()
        n_valid   = pairs_df["passes_filters"].sum()

        logger.info(f"\n  Pairs tested      : {n_tested}")
        logger.info(f"  Cointegrated      : {n_coint}")
        logger.info(f"  Passed filters    : {n_valid}")

        # Show valid pairs
        if n_valid > 0:
            logger.info(f"\n  ✓ Valid pairs for spread trading:")
            valid = pairs_df[pairs_df["passes_filters"]]
            for _, row in valid.iterrows():
                logger.info(
                    f"    {row['pair_name']:20} | "
                    f"β={row['hedge_ratio']:.4f} | "
                    f"HL={row['half_life_hours']:.1f}h | "
                    f"p={row['adf_pvalue']:.4f}"
                )

        # Show spread data statistics
        if spread_data:
            logger.info(f"\n  Spread DataFrames produced: {len(spread_data)}")
            for pair_key, df in spread_data.items():
                sig_cols = [c for c in df.columns if "signal" in c]
                fwd_cols = [c for c in df.columns if "forward" in c]
                logger.info(
                    f"    {pair_key:25} | {len(df):>7,} rows | "
                    f"{len(sig_cols)} signals | {len(fwd_cols)} labels"
                )

        logger.info(
            "\n  Next step: run feature_validator on each spread DataFrame\n"
            "  with forward_return_col='spread_forward_4h'\n"
            "  Key feature to test: pair_signal, pair_signal_hl_weighted"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_cointegration_research(
    data_dir:   str = "data",
    output_dir: str = "research_data",
    interval:   str = "1h",
) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
    """
    Standalone runner. Loads all 7 CSVs, runs cointegration tests,
    saves pair summary and spread DataFrames, prints results.

    Run from project root:
        python -m bot.research.cointegration_engine

    Returns
    -------
    Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]
        pairs_df    : All 21 pair test results
        spread_data : Valid pair spread DataFrames for validator
    """

    from pathlib import Path

    logger.info("=" * 70)
    logger.info("COINTEGRATION ENGINE — STANDALONE RUN")
    logger.info("=" * 70)

    # ── Load all 7 CSVs ───────────────────────────────────────────────────────
    data: Dict[str, pd.DataFrame] = {}

    for symbol in UNIVERSE:

        csv_path = Path(data_dir) / f"{symbol}_{interval}.csv"

        if not csv_path.exists():
            logger.warning(f"  {symbol}: not found at {csv_path}")
            continue

        try:
            df = pd.read_csv(csv_path)

            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                df.set_index("timestamp", inplace=True)

            for col in ["open", "high", "low", "close", "volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            df.dropna(subset=["close"], inplace=True)
            df.sort_index(inplace=True)

            data[symbol] = df
            logger.info(f"  Loaded {symbol}: {len(df):,} candles")

        except Exception as e:
            logger.error(f"  Failed to load {symbol}: {e}")

    # ── Run engine ────────────────────────────────────────────────────────────
    engine              = CointegrationEngine()
    pairs_df, spread_data = engine.run_all(data)

    # ── Save outputs ──────────────────────────────────────────────────────────
    Path(output_dir).mkdir(exist_ok=True)

    # Save pair summary
    if not pairs_df.empty:
        pairs_path = Path(output_dir) / "cointegration_pairs.csv"
        pairs_df.to_csv(pairs_path, index=False)
        logger.info(f"\n  Saved pair summary: {pairs_path}")

    # Save each valid pair's spread DataFrame
    for pair_key, df in spread_data.items():
        out = Path(output_dir) / f"{pair_key}_cointegration.csv"
        df.reset_index().to_csv(out, index=False)
        logger.info(f"  Saved spread data: {out}")

    # ── Print pair summary table ──────────────────────────────────────────────
    if not pairs_df.empty:
        logger.info("\n" + "=" * 70)
        logger.info("ALL PAIRS — TEST RESULTS")
        logger.info("=" * 70)
        logger.info(
            f"\n{'Pair':20} {'Coint_p':>9} {'ADF_p':>8} "
            f"{'β':>8} {'HL(h)':>8} {'Status':>12}"
        )
        logger.info("-" * 70)
        for _, row in pairs_df.iterrows():
            if row["passes_filters"]:
                status = "✓ VALID"
            elif row["is_cointegrated"]:
                status = "⚠ COINT/NO HL"
            else:
                status = "✗ NONE"
            logger.info(
                f"  {row['pair_name']:20} "
                f"{row['coint_pvalue']:>9.4f} "
                f"{row['adf_pvalue']:>8.4f} "
                f"{row['hedge_ratio']:>8.4f} "
                f"{row['half_life_hours']:>8.1f} "
                f"{status:>12}"
            )

    return pairs_df, spread_data


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_cointegration_research(
        data_dir="data",
        output_dir="research_data",
        interval="1h",
    )