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
#   half_life            How many CANDLES does this spread take to revert
#                        halfway? (converted to physical time via
#                        bot.instruments.candles_to_wall_clock() — see
#                        PairResult.half_life_time/half_life_time_unit)
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
from bot.quant_core.provenance import write_legacy_provenance   # claude code changed: Stage 2D Rev B G-1 — LEGACY sidecars: this engine runs pre-Stage-2D (legacy) methodology

import logging                              # Structured logging
import warnings                             # Suppress statistical warnings
import itertools                            # Generate all pair combinations
from bot.quant_core.cointegration import engle_granger   # claude code changed: Stage 2D — canonical Engle–Granger (MacKinnon), UNTESTABLE instead of fallback

from typing import Dict, List, Optional, Tuple   # Type hints

import numpy as np                          # Numerical operations
import pandas as pd                         # DataFrame operations
from scipy import stats                     # OLS regression, Spearman IC

# Statistical tests for cointegration
from statsmodels.tsa.stattools import adfuller, coint   # ADF test, cointegration test
from statsmodels.regression.linear_model import OLS     # OLS for hedge ratio
from statsmodels.tools import add_constant              # Add intercept to OLS
from statsmodels.stats.multitest import multipletests   # claude code changed: restored — Phase B (P1-5) FDR correction across all tested pairs; accidentally dropped by commit de4fd8b, restored in Phase 1C (Blocker D) with the Phase 1B timeframe/half_life fields preserved

from bot.instruments import candles_to_wall_clock  # claude code changed: new — Multi-Asset Foundation Refactor Phase 1B, Objective 2. The single candles->wall-clock-time conversion, see bot/instruments.py
from bot.instruments import symbols_for_asset_class, ASSET_CLASS_CRYPTO  # claude code changed: new — universe expansion mission. Derives UNIVERSE from the single instrument registry instead of an independent hand-typed copy (bot/instruments.py's own module docstring already flagged this file's independent list as a known drift risk vs. fetch_all_symbols.SYMBOLS)
from bot.research.data_access import load_ohlcv_via_provider  # claude code changed: Data-Layer Audit migration (F.3) — see DATA_LAYER_ARCHITECTURE_AUDIT.md

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

# claude code changed: was a hand-typed 20-symbol list (underscore format,
# e.g. 'BTC_USDT') independent from bot.instruments.INSTRUMENT_REGISTRY —
# a real drift risk bot/instruments.py's own module docstring already
# flagged. Now derived from the single instrument registry (which is
# itself derived from bot/fetch_all_symbols.SYMBOLS, in turn driven by
# bot/universe_selector.py's dynamic 50-symbol selection) — converting
# "/" to "_" only to match this module's own pre-existing internal
# convention (every downstream reference here, e.g. run_cointegration_research's
# f"{symbol}_{interval}.csv", expects underscore form). Recomputed at
# import time, so a re-run of universe_selector.py + fetch_all_symbols.py
# automatically flows through here with no manual list edit.
UNIVERSE: List[str] = [
    s.replace("/", "_") for s in symbols_for_asset_class(ASSET_CLASS_CRYPTO)
]

# claude code changed: new — universe expansion mission, Step 4. See
# CointegrationEngine.__init__'s enable_correlation_prefilter comment for
# the full rationale on why this is off by default and deliberately weak.
CORRELATION_PREFILTER_THRESHOLD: float = 0.5

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

# Half-life filter — maximum acceptable half-life, expressed in CANDLES.
# Spreads that take too long to revert tie up capital unproductively.
# 120 candles of 1h data = 5 days — if reversion takes longer than this,
# skip the pair.
#
# claude code changed: Multi-Asset Foundation Refactor Phase 1B,
# Objective 2. These two constants are named "*_HOURS" and were always
# documented as hours — but _estimate_half_life() has only ever returned
# a raw candle count (see that method), so numerically these bounds have
# ALWAYS been compared against candles, not hours; the name was
# aspirational, not descriptive. Deliberately NOT rescaling these bounds
# by timeframe in this phase: "how many candles of illiquid capital lockup
# is acceptable" is arguably a more natural risk bound than a wall-clock
# one regardless of candle width, and reinterpreting the THRESHOLD's
# meaning is a bigger judgment call than this phase's actual objective
# (fixing the REPORTED units — see half_life_time/half_life_time_unit).
# For the only data this engine has ever run on (1h candles), the two
# readings are numerically identical, so this note changes no behavior
# today — it only stops the name from implying a conversion that was
# never actually happening.
MAX_HALF_LIFE_HOURS: int = 120   # 120 candles (= 5 days at 1h)

# Minimum half-life, expressed in CANDLES.
# Spreads that revert too fast are too fast for the candle resolution
# being traded — the entry and exit would need finer-grained data.
MIN_HALF_LIFE_HOURS: float = 2.0  # 2 candles

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

