# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/feature_validator_v2_institutional.py
#
# PHASE 2 UPGRADED: INSTITUTIONAL-GRADE FEATURE VALIDATOR
#
# Version: 2.0 (Professional)
# Incorporates all ChatGPT recommendations for institutional-quality research
#
# Features:
# ✓ Economic significance thresholds (not just p-value)
# ✓ Multiple testing correction (Bonferroni & FDR)
# ✓ Mann-Whitney U test (non-parametric alternative)
# ✓ Information Coefficient (IC) analysis
# ✓ Stability testing across time periods
# ✓ Sharpe contribution per feature
# ✓ Market regime validation
# ✓ Feature interaction testing
# ✓ Feature versioning & hashing
# ✓ HTML report generation
# ✓ Parallel processing ready
#
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────────────────────────
# IMPORTS AND DEPENDENCIES
# ─────────────────────────────────────────────────────────────────────────────────────────────────────────

import logging                                  # Professional logging
import re                                       # claude code changed: new — parses the horizon out of forward_return_col's name (Phase B, P1-3)
import pandas as pd                             # DataFrame operations
import numpy as np                              # Numerical operations
from typing import Dict, List, Tuple, Optional  # Type hints
from pathlib import Path                       # Path handling
from scipy import stats                        # Statistical tests (t-test, Mann-Whitney)
from statsmodels.stats.multitest import multipletests  # Multiple testing correction
from dataclasses import dataclass, asdict      # Type-safe data structures
import json                                    # JSON serialization
import warnings                                # Warning handling
import hashlib                                 # Feature versioning via hash
from datetime import datetime                  # Timestamps
import concurrent.futures                      # Parallel processing (optional)
from bot import data_fetcher

# Suppress warnings
warnings.filterwarnings('ignore')

# ─────────────────────────────────────────────────────────────────────────────────────────────────────────
# LOGGING CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)
# Configure logging with timestamp, level, and message
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s"
)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# MARKET REGIME ENUMERATION
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

class MarketRegime:
    """
    Market regime classification constants.
    These are used to separate observations by market condition.
    A feature that works in TRENDING may fail in CRASH.
    We test separately.
    """
    
    TRENDING = "trending"      # Strong directional movement
    RANGING = "ranging"        # Sideways, mean-reverting
    VOLATILE = "volatile"      # High volatility, choppy
    CRASH = "crash"            # Major drawdown in progress
    RECOVERY = "recovery"      # Bouncing off crash lows


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# CONFIGURATION CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

# Statistical thresholds
ALPHA = 0.05                                   # Statistical significance level (95% confidence)
MIN_QUARTILE_OBSERVATIONS = 30                 # Minimum observations per quartile
ECONOMIC_SIGNIFICANCE_THRESHOLD = 0.005        # Minimum 0.5% return difference (CRITICAL!)
                                              # This prevents trading tiny differences that don't cover costs

# Multiple testing correction
USE_BONFERRONI = True                         # Use Bonferroni for ultra-conservative approach
USE_FDR = True                                # Use FDR (False Discovery Rate) for balanced approach
# Both are calculated; FDR is more practical than Bonferroni for many features

# Stability testing windows (test across different time periods)
STABILITY_WINDOWS = {
    '2021': (pd.Timestamp('2021-01-01'), pd.Timestamp('2021-12-31')),  # Bull market
    '2022': (pd.Timestamp('2022-01-01'), pd.Timestamp('2022-12-31')),  # Bear market
    '2023': (pd.Timestamp('2023-01-01'), pd.Timestamp('2023-12-31')),  # Recovery
    '2024': (pd.Timestamp('2024-01-01'), pd.Timestamp('2024-12-31')),  # Current
}

# Information coefficient (IC) thresholds
IC_EXCELLENT = 0.15                           # IC > 0.15 = excellent predictive power
IC_GOOD = 0.10                                # IC > 0.10 = good predictive power
IC_ACCEPTABLE = 0.05                          # IC > 0.05 = acceptable

# Sharpe ratio threshold for contribution
MIN_SHARPE_CONTRIBUTION = 0.5                 # Feature should contribute at least this to Sharpe

# claude code changed: new — Phase B remediation (forensic-audit findings
# P1-2, P1-3). See _block_shuffle_1d()/_block_permutation_pvalue()'s
# docstrings for the dependence-aware significance test this drives, and
# apply_family_wide_correction()'s docstring for the multiple-testing scope
# fix. N_PERMUTATIONS matches permutation_test_engine.py's
# DEFAULT_N_PERMUTATIONS (100) — same convention already established
# elsewhere in this research pipeline, not picked independently.
BLOCK_PERMUTATION_N: int = 100
DEFAULT_HORIZON_CANDLES_FALLBACK: int = 4     # Used only if forward_return_col's name doesn't parse to a number


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# ENHANCED VALIDATION RESULT (Dataclass)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass
class InstitutionalValidationResult:
    """
    Complete validation result for institutional research.
    
    This replaces the simple FeatureValidationResult.
    Includes all metrics ChatGPT recommended.
    """
    
    # Basic identification
    feature_name: str                          # Name of the feature
    feature_version: str                       # Version hash for reproducibility
    calculation_timestamp: str                 # When was this calculated?
    
    # Statistical testing (Basic)
    is_valid: bool                             # Overall: should we keep this feature?
    p_value: float                             # T-test p-value
    p_value_corrected_bonferroni: float       # p-value after Bonferroni correction
    p_value_corrected_fdr: float               # p-value after FDR correction
    t_statistic: float                         # T-test statistic
    mann_whitney_pvalue: float                 # Mann-Whitney U test p-value (non-parametric)

    # claude code changed: new — Phase B (P1-3). Mann-Whitney/t-test above
    # assume i.i.d. samples; forward_return_4h-style labels on 1h candles
    # overlap and are serially dependent, which makes those p-values
    # systematically too small. This is the dependence-aware p-value that
    # actually drives passed_statistical_significance and multiple-testing
    # correction now — see _block_permutation_pvalue().
    block_permutation_pvalue: float

    # Economic significance
    quartile_spread: float                     # Q4 - Q1 return difference (%)
    is_economically_significant: bool          # spread > threshold?
    quartile_returns: Dict[int, float]         # Returns for Q1, Q2, Q3, Q4
    
    # Win rate analysis
    win_rate_spread: float                     # Q4 win rate - Q1 win rate
    quartile_win_rates: Dict[int, float]       # Win rate for each quartile
    
    # Information coefficient (NEW!)
    ic_overall: float                          # Spearman correlation with returns (-1 to +1)
    ic_rank: str                               # "excellent" / "good" / "acceptable" / "poor"
    
    # Stability across time (NEW!)
    stability_scores: Dict[str, float]         # IC score per year (2021, 2022, etc.)
    stability_consistency: float                # How consistent across years? (std dev)
    
    # Stability across regimes (NEW!)
    regime_validation: Dict[str, Dict]         # Validate separately: trending, ranging, crash
    regime_strengths: Dict[str, str]           # Which regimes is this strong in?
    
    # Sharpe contribution (NEW!)
    sharpe_contribution: float                 # What Sharpe if trading only this feature?
    sharpe_rank: str                           # "excellent" / "good" / "weak"
    
    # Feature details
    return_direction: str                      # "ascending" / "inverted" / "non_monotonic"
    min_observations_per_quartile: int         # Data quality check
    null_count: int                            # How many NaNs removed?
    
    # Decision logic (transparent)
    passed_statistical_significance: bool      # p < 0.05?
    passed_economic_significance: bool         # spread > threshold?
    passed_multiple_testing: bool              # survives correction?
    passed_stability: bool                     # consistent over time?
    
    # Final recommendation
    recommendation: str                        # "STRONG KEEP" / "KEEP" / "REVIEW" / "DELETE"
    confidence_level: float                    # 0-100%, how confident in this decision?


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# MARKET REGIME DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

