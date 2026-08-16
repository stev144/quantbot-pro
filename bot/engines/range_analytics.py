"""
bot/engines/range_analytics.py  (v2 — institutional grade)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Historical Range Analysis Engine — Institutional Grade

UPGRADE SUMMARY (v1 → v2)
──────────────────────────
Six improvements added based on ChatGPT quantitative review:

  1. FORWARD RETURN ENGINE
     Measures what actually happens AFTER each range condition.
     Computes avg_return_1h/4h/24h segmented by range_used buckets.
     This is where TRUE predictive edge is discovered — not from
     measuring conditions, but from proving those conditions predict
     future price movement.

  2. VOLATILITY CLUSTERING
     Markets trend in volatility as much as in price.
     New metrics: vol_acceleration, vol_persistence, rolling_variance.
     vol_acceleration = current_7d_vol / previous_7d_vol
     Tells you whether volatility is speeding up or slowing down.

  3. REGIME PERSISTENCE PROBABILITY
     How long does each volatility state typically last?
     Computed from the historical state sequence.
     Now the bot knows: "EXPANDING usually lasts 2.4 days —
     it has already been expanding 2 days, exhaustion likely."

  4. DIRECTIONAL EFFICIENCY
     efficiency = abs(close - open) / (high - low)
     High efficiency = clean directional candle.
     Low efficiency = noisy whipsaw candle.
     Filters fake volatility from real momentum.

  5. REALIZED vs EXPECTED RANGE
     Conditions on: weekday, asset, volatility regime.
     Monday BTC behaves differently from Sunday BTC.
     Now the module computes conditional expected ranges.

  6. TIME-OF-DAY / SESSION BEHAVIOR
     Classifies intraday candles into sessions:
     ASIA / LONDON / NEW_YORK / OVERLAP
     Computes session-level ADR and volatility patterns.
     Requires hourly df passed as session_df parameter.

All v1 public methods remain unchanged — fully backward compatible.
New fields added to RangeProfile dataclass.
New public methods added alongside existing ones.

Usage
─────
  from bot.engines.range_analytics import RangeAnalytics

  ra      = RangeAnalytics()
  profile = ra.analyse(daily_df, current_price, symbol="BTCUSDT",
                       hourly_df=hourly_df)

  # All v1 methods still work
  score = ra.range_score(profile, "LONG")
  tags  = ra.backtest_tags(profile)

  # New v2 methods
  fwd   = ra.forward_returns(daily_df, lookback=30)
  clust = ra.volatility_cluster_report(daily_df)
  sess  = ra.session_report(hourly_df, symbol="BTCUSDT")
  ra.print_report(profile, direction="LONG")   # now shows all v2 fields
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — DATA CLASSES
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class DailyRangeRow:
    """One day's range statistics — used in the per-day audit breakdown."""
    date:               str
    high:               float
    low:                float
    range_dollars:      float
    range_pct:          float
    vs_adr:             float
    broke_adr:          bool
    # v2 additions
    efficiency:         float    # abs(close-open)/(high-low) — directional quality
    vol_state:          str      # COMPRESSED / NORMAL / EXPANDING on that day
    weekday:            str      # MON / TUE / WED / THU / FRI / SAT / SUN
    forward_return_1d:  float    # actual % return on the NEXT day (populated in analyse)


@dataclass
class ForwardReturnBucket:
    """
    Forward return statistics for one range_used band.
    Answers: "After price has consumed X% of its ADR,
              what does it do over the next N hours?"
    """
    bucket_label:       str      # e.g. "0–30%"
    range_low:          float
    range_high:         float
    sample_size:        int
    avg_return_1d:      float    # mean % return next day
    avg_return_2d:      float    # mean % return in 2 days
    median_return_1d:   float
    positive_rate:      float    # fraction of times next-day return was positive
    avg_next_range:     float    # avg range of next candle (volatility forecast)


@dataclass
class VolatilityCluster:
    """
    Volatility clustering statistics for the asset.
    Tells you how long each state lasts and how states transition.
    """
    # ── Duration statistics ────────────────────────────────────────────────
    compressed_avg_days:  float    # avg consecutive days in COMPRESSED
    normal_avg_days:      float
    expanding_avg_days:   float

    # ── Current regime duration ────────────────────────────────────────────
    current_state:        str
    current_state_days:   int      # how many consecutive days in current state
    exhaustion_prob:      float    # probability current state ends tomorrow (0–1)

    # ── Transition probabilities ───────────────────────────────────────────
    # P(next_state | current_state) — 3×3 Markov matrix as nested dict
    transition_matrix:    Dict[str, Dict[str, float]]

    # ── Acceleration ──────────────────────────────────────────────────────
    vol_acceleration:     float    # current_7d_vol / previous_7d_vol
    vol_momentum:         str      # "ACCELERATING" | "DECELERATING" | "STABLE"

    # ── Rolling variance ──────────────────────────────────────────────────
    rolling_variance_7d:  float    # variance of daily ranges over last 7 days
    rolling_variance_30d: float    # variance of daily ranges over last 30 days
    variance_ratio:       float    # 7d / 30d — rising = clustering in progress


@dataclass
class DirectionalEfficiency:
    """
    Measures how clean (directional) vs noisy (whipsaw) recent price action is.
    efficiency = abs(close - open) / (high - low)
    Range: 0.0 (all wick, no direction) → 1.0 (full marubozu candle)
    """
    avg_efficiency_7d:    float    # mean efficiency last 7 candles
    avg_efficiency_30d:   float    # mean efficiency last 30 candles
    efficiency_trend:     str      # "IMPROVING" | "DETERIORATING" | "STABLE"
    current_efficiency:   float    # today's candle efficiency
    high_efficiency_rate: float    # fraction of days with efficiency > 0.60
    fake_vol_rate:        float    # fraction of days efficiency < 0.30 (noisy)


@dataclass
class ConditionalExpectedRange:
    """
    Expected range conditioned on weekday and volatility regime.
    Example: BTC on Mondays in COMPRESSED state → avg ADR $1,840
    """
    weekday:              str      # "MON" | "TUE" | ...
    vol_state:            str      # "COMPRESSED" | "NORMAL" | "EXPANDING"
    expected_range:       float    # conditional ADR in dollars
    expected_range_pct:   float    # conditional ADR as %
    sample_size:          int      # how many historical days matched condition
    reliability:          str      # "HIGH" (N≥10) | "MEDIUM" (N≥5) | "LOW" (N<5)


