# ═══════════════════════════════════════════════════════════════════════════════
# bot/research/feature_stability_analyzer.py
#
# FEATURE STABILITY ANALYZER
# Module 1 of 3 — Temporal Research Suite
#
# Version  : 1.0 (Research Grade)
# Author   : QuantiBot Pro Research Pipeline
#
# ─────────────────────────────────────────────────────────────────────────────
# PURPOSE
# ─────────────────────────────────────────────────────────────────────────────
#
# Answers the question:
#   "How did each feature perform in each individual year AND
#    across rolling windows of 12, 18, and 24 months?"
#
# Four metrics per feature per time window:
#
#   IC (Information Coefficient)
#       Spearman correlation between feature and forward return
#       The primary measure of predictive power
#       IC > 0.05 = strong, IC > 0.03 = moderate, IC < 0.01 = dead
#
#   Sharpe Contribution
#       If you traded ONLY this feature, what risk-adjusted return?
#       Sharpe > 1.0 = excellent, Sharpe > 0.5 = acceptable
#       Negative Sharpe = feature actively hurts performance
#
#   Win Rate
#       Percentage of times the feature correctly predicted direction
#       Win rate > 55% = edge, < 50% = coin flip, < 45% = reverse signal
#
#   Quartile Spread
#       Return difference between top quartile and bottom quartile
#       Must exceed 0.5% to cover trading costs in live market
#
# Rolling windows (ChatGPT's improvement):
#   Markets don't change neatly on January 1st.
#   Rolling 12m/18m/24m windows catch gradual decay that yearly splits miss.
#   Example: a feature that died in mid-2024 shows up in yearly 2024 average
#   but the 12-month rolling window reveals exactly when it stopped working.
#
# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT
# ─────────────────────────────────────────────────────────────────────────────
#
# Per symbol:
#   {SYMBOL}_stability_yearly.csv     — IC/Sharpe/WinRate/Spread per year
#   {SYMBOL}_stability_rolling.csv    — Same metrics on rolling windows
#   {SYMBOL}_stability_summary.csv    — Combined view for the comparator
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

# Calendar year windows
YEAR_WINDOWS: Dict[str, Tuple[str, str]] = {
    "2020": ("2020-01-01", "2020-12-31"),
    "2021": ("2021-01-01", "2021-12-31"),
    "2022": ("2022-01-01", "2022-12-31"),
    "2023": ("2023-01-01", "2023-12-31"),
    "2024": ("2024-01-01", "2024-12-31"),
    "2025": ("2025-01-01", "2025-12-31"),
    "2026": ("2026-01-01", "2026-12-31"),
}

# Market regime context — printed in reports for human interpretation
YEAR_CONTEXT: Dict[str, str] = {
    "2020": "COVID crash + DeFi summer + retail entry",
    "2021": "Bull market peak — retail mania",
    "2022": "Luna + FTX collapse — bear market",
    "2023": "Recovery + institutional preparation",
    "2024": "BTC ETF approval — institutional entry",
    "2025": "Full institutional era",
    "2026": "Current year — partial data",
}

# Rolling window sizes in months
# These catch gradual decay that calendar year splits miss
ROLLING_WINDOWS_MONTHS: List[int] = [12, 18, 24]

# Symbols to process
SYMBOLS: List[str] = [
    "BTC_USDT", "ETH_USDT", "BNB_USDT", "XRP_USDT",
    "ADA_USDT", "DOGE_USDT", "SOL_USDT",
]

# Thresholds for metric classification
IC_STRONG:      float = 0.05    # IC above this = strong signal
IC_MODERATE:    float = 0.03    # IC above this = moderate signal
IC_WEAK:        float = 0.01    # IC above this = weak signal
SHARPE_GOOD:    float = 1.0     # Sharpe above this = good
SHARPE_OK:      float = 0.5     # Sharpe above this = acceptable
WIN_RATE_EDGE:  float = 0.55    # Win rate above this = edge
SPREAD_MIN:     float = 0.005   # Minimum quartile spread to cover costs

# Minimum observations per window for valid statistics
MIN_OBS: int = 500

# Columns excluded from feature testing
EXCLUDE_COLS = {
    'symbol', 'timeframe', 'timestamp', 'market_regime',
    'forward_return_1h', 'forward_return_4h', 'forward_return_24h',
    'forward_return_2h', 'forward_return_12h',
    'win_1h', 'win_4h', 'win_24h',
    'close', 'open', 'high', 'low', 'volume',
    'realized_vol', 'adx', 'volatility_state',
    'index', 'Unnamed: 0',
}

