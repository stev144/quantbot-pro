# ============================================================
# bot/core/dry_run_test.py
# Dry Run Test — verifies every system component before trading
#
# PURPOSE:
# Run this ONCE before starting bot_runner.py for the first time
# or after making any code changes.
# It tests every file in the system and reports pass or fail.
#
# If any test fails — fix it before running the live bot.
# A failing component in live trading costs real money.
#
# HOW TO RUN:
#   python bot/core/dry_run_test.py
#
# WHAT IT TESTS:
#   1. All imports load without errors
#   2. RegimeDetector produces a valid RegimeResult
#   3. MovingAverageStrategy produces a valid signal dict
#   4. MeanReversionStrategy produces a valid signal dict
#   5. StrategyRouter routes correctly per regime
#   6. TradeNarrativeGenerator produces a valid narrative
#   7. PositionSizer calculates a valid quantity
#   8. Exchange connection (API ping — no orders placed)
#   9. MarketData fetches real price and candles
#  10. Full pipeline dry run — signal to narrative, no order
#  11. Edge case tests — flat market + volatility spike
# ============================================================
import os          # For retrieving environment variables
import sys         # For sys.exit() on critical failure
import time        # For latency measurement
import logging     # Structured logging
import traceback   # For detailed error output on failures
import pandas as pd    # For building test DataFrames
import numpy as np     # For generating synthetic price data
from types import SimpleNamespace
import importlib
from dataclasses import is_dataclass
import pandas as pd
import numpy as np

# -----------------------------------------------------------------
# claude code changed: new — force UTF-8 on stdout/stderr. This file's
# own output uses ✓/✗/~/→ throughout (35 occurrences) as pass/fail/skip
# markers, and Windows' default console codepage (cp1252) cannot encode
# most of them. Without this, the script crashed with UnicodeEncodeError
# on the very first such character it tried to print — before section 1
# of the test even ran — which looked like a test failure but was really
# just a console-encoding mismatch having nothing to do with the bot's
# actual components. reconfigure() is Python 3.7+; guarded in case an
# older interpreter or an unusual stdout wrapper doesn't support it.
# -----------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# -----------------------------------------------------------------
# Make sure the project root is on sys.path when running directly:
#   python bot/core/dry_run_test.py
# -----------------------------------------------------------------
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# -----------------------------------------------------------------
# claude code changed: new — configure Django before any test tries to
# import ExecutionEngine or TradeLogger, both of which pull in
# bot.journal.models.TradeRecord (a Django model). Without this, those
# two import tests failed with "Apps aren't loaded yet" / a misleading
# masked error — not because the components were broken, but because
# this test script itself never set Django up. Same root cause and same
# fix as bot_runner.py (see its header comment for the full explanation);
# this file is meant to be run BEFORE bot_runner.py per its own docstring
# above, so it needs to be correct on its own, not just inherit a fix
# from being imported by something else.
# -----------------------------------------------------------------
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

# Set up clean logging for the test output
logging.basicConfig(
    level=logging.WARNING,
    format="%(message)s"
)

# Create a dedicated logger for test output
logger = logging.getLogger("DryRunTest")


# ============================================================
# TEST RESULT TRACKER
# ============================================================
class TestResults:
    """Tracks pass/fail counts and prints a final report."""

    def __init__(self):
        self.passed = []
        self.failed = []
        self.skipped = []

    def pass_test(self, name: str):
        """Records a passing test."""
        self.passed.append(name)
        print(f"  ✓ PASS  {name}")

    def fail_test(self, name: str, error: str):
        """Records a failing test."""
        self.failed.append((name, error))
        print(f"  ✗ FAIL  {name}")
        print(f"         Error: {error}")

    def skip_test(self, name: str, reason: str):
        """Records a skipped test."""
        self.skipped.append((name, reason))
        print(f"  ~ SKIP  {name} ({reason})")

    def print_summary(self):
        """Prints the final pass/fail summary."""
        total = len(self.passed) + len(self.failed) + len(self.skipped)
        print()
        print("=" * 55)
        print("DRY RUN TEST SUMMARY")
        print("=" * 55)
        print(f"  Total tests : {total}")
        print(f"  Passed      : {len(self.passed)}")
        print(f"  Failed      : {len(self.failed)}")
        print(f"  Skipped     : {len(self.skipped)}")
        print("=" * 55)

        if self.failed:
            print()
            print("FAILED TESTS — fix these before running live:")
            for name, error in self.failed:
                print(f"  ✗ {name}")
                print(f"    {error}")
            print()
            return False

        print()
        print("All tests passed — safe to run bot_runner.py")
        print()
        return True