@dataclass
class SessionProfile:
    """
    Intraday session statistics. Requires hourly OHLCV data.
    Sessions: ASIA(00-08 UTC), LONDON(08-12 UTC),
              NY_OPEN(12-16 UTC), OVERLAP(12-17 UTC), NY_CLOSE(16-00 UTC)
    """
    session_name:         str
    utc_start:            int      # hour UTC
    utc_end:              int      # hour UTC
    avg_range_dollars:    float    # avg intraday range during this session
    avg_range_pct:        float
    avg_volume:           float
    expansion_rate:       float    # fraction of sessions where range > overall ADR/4
    typical_direction:    str      # "BULLISH" | "BEARISH" | "NEUTRAL"
    direction_consistency:float    # 0–1, how often direction matches typical


@dataclass
class RangeProfile:
    """
    Complete range analysis result — v2 extended.

    All v1 fields preserved exactly.
    All v2 fields added at the bottom — backward compatible.
    """

    # ════════════════════════════════════════
    # V1 FIELDS (unchanged)
    # ════════════════════════════════════════

    symbol:                  str
    sample_days:             int
    adr_dollars:             float
    adr_percent:             float
    adr_std:                 float
    adr_median:              float
    range_respect_rate:      float
    expansion_factor:        float
    max_expansion:           float
    consecutive_expansions:  int
    current_range_used:      float
    range_bias:              float
    today_range_dollars:     float
    remaining_range:         float
    volatility_state:        str
    volatility_ratio:        float
    today_vs_percentile:     float
    avg_high_time_pct:       float
    avg_low_time_pct:        float
    grid_spacing:            float
    grid_levels:             int
    grid_capital_ratio:      float
    daily_breakdown:         List[DailyRangeRow] = field(default_factory=list)

    # ════════════════════════════════════════
    # V2 FIELDS — new institutional additions
    # ════════════════════════════════════════

    # ── Improvement 1: Forward returns ────────────────────────────────────
    forward_return_buckets:  List[ForwardReturnBucket] = field(default_factory=list)
    # Current day's expected forward return based on range_used bucket
    expected_next_day_return: float = 0.0
    predicted_direction:      str   = "NEUTRAL"   # BULLISH / BEARISH / NEUTRAL

    # ── Improvement 2: Volatility clustering ─────────────────────────────
    vol_cluster:             Optional[VolatilityCluster] = None
    vol_acceleration:        float = 1.0    # >1 = accelerating, <1 = decelerating
    vol_momentum:            str   = "STABLE"

    # ── Improvement 3: Regime persistence ────────────────────────────────
    current_state_days:      int   = 0      # days in current vol state
    exhaustion_prob:         float = 0.0    # prob state ends soon
    expected_state_duration: float = 0.0   # historical avg days for this state

    # ── Improvement 4: Directional efficiency ────────────────────────────
    efficiency:              Optional[DirectionalEfficiency] = None
    current_efficiency:      float = 0.5
    efficiency_signal:       str   = "NEUTRAL"  # CLEAN / NOISY / NEUTRAL

    # ── Improvement 5: Conditional expected range ────────────────────────
    conditional_expected_range: Optional[ConditionalExpectedRange] = None
    realized_vs_expected:    float = 1.0    # today_range / conditional_expected

    # ── Improvement 6: Session behavior ──────────────────────────────────
    session_profiles:        List[SessionProfile] = field(default_factory=list)
    current_session:         str   = "UNKNOWN"
    session_expansion_likely:bool  = False


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — MAIN CLASS
# ═════════════════════════════════════════════════════════════════════════════