# claude code changed: new — out-of-sample persistence check (see module
# docstring / OUT-OF-SAMPLE PERSISTENCE section below). Below this many
# held-out candles, an ADF test is too underpowered to trust either way —
# the pair is rejected with an honest "insufficient OOS data" reason
# rather than silently skipping the check (which would let a
# barely-longer-than-TRAINING_WINDOW pair pass on the in-sample result
# alone, exactly the gap this fix exists to close).
MIN_OOS_CANDLES: int = 1_000


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
        timeframe:       str = "1h",  # claude code changed: new — Multi-Asset Foundation Refactor Phase 1B, Objective 2. Defaults to "1h" so every existing call site (all of which predate this parameter) keeps constructing byte-identical PairResults.
        adf_pvalue_fdr:  Optional[float] = None,
        coint_pvalue_fdr: Optional[float] = None,   # claude code changed: Stage 2D — BH-adjusted ENGLE–GRANGER p-value; the selection decision uses this (adf_pvalue_fdr is now diagnostic only)   # claude code changed: restored — Phase B (P1-5); accidentally dropped by commit de4fd8b, restored in Phase 1C (Blocker D)
        passes_fdr:      Optional[bool]  = None,   # claude code changed: restored — Phase B (P1-5); None until run_all()'s correction step assigns it
        oos_adf_pvalue:      Optional[float] = None,   # claude code changed: new — out-of-sample persistence check, see module docstring update
        oos_adf_pvalue_fdr:  Optional[float] = None,   # claude code changed: new — same "provisional then corrected" pattern as adf_pvalue_fdr
        passes_oos_persistence: Optional[bool] = None,   # claude code changed: new — None until _apply_oos_persistence_filter() assigns it
        training_window_candles: Optional[int] = None,   # claude code changed: new — real look-ahead-bias fix. The exact number of leading candles (train_end in _test_pair()) whose hedge_ratio/intercept were FIT on this data — downstream consumers (kalman_filter_engine.py) need this to exclude that same span from backtesting instead of only excluding their own short Kalman-convergence warmup. None only for the pre-train_end early-exit (insufficient aligned data), where no training window was ever established.
    ) -> None:

        self.symbol_a        = symbol_a         # First asset in pair
        self.symbol_b        = symbol_b         # Second asset in pair
        self.pair_name       = f"{symbol_a}/{symbol_b}"   # Human-readable name
        self.coint_pvalue    = coint_pvalue      # Engle-Granger p-value
        self.hedge_ratio     = hedge_ratio       # β: A = α + β×B + ε
        self.intercept       = intercept         # α: equilibrium level offset
        # claude code changed: was documented "Hours for spread to revert
        # 50%" — that was never actually true for any timeframe other than
        # 1h; _estimate_half_life() below has only ever returned a candle
        # count (the raw -log(2)/lambda AR(1) decay constant), silently
        # correct only because every dataset this engine has ever seen
        # happened to be 1h candles. Renamed to say what it actually is;
        # see half_life_time/half_life_time_unit below for the real,
        # timeframe-aware wall-clock conversion.
        self.half_life       = half_life         # Candles for spread to revert 50% (raw OU decay periods)
        self.adf_pvalue      = adf_pvalue        # ADF test p-value on residuals
        self.is_cointegrated = is_cointegrated   # Did it pass the coint test?
        self.passes_filters  = passes_filters    # Did it pass half-life filters?
        self.reject_reason   = reject_reason     # Why was it rejected (if applicable)
        self.timeframe       = timeframe         # claude code changed: new — the candle resolution half_life was estimated on; see bot/instruments.py's candles_to_wall_clock()
        # claude code changed: restored — Phase B (P1-5). Assigned by
        # run_all()'s _apply_fdr_correction() after every pair in the
        # universe has been tested, not by _test_pair() itself — multiple-
        # testing correction needs the full cross-pair p-value array, same
        # "provisional then corrected" pattern feature_validator.py uses.
        # None until that step runs.
        self.adf_pvalue_fdr  = adf_pvalue_fdr   # claude code changed: Stage 2D — DIAGNOSTIC ONLY (ordinary DF tables are invalid for an estimated β)
        self.coint_pvalue_fdr = coint_pvalue_fdr   # claude code changed: Stage 2D — decision column
        self.passes_fdr      = passes_fdr
        # claude code changed: new — real bug found by out-of-sample audit
        # (see module docstring): a pair being stationary within the fixed
        # TRAINING_WINDOW never meant the relationship actually persisted.
        # Empirically, 76 of 84 pairs that passed the training-window-only
        # ADF test (90.5%) failed a second ADF test on the post-training
        # window (a PSEUDO-holdout — already exposed to earlier research, NOT an
        # untouched holdout; claude code changed: F-19 wording) using the SAME frozen hedge ratio/intercept
        # — a genuine, permanent structural break in most cases (spread
        # means shifting by 6+ training-period standard deviations), not
        # noise. oos_adf_pvalue is the raw result; oos_adf_pvalue_fdr/
        # passes_oos_persistence follow the same "provisional then
        # corrected" pattern as adf_pvalue_fdr/passes_fdr above — assigned
        # by _apply_oos_persistence_filter() after every candidate pair
        # tested, not by _test_pair() itself.
        self.oos_adf_pvalue         = oos_adf_pvalue
        self.oos_adf_pvalue_fdr     = oos_adf_pvalue_fdr
        self.passes_oos_persistence = passes_oos_persistence
        self.training_window_candles = training_window_candles   # claude code changed: new — see __init__'s param comment
        # claude code changed: new — the actual physical-time conversion,
        # derived from the instrument/timeframe abstraction (bot/
        # instruments.py), never a hardcoded "×1 for hours" assumption.
        # inf half_life (non-cointegrated/failed pairs) converts to inf
        # cleanly — still meaningful ("never reverts"), never a crash.
        self.half_life_time, self.half_life_time_unit = candles_to_wall_clock(half_life, timeframe)

    def to_dict(self) -> Dict:
        """Convert to dictionary for DataFrame construction."""
        return {
            "pair_name":       self.pair_name,
            "symbol_a":        self.symbol_a,
            "symbol_b":        self.symbol_b,
            "coint_pvalue":    round(self.coint_pvalue, 6),
            "adf_pvalue":      round(self.adf_pvalue, 6),
            "adf_pvalue_fdr":  round(self.adf_pvalue_fdr, 6) if self.adf_pvalue_fdr is not None else None,   # claude code changed: restored — Phase B (P1-5)
            "coint_pvalue_fdr": round(self.coint_pvalue_fdr, 6) if self.coint_pvalue_fdr is not None else None,   # claude code changed: Stage 2D
            "passes_fdr":      self.passes_fdr,   # claude code changed: restored — Phase B (P1-5)
            "hedge_ratio":     round(self.hedge_ratio, 6),
            "intercept":       round(self.intercept, 6),
            # claude code changed: half_life_hours is KEPT, unchanged in
            # meaning and value, for backward compatibility with existing
            # consumers of the standalone research pipeline's CSV output
            # (kalman_filter_engine.py's loader, templates/
            # pairs_performance.html, templates/research_lab.html) — every
            # one of them has only ever run on 1h data, for which this
            # value is, and remains, numerically correct. New code should
            # prefer half_life_candles/half_life_time/half_life_time_unit
            # below, which are honestly correct for ANY timeframe, not
            # just 1h.
            "half_life_hours":     round(self.half_life, 2),
            "half_life_candles":   round(self.half_life, 2),
            "half_life_time":      round(self.half_life_time, 2),
            "half_life_time_unit": self.half_life_time_unit,
            "timeframe":            self.timeframe,
            "is_cointegrated": self.is_cointegrated,
            "passes_filters":  self.passes_filters,
            "reject_reason":   self.reject_reason,
            # claude code changed: new — see PairResult.__init__'s comment
            "oos_adf_pvalue":     round(self.oos_adf_pvalue, 6) if self.oos_adf_pvalue is not None else None,
            "oos_adf_pvalue_fdr": round(self.oos_adf_pvalue_fdr, 6) if self.oos_adf_pvalue_fdr is not None else None,
            "passes_oos_persistence": self.passes_oos_persistence,
            # claude code changed: new — look-ahead-bias fix. Persists the
            # exact candle count hedge_ratio/intercept were fit on, so any
            # downstream consumer (kalman_filter_engine.py) can exclude
            # this same span instead of independently guessing/hardcoding
            # a warmup length that has nothing to do with the OLS fit.
            "training_window_candles": self.training_window_candles,
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
        timeframe:             str              = "1h",  # claude code changed: new — Multi-Asset Foundation Refactor Phase 1B, Objective 2. Defaults to "1h", this engine's only real dataset today — every existing construction site (none of which pass this parameter) is unaffected. MAX/MIN_HALF_LIFE_HOURS/TRAINING_WINDOW/ZSCORE_WINDOW stay expressed in raw candles — this parameter affects ONLY how half-life is REPORTED (half_life_time/half_life_time_unit), not the filter thresholds themselves; see the module-level note on why that's the correct, minimal scope for this fix.
        enable_correlation_prefilter: bool = False,   # claude code changed: new — universe expansion mission, Step 4. Off by default — every existing construction site is unaffected. See CORRELATION_PREFILTER_THRESHOLD below for why this exists and why it's deliberately weak.
        correlation_prefilter_threshold: float = None,  # claude code changed: new — defaults to CORRELATION_PREFILTER_THRESHOLD when None; exposed as a constructor param for sensitivity analysis, matching every other threshold in this class.
        min_oos_candles:       int              = MIN_OOS_CANDLES,   # claude code changed: new — out-of-sample persistence check, exposed for sensitivity analysis/fast tests matching every other threshold in this class.
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
        self.max_half_life      = max_half_life       # Max acceptable half-life (candles — see timeframe note above)
        self.min_half_life      = min_half_life       # Min acceptable half-life (candles — see timeframe note above)
        self.forward_horizons   = forward_horizons or FORWARD_HORIZONS
        self.timeframe          = timeframe           # claude code changed: new — candle resolution of the data this engine instance will receive; threaded into every PairResult so half-life is reported honestly

        # claude code changed: new — universe expansion mission, Step 4.
        # 50 symbols -> C(50,2)=1225 pairs, each requiring an OLS fit + ADF
        # test; a cheap correlation check (already-loaded price series, no
        # extra I/O) can skip pairs that are obviously unrelated before
        # paying for the expensive test. Off by default because it's a
        # real trade-off, not a pure win: correlation and cointegration are
        # different properties (see this module's own docstring on why —
        # "Low correlation + cointegrated -> wander independently but
        # always revert" is a real, valid case this prefilter would skip).
        # When enabled, the threshold is deliberately weak (0.5, not e.g.
        # 0.8) specifically so it only screens out pairs with essentially
        # no linear relationship at all, minimizing the risk of discarding
        # a real low-correlation cointegrated pair.
        self.min_oos_candles = min_oos_candles   # claude code changed: new — see MIN_OOS_CANDLES module comment

        self.enable_correlation_prefilter = enable_correlation_prefilter
        self.correlation_prefilter_threshold = (
            correlation_prefilter_threshold
            if correlation_prefilter_threshold is not None
            else CORRELATION_PREFILTER_THRESHOLD
        )

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

            # claude code changed: new — universe expansion mission, Step 4.
            # Off by default (self.enable_correlation_prefilter); see
            # __init__'s comment for why this is deliberately weak and
            # opt-in rather than always-on.
            if self.enable_correlation_prefilter:
                # claude code changed: correlation on log-price RETURNS
                # (diff), not raw log-price levels. Two trending series are
                # almost always highly "correlated" in level regardless of
                # any real relationship (classic spurious-regression
                # territory — exactly the failure mode cointegration
                # testing exists to guard against), which would make a
                # level-based prefilter a near no-op. Returns correlation
                # is the standard, meaningful measure for "do these two
                # assets actually move together."
                pair_corr = prices[symbol_a].diff().corr(prices[symbol_b].diff())
                if pd.isna(pair_corr) or abs(pair_corr) < self.correlation_prefilter_threshold:   # claude code changed: Stage 2D — a NaN correlation used to PASS the prefilter; now it is skipped (fail closed)
                    logger.info(
                        f"  {symbol_a}/{symbol_b}: SKIPPED — "
                        f"|correlation|={abs(pair_corr):.3f} < {self.correlation_prefilter_threshold} prefilter threshold"
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

        # ── Step 2.5: FDR correction across every pair tested ─────────────────
        # claude code changed: restored — Phase B remediation (forensic-audit
        # finding P1-5). Each pair above was tested independently at a flat
        # p < coint_threshold (0.05) with no correction across the
        # C(20,2) = 190 pairs actually tested — at alpha=0.05 with 190
        # independent tests, roughly 190*0.05 ≈ 9-10 pairs are expected to
        # look "cointegrated" by chance alone even with zero real
        # relationships. This step corrects for that AFTER every pair has
        # been tested (needs the full cross-pair p-value array, same
        # "provisional then corrected" pattern feature_validator.py uses —
        # see apply_family_wide_correction() there for the sibling fix).
        #
        # Deliberately conservative, not loosened: a pair that was
        # is_cointegrated=True under the raw threshold but fails FDR
        # correction is DOWNGRADED (passes_filters set False, reject_reason
        # updated) — this only ever removes pairs, never adds ones the raw
        # test rejected. The goal stated in the remediation brief is fewer,
        # more trustworthy pairs, not more pairs.
        #
        # This method was accidentally deleted by a later, unrelated commit
        # (Academy module / derivatives engine / research pipeline addition)
        # that clobbered this file — restored here in Phase 1C (Blocker D),
        # verbatim from its original Phase B implementation, alongside the
        # Phase 1B timeframe/half_life_candles fields that were added after
        # the original FDR work and must be preserved.
        self._apply_fdr_correction()

        # ── Step 2.6: Out-of-sample persistence correction ────────────────────
        # claude code changed: new — see PairResult.__init__/_test_pair()'s
        # comments for the full finding. FDR-corrects the OOS ADF p-values
        # across every candidate pair still standing after Step 2.5 (the
        # in-sample FDR correction) — a second, genuine multiple-testing
        # problem, since confirming persistence across ~80-500 candidate
        # pairs simultaneously has the same false-positive risk the Step
        # 2.5 correction exists to control for the in-sample test.
        self._apply_oos_persistence_filter()

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
    # FDR CORRECTION ACROSS ALL TESTED PAIRS
    # claude code changed: restored — Phase B remediation (forensic-audit
    # finding P1-5). See the call site in run_all() ("Step 2.5") for the
    # full reasoning. Mutates self.pair_results in place: every PairResult
    # gets adf_pvalue_fdr/passes_fdr assigned, and any pair that was
    # is_cointegrated=True under the raw threshold but fails the corrected
    # threshold has passes_filters downgraded to False with an updated
    # reject_reason — never the reverse. Restored verbatim from commit
    # 3dbcb1c (accidentally deleted by a later, unrelated commit) in
    # Phase 1C (Blocker D).
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_fdr_correction(self) -> None:
        if not self.pair_results:
            return

        coint_pvalues = np.array([r.coint_pvalue for r in self.pair_results])   # claude code changed: Stage 2D — BH on Engle–Granger p-values (was ordinary-DF p-values)
        _, fdr_pvalues, _, _ = multipletests(
            coint_pvalues, alpha=self.coint_threshold, method='fdr_bh'
        )
        adf_diag = np.array([r.adf_pvalue for r in self.pair_results], dtype=float)   # claude code changed: Stage 2D — diagnostic only
        adf_fdr_diag = np.full(len(adf_diag), np.nan)   # claude code changed: Stage 2D
        finite = np.isfinite(adf_diag)   # claude code changed: Stage 2D
        if finite.any():   # claude code changed: Stage 2D
            adf_fdr_diag[finite] = multipletests(adf_diag[finite], alpha=self.coint_threshold, method='fdr_bh')[1]   # claude code changed: Stage 2D

        n_pass_fdr = 0
        n_downgraded = 0

        for result, p_fdr, p_adf_fdr in zip(self.pair_results, fdr_pvalues, adf_fdr_diag):   # claude code changed: Stage 2D
            result.coint_pvalue_fdr = float(p_fdr)   # claude code changed: Stage 2D — decision
            result.adf_pvalue_fdr = float(p_adf_fdr)   # claude code changed: Stage 2D — diagnostic only
            result.passes_fdr = bool(p_fdr < self.coint_threshold)

            if result.passes_fdr:
                n_pass_fdr += 1

            if result.is_cointegrated and not result.passes_fdr:
                # Was cointegrated under the raw per-pair threshold, but
                # doesn't survive correction across all pairs tested —
                # downgrade the final decision, keep is_cointegrated as the
                # historical record of what the raw test said.
                result.passes_filters = False
                result.reject_reason = (
                    f"failed FDR-corrected significance "
                    f"(p_fdr={p_fdr:.4f} >= {self.coint_threshold}, "
                    f"raw Engle-Granger p={result.coint_pvalue:.4f} was < threshold "   # claude code changed: Stage 2D
                    f"but did not survive correction across "
                    f"{len(self.pair_results)} pairs tested)"
                )
                n_downgraded += 1

        logger.info(
            f"\nStep 2.5: FDR correction across {len(self.pair_results)} pairs — "
            f"{n_pass_fdr} pass FDR at alpha={self.coint_threshold}, "
            f"{n_downgraded} pair(s) downgraded (passed raw threshold, "
            f"failed correction)"
        )


    # ─────────────────────────────────────────────────────────────────────────
    # OUT-OF-SAMPLE PERSISTENCE CORRECTION ACROSS ALL CANDIDATE PAIRS
    # claude code changed: new — see _test_pair()'s "OUT-OF-SAMPLE PERSISTENCE
    # CHECK" comment for the full finding (76/84 previously-valid pairs, 90.5%,
    # failed a second ADF test on post-training pseudo-holdout data (already exposed — not untouched; claude code changed: F-19) using the same frozen
    # hedge ratio). Same "provisional then corrected" pattern as
    # _apply_fdr_correction() immediately above, applied to oos_adf_pvalue
    # instead of adf_pvalue: mutates self.pair_results in place, only ever
    # downgrading passes_filters, never upgrading it.
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_oos_persistence_filter(self) -> None:
        # Only pairs that actually reached the OOS-testing stage in
        # _test_pair() (is_cointegrated=True, oos_adf_pvalue computed) enter
        # this correction — pairs rejected earlier (failed in-sample ADF,
        # OLS failure) never had a real out-of-sample test run at all.
        candidates = [r for r in self.pair_results if r.oos_adf_pvalue is not None]
        if not candidates:
            return

        oos_pvalues = np.array([r.oos_adf_pvalue for r in candidates])
        _, oos_fdr_pvalues, _, _ = multipletests(
            oos_pvalues, alpha=self.coint_threshold, method='fdr_bh'
        )

        n_pass_oos_fdr = 0
        n_downgraded = 0

        for result, p_fdr in zip(candidates, oos_fdr_pvalues):
            result.oos_adf_pvalue_fdr = float(p_fdr)
            result.passes_oos_persistence = bool(p_fdr < self.coint_threshold)

            if result.passes_oos_persistence:
                n_pass_oos_fdr += 1

            if result.passes_filters and not result.passes_oos_persistence:
                # Passed every prior filter (in-sample ADF, in-sample FDR,
                # half-life, raw OOS threshold) but the relationship's
                # persistence doesn't survive correction across every
                # candidate pair tested for it — downgrade the final
                # decision. Pairs the raw OOS check already rejected inside
                # _test_pair() are left alone here (their reject_reason
                # already explains why; FDR-adjusted p-values are always >=
                # raw p-values under BH, so they could never have passed
                # this correction either).
                result.passes_filters = False
                result.reject_reason = (
                    f"failed FDR-corrected out-of-sample persistence "
                    f"(oos_p_fdr={p_fdr:.4f} >= {self.coint_threshold}, "
                    f"raw OOS ADF p={result.oos_adf_pvalue:.4f} was < threshold "
                    f"but did not survive correction across "
                    f"{len(candidates)} candidate pairs tested)"
                )
                n_downgraded += 1

        logger.info(
            f"\nStep 2.6: Out-of-sample persistence correction across "
            f"{len(candidates)} candidate pairs — {n_pass_oos_fdr} pass "
            f"FDR-corrected OOS persistence at alpha={self.coint_threshold}, "
            f"{n_downgraded} pair(s) downgraded (passed every prior filter, "
            f"failed OOS persistence)"
        )


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
                timeframe=self.timeframe,
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
                timeframe=self.timeframe,
                training_window_candles=train_end,
            )

        # ── Calculate residuals (the spread) on training data ─────────────────
        # spread = log(A) - α - β × log(B)
        # This is the deviation from the long-run equilibrium relationship
        spread_train = (
            train["price_a"] -
            intercept -
            hedge_ratio * train["price_b"]
        )

        # ── Step 2: ordinary ADF on the residual — DIAGNOSTIC ONLY ───────────
        # claude code changed: Stage 2D (Stage 2C §5). An ordinary DF p-value on
        # an OLS residual ignores that β̂ was estimated to minimise residual
        # variance, so it over-rejects (Stage 1: 11–15 % false positives at 5 %).
        # It is still recorded for comparison but no longer decides anything.
        try:
            adf_result = adfuller(
                spread_train.dropna(),
                maxlag=20,          # Check lags up to 20 periods
                autolag="AIC",      # Use AIC to select optimal lag
                regression="c",     # Include constant in ADF regression
            )
            adf_pvalue = float(adf_result[1])    # Extract p-value (diagnostic)
        except Exception:   # claude code changed: Stage 2D — a diagnostic failure is recorded as NaN, it does not reject or accept
            adf_pvalue = float("nan")

        # ── Engle–Granger test: THE selection decision ────────────────────────
        # claude code changed: Stage 2D — MacKinnon cointegration p-value via
        # bot.quant_core.cointegration.engle_granger. If it cannot be computed
        # (gap/NaN in the window, numerical failure) the pair is UNTESTABLE,
        # counted in the FDR family at p = 1, never re-tested with another test.
        eg = engle_granger(train["price_a"], train["price_b"], min_obs=train_end,   # claude code changed: Stage 2D — exactly the declared training window (no new threshold); NaN/gaps inside it → UNTESTABLE
                          
                           maxlag=None, autolag="aic")
        if not eg.ok:
            return PairResult(
                symbol_a=symbol_a, symbol_b=symbol_b,
                coint_pvalue=1.0, hedge_ratio=hedge_ratio, intercept=intercept,
                half_life=np.inf, adf_pvalue=adf_pvalue,
                is_cointegrated=False, passes_filters=False,
                reject_reason=f"UNTESTABLE: {eg.reason}",
                timeframe=self.timeframe,
                training_window_candles=train_end,
            )
        coint_pvalue = eg.value.pvalue

        # ── Determine cointegration ───────────────────────────────────────────
        is_cointegrated = coint_pvalue < self.coint_threshold   # claude code changed: Stage 2D — was adf_pvalue (ordinary DF)

        if not is_cointegrated:
            return PairResult(
                symbol_a=symbol_a, symbol_b=symbol_b,
                coint_pvalue=coint_pvalue, hedge_ratio=hedge_ratio,
                intercept=intercept, half_life=np.inf,
                adf_pvalue=adf_pvalue,
                is_cointegrated=False, passes_filters=False,
                reject_reason=f"Engle-Granger p={coint_pvalue:.4f} >= {self.coint_threshold}",   # claude code changed: Stage 2D
                timeframe=self.timeframe,
                training_window_candles=train_end,
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
                f"half-life {half_life:.1f} candles > max {self.max_half_life} candles "
                f"(spread reverts too slowly)"
            )
        elif half_life < self.min_half_life:
            passes_filters = False
            reject_reason  = (
                f"half-life {half_life:.1f} candles < min {self.min_half_life} candles "
                f"(spread reverts too fast for {self.timeframe} candles)"
            )

        # ── OUT-OF-SAMPLE PERSISTENCE CHECK ───────────────────────────────────
        # claude code changed: new — real bug found by out-of-sample audit.
        # Being stationary WITHIN the training window (above) never proved
        # the relationship actually persists — a pair can pass that test by
        # chance or a temporary regime, then break down permanently right
        # after. Applies the SAME frozen hedge_ratio/intercept (no
        # re-fitting — this is a pure persistence check, not a second
        # estimation) to the post-training pseudo-holdout candles (exposed to prior research, so not an untouched holdout — claude code changed: F-19) and re-runs ADF.
        # Empirically this rejected 76 of 84 pairs (90.5%) that had passed
        # the training-window-only test — most by a permanent, multi-sigma
        # mean shift, not noise. oos_adf_pvalue is recorded here at its raw
        # (pre-FDR) value; _apply_oos_persistence_filter() (run_all(), Step
        # 2.6) applies FDR correction across every candidate pair and does
        # the final gating — this raw check can only ever DOWNGRADE
        # passes_filters, mirroring how the half-life filters above and the
        # existing ADF-FDR correction already behave.
        oos = aligned.iloc[train_end:]
        oos_adf_pvalue = 1.0   # Fail-closed default if the OOS check can't run at all
        if len(oos) < self.min_oos_candles:
            passes_filters = False
            reject_reason  = reject_reason or (
                f"insufficient out-of-sample data ({len(oos)} candles < "
                f"{self.min_oos_candles} minimum) to confirm persistence"
            )
        else:
            spread_oos = oos["price_a"] - intercept - hedge_ratio * oos["price_b"]
            try:
                oos_adf_result = adfuller(
                    spread_oos.dropna(), maxlag=20, autolag="AIC", regression="c",
                )
                oos_adf_pvalue = float(oos_adf_result[1])
            except Exception as e:
                passes_filters = False
                reject_reason  = reject_reason or f"out-of-sample ADF test failed: {e}"
                oos_adf_pvalue = 1.0

            if oos_adf_pvalue >= self.coint_threshold:
                passes_filters = False
                reject_reason  = reject_reason or (
                    f"out-of-sample ADF p={oos_adf_pvalue:.4f} >= {self.coint_threshold} "
                    f"— stationary in training window but relationship did not persist "
                    f"out-of-sample (likely a training-window-only artifact, not "
                    f"genuine long-run cointegration)"
                )

        return PairResult(
            symbol_a=symbol_a, symbol_b=symbol_b,
            coint_pvalue=coint_pvalue, hedge_ratio=hedge_ratio,
            intercept=intercept, half_life=half_life,
            adf_pvalue=adf_pvalue,
            is_cointegrated=True, passes_filters=passes_filters,
            reject_reason=reject_reason,
            timeframe=self.timeframe,
            oos_adf_pvalue=oos_adf_pvalue,
            training_window_candles=train_end,
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

        In discrete time (one candle per step, whatever that candle's
        actual width is):
            ΔS_t = λ × S_{t-1} + ε_t

        Where:
            λ = mean-reversion speed (must be negative for mean reversion)
            μ = long-run equilibrium (zero for demeaned spread)
            σ = volatility of the spread
            W_t = Brownian motion (random noise)

        Half-life formula:
            HL = -log(2) / λ

        Interpretation:
            HL = 10 CANDLES means: if the spread is 100 units above
            equilibrium, it will be at 50 units above equilibrium after 10
            candles on average — this function has no notion of wall-clock
            time at all, since it never sees a timeframe, only a spread
            Series indexed by candle. Converting 10 candles to physical
            time (10 hours on 1h data, 40 hours on 4h data, 10 days on
            daily data) is _test_pair()'s/PairResult's job, via
            bot.instruments.candles_to_wall_clock() — see PairResult's
            half_life_time/half_life_time_unit.

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
        # claude code changed: Stage 2D — ffill(limit=3) REMOVED: forward-filling invents prices and turns
        # a gap into zero-change observations (Stage 2C §16). Missing candles are no longer filled; the
        # dropna() below still removes them as rows (row compression). Replacing that with the canonical
        # time grid needs the owner's gap policy (g_max / re-seed, Stage 2C §16) — REMAINING DEFECT.
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
    output_dir: str = "research_data",
    interval:   str = "1h",
    universe:   Optional[List[str]] = None,
    start=None,
    end=None,
) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
    """
    Standalone runner. Fetches all UNIVERSE symbols via the canonical
    MarketDataProvider contract, runs cointegration tests, saves pair
    summary and spread DataFrames, prints results.

    Run from project root:
        python -m bot.research.cointegration_engine

    claude code changed: Data-Layer Architecture Audit migration (F.3,
    DATA_LAYER_ARCHITECTURE_AUDIT.md) — was a direct data_dir/*.csv read
    loop (data_dir param removed, replaced by start/end/`universe`'s own
    asset class implicitly deciding the provider via load_ohlcv_via_provider's
    ASSET_CLASS_CRYPTO default). `start`/`end` default to a 5-year lookback
    ending now, matching this codebase's existing data/*.csv history
    depth — same convention as regime_conditional_pairs.py's driver.

    Returns
    -------
    Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]
        pairs_df    : All pair test results
        spread_data : Valid pair spread DataFrames for validator
    """

    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    logger.info("=" * 70)
    logger.info("COINTEGRATION ENGINE — STANDALONE RUN")
    logger.info("=" * 70)

    if end is None:
        end = datetime.now(timezone.utc)
    if start is None:
        start = end - timedelta(days=5 * 365)

    # ── Fetch all UNIVERSE symbols via the canonical provider contract ─────────
    data: Dict[str, pd.DataFrame] = {}

    for symbol in (universe or UNIVERSE):
        df = load_ohlcv_via_provider(symbol, ASSET_CLASS_CRYPTO, interval, start, end)
        if df is not None:
            data[symbol] = df
            logger.info(f"  Loaded {symbol}: {len(df):,} candles")

    # ── Run engine ────────────────────────────────────────────────────────────
    engine              = CointegrationEngine(universe=universe)
    pairs_df, spread_data = engine.run_all(data)

    # ── Save outputs ──────────────────────────────────────────────────────────
    Path(output_dir).mkdir(exist_ok=True)

    # Save pair summary
    if not pairs_df.empty:
        pairs_path = Path(output_dir) / "cointegration_pairs.csv"
        # claude code changed: 2026-10-04 — every row of this LEGACY scan says how it was selected (first-N training
        # window over each pair's whole history, reading zone V and later). Canonical selection: run_selection_zone_screen.
        pairs_df = pairs_df.assign(selection_basis="LEGACY_FIRST_N_TRAINING_WINDOW")
        logger.warning("  cointegration_pairs.csv is a LEGACY selection (not zone-S, not D-3) — never canonical evidence")
        pairs_df.to_csv(pairs_path, index=False)
        write_legacy_provenance(pairs_path, kind="COINTEGRATION_SCAN", producer="bot.research.cointegration_engine")   # claude code changed: Stage 2D Rev B G-1
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
            f"{'β':>8} {'HL(h)':>8} {'Status':>16}"
        )
        logger.info("-" * 70)
        for _, row in pairs_df.iterrows():
            status = _rejection_status_label(row)
            logger.info(
                f"  {row['pair_name']:20} "
                f"{row['coint_pvalue']:>9.4f} "
                f"{row['adf_pvalue']:>8.4f} "
                f"{row['hedge_ratio']:>8.4f} "
                f"{row['half_life_hours']:>8.1f} "
                f"{status:>16}"
            )

    return pairs_df, spread_data