# ============================================================
# SYNTHETIC DATA GENERATOR
# Creates a realistic OHLCV DataFrame for testing
# Does not require a live exchange connection
# ============================================================
def make_test_df(n_candles: int = 200) -> pd.DataFrame:
    """
    Generates a synthetic OHLCV DataFrame for component testing.
    Uses a random walk to simulate realistic price movement.
    Returns a DataFrame with DatetimeIndex — matches live data format.
    """
    np.random.seed(42)

    returns = np.random.normal(0, 0.01, n_candles)
    close = 5.0 * np.exp(np.cumsum(returns))

    high = close * (1 + np.abs(np.random.normal(0, 0.005, n_candles)))
    low = close * (1 - np.abs(np.random.normal(0, 0.005, n_candles)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = np.random.uniform(1e6, 5e6, n_candles)

    df = pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })

    df.index = pd.date_range(
        start="2024-01-01",
        periods=n_candles,
        freq="1h"
    )

    return df


def _try_import(candidates):
    """
    Try multiple import paths and return the first one that works.
    Useful because some files moved during refactors.
    """
    last_error = None
    for path in candidates:
        try:
            return importlib.import_module(path)
        except Exception as e:
            last_error = e
    raise last_error


# ============================================================
# INDIVIDUAL TESTS
# ============================================================

def test_imports(results: TestResults):
    """Tests that all project modules import without errors."""

    print()
    print("── 1. IMPORT TESTS ──────────────────────────────────")

    import_groups = [
        ("RegimeDetector", [
            "bot.engines.regime_detector",
        ]),
        ("StrategyRouter", [
            "bot.engines.strategy_router",
        ]),
        ("TradeNarrativeGenerator", [
            "bot.engines.trade_narrative",
        ]),
        ("MovingAverageStrategy", [
            "bot.strategies.moving_average",
        ]),
        ("MeanReversionStrategy", [
            "bot.strategies.mean_reversion_strategy",
        ]),
        ("PositionSizer", [
            "bot.risk.position_sizer",
        ]),
        ("TradeAnalytics", [
            "bot.engines.analytics_engine",
            "bot.analytics",
        ]),
        ("OrderManager", [
            "bot.engines.order_manager",
        ]),
        ("MarketData", [
            "bot.engines.market_data",
            "bot.data_fetcher",
        ]),
        ("ExecutionEngine", [
            "bot.engines.execution_engine",
        ]),
        ("TradeLogger", [
            "bot.journal.trade_logger",
            "bot.engines.trade_logger",
        ]),
        ("Trade (model)", [
            "bot.core.trade",
            "bot.journal.models",
            "bot.models",
        ]),
        ("Simulation helpers", [
            "bot.engines.simulation",
        ]),
    ]

    for name, candidates in import_groups:
        try:
            module = _try_import(candidates)
            results.pass_test(f"Import: {name} ({module.__name__})")
        except Exception as e:
            results.fail_test(f"Import: {name}", str(e))


def test_regime_detector(results: TestResults, df: pd.DataFrame):
    """Tests that RegimeDetector classifies the test data."""

    print()
    print("── 2. REGIME DETECTOR ───────────────────────────────")

    try:
        from bot.engines.regime_detector import RegimeDetector

        detector = RegimeDetector()
        result = detector.detect(df)

        assert result is not None, "detect() returned None"
        assert result.regime in (
            "TRENDING_UP", "TRENDING_DOWN", "RANGING", "HIGH_VOLATILITY"
        ), f"Invalid regime: {result.regime}"
        assert result.confidence in ("HIGH", "MEDIUM", "LOW"), \
            f"Invalid confidence: {result.confidence}"
        assert isinstance(result.adx, float), "ADX is not a float"
        assert isinstance(result.atr_ratio, float), "ATR ratio is not a float"
        assert result.summary != "", "Summary is empty"

        results.pass_test(f"RegimeDetector.detect() → {result.regime} ({result.confidence})")
        results.pass_test(f"RegimeDetector fields valid (ADX={result.adx:.1f})")

    except AssertionError as e:
        results.fail_test("RegimeDetector", str(e))
    except Exception as e:
        results.fail_test("RegimeDetector", f"{type(e).__name__}: {e}")


