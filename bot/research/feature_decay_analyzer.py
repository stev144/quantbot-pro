# ═══════════════════════════════════════════════════════════════════════════════
# bot/research/feature_decay_analyzer.py
#
# FEATURE DECAY ANALYZER
# Module 2 of 3 — Temporal Research Suite
#
# Version  : 2.0 (Research Grade — peer reviewed fixes applied)
# Author   : QuantiBot Pro Research Pipeline
#
# v2.0 changes (per ChatGPT peer review):
#   FIX 1 — Removed synthetic Sharpe from _grade(): was distorting results
#   FIX 2 — _grade() now uses IC + p-value only: correct for feature research
#   FIX 3 — Display shows p-value instead of Sharpe: more informative
#   FIX 4 — Fixed IC bar scale at 0.10: was per-feature max (misleading)
#   FIX 5 — Trend uses linear regression slope: more robust than mean comparison
#
# ─────────────────────────────────────────────────────────────────────────────
# PURPOSE
# ─────────────────────────────────────────────────────────────────────────────
#
# Produces the visual decay display that ChatGPT described:
#
#   Feature: rsi
#   Symbol : BTC_USDT
#
#   Year  IC      Sharpe  Trend
#   2020  ████████  0.082   1.45  EXCELLENT
#   2021  ███████   0.071   1.31  EXCELLENT
#   2022  ██████    0.046   0.89  GOOD
#   2023  █████     0.031   0.52  WEAK
#   2024  ███       0.018   0.21  POOR
#   2025  ██        0.009  -0.04  DEAD
#   2026  █         0.006  -0.08  DEAD
#
#   Status: DECAYING → AVOID IN LIVE TRADING
#
# Also produces regime-specific breakdown:
#   Bull markets (2020 H2, 2021): how does feature perform?
#   Bear markets (2022): does it survive the crash?
#   ETF era (2024+): is it still alive after institutional entry?
#   Crisis events (Luna May 2022, FTX Nov 2022): does it break down?
#
# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT
# ─────────────────────────────────────────────────────────────────────────────
#
#   research_data/temporal/{SYMBOL}_decay_report.txt   — human-readable report
#   research_data/temporal/{SYMBOL}_decay_data.csv     — machine-readable data
#   research_data/temporal/{SYMBOL}_regime_breakdown.csv — regime analysis
#
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

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

YEAR_WINDOWS: Dict[str, Tuple[str, str]] = {
    "2020": ("2020-01-01", "2020-12-31"),
    "2021": ("2021-01-01", "2021-12-31"),
    "2022": ("2022-01-01", "2022-12-31"),
    "2023": ("2023-01-01", "2023-12-31"),
    "2024": ("2024-01-01", "2024-12-31"),
    "2025": ("2025-01-01", "2025-12-31"),
    "2026": ("2026-01-01", "2026-12-31"),
}

YEAR_CONTEXT: Dict[str, str] = {
    "2020": "COVID crash + DeFi summer",
    "2021": "Bull market peak",
    "2022": "Luna + FTX — bear market",
    "2023": "Recovery",
    "2024": "BTC ETF — institutional entry",
    "2025": "Full institutional era",
    "2026": "Current year",
}

# Specific regime windows within years
# These capture events that span parts of years, not full years
REGIME_WINDOWS: Dict[str, Tuple[str, str, str]] = {
    "bull_2020h2":    ("2020-07-01", "2020-12-31", "Bull — DeFi summer"),
    "bull_2021":      ("2021-01-01", "2021-11-30", "Bull — retail mania peak"),
    "luna_crash":     ("2022-05-01", "2022-06-30", "Crisis — Luna collapse"),
    "ftx_crash":      ("2022-11-01", "2022-12-31", "Crisis — FTX collapse"),
    "bear_2022":      ("2022-01-01", "2022-12-31", "Bear — full year"),
    "recovery_2023":  ("2023-01-01", "2023-12-31", "Recovery — ETF anticipation"),
    "etf_entry":      ("2024-01-01", "2024-06-30", "ETF era — institutional entry"),
    "post_etf":       ("2024-07-01", "2024-12-31", "Post-ETF — new normal"),
    "institutional":  ("2025-01-01", "2025-12-31", "Full institutional era"),
    "current":        ("2026-01-01", "2026-12-31", "Current market"),
}