class MarketRegimeDetector:
    """
    Detects market regime for each observation.
    
    A feature might:
    - Work great in RANGING markets (mean reversion works)
    - Work terribly in CRASH markets (everything sells)
    - Work OK in TRENDING markets
    
    We test separately to discover this.
    """
    
    @staticmethod
    def detect_regime(df: pd.DataFrame, 
                     volatility_col: str = 'realized_vol',
                     adx_col: str = 'adx',
                     adr_col: str = 'adr') -> pd.Series:
        """
        Classify each observation into a market regime.
        
        Args:
            df (pd.DataFrame): Observations with volatility/ADX/ADR
            volatility_col (str): Column with volatility
            adx_col (str): Column with trend strength (ADX)
            adr_col (str): Column with daily range
        
        Returns:
            pd.Series: Regime label for each observation
        """
        
        try:
            # Calculate rolling statistics for regime detection
            # Use 20-period windows for stability
            vol_mean = df[volatility_col].rolling(20).mean()  # Normal volatility level
            vol_current = df[volatility_col]                  # Current volatility
            
            # Calculate returns for crash detection
            returns = df['close'].pct_change() if 'close' in df.columns else pd.Series(0, index=df.index)
            dd_5 = returns.rolling(5).sum()                   # 5-candle drawdown
            
            regime = pd.Series("ranging", index=df.index)     # Default: ranging
            
            # CRASH: Big losses in recent period + high vol
            is_crash = (dd_5 < -0.05) & (vol_current > vol_mean * 1.5)
            regime[is_crash] = MarketRegime.CRASH
            
            # RECOVERY: After crash, vol cooling but still elevated
            is_recovery = (dd_5 > -0.02) & (vol_current > vol_mean * 1.2) & ~is_crash
            regime[is_recovery] = MarketRegime.RECOVERY
            
            # TRENDING: High trend strength (ADX) + moderate vol
            if adx_col in df.columns:
                is_trending = (df[adx_col] > 40) & ~is_crash & ~is_recovery
                regime[is_trending] = MarketRegime.TRENDING
            
            # VOLATILE: High vol but no crash
            is_volatile = (vol_current > vol_mean * 1.3) & ~is_crash & ~is_trending
            regime[is_volatile] = MarketRegime.VOLATILE
            
            # RANGING: Low ADX, normal vol (default)
            # Anything not classified above is ranging
            
            logger.debug(f"Regime distribution: {regime.value_counts().to_dict()}")
            return regime
        
        except Exception as e:
            logger.warning(f"Error detecting regime: {e}. Defaulting to 'ranging'")
            return pd.Series(MarketRegime.RANGING, index=df.index)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# INSTITUTIONAL FEATURE VALIDATOR
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

