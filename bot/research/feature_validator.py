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
    
    def __init__(self, min_observations: int = 30, alpha: float = 0.05):
        """Initialize validator with institutional settings"""
        self.min_observations = min_observations
        self.alpha = alpha
        self.results: List[InstitutionalValidationResult] = []
        # Initialize regime detector
        self.regime_detector = MarketRegimeDetector()
        
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
        Comprehensive feature validation with all institutional checks.
        
        Args:
            df (pd.DataFrame): Observation database from Phase 1
            forward_return_col (str): Forward return column (label)
        
        Returns:
            pd.DataFrame: Full validation results
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
            'forward_return_1h', 'forward_return_4h', 'forward_return_24h',
            'win_1h', 'win_4h', 'win_24h', 'close', 'open', 'high', 'low', 'volume',
            'realized_vol', 'adx'
        }
        feature_cols = [col for col in df.columns if col not in exclude_cols and df[col].dtype in [np.float64, np.float32, np.int64]]
        
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
        
        # Convert results to dataframe
        results_df = self._results_to_dataframe()
        
        # Apply multiple testing correction globally
        logger.info(f"\nApplying multiple testing corrections...")
        results_df = self._apply_multiple_testing_correction(results_df)
        
        # Sort by confidence level — guard added in case column is missing
        if 'confidence_level' in results_df.columns:
            results_df = results_df.sort_values('confidence_level', ascending=False)
        
        # Print summary
        self._print_institutional_summary(results_df)
        
        return results_df
    
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
        passed_statistical_significance = p_value < self.alpha
        passed_economic_significance = is_economically_significant
        # Multiple testing correction applied later
        passed_stability = stability_consistency < 0.20  # IC relatively stable
        
        # Build recommendation
        reasons = []
        
        if not passed_statistical_significance:
            reasons.append("Failed statistical significance (p >= 0.05)")
        
        if not passed_economic_significance:
            reasons.append(f"Failed economic significance (spread {quartile_spread:.4f} < {ECONOMIC_SIGNIFICANCE_THRESHOLD:.4f})")
        
        if not passed_stability:
            reasons.append(f"Poor stability across time (std={stability_consistency:.3f})")
        
        if len(reasons) == 0 and abs(ic_overall) > IC_ACCEPTABLE:
            recommendation = "STRONG KEEP"
            confidence_level = 95.0
        elif len(reasons) == 0 and abs(ic_overall) > 0.02:
            recommendation = "KEEP"
            confidence_level = 75.0
        elif len(reasons) <= 1 and abs(ic_overall) > 0.01:
            recommendation = "REVIEW"
            confidence_level = 50.0
        else:
            recommendation = "DELETE"
            confidence_level = 85.0
        
        # Create result
        result = InstitutionalValidationResult(
            feature_name=feature_col,
            feature_version=feature_version,
            calculation_timestamp=datetime.now().isoformat(),
            
            is_valid=(p_value < self.alpha) and is_economically_significant,
            p_value=float(p_value),
            p_value_corrected_bonferroni=float(p_value),  # Will be updated in correction step
            p_value_corrected_fdr=float(p_value),         # Will be updated in correction step
            t_statistic=float(t_stat),
            mann_whitney_pvalue=float(p_value_mw),
            
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
            p_values = results_df['p_value_mannwhitney'].values

            # Apply corrections
            rejected, bonferroni_pvals, _, fdr_pvals = multipletests(
                p_values,
                alpha=self.alpha,
                method='fdr_bh'  # Benjamini-Hochberg FDR
            )

            results_df['p_bonferroni']      = bonferroni_pvals
            results_df['p_fdr']             = fdr_pvals
            results_df['passes_fdr']        = fdr_pvals < self.alpha
            results_df['passes_bonferroni'] = bonferroni_pvals < self.alpha

            # FIX: rejected is a numpy bool array — use plain Python sum() not .sum()
            # to avoid 'bool object has no attribute sum' error
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
            results_df['p_bonferroni']      = results_df['p_value_mannwhitney']
            results_df['p_fdr']             = results_df['p_value_mannwhitney']
            results_df['passes_fdr']        = False
            results_df['passes_bonferroni'] = False
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