# claude code changed: real bug fix — the print loop above used to collapse
# every "is_cointegrated=True but passes_filters=False" pair under one
# label, "⚠ COINT/NO HL", regardless of WHICH filter actually rejected it
# (half-life out of range, insufficient out-of-sample data, the raw OOS
# persistence check, or either FDR-correction downgrade in run_all()'s
# Step 2.5/2.6). That made every one of those distinct, already-recorded
# reject_reason strings (see _test_pair()/_apply_fdr_correction()/
# _apply_oos_persistence_filter()) invisible in the summary table a user
# actually reads — e.g. a pair with a perfectly in-range half-life (well
# inside [min_half_life, max_half_life]) still printed "NO HL", which
# reads as a half-life failure it never had. This derives a specific,
# short tag straight from the real reject_reason text instead.
def _rejection_status_label(row: pd.Series) -> str:
    if row["passes_filters"]:
        return "✓ VALID"
    if not row["is_cointegrated"]:
        return "✗ NONE"

    reason = (row.get("reject_reason") or "").lower()
    if reason.startswith("half-life"):
        return "⚠ HALF-LIFE"
    if "insufficient out-of-sample data" in reason:
        return "⚠ OOS DATA"
    if "out-of-sample adf test failed" in reason:
        return "⚠ OOS ERROR"
    if "fdr-corrected out-of-sample persistence" in reason:
        return "⚠ OOS FDR"
    if "did not persist out-of-sample" in reason:
        return "⚠ OOS FAILED"
    if "fdr-corrected significance" in reason:
        return "⚠ FDR"
    # Cointegrated, rejected, but for a reason not covered above — surfaced
    # generically rather than mislabeled as a specific filter that didn't
    # actually reject it. Check reject_reason directly if this appears.
    return "⚠ COINT/OTHER"