def test_moving_average_strategy(results: TestResults, df: pd.DataFrame):
    """Tests MovingAverageStrategy.check_signal() returns valid dict."""

    print()
    print("── 3. MOVING AVERAGE STRATEGY ───────────────────────")

    try:
        from bot.strategies.moving_average import MovingAverageStrategy

        strategy = MovingAverageStrategy()

        if hasattr(strategy, "check_signal"):
            signal = strategy.check_signal(df)
        elif hasattr(strategy, "evaluate"):
            signal = strategy.evaluate(df)
        else:
            raise AttributeError("MovingAverageStrategy has no check_signal() or evaluate()")

        assert signal is not None, "Strategy returned None"
        assert isinstance(signal, dict), "Strategy output is not a dict"
        assert "signal" in signal, "Missing 'signal' key"
        assert "entry" in signal, "Missing 'entry' key"
        assert "sl" in signal, "Missing 'sl' key"
        assert "tp" in signal, "Missing 'tp' key"
        assert "rsi" in signal, "Missing 'rsi' key"
        assert "reason" in signal, "Missing 'reason' key"
        assert signal["signal"] in ("BUY", "SELL", "NO_SIGNAL"), \
            f"Invalid signal value: {signal['signal']}"

        results.pass_test(
            f"MovingAverageStrategy → {signal['signal']} ({signal['reason']})"
        )

        if signal["signal"] == "BUY":
            assert signal["sl"] < signal["entry"], "BUY stop loss must be below entry"
            assert signal["tp"] > signal["entry"], "BUY take profit must be above entry"
            results.pass_test("BUY signal levels validated (SL < entry < TP)")

        elif signal["signal"] == "SELL":
            assert signal["sl"] > signal["entry"], "SELL stop loss must be above entry"
            assert signal["tp"] < signal["entry"], "SELL take profit must be below entry"
            results.pass_test("SELL signal levels validated (TP < entry < SL)")

    except AssertionError as e:
        results.fail_test("MovingAverageStrategy", str(e))
    except Exception as e:
        results.fail_test("MovingAverageStrategy", f"{type(e).__name__}: {e}")


def test_mean_reversion_strategy(results: TestResults, df: pd.DataFrame):
    """Tests MeanReversionStrategy.evaluate() returns valid dict."""

    print()
    print("── 4. MEAN REVERSION STRATEGY ───────────────────────")

    try:
        from bot.strategies.mean_reversion_strategy import MeanReversionStrategy
        from bot.engines.regime_detector import RegimeResult

        strategy = MeanReversionStrategy()

        fake_regime = RegimeResult(
            regime="RANGING",
            confidence="HIGH",
            adx=15.0,
            atr_ratio=1.0,
            ema_spread_pct=0.1,
            bb_width_pct=1.5,
            adx_trending=False,
            volatility_extreme=False,
            summary="RANGING | HIGH | test",
        )

        if hasattr(strategy, "evaluate"):
            signal = strategy.evaluate(df, fake_regime)
        elif hasattr(strategy, "check_signal"):
            signal = strategy.check_signal(df, fake_regime)
        else:
            raise AttributeError("MeanReversionStrategy has no evaluate() or check_signal()")

        if signal is None:
            results.pass_test("MeanReversionStrategy → None (valid)")
        else:
            assert isinstance(signal, dict), "Strategy output is not a dict"
            assert "signal" in signal, "Missing 'signal' key"
            assert signal["signal"] in ("BUY", "SELL", "NO_SIGNAL"), \
                f"Invalid signal: {signal['signal']}"
            results.pass_test(
                f"MeanReversionStrategy → {signal['signal']} ({signal.get('reason', 'no reason')})"
            )

    except AssertionError as e:
        results.fail_test("MeanReversionStrategy", str(e))
    except Exception as e:
        results.fail_test("MeanReversionStrategy", f"{type(e).__name__}: {e}")