# Annualisation factor for Sharpe on 1h candles
# 1h candles: 24 × 365 = 8,760 candles per year
ANNUALISATION_FACTOR: float = np.sqrt(8_760)


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE STABILITY ANALYZER
# ═══════════════════════════════════════════════════════════════════════════════

class FeatureStabilityAnalyzer:
    """
    Measures feature stability across calendar years and rolling windows.

    Four metrics per feature per time window:
        IC          — predictive power (Spearman rank correlation)
        Sharpe      — risk-adjusted return if trading this feature alone
        Win Rate    — direction accuracy
        Spread      — quartile return spread (must exceed trading costs)

    Calendar years catch structural breaks (FTX, ETF approval).
    Rolling windows catch gradual decay between structural breaks.
    Together they give a complete picture of feature health.
    """

    def __init__(
        self,
        forward_col:  str   = "forward_return_4h",
        output_dir:   str   = "research_data/temporal",
        min_obs:      int   = MIN_OBS,
        alpha:        float = 0.05,
    ) -> None:

        self.forward_col  = forward_col       # What we predict
        self.output_dir   = Path(output_dir)  # Where to save results
        self.min_obs      = min_obs           # Minimum observations per window
        self.alpha        = alpha             # Statistical significance threshold
        self.output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("FeatureStabilityAnalyzer initialised")
        logger.info(f"  Forward return   : {forward_col}")
        logger.info(f"  Output directory : {output_dir}")
        logger.info(f"  Min observations : {min_obs:,}")
        logger.info(
            f"  Rolling windows  : "
            f"{ROLLING_WINDOWS_MONTHS} months"
        )


    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC: analyze()
    # ─────────────────────────────────────────────────────────────────────────

    def analyze(
        self,
        df:     pd.DataFrame,
        symbol: str,
    ) -> Dict[str, pd.DataFrame]:
        """
        Run full stability analysis for one symbol.

        Parameters
        ----------
        df : pd.DataFrame
            Observations DataFrame with DatetimeIndex and feature columns.

        symbol : str
            Symbol name for logging and output file naming.

        Returns
        -------
        Dict[str, pd.DataFrame]
            "yearly"  : stability metrics per calendar year
            "rolling" : stability metrics per rolling window
            "summary" : combined view for the evolution report
        """

        logger.info(f"\n{'='*70}")
        logger.info(f"STABILITY ANALYSIS — {symbol}")
        logger.info(f"{'='*70}")

        # Parse timestamp to DatetimeIndex
        df = self._parse_timestamp(df, symbol)
        if df is None:
            return {}

        # Get testable feature columns
        features = self._get_features(df)
        if not features:
            logger.warning(f"  {symbol}: no testable features found")
            return {}

        logger.info(f"  Features to analyze: {len(features)}")
        logger.info(
            f"  Data range: {df.index[0].date()} → {df.index[-1].date()}"
        )

        # Check forward return column exists
        if self.forward_col not in df.columns:
            available = [c for c in df.columns if 'forward' in c.lower()]
            logger.warning(
                f"  {symbol}: '{self.forward_col}' not found. "
                f"Available: {available}"
            )
            return {}

        # ── Calendar year analysis ────────────────────────────────────────────
        yearly_results = self._analyze_yearly(df, features, symbol)

        # ── Rolling window analysis ───────────────────────────────────────────
        rolling_results = self._analyze_rolling(df, features, symbol)

        # ── Build summary ─────────────────────────────────────────────────────
        summary = self._build_summary(yearly_results, rolling_results, symbol)

        # ── Save all outputs ──────────────────────────────────────────────────
        self._save_results(yearly_results, rolling_results, summary, symbol)

        return {
            "yearly":  yearly_results,
            "rolling": rolling_results,
            "summary": summary,
        }


    # ─────────────────────────────────────────────────────────────────────────
    # CALENDAR YEAR ANALYSIS
    # ─────────────────────────────────────────────────────────────────────────

    def _analyze_yearly(
        self,
        df:       pd.DataFrame,
        features: List[str],
        symbol:   str,
    ) -> pd.DataFrame:
        """
        Compute IC, Sharpe, win rate, and quartile spread for each calendar year.

        This is the primary analysis. Each year has its own market character
        (retail mania, bear market, institutional entry) so separating them
        reveals whether features are regime-specific or broadly applicable.
        """

        logger.info("\n  CALENDAR YEAR ANALYSIS")
        logger.info("  " + "-" * 60)

        all_rows = []

        for year, (start_str, end_str) in YEAR_WINDOWS.items():

            # Slice this year's data
            start  = pd.Timestamp(start_str, tz="UTC")
            end    = pd.Timestamp(end_str,   tz="UTC")
            yr_df  = df.loc[(df.index >= start) & (df.index <= end)].copy()
            n_obs  = len(yr_df)

            if n_obs < self.min_obs:
                logger.info(
                    f"  {year}: {n_obs:,} candles — "
                    f"below minimum {self.min_obs:,}, skipping"
                )
                continue

            context = YEAR_CONTEXT.get(year, "")
            logger.info(f"  {year}: {n_obs:,} candles — {context}")

            # Compute all four metrics for each feature
            for feature in features:
                row = self._compute_metrics(
                    yr_df, feature, symbol, year, n_obs,
                    window_type="yearly"
                )
                if row:
                    all_rows.append(row)

        yearly_df = pd.DataFrame(all_rows)
        if not yearly_df.empty:
            # Column is named 'window' (set in _compute_metrics) not 'year'
            # Sort by feature name first, then by window (year) chronologically
            yearly_df = yearly_df.sort_values(
                ["feature", "window"], ascending=[True, True]
            )

        return yearly_df


    # ─────────────────────────────────────────────────────────────────────────
    # ROLLING WINDOW ANALYSIS
    # ─────────────────────────────────────────────────────────────────────────

    def _analyze_rolling(
        self,
        df:       pd.DataFrame,
        features: List[str],
        symbol:   str,
    ) -> pd.DataFrame:
        """
        Compute metrics on rolling 12, 18, and 24-month windows.

        ChatGPT's key improvement:
            Calendar years miss gradual decay between structural breaks.
            Rolling windows capture: "Did this feature work in the
            most recent 12 months ending today?"

        We compute rolling windows ending at the MOST RECENT data point.
        This directly answers: "Is this feature alive RIGHT NOW?"
        """

        logger.info("\n  ROLLING WINDOW ANALYSIS")
        logger.info("  " + "-" * 60)

        latest = df.index.max()   # Most recent timestamp in the data
        all_rows = []

        for months in ROLLING_WINDOWS_MONTHS:

            # Rolling window: most recent N months of data
            window_start = latest - pd.DateOffset(months=months)
            window_df    = df.loc[df.index >= window_start].copy()
            n_obs        = len(window_df)

            window_label = f"last_{months}m"

            if n_obs < self.min_obs:
                logger.info(
                    f"  Rolling {months}m: {n_obs:,} candles — "
                    f"below minimum {self.min_obs:,}, skipping"
                )
                continue

            date_range = (
                f"{window_df.index[0].date()} → "
                f"{window_df.index[-1].date()}"
            )
            logger.info(
                f"  Rolling {months}m: {n_obs:,} candles "
                f"({date_range})"
            )

            # Compute all four metrics for each feature
            for feature in features:
                row = self._compute_metrics(
                    window_df, feature, symbol,
                    window_label, n_obs,
                    window_type="rolling"
                )
                if row:
                    all_rows.append(row)

        rolling_df = pd.DataFrame(all_rows)
        if not rolling_df.empty:
            rolling_df = rolling_df.sort_values(
                ["feature", "window"], ascending=[True, True]
            )

        return rolling_df


    # ─────────────────────────────────────────────────────────────────────────
    # COMPUTE FOUR METRICS
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_metrics(
        self,
        window_df:   pd.DataFrame,
        feature:     str,
        symbol:      str,
        window:      str,
        n_obs:       int,
        window_type: str,
    ) -> Optional[Dict]:
        """
        Compute IC, Sharpe, win rate, and quartile spread for one
        feature in one time window.

        All four metrics together give a complete picture:
            IC alone can be significant but economically tiny
            Sharpe shows if the signal is large enough to profit from
            Win rate shows directional accuracy
            Spread shows if the signal covers transaction costs
        """

        try:
            feat_series = window_df[feature]
            fwd_returns = window_df[self.forward_col]

            # Remove NaN from both simultaneously
            valid = (
                feat_series.notna() &
                np.isfinite(feat_series) &
                fwd_returns.notna() &
                np.isfinite(fwd_returns)
            )

            n_valid = valid.sum()
            if n_valid < 100:
                return None

            feat_clean = feat_series[valid].values
            fwd_clean  = fwd_returns[valid].values

            # ── IC: Spearman rank correlation ─────────────────────────────────
            # Measures predictive power: how well does feature predict return?
            # Spearman is rank-based — robust to extreme outliers in crypto
            ic, pvalue = stats.spearmanr(feat_clean, fwd_clean)

            # ── Sharpe: risk-adjusted return if trading this feature ──────────
            # We "trade" in the direction of the feature's sign
            # If feature is positive → we go long → return = forward_return
            # If feature is negative → we go short → return = -forward_return
            # This is a simplified but standard institutional approximation
            ic_sign          = np.sign(ic) if not np.isnan(ic) else 1.0
            strategy_returns = fwd_clean * ic_sign * np.sign(feat_clean)
            mean_ret         = np.mean(strategy_returns)
            std_ret          = np.std(strategy_returns)
            sharpe = (
                (mean_ret / (std_ret + 1e-10)) * ANNUALISATION_FACTOR
                if std_ret > 0 else 0.0
            )

            # ── Win rate: direction accuracy ──────────────────────────────────
            # Does the feature correctly predict the DIRECTION of the next move?
            # True when: (feature positive AND return positive)
            #         OR (feature negative AND return negative)
            correct_direction = (
                (feat_clean > 0) & (fwd_clean > 0)
            ) | (
                (feat_clean < 0) & (fwd_clean < 0)
            )
            win_rate = correct_direction.mean()

            # ── Quartile spread: economic significance ────────────────────────
            # Divide feature into 4 quartiles
            # Spread = mean return of top quartile minus mean return of bottom
            # Must exceed SPREAD_MIN (0.5%) to cover transaction costs
            q25 = np.percentile(feat_clean, 25)
            q75 = np.percentile(feat_clean, 75)

            bottom_q_returns = fwd_clean[feat_clean <= q25]
            top_q_returns    = fwd_clean[feat_clean >= q75]

            if len(bottom_q_returns) > 0 and len(top_q_returns) > 0:
                spread = abs(
                    np.mean(top_q_returns) - np.mean(bottom_q_returns)
                )
            else:
                spread = np.nan

            # ── Classify each metric ──────────────────────────────────────────
            ic_grade     = self._grade_ic(ic)
            sharpe_grade = self._grade_sharpe(sharpe)
            wr_grade     = self._grade_win_rate(win_rate)
            spread_grade = self._grade_spread(spread)

            # ── Overall verdict ───────────────────────────────────────────────
            # A feature needs IC AND economic significance (spread) to be tradeable
            verdict = self._overall_verdict(ic, pvalue, sharpe, spread)

            return {
                "symbol":       symbol,
                "feature":      feature,
                "window":       window,
                "window_type":  window_type,
                "n_obs":        n_valid,
                "market_context": YEAR_CONTEXT.get(window, ""),
                # IC metrics
                "ic":           round(float(ic), 6) if not np.isnan(ic) else np.nan,
                "ic_pvalue":    round(float(pvalue), 6),
                "ic_significant": pvalue < self.alpha,
                "ic_grade":     ic_grade,
                # Sharpe metrics
                "sharpe":       round(float(sharpe), 4),
                "sharpe_grade": sharpe_grade,
                # Win rate metrics
                "win_rate":     round(float(win_rate), 4),
                "win_rate_grade": wr_grade,
                # Quartile spread
                "quartile_spread": round(float(spread), 6) if not np.isnan(spread) else np.nan,
                "spread_grade": spread_grade,
                "covers_costs": (not np.isnan(spread)) and spread >= SPREAD_MIN,
                # Overall
                "verdict":      verdict,
            }

        except Exception as e:
            logger.debug(f"    {feature} / {window}: metrics failed — {e}")
            return None


    # ─────────────────────────────────────────────────────────────────────────
    # GRADING HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _grade_ic(self, ic: float) -> str:
        """Grade IC value from EXCELLENT to DEAD."""
        if np.isnan(ic):
            return "N/A"
        abs_ic = abs(ic)
        if abs_ic >= IC_STRONG:    return "EXCELLENT"
        if abs_ic >= IC_MODERATE:  return "GOOD"
        if abs_ic >= IC_WEAK:      return "WEAK"
        return "DEAD"

    def _grade_sharpe(self, sharpe: float) -> str:
        """Grade Sharpe contribution."""
        if np.isnan(sharpe):    return "N/A"
        if sharpe >= SHARPE_GOOD: return "EXCELLENT"
        if sharpe >= SHARPE_OK:   return "GOOD"
        if sharpe >= 0:           return "WEAK"
        return "NEGATIVE"

    def _grade_win_rate(self, wr: float) -> str:
        """Grade directional accuracy."""
        if np.isnan(wr):              return "N/A"
        if wr >= WIN_RATE_EDGE:       return "EDGE"
        if wr >= 0.50:                return "SLIGHT_EDGE"
        if wr >= 0.45:                return "COIN_FLIP"
        return "REVERSE"

    def _grade_spread(self, spread: float) -> str:
        """Grade economic significance (quartile spread)."""
        if np.isnan(spread):           return "N/A"
        if spread >= SPREAD_MIN * 3:   return "STRONG"
        if spread >= SPREAD_MIN:       return "COVERS_COSTS"
        if spread >= SPREAD_MIN * 0.5: return "MARGINAL"
        return "BELOW_COSTS"

    def _overall_verdict(
        self,
        ic:     float,
        pvalue: float,
        sharpe: float,
        spread: float,
    ) -> str:
        """
        Overall verdict combining all four metrics.

        A feature must pass BOTH statistical AND economic significance
        to be considered tradeable. This is the institutional standard.
        """
        ic_ok     = not np.isnan(ic) and abs(ic) >= IC_MODERATE and pvalue < self.alpha
        sharpe_ok = not np.isnan(sharpe) and sharpe >= SHARPE_OK
        spread_ok = not np.isnan(spread) and spread >= SPREAD_MIN

        if ic_ok and sharpe_ok and spread_ok:  return "STRONG_KEEP"
        if ic_ok and (sharpe_ok or spread_ok): return "KEEP"
        if ic_ok:                               return "REVIEW"
        return "DELETE"


    # ─────────────────────────────────────────────────────────────────────────
    # BUILD SUMMARY
    # ─────────────────────────────────────────────────────────────────────────

    def _build_summary(
        self,
        yearly_df:  pd.DataFrame,
        rolling_df: pd.DataFrame,
        symbol:     str,
    ) -> pd.DataFrame:
        """
        Build a compact summary with one row per feature.

        Shows: IC in each year + rolling windows + trend + recommendation.
        This is the input to the Feature Evolution Report.
        """

        if yearly_df.empty:
            return pd.DataFrame()

        features = yearly_df["feature"].unique()
        rows = []

        for feature in features:

            row = {"symbol": symbol, "feature": feature}

            # IC by year
            feat_yr = yearly_df[yearly_df["feature"] == feature]
            for _, r in feat_yr.iterrows():
                row[f"ic_{r['window']}"]     = r["ic"]
                row[f"sharpe_{r['window']}"] = r["sharpe"]
                row[f"wr_{r['window']}"]     = r["win_rate"]
                row[f"verdict_{r['window']}"]= r["verdict"]

            # IC by rolling window
            if not rolling_df.empty:
                feat_roll = rolling_df[rolling_df["feature"] == feature]
                for _, r in feat_roll.iterrows():
                    row[f"ic_{r['window']}"]     = r["ic"]
                    row[f"sharpe_{r['window']}"] = r["sharpe"]
                    row[f"verdict_{r['window']}"]= r["verdict"]

            # Trend: is IC increasing or decreasing in recent years?
            recent_ics = [
                row.get(f"ic_{y}", np.nan)
                for y in ["2024", "2025", "2026"]
                if not np.isnan(row.get(f"ic_{y}", np.nan))
            ]
            early_ics = [
                row.get(f"ic_{y}", np.nan)
                for y in ["2020", "2021", "2022"]
                if not np.isnan(row.get(f"ic_{y}", np.nan))
            ]

            mean_recent = np.nanmean(np.abs(recent_ics)) if recent_ics else 0.0
            mean_early  = np.nanmean(np.abs(early_ics))  if early_ics  else 0.0

            # Trend classification
            if mean_recent < 0.01:
                row["trend"] = "DEAD"
            elif mean_recent > mean_early * 1.3 and mean_recent > 0.03:
                row["trend"] = "GAINING"
            elif mean_early > mean_recent * 1.3 and mean_early > 0.03:
                row["trend"] = "DECAYING"
            elif abs(mean_recent - mean_early) < 0.01:
                row["trend"] = "STABLE"
            else:
                row["trend"] = "VOLATILE"

            # Final recommendation based on recent period only
            if mean_recent >= IC_STRONG:
                row["live_recommendation"] = "TRADE NOW"
            elif mean_recent >= IC_MODERATE:
                row["live_recommendation"] = "WATCH"
            elif mean_recent >= IC_WEAK:
                row["live_recommendation"] = "RESEARCH"
            else:
                row["live_recommendation"] = "AVOID"

            rows.append(row)

        summary_df = pd.DataFrame(rows)

        # Sort by absolute recent IC — most relevant at top
        recent_ic_cols = [
            c for c in summary_df.columns
            if c.startswith("ic_") and
            any(y in c for y in ["2024", "2025", "2026", "last_"])
        ]
        if recent_ic_cols:
            summary_df["_abs_recent"] = summary_df[recent_ic_cols].abs().mean(axis=1)
            summary_df = summary_df.sort_values("_abs_recent", ascending=False)
            summary_df = summary_df.drop(columns=["_abs_recent"])

        return summary_df


    # ─────────────────────────────────────────────────────────────────────────
    # SAVE RESULTS
    # ─────────────────────────────────────────────────────────────────────────

    def _save_results(
        self,
        yearly_df:  pd.DataFrame,
        rolling_df: pd.DataFrame,
        summary_df: pd.DataFrame,
        symbol:     str,
    ) -> None:
        """Save all three result DataFrames to CSV."""

        if not yearly_df.empty:
            p = self.output_dir / f"{symbol}_stability_yearly.csv"
            yearly_df.to_csv(p, index=False)
            logger.info(f"  Saved: {p}")

        if not rolling_df.empty:
            p = self.output_dir / f"{symbol}_stability_rolling.csv"
            rolling_df.to_csv(p, index=False)
            logger.info(f"  Saved: {p}")

        if not summary_df.empty:
            p = self.output_dir / f"{symbol}_stability_summary.csv"
            summary_df.to_csv(p, index=False)
            logger.info(f"  Saved: {p}")


    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_timestamp(
        self, df: pd.DataFrame, symbol: str
    ) -> Optional[pd.DataFrame]:
        """Parse timestamp and set as UTC DatetimeIndex."""
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
            logger.warning(f"  {symbol}: could not parse timestamp")
            return None
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.sort_index(inplace=True)
        return df

    def _get_features(self, df: pd.DataFrame) -> List[str]:
        """Return testable numeric feature columns."""
        return [
            c for c in df.columns
            if c not in EXCLUDE_COLS
            and df[c].dtype in [np.float64, np.float32, np.int64, np.int32]
            and not df[c].isna().all()
        ]


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_stability_analysis(
    observations_dir: str = "research_data",
    output_dir:       str = "research_data/temporal",
    forward_col:      str = "forward_return_4h",
    symbols:          List[str] = None,
) -> Dict[str, Dict]:
    """
    Run stability analysis for all symbols.

    Run from project root:
        python -m bot.research.feature_stability_analyzer
    """

    analyzer = FeatureStabilityAnalyzer(
        forward_col=forward_col,
        output_dir=output_dir,
    )

    symbols  = symbols or SYMBOLS
    results  = {}

    logger.info("=" * 70)
    logger.info("FEATURE STABILITY ANALYZER — STARTING")
    logger.info("=" * 70)

    for symbol in symbols:
        obs_path = Path(observations_dir) / f"{symbol}_observations.csv"
        if not obs_path.exists():
            logger.warning(f"  {symbol}: observations not found at {obs_path}")
            continue
        try:
            df = pd.read_csv(obs_path)
            results[symbol] = analyzer.analyze(df, symbol)
        except Exception as e:
            logger.error(f"  {symbol}: failed — {e}")

    logger.info("\n" + "=" * 70)
    logger.info("STABILITY ANALYSIS COMPLETE")
    logger.info(f"Results saved to: {output_dir}")
    logger.info("=" * 70)

    return results


if __name__ == "__main__":
    run_stability_analysis()