# ── Entry point ───────────────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# claude code changed: 2026-10-04 — CANONICAL SELECTION IN ZONE S (owner decisions D-8 + D-3). Step 2 of the
# post-decision-sheet plan. The legacy run_all()/_test_pair() path fits on the FIRST N candles of each pair's
# whole history and checks persistence on the rest — i.e. it reads zone V (and anything later). The functions
# below reuse the SAME single-pair test (_test_pair: Engle–Granger + the engine's existing half-life and
# persistence filters, unchanged) but feed it ONLY zone S candles, and route the scan's p-values through the
# D-3 exploratory screen (BH q = 0.10, zone S only, labelled EXPLORATORY — NOT CLAIMABLE, ≤ 5 nominated).
#
#   coverage   both legs observed at or before S_start and at or after S_end; no gap > protocol.gap_max_candles
#              (existing frozen gap policy, D8) inside zone S — otherwise UNTESTABLE (never filled)
#   seed       a pair is NOMINATABLE only if the canonical causal seed exists for it (L = 1000 complete grid
#              candles immediately before S_start) — checked with the real causal_init, not re-implemented
#   family     every TESTABLE pair of the scan is one exploratory BH family (filters only ever downgrade after
#              correction, never shrink the family before it)
#   seal       any candle ≥ F (sealed holdout) in the input is refused
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