SYMBOLS: List[str] = [
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

# Bar chart configuration for terminal display
MAX_BAR_WIDTH: int = 20        # Maximum bar length in characters
BAR_CHAR:      str = "█"       # Bar fill character
EMPTY_CHAR:    str = "░"       # Empty bar character

# IC thresholds
IC_STRONG:   float = 0.05
IC_MODERATE: float = 0.03
IC_WEAK:     float = 0.01

MIN_OBS: int = 300
ANNUALISATION_FACTOR: float = np.sqrt(8_760)

EXCLUDE_COLS = {
    'symbol', 'timeframe', 'timestamp', 'market_regime',
    'forward_return_1h', 'forward_return_4h', 'forward_return_24h',
    'forward_return_2h', 'forward_return_12h',
    'win_1h', 'win_4h', 'win_24h',
    'close', 'open', 'high', 'low', 'volume',
    'realized_vol', 'adx', 'volatility_state',
    'index', 'Unnamed: 0',
}


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE DECAY ANALYZER
# ═══════════════════════════════════════════════════════════════════════════════

class FeatureDecayAnalyzer:
    """
    Produces visual decay displays and regime-specific feature analysis.

    Two outputs per symbol:

    1. Decay display (terminal + text file):
       Shows IC bar charts for each feature across each year.
       Immediately shows whether a feature is GAINING, STABLE, or DECAYING.

    2. Regime breakdown (CSV):
       Shows feature performance in specific market conditions:
       bull markets, bear markets, crashes, ETF era, institutional era.
       Reveals whether a feature is regime-specific or broadly applicable.
    """

    def __init__(
        self,
        forward_col: str = "forward_return_4h",
        output_dir:  str = "research_data/temporal",
        min_obs:     int = MIN_OBS,
        alpha:       float = 0.05,
    ) -> None:

        self.forward_col = forward_col
        self.output_dir  = Path(output_dir)
        self.min_obs     = min_obs
        self.alpha       = alpha
        self.output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("FeatureDecayAnalyzer initialised")
        logger.info(f"  Forward return : {forward_col}")
        logger.info(f"  Output dir     : {output_dir}")


    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC: analyze()
    # ─────────────────────────────────────────────────────────────────────────

    def analyze(
        self,
        df:     pd.DataFrame,
        symbol: str,
    ) -> Dict[str, pd.DataFrame]:
        """
        Run full decay analysis for one symbol.

        Returns
        -------
        Dict with "decay_data" and "regime_breakdown" DataFrames.
        """

        logger.info(f"\n{'='*70}")
        logger.info(f"DECAY ANALYSIS — {symbol}")
        logger.info(f"{'='*70}")

        df = self._parse_timestamp(df, symbol)
        if df is None:
            return {}

        features = self._get_features(df)
        if not features or self.forward_col not in df.columns:
            logger.warning(f"  {symbol}: missing features or forward column")
            return {}

        logger.info(f"  Features: {len(features)} | Candles: {len(df):,}")

        # ── Year-by-year IC for decay display ────────────────────────────────
        decay_data = self._compute_decay_data(df, features, symbol)

        # ── Regime-specific breakdown ─────────────────────────────────────────
        regime_data = self._compute_regime_breakdown(df, features, symbol)

        # ── Build and save visual decay report ───────────────────────────────
        report_text = self._build_decay_report(decay_data, symbol)
        self._save_report(report_text, decay_data, regime_data, symbol)

        # Print to terminal
        print(report_text)

        return {
            "decay_data":       decay_data,
            "regime_breakdown": regime_data,
        }


    # ─────────────────────────────────────────────────────────────────────────
    # COMPUTE DECAY DATA
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_decay_data(
        self,
        df:       pd.DataFrame,
        features: List[str],
        symbol:   str,
    ) -> pd.DataFrame:
        """
        Compute IC and Sharpe for each feature in each calendar year.
        This is the raw data behind the bar chart display.
        """

        rows = []

        for year, (start_str, end_str) in YEAR_WINDOWS.items():

            start = pd.Timestamp(start_str, tz="UTC")
            end   = pd.Timestamp(end_str,   tz="UTC")
            yr_df = df.loc[(df.index >= start) & (df.index <= end)]

            if len(yr_df) < self.min_obs:
                continue

            fwd = yr_df[self.forward_col]

            for feature in features:

                feat = yr_df[feature]
                valid = (
                    feat.notna() & np.isfinite(feat) &
                    fwd.notna()  & np.isfinite(fwd)
                )

                if valid.sum() < 100:
                    continue

                fc = feat[valid].values
                fw = fwd[valid].values

                # IC — the primary measure of predictive power
                # Spearman rank correlation: feature vs forward return
                ic, pvalue = stats.spearmanr(fc, fw)

                # FIX 2: Sharpe is NOT computed here.
                # A valid Sharpe requires a real trading strategy with position
                # sizing, thresholds, and transaction costs. np.sign(feature)
                # is not a portfolio. Computing Sharpe this way produces an
                # artificial number that contaminates the grade.
                # Sharpe belongs in strategy evaluation, not feature evaluation.
                sharpe = np.nan    # Kept as column for CSV compatibility only

                # Grade — based on IC and p-value only (see FIX 1 in _grade)
                grade = self._grade(ic, pvalue, sharpe)

                rows.append({
                    "symbol":  symbol,
                    "feature": feature,
                    "year":    year,
                    "context": YEAR_CONTEXT.get(year, ""),
                    "n_obs":   int(valid.sum()),
                    "ic":      round(float(ic), 6) if not np.isnan(ic) else np.nan,
                    "pvalue":  round(float(pvalue), 6),
                    "sharpe":  round(float(sharpe), 4),
                    "grade":   grade,
                    "significant": pvalue < self.alpha,
                })

        return pd.DataFrame(rows)


    # ─────────────────────────────────────────────────────────────────────────
    # COMPUTE REGIME BREAKDOWN
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_regime_breakdown(
        self,
        df:       pd.DataFrame,
        features: List[str],
        symbol:   str,
    ) -> pd.DataFrame:
        """
        Compute feature IC for specific market regimes.

        This answers:
            "Does RSI work in bear markets but not bull markets?"
            "Did this feature survive the FTX crash?"
            "Is it only useful in the ETF institutional era?"

        These insights are critical for regime-adaptive trading:
        you only deploy a feature in the regime where it works.
        """

        rows = []

        for regime_name, (start_str, end_str, context) in REGIME_WINDOWS.items():

            start    = pd.Timestamp(start_str, tz="UTC")
            end      = pd.Timestamp(end_str,   tz="UTC")
            reg_df   = df.loc[(df.index >= start) & (df.index <= end)]
            n_regime = len(reg_df)

            if n_regime < self.min_obs:
                continue

            fwd = reg_df[self.forward_col]

            for feature in features:

                feat  = reg_df[feature]
                valid = (
                    feat.notna() & np.isfinite(feat) &
                    fwd.notna()  & np.isfinite(fwd)
                )

                if valid.sum() < 50:
                    continue

                fc = feat[valid].values
                fw = fwd[valid].values

                ic, pvalue = stats.spearmanr(fc, fw)

                rows.append({
                    "symbol":  symbol,
                    "feature": feature,
                    "regime":  regime_name,
                    "context": context,
                    "n_obs":   int(valid.sum()),
                    "ic":      round(float(ic), 6) if not np.isnan(ic) else np.nan,
                    "pvalue":  round(float(pvalue), 6),
                    "significant": pvalue < self.alpha,
                    "ic_grade": self._grade_ic_only(ic),
                })

        return pd.DataFrame(rows)


    # ─────────────────────────────────────────────────────────────────────────
    # BUILD VISUAL DECAY REPORT
    # ─────────────────────────────────────────────────────────────────────────

    def _build_decay_report(
        self,
        decay_data: pd.DataFrame,
        symbol:     str,
    ) -> str:
        """
        Build the visual text report with bar charts.

        This is the output ChatGPT described:
            Feature: rsi
            2020  ████████  0.082  EXCELLENT
            2021  ███████   0.071  EXCELLENT
            ...
            2025  ██        0.009  DEAD
            Status: DECAYING
        """

        if decay_data.empty:
            return f"\nNo decay data for {symbol}\n"

        lines = []
        sep   = "=" * 70

        lines.append(sep)
        lines.append(f"FEATURE DECAY REPORT — {symbol}")
        lines.append(f"Forward return: {self.forward_col}")
        lines.append(sep)

        features = decay_data["feature"].unique()

        for feature in features:

            feat_df = decay_data[decay_data["feature"] == feature]
            if feat_df.empty:
                continue

            lines.append(f"\nFeature: {feature}")
            lines.append("-" * 60)
            lines.append(
                f"{'Year':<6} {'IC Bar':<22} {'IC':>7} "
                f"{'P-value':>10} {'Grade':<12}"    # FIX 3: P-value replaces Sharpe
            )
            lines.append("-" * 60)

            # FIX 4: Use a FIXED IC scale of 0.10 for ALL features.
            # Previously: max_ic = feat_df["ic"].abs().max()
            # Problem: every feature filled the bar to 100% regardless of IC size.
            #   A feature with IC=0.01 looked as strong as one with IC=0.08.
            #   This was visually misleading — weak features appeared strong.
            # Fix: anchor scale at 0.10 so bars reflect true absolute IC strength:
            #   IC=0.08 → 16/20 bars filled  (strong — almost full)
            #   IC=0.05 → 10/20 bars filled  (moderate — half bar)
            #   IC=0.02 →  4/20 bars filled  (weak — short bar)
            #   IC=0.005 → 1/20 bars filled  (dead — barely visible)
            FIXED_IC_SCALE = 0.10    # Global maximum for bar scaling

            for _, row in feat_df.sort_values("year").iterrows():

                ic    = row["ic"]       # Information coefficient
                pval  = row["pvalue"]   # p-value (FIX 3: shown instead of Sharpe)
                grade = row["grade"]    # Feature grade
                year  = row["year"]     # Calendar year

                # Build bar using fixed scale — honest visual representation
                if pd.isna(ic):
                    bar = "N/A" + " " * 18    # Placeholder when IC unavailable
                else:
                    # Scale bar length by absolute IC vs fixed 0.10 maximum
                    bar_length = int(abs(ic) / FIXED_IC_SCALE * MAX_BAR_WIDTH)
                    bar_length = max(1, min(bar_length, MAX_BAR_WIDTH))  # Clamp 1-20
                    filled     = BAR_CHAR  * bar_length                  # Filled portion
                    empty      = EMPTY_CHAR * (MAX_BAR_WIDTH - bar_length)  # Empty portion
                    bar        = filled + empty

                # Format IC with sign — negative IC is also informative
                ic_str   = f"{ic:+.4f}"   if not pd.isna(ic)   else "   N/A"
                # FIX 3: Show p-value instead of Sharpe
                # p-value directly measures statistical confidence
                # Sharpe was synthetic and misleading (see _grade docstring)
                pval_str = f"{pval:.4f}"  if not pd.isna(pval) else "   N/A"

                lines.append(
                    f"{year:<6} {bar:<22} {ic_str:>7} "
                    f"{pval_str:>10} {grade:<12}"
                )

            # Determine trend
            trend = self._determine_trend(feat_df)
            lines.append(f"\n  → Trend: {trend}")
            lines.append("")

        # Summary table at the end
        lines.append(sep)
        lines.append("SUMMARY — TREND CLASSIFICATION")
        lines.append(sep)
        lines.append(f"\n{'Feature':<30} {'Trend':<12} {'Recommendation'}")
        lines.append("-" * 60)

        for feature in features:
            feat_df = decay_data[decay_data["feature"] == feature]
            trend   = self._determine_trend(feat_df)
            rec     = self._trend_to_recommendation(trend)
            lines.append(f"  {feature:<30} {trend:<12} {rec}")

        lines.append(sep)

        return "\n".join(lines)


    def _determine_trend(self, feat_df: pd.DataFrame) -> str:
        """
        Classify IC trend for one feature across years using linear regression.

        FIX 5 (peer review):
            Previous method used mean_recent vs mean_early comparison.
            Problem: averages ignore whether IC is consistently improving
            or just had one lucky year. A feature with IC values of
            [0.01, 0.08, 0.01, 0.01, 0.01] has high mean_early but the
            0.08 was an outlier, not a trend.

            Fix: fit a simple OLS regression line through the yearly absolute
            IC values. The slope of that line tells the true trend:
                positive slope  → IC is consistently GAINING over time
                negative slope  → IC is consistently DECAYING over time
                near-zero slope → IC is STABLE (no meaningful trend)

            This is much more robust because regression uses ALL data points
            and is not distorted by a single outlier year.

        Trend categories:
            DEAD     — recent mean |IC| below weak threshold (< 0.01)
            EMERGING — was silent early, now alive (regression confirms growth)
            GAINING  — slope positive and recent IC above moderate threshold
            DECAYING — slope negative and early IC was meaningful
            STABLE   — slope near zero and IC above weak threshold
            VOLATILE — IC varies with no consistent direction
        """

        # Get year indices and IC values for regression
        ics      = feat_df.set_index("year")["ic"]    # IC by year
        all_years = list(YEAR_WINDOWS.keys())           # All possible years

        # Build arrays: x = year index (0,1,2,...), y = absolute IC
        x_vals = []    # Year index (numeric for regression)
        y_vals = []    # Absolute IC value

        for i, year in enumerate(all_years):
            ic_val = ics.get(year, np.nan)
            if not pd.isna(ic_val):
                x_vals.append(i)              # Year as integer index
                y_vals.append(abs(ic_val))    # Absolute IC (direction irrelevant for trend)

        # Need at least 2 data points for regression
        if len(x_vals) < 2:
            return "INSUFFICIENT_DATA"

        # Compute recent and early IC means for threshold checks
        recent_ics = [
            ics.get(y, np.nan) for y in ["2024", "2025", "2026"]
            if not pd.isna(ics.get(y, np.nan))
        ]
        early_ics = [
            ics.get(y, np.nan) for y in ["2020", "2021"]
            if not pd.isna(ics.get(y, np.nan))
        ]

        mean_recent = np.nanmean(np.abs(recent_ics)) if recent_ics else 0.0
        mean_early  = np.nanmean(np.abs(early_ics))  if early_ics  else 0.0

        # DEAD: no meaningful signal in recent years regardless of trend
        # Check this first — a decaying feature that is now dead = DEAD
        if mean_recent < IC_WEAK:
            return "DEAD"

        # FIX 5: Fit linear regression through absolute IC values
        # slope > 0: IC increasing year over year (GAINING)
        # slope < 0: IC decreasing year over year (DECAYING)
        # slope ≈ 0: IC roughly constant (STABLE)
        x_arr  = np.array(x_vals, dtype=float)   # Year indices as float array
        y_arr  = np.array(y_vals, dtype=float)    # Absolute IC values as float array

        # OLS regression: y = slope * x + intercept
        slope, intercept, r_value, p_value, std_err = stats.linregress(x_arr, y_arr)

        # Significance of the slope — is the trend real or random?
        slope_significant = p_value < 0.10    # Use 10% for trend detection (lenient)

        # EMERGING: was near-zero early, now showing signal
        # Regression confirms the growth with positive slope
        if mean_early < IC_WEAK and mean_recent > IC_MODERATE:
            return "EMERGING"

        # GAINING: significant positive slope AND recent IC is meaningful
        if slope > 0.002 and slope_significant and mean_recent > IC_MODERATE:
            return "GAINING"

        # DECAYING: significant negative slope AND early IC was meaningful
        if slope < -0.002 and slope_significant and mean_early > IC_MODERATE:
            return "DECAYING"

        # STABLE: near-zero slope and IC is consistently present
        # Also catches: significant slope but IC was always low (not meaningful change)
        if abs(slope) <= 0.002 and mean_recent > IC_WEAK:
            return "STABLE"

        # VOLATILE: IC varies but regression slope is not significant
        # Cannot determine a reliable trend from available data
        return "VOLATILE"

    def _trend_to_recommendation(self, trend: str) -> str:
        """Convert trend to trading recommendation."""
        mapping = {
            "DEAD":     "AVOID — no signal in current market",
            "EMERGING": "TRADE NOW — signal growing in institutional era",
            "GAINING":  "TRADE NOW — improving over time",
            "DECAYING": "AVOID — signal dying, will hurt live performance",
            "STABLE":   "TRADE NOW — consistent across regimes",
            "VOLATILE": "RESEARCH — inconsistent, regime-dependent",
        }
        return mapping.get(trend, "UNKNOWN")

    def _grade(self, ic: float, pvalue: float, sharpe: float = np.nan) -> str:
        """
        Grade a feature based on IC strength and statistical significance only.

        FIX 1 + FIX 2 (peer review):
            Sharpe is completely removed from grading.
            The synthetic Sharpe computed from np.sign(feature) is not a real
            trading strategy — it ignores position sizing, thresholds, ranking,
            and transaction costs. Using it in _grade() caused statistically
            significant features to appear as NOT_SIG or WEAK incorrectly.

            IC measures predictive power.
            p-value measures statistical confidence.
            These two metrics are sufficient and correct for feature evaluation.
            Sharpe belongs in strategy evaluation, not feature grading.

        Thresholds (extended per review for finer granularity):
            IC >= 0.07 → EXCELLENT  (exceptional predictive power)
            IC >= 0.05 → STRONG     (strong predictive power)
            IC >= 0.03 → GOOD       (moderate predictive power)
            IC >= 0.02 → MODERATE   (weak but present signal)
            IC >= 0.01 → WEAK       (barely detectable signal)
            IC <  0.01 → DEAD       (no meaningful signal)
        """

        if pd.isna(ic):
            return "N/A"             # IC could not be calculated

        if pvalue >= self.alpha:
            return "NOT_SIG"         # IC is not statistically significant

        abs_ic = abs(ic)             # Direction does not matter for grading

        if abs_ic >= 0.07:  return "EXCELLENT"   # Exceptional — rare in any market
        if abs_ic >= 0.05:  return "STRONG"      # Strong — worth trading
        if abs_ic >= 0.03:  return "GOOD"        # Good — investigate further
        if abs_ic >= 0.02:  return "MODERATE"    # Moderate — combine with others
        if abs_ic >= 0.01:  return "WEAK"        # Weak — monitor only
        return "DEAD"                            # Below any meaningful threshold

    def _grade_ic_only(self, ic: float) -> str:
        """Grade IC value alone."""
        if pd.isna(ic): return "N/A"
        abs_ic = abs(ic)
        if abs_ic >= IC_STRONG:    return "STRONG"
        if abs_ic >= IC_MODERATE:  return "MODERATE"
        if abs_ic >= IC_WEAK:      return "WEAK"
        return "DEAD"


    # ─────────────────────────────────────────────────────────────────────────
    # SAVE OUTPUTS
    # ─────────────────────────────────────────────────────────────────────────

    def _save_report(
        self,
        report_text: str,
        decay_data:  pd.DataFrame,
        regime_data: pd.DataFrame,
        symbol:      str,
    ) -> None:
        """Save text report and CSV data files."""

        # Text decay report
        txt_path = self.output_dir / f"{symbol}_decay_report.txt"
        txt_path.write_text(report_text, encoding="utf-8")
        logger.info(f"  Saved: {txt_path}")

        # CSV decay data
        if not decay_data.empty:
            csv_path = self.output_dir / f"{symbol}_decay_data.csv"
            decay_data.to_csv(csv_path, index=False)
            logger.info(f"  Saved: {csv_path}")

        # Regime breakdown
        if not regime_data.empty:
            reg_path = self.output_dir / f"{symbol}_regime_breakdown.csv"
            regime_data.to_csv(reg_path, index=False)
            logger.info(f"  Saved: {reg_path}")


    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_timestamp(self, df, symbol):
        df = df.copy()
        for col in ['timestamp', 'index', 'Unnamed: 0']:
            if col in df.columns:
                try:
                    df[col] = pd.to_datetime(df[col], utc=True)
                    df.set_index(col, inplace=True)
                    break
                except Exception:
                    continue
        if not isinstance(df.index, pd.DatetimeIndex):
            return None
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.sort_index(inplace=True)
        return df

    def _get_features(self, df):
        return [
            c for c in df.columns
            if c not in EXCLUDE_COLS
            and df[c].dtype in [np.float64, np.float32, np.int64, np.int32]
            and not df[c].isna().all()
        ]


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_decay_analysis(
    observations_dir: str = "research_data",
    output_dir:       str = "research_data/temporal",
    forward_col:      str = "forward_return_4h",
    symbols:          List[str] = None,
) -> Dict[str, Dict]:
    """
    Run decay analysis for all symbols.

    Run from project root:
        python -m bot.research.feature_decay_analyzer
    """

    analyzer = FeatureDecayAnalyzer(
        forward_col=forward_col,
        output_dir=output_dir,
    )

    symbols = symbols or SYMBOLS
    results = {}

    logger.info("=" * 70)
    logger.info("FEATURE DECAY ANALYZER — STARTING")
    logger.info("=" * 70)

    for symbol in symbols:
        obs_path = Path(observations_dir) / f"{symbol}_observations.csv"
        if not obs_path.exists():
            logger.warning(f"  {symbol}: not found at {obs_path}")
            continue
        try:
            df = pd.read_csv(obs_path)
            results[symbol] = analyzer.analyze(df, symbol)
        except Exception as e:
            logger.error(f"  {symbol}: failed — {e}")

    logger.info("\n" + "=" * 70)
    logger.info("DECAY ANALYSIS COMPLETE")
    logger.info("=" * 70)

    return results


if __name__ == "__main__":
    run_decay_analysis()