class RangeAnalytics:
    """
    Institutional-grade range analysis engine.

    Parameters
    ──────────
    lookback_days    : int   days of history for core ADR (default 30)
    adr_threshold    : float multiplier defining "broke ADR" (default 1.15)
    grid_pct         : float fraction of ADR per grid level (default 0.10)
    min_grid_levels  : int   floor for grid count (default 4)
    max_grid_levels  : int   ceiling for grid count (default 20)
    efficiency_clean : float efficiency above this = clean candle (default 0.60)
    efficiency_noisy : float efficiency below this = noisy candle (default 0.30)
    """

    # ── Session definitions (UTC hours) ──────────────────────────────────────
    SESSIONS = {
        "ASIA":     (0,  8),
        "LONDON":   (8,  12),
        "NY_OPEN":  (12, 17),
        "NY_CLOSE": (17, 24),
    }

    WEEKDAY_MAP = {0:"MON", 1:"TUE", 2:"WED", 3:"THU", 4:"FRI", 5:"SAT", 6:"SUN"}

    def __init__(
        self,
        lookback_days:    int   = 30,
        adr_threshold:    float = 1.15,
        grid_pct:         float = 0.10,
        min_grid_levels:  int   = 4,
        max_grid_levels:  int   = 20,
        efficiency_clean: float = 0.60,
        efficiency_noisy: float = 0.30,
    ) -> None:
        self.lookback_days    = lookback_days
        self.adr_threshold    = adr_threshold
        self.grid_pct         = grid_pct
        self.min_grid_levels  = min_grid_levels
        self.max_grid_levels  = max_grid_levels
        self.efficiency_clean = efficiency_clean
        self.efficiency_noisy = efficiency_noisy

    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC: MAIN ENTRY POINT (v1 signature preserved + optional hourly_df)
    # ─────────────────────────────────────────────────────────────────────

    def analyse(
        self,
        daily_df:      pd.DataFrame,
        current_price: float,
        symbol:        str                    = "UNKNOWN",
        hourly_df:     Optional[pd.DataFrame] = None,
    ) -> RangeProfile:
        """
        Run the full range analysis and return a RangeProfile.

        Parameters
        ──────────
        daily_df      : daily OHLCV DataFrame (≥ 10 rows, recommended ≥ 30)
        current_price : latest traded price
        symbol        : trading pair string
        hourly_df     : optional hourly OHLCV for session analysis (v2)

        Returns
        ───────
        RangeProfile with all v1 + v2 fields populated.
        """
        self._validate(daily_df)

        df  = daily_df.tail(self.lookback_days).copy()
        df  = self._add_range_columns(df)

        # ── V1: Core ADR ──────────────────────────────────────────────────
        adr_dollars  = float(df["daily_range"].mean())
        adr_pct      = float(df["daily_range_pct"].mean())
        adr_std      = float(df["daily_range"].std())
        adr_median   = float(df["daily_range"].median())

        # ── V1: Range respect ─────────────────────────────────────────────
        threshold    = adr_dollars * self.adr_threshold
        broke_mask   = df["daily_range"] > threshold
        respect_rate = float((~broke_mask).sum()) / len(df)

        broke_df     = df[broke_mask]
        if len(broke_df) > 0:
            expansion_factor = float((broke_df["daily_range"] / adr_dollars).mean())
            max_expansion    = float((broke_df["daily_range"] / adr_dollars).max())
        else:
            expansion_factor = 1.0
            max_expansion    = 1.0

        consec = self._max_consecutive(broke_mask.tolist())

        # ── V1: Volatility state ──────────────────────────────────────────
        recent_adr  = float(df.tail(7)["daily_range"].mean())
        vol_ratio   = recent_adr / adr_dollars if adr_dollars > 0 else 1.0
        vol_state   = self._volatility_state(vol_ratio)

        # ── V1: Intraday metrics ──────────────────────────────────────────
        today       = df.iloc[-1]
        today_range = float(today["daily_range"])
        range_used  = today_range / adr_dollars if adr_dollars > 0 else 0.0
        today_span  = float(today["high"] - today["low"])

        price_clamp = max(float(today["low"]), min(current_price, float(today["high"])))
        range_bias  = (price_clamp - float(today["low"])) / today_span if today_span > 0 else 0.5

        remaining   = max(0.0, adr_dollars - today_range)
        percentile  = float((df["daily_range"] <= today_range).sum() / len(df))

        # ── V1: Grid recommendation ───────────────────────────────────────
        spacing, levels, cap_ratio = self._grid_recommendation(adr_dollars, vol_state)

        # ══════════════════════════════════════════════════════
        # V2 ADDITIONS
        # ══════════════════════════════════════════════════════

        # ── V2.1: Forward returns ─────────────────────────────────────────
        fwd_buckets  = self._compute_forward_returns(df)
        exp_next, pred_dir = self._expected_next_return(fwd_buckets, range_used)

        # ── V2.2: Volatility clustering ───────────────────────────────────
        vol_cluster  = self._compute_vol_cluster(df, vol_state, adr_dollars)

        # V2.2 surface fields
        vol_accel    = vol_cluster.vol_acceleration
        vol_momentum = vol_cluster.vol_momentum

        # ── V2.3: Regime persistence ──────────────────────────────────────
        cur_state_days  = vol_cluster.current_state_days
        exhaustion_prob = vol_cluster.exhaustion_prob
        exp_duration    = {
            "COMPRESSED": vol_cluster.compressed_avg_days,
            "NORMAL":     vol_cluster.normal_avg_days,
            "EXPANDING":  vol_cluster.expanding_avg_days,
        }.get(vol_state, 0.0)

        # ── V2.4: Directional efficiency ──────────────────────────────────
        eff_obj      = self._compute_efficiency(df)
        eff_signal   = self._efficiency_signal(eff_obj)

        # ── V2.5: Conditional expected range ─────────────────────────────
        cond_exp     = self._conditional_expected_range(df, vol_state)
        realized_vs  = (today_range / cond_exp.expected_range
                        if cond_exp and cond_exp.expected_range > 0 else 1.0)

        # ── V2.6: Session behavior ────────────────────────────────────────
        session_profiles  = []
        current_session   = "UNKNOWN"
        session_exp_likely= False
        if hourly_df is not None and len(hourly_df) >= 24:
            session_profiles   = self._compute_session_profiles(hourly_df, adr_dollars)
            current_session    = self._current_session()
            session_exp_likely = self._session_expansion_likely(
                session_profiles, current_session
            )

        # ── V2 enhanced daily breakdown ───────────────────────────────────
        breakdown = self._build_breakdown_v2(df, adr_dollars)

        return RangeProfile(
            # ── V1 fields ──────────────────────────────────────────────────
            symbol                   = symbol,
            sample_days              = len(df),
            adr_dollars              = round(adr_dollars,      2),
            adr_percent              = round(adr_pct,          4),
            adr_std                  = round(adr_std,          2),
            adr_median               = round(adr_median,       2),
            range_respect_rate       = round(respect_rate,     4),
            expansion_factor         = round(expansion_factor, 3),
            max_expansion            = round(max_expansion,    3),
            consecutive_expansions   = consec,
            current_range_used       = round(range_used,       4),
            range_bias               = round(range_bias,       4),
            today_range_dollars      = round(today_range,      2),
            remaining_range          = round(remaining,        2),
            volatility_state         = vol_state,
            volatility_ratio         = round(vol_ratio,        4),
            today_vs_percentile      = round(percentile,       4),
            avg_high_time_pct        = 0.0,
            avg_low_time_pct         = 0.0,
            grid_spacing             = round(spacing,          2),
            grid_levels              = levels,
            grid_capital_ratio       = round(cap_ratio,        4),
            daily_breakdown          = breakdown,
            # ── V2 fields ──────────────────────────────────────────────────
            forward_return_buckets   = fwd_buckets,
            expected_next_day_return = round(exp_next,         5),
            predicted_direction      = pred_dir,
            vol_cluster              = vol_cluster,
            vol_acceleration         = round(vol_accel,        4),
            vol_momentum             = vol_momentum,
            current_state_days       = cur_state_days,
            exhaustion_prob          = round(exhaustion_prob,  4),
            expected_state_duration  = round(exp_duration,     2),
            efficiency               = eff_obj,
            current_efficiency       = round(eff_obj.current_efficiency, 4),
            efficiency_signal        = eff_signal,
            conditional_expected_range = cond_exp,
            realized_vs_expected     = round(realized_vs,      4),
            session_profiles         = session_profiles,
            current_session          = current_session,
            session_expansion_likely = session_exp_likely,
        )

    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC: RANGE SCORE (v1 preserved + v2 enhancements)
    # ─────────────────────────────────────────────────────────────────────

    def range_score(
        self,
        profile:   RangeProfile,
        direction: str = "LONG",
    ) -> float:
        """
        Returns a float 0.0–1.0.

        Scoring breakdown (v2)
        ──────────────────────
        Range consumption      0.00–0.30   (v1, reduced weight)
        Directional bias       0.00–0.20   (v1, reduced weight)
        Volatility state       0.00–0.15   (v1)
        Respect rate bonus     0.00–0.08   (v1)
        ─── v2 additions ────────────────
        Forward return signal  0.00–0.12   predicted direction matches trade
        Efficiency signal      0.00–0.08   clean candle = higher confidence
        Exhaustion protection  0.00–0.05   penalise if vol state near exhaustion
        Session bonus          0.00–0.02   session known to expand in direction
        ──────────────────────────────────
        Maximum                      1.00
        """
        score = 0.0

        # ── 1. Range consumption (0.30 max) ───────────────────────────────
        ru = profile.current_range_used
        if ru < 0.30:
            score += 0.30
        elif ru < 0.50:
            score += 0.22
        elif ru < 0.65:
            score += 0.12
        elif ru < 0.80:
            score += 0.04
        # else: 0.00 — near exhaustion

        # ── 2. Directional bias (0.20 max) ────────────────────────────────
        bias = profile.range_bias
        if direction.upper() == "LONG":
            if bias < 0.25:
                score += 0.20
            elif bias < 0.45:
                score += 0.12
            elif bias < 0.60:
                score += 0.05
        else:
            if bias > 0.75:
                score += 0.20
            elif bias > 0.55:
                score += 0.12
            elif bias > 0.40:
                score += 0.05

        # ── 3. Volatility state (0.15 max) ────────────────────────────────
        if profile.volatility_state == "NORMAL":
            score += 0.15
        elif profile.volatility_state == "COMPRESSED":
            score += 0.08
        # EXPANDING: 0.00

        # ── 4. Respect rate (0.08 max) ────────────────────────────────────
        score += profile.range_respect_rate * 0.08

        # ── 5. Forward return prediction (0.12 max) ───────────────────────
        # Only add score if predicted direction matches the trade
        if profile.predicted_direction == "BULLISH" and direction.upper() == "LONG":
            score += 0.12
        elif profile.predicted_direction == "BEARISH" and direction.upper() == "SHORT":
            score += 0.12
        elif profile.predicted_direction == "NEUTRAL":
            score += 0.04   # neutral is slightly positive — no headwind

        # ── 6. Efficiency signal (0.08 max) ───────────────────────────────
        if profile.efficiency_signal == "CLEAN":
            score += 0.08   # recent candles are directional — trend is real
        elif profile.efficiency_signal == "NEUTRAL":
            score += 0.04
        # NOISY: 0.00 — fake volatility, avoid

        # ── 7. Exhaustion protection (0.05 max) ───────────────────────────
        # If the current volatility state is likely near its end,
        # reduce score — conditions may shift against the trade
        if profile.exhaustion_prob < 0.25:
            score += 0.05   # state is young, likely to persist
        elif profile.exhaustion_prob < 0.55:
            score += 0.02
        # exhaustion_prob >= 0.55: 0.00 — state likely ending soon

        # ── 8. Session expansion bonus (0.02 max) ─────────────────────────
        if profile.session_expansion_likely:
            score += 0.02

        return round(min(score, 1.0), 4)

    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC: BACKTEST TAGS (v1 preserved + v2 additions)
    # ─────────────────────────────────────────────────────────────────────

    def backtest_tags(self, profile: RangeProfile) -> Dict[str, object]:
        """
        Returns a flat dict to attach to every trade record.
        All v1 keys preserved. V2 keys added.
        """
        tags = {
            # ── V1 ──────────────────────────────────────────────────────
            "range_used_at_entry":    profile.current_range_used,
            "range_bias_at_entry":    profile.range_bias,
            "volatility_state":       profile.volatility_state,
            "volatility_ratio":       profile.volatility_ratio,
            "adr_at_entry":           profile.adr_dollars,
            "adr_pct_at_entry":       profile.adr_percent,
            "respect_rate_at_entry":  profile.range_respect_rate,
            "expansion_factor":       profile.expansion_factor,
            "today_vs_percentile":    profile.today_vs_percentile,
            "remaining_range":        profile.remaining_range,
            "consecutive_expansions": profile.consecutive_expansions,
            # ── V2 ──────────────────────────────────────────────────────
            "predicted_direction":    profile.predicted_direction,
            "expected_next_return":   profile.expected_next_day_return,
            "vol_acceleration":       profile.vol_acceleration,
            "vol_momentum":           profile.vol_momentum,
            "current_state_days":     profile.current_state_days,
            "exhaustion_prob":        profile.exhaustion_prob,
            "current_efficiency":     profile.current_efficiency,
            "efficiency_signal":      profile.efficiency_signal,
            "realized_vs_expected":   profile.realized_vs_expected,
            "current_session":        profile.current_session,
            "session_exp_likely":     profile.session_expansion_likely,
        }
        return tags

    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC: FORWARD RETURNS (standalone research method)
    # ─────────────────────────────────────────────────────────────────────

    def forward_returns(
        self,
        daily_df:  pd.DataFrame,
        lookback:  int = 60,
    ) -> List[ForwardReturnBucket]:
        """
        Compute forward return statistics segmented by range_used bucket.
        Call this standalone to run a forward-return research study.

        Parameters
        ──────────
        daily_df : daily OHLCV DataFrame (needs ≥ lookback + 2 rows)
        lookback : number of days to analyse

        Returns
        ───────
        List of ForwardReturnBucket — one per range_used band.

        Example output interpretation
        ──────────────────────────────
          bucket "0–30%"   avg_return_1d = +1.8%  positive_rate = 0.63
          bucket "80–100%" avg_return_1d = -0.3%  positive_rate = 0.41
          → Entering when < 30% of range used has significantly better
            forward returns than entering at range exhaustion.
        """
        self._validate(daily_df)
        df = daily_df.tail(lookback + 2).copy()
        df = self._add_range_columns(df)
        adr = float(df["daily_range"].mean())
        return self._compute_forward_returns(df, adr_override=adr)

    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC: VOLATILITY CLUSTER REPORT (standalone)
    # ─────────────────────────────────────────────────────────────────────

    def volatility_cluster_report(
        self,
        daily_df: pd.DataFrame,
    ) -> VolatilityCluster:
        """
        Standalone volatility clustering analysis.
        Returns a VolatilityCluster with regime durations,
        transition probabilities, and acceleration metrics.
        """
        self._validate(daily_df)
        df       = daily_df.tail(self.lookback_days).copy()
        df       = self._add_range_columns(df)
        adr      = float(df["daily_range"].mean())
        recent   = float(df.tail(7)["daily_range"].mean())
        ratio    = recent / adr if adr > 0 else 1.0
        state    = self._volatility_state(ratio)
        return self._compute_vol_cluster(df, state, adr)

    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC: SESSION REPORT (standalone)
    # ─────────────────────────────────────────────────────────────────────

    def session_report(
        self,
        hourly_df: pd.DataFrame,
        symbol:    str = "UNKNOWN",
    ) -> List[SessionProfile]:
        """
        Compute session-level volatility and direction statistics.
        Requires a DataFrame of hourly OHLCV candles with a
        DatetimeIndex in UTC or a 'datetime' column parseable as UTC.

        Returns a list of SessionProfile, one per session.
        """
        if len(hourly_df) < 24:
            logger.warning("session_report: need ≥ 24 hourly rows")
            return []
        daily_adr = float((hourly_df["high"] - hourly_df["low"]).mean()) * 24
        return self._compute_session_profiles(hourly_df, daily_adr)

    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC: COMPARE ASSETS (v1 preserved + v2 extra columns)
    # ─────────────────────────────────────────────────────────────────────

    def compare_assets(
        self,
        asset_data: Dict[str, tuple],
    ) -> pd.DataFrame:
        """
        Multi-asset comparison DataFrame.
        asset_data = {symbol: (daily_df, current_price)}
        or          {symbol: (daily_df, current_price, hourly_df)}
        """
        rows = []
        for symbol, data in asset_data.items():
            try:
                daily_df      = data[0]
                current_price = data[1]
                hourly_df     = data[2] if len(data) > 2 else None

                p = self.analyse(daily_df, current_price, symbol, hourly_df)
                rows.append({
                    # ── V1 ────────────────────────────────────────────────
                    "symbol":              p.symbol,
                    "adr_dollars":         p.adr_dollars,
                    "adr_percent":         p.adr_percent,
                    "range_used":          p.current_range_used,
                    "range_bias":          p.range_bias,
                    "volatility_state":    p.volatility_state,
                    "volatility_ratio":    p.volatility_ratio,
                    "respect_rate":        p.range_respect_rate,
                    "expansion_factor":    p.expansion_factor,
                    "today_pct":           p.today_vs_percentile,
                    "range_score_long":    self.range_score(p, "LONG"),
                    "range_score_short":   self.range_score(p, "SHORT"),
                    "grid_spacing":        p.grid_spacing,
                    "grid_levels":         p.grid_levels,
                    # ── V2 ────────────────────────────────────────────────
                    "predicted_direction": p.predicted_direction,
                    "exp_next_return":     p.expected_next_day_return,
                    "vol_acceleration":    p.vol_acceleration,
                    "vol_momentum":        p.vol_momentum,
                    "current_state_days":  p.current_state_days,
                    "exhaustion_prob":     p.exhaustion_prob,
                    "efficiency_signal":   p.efficiency_signal,
                    "current_efficiency":  p.current_efficiency,
                    "realized_vs_exp":     p.realized_vs_expected,
                    "current_session":     p.current_session,
                    "session_exp_likely":  p.session_expansion_likely,
                })
            except Exception as e:
                logger.warning("compare_assets: %s skipped — %s", symbol, e)

        if not rows:
            return pd.DataFrame()

        out = pd.DataFrame(rows)
        out.sort_values("range_score_long", ascending=False, inplace=True)
        out.reset_index(drop=True, inplace=True)
        return out

    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC: PRINT REPORT (extended for v2)
    # ─────────────────────────────────────────────────────────────────────

    def print_report(
        self,
        profile:   RangeProfile,
        direction: str = "LONG",
    ) -> None:
        """Prints a full institutional-grade report for one RangeProfile."""
        score = self.range_score(profile, direction)
        w     = 68
        print("=" * w)
        print(f"  RANGE ANALYSIS v2 — {profile.symbol}  [{direction}]")
        print("=" * w)

        # ── V1 section ────────────────────────────────────────────────────
        print(f"  {'Sample days':<32}: {profile.sample_days}")
        print(f"  {'ADR (dollars)':<32}: ${profile.adr_dollars:,.2f}  "
              f"±${profile.adr_std:,.2f}")
        print(f"  {'ADR (percent)':<32}: {profile.adr_percent:.3%}")
        print(f"  {'ADR median':<32}: ${profile.adr_median:,.2f}")
        print("-" * w)
        print(f"  {'Range respect rate':<32}: {profile.range_respect_rate:.1%}")
        print(f"  {'Expansion factor':<32}: {profile.expansion_factor:.2f}×  "
              f"(max {profile.max_expansion:.2f}×)")
        print(f"  {'Consec. ADR breaks':<32}: {profile.consecutive_expansions} days")
        print("-" * w)
        print(f"  {'Today range used':<32}: {profile.current_range_used:.1%} of ADR")
        print(f"  {'Today range dollars':<32}: ${profile.today_range_dollars:,.2f}")
        print(f"  {'Remaining range':<32}: ${profile.remaining_range:,.2f}")
        print(f"  {'Range bias (0=low,1=high)':<32}: {profile.range_bias:.3f}")
        print(f"  {'Today vs percentile':<32}: {profile.today_vs_percentile:.1%}")
        print("-" * w)

        # ── V2.1 Forward returns ──────────────────────────────────────────
        print(f"  FORWARD RETURN PREDICTION")
        print(f"  {'Predicted direction':<32}: {profile.predicted_direction}")
        print(f"  {'Expected next-day return':<32}: {profile.expected_next_day_return:+.3%}")
        if profile.forward_return_buckets:
            print(f"  {'Bucket':>12} {'WR':>7} {'Avg 1d':>9} {'Avg 2d':>9} {'N':>5}")
            for b in profile.forward_return_buckets:
                print(
                    f"  {b.bucket_label:>12} "
                    f"{b.positive_rate:>7.1%} "
                    f"{b.avg_return_1d:>+9.3%} "
                    f"{b.avg_return_2d:>+9.3%} "
                    f"{b.sample_size:>5}"
                )
        print("-" * w)

        # ── V2.2 + V2.3 Volatility clustering & persistence ───────────────
        print(f"  VOLATILITY REGIME")
        print(f"  {'Volatility state':<32}: {profile.volatility_state}  "
              f"(ratio {profile.volatility_ratio:.3f})")
        print(f"  {'Vol acceleration':<32}: {profile.vol_acceleration:.3f}  "
              f"({profile.vol_momentum})")
        print(f"  {'Current state duration':<32}: {profile.current_state_days} days")
        print(f"  {'Expected state duration':<32}: {profile.expected_state_duration:.1f} days")
        print(f"  {'Exhaustion probability':<32}: {profile.exhaustion_prob:.1%}")
        if profile.vol_cluster:
            vc = profile.vol_cluster
            print(f"  {'Rolling variance 7d/30d':<32}: "
                  f"{vc.rolling_variance_7d:.2f} / {vc.rolling_variance_30d:.2f}  "
                  f"(ratio {vc.variance_ratio:.2f})")
        print("-" * w)

        # ── V2.4 Directional efficiency ───────────────────────────────────
        print(f"  DIRECTIONAL EFFICIENCY")
        print(f"  {'Current candle efficiency':<32}: {profile.current_efficiency:.3f}  "
              f"[{profile.efficiency_signal}]")
        if profile.efficiency:
            ef = profile.efficiency
            print(f"  {'Avg efficiency 7d':<32}: {ef.avg_efficiency_7d:.3f}")
            print(f"  {'Avg efficiency 30d':<32}: {ef.avg_efficiency_30d:.3f}")
            print(f"  {'Efficiency trend':<32}: {ef.efficiency_trend}")
            print(f"  {'High efficiency rate':<32}: {ef.high_efficiency_rate:.1%}")
            print(f"  {'Fake volatility rate':<32}: {ef.fake_vol_rate:.1%}")
        print("-" * w)

        # ── V2.5 Conditional expected range ──────────────────────────────
        print(f"  CONDITIONAL RANGE")
        if profile.conditional_expected_range:
            ce = profile.conditional_expected_range
            print(f"  {'Condition':<32}: {ce.weekday} + {ce.vol_state}  "
                  f"(N={ce.sample_size}, {ce.reliability})")
            print(f"  {'Conditional expected ADR':<32}: ${ce.expected_range:,.2f}")
            print(f"  {'Realized vs expected':<32}: {profile.realized_vs_expected:.2f}×")
        print("-" * w)

        # ── V2.6 Session ──────────────────────────────────────────────────
        print(f"  SESSION BEHAVIOR")
        print(f"  {'Current session':<32}: {profile.current_session}")
        print(f"  {'Session expansion likely':<32}: "
              f"{'YES' if profile.session_expansion_likely else 'NO'}")
        if profile.session_profiles:
            print(f"  {'Session':>12} {'Avg Range':>11} {'ExpRate':>9} {'Dir':>9}")
            for sp in profile.session_profiles:
                print(
                    f"  {sp.session_name:>12} "
                    f"${sp.avg_range_dollars:>10,.2f} "
                    f"{sp.expansion_rate:>9.1%} "
                    f"{sp.typical_direction:>9}"
                )
        print("-" * w)

        # ── Grid & final score ────────────────────────────────────────────
        print(f"  {'Grid spacing':<32}: ${profile.grid_spacing:,.2f}  "
              f"({profile.grid_levels} levels)")
        print(f"  {'RANGE SCORE ({:5s})':<32}  {score:.4f}  "
              f"({score*100:.1f}/100)".format(direction))
        print("=" * w)

    # ═════════════════════════════════════════════════════════════════════
    # PRIVATE: V2.1 — FORWARD RETURNS
    # ═════════════════════════════════════════════════════════════════════

    def _compute_forward_returns(
        self,
        df:           pd.DataFrame,
        adr_override: Optional[float] = None,
    ) -> List[ForwardReturnBucket]:
        """
        For each historical day, compute:
          range_used = that day's range / ADR
          forward_return_1d = (next day close - this day close) / this day close
          forward_return_2d = (day after next close - this day close) / this day close

        Segment by range_used band and aggregate statistics.
        This answers: "what happens AFTER price is at X% of its ADR?"
        """
        if len(df) < 5:
            return []

        adr = adr_override or float(df["daily_range"].mean())
        if adr <= 0:
            return []

        records = []
        closes  = df["close"].values
        highs   = df["high"].values
        lows    = df["low"].values
        ranges  = df["daily_range"].values

        for i in range(len(df) - 2):
            ru = ranges[i] / adr
            r1 = (closes[i + 1] - closes[i]) / closes[i] if closes[i] > 0 else 0.0
            r2 = (closes[i + 2] - closes[i]) / closes[i] if closes[i] > 0 else 0.0
            next_range = ranges[i + 1] / adr
            records.append((ru, r1, r2, next_range))

        bands = [
            ("0–30%",   0.00, 0.30),
            ("30–50%",  0.30, 0.50),
            ("50–65%",  0.50, 0.65),
            ("65–80%",  0.65, 0.80),
            ("80–100%", 0.80, 1.00),
            ("100%+",   1.00, 99.0),
        ]

        buckets = []
        for label, lo, hi in bands:
            subset = [(r1, r2, nr) for (ru, r1, r2, nr) in records if lo <= ru < hi]
            n      = len(subset)
            if n == 0:
                continue
            r1_arr  = np.array([x[0] for x in subset])
            r2_arr  = np.array([x[1] for x in subset])
            nr_arr  = np.array([x[2] for x in subset])
            buckets.append(ForwardReturnBucket(
                bucket_label     = label,
                range_low        = lo,
                range_high       = hi,
                sample_size      = n,
                avg_return_1d    = round(float(r1_arr.mean()),    5),
                avg_return_2d    = round(float(r2_arr.mean()),    5),
                median_return_1d = round(float(np.median(r1_arr)),5),
                positive_rate    = round(float((r1_arr > 0).mean()), 4),
                avg_next_range   = round(float(nr_arr.mean()),    4),
            ))

        return buckets

    def _expected_next_return(
        self,
        buckets:    List[ForwardReturnBucket],
        range_used: float,
    ) -> Tuple[float, str]:
        """
        Look up which bucket the current range_used falls into and
        return its avg_return_1d and a direction label.
        """
        for b in buckets:
            if b.range_low <= range_used < b.range_high:
                exp  = b.avg_return_1d
                if exp > 0.003:
                    direction = "BULLISH"
                elif exp < -0.003:
                    direction = "BEARISH"
                else:
                    direction = "NEUTRAL"
                return exp, direction
        return 0.0, "NEUTRAL"

    # ═════════════════════════════════════════════════════════════════════
    # PRIVATE: V2.2 + V2.3 — VOLATILITY CLUSTERING & PERSISTENCE
    # ═════════════════════════════════════════════════════════════════════

    def _compute_vol_cluster(
        self,
        df:        pd.DataFrame,
        cur_state: str,
        adr:       float,
    ) -> VolatilityCluster:
        """
        Compute full volatility clustering statistics.

        Builds state sequence from rolling 7d ADR vs 30d ADR,
        computes avg duration of each state, builds Markov
        transition matrix, and calculates vol acceleration.
        """
        ranges = df["daily_range"].values
        n      = len(ranges)

        # Build state for each day using a rolling window
        states = []
        for i in range(n):
            start  = max(0, i - 6)
            window = ranges[start: i + 1]
            w_adr  = float(window.mean())
            ratio  = w_adr / adr if adr > 0 else 1.0
            states.append(self._volatility_state(ratio))

        # ── Duration per state ────────────────────────────────────────────
        def avg_run_length(state_name):
            runs = []
            cur  = 0
            for s in states:
                if s == state_name:
                    cur += 1
                else:
                    if cur > 0:
                        runs.append(cur)
                    cur = 0
            if cur > 0:
                runs.append(cur)
            return float(np.mean(runs)) if runs else 0.0

        comp_avg = avg_run_length("COMPRESSED")
        norm_avg = avg_run_length("NORMAL")
        exp_avg  = avg_run_length("EXPANDING")

        # ── Current state consecutive days ────────────────────────────────
        cur_days = 0
        for s in reversed(states):
            if s == cur_state:
                cur_days += 1
            else:
                break

        # ── Exhaustion probability ────────────────────────────────────────
        # P(exhaustion) ∝ how far current_days is past the avg duration
        avg_dur = {
            "COMPRESSED": comp_avg,
            "NORMAL":     norm_avg,
            "EXPANDING":  exp_avg,
        }.get(cur_state, 1.0)

        if avg_dur > 0:
            exhaustion_prob = min(1.0, cur_days / avg_dur)
        else:
            exhaustion_prob = 0.5

        # ── Markov transition matrix ──────────────────────────────────────
        state_list = ["COMPRESSED", "NORMAL", "EXPANDING"]
        counts     = {s: {t: 0 for t in state_list} for s in state_list}
        for i in range(len(states) - 1):
            s = states[i]
            t = states[i + 1]
            if s in counts and t in counts[s]:
                counts[s][t] += 1

        transition = {}
        for s in state_list:
            total = sum(counts[s].values())
            transition[s] = {
                t: round(counts[s][t] / total, 4) if total > 0 else 0.0
                for t in state_list
            }

        # ── Volatility acceleration ───────────────────────────────────────
        recent_7  = float(ranges[-7:].mean())  if n >= 7  else float(ranges.mean())
        prev_7    = float(ranges[-14:-7].mean()) if n >= 14 else float(ranges.mean())
        vol_accel = recent_7 / prev_7 if prev_7 > 0 else 1.0

        if vol_accel > 1.15:
            vol_momentum = "ACCELERATING"
        elif vol_accel < 0.85:
            vol_momentum = "DECELERATING"
        else:
            vol_momentum = "STABLE"

        # ── Rolling variance ──────────────────────────────────────────────
        rv7  = float(np.var(ranges[-7:]))  if n >= 7  else 0.0
        rv30 = float(np.var(ranges[-30:])) if n >= 30 else float(np.var(ranges))
        vr   = rv7 / rv30 if rv30 > 0 else 1.0

        return VolatilityCluster(
            compressed_avg_days  = round(comp_avg, 2),
            normal_avg_days      = round(norm_avg, 2),
            expanding_avg_days   = round(exp_avg,  2),
            current_state        = cur_state,
            current_state_days   = cur_days,
            exhaustion_prob      = round(exhaustion_prob, 4),
            transition_matrix    = transition,
            vol_acceleration     = round(vol_accel, 4),
            vol_momentum         = vol_momentum,
            rolling_variance_7d  = round(rv7,  2),
            rolling_variance_30d = round(rv30, 2),
            variance_ratio       = round(vr,   4),
        )

    # ═════════════════════════════════════════════════════════════════════
    # PRIVATE: V2.4 — DIRECTIONAL EFFICIENCY
    # ═════════════════════════════════════════════════════════════════════

    def _compute_efficiency(self, df: pd.DataFrame) -> DirectionalEfficiency:
        """
        efficiency = abs(close - open) / (high - low)

        1.0 = full marubozu, fully directional
        0.0 = all wick, no direction (doji / spinning top)

        High efficiency = price is moving with conviction.
        Low efficiency  = fake volatility, whipsaws.
        """
        ranges = df["high"] - df["low"]
        bodies = (df["close"] - df["open"]).abs()

        # Avoid division by zero
        eff_series = np.where(ranges > 0, bodies / ranges, 0.5)

        avg_7d  = float(eff_series[-7:].mean())   if len(eff_series) >= 7  else float(eff_series.mean())
        avg_30d = float(eff_series[-30:].mean())  if len(eff_series) >= 30 else float(eff_series.mean())
        cur_eff = float(eff_series[-1])

        if avg_7d > avg_30d * 1.05:
            trend = "IMPROVING"
        elif avg_7d < avg_30d * 0.95:
            trend = "DETERIORATING"
        else:
            trend = "STABLE"

        high_rate = float((eff_series >= self.efficiency_clean).mean())
        fake_rate = float((eff_series <= self.efficiency_noisy).mean())

        return DirectionalEfficiency(
            avg_efficiency_7d   = round(avg_7d,    4),
            avg_efficiency_30d  = round(avg_30d,   4),
            efficiency_trend    = trend,
            current_efficiency  = round(cur_eff,   4),
            high_efficiency_rate= round(high_rate, 4),
            fake_vol_rate       = round(fake_rate, 4),
        )

    def _efficiency_signal(self, eff: DirectionalEfficiency) -> str:
        """Classify efficiency into CLEAN / NEUTRAL / NOISY for scoring."""
        if eff.current_efficiency >= self.efficiency_clean:
            return "CLEAN"
        if eff.current_efficiency <= self.efficiency_noisy:
            return "NOISY"
        return "NEUTRAL"

    # ═════════════════════════════════════════════════════════════════════
    # PRIVATE: V2.5 — CONDITIONAL EXPECTED RANGE
    # ═════════════════════════════════════════════════════════════════════

    def _conditional_expected_range(
        self,
        df:        pd.DataFrame,
        vol_state: str,
    ) -> Optional[ConditionalExpectedRange]:
        """
        Compute expected ADR conditioned on:
          • weekday of today
          • current volatility state

        Requires DatetimeIndex on df for weekday extraction.
        Falls back to returning None if index is not datetime-like.
        """
        try:
            idx = pd.to_datetime(df.index)
        except Exception:
            return None

        today_wd = idx[-1].weekday()
        wd_str   = self.WEEKDAY_MAP.get(today_wd, "UNK")

        # Add weekday column
        df_wd = df.copy()
        df_wd["weekday"] = [i.weekday() for i in idx]

        # Compute rolling vol_state for each row
        ranges    = df["daily_range"].values
        adr_full  = float(np.mean(ranges))
        row_states = []
        for i in range(len(ranges)):
            start = max(0, i - 6)
            w_adr = float(ranges[start:i+1].mean())
            row_states.append(
                self._volatility_state(w_adr / adr_full if adr_full > 0 else 1.0)
            )
        df_wd["vol_state"] = row_states

        # Filter to matching weekday + vol_state
        mask   = (df_wd["weekday"] == today_wd) & (df_wd["vol_state"] == vol_state)
        subset = df_wd[mask]["daily_range"]
        n      = len(subset)

        if n == 0:
            # Fallback to weekday only
            mask2  = df_wd["weekday"] == today_wd
            subset = df_wd[mask2]["daily_range"]
            n      = len(subset)
            vol_state_used = "ANY"
        else:
            vol_state_used = vol_state

        if n == 0:
            return None

        exp_range = float(subset.mean())
        exp_pct   = exp_range / float(df["low"].iloc[-1]) * 100 if float(df["low"].iloc[-1]) > 0 else 0.0
        reliability = "HIGH" if n >= 10 else ("MEDIUM" if n >= 5 else "LOW")

        return ConditionalExpectedRange(
            weekday            = wd_str,
            vol_state          = vol_state_used,
            expected_range     = round(exp_range, 2),
            expected_range_pct = round(exp_pct,   4),
            sample_size        = n,
            reliability        = reliability,
        )

    # ═════════════════════════════════════════════════════════════════════
    # PRIVATE: V2.6 — SESSION BEHAVIOR
    # ═════════════════════════════════════════════════════════════════════

    def _compute_session_profiles(
        self,
        hourly_df:   pd.DataFrame,
        daily_adr:   float,
    ) -> List[SessionProfile]:
        """
        Compute statistics per trading session from hourly OHLCV data.

        Attempts to extract UTC hour from the DataFrame index.
        Falls back gracefully if index is not datetime-like.
        """
        profiles = []

        try:
            idx = pd.to_datetime(hourly_df.index, utc=True)
        except Exception:
            try:
                idx = pd.to_datetime(hourly_df["datetime"], utc=True)
            except Exception:
                return []

        df_h = hourly_df.copy()
        df_h["utc_hour"] = [i.hour for i in idx]
        df_h["candle_range"] = df_h["high"] - df_h["low"]
        df_h["candle_return"] = (df_h["close"] - df_h["open"]) / df_h["open"].replace(0, np.nan)
        session_adr_quarter = daily_adr / 4   # rough session ADR benchmark

        for session_name, (h_start, h_end) in self.SESSIONS.items():
            mask   = (df_h["utc_hour"] >= h_start) & (df_h["utc_hour"] < h_end)
            subset = df_h[mask]

            if len(subset) < 5:
                continue

            avg_range   = float(subset["candle_range"].mean())
            avg_range_p = avg_range / float(subset["low"].replace(0, np.nan).mean() or 1) * 100
            avg_vol     = float(subset["volume"].mean()) if "volume" in subset.columns else 0.0
            exp_rate    = float((subset["candle_range"] > session_adr_quarter).mean())

            returns     = subset["candle_return"].dropna()
            avg_ret     = float(returns.mean()) if len(returns) > 0 else 0.0
            if avg_ret > 0.001:
                direction = "BULLISH"
            elif avg_ret < -0.001:
                direction = "BEARISH"
            else:
                direction = "NEUTRAL"

            pos_rate    = float((returns > 0).mean()) if len(returns) > 0 else 0.5
            dir_consist = max(pos_rate, 1 - pos_rate)

            profiles.append(SessionProfile(
                session_name          = session_name,
                utc_start             = h_start,
                utc_end               = h_end,
                avg_range_dollars     = round(avg_range,   2),
                avg_range_pct         = round(avg_range_p, 4),
                avg_volume            = round(avg_vol,     2),
                expansion_rate        = round(exp_rate,    4),
                typical_direction     = direction,
                direction_consistency = round(dir_consist, 4),
            ))

        return profiles

    def _current_session(self) -> str:
        """Return the current UTC session name."""
        import datetime
        hour = datetime.datetime.utcnow().hour
        for name, (start, end) in self.SESSIONS.items():
            if start <= hour < end:
                return name
        return "NY_CLOSE"

    def _session_expansion_likely(
        self,
        profiles:        List[SessionProfile],
        current_session: str,
    ) -> bool:
        """Return True if the current session historically has expansion_rate > 0.40."""
        for sp in profiles:
            if sp.session_name == current_session:
                return sp.expansion_rate > 0.40
        return False

    # ═════════════════════════════════════════════════════════════════════
    # PRIVATE: V1 HELPERS (unchanged)
    # ═════════════════════════════════════════════════════════════════════

    @staticmethod
    def _validate(df: pd.DataFrame) -> None:
        required = {"open", "high", "low", "close", "volume"}
        missing  = required - set(df.columns)
        if missing:
            raise ValueError(f"RangeAnalytics: missing columns {missing}")
        if len(df) < 5:
            raise ValueError(f"RangeAnalytics: need ≥ 5 rows, got {len(df)}")

    @staticmethod
    def _add_range_columns(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["daily_range"]     = df["high"] - df["low"]
        df["daily_range_pct"] = (
            df["daily_range"] / df["low"].replace(0, np.nan) * 100
        )
        return df

    def _volatility_state(self, ratio: float) -> str:
        if ratio < 0.75:
            return "COMPRESSED"
        if ratio > 1.35:
            return "EXPANDING"
        return "NORMAL"

    def _grid_recommendation(
        self, adr_dollars: float, vol_state: str
    ) -> tuple:
        spacing = adr_dollars * self.grid_pct
        if vol_state == "COMPRESSED":
            spacing *= 0.70
        elif vol_state == "EXPANDING":
            spacing *= 1.60
        spacing  = max(spacing, 0.01)
        cover    = adr_dollars * 0.80
        levels   = int(cover / spacing)
        levels   = max(self.min_grid_levels, min(levels, self.max_grid_levels))
        cap_ratio= 1.0 / levels if levels > 0 else 0.05
        return spacing, levels, cap_ratio

    @staticmethod
    def _max_consecutive(bool_list: list) -> int:
        max_run = current = 0
        for v in bool_list:
            if v:
                current += 1
                max_run  = max(max_run, current)
            else:
                current  = 0
        return max_run

    def _build_breakdown_v2(
        self,
        df:          pd.DataFrame,
        adr_dollars: float,
    ) -> List[DailyRangeRow]:
        """Per-day breakdown with v2 additions: efficiency, vol_state, weekday."""
        rows      = []
        threshold = adr_dollars * self.adr_threshold
        ranges    = df["daily_range"].values
        n         = len(df)

        try:
            idx_dt = pd.to_datetime(df.index)
            has_dt = True
        except Exception:
            has_dt = False

        for i, (raw_idx, row) in enumerate(df.iterrows()):
            r   = float(row["daily_range"])
            rng = float(row["high"] - row["low"])
            bod = abs(float(row["close"]) - float(row["open"]))
            eff = bod / rng if rng > 0 else 0.5

            # Rolling vol state for this day
            start  = max(0, i - 6)
            w_adr  = float(ranges[start: i + 1].mean())
            ratio  = w_adr / adr_dollars if adr_dollars > 0 else 1.0
            vs     = self._volatility_state(ratio)

            # Weekday
            if has_dt:
                try:
                    wd = self.WEEKDAY_MAP.get(idx_dt[i].weekday(), "UNK")
                except Exception:
                    wd = "UNK"
            else:
                wd = "UNK"

            # Forward return (next day) — available for all but last row
            if i < n - 1:
                next_close = float(df["close"].iloc[i + 1])
                this_close = float(row["close"])
                fwd_r1d    = (next_close - this_close) / this_close if this_close > 0 else 0.0
            else:
                fwd_r1d = 0.0

            try:
                date_str = str(raw_idx)[:10]
            except Exception:
                date_str = str(i)

            rows.append(DailyRangeRow(
                date              = date_str,
                high              = round(float(row["high"]),  2),
                low               = round(float(row["low"]),   2),
                range_dollars     = round(r,                    2),
                range_pct         = round(float(row["daily_range_pct"]), 4),
                vs_adr            = round(r / adr_dollars, 3)  if adr_dollars else 0,
                broke_adr         = r > threshold,
                efficiency        = round(eff,   4),
                vol_state         = vs,
                weekday           = wd,
                forward_return_1d = round(fwd_r1d, 5),
            ))

        return rows