SELECTION_SCREEN_VERSION = "QBP-COINT-ZONE-S-1"


def _zone_s_slice(log_a: pd.Series, log_b: pd.Series, *, zones=None, protocol=None, calendar=None,
                  gap_max_candles=None):   # claude code changed: 2026-10-04 asset-agnostic — optional session calendar + its owner g_max
    """(aligned zone-S frame, reason). Frame has columns a, b on the timestamps both legs observed in S.
    calendar None / continuous: the unchanged REVB-4 (crypto) rule — any gap > g_max grid candles fails.
    SessionCalendar: scheduled-closure hours are not on the grid (Decision 5 A); only missing TRADING hours count
    against `gap_max_candles`, which is an owner value with no default (Decision 5 B)."""
    from bot.quant_core.protocol import CANONICAL
    from bot.quant_core.research_zones import ZONES
    zones, protocol = zones or ZONES, protocol or CANONICAL
    for s in (log_a, log_b):
        zones.assert_no_sealed_data(s.index, purpose="cointegration selection screen")
    both = pd.concat({"a": log_a, "b": log_b}, axis=1, join="inner").dropna().sort_index()
    idx = pd.DatetimeIndex(both.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    both.index = idx
    if len(both) == 0 or both.index[0] > zones.S0 or both.index[-1] < zones.S1:
        return None, (f"does not cover zone S ({zones.S0} → {zones.S1}); "
                      f"data spans {both.index[0] if len(both) else None} → {both.index[-1] if len(both) else None}")
    s = both.loc[(both.index >= zones.S0) & (both.index <= zones.S1)]
    if calendar is not None and not calendar.is_continuous:   # claude code changed: 2026-10-04 asset-agnostic — trading-grid gap rule
        from bot.quant_core.market_profile import assert_no_closure_rows, max_missing_trading_run
        if gap_max_candles is None:
            raise ValueError("a session calendar needs its own unexpected-gap g_max (owner value, Decision 5 B)")
        assert_no_closure_rows(s.index, calendar, purpose="cointegration selection screen")
        run = max_missing_trading_run(s.index, calendar, zones.S0, zones.S1)
        if run > gap_max_candles:
            return None, f"{run} consecutive missing trading candles inside zone S exceed the {gap_max_candles}-candle gap policy"
        return s, ""
    max_gap = (s.index[1:] - s.index[:-1]).max() if len(s) > 1 else pd.Timedelta(0)
    allowed = pd.Timedelta(hours=protocol.gap_max_candles + 1)
    if max_gap > allowed:
        return None, f"gap of {max_gap} inside zone S exceeds the {protocol.gap_max_candles}-candle gap policy"
    return s, ""


def _seed_eligibility(raw_a: pd.Series, raw_b: pd.Series, *, protocol=None):
    """(eligible, reason) using the canonical seed code path (KalmanFilterEngine alignment + causal_init)."""
    from bot.quant_core.protocol import CANONICAL
    from bot.quant_core.seed import causal_init
    from bot.research.kalman_filter_engine import KalmanFilterEngine
    protocol = protocol or CANONICAL
    eng = KalmanFilterEngine(pair_name="A/B", symbol_a="A", symbol_b="B", ols_beta=1.0, ols_alpha=0.0)
    try:
        aligned = eng._align_prices(raw_a, raw_b)
        la, lb = eng._to_log_prices(aligned)
        causal_init(la, lb, protocol)
        return True, ""
    except Exception as e:      # any refusal of the canonical seed makes the pair non-nominatable
        return False, f"no canonical seed: {e}"


def _market_block(symbol_a, symbol_b, close_a, close_b, zones) -> Optional[Dict]:   # claude code changed: 2026-10-04 asset-agnostic — new
    """None when the pair may run under its market profile; otherwise a BLOCKED result (never UNTESTABLE or
    REJECTED: an undecided market rule is not a data problem and not negative evidence). The holdout seal is
    checked first, for every asset class."""
    from bot.quant_core.market_profile import (AssetClassMismatch, OwnerDecisionMissing, assert_continuous_market,
                                               profile_for_pair)
    for s in (close_a, close_b):
        zones.assert_no_sealed_data(s.index, purpose="cointegration selection screen")
    try:
        market = profile_for_pair(symbol_a, symbol_b).require_canonical(f"zone-S selection {symbol_a}/{symbol_b}")
        if market.calendar.is_continuous:
            assert_continuous_market(close_a.dropna().index, purpose=f"zone-S selection {symbol_a}")
            assert_continuous_market(close_b.dropna().index, purpose=f"zone-S selection {symbol_b}")
        return None
    except (OwnerDecisionMissing, AssetClassMismatch) as e:
        return {"symbol_a": symbol_a, "symbol_b": symbol_b, "pair_name": f"{symbol_a}/{symbol_b}",
                "selection_basis": "ZONE_S_D8", "selection_screen_version": SELECTION_SCREEN_VERSION,
                "zones_hash": zones.zones_hash(), "zone_s": [zones.S0.isoformat(), zones.S1.isoformat()],
                "status": "BLOCKED", "reject_reason": str(e), "is_cointegrated": None, "passes_filters": None,
                "coint_pvalue": None, "seed_eligible": False, "seed_reason": str(e)}


def pair_test_in_selection_zone(symbol_a: str, symbol_b: str, close_a: pd.Series, close_b: pd.Series, *,
                                timeframe: str = "1h", zones=None, protocol=None) -> Dict:
    """One pair, zone S only. Returns the PairResult dict (same keys as the legacy single-pair test) plus
    selection_basis / status / seed eligibility / zone provenance."""
    from bot.quant_core.research_zones import ZONES
    zones = zones or ZONES
    blocked = _market_block(symbol_a, symbol_b, close_a, close_b, zones)   # claude code changed: 2026-10-04 asset-agnostic
    if blocked is not None:
        return blocked
    log_a = np.log(close_a.astype(float).clip(lower=1e-12))
    log_b = np.log(close_b.astype(float).clip(lower=1e-12))
    eligible, seed_reason = _seed_eligibility(close_a, close_b, protocol=protocol)
    base = {"symbol_a": symbol_a, "symbol_b": symbol_b, "selection_basis": "ZONE_S_D8",
            "selection_screen_version": SELECTION_SCREEN_VERSION, "zones_hash": zones.zones_hash(),
            "zone_s": [zones.S0.isoformat(), zones.S1.isoformat()],
            "seed_eligible": eligible, "seed_reason": seed_reason}
    s, reason = _zone_s_slice(log_a, log_b, zones=zones, protocol=protocol)
    if s is None:
        return {**base, "status": "UNTESTABLE", "reject_reason": reason, "is_cointegrated": False,
                "passes_filters": False, "coint_pvalue": None}
    engine = CointegrationEngine(timeframe=timeframe)
    pr = engine._test_pair(symbol_a, symbol_b, s["a"], s["b"])
    out = pr.to_dict()
    untestable = (pr.reject_reason or "").startswith("UNTESTABLE")
    out.update(base)
    out.update({"status": "UNTESTABLE" if untestable else "OK", "n_zone_s_candles": int(len(s))})
    return out


# claude code changed: 2026-10-04 — PARALLEL SELECTION SCREEN. Each pair test is a pure, deterministic function of
# its two close series (no shared state, no randomness), so pairs can be tested in separate processes. Workers get
# the universe's closes ONCE (initializer) and receive only (symbol_a, symbol_b) per task; ProcessPoolExecutor.map
# returns results in the ORIGINAL pair order, and the exploratory BH correction runs once, in the parent, over the
# merged rows — exactly as in the serial path. A worker exception (e.g. a sealed-holdout candle) is re-raised in the
# parent: nothing is skipped silently. Serial and parallel outputs are tested to be identical.
_SCREEN_WORKER_STATE = None


def _screen_worker_init(closes, timeframe, zones, protocol):   # claude code changed: 2026-10-04 — new (runs in each worker)
    global _SCREEN_WORKER_STATE
    logging.disable(logging.WARNING)
    _SCREEN_WORKER_STATE = (closes, timeframe, zones, protocol)


def _screen_worker_pair(pair):   # claude code changed: 2026-10-04 — new (runs in each worker)
    closes, timeframe, zones, protocol = _SCREEN_WORKER_STATE
    a, b = pair
    return pair_test_in_selection_zone(a, b, closes[a], closes[b], timeframe=timeframe, zones=zones, protocol=protocol)


def run_selection_zone_screen(closes: Dict[str, pd.Series], pairs: Optional[List[Tuple[str, str]]] = None, *,
                              timeframe: str = "1h", zones=None, protocol=None,
                              n_workers: int = 1) -> Dict:   # claude code changed: 2026-10-04 — n_workers > 1 tests pairs in parallel processes (identical results)
    """The canonical selection stage (D-8 zone S + D-3 exploration). Never claimable — it can only NOMINATE at
    most 5 pairs for D-3 registration and a later single confirmatory test in zone V."""
    from itertools import combinations
    from bot.quant_core.hypothesis_registry import exploratory_screen
    from bot.quant_core.protocol import CANONICAL, METHODOLOGY_VERSION
    from bot.quant_core.research_zones import ZONES
    zones, protocol = zones or ZONES, protocol or CANONICAL
    pairs = pairs if pairs is not None else list(combinations(sorted(closes), 2))
    if n_workers is None or int(n_workers) <= 1 or len(pairs) < 2:
        rows = [pair_test_in_selection_zone(a, b, closes[a], closes[b], timeframe=timeframe, zones=zones,
                                            protocol=protocol) for a, b in pairs]
    else:   # claude code changed: 2026-10-04 — parallel path; same function per pair, original order kept
        from concurrent.futures import ProcessPoolExecutor
        needed = {s: closes[s] for s in sorted({s for p in pairs for s in p})}
        n_workers = int(n_workers)
        chunk = max(1, len(pairs) // (n_workers * 8))
        with ProcessPoolExecutor(max_workers=n_workers, initializer=_screen_worker_init,
                                 initargs=(needed, timeframe, zones, protocol)) as ex:
            rows = list(ex.map(_screen_worker_pair, [tuple(p) for p in pairs], chunksize=chunk))
    testable = [r for r in rows if r["status"] == "OK" and r.get("coint_pvalue") is not None]
    family_note = (f"one exploratory BH family = all {len(testable)} testable pairs of this scan "
                   f"(of {len(rows)} requested), Engle–Granger p on zone S only")
    if testable:
        s_index = pd.date_range(zones.S0, zones.S1, freq="h")
        screen = exploratory_screen({r["pair_name"]: float(r["coint_pvalue"]) for r in testable}, s_index, zones=zones)
        ok_after = {r["pair_name"] for r in testable if r["passes_filters"] and r["seed_eligible"]}
        survivors = [p for p in screen["survivors"] if p in ok_after]   # downgrade-only, after correction
        nominated = survivors[:5]
    else:
        screen, survivors, nominated = {"p_bh": {}, "survivors": [], "label": None}, [], []
    for r in rows:
        r["p_bh_exploratory"] = screen["p_bh"].get(r.get("pair_name")) if testable else None
        r["nominated"] = r.get("pair_name") in nominated
    from bot.quant_core.hypothesis_registry import EXPLORATORY_LABEL
    return {"label": EXPLORATORY_LABEL, "claimable": False, "selection_screen_version": SELECTION_SCREEN_VERSION,
            "methodology_version": METHODOLOGY_VERSION, "protocol_hash": protocol.config_hash(),
            "zones_hash": zones.zones_hash(), "family_definition": family_note,
            "half_life_bounds_candles": [MIN_HALF_LIFE_HOURS, MAX_HALF_LIFE_HOURS],
            "n_requested": len(rows), "n_testable": len(testable), "bh_survivors": screen["survivors"],
            "survivors_after_filters": survivors, "nominated": nominated, "pairs": rows}


def run_selection_screen_on_data(data_dir: str = "data", symbols: Optional[List[str]] = None, *, n_workers: int = 1,
                                 output_path: Optional[str] = None, timeframe: str = "1h") -> Dict:   # claude code changed: 2026-10-04 — new
    """Load <SYMBOL>_1h.csv closes (crypto: *_USDT_1h.csv in `data_dir` when `symbols` is None) and run the zone-S
    selection screen. Every symbol's FULL file is passed in, so the seal check sees any sealed candle and refuses.
    `output_path` (JSON) is written only if it does not exist yet — an earlier screen is never overwritten."""
    import glob
    import json
    from pathlib import Path
    if output_path is not None and Path(output_path).exists():
        raise FileExistsError(f"{output_path} already exists — a screen result is never overwritten; choose a new path")
    if symbols is None:
        symbols = sorted(Path(p).name[:-len("_1h.csv")] for p in glob.glob(str(Path(data_dir) / "*_USDT_1h.csv")))
    closes = {}
    for sym in symbols:
        d = pd.read_csv(Path(data_dir) / f"{sym}_1h.csv", usecols=["timestamp", "close"], float_precision="round_trip")
        closes[sym] = pd.Series(d["close"].to_numpy(float), index=pd.to_datetime(d["timestamp"], utc=True))
    result = run_selection_zone_screen(closes, timeframe=timeframe, n_workers=n_workers)
    result["universe"] = {"data_dir": str(data_dir), "symbols": list(symbols), "n_symbols": len(symbols)}
    if output_path is not None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "x", encoding="utf-8") as f:   # "x": fail if it appeared meanwhile
            json.dump(result, f, indent=1, default=str)
    return result


if __name__ == "__main__":
    run_cointegration_research(
        output_dir="research_data",
        interval="1h",
    )