def test_strategy_router(results: TestResults, df: pd.DataFrame):
    """Tests StrategyRouter.route() returns valid enriched signal."""

    print()
    print("── 5. STRATEGY ROUTER ───────────────────────────────")

    try:
        from bot.engines.regime_detector import RegimeDetector
        from bot.engines.strategy_router import StrategyRouter

        detector = RegimeDetector()
        router = StrategyRouter()

        regime_result = detector.detect(df)
        signal = router.route(df, regime_result)

        assert signal is not None, "route() returned None"
        assert isinstance(signal, dict), "route() did not return dict"
        assert "signal" in signal, "Missing 'signal' key"
        assert "regime" in signal, "Missing 'regime' key"
        assert "regime_confidence" in signal, "Missing 'regime_confidence' key"
        assert signal["signal"] in ("BUY", "SELL", "NO_SIGNAL"), \
            f"Invalid signal: {signal['signal']}"

        results.pass_test(
            f"StrategyRouter.route() → {signal['signal']} | "
            f"Regime: {signal['regime']} ({signal['regime_confidence']})"
        )

        if signal["signal"] == "BUY":
            assert signal["sl"] < signal["entry"] < signal["tp"], \
                "Invalid BUY structure (SL < Entry < TP violated)"
            results.pass_test("Router BUY structure valid")

        elif signal["signal"] == "SELL":
            assert signal["tp"] < signal["entry"] < signal["sl"], \
                "Invalid SELL structure (TP < Entry < SL violated)"
            results.pass_test("Router SELL structure valid")

        stats = router.get_route_stats()
        assert isinstance(stats, dict), "get_route_stats() did not return dict"
        assert "total_candles_evaluated" in stats, "Missing total in route stats"
        results.pass_test("StrategyRouter.get_route_stats() works")

    except AssertionError as e:
        results.fail_test("StrategyRouter", str(e))
    except Exception as e:
        results.fail_test("StrategyRouter", f"{type(e).__name__}: {e}")


def test_trade_narrative(results: TestResults, df: pd.DataFrame):
    """Tests TradeNarrativeGenerator produces valid narrative."""

    print()
    print("── 6. TRADE NARRATIVE ───────────────────────────────")

    try:
        from bot.engines.regime_detector import RegimeDetector
        from bot.engines.strategy_router import StrategyRouter
        from bot.engines.trade_narrative import TradeNarrativeGenerator

        detector = RegimeDetector()
        router = StrategyRouter()
        narrator = TradeNarrativeGenerator()

        regime_result = detector.detect(df)
        signal = router.route(df, regime_result)
        signal["symbol"] = "TEST/USDT"

        if signal["signal"] == "NO_SIGNAL":
            signal = {
                "signal": "BUY",
                "entry": 5.00,
                "sl": 4.80,
                "tp": 5.60,
                "rsi": 47.0,
                "reason": "ema_buy_setup",
                "strategy": "MovingAverageStrategy",
                "regime": "TRENDING_UP",
                "regime_confidence": "HIGH",
                "adx": 28.5,
                "atr_ratio": 1.1,
                "ema_spread_pct": 0.7,
                "bb_width_pct": 3.2,
                "regime_summary": "TRENDING_UP | HIGH | test",
                "volatility_extreme": False,
                "symbol": "TEST/USDT",
            }

        narrative = narrator.generate_entry(signal, "TEST/USDT")

        assert narrative is not None, "generate_entry() returned None"
        assert hasattr(narrative, "entry_narrative"), "Missing entry_narrative attr"
        assert narrative.entry_narrative != "", "Entry narrative text is empty"
        assert narrative.regime != "", "Regime is empty in narrative"
        assert isinstance(narrative.conditions, dict), "Conditions is not a dict"
        assert len(narrative.conditions) > 0, "Conditions dict is empty"

        results.pass_test("TradeNarrativeGenerator.generate_entry() works")
        results.pass_test(f"Narrative conditions: {len(narrative.conditions)} fields")

        narrative_dict = narrator.to_dict(narrative)
        assert isinstance(narrative_dict, dict), "to_dict() did not return dict"
        assert "entry_narrative" in narrative_dict, "Missing entry_narrative in dict"
        results.pass_test("TradeNarrativeGenerator.to_dict() works")

        exit_text = narrator.generate_exit(
            narrative=narrative,
            exit_price=5.60,
            exit_reason="take_profit",
            net_pnl=47.20,
            r_multiple=2.94,
            holding_candles=23,
        )
        assert exit_text != "", "Exit narrative is empty"
        results.pass_test("TradeNarrativeGenerator.generate_exit() works")

    except AssertionError as e:
        results.fail_test("TradeNarrativeGenerator", str(e))
    except Exception as e:
        results.fail_test("TradeNarrativeGenerator", f"{type(e).__name__}: {e}")


