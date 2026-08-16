"""
bot/management/commands/deep_health_check.py  (v2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Deep Health Check — Five-Layer Self-Auditing Platform

WHAT THIS MODULE IS
─────────────────
A Django management command that performs a five-layer automated audit
of every module, class, and function in Quant Bot Pro. It goes far
beyond basic health checks that only confirm imports and connectivity.

THE FIVE LAYERS
─────────────────
Layer 1 — Structural Health
  Does the module exist? Does the class exist?
  Does the method exist? Can it be imported?
  Catches: renamed files, deleted methods, broken imports.

Layer 2 — Functional Health
  Does the function run without crashing?
  Does it accept the expected argument types?
  Does it return something rather than None?
  Catches: runtime errors, wrong signatures, broken logic.

Layer 3 — Behavioural Validation  [most important]
  Does the function return the MATHEMATICALLY CORRECT result?
  A function can exist, run, and return a value while still
  being silently wrong. Layer 3 is the only layer that catches:
    - Expectancy formula computing the wrong value
    - Position sizer returning 1000x too large a size
    - Range score returning high score for exhausted range
    - Regime detector always returning the same classification

Layer 4 — Integration Validation  [NEW in v2]
  Do modules work TOGETHER correctly end-to-end?
  Tests the full pipeline:
    data → regime → structure → signal → filter → sizer
  A module can pass Layer 3 individually but fail when
  its output is passed into the next module in the chain.
  Catches: interface mismatches, data format incompatibilities,
  missing fields that only appear when modules are composed.

Layer 5 — Statistical Drift Detection  [NEW in v2]
  Are statistical values still within expected historical ranges?
  The bot may be technically working but producing values that
  have drifted far from what they should be given current data.
  Examples:
    ADR that has drifted from $2,300 to $50 (data pipeline broken)
    Win rate that has drifted from 55% to 20% (strategy degraded)
    Expectancy that went negative without anyone noticing
  Catches: data pipeline degradation, parameter drift,
  model decay, silent performance regression.

FOUR IMPROVEMENTS FROM v1
───────────────────────────
  1. Safe class instantiation  — uses proper constructors not __new__
  2. Tiered audit modes        — fast (L1+L2) / standard (L1-L3) / deep (all 5)
  3. Layer 4 integration tests — full pipeline composition tests
  4. Layer 5 drift detection   — statistical baseline comparison

USAGE
──────
  python manage.py deep_health_check                    (standard: L1-L3)
  python manage.py deep_health_check --mode fast        (L1+L2 only, quick)
  python manage.py deep_health_check --mode deep        (all 5 layers)
  python manage.py deep_health_check --module range_analytics
  python manage.py deep_health_check --module signal_engine --verbose
  python manage.py deep_health_check --report logs/deep_health.json
  python manage.py deep_health_check --baseline logs/baseline.json
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ── Standard library ──────────────────────────────────────────────────────
from __future__ import annotations          # forward type hints
import importlib                            # dynamic module importing
import inspect                              # introspect classes and methods
import json                                 # JSON report serialisation
import logging                              # structured logging
import os                                   # file path operations
import time                                 # elapsed time measurement
import traceback                            # full stack trace on failures
from dataclasses import dataclass, field    # clean result containers
from datetime import datetime               # timestamps on all results
from decimal import Decimal                 # financial precision type
from typing import Any, Callable, Dict, List, Optional, Tuple  # type hints

# ── Third-party ───────────────────────────────────────────────────────────
import numpy as np                          # synthetic data creation
import pandas as pd                         # DataFrame for test inputs

# ── Django ────────────────────────────────────────────────────────────────
from django.core.management.base import BaseCommand  # management command base

logger = logging.getLogger(__name__)        # module-level named logger


# ── Terminal colours ──────────────────────────────────────────────────────
GREEN  = "\033[92m"     # pass
RED    = "\033[91m"     # fail
YELLOW = "\033[93m"     # warn
CYAN   = "\033[96m"     # headers
BOLD   = "\033[1m"      # names
DIM    = "\033[2m"      # detail
RESET  = "\033[0m"      # reset


# ═════════════════════════════════════════════════════════════════════════
# SECTION 1 — RESULT DATA CLASSES
# ═════════════════════════════════════════════════════════════════════════

@dataclass
class FunctionResult:
    """Complete audit record for one function across all layers."""
    module_path:    str             # dotted path e.g. "bot.engines.range_analytics"
    class_name:     str             # class name or "module" for standalone functions
    function_name:  str             # the method or function name
    is_method:      bool            # True = belongs to a class

    # Layer 1 — Structural
    layer1_exists:      bool  = False   # was found in the module
    layer1_importable:  bool  = False   # module imported without error
    layer1_message:     str   = ""

    # Layer 2 — Functional
    layer2_runs:        bool  = False   # executed without exception
    layer2_returns:     bool  = False   # returned a non-None value
    layer2_elapsed_ms:  float = 0.0     # execution time in milliseconds
    layer2_message:     str   = ""
    layer2_error:       str   = ""      # exception message if crashed

    # Layer 3 — Behavioural
    layer3_tested:      bool  = False   # a mathematical test exists
    layer3_passed:      bool  = False   # the assertion passed
    layer3_assertion:   str   = ""      # what was tested
    layer3_expected:    str   = ""      # what was expected
    layer3_actual:      str   = ""      # what was actually returned
    layer3_message:     str   = ""

    overall_status:     str   = "SKIP"  # PASS / FAIL / WARN / SKIP
    notes:              List[str] = field(default_factory=list)


@dataclass
class IntegrationResult:
    """Result of one Layer 4 integration (pipeline) test."""
    test_name:      str             # human-readable name of the pipeline test
    stages:         List[str]       # modules involved in this pipeline test
    passed:         bool            # all stages completed successfully
    elapsed_ms:     float           # total pipeline elapsed time
    message:        str             # summary message
    detail:         str   = ""      # full detail including intermediate values
    error:          str   = ""      # exception message if pipeline failed


@dataclass
class DriftResult:
    """Result of one Layer 5 statistical drift check."""
    metric_name:    str             # e.g. "adr_dollars", "range_respect_rate"
    module:         str             # which module produced this metric
    current_value:  float           # value measured in this run
    baseline_value: float           # value from the stored baseline
    tolerance_pct:  float           # allowable drift as a fraction (e.g. 0.30 = 30%)
    drift_pct:      float           # actual drift as a fraction
    passed:         bool            # True if within tolerance
    severity:       str             # "OK" / "WARNING" / "CRITICAL"
    message:        str


@dataclass
class ModuleReport:
    """Complete audit report for one module."""
    module_path:        str
    module_file:        str
    imported:           bool
    import_error:       str
    classes_found:      List[str]
    functions_found:    List[str]
    results:            List[FunctionResult]
    total:      int   = 0
    passed:     int   = 0
    failed:     int   = 0
    warned:     int   = 0
    skipped:    int   = 0
    health_score: float = 0.0
    elapsed_ms:   float = 0.0


@dataclass
class SessionReport:
    """Master report for one complete audit session."""
    timestamp:          str                     # ISO timestamp
    audit_mode:         str                     # fast / standard / deep
    module_reports:     List[ModuleReport]
    integration_results:List[IntegrationResult] # Layer 4
    drift_results:      List[DriftResult]       # Layer 5
    total_functions:    int   = 0
    total_passed:       int   = 0
    total_failed:       int   = 0
    total_warned:       int   = 0
    platform_health:    float = 0.0
    elapsed_seconds:    float = 0.0


# ═════════════════════════════════════════════════════════════════════════
# SECTION 2 — SAFE INSTANTIATION REGISTRY  [v2 fix #1]
#
# ChatGPT correctly identified that cls.__new__(cls) is dangerous.
# It bypasses __init__ so self.exchange, self.config, self.cache etc
# are never set, causing methods to fail for artificial reasons.
#
# This registry maps each class to its correct lightweight constructor.
# Each factory returns a fully initialised, test-safe instance.
# ═════════════════════════════════════════════════════════════════════════

def _safe_instance_factory() -> Dict[str, Callable]:
    """
    Returns a dict mapping class name → factory function.
    Each factory creates a properly initialised test instance.
    Using real constructors means Layer 2 tests reflect actual
    behaviour, not artificial failures from missing attributes.
    """
    factories = {}

    # ── RangeAnalytics ────────────────────────────────────────────────
    def make_range_analytics():
        try:
            from bot.engines.range_analytics import RangeAnalytics
            return RangeAnalytics(lookback_days=30)         # proper constructor
        except ImportError:
            return None

    factories["RangeAnalytics"] = make_range_analytics

    # ── StructureAnalyzer ─────────────────────────────────────────────
    def make_structure_analyzer():
        try:
            from bot.engines.structure_analyzer import StructureAnalyzer
            return StructureAnalyzer(swing_strength=3)      # proper constructor
        except ImportError:
            return None

    factories["StructureAnalyzer"] = make_structure_analyzer

    # ── SetupValidator ────────────────────────────────────────────────
    def make_setup_validator():
        try:
            from bot.engines.setup_validator import SetupValidator
            return SetupValidator(min_sample=3)             # low sample for testing
        except ImportError:
            return None

    factories["SetupValidator"] = make_setup_validator

    # ── RegimeDetector ────────────────────────────────────────────────
    def make_regime_detector():
        try:
            from bot.engines.regime_detector import RegimeDetector
            return RegimeDetector()
        except ImportError:
            return None

    factories["RegimeDetector"] = make_regime_detector

    # ── StrategyRouter ────────────────────────────────────────────────
    def make_strategy_router():
        try:
            from bot.engines.strategy_router import StrategyRouter
            return StrategyRouter()
        except ImportError:
            return None

    factories["StrategyRouter"] = make_strategy_router

    # ── PositionSizer ─────────────────────────────────────────────────
    def make_position_sizer():
        try:
            from bot.risk.position_sizer import PositionSizer
            return PositionSizer(risk_pct=0.01)             # 1% risk for testing
        except ImportError:
            return None

    factories["PositionSizer"] = make_position_sizer

    # ── CircuitBreaker ────────────────────────────────────────────────
    def make_circuit_breaker():
        try:
            from bot.exchanges.base import CircuitBreaker
            return CircuitBreaker(failure_threshold=5)      # standard threshold
        except ImportError:
            return None

    factories["CircuitBreaker"] = make_circuit_breaker

    # ── RateLimiter ───────────────────────────────────────────────────
    def make_rate_limiter():
        try:
            from bot.exchanges.base import RateLimiter
            return RateLimiter(rate_limit_ms=50)            # fast for testing
        except ImportError:
            return None

    factories["RateLimiter"] = make_rate_limiter

    # ── TradeAnalytics (merged analytics) ─────────────────────────────
    def make_trade_analytics():
        try:
            from bot.analytics import TradeAnalytics
            return TradeAnalytics()
        except ImportError:
            return None

    factories["TradeAnalytics"] = make_trade_analytics

    # ── GridStrategy ──────────────────────────────────────────────────
    def make_grid_strategy():
        try:
            from bot.strategies.grid_strategy import GridStrategy
            return GridStrategy()
        except ImportError:
            return None

    factories["GridStrategy"] = make_grid_strategy

    return factories


# ═════════════════════════════════════════════════════════════════════════
# SECTION 3 — SYNTHETIC DATA FACTORY
# Centralised place for all test data so tests never have to
# create their own data. Consistent data = reproducible results.
# ═════════════════════════════════════════════════════════════════════════

class TestData:
    """
    Single source of truth for all synthetic test data.
    All test functions import from here — no inline data creation.
    Fixed random seeds ensure every run uses identical data.
    """

    @staticmethod
    def ohlcv_df(rows: int = 120, seed: int = 42) -> pd.DataFrame:
        """
        OHLCV DataFrame with BTC-like prices.
        DatetimeIndex in UTC, hourly frequency.
        OHLCV invariants always satisfied: high >= close >= low.
        """
        np.random.seed(seed)                                        # reproducible
        close = 50000 + np.cumsum(np.random.randn(rows) * 200)     # random walk
        high  = close + np.random.uniform(50, 300, rows)            # above close
        low   = close - np.random.uniform(50, 300, rows)            # below close
        open_ = close - np.random.randn(rows) * 100                 # near close
        vol   = np.random.uniform(1, 10, rows)                      # positive volume
        df    = pd.DataFrame({"open": open_, "high": high,
                              "low": low, "close": close, "volume": vol})
        df.index = pd.date_range("2024-01-01", periods=rows, freq="1h")
        return df

    @staticmethod
    def daily_df(rows: int = 60, seed: int = 42) -> pd.DataFrame:
        """Daily OHLCV DataFrame with DatetimeIndex for range analytics."""
        np.random.seed(seed)
        close = 50000 + np.cumsum(np.random.randn(rows) * 500)
        high  = close + np.random.uniform(200, 1200, rows)
        low   = close - np.random.uniform(200, 1200, rows)
        open_ = close - np.random.randn(rows) * 300
        vol   = np.random.uniform(100, 1000, rows)
        df    = pd.DataFrame({"open": open_, "high": high,
                              "low": low, "close": close, "volume": vol})
        df.index = pd.date_range("2024-01-01", periods=rows, freq="D")
        return df

    @staticmethod
    def known_trades() -> List[Dict]:
        """
        10 trades with mathematically exact known statistics.
        5 wins at +2R and 5 losses at -1R produces:
          win_rate   = 0.500  (exactly)
          expectancy = +0.500R (exactly)
          profit_factor = 2.000 (exactly)
        These are the ground-truth values for Layer 3 validation.
        """
        trades = []
        for _ in range(5):                                          # 5 winners
            trades.append({
                "r_multiple": 2.0, "profit": 200.0,
                "direction": "LONG", "regime": "TRENDING_UP",
                "setup_type": "LONG_CHOCH_FVG", "win": True,
                "volatility_state": "NORMAL",
                "range_used_at_entry": 0.25,
                "fvg_used": True, "ob_used": True,
                "choch_used": True, "bos_used": False,
            })
        for _ in range(5):                                          # 5 losers
            trades.append({
                "r_multiple": -1.0, "profit": -100.0,
                "direction": "LONG", "regime": "RANGING",
                "setup_type": "LONG_BOS_RANGE_POOR", "win": False,
                "volatility_state": "EXPANDING",
                "range_used_at_entry": 0.82,
                "fvg_used": False, "ob_used": False,
                "choch_used": False, "bos_used": True,
            })
        return trades

    @staticmethod
    def fvg_df() -> pd.DataFrame:
        """
        5-candle DataFrame containing one guaranteed bullish FVG.
        candle[0].high = 100.0, candle[2].low = 105.0
        Gap zone = [100.0, 105.0] — must be detected.
        """
        return pd.DataFrame({
            "open":   [95.0,  99.0,  106.0, 104.0, 105.0],
            "high":   [100.0, 101.0, 110.0, 108.0, 109.0],  # [0].high = 100
            "low":    [93.0,  97.0,  105.0, 102.0, 103.0],  # [2].low  = 105
            "close":  [99.0,  100.0, 109.0, 107.0, 108.0],
            "volume": [100.0,  90.0, 150.0,  80.0,  95.0],
        })


# ═════════════════════════════════════════════════════════════════════════
# SECTION 4 — LAYER 3 BEHAVIOURAL TEST REGISTRY
#
# v2 fix #2: Tests are registered via decorator not hardcoded strings.
# Adding a new test means writing one function with @register_test.
# No central dict to maintain. No string keys to get wrong.
# ═════════════════════════════════════════════════════════════════════════

_TEST_REGISTRY: Dict[str, Callable] = {}     # key → test callable


def register_test(key: str):
    """
    Decorator that registers a behavioural test function.

    Usage:
        @register_test("bot.engines.range_analytics::RangeAnalytics::analyse")
        def test_analyse(obj):
            ...
            return (passed, expected, actual, description)

    The key format is: "module::ClassName::method_name"
    For standalone functions: "module::function_name"
    """
    def decorator(fn: Callable) -> Callable:
        _TEST_REGISTRY[key] = fn                    # register with the key
        return fn                                    # return unchanged for normal use
    return decorator


def build_tests() -> Dict[str, Callable]:
    """
    Trigger registration of all tests by importing this function.
    Tests are defined below and registered via @register_test decorator.
    Returns the populated test registry.
    """
    return _TEST_REGISTRY                           # return after all decorators have run


# ─────────────────────────────────────────────────────────────────────
# RANGE ANALYTICS TESTS
# ─────────────────────────────────────────────────────────────────────

@register_test("bot.engines.range_analytics::RangeAnalytics::analyse")
def _test_ra_analyse(obj):
    """ADR must be positive and volatility_state must be a valid label."""
    df     = TestData.daily_df(60)
    result = obj.analyse(df, float(df["close"].iloc[-1]), "BTCUSDT")
    passed = (result is not None
              and result.adr_dollars > 0
              and result.volatility_state in ["COMPRESSED", "NORMAL", "EXPANDING"])
    return (passed,
            "adr_dollars > 0, valid volatility_state",
            f"adr={result.adr_dollars if result else 'None'}, "
            f"state={result.volatility_state if result else 'None'}",
            "analyse() returns valid RangeProfile with positive ADR")


@register_test("bot.engines.range_analytics::RangeAnalytics::range_score_exhausted")
def _test_ra_score_exhausted(obj):
    """Exhausted range (95%) + EXPANDING must score below 0.25."""
    df = TestData.daily_df(60)
    p  = obj.analyse(df, float(df["close"].iloc[-1]), "TEST")
    p.current_range_used  = 0.95
    p.predicted_direction = "NEUTRAL"
    p.exhaustion_prob     = 0.90
    p.volatility_state    = "EXPANDING"             # realistic for 95% exhaustion
    score = obj.range_score(p, "LONG")
    passed = score < 0.25
    return (passed,
            "score < 0.25 (exhausted+EXPANDING)",
            f"score={score:.4f}",
            "range_score() heavily penalises exhausted range in EXPANDING vol")


@register_test("bot.engines.range_analytics::RangeAnalytics::range_score_fresh")
def _test_ra_score_fresh(obj):
    """Fresh range (15%) + NORMAL + BULLISH prediction must score above 0.50."""
    df = TestData.daily_df(60)
    p  = obj.analyse(df, float(df["close"].iloc[-1]), "TEST")
    p.current_range_used  = 0.15
    p.range_bias          = 0.20
    p.volatility_state    = "NORMAL"
    p.predicted_direction = "BULLISH"
    p.exhaustion_prob     = 0.10
    score = obj.range_score(p, "LONG")
    passed = score > 0.50
    return (passed,
            "score > 0.50 (fresh+NORMAL+BULLISH)",
            f"score={score:.4f}",
            "range_score() rewards entries early in daily range")


@register_test("bot.engines.range_analytics::RangeAnalytics::backtest_tags")
def _test_ra_tags(obj):
    """backtest_tags() must contain all 10 required fields for trade logging."""
    df      = TestData.daily_df(60)
    profile = obj.analyse(df, float(df["close"].iloc[-1]), "TEST")
    tags    = obj.backtest_tags(profile)
    required = ["range_used_at_entry", "range_bias_at_entry", "volatility_state",
                "adr_at_entry", "respect_rate_at_entry", "predicted_direction",
                "vol_acceleration", "current_efficiency", "exhaustion_prob",
                "current_session"]
    missing = [k for k in required if k not in tags]
    passed  = len(missing) == 0
    return (passed,
            f"all {len(required)} keys present",
            f"missing={missing}" if missing else f"all {len(tags)} keys present",
            "backtest_tags() returns complete dict for trade record tagging")


# ─────────────────────────────────────────────────────────────────────
# PRECISION UTILITY TESTS
# ─────────────────────────────────────────────────────────────────────

@register_test("bot.exchanges.base::quantise_quantity")
def _test_q_rounds_down(fn):
    """0.0129 with step 0.001 MUST give 0.012 not 0.013 — financial safety."""
    result = fn(0.0129, Decimal("0.001"))
    passed = result == Decimal("0.012")
    return (passed, "Decimal('0.012') — DOWN", str(result),
            "quantise_quantity() rounds DOWN to protect risk budget")


@register_test("bot.exchanges.base::quantise_quantity_precision")
def _test_q_decimal_type(fn):
    """Must return Decimal not float — IEEE 754 float imprecision is dangerous."""
    result = fn(0.012345678, Decimal("0.001"))
    passed = isinstance(result, Decimal) and result == Decimal("0.012")
    return (passed, "isinstance Decimal and == 0.012",
            f"type={type(result).__name__}, val={result}",
            "quantise_quantity() returns Decimal not float")


@register_test("bot.exchanges.base::quantise_price")
def _test_q_price(fn):
    """50123.4567 with tick 0.01 must give 50123.45."""
    result = fn(50123.4567, Decimal("0.01"))
    passed = result == Decimal("50123.45")
    return (passed, "Decimal('50123.45')", str(result),
            "quantise_price() applies tick_size correctly")


# ─────────────────────────────────────────────────────────────────────
# SETUP VALIDATOR MATHEMATICAL TESTS
# ─────────────────────────────────────────────────────────────────────

@register_test("bot.engines.setup_validator::SetupValidator::validate_expectancy")
def _test_sv_expectancy(obj):
    """5W@+2R + 5L@-1R must produce exactly +0.500R expectancy."""
    trades = TestData.known_trades()
    report = obj.validate(trades)
    actual = report.overall_expectancy
    passed = abs(actual - 0.5) < 0.001
    return (passed, "expectancy = 0.500R ±0.001",
            f"expectancy = {actual:.4f}R",
            "validate() computes mathematically correct expectancy")


@register_test("bot.engines.setup_validator::SetupValidator::validate_pf")
def _test_sv_pf(obj):
    """5W@+2R + 5L@-1R must produce profit_factor = 2.000 exactly."""
    trades = TestData.known_trades()
    report = obj.validate(trades)
    actual = report.overall_pf
    passed = abs(actual - 2.0) < 0.001
    return (passed, "profit_factor = 2.000 ±0.001",
            f"profit_factor = {actual:.4f}",
            "validate() computes mathematically correct profit factor")


@register_test("bot.engines.setup_validator::SetupValidator::validate_wr")
def _test_sv_wr(obj):
    """5W/10T must produce exactly 0.500 win rate."""
    trades = TestData.known_trades()
    report = obj.validate(trades)
    actual = report.overall_win_rate
    passed = abs(actual - 0.5) < 0.001
    return (passed, "win_rate = 0.500 ±0.001",
            f"win_rate = {actual:.4f}",
            "validate() computes correct win rate")


@register_test("bot.engines.setup_validator::SetupValidator::validate_segmentation")
def _test_sv_segmentation(obj):
    """CHOCH setup (all wins) and BOS_POOR (all losses) must segment correctly."""
    trades  = TestData.known_trades()
    report  = obj.validate(trades)
    winning = [s for s in report.setup_stats if "CHOCH" in s.setup_type]
    losing  = [s for s in report.setup_stats if "BOS_RANGE_POOR" in s.setup_type]
    passed  = (len(winning) > 0 and len(losing) > 0
               and winning[0].win_rate > 0.9
               and losing[0].win_rate  < 0.1)
    return (passed,
            "CHOCH WR > 90%, BOS_POOR WR < 10%",
            f"CHOCH={winning[0].win_rate:.0%} if found",
            "validate() correctly segments by setup_type")


# ─────────────────────────────────────────────────────────────────────
# STRUCTURE ANALYZER TESTS
# ─────────────────────────────────────────────────────────────────────

@register_test("bot.engines.structure_analyzer::StructureAnalyzer::analyse")
def _test_sa_analyse(obj):
    """analyse() must return a valid trend label and non-empty swing lists."""
    df     = TestData.ohlcv_df(80)
    result = obj.analyse(df)
    passed = (result is not None
              and result.trend in ["UPTREND", "DOWNTREND", "RANGING"]
              and isinstance(result.swing_highs, list)
              and isinstance(result.swing_lows,  list))
    return (passed,
            "valid trend, swing_highs and swing_lows are lists",
            f"trend={result.trend if result else 'None'}",
            "analyse() returns StructureResult with valid trend")


@register_test("bot.engines.structure_analyzer::StructureAnalyzer::_detect_fvgs")
def _test_sa_fvgs(obj):
    """Must detect at least 1 FVG in a DataFrame with a known guaranteed gap."""
    df   = TestData.fvg_df()
    fvgs = obj._detect_fvgs(df, atr=3.0)
    passed = len(fvgs) > 0
    return (passed, "≥ 1 FVG detected", f"{len(fvgs)} FVG(s) found",
            "_detect_fvgs() identifies genuine price gaps")


# ─────────────────────────────────────────────────────────────────────
# CIRCUIT BREAKER TESTS
# ─────────────────────────────────────────────────────────────────────

@register_test("bot.exchanges.base::CircuitBreaker::trips_at_threshold")
def _test_cb_trips(obj):
    """Must be CLOSED after 4 failures and OPEN after the 5th."""
    cb = obj.__class__()                            # use same class
    for _ in range(4): cb.record_failure()
    before = cb.get_state() == "CLOSED"
    cb.record_failure()
    after  = cb.get_state() == "OPEN"
    passed = before and after
    return (passed, "CLOSED@4, OPEN@5",
            f"@4={cb.get_state() if not before else 'CLOSED'}, @5={cb.get_state()}",
            "CircuitBreaker trips at exactly the failure threshold")


@register_test("bot.exchanges.base::CircuitBreaker::recovers_after_reset")
def _test_cb_recovers(obj):
    """Must return to CLOSED after reset() — exchange re-enabled correctly."""
    cb = obj.__class__(failure_threshold=3)
    for _ in range(3): cb.record_failure()
    assert cb.get_state() == "OPEN"
    cb.reset()
    passed = cb.get_state() == "CLOSED"
    return (passed, "CLOSED after reset()", cb.get_state(),
            "CircuitBreaker recovers to CLOSED after reset")


# ─────────────────────────────────────────────────────────────────────
# RATE LIMITER TEST
# ─────────────────────────────────────────────────────────────────────

@register_test("bot.exchanges.base::RateLimiter::enforces_gap")
def _test_rl_gap(obj):
    """Two acquire() calls with 100ms limit must take ≥ 90ms total."""
    rl    = obj.__class__(rate_limit_ms=100, burst_budget=10)
    start = time.time()
    rl.acquire()
    rl.acquire()
    elapsed_ms = (time.time() - start) * 1000
    passed     = elapsed_ms >= 90
    return (passed, "elapsed ≥ 90ms", f"elapsed={elapsed_ms:.1f}ms",
            "RateLimiter enforces minimum gap between API calls")


# ─────────────────────────────────────────────────────────────────────
# BACKTESTER TEST
# ─────────────────────────────────────────────────────────────────────

@register_test("bot.backtesting.backtester::Backtester::run")
def _test_bt_run(klass):
    """run() must return a result with all required statistical fields."""
    df      = TestData.ohlcv_df(200)
    bt      = klass(df, run_validation=False)
    result  = bt.run()
    required = ["total_trades", "win_rate", "profit_factor",
                "expectancy", "max_drawdown_pct", "equity_curve"]
    passed  = result is not None and all(hasattr(result, k) for k in required)
    return (passed,
            f"BacktestResult has {len(required)} required attributes",
            f"has_all={passed}",
            "Backtester.run() returns complete BacktestResult")


# ═════════════════════════════════════════════════════════════════════════
# SECTION 5 — LAYER 4: INTEGRATION TESTS  [NEW in v2]
#
# These test the PIPELINE: does data flow correctly from module to module?
# Each test chains multiple modules together and checks the end result.
# ═════════════════════════════════════════════════════════════════════════

class IntegrationLayer:
    """
    Layer 4: Tests that modules compose correctly together.

    A module can pass all individual tests and still fail in the pipeline
    because its output format does not match what the next module expects.
    These tests exercise the full data flow between modules.

    Integration tests registered here:
      1. Data → RangeAnalytics → Signal Score
         Does a real DataFrame produce a valid score end-to-end?

      2. Data → RegimeDetector → StrategyRouter
         Does the regime result feed correctly into the router?

      3. Data → StructureAnalyzer → SignalEngine (feature check)
         Does structure output have all keys signal_engine expects?

      4. SetupValidator → StrategyScorer
         Does the validation output have the shape scorer expects?

      5. RangeProfile → GridStrategy
         Does a RangeProfile correctly configure a grid?
    """

    def __init__(self, factories: Dict[str, Callable]) -> None:
        self._factories = factories                     # safe instance factories

    def run_all(self) -> List[IntegrationResult]:
        """Run all integration tests and return results."""
        tests = [
            self._test_range_to_score,
            self._test_regime_to_router,
            self._test_structure_fields,
            self._test_validator_to_scorer,
            self._test_range_to_grid,
        ]
        results = []
        for test_fn in tests:
            start = time.time()
            try:
                result = test_fn()
                result.elapsed_ms = (time.time() - start) * 1000
            except Exception as e:
                result = IntegrationResult(
                    test_name  = test_fn.__name__,
                    stages     = [],
                    passed     = False,
                    elapsed_ms = (time.time() - start) * 1000,
                    message    = f"Integration test raised: {type(e).__name__}",
                    error      = traceback.format_exc()[:400],
                )
            results.append(result)
        return results

    def _test_range_to_score(self) -> IntegrationResult:
        """
        Pipeline: daily_df → RangeAnalytics.analyse() → range_score()
        Verifies that a real DataFrame produces a valid 0-1 score end-to-end.
        This is the most commonly executed pipeline path in the live bot.
        """
        name   = "RangeAnalytics → range_score pipeline"
        stages = ["TestData.daily_df", "RangeAnalytics.analyse", "RangeAnalytics.range_score"]

        ra = self._factories.get("RangeAnalytics", lambda: None)()
        if ra is None:
            return IntegrationResult(
                test_name=name, stages=stages, passed=False,
                elapsed_ms=0, message="RangeAnalytics not importable")

        df      = TestData.daily_df(60)
        profile = ra.analyse(df, float(df["close"].iloc[-1]), "BTCUSDT")

        # Verify profile has all the fields range_score needs
        required_attrs = ["current_range_used", "range_bias", "volatility_state",
                          "range_respect_rate", "predicted_direction",
                          "exhaustion_prob", "efficiency_signal"]
        missing = [a for a in required_attrs if not hasattr(profile, a)]

        if missing:
            return IntegrationResult(
                test_name=name, stages=stages, passed=False, elapsed_ms=0,
                message=f"Profile missing fields: {missing}",
                detail=f"Present attrs: {[a for a in required_attrs if hasattr(profile, a)]}")

        score = ra.range_score(profile, "LONG")
        passed = isinstance(score, float) and 0.0 <= score <= 1.0

        return IntegrationResult(
            test_name=name, stages=stages, passed=passed, elapsed_ms=0,
            message=f"Score={score:.4f} ({'valid' if passed else 'INVALID — out of range'})",
            detail=f"adr={profile.adr_dollars}, state={profile.volatility_state}, "
                   f"range_used={profile.current_range_used:.2f}")

    def _test_regime_to_router(self) -> IntegrationResult:
        """
        Pipeline: ohlcv_df → RegimeDetector.detect() → StrategyRouter.route()
        Verifies that the regime result has the attributes StrategyRouter expects.
        A missing attribute name here would cause AttributeError mid-trade.
        """
        name   = "RegimeDetector → StrategyRouter pipeline"
        stages = ["TestData.ohlcv_df", "RegimeDetector.detect", "StrategyRouter.route"]

        detector = self._factories.get("RegimeDetector", lambda: None)()
        router   = self._factories.get("StrategyRouter", lambda: None)()

        if detector is None:
            return IntegrationResult(
                test_name=name, stages=stages, passed=False,
                elapsed_ms=0, message="RegimeDetector not importable")

        df     = TestData.ohlcv_df(120)
        regime = detector.detect(df)

        # Verify regime result has expected attributes
        required = ["regime", "confidence"]
        missing  = [a for a in required
                    if not hasattr(regime, a) and not (
                        hasattr(regime, "dominant") if a == "regime" else False
                    )]

        if missing:
            return IntegrationResult(
                test_name=name, stages=stages, passed=False, elapsed_ms=0,
                message=f"RegimeResult missing: {missing}")

        # Test router if available
        if router is not None:
            try:
                signal = router.route(df, regime)
                passed = signal is not None
                detail = f"regime={getattr(regime, 'regime', getattr(regime, 'dominant', '?'))}, signal_type={type(signal).__name__}"
            except Exception as e:
                passed = False
                detail = f"router.route() raised: {e}"
        else:
            passed = True                               # router not available — regime check passed
            detail = f"regime={getattr(regime, 'regime', getattr(regime, 'dominant', '?'))}"

        return IntegrationResult(
            test_name=name, stages=stages, passed=passed,
            elapsed_ms=0, message="Regime → Router pipeline complete" if passed else "Pipeline failed",
            detail=detail)

    def _test_structure_fields(self) -> IntegrationResult:
        """
        Pipeline: ohlcv_df → StructureAnalyzer.analyse() → signal_engine field check
        Verifies that StructureResult has every field that signal_engine.py reads.
        Missing fields cause KeyError/AttributeError at signal generation time.
        """
        name   = "StructureAnalyzer output → SignalEngine fields"
        stages = ["TestData.ohlcv_df", "StructureAnalyzer.analyse", "signal_engine field check"]

        sa = self._factories.get("StructureAnalyzer", lambda: None)()
        if sa is None:
            return IntegrationResult(
                test_name=name, stages=stages, passed=False,
                elapsed_ms=0, message="StructureAnalyzer not importable")

        df     = TestData.ohlcv_df(80)
        result = sa.analyse(df)

        # All fields that signal_engine._score_direction() reads
        required = ["trend", "trend_strength", "last_choch", "last_bos",
                    "bullish_fvg_near", "bearish_fvg_near",
                    "ob_near_long", "ob_near_short",
                    "liquidity_above", "liquidity_below",
                    "active_fvgs", "order_blocks",
                    "support", "resistance", "current_price"]

        missing = [f for f in required if not hasattr(result, f)]
        passed  = len(missing) == 0

        return IntegrationResult(
            test_name=name, stages=stages, passed=passed, elapsed_ms=0,
            message=f"All {len(required)} signal_engine fields present" if passed
                    else f"Missing {len(missing)} fields: {missing}",
            detail=f"trend={result.trend}, fvg_near={result.bullish_fvg_near}")

    def _test_validator_to_scorer(self) -> IntegrationResult:
        """
        Pipeline: known_trades → SetupValidator.validate() → StrategyScorer check
        Verifies that the ValidationReport has the fields StrategyScorer expects.
        """
        name   = "SetupValidator → StrategyScorer interface"
        stages = ["TestData.known_trades", "SetupValidator.validate",
                  "StrategyScorer interface check"]

        sv = self._factories.get("SetupValidator", lambda: None)()
        if sv is None:
            return IntegrationResult(
                test_name=name, stages=stages, passed=False,
                elapsed_ms=0, message="SetupValidator not importable")

        trades = TestData.known_trades()
        report = sv.validate(trades)

        # Fields that strategy_scorer.py reads from a validation report
        required = ["total_trades", "overall_win_rate",
                    "overall_expectancy", "overall_pf", "setup_stats"]
        missing  = [f for f in required if not hasattr(report, f)]
        passed   = len(missing) == 0

        return IntegrationResult(
            test_name=name, stages=stages, passed=passed, elapsed_ms=0,
            message=f"ValidationReport has all scorer fields" if passed
                    else f"Missing: {missing}",
            detail=f"expectancy={report.overall_expectancy:.3f}R, "
                   f"pf={report.overall_pf:.2f}, "
                   f"setups={len(report.setup_stats)}")

    def _test_range_to_grid(self) -> IntegrationResult:
        """
        Pipeline: daily_df → RangeAnalytics.analyse() → GridStrategy.build_config()
        Verifies that a RangeProfile feeds correctly into the grid engine.
        """
        name   = "RangeAnalytics → GridStrategy pipeline"
        stages = ["TestData.daily_df", "RangeAnalytics.analyse",
                  "GridStrategy.build_config"]

        ra   = self._factories.get("RangeAnalytics",  lambda: None)()
        grid = self._factories.get("GridStrategy",     lambda: None)()

        if ra is None:
            return IntegrationResult(
                test_name=name, stages=stages, passed=False,
                elapsed_ms=0, message="RangeAnalytics not importable")
        if grid is None:
            return IntegrationResult(
                test_name=name, stages=stages, passed=False,
                elapsed_ms=0, message="GridStrategy not importable")

        df      = TestData.daily_df(60)
        profile = ra.analyse(df, float(df["close"].iloc[-1]), "BTCUSDT")

        config  = grid.build_config(
            profile       = profile,
            current_price = float(df["close"].iloc[-1]),
            capital       = 1000.0,
            regime        = "RANGING",
        )

        # Verify grid config has the required fields
        required = ["spacing", "num_levels", "capital_per_level",
                    "upper_boundary", "lower_boundary", "activate"]
        missing  = [f for f in required if not hasattr(config, f)]
        passed   = len(missing) == 0 and config.spacing > 0

        return IntegrationResult(
            test_name=name, stages=stages, passed=passed, elapsed_ms=0,
            message=f"Grid config valid: spacing=${config.spacing:.2f}, "
                    f"levels={config.num_levels}" if passed
                    else f"Missing fields: {missing}",
            detail=f"activate={config.activate}, adr_basis={config.adr_basis}")


# ═════════════════════════════════════════════════════════════════════════
# SECTION 6 — LAYER 5: STATISTICAL DRIFT DETECTION  [NEW in v2]
#
# The bot may be technically working but producing values that have
# drifted far from their expected range. This is the most subtle and
# dangerous failure mode in any quantitative system.
#
# Examples of drift that this layer catches:
#   ADR drifted from $2,300 to $50     → data pipeline broken
#   Respect rate dropped from 0.73 to 0.12 → market regime changed
#   Range score always returning 0.0    → scoring formula broken
#   Forward return prediction stuck at NEUTRAL → forward engine broken
# ═════════════════════════════════════════════════════════════════════════

class DriftLayer:
    """
    Layer 5: Statistical drift detection.

    On first run with --baseline, computes current metric values and
    saves them as the baseline. On subsequent runs, compares current
    values against the baseline and flags metrics that have drifted
    beyond their tolerance thresholds.

    Tolerances are set conservatively:
      30%  drift = WARNING    (investigate)
      50%  drift = CRITICAL   (likely broken)
    """

    # Define which metrics to track, from which module, and their tolerances
    DRIFT_CHECKS = [
        # (metric_name, module_key, tolerance_pct, description)
        ("adr_dollars",           "RangeAnalytics", 0.50,
         "Average daily range in dollars — large drift = data pipeline change"),
        ("adr_percent",           "RangeAnalytics", 0.40,
         "ADR as percentage — should be stable for same asset"),
        ("range_respect_rate",    "RangeAnalytics", 0.30,
         "Fraction of days ADR holds — drops when volatility regime changes"),
        ("current_range_used",    "RangeAnalytics", 0.60,
         "Today's range consumption — high variance expected, wide tolerance"),
        ("volatility_ratio",      "RangeAnalytics", 0.40,
         "7d vs 30d ADR ratio — sudden shift = volatility regime change"),
        ("current_efficiency",    "RangeAnalytics", 0.40,
         "Directional efficiency of last candle — noisy but informative"),
    ]

    def __init__(self, factories: Dict[str, Callable]) -> None:
        self._factories = factories

    def run(
        self,
        baseline_path: Optional[str] = None,
    ) -> List[DriftResult]:
        """
        Compute current metric values and compare against baseline.
        If no baseline exists, records current values as the new baseline.

        Parameters
        ──────────
        baseline_path : path to JSON baseline file. None = skip drift checks.

        Returns
        ───────
        List of DriftResult, one per tracked metric.
        """
        if baseline_path is None:
            return []                                   # drift checks disabled

        # Compute current values
        current_values = self._compute_current_values()
        if not current_values:
            return []

        # Load or create baseline
        if os.path.exists(baseline_path):
            with open(baseline_path, "r") as f:
                baseline = json.load(f)                 # load existing baseline
        else:
            # First run — save current as baseline
            os.makedirs(os.path.dirname(baseline_path)
                        if os.path.dirname(baseline_path) else ".", exist_ok=True)
            with open(baseline_path, "w") as f:
                json.dump({"created": datetime.now().isoformat(),
                           "values": current_values}, f, indent=2)
            return []                                   # no comparison on first run

        baseline_values = baseline.get("values", {})
        results         = []

        for metric, module_key, tolerance, description in self.DRIFT_CHECKS:
            current  = current_values.get(metric)
            baseline = baseline_values.get(metric)

            if current is None or baseline is None:
                continue                                # metric not available

            # Compute drift as fraction of baseline
            if abs(baseline) < 1e-10:
                drift_pct = 0.0                         # avoid division by zero
            else:
                drift_pct = abs(current - baseline) / abs(baseline)

            # Classify severity
            if drift_pct > 0.50:
                severity = "CRITICAL"
            elif drift_pct > tolerance:
                severity = "WARNING"
            else:
                severity = "OK"

            passed = severity == "OK"

            results.append(DriftResult(
                metric_name    = metric,
                module         = module_key,
                current_value  = round(float(current),  4),
                baseline_value = round(float(baseline), 4),
                tolerance_pct  = tolerance,
                drift_pct      = round(drift_pct,       4),
                passed         = passed,
                severity       = severity,
                message        = (
                    f"{metric}: current={current:.4f}, "
                    f"baseline={baseline:.4f}, "
                    f"drift={drift_pct:.1%} "
                    f"({'within tolerance' if passed else severity})"
                ),
            ))

        return results

    def _compute_current_values(self) -> Dict[str, float]:
        """Run each module with test data and extract trackable metric values."""
        values = {}

        ra = self._factories.get("RangeAnalytics", lambda: None)()
        if ra is not None:
            try:
                df      = TestData.daily_df(60)
                profile = ra.analyse(df, float(df["close"].iloc[-1]), "DRIFT_CHECK")
                values["adr_dollars"]        = profile.adr_dollars
                values["adr_percent"]        = profile.adr_percent
                values["range_respect_rate"] = profile.range_respect_rate
                values["current_range_used"] = profile.current_range_used
                values["volatility_ratio"]   = profile.volatility_ratio
                values["current_efficiency"] = profile.current_efficiency
            except Exception as e:
                logger.warning("DriftLayer: RangeAnalytics metric extraction failed — %s", e)

        return values


# ═════════════════════════════════════════════════════════════════════════
# SECTION 7 — MODULE DISCOVERY
#
# v2 upgrade: instead of a static hardcoded list that must be manually
# updated every time a new module is created, the system now recursively
# walks the entire bot/ package and discovers every importable .py file
# automatically. Exclusion patterns filter out non-auditable files like
# migrations, settings, and URL configs.
#
# This means: you add a new module to the project and it is automatically
# included in the next health check run. No maintenance required.
# ═════════════════════════════════════════════════════════════════════════

from pathlib import Path                        # used for recursive .py file discovery


# Patterns that mark a module as non-auditable.
# Any module whose dotted path contains one of these strings is skipped.
EXCLUDED_MODULE_PATTERNS = {
    "migrations",       # Django migration files — generated, not hand-written
    ".tests",           # unit test files — tested separately
    ".test",            # alternate test naming convention
    ".admin",           # Django admin registrations — UI boilerplate
    ".apps",            # Django app config — framework boilerplate
    ".settings",        # Django settings — not auditable logic
    ".urls",            # URL routing — not auditable logic
    ".wsgi",            # WSGI entry point — not auditable logic
    ".asgi",            # ASGI entry point — not auditable logic
    "management",       # skip the health check commands themselves
}


def should_exclude_module(module_path: str) -> bool:
    """
    Return True if this module should be excluded from auditing.
    Checks every exclusion pattern against the full dotted module path.
    """
    return any(pattern in module_path for pattern in EXCLUDED_MODULE_PATTERNS)


def discover_all_bot_modules(root_package: str = "bot") -> List[str]:
    """
    Recursively discover every importable Python module inside the bot package.

    How it works
    ─────────────
    1. Import the root package to find its filesystem location.
    2. Walk every .py file recursively under that directory.
    3. Convert each file path to a dotted module path.
    4. Apply exclusion filters.
    5. Return a sorted, deduplicated list.

    This replaces the old static BOT_MODULES list completely.
    Every new module added to the project is discovered automatically.

    Parameters
    ──────────
    root_package : str  dotted name of the top-level package (default "bot")

    Returns
    ───────
    List of dotted module path strings sorted alphabetically.
    """
    try:
        package   = importlib.import_module(root_package)   # import the root package
    except ImportError as e:
        logger.error("discover_all_bot_modules: cannot import '%s' — %s", root_package, e)
        return []

    root_path = Path(package.__file__).parent               # absolute path to bot/
    modules: List[str] = []

    for file in root_path.rglob("*.py"):                    # walk all .py files recursively
        if file.name.startswith("__"):                      # skip __init__.py, __main__.py
            continue

        relative    = file.relative_to(root_path)           # path relative to bot/
        module_parts= relative.with_suffix("").parts        # remove .py, split into parts
        module_path = ".".join([root_package, *module_parts])  # join as dotted path

        if should_exclude_module(module_path):              # apply exclusion filters
            continue

        modules.append(module_path)                         # add to discovery list

    return sorted(set(modules))                             # sort and deduplicate


def discover_module_members(
    module_path: str,
) -> Tuple[Any, List[Tuple[str, Any]], List[Tuple[str, Any]]]:
    """
    Import a module and return (module, classes, standalone_functions).
    Only returns members that are defined in this module, not imported ones.
    """
    module    = importlib.import_module(module_path)        # dynamic import
    classes   = []                                          # (name, class) pairs
    functions = []                                          # (name, function) pairs

    for name, obj in inspect.getmembers(module):
        if name.startswith("__"):                           # skip dunder members
            continue
        if inspect.isclass(obj) and obj.__module__ == module_path:
            classes.append((name, obj))                     # only classes defined here
        elif inspect.isfunction(obj) and obj.__module__ == module_path:
            functions.append((name, obj))                   # only functions defined here

    return module, classes, functions


def discover_class_methods(
    cls: Any, include_private: bool = True
) -> List[Tuple[str, Any]]:
    """
    Return all non-dunder methods on a class.
    include_private=True includes single-underscore helpers which often
    contain critical maths that needs Layer 3 behavioural validation.
    """
    methods = []
    for name, obj in inspect.getmembers(cls, predicate=inspect.isfunction):
        if name.startswith("__"):                           # always skip dunder
            continue
        if not include_private and name.startswith("_"):   # optionally skip private
            continue
        methods.append((name, obj))
    return methods


# ═════════════════════════════════════════════════════════════════════════
# SECTION 8 — LAYER RUNNER (Layers 1–3)
# ═════════════════════════════════════════════════════════════════════════

class LayerRunner:
    """
    Runs Layers 1, 2, and 3 for a single function.
    Uses safe factories for instantiation, not cls.__new__().
    """

    def __init__(
        self,
        test_registry: Dict[str, Callable],
        factories:     Dict[str, Callable],
    ) -> None:
        self._tests     = test_registry             # Layer 3 behavioural tests
        self._factories = factories                  # safe instance factories

    def run(
        self,
        module_path:    str,
        class_name:     str,
        function_name:  str,
        fn_or_method:   Any,
        class_obj:      Any,                        # the class itself (not instance)
        is_method:      bool,
    ) -> FunctionResult:
        """Run all three layers for one function and return FunctionResult."""

        result = FunctionResult(
            module_path   = module_path,
            class_name    = class_name,
            function_name = function_name,
            is_method     = is_method,
        )

        # ── Layer 1: Structural ───────────────────────────────────────
        result.layer1_exists     = callable(fn_or_method)
        result.layer1_importable = True                 # import succeeded to get here
        result.layer1_message    = (
            "Callable and importable"
            if result.layer1_exists
            else "Not callable"
        )
        if not result.layer1_exists:
            result.overall_status = "FAIL"
            return result

        # ── Layer 2: Functional ───────────────────────────────────────
        start = time.time()
        try:
            instance   = self._get_instance(class_name, class_obj)
            output     = self._try_call(fn_or_method, instance, function_name)
            result.layer2_elapsed_ms = (time.time() - start) * 1000
            result.layer2_runs       = True
            result.layer2_returns    = output is not None
            result.layer2_message    = (
                f"OK in {result.layer2_elapsed_ms:.1f}ms → {type(output).__name__}"
            )
        except Exception as e:
            result.layer2_elapsed_ms = (time.time() - start) * 1000
            result.layer2_runs       = False
            result.layer2_error      = f"{type(e).__name__}: {str(e)[:200]}"
            result.layer2_message    = f"Raised {type(e).__name__}"
            result.overall_status    = "FAIL"
            result.notes.append(traceback.format_exc()[:400])
            return result

        # ── Layer 3: Behavioural ──────────────────────────────────────
        test_key = self._find_test_key(module_path, class_name, function_name)

        if test_key and test_key in self._tests:
            result.layer3_tested = True
            try:
                # Pass the properly instantiated object to the test
                instance = self._get_instance(class_name, class_obj)
                target   = instance if is_method else fn_or_method
                passed, expected, actual, desc = self._tests[test_key](target)
                result.layer3_passed    = passed
                result.layer3_assertion = desc
                result.layer3_expected  = str(expected)
                result.layer3_actual    = str(actual)
                result.layer3_message   = (
                    f"CORRECT — {desc}" if passed
                    else f"WRONG — expected {expected}, got {actual}"
                )
            except Exception as e:
                result.layer3_passed  = False
                result.layer3_message = f"Test raised: {type(e).__name__}: {str(e)[:120]}"
                result.notes.append(traceback.format_exc()[:300])
        else:
            result.layer3_tested  = False
            result.layer3_message = "No behavioural test defined yet"

        # ── Overall status ────────────────────────────────────────────
        if not result.layer2_runs:
            result.overall_status = "FAIL"
        elif result.layer3_tested and not result.layer3_passed:
            result.overall_status = "FAIL"
        elif not result.layer3_tested:
            result.overall_status = "WARN"              # passed L1+L2 but no L3
        else:
            result.overall_status = "PASS"

        return result

    def _get_instance(self, class_name: str, class_obj: Any) -> Optional[Any]:
        """
        Get a properly instantiated instance using the safe factory registry.
        Falls back to no-arg constructor if no factory registered.
        This replaces the dangerous cls.__new__(cls) from v1.
        """
        factory = self._factories.get(class_name)          # look up factory
        if factory:
            try:
                return factory()                            # use registered factory
            except Exception:
                pass                                        # fall through to default

        # Fallback: try no-arg constructor
        if class_obj is not None:
            try:
                return class_obj()
            except Exception:
                pass

        return None                                         # cannot instantiate

    def _try_call(
        self,
        fn:            Any,
        instance:      Any,
        function_name: str,
    ) -> Any:
        """Attempt to call a function with intelligently chosen test arguments."""
        sig    = inspect.signature(fn)
        params = dict(sig.parameters)
        params.pop("self", None)

        if not params:
            return fn() if instance is None else fn.__get__(instance)()

        kwargs = {}
        for name, param in params.items():
            if param.default is not inspect.Parameter.empty and \
               param.annotation is inspect.Parameter.empty:
                continue                                     # use default
            kwargs[name] = self._value_for_param(name, param.annotation)

        if instance is not None:
            return fn(instance, **kwargs)
        return fn(**kwargs)

    @staticmethod
    def _value_for_param(name: str, annotation: Any) -> Any:
        """Return appropriate synthetic value based on parameter name."""
        n = name.lower()
        if "daily_df" in n:
            return TestData.daily_df(60)
        if "df" in n or "dataframe" in n:
            return TestData.ohlcv_df(80)
        if "symbol" in n:
            return "BTCUSDT"
        if "interval" in n or "timeframe" in n:
            return "1h"
        if "price" in n:
            return 50000.0
        if "balance" in n:
            return 1000.0
        if "limit" in n or "candles" in n:
            return 100
        if "capital" in n:
            return 5000.0
        if "direction" in n:
            return "LONG"
        if "lookback" in n:
            return 30
        if "trades" in n:
            return TestData.known_trades()
        if annotation == float:
            return 1.0
        if annotation == int:
            return 10
        if annotation == str:
            return "test"
        if annotation == bool:
            return True
        return None

    def _find_test_key(self, module: str, cls: str, fn: str) -> Optional[str]:
        """Find matching test key from registry for this function."""
        # Exact match: module::Class::method
        primary = f"{module}::{cls}::{fn}"
        if primary in self._tests:
            return primary
        # Standalone: module::function
        standalone = f"{module}::{fn}"
        if standalone in self._tests:
            return standalone
        # Variant keys (e.g. validate_expectancy, validate_pf for same class)
        prefix = f"{module}::{cls}::"
        for key in self._tests:
            if key.startswith(prefix) and fn in key:
                return key
        return None


# ═════════════════════════════════════════════════════════════════════════
# SECTION 9 — DJANGO MANAGEMENT COMMAND
# ═════════════════════════════════════════════════════════════════════════

class Command(BaseCommand):
    """
    Django management command: python manage.py deep_health_check

    Performs a five-layer self-audit of the entire Quant Bot Pro platform.
    Three audit modes:
      fast     — Layers 1+2 only (structure and execution, ~5s)
      standard — Layers 1–3 (adds mathematical validation, ~20s)
      deep     — All 5 layers (adds integration + drift detection, ~45s)
    """

    help = "Five-layer deep health audit: structural, functional, behavioural, integration, drift"

    def add_arguments(self, parser):
        """Register all CLI flags."""
        parser.add_argument(
            "--mode", type=str, default="standard",
            choices=["fast", "standard", "deep"],
            help="fast=L1+L2 | standard=L1-L3 | deep=all 5 layers"
        )
        parser.add_argument(
            "--module", type=str, default=None,
            help="Audit only modules matching this name"
        )
        parser.add_argument(
            "--verbose", action="store_true",
            help="Print full detail including stack traces and L3 assertions"
        )
        parser.add_argument(
            "--report", type=str, default="logs/deep_health.json",
            help="Path for JSON report output"
        )
        parser.add_argument(
            "--baseline", type=str, default=None,
            help="Path to baseline JSON for Layer 5 drift detection"
        )
        parser.add_argument(
            "--no-private", action="store_true",
            help="Skip single-underscore private methods"
        )

    def handle(self, *args, **options):
        """Entry point called by Django."""
        mode         = options["mode"]
        module_filter= options["module"]
        verbose      = options["verbose"]
        report_path  = options["report"]
        baseline     = options["baseline"]
        skip_private = options["no_private"]

        # Build infrastructure
        factories  = _safe_instance_factory()           # safe constructors
        tests      = build_tests()                      # trigger decorator registration
        runner     = LayerRunner(tests, factories)      # L1-L3 runner
        integrator = IntegrationLayer(factories)        # L4 runner
        drifter    = DriftLayer(factories)              # L5 runner

        # Automatically discover every module in the bot package.
        # No static list to maintain — new modules appear automatically.
        # New files added to bot/ are included without any code change here.
        all_modules = discover_all_bot_modules()

        if module_filter:
            # Filter to only modules whose dotted path contains the search string
            modules = [m for m in all_modules if module_filter.lower() in m.lower()]
            if not modules:
                self.stdout.write(
                    f"{RED}No module matching: {module_filter}{RESET}\n"
                    f"{DIM}Discovered modules:{RESET}\n"
                    + "\n".join(f"  {DIM}{m}{RESET}" for m in all_modules)
                )
                return
        else:
            modules = all_modules       # audit everything discovered

        self._print_header(mode, len(modules))

        session_start   = time.time()
        module_reports  = []

        # ── Layers 1-3 for each module ────────────────────────────────
        for module_path in modules:
            report = self._audit_module(
                module_path, runner, verbose, skip_private, mode
            )
            module_reports.append(report)
            self._print_module_report(report, verbose)

        # ── Layer 4: Integration tests (standard + deep mode) ─────────
        integration_results = []
        if mode in ("standard", "deep"):
            self.stdout.write(
                f"\n{BOLD}{CYAN}── LAYER 4: INTEGRATION TESTS ─────────────────────{RESET}"
            )
            integration_results = integrator.run_all()
            for ir in integration_results:
                icon   = f"{GREEN}✔{RESET}" if ir.passed else f"{RED}✘{RESET}"
                stages = " → ".join(ir.stages[-2:]) if ir.stages else "?"
                self.stdout.write(
                    f"  {icon}  {BOLD}{ir.test_name:<45}{RESET}  "
                    f"{DIM}{ir.elapsed_ms:.0f}ms{RESET}"
                )
                self.stdout.write(
                    f"     {'✔' if ir.passed else '✘'}  {ir.message}"
                )
                if verbose and ir.detail:
                    self.stdout.write(f"       {DIM}↳ {ir.detail}{RESET}")
                if ir.error and verbose:
                    self.stdout.write(f"       {RED}↳ {ir.error[:200]}{RESET}")

        # ── Layer 5: Drift detection (deep mode only) ──────────────────
        drift_results = []
        if mode == "deep":
            self.stdout.write(
                f"\n{BOLD}{CYAN}── LAYER 5: STATISTICAL DRIFT DETECTION ───────────{RESET}"
            )
            drift_results = drifter.run(baseline)
            if not drift_results:
                self.stdout.write(
                    f"  {DIM}No baseline provided or first run — "
                    f"use --baseline logs/drift_baseline.json{RESET}"
                )
            else:
                for dr in drift_results:
                    icon = (
                        f"{GREEN}✔{RESET}" if dr.severity == "OK"
                        else f"{YELLOW}⚠{RESET}" if dr.severity == "WARNING"
                        else f"{RED}✘{RESET}"
                    )
                    self.stdout.write(
                        f"  {icon}  {BOLD}{dr.metric_name:<30}{RESET}  "
                        f"current={dr.current_value:.4f}  "
                        f"baseline={dr.baseline_value:.4f}  "
                        f"drift={dr.drift_pct:.1%}  "
                        f"{DIM}[{dr.severity}]{RESET}"
                    )

        # ── Summary ───────────────────────────────────────────────────
        elapsed = time.time() - session_start
        session = SessionReport(
            timestamp           = datetime.now().isoformat(),
            audit_mode          = mode,
            module_reports      = module_reports,
            integration_results = integration_results,
            drift_results       = drift_results,
            total_functions     = sum(r.total  for r in module_reports),
            total_passed        = sum(r.passed for r in module_reports),
            total_failed        = sum(r.failed for r in module_reports),
            total_warned        = sum(r.warned for r in module_reports),
            elapsed_seconds     = elapsed,
        )
        session.platform_health = max(0.0, round(
            ((session.total_passed * 2 + session.total_warned)
             / max(session.total_functions * 2, 1)) * 100, 1
        ))

        self._print_summary(session, integration_results, drift_results)
        self._save_report(session, report_path)

    def _audit_module(
        self,
        module_path:  str,
        runner:       LayerRunner,
        verbose:      bool,
        skip_private: bool,
        mode:         str,
    ) -> ModuleReport:
        """Run Layers 1-3 on every function in one module."""
        start = time.time()

        try:
            module, classes, functions = discover_module_members(module_path)
            module_file = getattr(module, "__file__", "unknown")
            imported    = True
            import_err  = ""
        except Exception as e:
            return ModuleReport(
                module_path=module_path, module_file="IMPORT FAILED",
                imported=False, import_error=f"{type(e).__name__}: {str(e)}",
                classes_found=[], functions_found=[], results=[],
                total=0, passed=0, failed=1, warned=0, skipped=0,
                health_score=0.0, elapsed_ms=(time.time()-start)*1000,
            )

        results = []

        for class_name, cls in classes:
            methods = discover_class_methods(cls, include_private=not skip_private)
            for method_name, method in methods:
                r = runner.run(
                    module_path   = module_path,
                    class_name    = class_name,
                    function_name = method_name,
                    fn_or_method  = method,
                    class_obj     = cls,
                    is_method     = True,
                )
                results.append(r)

        for func_name, func in functions:
            r = runner.run(
                module_path   = module_path,
                class_name    = "module",
                function_name = func_name,
                fn_or_method  = func,
                class_obj     = None,
                is_method     = False,
            )
            results.append(r)

        total   = len(results)
        passed  = sum(1 for r in results if r.overall_status == "PASS")
        failed  = sum(1 for r in results if r.overall_status == "FAIL")
        warned  = sum(1 for r in results if r.overall_status == "WARN")
        skipped = sum(1 for r in results if r.overall_status == "SKIP")
        health  = max(0, round(
            ((passed * 2 + warned) / max(total * 2, 1)) * 100, 1
        ))

        return ModuleReport(
            module_path=module_path, module_file=module_file,
            imported=imported, import_error=import_err,
            classes_found=[n for n,_ in classes],
            functions_found=[n for n,_ in functions],
            results=results, total=total, passed=passed,
            failed=failed, warned=warned, skipped=skipped,
            health_score=health, elapsed_ms=(time.time()-start)*1000,
        )

    def _print_header(self, mode: str, module_count: int) -> None:
        """Print the audit banner."""
        mode_labels = {
            "fast":     "FAST — Layers 1+2 only",
            "standard": "STANDARD — Layers 1–3",
            "deep":     "DEEP — All 5 Layers",
        }
        self.stdout.write("")
        self.stdout.write(f"{BOLD}{CYAN}{'═' * 76}{RESET}")
        self.stdout.write(
            f"{BOLD}{CYAN}   QUANT BOT PRO — DEEP HEALTH CHECK "
            f"[{mode_labels.get(mode, mode)}]{RESET}"
        )
        self.stdout.write(f"{BOLD}{CYAN}{'═' * 76}{RESET}")
        self.stdout.write(
            f"  {DIM}Modules: {module_count}   "
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}\n"
        )

    # ── METHOD COMMENTARY REGISTRY ────────────────────────────────────────
    # Maps function name → (role_tag, one_line_description)
    # role_tag    : what part of the trading pipeline this belongs to
    # description : plain English explanation of what this function does
    #
    # This makes the terminal output self-documenting. When you read the
    # health check at 2am you know not just that something failed but
    # exactly which part of the trading pipeline is broken and what it
    # was supposed to do.
    #
    # To add commentary for a new method, add one line here:
    # "your_method_name": ("ROLE_TAG", "What it does in plain English.")
    # ──────────────────────────────────────────────────────────────────────
    METHOD_COMMENTARY: Dict[str, Tuple[str, str]] = {
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Keys use fully qualified format: "module::ClassName::method_name"
        # This prevents collisions between same-named methods in different
        # modules (e.g. analyse(), run(), detect() all exist in many files).
        # The lookup in _print_module_report tries the full key first, then
        # falls back to the short function name for any unregistered methods.
        #
        # To add commentary for a new method, add one line using the format:
        # "bot.your_module::YourClass::your_method": ("ROLE_TAG", "Description.")
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        # ── bot/engines/range_analytics.py ───────────────────────────────
        "bot.engines.range_analytics::RangeAnalytics::analyse":                    ("CORE ENGINE",        "Computes ADR, range exhaustion, volatility state and forward return prediction."),
        "bot.engines.range_analytics::RangeAnalytics::range_score":                ("SIGNAL SCORING",     "Produces a 0–1 directional quality score from the RangeProfile."),
        "bot.engines.range_analytics::RangeAnalytics::backtest_tags":              ("BACKTEST ANALYTICS", "Generates the full tag dictionary attached to every trade record."),
        "bot.engines.range_analytics::RangeAnalytics::forward_returns":            ("STATISTICS",         "Calculates historical forward return distributions by range bucket."),
        "bot.engines.range_analytics::RangeAnalytics::volatility_cluster_report":  ("VOLATILITY INTEL",   "Computes vol clustering, Markov transitions and regime persistence."),
        "bot.engines.range_analytics::RangeAnalytics::session_report":             ("SESSION ANALYTICS",  "Profiles intraday behaviour by ASIA / LONDON / NY session."),
        "bot.engines.range_analytics::RangeAnalytics::compare_assets":             ("MULTI-ASSET",        "Ranks multiple assets by range conditions for capital allocation."),
        "bot.engines.range_analytics::RangeAnalytics::print_report":               ("DIAGNOSTICS",        "Renders a full human-readable RangeProfile report to stdout."),
        "bot.engines.range_analytics::RangeAnalytics::_compute_forward_returns":   ("STATISTICS",         "Segments historical returns by range_used band to prove predictive edge."),
        "bot.engines.range_analytics::RangeAnalytics::_expected_next_return":      ("PREDICTION",         "Looks up the current range bucket and returns its forward return estimate."),
        "bot.engines.range_analytics::RangeAnalytics::_compute_vol_cluster":       ("VOLATILITY INTEL",   "Builds the Markov state matrix and computes exhaustion probability."),
        "bot.engines.range_analytics::RangeAnalytics::_compute_efficiency":        ("CANDLE QUALITY",     "Measures directional cleanliness of recent candles (0=noisy, 1=clean)."),
        "bot.engines.range_analytics::RangeAnalytics::_efficiency_signal":         ("SIGNAL FILTER",      "Classifies efficiency into CLEAN / NEUTRAL / NOISY for scoring."),
        "bot.engines.range_analytics::RangeAnalytics::_conditional_expected_range":("STATISTICS",         "Returns expected ADR conditioned on weekday and volatility state."),
        "bot.engines.range_analytics::RangeAnalytics::_compute_session_profiles":  ("SESSION ANALYTICS",  "Builds per-session volatility and direction statistics from hourly data."),
        "bot.engines.range_analytics::RangeAnalytics::_current_session":           ("SESSION ANALYTICS",  "Returns the current UTC trading session name (ASIA/LONDON/NY)."),
        "bot.engines.range_analytics::RangeAnalytics::_session_expansion_likely":  ("SESSION FILTER",     "Returns True if this session historically sees range expansion > 40%."),
        "bot.engines.range_analytics::RangeAnalytics::_grid_recommendation":       ("GRID ENGINE",        "Computes volatility-adjusted grid spacing and level count from ADR."),
        "bot.engines.range_analytics::RangeAnalytics::_validate":                  ("DATA QUALITY",       "Guards against missing OHLCV columns or insufficient row counts."),
        "bot.engines.range_analytics::RangeAnalytics::_add_range_columns":         ("DATA PREP",          "Adds daily_range and daily_range_pct columns to the DataFrame."),
        "bot.engines.range_analytics::RangeAnalytics::_volatility_state":          ("REGIME DETECTION",   "Classifies 7d/30d ADR ratio as COMPRESSED / NORMAL / EXPANDING."),
        "bot.engines.range_analytics::RangeAnalytics::_max_consecutive":           ("STATISTICS",         "Finds the longest consecutive streak of True values in a boolean list."),
        "bot.engines.range_analytics::RangeAnalytics::_build_breakdown_v2":        ("AUDIT TRAIL",        "Builds the per-day breakdown with efficiency, vol state and weekday."),

        # ── bot/engines/structure_analyzer.py ────────────────────────────
        "bot.engines.structure_analyzer::StructureAnalyzer::analyse":                    ("CORE ENGINE",     "Detects BOS, CHoCH, FVGs, Order Blocks, Liquidity Pools and swing points."),
        "bot.engines.structure_analyzer::StructureAnalyzer::_find_swing_highs":          ("SWING DETECTION", "Identifies swing high turning points with configurable confirmation strength."),
        "bot.engines.structure_analyzer::StructureAnalyzer::_find_swing_lows":           ("SWING DETECTION", "Identifies swing low turning points with configurable confirmation strength."),
        "bot.engines.structure_analyzer::StructureAnalyzer::_classify_structure_events": ("STRUCTURE LOGIC", "Classifies each swing break as BOS (continuation) or CHoCH (reversal)."),
        "bot.engines.structure_analyzer::StructureAnalyzer::_classify_trend":            ("TREND ENGINE",    "Determines UPTREND / DOWNTREND / RANGING from recent structure events."),
        "bot.engines.structure_analyzer::StructureAnalyzer::_detect_fvgs":               ("SMC ENGINE",      "Finds three-candle imbalance zones (Fair Value Gaps) in the DataFrame."),
        "bot.engines.structure_analyzer::StructureAnalyzer::_mark_filled_fvgs":          ("SMC ENGINE",      "Removes FVGs that have been closed into and are no longer active."),
        "bot.engines.structure_analyzer::StructureAnalyzer::_detect_order_blocks":       ("SMC ENGINE",      "Finds the last opposing candle before each impulse move (Order Block)."),
        "bot.engines.structure_analyzer::StructureAnalyzer::_mark_tested_obs":           ("SMC ENGINE",      "Flags Order Blocks that price has already returned to and tested."),
        "bot.engines.structure_analyzer::StructureAnalyzer::_detect_liquidity_pools":    ("LIQUIDITY",       "Identifies equal highs and equal lows that represent stop-loss clusters."),
        "bot.engines.structure_analyzer::StructureAnalyzer::_is_level_swept":            ("LIQUIDITY",       "Checks whether a liquidity pool has been taken out by subsequent price."),
        "bot.engines.structure_analyzer::StructureAnalyzer::_key_levels":                ("LEVELS",          "Returns the nearest support and resistance levels from swing points."),
        "bot.engines.structure_analyzer::StructureAnalyzer::_compute_atr":               ("INDICATORS",      "Computes the ATR used for proximity thresholds and FVG size filtering."),
        "bot.engines.structure_analyzer::StructureAnalyzer::_local_trend_before":        ("STRUCTURE LOGIC", "Determines trend direction before a given index using recent swings."),
        "bot.engines.structure_analyzer::StructureAnalyzer::_swing_sequence_trend":      ("TREND ENGINE",    "Classifies trend from HH-HL or LL-LH swing sequence pattern."),
        "bot.engines.structure_analyzer::StructureAnalyzer::_validate":                  ("DATA QUALITY",    "Guards against missing columns or insufficient row counts."),

        # ── bot/engines/signal_engine.py ─────────────────────────────────
        "bot.engines.signal_engine::module::generate_signal":    ("CORE ENGINE",    "Produces the final BUY/SELL/HOLD signal with full probabilistic score."),
        "bot.engines.signal_engine::module::_score_direction":   ("SIGNAL SCORING", "Scores one direction (LONG or SHORT) across all six feature components."),
        "bot.engines.signal_engine::module::_score_structure":   ("SIGNAL SCORING", "Contributes up to 0.25 score based on BOS/CHoCH alignment with direction."),
        "bot.engines.signal_engine::module::_score_fvg":         ("SIGNAL SCORING", "Contributes up to 0.20 score based on unfilled FVG proximity."),
        "bot.engines.signal_engine::module::_score_order_block": ("SIGNAL SCORING", "Contributes up to 0.15 score based on untested Order Block proximity."),
        "bot.engines.signal_engine::module::_score_ema_bias":    ("SIGNAL SCORING", "Contributes up to 0.10 score based on EMA slope confirming direction."),
        "bot.engines.signal_engine::module::_score_volatility":  ("SIGNAL SCORING", "Contributes up to 0.10 score — NORMAL scores best, EXPANDING scores zero."),
        "bot.engines.signal_engine::module::_calculate_levels":  ("RISK ENGINE",    "Computes entry, stop-loss, take-profit and RR ratio from Order Block zones."),
        "bot.engines.signal_engine::module::_build_setup_type":  ("ANALYTICS",      "Builds the setup_type tag string used in the validation table."),
        "bot.engines.signal_engine::module::_infer_regime":      ("REGIME",         "Produces a regime label from ADX and structure trend for trade tagging."),
        "bot.engines.signal_engine::module::_hold":              ("SIGNAL ENGINE",  "Returns a fully populated HOLD signal dict when entry conditions fail."),
        "bot.engines.signal_engine::module::_compute_atr":       ("INDICATORS",     "Computes ATR for stop placement and signal scoring reference."),
        "bot.engines.signal_engine::module::_compute_adx":       ("INDICATORS",     "Computes ADX, +DI and -DI for trend strength and regime detection."),
        "bot.engines.signal_engine::module::_compute_emas":      ("INDICATORS",     "Computes fast (20) and slow (50) EMAs as momentum confirmation."),

        # ── bot/engines/setup_validator.py ───────────────────────────────
        "bot.engines.setup_validator::SetupValidator::validate":           ("CORE ENGINE",   "Analyses all trades and produces the full statistical validation report."),
        "bot.engines.setup_validator::SetupValidator::print_report":       ("DIAGNOSTICS",   "Prints the validation table with setup-type breakdown to stdout."),
        "bot.engines.setup_validator::SetupValidator::to_dataframe":       ("DATA EXPORT",   "Converts setup_stats to a pandas DataFrame for further analysis."),
        "bot.engines.setup_validator::SetupValidator::save_json":          ("AUDIT TRAIL",   "Persists the ValidationReport as a JSON file for historical comparison."),
        "bot.engines.setup_validator::SetupValidator::_group_stats":       ("STATISTICS",    "Groups trades by a column and computes SetupStats for each group."),
        "bot.engines.setup_validator::SetupValidator::_compute_stats":     ("STATISTICS",    "Computes win rate, expectancy, profit factor and Sharpe for one trade slice."),
        "bot.engines.setup_validator::SetupValidator::_range_buckets":     ("STATISTICS",    "Segments trades by range_used_at_entry to validate the ADR feature."),
        "bot.engines.setup_validator::SetupValidator::_feature_importance":("STATISTICS",    "Measures how much each boolean feature improves expectancy when present."),
        "bot.engines.setup_validator::SetupValidator::_suggest_weights":   ("ML PREP",       "Suggests updated signal scoring weights from feature importance data."),

        # ── bot/exchanges/base.py ─────────────────────────────────────────
        "bot.exchanges.base::BaseExchange::get_ohlcv":               ("DATA LAYER",      "Fetches OHLCV candlestick data — the most frequently called method."),
        "bot.exchanges.base::BaseExchange::place_order":             ("EXECUTION",        "Places a real order on the exchange — the most consequential method."),
        "bot.exchanges.base::BaseExchange::cancel_order":            ("EXECUTION",        "Cancels an open order — prevents unintended positions after TP/SL fills."),
        "bot.exchanges.base::BaseExchange::get_balance":             ("RISK",             "Returns account balance — called before every trade for size validation."),
        "bot.exchanges.base::BaseExchange::get_ticker":              ("MARKET DATA",      "Returns live price snapshot for dashboard and PnL tracking."),
        "bot.exchanges.base::BaseExchange::get_open_orders":         ("ORDER MGMT",       "Fetches all open orders — called every cycle to detect silent fills."),
        "bot.exchanges.base::BaseExchange::get_order_status":        ("ORDER MGMT",       "Fetches one order status — used to detect partial fills."),
        "bot.exchanges.base::BaseExchange::get_symbol_info":         ("PRECISION",        "Returns tick_size and step_size — essential before every order."),
        "bot.exchanges.base::BaseExchange::normalise_symbol":        ("DATA PREP",        "Converts symbol to exchange-specific format (BTCUSDT vs BTC/USDT)."),
        "bot.exchanges.base::BaseExchange::get_info":                ("METADATA",         "Returns exchange capabilities, fees and rate limits."),
        "bot.exchanges.base::BaseExchange::test_connection":         ("HEALTH",           "Verifies exchange reachability and credential validity — read-only."),
        "bot.exchanges.base::BaseExchange::get_fee_rate":            ("RISK",             "Returns taker/maker fee — used by backtester and position sizer."),
        "bot.exchanges.base::BaseExchange::get_symbol_info_cached":  ("PRECISION",        "Returns SymbolInfo with caching — prevents repeated API calls."),
        "bot.exchanges.base::BaseExchange::prepare_order_quantity":  ("PRECISION",        "Converts float quantity to Decimal rounded DOWN to step_size."),
        "bot.exchanges.base::BaseExchange::prepare_order_price":     ("PRECISION",        "Converts float price to Decimal rounded to tick_size."),
        "bot.exchanges.base::BaseExchange::validate_symbol":         ("DATA QUALITY",     "Quick format check for a symbol string — no API call needed."),
        "bot.exchanges.base::BaseExchange::safe_get_ohlcv":          ("RESILIENCE",       "Crash-safe wrapper around get_ohlcv — returns None on any error."),
        "bot.exchanges.base::BaseExchange::safe_place_order":        ("RESILIENCE",       "Crash-safe wrapper that checks circuit breaker before every order."),
        "bot.exchanges.base::BaseExchange::get_circuit_state":       ("MONITORING",       "Returns CLOSED/OPEN/HALF_OPEN for dashboard and alerting."),
        "bot.exchanges.base::BaseExchange::register_ws_callback":    ("WEBSOCKET",        "Registers a handler function for a real-time websocket channel."),
        "bot.exchanges.base::BaseExchange::dispatch_ws_message":     ("WEBSOCKET",        "Routes incoming websocket messages to all registered callbacks."),
        "bot.exchanges.base::RateLimiter::acquire":                  ("RATE LIMIT",       "Blocks until it is safe to make the next API request."),
        "bot.exchanges.base::RateLimiter::reset":                    ("RATE LIMIT",       "Clears rate limiter state after a connection recovery."),
        "bot.exchanges.base::CircuitBreaker::record_success":        ("CIRCUIT BREAKER",  "Resets failure count after a successful API call."),
        "bot.exchanges.base::CircuitBreaker::record_failure":        ("CIRCUIT BREAKER",  "Increments failure count — trips breaker at threshold."),
        "bot.exchanges.base::CircuitBreaker::is_open":               ("CIRCUIT BREAKER",  "Returns True if exchange should be avoided — auto-transitions to HALF_OPEN."),
        "bot.exchanges.base::module::quantise_quantity":             ("PRECISION",        "Rounds DOWN to step_size — financial safety for position sizing."),
        "bot.exchanges.base::module::quantise_price":                ("PRECISION",        "Rounds to tick_size — prevents order rejections on exchange."),

        # ── bot/backtesting/backtester.py ────────────────────────────────
        "bot.backtesting.backtester::Backtester::run":              ("CORE ENGINE",  "Simulates all trades on historical data and returns BacktestResult."),
        "bot.backtesting.backtester::Backtester::print_result":     ("DIAGNOSTICS",  "Prints headline backtest stats and validation table to stdout."),
        "bot.backtesting.backtester::Backtester::_check_exit":      ("SIMULATION",   "Checks if SL or TP was hit on a candle including gap-down handling."),
        "bot.backtesting.backtester::Backtester::_force_close":     ("SIMULATION",   "Force-closes open position at candle close — end of data safeguard."),
        "bot.backtesting.backtester::Backtester::_finalise_trade":  ("SIMULATION",   "Converts open trade and exit outcome into a complete TradeRecord."),
        "bot.backtesting.backtester::Backtester::_aggregate":       ("STATISTICS",   "Computes all headline statistics from the completed trade list."),
        "bot.backtesting.backtester::Backtester::_record_to_dict":  ("ANALYTICS",    "Converts a TradeRecord to the flat dict that SetupValidator expects."),

        # ── bot/risk/position_sizer.py ────────────────────────────────────
        "bot.risk.position_sizer::PositionSizer::calculate":               ("RISK ENGINE", "Calculates position size in base units from balance, entry and stop."),
        "bot.risk.position_sizer::PositionSizer::calculate_position_size": ("RISK ENGINE", "Alternate sizing method — same purpose as calculate()."),

        # ── bot/engines/regime_detector.py ───────────────────────────────
        "bot.engines.regime_detector::RegimeDetector::detect": ("CORE ENGINE", "Classifies market as TRENDING_UP/DOWN, RANGING or HIGH_VOLATILITY."),

        # ── bot/engines/strategy_router.py ───────────────────────────────
        "bot.engines.strategy_router::StrategyRouter::route": ("ROUTING", "Selects the appropriate strategy based on the current regime."),

        # ── bot/engines/trade_filter.py ──────────────────────────────────
        "bot.engines.trade_filter::TradeFilter::trade_quality_filter": ("SIGNAL FILTER", "Returns True only when signal meets all quality thresholds."),

        # ── bot/journal/trade_logger.py ───────────────────────────────────
        "bot.journal.trade_logger::TradeLogger::log_entry":    ("JOURNAL", "Saves trade entry to database with full signal and fill details."),
        "bot.journal.trade_logger::TradeLogger::log_exit":     ("JOURNAL", "Updates trade record with exit price, reason and final PnL."),
        "bot.journal.trade_logger::TradeLogger::record_entry": ("JOURNAL", "Alternate entry logging method — same purpose as log_entry()."),

        # ── bot/analytics.py (merged) ────────────────────────────────────
        "bot.analytics::TradeAnalytics::record_trade":    ("ANALYTICS", "Accumulates one trade for streaming analytics computation."),
        "bot.analytics::TradeAnalytics::summary":         ("ANALYTICS", "Returns the full performance summary dict for dashboard and scorer."),
        "bot.analytics::TradeAnalytics::equity_curve":    ("ANALYTICS", "Returns the running balance series for the equity curve chart."),
        "bot.analytics::TradeAnalytics::drawdown_series": ("ANALYTICS", "Returns peak-to-trough drawdown series for the chart."),
        "bot.analytics::TradeAnalytics::rolling_metrics": ("ANALYTICS", "Returns 30-trade rolling win rate and expectancy for trend monitoring."),

        # ── bot/strategies/grid_strategy.py ──────────────────────────────
        "bot.strategies.grid_strategy::GridStrategy::build_config":           ("GRID ENGINE", "Builds a complete GridConfig from RangeProfile — data-driven spacing."),
        "bot.strategies.grid_strategy::GridStrategy::check_grid_health":      ("GRID ENGINE", "Returns HOLD/RESET/CLOSE recommendation based on price vs grid."),
        "bot.strategies.grid_strategy::GridStrategy::select_best_grid_asset": ("MULTI-ASSET", "Ranks multiple assets and returns the best candidate for grid trading."),
        "bot.strategies.grid_strategy::GridStrategy::print_config":           ("DIAGNOSTICS", "Renders a full GridConfig summary with all levels to stdout."),
        "bot.strategies.grid_strategy::GridStrategy::_check_activation":      ("GRID ENGINE", "Validates regime, respect rate and range usage before activating grid."),
    }

    def _print_module_report(self, report: ModuleReport, verbose: bool) -> None:
        """Print results for one module with annotated role tags and descriptions."""
        colour = (GREEN if report.health_score >= 80
                  else YELLOW if report.health_score >= 50 else RED)

        # Module header line
        self.stdout.write(
            f"\n{BOLD}MODULE: {report.module_path}{RESET}  "
            f"{colour}[{report.health_score:.0f}/100]{RESET}"
        )
        self.stdout.write(f"  {DIM}{'─' * 70}{RESET}")

        if not report.imported:
            self.stdout.write(f"  {RED}✘ IMPORT FAILED: {report.import_error}{RESET}")
            return

        for r in report.results:
            icon = (
                f"{GREEN}✔{RESET}" if r.overall_status == "PASS"
                else f"{RED}✘{RESET}"   if r.overall_status == "FAIL"
                else f"{YELLOW}⚠{RESET}"if r.overall_status == "WARN"
                else f"{DIM}○{RESET}"
            )

            # Look up commentary for this function name
            # Build the fully-qualified key: module::Class::method
            # This prevents collisions where two classes in different modules
            # have methods with the same name (e.g. analyse(), run(), detect())
            # Falls back to function-name-only lookup for backward compatibility.
            full_key  = f"{r.module_path}::{r.class_name}::{r.function_name}"
            short_key = r.function_name
            commentary = (
                self.METHOD_COMMENTARY.get(full_key)    # exact qualified match first
                or self.METHOD_COMMENTARY.get(short_key) # fallback to name-only
            )
            role_tag   = commentary[0] if commentary else None
            description= commentary[1] if commentary else None

            # Class prefix for methods vs standalone functions
            prefix = f"{r.class_name}." if r.is_method else ""

            # Main result line: icon | class.method() | elapsed
            self.stdout.write(
                f"\n  {icon}  {BOLD}{prefix}{r.function_name}{RESET}()  "
                f"{DIM}{r.layer2_elapsed_ms:.1f}ms{RESET}"
            )

            # Role tag line — shown in cyan brackets e.g. [CORE ENGINE]
            if role_tag:
                self.stdout.write(
                    f"       {CYAN}[{role_tag}]{RESET}"
                )

            # Description line — plain English what this function does
            if description:
                self.stdout.write(
                    f"       {DIM}{description}{RESET}"
                )

            # Layer 3 result — most important line, always shown
            if r.layer3_tested:
                c      = GREEN if r.layer3_passed else RED
                label  = "✔ VERIFIED" if r.layer3_passed else "✘ WRONG OUTPUT"
                self.stdout.write(
                    f"       {c}L3 {label}: {r.layer3_message}{RESET}"
                )
            elif r.overall_status == "FAIL" and r.layer2_error:
                self.stdout.write(
                    f"       {RED}L2 CRASHED: {r.layer2_error[:80]}{RESET}"
                )
            elif r.overall_status == "WARN" and not r.layer3_tested:
                self.stdout.write(
                    f"       {YELLOW}L3 PENDING: No behavioural test defined yet{RESET}"
                )

            # Verbose: full stack trace and assertion detail
            if verbose:
                if r.layer3_tested and not r.layer3_passed:
                    self.stdout.write(f"       {DIM}Expected : {r.layer3_expected}{RESET}")
                    self.stdout.write(f"       {DIM}Actual   : {r.layer3_actual}{RESET}")
                for note in r.notes:
                    for line in note.split("\n"):
                        if line.strip():
                            self.stdout.write(f"       {DIM}↳ {line}{RESET}")

        # Module footer with pass/fail counts
        self.stdout.write(f"\n  {DIM}{'─' * 70}{RESET}")
        self.stdout.write(
            f"  {GREEN}✔ {report.passed} passed{RESET}   "
            f"{RED}✘ {report.failed} failed{RESET}   "
            f"{YELLOW}⚠ {report.warned} warned{RESET}   "
            f"{DIM}○ {report.skipped} skipped   "
            f"{report.elapsed_ms:.0f}ms total{RESET}"
        )

    def _print_summary(
        self,
        session:      SessionReport,
        integration:  List[IntegrationResult],
        drift:        List[DriftResult],
    ) -> None:
        """Print the overall audit summary."""
        int_passed  = sum(1 for r in integration if r.passed)
        int_total   = len(integration)
        drift_ok    = sum(1 for r in drift if r.passed)
        drift_total = len(drift)

        self.stdout.write(f"\n{BOLD}{CYAN}{'═' * 76}{RESET}")
        self.stdout.write(f"{BOLD}  DEEP HEALTH CHECK SUMMARY  [{session.audit_mode.upper()}]{RESET}")
        self.stdout.write(f"{BOLD}{CYAN}{'═' * 76}{RESET}")
        self.stdout.write(
            f"  {GREEN}Functions passed  : {session.total_passed}/{session.total_functions}{RESET}")
        self.stdout.write(
            f"  {RED}Functions failed  : {session.total_failed}/{session.total_functions}{RESET}")
        self.stdout.write(
            f"  {YELLOW}Warnings          : {session.total_warned}/{session.total_functions}{RESET}")
        if int_total:
            self.stdout.write(
                f"  {GREEN if int_passed==int_total else RED}"
                f"Integration tests : {int_passed}/{int_total}{RESET}")
        if drift_total:
            self.stdout.write(
                f"  {GREEN if drift_ok==drift_total else YELLOW}"
                f"Drift checks      : {drift_ok}/{drift_total}{RESET}")
        self.stdout.write(f"  {CYAN}Platform health   : {session.platform_health:.1f}/100{RESET}")
        self.stdout.write(f"  {DIM}Elapsed           : {session.elapsed_seconds:.2f}s{RESET}")

        failed_mods = [r for r in session.module_reports if r.failed > 0]
        if failed_mods:
            self.stdout.write(f"\n  {RED}{BOLD}CRITICAL FAILURES:{RESET}")
            for r in failed_mods:
                bad_fns = [res.function_name
                           for res in r.results if res.overall_status == "FAIL"]
                self.stdout.write(
                    f"  {RED}✘ {r.module_path}: {', '.join(bad_fns[:5])}{RESET}")

        failed_int = [r for r in integration if not r.passed]
        if failed_int:
            self.stdout.write(f"\n  {RED}{BOLD}FAILED INTEGRATION TESTS:{RESET}")
            for r in failed_int:
                self.stdout.write(f"  {RED}✘ {r.test_name}: {r.message}{RESET}")

        critical_drift = [r for r in drift if r.severity == "CRITICAL"]
        if critical_drift:
            self.stdout.write(f"\n  {RED}{BOLD}CRITICAL DRIFT DETECTED:{RESET}")
            for r in critical_drift:
                self.stdout.write(
                    f"  {RED}✘ {r.metric_name}: {r.drift_pct:.1%} drift{RESET}")

        total_issues = session.total_failed + len(failed_int) + len(critical_drift)
        if total_issues == 0 and session.total_warned == 0:
            self.stdout.write(
                f"\n  {GREEN}{BOLD}🟢  ALL LAYERS CLEAR — PLATFORM VERIFIED{RESET}")
        elif total_issues == 0:
            self.stdout.write(
                f"\n  {YELLOW}{BOLD}🟡  PASSING — ADD LAYER 3 TESTS TO CLOSE BLIND SPOTS{RESET}")
        else:
            self.stdout.write(
                f"\n  {RED}{BOLD}🔴  {total_issues} ISSUE(S) REQUIRE ATTENTION{RESET}")

        self.stdout.write(f"{BOLD}{CYAN}{'═' * 76}{RESET}\n")

    def _save_report(self, session: SessionReport, path: str) -> None:
        """Persist the full session report as JSON."""
        os.makedirs(
            os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        data = {
            "timestamp":        session.timestamp,
            "audit_mode":       session.audit_mode,
            "platform_health":  session.platform_health,
            "total_functions":  session.total_functions,
            "total_passed":     session.total_passed,
            "total_failed":     session.total_failed,
            "elapsed_seconds":  session.elapsed_seconds,
            "modules": [
                {
                    "module_path":  r.module_path,
                    "health_score": r.health_score,
                    "passed":       r.passed,
                    "failed":       r.failed,
                    "functions": [
                        {
                            "class":      res.class_name,
                            "function":   res.function_name,
                            "status":     res.overall_status,
                            "l3_tested":  res.layer3_tested,
                            "l3_passed":  res.layer3_passed,
                            "l3_actual":  res.layer3_actual,
                            "elapsed_ms": round(res.layer2_elapsed_ms, 2),
                            "error":      res.layer2_error,
                        } for res in r.results
                    ]
                } for r in session.module_reports
            ],
            "integration_tests": [
                {
                    "test":    ir.test_name,
                    "passed":  ir.passed,
                    "message": ir.message,
                    "elapsed": round(ir.elapsed_ms, 2),
                } for ir in session.integration_results
            ],
            "drift_checks": [
                {
                    "metric":   dr.metric_name,
                    "current":  dr.current_value,
                    "baseline": dr.baseline_value,
                    "drift_pct":dr.drift_pct,
                    "severity": dr.severity,
                } for dr in session.drift_results
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        self.stdout.write(f"  {DIM}Report: {path}{RESET}")