class FeatureValidator:
    """
    Production-grade feature validator implementing all institutional best practices.
    
    Validation pipeline:
    1. Quartile analysis (basic)
    2. T-test + Mann-Whitney U test (statistical)
    3. Multiple testing correction (Bonferroni + FDR)
    4. Economic significance check (> 0.5% return diff)
    5. Information Coefficient (predictive power)
    6. Stability testing (across time periods)
    7. Regime-specific validation (trending/ranging/crash)
    8. Sharpe contribution (trading impact)
    9. Final decision logic (transparent reasoning)
    """
    
    def __init__(self, min_observations: int = 30, alpha: float = 0.05,
                 random_seed: Optional[int] = None):
        """Initialize validator with institutional settings"""
        self.min_observations = min_observations
        self.alpha = alpha
        self.results: List[InstitutionalValidationResult] = []
        # Initialize regime detector
        self.regime_detector = MarketRegimeDetector()
        # claude code changed: new — Phase B (P1-3). Isolated RNG for the
        # block-permutation significance test, same pattern
        # permutation_test_engine.py uses (np.random.default_rng, not the
        # global numpy RNG). random_seed=None gives a fresh shuffle
        # sequence each run; set it for reproducible test fixtures.
        self.rng = np.random.default_rng(random_seed)

        logger.info(f"InstitutionalFeatureValidator initialized")
        logger.info(f"  Min observations per quartile: {min_observations}")
        logger.info(f"  Statistical significance: {alpha}")
        logger.info(f"  Economic significance: >{ECONOMIC_SIGNIFICANCE_THRESHOLD*100:.2f}%")
    
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # MAIN METHOD: Validate all features with full institutional pipeline
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    
    def validate_all_features_institutional(self,
                                           df: pd.DataFrame,
                                           forward_return_col: str = 'forward_return_4h') -> pd.DataFrame:
        """
        Comprehensive feature validation with all institutional checks,
        scoped to whatever is in `df` alone.

        claude code changed: Phase B (P1-2) — this now delegates to
        validate_all_features_raw() + the same correction/recompute steps
        it always ran, unchanged in behavior for a single-call df (e.g. an
        already-pooled multi-symbol research_data/observations.csv, or any
        single-symbol exploratory run). What changed is that
        run_research_all.py — the orchestrator that runs one call per
        symbol — no longer relies on THIS method's per-call correction to
        be the final word; see apply_family_wide_correction() below for
        why, and validate_all_features_raw() for the split that makes it
        possible.

        Args:
            df (pd.DataFrame): Observation database from Phase 1
            forward_return_col (str): Forward return column (label)

        Returns:
            pd.DataFrame: Full validation results, corrected against
            exactly the features present in `df` — nothing wider.
        """
        results_df = self.validate_all_features_raw(df, forward_return_col)

        # Apply multiple testing correction across whatever features were
        # actually passed to this call.
        logger.info(f"\nApplying multiple testing corrections...")
        results_df = self._apply_multiple_testing_correction(results_df)
        results_df = self._recompute_recommendations_after_correction(results_df)

        if 'confidence_level' in results_df.columns:
            results_df = results_df.sort_values('confidence_level', ascending=False)

        self._print_institutional_summary(results_df)

        return results_df

    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # RAW VALIDATION — no correction applied
    # claude code changed: new — Phase B (P1-2). Extracted from what used
    # to be the first half of validate_all_features_institutional(), which
    # computed per-feature raw stats AND applied multiple-testing
    # correction in the same call. That fused shape is exactly why
    # run_research_all.py's per-symbol correction was too narrow: each
    # symbol's ~16-19 features got corrected against each other, never
    # against the other ~300 features tested across the other 19 symbols
    # in the same research run. Splitting "compute the raw per-feature
    # result" from "correct across however many results you're holding"
    # lets the caller decide the correction scope explicitly instead of it
    # being implicitly "whatever one call happened to see" — see
    # apply_family_wide_correction() for the family-scoped caller.
    # ───────────────────────────────────────────────────────────────────────────────────────────────────

    def validate_all_features_raw(self,
                                   df: pd.DataFrame,
                                   forward_return_col: str = 'forward_return_4h') -> pd.DataFrame:
        """
        Computes every feature's raw per-feature statistics (quartiles,
        t-test, Mann-Whitney, block-permutation p-value, IC, stability,
        regime breakdown) WITHOUT applying multiple-testing correction.

        Returns a results DataFrame with a provisional (uncorrected)
        recommendation — callers that want a final, correction-aware
        recommendation must run this through _apply_multiple_testing_
        correction() + _recompute_recommendations_after_correction()
        themselves, at whatever scope their hypothesis family actually is
        (see apply_family_wide_correction() for the intended one).
        """

        logger.info(f"\n{'='*120}")
        logger.info("PHASE 2 INSTITUTIONAL: COMPREHENSIVE FEATURE VALIDATION")
        logger.info(f"{'='*120}")
        
        logger.info(f"\nConfiguration:")
        logger.info(f"  Total observations: {len(df):,}")
        logger.info(f"  Statistical alpha: {self.alpha}")
        logger.info(f"  Economic threshold: {ECONOMIC_SIGNIFICANCE_THRESHOLD*100:.2f}%")
        logger.info(f"  Multiple testing: Bonferroni + FDR corrections enabled")
        logger.info(f"  Tests: T-test, Mann-Whitney U, Information Coefficient")
        logger.info(f"  Stability: Across time periods + market regimes")
        
        # Add market regime classification
        logger.info(f"\nDetecting market regimes...")
        df['market_regime'] = self.regime_detector.detect_regime(df)
        logger.info(f"  Regime distribution:")
        for regime, count in df['market_regime'].value_counts().items():
            logger.info(f"    {regime:12} {count:10,} observations ({count/len(df)*100:5.1f}%)")
        
        # Get all feature columns (exclude metadata/labels)
        exclude_cols = {
            'symbol', 'timeframe', 'timestamp', 'market_regime',
            'close', 'open', 'high', 'low', 'volume',
            'realized_vol', 'adx'
        }
        # claude code changed: was an enumerated set of specific horizons
        # (forward_return_1h/4h/24h, win_1h/4h/24h) that didn't cover every
        # horizon the research pipeline actually produces (e.g. contagion_
        # engine.py emits forward_return_2h/12h too). Any horizon missing
        # from the enumeration silently passed through as a "feature" —
        # confirmed with real data: forward_return_2h got validated against
        # itself as the label (IC=1.0, "STRONG KEEP") when forward_return_2h
        # was the target column, pure leakage rather than signal. Matching
        # by prefix instead covers every horizon these label columns come in.
        label_prefixes = ('forward_return_', 'win_')
        feature_cols = [
            col for col in df.columns
            if col not in exclude_cols
            and not col.startswith(label_prefixes)
            and df[col].dtype in [np.float64, np.float32, np.int64]
        ]
        
        logger.info(f"\nValidating {len(feature_cols)} features...")
        logger.info("-" * 120)
        
        # Validate each feature
        self.results = []
        forward_returns = df[forward_return_col].values
        
        for i, feature_col in enumerate(feature_cols, 1):
            try:
                result = self._validate_single_feature_institutional(
                    df, feature_col, forward_return_col, forward_returns
                )
                self.results.append(result)
                
                # Log progress
                if i % 5 == 0 or i == len(feature_cols):
                    recommendation = result.recommendation
                    logger.info(f"  [{i}/{len(feature_cols)}] {result.feature_name:30} → {recommendation}")
            
            except Exception as e:
                logger.warning(f"  Skipping {feature_col}: {e}")
                continue
        
        # Convert results to dataframe — provisional recommendation only,
        # no correction applied. Caller decides correction scope.
        results_df = self._results_to_dataframe()
        return results_df

    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # DETERMINE RECOMMENDATION — shared decision-tree logic
    # claude code changed: new — extracted from what used to be inline code
    # in _validate_single_feature_institutional(), so the SAME logic can
    # be reused for the provisional (pre-correction) and final
    # (post-correction) recommendation without the two copies drifting
    # out of sync.
    # ───────────────────────────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _determine_recommendation(
        passed_significance: bool,
        passed_economic: bool,
        passed_stability: bool,
        quartile_spread: float,
        stability_consistency: float,
        ic_overall: float,
        significance_failure_reason: str = "Failed statistical significance (p >= alpha)",
    ) -> Tuple[str, float]:
        reasons = []

        if not passed_significance:
            reasons.append(significance_failure_reason)

        if not passed_economic:
            reasons.append(f"Failed economic significance (spread {quartile_spread:.4f} < {ECONOMIC_SIGNIFICANCE_THRESHOLD:.4f})")

        if not passed_stability:
            reasons.append(f"Poor stability across time (std={stability_consistency:.3f})")

        if len(reasons) == 0 and abs(ic_overall) > IC_ACCEPTABLE:
            return "STRONG KEEP", 95.0
        elif len(reasons) == 0 and abs(ic_overall) > 0.02:
            return "KEEP", 75.0
        elif len(reasons) <= 1 and abs(ic_overall) > 0.01:
            return "REVIEW", 50.0
        else:
            return "DELETE", 85.0

    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # DEPENDENCE-AWARE SIGNIFICANCE TEST (BLOCK PERMUTATION)
    # claude code changed: new — Phase B remediation (forensic-audit
    # finding P1-3). forward_return_4h on 1h candles overlaps 3-of-4 hours
    # between consecutive rows, so consecutive labels (and consecutive
    # feature values, for any feature built from a rolling/EWM window) are
    # seriously serially dependent — the exact opposite of what
    # scipy.stats.ttest_ind/mannwhitneyu assume about their inputs. Under
    # that dependence, the true number of "independent" observations is
    # much smaller than the row count, so the analytic p-value is
    # systematically too small (overstates significance) before multiple-
    # testing correction even gets a chance to run on it.
    #
    # Reuses the same METHODOLOGY permutation_test_engine.py already
    # established for the Kalman-pairs pipeline (contiguous block shuffle,
    # preserve each block's internal short-range structure, destroy the
    # cross-block feature/return alignment, empirical p-value = fraction of
    # shuffles at least as extreme as the real result) rather than
    # inventing an unrelated statistical framework (e.g. Newey-West). The
    # implementation itself can't be imported directly —
    # PermutationTestEngine._block_shuffle() is hard-wired to
    # EntryExitEngine's specific Kalman columns and re-runs a full
    # backtest engine per shuffle — so this is a from-scratch application
    # of the same block-shuffle principle to a feature-validation test
    # statistic (quartile spread) instead.
    # ───────────────────────────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_horizon_candles(forward_return_col: str) -> int:
        """
        Extracts the label horizon in candles from a column name like
        'forward_return_4h' -> 4. This horizon IS the mechanical source of
        the overlap: row t's label and row t+1's label share horizon-1
        candles of underlying return in common by construction, so it's
        the natural, principled block size — not an arbitrary tuning
        knob. Falls back to DEFAULT_HORIZON_CANDLES_FALLBACK if the name
        doesn't parse (e.g. a custom label column).
        """
        match = re.search(r'(\d+)\s*h', forward_return_col)
        if match:
            horizon = int(match.group(1))
            return max(horizon, 1)
        return DEFAULT_HORIZON_CANDLES_FALLBACK

    def _block_shuffle_1d(self, arr: np.ndarray, block_size: int) -> np.ndarray:
        """
        Chops arr into contiguous blocks of block_size and reassembles
        them in a randomly shuffled order. Preserves each block's own
        internal serial structure while destroying alignment across block
        boundaries — same principle as
        PermutationTestEngine._block_shuffle(), applied to a plain 1-D
        array instead of a multi-column DataFrame.
        """
        n = len(arr)
        n_blocks = int(np.ceil(n / block_size))
        block_bounds = [
            (i * block_size, min((i + 1) * block_size, n))
            for i in range(n_blocks)
        ]
        order = self.rng.permutation(n_blocks)
        positions = np.concatenate([
            np.arange(start, end) for start, end in (block_bounds[i] for i in order)
        ])
        return arr[positions]

    def _block_permutation_pvalue(
        self,
        returns_clean: np.ndarray,
        q1_mask: np.ndarray,
        q4_mask: np.ndarray,
        real_spread: float,
        block_size: int,
        n_permutations: int = BLOCK_PERMUTATION_N,
    ) -> float:
        """
        Empirical, dependence-aware significance test for the same
        Q4-mean-minus-Q1-mean spread the Mann-Whitney/t-test compare.

        Feature-quartile membership (q1_mask/q4_mask) is computed ONCE
        from the real, unshuffled feature values and held fixed — only
        the RETURNS are block-shuffled each replica. This asks exactly
        the right null-hypothesis question ("if returns were randomly
        reassigned in a way that preserves their own short-range
        dependence structure, but with no real relationship to which
        feature-quartile a row belongs to, how often would a spread this
        large happen by chance?") without disturbing the feature side's
        own autocorrelation at all.

        Empirical p-value convention matches
        PermutationTestEngine._compare(): (extreme_count + 1) / (n + 1) —
        never exactly zero, since the real result is itself one possible
        draw.
        """
        extreme_count = 0
        for _ in range(n_permutations):
            shuffled_returns = self._block_shuffle_1d(returns_clean, block_size)
            shuffled_spread = abs(
                np.mean(shuffled_returns[q4_mask]) - np.mean(shuffled_returns[q1_mask])
            )
            if shuffled_spread >= abs(real_spread):
                extreme_count += 1
        return (extreme_count + 1) / (n_permutations + 1)

    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # VALIDATE SINGLE FEATURE WITH FULL INSTITUTIONAL PIPELINE
    # ───────────────────────────────────────────────────────────────────────────────────────────────────

    def _validate_single_feature_institutional(self,
                                              df: pd.DataFrame,
                                              feature_col: str,
                                              forward_return_col: str,
                                              forward_returns: np.ndarray) -> InstitutionalValidationResult:
        """
        Comprehensive validation of a single feature.
        
        Performs:
        1. Quartile analysis
        2. T-test + Mann-Whitney
        3. Economic significance check
        4. Information coefficient
        5. Stability testing
        6. Regime-specific validation
        7. Sharpe contribution
        8. Final decision
        """
        
        # ── STEP 1: Extract and clean data ────────────────────────────────────
        feature_data = df[feature_col].copy()
        returns_series = pd.Series(forward_returns, index=df.index)

        # FIX: Filter NaNs from BOTH the feature column AND the returns column.
        # Previously only feature NaNs were removed. NaNs remaining in returns_clean
        # caused stats.spearmanr() to return NaN for ic_overall on every feature.
        valid_idx = (
            feature_data.notna() &
            np.isfinite(feature_data) &
            returns_series.notna() &
            np.isfinite(returns_series)
        )

        feature_clean = feature_data[valid_idx].values
        returns_clean = returns_series[valid_idx].values  # now guaranteed NaN-free

        null_count = (~valid_idx).sum()
        
        if len(feature_clean) < self.min_observations * 4:
            raise ValueError(f"Insufficient data: {len(feature_clean)}")
        
        # ── STEP 2: Quartile analysis ────────────────────────────────────────
        # Divide into 4 equal groups
        q1_thresh = np.percentile(feature_clean, 25)
        q2_thresh = np.percentile(feature_clean, 50)
        q3_thresh = np.percentile(feature_clean, 75)
        
        q1_mask = feature_clean <= q1_thresh
        q2_mask = (feature_clean > q1_thresh) & (feature_clean <= q2_thresh)
        q3_mask = (feature_clean > q2_thresh) & (feature_clean <= q3_thresh)
        q4_mask = feature_clean > q3_thresh
        
        # Calculate metrics per quartile
        q1_returns = returns_clean[q1_mask]
        q2_returns = returns_clean[q2_mask]
        q3_returns = returns_clean[q3_mask]
        q4_returns = returns_clean[q4_mask]
        
        # Check minimum observations
        min_q = min(len(q1_returns), len(q2_returns), len(q3_returns), len(q4_returns))
        if min_q < self.min_observations:
            raise ValueError(f"Quartile too small: {min_q}")
        
        # Mean returns
        q_means = {
            1: np.mean(q1_returns),
            2: np.mean(q2_returns),
            3: np.mean(q3_returns),
            4: np.mean(q4_returns),
        }
        
        # Win rates
        q_wins = {
            1: (q1_returns > 0).mean(),
            2: (q2_returns > 0).mean(),
            3: (q3_returns > 0).mean(),
            4: (q4_returns > 0).mean(),
        }
        
        # Determine direction
        spread = q_means[4] - q_means[1]
        if abs(spread) < 0.00001:
            direction = "non_monotonic"
            comp_r1, comp_r2 = q1_returns, q4_returns
        elif spread > 0:
            direction = "ascending"
            comp_r1, comp_r2 = q1_returns, q4_returns
        else:
            direction = "inverted"
            comp_r1, comp_r2 = q4_returns, q1_returns
        
        # ── STEP 3: Statistical tests ────────────────────────────────────────
        
        # T-test (parametric)
        t_stat, p_value_t = stats.ttest_ind(comp_r1, comp_r2)
        
        # Mann-Whitney U test (non-parametric, more robust)
        # This doesn't assume normal distribution
        # Use this if distributions are non-normal (which they are in trading)
        u_stat, p_value_mw = stats.mannwhitneyu(comp_r1, comp_r2, alternative='two-sided')
        
        # Use Mann-Whitney p-value (more robust for market data)
        p_value = p_value_mw

        # claude code changed: new — Phase B (P1-3). Mann-Whitney/t-test
        # above assume i.i.d. observations; forward_return_4h-style labels
        # overlap across consecutive rows and violate that assumption,
        # which makes p_value_mw systematically too small. block_pvalue is
        # the dependence-aware replacement — see
        # _block_permutation_pvalue()'s docstring. Computed here (not
        # deferred) because it needs the same q1_mask/q4_mask/spread this
        # step already has in scope.
        horizon_candles = self._parse_horizon_candles(forward_return_col)
        block_pvalue = self._block_permutation_pvalue(
            returns_clean=returns_clean,
            q1_mask=q1_mask,
            q4_mask=q4_mask,
            real_spread=spread,
            block_size=horizon_candles,
        )

        # ── STEP 4: Economic significance check (CRITICAL!) ────────────────────
        # This is what ChatGPT emphasized
        # p < 0.05 means statistically real
        # But we also need spread > threshold (covers costs)
        quartile_spread = abs(spread)
        is_economically_significant = quartile_spread > ECONOMIC_SIGNIFICANCE_THRESHOLD
        
        # ── STEP 5: Information Coefficient (Spearman rank correlation) ───────
        # IC = how much does feature correlate with actual returns?
        # Range: -1 to +1
        # 0.10+ is excellent in quant research
        # Both arrays are now NaN-free so spearmanr() will return a real number
        ic_overall, ic_pvalue = stats.spearmanr(feature_clean, returns_clean)
        
        # Classify IC strength
        if abs(ic_overall) >= IC_EXCELLENT:
            ic_rank = "excellent"
        elif abs(ic_overall) >= IC_GOOD:
            ic_rank = "good"
        elif abs(ic_overall) >= IC_ACCEPTABLE:
            ic_rank = "acceptable"
        else:
            ic_rank = "poor"
        
        # ── STEP 6: Stability testing (across time periods) ──────────────────
        # Does this feature work in 2021? 2022? 2023? 2024?
        stability_scores = {}
        
        for year, (start_date, end_date) in STABILITY_WINDOWS.items():
            try:
                # Filter data to this year (if timestamp available)
                if 'timestamp' in df.columns:
                    year_mask = (pd.to_datetime(df['timestamp']) >= start_date) & \
                               (pd.to_datetime(df['timestamp']) <= end_date)
                    year_feature = feature_data[year_mask].copy()
                    year_returns_raw = returns_series[year_mask]
                else:
                    year_feature = feature_data.copy()
                    year_returns_raw = returns_series

                # FIX: Filter NaNs from BOTH feature and returns for the year slice.
                # Previously only feature NaNs were removed, so year_returns[valid_year]
                # could still contain NaNs and cause spearmanr() to return NaN.
                valid_year = (
                    year_feature.notna() &
                    np.isfinite(year_feature.fillna(0)) &
                    year_returns_raw.notna() &
                    np.isfinite(year_returns_raw.fillna(0))
                )

                if valid_year.sum() > 50:  # Need minimum data
                    ic_year, _ = stats.spearmanr(
                        year_feature[valid_year].values,
                        year_returns_raw[valid_year].values   # .values — NaN-free numpy array
                    )
                    stability_scores[year] = float(ic_year)
            except:
                stability_scores[year] = 0.0
        
        # Calculate stability consistency (low std = consistent across years)
        stability_consistency = np.std(list(stability_scores.values()))
        
        # ── STEP 7: Regime-specific validation ───────────────────────────────
        # Does feature work in TRENDING? RANGING? CRASH?
        market_regime = df.get('market_regime', pd.Series(MarketRegime.RANGING, index=df.index))
        regime_validation = {}
        regime_strengths = {}
        
        for regime in [MarketRegime.TRENDING, MarketRegime.RANGING, 
                       MarketRegime.VOLATILE, MarketRegime.CRASH, MarketRegime.RECOVERY]:
            
            regime_mask = (market_regime == regime) & valid_idx
            
            if regime_mask.sum() > 30:  # Minimum observations
                regime_feature = feature_clean[regime_mask[valid_idx].values]
                regime_returns = returns_clean[regime_mask[valid_idx].values]
                
                # Calculate IC for this regime
                if len(regime_feature) > 10:
                    ic_regime, _ = stats.spearmanr(regime_feature, regime_returns)
                else:
                    ic_regime = 0.0
                
                regime_validation[regime] = {
                    'ic': float(ic_regime),
                    'observations': int(regime_mask.sum()),
                }
                
                # Mark as strong if IC > 0.05
                if abs(ic_regime) > 0.05:
                    regime_strengths[regime] = "strong"
                elif abs(ic_regime) > 0.02:
                    regime_strengths[regime] = "moderate"
                else:
                    regime_strengths[regime] = "weak"
        
        # ── STEP 8: Sharpe contribution ──────────────────────────────────────
        # If we traded ONLY this feature, what Sharpe would we get?
        # This is imperfect but useful
        try:
            # Simple Sharpe: return / volatility
            strategy_returns = returns_clean * np.sign(ic_overall)  # Trade in direction of correlation
            sharpe_contribution = np.mean(strategy_returns) / (np.std(strategy_returns) + 1e-8) * np.sqrt(252)
            
            if sharpe_contribution > 1.5:
                sharpe_rank = "excellent"
            elif sharpe_contribution > 1.0:
                sharpe_rank = "good"
            elif sharpe_contribution > 0.5:
                sharpe_rank = "moderate"
            else:
                sharpe_rank = "weak"
        except:
            sharpe_contribution = 0.0
            sharpe_rank = "weak"
        
        # ── STEP 9: Feature versioning ───────────────────────────────────────
        # Store calculation hash for reproducibility
        # If feature calculation changes, hash changes
        version_string = f"{feature_col}_{datetime.now().isoformat()}"
        feature_version = hashlib.md5(version_string.encode()).hexdigest()[:8]
        
        # ── STEP 10: Final decision logic (transparent) ────────────────────────

        # Check each criterion
        # claude code changed: was `p_value < self.alpha` (the i.i.d.-
        # assuming Mann-Whitney p-value). Now gates on block_pvalue, the
        # dependence-aware empirical p-value — see the STEP 3.5 comment
        # above for why p_value_mw can't be trusted as-is on overlapping
        # labels. p_value_mw/mann_whitney_pvalue are still computed and
        # stored for transparency/comparison, just no longer authoritative.
        passed_statistical_significance = block_pvalue < self.alpha
        passed_economic_significance = is_economically_significant
        # claude code changed: multiple testing correction needs the full
        # cross-feature p-value array, which only exists once every feature
        # has been validated — so this per-feature pass uses the RAW
        # (uncorrected) p-value as a PROVISIONAL recommendation only.
        # validate_all_features_institutional() recomputes the FINAL
        # recommendation/confidence_level for every feature via
        # _determine_recommendation() after real FDR correction is applied
        # (see that method and _apply_multiple_testing_correction() below) —
        # this provisional value is what gets overwritten.
        passed_stability = stability_consistency < 0.20  # IC relatively stable

        recommendation, confidence_level = self._determine_recommendation(
            passed_significance=passed_statistical_significance,
            passed_economic=passed_economic_significance,
            passed_stability=passed_stability,
            quartile_spread=quartile_spread,
            stability_consistency=stability_consistency,
            ic_overall=ic_overall,
        )
        
        # Create result
        result = InstitutionalValidationResult(
            feature_name=feature_col,
            feature_version=feature_version,
            calculation_timestamp=datetime.now().isoformat(),
            
            # claude code changed: was (p_value < self.alpha) — the naive
            # Mann-Whitney raw p-value. Phase B (P1-3): every other
            # significance-derived field in this pipeline (passed_
            # statistical_significance, passes_fdr, the recommendation
            # itself) now runs on block_pvalue instead; leaving 'valid'
            # on the old measure would let research_lab_data.py's
            # "STATISTICALLY SUPPORTED" classification (valid AND
            # passes_fdr AND passes_bonferroni) combine two different,
            # inconsistent significance tests.
            is_valid=(block_pvalue < self.alpha) and is_economically_significant,
            p_value=float(p_value),
            p_value_corrected_bonferroni=float(p_value),  # Will be updated in correction step
            p_value_corrected_fdr=float(p_value),         # Will be updated in correction step
            t_statistic=float(t_stat),
            mann_whitney_pvalue=float(p_value_mw),
            block_permutation_pvalue=float(block_pvalue),

            quartile_spread=float(quartile_spread),
            is_economically_significant=is_economically_significant,
            quartile_returns=q_means,
            
            win_rate_spread=float(q_wins[4] - q_wins[1]),
            quartile_win_rates=q_wins,
            
            ic_overall=float(ic_overall),
            ic_rank=ic_rank,
            
            stability_scores=stability_scores,
            stability_consistency=float(stability_consistency),
            
            regime_validation=regime_validation,
            regime_strengths=regime_strengths,
            
            sharpe_contribution=float(sharpe_contribution),
            sharpe_rank=sharpe_rank,
            
            return_direction=direction,
            min_observations_per_quartile=min_q,
            null_count=int(null_count),
            
            passed_statistical_significance=passed_statistical_significance,
            passed_economic_significance=passed_economic_significance,
            passed_multiple_testing=passed_statistical_significance,  # Updated after correction
            passed_stability=passed_stability,
            
            recommendation=recommendation,
            confidence_level=confidence_level,
        )
        
        return result
    
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # CONVERT RESULTS TO DATAFRAME
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    
    def _results_to_dataframe(self) -> pd.DataFrame:
        """Convert results to pandas DataFrame"""
        
        data = {
            'feature': [r.feature_name for r in self.results],
            'recommendation': [r.recommendation for r in self.results],
            'confidence_level': [r.confidence_level for r in self.results],  # matches dataclass field name
            'valid': [r.is_valid for r in self.results],
            'p_value_mannwhitney': [r.mann_whitney_pvalue for r in self.results],
            'p_value_block_permutation': [r.block_permutation_pvalue for r in self.results],  # claude code changed: new — Phase B (P1-3), the authoritative significance p-value
            'quartile_spread': [r.quartile_spread for r in self.results],
            'econ_significant': [r.is_economically_significant for r in self.results],
            'ic_overall': [r.ic_overall for r in self.results],
            'ic_rank': [r.ic_rank for r in self.results],
            'stability_std': [r.stability_consistency for r in self.results],
            'sharpe_contrib': [r.sharpe_contribution for r in self.results],
            'sharpe_rank': [r.sharpe_rank for r in self.results],
            'direction': [r.return_direction for r in self.results],
        }
        
        return pd.DataFrame(data)
    
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # APPLY MULTIPLE TESTING CORRECTION
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    
    def _apply_multiple_testing_correction(self, results_df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply Bonferroni and FDR corrections to p-values.
        
        This is CRITICAL: If you test 40 features at 95% confidence,
        statistically 2 will appear significant by pure chance.
        
        Corrections:
        - Bonferroni: Very conservative, divides alpha by number of tests
        - FDR: More practical, controls false discovery rate
        """

        try:
            results_df = results_df.copy()
            # claude code changed: was results_df['p_value_mannwhitney'] —
            # Phase B (P1-3). Correcting the i.i.d.-assuming Mann-Whitney
            # p-value would still be wrong even at the right family scope;
            # p_value_block_permutation is the dependence-aware p-value
            # that actually accounts for overlapping-label autocorrelation
            # (see _block_permutation_pvalue()). p_value_mannwhitney stays
            # in the output for transparency/comparison only.
            p_values = results_df['p_value_block_permutation'].values

            # claude code changed: multipletests() only returns real,
            # per-feature corrected p-values in its SECOND return slot for
            # whichever single `method=` was requested — the 3rd/4th slots
            # are scalar significance thresholds (alphacSidak/alphacBonf),
            # not a second method's p-value array. The original code called
            # this once with method='fdr_bh' and mislabeled slot 4 (a
            # scalar) as "bonferroni_pvals", then crashed computing n_fdr
            # over it ('float' object is not iterable), which silently
            # forced passes_fdr/passes_bonferroni to False for every
            # feature via the except block below. Two separate calls, one
            # per method, gives each its own real corrected p-value array.
            _, bonferroni_pvals, _, _ = multipletests(
                p_values, alpha=self.alpha, method='bonferroni'
            )
            _, fdr_pvals, _, _ = multipletests(
                p_values, alpha=self.alpha, method='fdr_bh'  # Benjamini-Hochberg FDR
            )

            results_df['p_bonferroni']      = bonferroni_pvals
            results_df['p_fdr']             = fdr_pvals
            results_df['passes_fdr']        = fdr_pvals < self.alpha
            results_df['passes_bonferroni'] = bonferroni_pvals < self.alpha

            n_raw        = int(sum(1 for p in p_values if p < 0.05))
            n_fdr        = int(sum(1 for p in fdr_pvals if p < self.alpha))
            n_bonferroni = int(sum(1 for p in bonferroni_pvals if p < self.alpha))

            logger.info(f"  Multiple testing correction applied:")
            logger.info(f"    Features passing p < 0.05 : {n_raw}")
            logger.info(f"    Features passing FDR       : {n_fdr}")
            logger.info(f"    Features passing Bonferroni: {n_bonferroni}")

            return results_df

        except Exception as e:
            logger.warning(f"Error applying multiple testing correction: {e}")
            # Always return a usable dataframe even on failure
            # so _print_institutional_summary() doesn't crash on missing columns
            results_df = results_df.copy()
            results_df['p_bonferroni']      = results_df['p_value_block_permutation']
            results_df['p_fdr']             = results_df['p_value_block_permutation']
            results_df['passes_fdr']        = False
            results_df['passes_bonferroni'] = False
            return results_df

    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # RECOMPUTE RECOMMENDATIONS AFTER CORRECTION
    # ───────────────────────────────────────────────────────────────────────────────────────────────────

    def _recompute_recommendations_after_correction(self, results_df: pd.DataFrame) -> pd.DataFrame:
        """
        Overwrite each feature's recommendation/confidence_level with the
        FDR-corrected version, and set passed_multiple_testing to the real
        passes_fdr outcome.

        claude code changed: new. _validate_single_feature_institutional()
        computes a PROVISIONAL recommendation from the raw, uncorrected
        p-value, because multiple-testing correction needs the full
        cross-feature p-value array that only exists once every feature has
        been validated. Previously nothing ever recomputed recommendation/
        confidence_level/passed_multiple_testing after
        _apply_multiple_testing_correction() ran, despite a comment
        implying it should be ("Updated after correction") — so
        passes_fdr/passes_bonferroni were decorative columns that never
        actually influenced STRONG KEEP/KEEP/REVIEW/DELETE. This method is
        the second half of that fix (the first half is
        _apply_multiple_testing_correction()'s tuple-unpacking fix above).
        """
        if "passes_fdr" not in results_df.columns:
            return results_df

        results_df = results_df.copy()
        final_recs, final_confs = [], []
        for _, row in results_df.iterrows():
            rec, conf = self._determine_recommendation(
                passed_significance=bool(row["passes_fdr"]),
                passed_economic=bool(row["econ_significant"]),
                passed_stability=bool(row["stability_std"] < 0.20),
                quartile_spread=row["quartile_spread"],
                stability_consistency=row["stability_std"],
                ic_overall=row["ic_overall"],
                significance_failure_reason="Failed FDR-corrected, dependence-aware statistical significance (p_fdr >= alpha)",
            )
            final_recs.append(rec)
            final_confs.append(conf)

        results_df["recommendation"] = final_recs
        results_df["confidence_level"] = final_confs
        results_df["passed_multiple_testing"] = results_df["passes_fdr"]
        return results_df

    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # PRINT INSTITUTIONAL SUMMARY
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    
    def _print_institutional_summary(self, results_df: pd.DataFrame) -> None:
        """Print comprehensive institutional summary"""
        
        logger.info(f"\n{'='*120}")
        logger.info("INSTITUTIONAL VALIDATION SUMMARY")
        logger.info(f"{'='*120}")
        
        # Count recommendations
        strong_keep = (results_df['recommendation'] == 'STRONG KEEP').sum()
        keep = (results_df['recommendation'] == 'KEEP').sum()
        review = (results_df['recommendation'] == 'REVIEW').sum()
        delete = (results_df['recommendation'] == 'DELETE').sum()
        
        logger.info(f"\nRecommendations:")
        logger.info(f"  ✓✓ STRONG KEEP: {strong_keep}")
        logger.info(f"  ✓  KEEP: {keep}")
        logger.info(f"  ⚠  REVIEW: {review}")
        logger.info(f"  ✗  DELETE: {delete}")
        
        # Show top features
        logger.info(f"\nTOP 10 FEATURES (by IC rank + confidence):")
        logger.info("-" * 120)
        
        top_df = results_df.head(10)
        for idx, row in top_df.iterrows():
            logger.info(
                f"  {row['feature']:25} | {row['recommendation']:12} | "
                f"p={row['p_value_mannwhitney']:.6f} | IC={row['ic_overall']:+.4f} ({row['ic_rank']}) | "
                f"Sharpe={row['sharpe_contrib']:+.3f}"
            )
        
        logger.info(f"\nStatistical Summary:")
        logger.info(f"  Mean IC (all features): {results_df['ic_overall'].mean():+.4f}")
        logger.info(f"  Mean Sharpe contribution: {results_df['sharpe_contrib'].mean():+.3f}")
        logger.info(f"  Mean quartile spread: {results_df['quartile_spread'].mean():.6f}")
        
        logger.info(f"\n{'='*120}\n")
    
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # SAVE RESULTS
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    
    def save_institutional_results(self, results_df: pd.DataFrame, 
                                  output_dir: str = "research_data") -> Dict[str, Path]:
        """Save full institutional validation results"""
        
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        saved_files = {}
        
        try:
            # Main results CSV
            csv_path = output_path / "validated_features_institutional.csv"
            results_df.to_csv(csv_path, index=False)
            saved_files['csv'] = csv_path
            logger.info(f"✓ Saved to {csv_path}")
            
            # Strong keep only
            strong_keep_path = output_path / "strong_keep_features.csv"
            strong_keep = results_df[results_df['recommendation'] == 'STRONG KEEP']
            if len(strong_keep) > 0:
                strong_keep.to_csv(strong_keep_path, index=False)
                saved_files['strong_keep'] = strong_keep_path
                logger.info(f"✓ Saved {len(strong_keep)} strong keep features to {strong_keep_path}")
            
            logger.info(f"✓ Results saved to {output_path}")
            return saved_files
        
        except Exception as e:
            logger.error(f"Error saving results: {e}")
            return {}


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# FAMILY-WIDE MULTIPLE-TESTING CORRECTION
# claude code changed: new — Phase B remediation (forensic-audit finding
# P1-2). The bug: run_research_all.py called
# validate_all_features_institutional() once PER SYMBOL, so each symbol's
# ~16-19 features were corrected only against each other — 20 independent
# corrections, each blind to the other 19. The true hypothesis space one
# run_research_all.py invocation actually tests is every (symbol, feature)
# pair together.
#
# HYPOTHESIS FAMILY DEFINITION (documented per the remediation brief):
# One "family" = every (symbol, feature) raw result produced by a single
# run_research_all.py invocation — today, ~20 symbols x ~16-19 features =
# roughly 320-380 tests. This is deliberately the FULL pooled scope, not a
# per-feature-across-symbols or per-symbol-across-features partial scope,
# because that's the scope the research pipeline's own existing language
# already claims ("re-run against forward_return_4h across all 20 tracked
# symbols" — validated_feature_registry.py) and it's the most conservative
# choice available: correcting a smaller family would let more features
# through, which is exactly the "optimize the methodology merely to
# increase the number of passing features" trap the remediation brief
# explicitly warns against. A test is NOT run per-symbol as a separate
# "replication opportunity" here — testing the same feature on 20 symbols
# and only reporting the ones that happened to pass is itself a form of
# selective reporting across a hypothesis space, and needs the same
# guard multiple-testing correction provides for testing 16 different
# features on one symbol.
#
# HOW FDR IS CONTROLLED: identical mechanism as before
# (statsmodels.stats.multitest.multipletests, method='fdr_bh', alpha=0.05)
# — only the population being corrected changed, from ~16-19 p-values to
# the full pooled ~320-380.
#
# WHAT CHANGES IN INTERPRETATION: "passes_fdr" for a given (symbol,
# feature) row now means "still significant after accounting for every
# other symbol/feature combination looked at in this research sweep", not
# just the ~16-19 features that happened to share that one symbol's CSV.
# Expect FEWER passes than the old per-symbol-scoped correction produced —
# that's the correction working as intended, not a regression.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def apply_family_wide_correction(
    raw_results_by_symbol: Dict[str, pd.DataFrame],
    alpha: float = 0.05,
) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame]:
    """
    Pools every symbol's raw (uncorrected) validate_all_features_raw()
    output into one hypothesis family, applies ONE multiple-testing
    correction across all of it, recomputes every row's final
    recommendation from the corrected significance, then splits the
    result back into one DataFrame per symbol (preserving the existing
    per-symbol CSV contract downstream consumers rely on).

    Args:
        raw_results_by_symbol: symbol -> validate_all_features_raw()
            output (uncorrected). Every value must already share the same
            columns (i.e. come from the same FeatureValidator config).
        alpha: significance level for the correction.

    Returns:
        (per_symbol_corrected, pooled_corrected) — per_symbol_corrected is
        symbol -> corrected DataFrame (same shape/columns as the input,
        recommendation/confidence_level/passed_multiple_testing now
        reflect the family-wide correction); pooled_corrected is the full
        concatenated DataFrame (with a 'symbol' column) — saved separately
        as the auditable record of exactly what the family-wide
        correction actually corrected across.
    """
    tagged = []
    for symbol, df in raw_results_by_symbol.items():
        df = df.copy()
        df.insert(0, 'symbol', symbol)
        tagged.append(df)

    pooled = pd.concat(tagged, ignore_index=True)

    # Reuse the exact same correction/recompute logic every other call
    # site uses — a throwaway validator instance just for its alpha
    # config and these two methods, not for re-running any per-feature
    # computation (that already happened in each symbol's raw pass).
    validator = FeatureValidator(alpha=alpha)
    pooled_corrected = validator._apply_multiple_testing_correction(pooled)
    pooled_corrected = validator._recompute_recommendations_after_correction(pooled_corrected)

    n_family = len(pooled_corrected)
    n_pass = int(pooled_corrected['passes_fdr'].sum()) if 'passes_fdr' in pooled_corrected.columns else 0
    logger.info(
        f"Family-wide correction: {n_pass}/{n_family} (symbol, feature) "
        f"pairs pass FDR at alpha={alpha} across the full pooled family "
        f"({len(raw_results_by_symbol)} symbols)."
    )

    per_symbol_corrected = {
        symbol: group.drop(columns=['symbol']).reset_index(drop=True)
        for symbol, group in pooled_corrected.groupby('symbol', sort=False)
    }

    return per_symbol_corrected, pooled_corrected


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# MAIN RUNNER
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def run_institutional_validation(
    observations_path: str = "research_data/observations.csv",
    output_dir: str = "research_data",
    forward_return_col: str = "forward_return_1h"
) -> pd.DataFrame:
    """
    Main entry point: Run institutional-grade feature validation.
    """
    
    logger.info(f"Loading observations from {observations_path}...")
    
    try:
        obs_df = pd.read_csv(observations_path)
        logger.info(f"✓ Loaded {len(obs_df):,} observations")
    except FileNotFoundError:
        logger.error(f"File not found: {observations_path}")
        return pd.DataFrame()
    
    # Run validator
    validator = FeatureValidator(min_observations=30, alpha=0.05)
    results_df = validator.validate_all_features_institutional(obs_df, forward_return_col)
    
    # Save results
    if len(results_df) > 0:
        validator.save_institutional_results(results_df, output_dir)
        
        logger.info("\n" + "="*120)
        logger.info("PHASE 2 INSTITUTIONAL COMPLETE")
        logger.info("="*120)
        logger.info(f"\n✓ Institutional validation complete!")
        logger.info(f"\nYou now have:")
        logger.info(f"  • validated_features_institutional.csv (complete results)")
        logger.info(f"  • strong_keep_features.csv (best features)")
        logger.info(f"\nNext: PHASE 3 (Feature Interaction Testing)")
        logger.info("="*120 + "\n")
    
    return results_df


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """Execute institutional feature validation"""
    
    results_df = run_institutional_validation(
        observations_path="research_data/observations.csv",
        output_dir="research_data",
        forward_return_col="forward_return_1h"
    )
    
    if len(results_df) > 0:
        logger.info("Institutional feature validation complete!")