def test_position_sizer(results: TestResults):
    """Tests PositionSizer.calculate() returns valid quantity."""

    print()
    print("── 7. POSITION SIZER ────────────────────────────────")

    try:
        from bot.risk.position_sizer import PositionSizer

        sizer = PositionSizer(risk_pct=0.01)

        qty = sizer.calculate(
            balance=5000,
            entry_price=5.00,
            stop_price=4.90
        )

        assert qty > 0, f"Quantity must be positive, got {qty}"
        assert round(qty, 4) == 500.0, f"Expected 500.0, got {qty}"

        results.pass_test(f"PositionSizer.calculate() → {qty} units (correct)")

        qty_zero = sizer.calculate(
            balance=5000,
            entry_price=5.00,
            stop_price=5.00
        )
        assert qty_zero == 0.0, f"Expected 0 for zero stop distance, got {qty_zero}"
        results.pass_test("PositionSizer returns 0 for zero stop distance (safe)")

    except AssertionError as e:
        results.fail_test("PositionSizer", str(e))
    except Exception as e:
        results.fail_test("PositionSizer", f"{type(e).__name__}: {e}")


def test_simulation_helpers(results: TestResults):
    """Tests apply_slippage, calc_fees, calculate_pnl."""

    print()
    print("── 8. SIMULATION HELPERS ────────────────────────────")

    try:
        from bot.engines.simulation import apply_slippage, calc_fees, calculate_pnl

        long_entry = apply_slippage(5.00, "LONG", True)
        assert long_entry > 5.00, "Long entry should be above signal price"
        results.pass_test(f"apply_slippage LONG entry: 5.00 → {long_entry}")

        short_entry = apply_slippage(5.00, "SHORT", True)
        assert short_entry < 5.00, "Short entry should be below signal price"
        results.pass_test(f"apply_slippage SHORT entry: 5.00 → {short_entry}")

        fees = calc_fees(5.00, 5.20, 500)
        assert fees > 0, f"Fees must be positive, got {fees}"
        results.pass_test(f"calc_fees: ${fees:.4f} (correct)")

        pnl = calculate_pnl("LONG", 5.00, 5.20, 500)
        assert pnl == 100.0, f"Expected $100 profit, got {pnl}"
        results.pass_test(f"calculate_pnl LONG: ${pnl} (correct)")

        pnl_short = calculate_pnl("SHORT", 5.00, 4.80, 500)
        assert pnl_short == 100.0, f"Expected $100 short profit, got {pnl_short}"
        results.pass_test(f"calculate_pnl SHORT: ${pnl_short} (correct)")

    except AssertionError as e:
        results.fail_test("Simulation helpers", str(e))
    except Exception as e:
        results.fail_test("Simulation helpers", f"{type(e).__name__}: {e}")


def test_exchange_connection(results: TestResults):
    """Tests Binance API connection — no orders placed."""

    print()
    print("── 9. EXCHANGE CONNECTION ───────────────────────────")

    try:
        import ccxt

        api_key = os.getenv("BINANCE_API_KEY", "")
        api_secret = os.getenv("BINANCE_API_SECRET", "")

        if not api_key or api_key == "YOUR_API_KEY_HERE":
            results.skip_test(
                "Exchange connection",
                "No API credentials set — set BINANCE_API_KEY env var"
            )
            return

        exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "options": {"defaultType": "spot"},
        })

        start = time.time()
        exchange.load_markets()
        latency_ms = int((time.time() - start) * 1000)

        results.pass_test(f"Binance API connected (latency: {latency_ms}ms)")

        start = time.time()
        ticker = exchange.fetch_ticker("NEAR/USDT")
        price_ms = int((time.time() - start) * 1000)

        assert ticker.get("last") is not None, "Could not fetch NEAR/USDT price"
        results.pass_test(
            f"NEAR/USDT price fetch: ${ticker['last']} ({price_ms}ms)"
        )

        balance = exchange.fetch_balance()
        usdt = balance.get("USDT", {}).get("free", 0)
        results.pass_test(f"Balance fetch: ${usdt:.2f} USDT free")

    except ccxt.AuthenticationError:
        results.fail_test("Exchange connection", "Authentication failed — check API keys")
    except Exception as e:
        results.fail_test("Exchange connection", f"{type(e).__name__}: {e}")


def test_full_pipeline(results: TestResults, df: pd.DataFrame):
    """
    Tests the complete signal pipeline end-to-end.
    Simulates one full candle: detect → route → narrative.
    No orders are placed.
    """

    print()
    print("── 10. FULL PIPELINE DRY RUN ────────────────────────")

    try:
        from bot.engines.regime_detector import RegimeDetector
        from bot.engines.strategy_router import StrategyRouter
        from bot.engines.trade_narrative import TradeNarrativeGenerator
        from bot.risk.position_sizer import PositionSizer

        detector = RegimeDetector()
        router = StrategyRouter()
        narrator = TradeNarrativeGenerator()
        sizer = PositionSizer(risk_pct=0.01)

        regime_result = detector.detect(df)
        results.pass_test(
            f"Pipeline step 1 — Regime: {regime_result.regime} "
            f"({regime_result.confidence}) ADX={regime_result.adx:.1f}"
        )

        start_time = time.time()
        signal = router.route(df, regime_result)
        duration_ms = (time.time() - start_time) * 1000
        assert duration_ms < 100, f"StrategyRouter too slow: {duration_ms:.2f}ms"
        results.pass_test(f"Pipeline step 2 — Signal: {signal['signal']} via {signal.get('strategy', 'none')}")
        results.pass_test(f"Pipeline performance OK ({duration_ms:.2f}ms)")

        if signal["signal"] in ("BUY", "SELL"):
            signal["symbol"] = "NEAR/USDT"
            qty = sizer.calculate(
                balance=5000,
                entry_price=signal["entry"],
                stop_price=signal["sl"],
            )
            results.pass_test(f"Pipeline step 3 — Position size: {qty} units")

            narrative = narrator.generate_entry(signal, "NEAR/USDT")
            results.pass_test(
                f"Pipeline step 4 — Narrative generated "
                f"({len(narrative.entry_narrative)} chars)"
            )

            print()
            print("  SAMPLE NARRATIVE PREVIEW:")
            print(f"  {narrative.entry_narrative[:200]}...")
            print()

        else:
            results.pass_test(
                f"Pipeline step 3 — No signal this candle "
                f"(reason: {signal.get('reason', 'unknown')})"
            )
            results.pass_test("Pipeline step 4 — Narrative skipped (no signal)")

        results.pass_test("Full pipeline completed without errors")

    except Exception as e:
        results.fail_test("Full pipeline", f"{type(e).__name__}: {e}")
        print(f"  Traceback: {traceback.format_exc()}")


def test_edge_cases(results: TestResults):
    """Tests system behavior under edge-case market conditions."""

    print()
    print("── 11. EDGE CASE TESTS ─────────────────────────────")

    try:
        from bot.engines.regime_detector import RegimeDetector

        detector = RegimeDetector()

        df_flat = pd.DataFrame({
            "open": [5] * 100,
            "high": [5] * 100,
            "low": [5] * 100,
            "close": [5] * 100,
            "volume": [1000] * 100,
        })
        df_flat.index = pd.date_range("2024-01-01", periods=100, freq="1h")

        result_flat = detector.detect(df_flat)
        assert result_flat is not None, "Detector failed on flat data"
        results.pass_test("RegimeDetector handles flat market")

        df_spike = df_flat.copy()
        df_spike.iloc[50, df_spike.columns.get_loc("high")] = 10
        df_spike.iloc[50, df_spike.columns.get_loc("low")] = 1

        result_spike = detector.detect(df_spike)
        assert result_spike is not None, "Detector failed on spike data"
        results.pass_test("RegimeDetector handles volatility spike")

    except Exception as e:
        results.fail_test("Edge cases", f"{type(e).__name__}: {e}")


# ============================================================
# MAIN — runs all tests and prints report
# ============================================================
def main():
    """Runs all dry run tests and prints a pass/fail report."""

    print()
    print("=" * 55)
    print("QUANT BOT PRO — DRY RUN TEST")
    print("Testing all system components before live trading")
    print("=" * 55)

    results = TestResults()

    print()
    print("Building synthetic test data (200 candles)...")
    df = make_test_df(n_candles=200)
    print(f"Test DataFrame: {len(df)} candles | {df.index[0]} → {df.index[-1]}")

    test_imports(results)
    test_regime_detector(results, df)
    test_moving_average_strategy(results, df)
    test_mean_reversion_strategy(results, df)
    test_strategy_router(results, df)
    test_trade_narrative(results, df)
    test_position_sizer(results)
    test_simulation_helpers(results)
    test_exchange_connection(results)
    test_full_pipeline(results, df)
    test_edge_cases(results)

    all_passed = results.print_summary()

    if not all_passed:
        print("🚨 DO NOT RUN BOT — FIX FAILURES FIRST")
        sys.exit(1)


if __name__ == "__main__":
    main()