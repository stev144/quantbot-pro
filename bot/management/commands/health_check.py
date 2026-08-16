"""
health_check.py — Quant Bot Full System Health Monitor

Usage:
    python manage.py health_check
    python manage.py health_check --verbose
    python manage.py health_check --module backtester
    python manage.py health_check --limit 200
    python manage.py health_check --no-async
"""

import os
import sys
import json
import time
import socket
import logging
import traceback
import inspect
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from django.core.cache import cache
from django.core.management.base import BaseCommand

# claude code changed: new — same fix bot_runner.py already has (see that
# file's header comment for the full rationale). This command's summary
# banner uses box-drawing characters (═) and status glyphs (✔/⚠/✖), which
# Windows' default console codepage (cp1252) can't encode — without this,
# `python manage.py health_check` crashes with UnicodeEncodeError before
# printing any results at all. Found while establishing a post-migration
# health baseline; this file never got the same fix bot_runner.py did.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import psutil
except Exception:
    psutil = None

try:
    import ccxt
except Exception:
    ccxt = None

try:
    from django.db import connection
except Exception:
    connection = None


# ── Terminal colours ──────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

ICON_PASS = f"{GREEN}✔  PASS{RESET}"
ICON_WARN = f"{YELLOW}⚠  WARN{RESET}"
ICON_FAIL = f"{RED}✘  FAIL{RESET}"

FIX_TIPS = {
    "System Resources": [
        "Close memory-heavy processes if RSS keeps rising",
        "Check for runaway loops or repeated refreshes",
        "Use caching for expensive computations",
    ],
    "Dependency Health": [
        "Verify cache backend is configured",
        "Verify database connection is healthy",
        "Check Binance env vars for live connectivity",
    ],
    "Data Fetcher": [
        "get_klines() uses total_candles= not limit=",
        "Check Binance API connectivity",
        "Check API keys and rate limits",
    ],
    "Market Data": [
        "MarketData class needs exchange object passed in",
        "Check get_ohlcv() returns a DataFrame with OHLCV columns",
    ],
    "Regime Detector": [
        "RegimeDetector.detect(df) must return a RegimeResult object",
        "Check ADX calculation has enough candles (min 14)",
    ],
    "Strategy Router": [
        "StrategyRouter.route(df, regime_result) must return a signal dict",
        "Check RegimeResult is passed correctly",
    ],
    "Signal Engine": [
        "generate_signal(df, i) must return a dict with a 'signal' key",
        "Check that index i is within DataFrame bounds",
    ],
    "Trade Filter": [
        "trade_quality_filter(signal) must return True or False",
        "Check all expected keys exist in the signal dict",
    ],
    "Position Sizer": [
        "PositionSizer.calculate(balance, entry, stop) must return a positive number",
        "Check stop != entry to avoid division by zero",
    ],
    "Backtester": [
        "Backtester(df).run() must return a results dict",
        "Check df has enough rows and correct OHLCV columns",
    ],
    "Portfolio Backtester": [
        "PortfolioBacktester.run() must return a summary dict",
        "Check constructor args match your actual class signature",
    ],
    "Trade Logger": [
        "TradeLogger.log_entry() needs signal and fill_result dicts",
        "Check all required fields are present",
    ],
    "Analytics": [
        "TradeAnalytics.summary() must return a dict",
        "Check that trades were recorded before summary()",
    ],
    "Trade Narrative": [
        "TradeNarrativeGenerator.generate_entry(signal, symbol) must return a narrative object",
        "Check all signal keys are present",
    ],
    "Structure Analyser": [
        "Structure analyser function/class must accept a DataFrame",
        "DataFrame needs high, low, close columns",
    ],
    "Strategy Scorer": [
        "StrategyScorer(results).evaluate() returns score inside the dict",
        "Check results dict structure matches expected format",
    ],
}

SLOW_THRESHOLDS = {
    "System Resources": 0.6,
    "Dependency Health": 2.5,
    "Data Fetcher": 3.0,
    "Market Data": 2.0,
    "Regime Detector": 2.0,
    "Strategy Router": 2.0,
    "Signal Engine": 2.0,
    "Trade Filter": 1.5,
    "Position Sizer": 1.0,
    "Backtester": 8.0,
    "Portfolio Backtester": 12.0,
    "Trade Logger": 1.5,
    "Analytics": 2.0,
    "Trade Narrative": 2.0,
    "Structure Analyser": 2.0,
    "Strategy Scorer": 2.0,
}


def _setup_file_logger(log_path):
    os.makedirs(os.path.dirname(log_path) if os.path.dirname(log_path) else ".", exist_ok=True)
    logger = logging.getLogger("health_check")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(handler)
    return logger


def _make_dummy_df(rows=120):
    """Creates a realistic dummy OHLCV DataFrame for testing."""
    import numpy as np

    np.random.seed(42)
    close = 50000 + np.cumsum(np.random.randn(rows) * 200)
    high = close + np.random.uniform(50, 300, rows)
    low = close - np.random.uniform(50, 300, rows)
    open_ = close - np.random.randn(rows) * 100
    vol = np.random.uniform(1, 10, rows)

    df = pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": vol,
    })
    df.index = pd.date_range("2024-01-01", periods=rows, freq="1h")
    return df


def _safe_first_existing_attr(candidates, label="object"):
    """
    candidates = [(module_path, attr_name), ...]
    """
    last_err = None
    for module_path, attr_name in candidates:
        try:
            module = __import__(module_path, fromlist=[attr_name])
            return getattr(module, attr_name)
        except Exception as e:
            last_err = e
    raise ImportError(f"Could not import {label}. Last error: {last_err}")


class Command(BaseCommand):
    help = "Runs a full health check across all Quant Bot engine files"

    def add_arguments(self, parser):
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--module", type=str, default=None)
        parser.add_argument("--limit", type=int, default=120)
        parser.add_argument("--no-async", action="store_true")
        parser.add_argument("--log-file", type=str, default="logs/health_check.log")

    def handle(self, *args, **options):
        verbose = options["verbose"]
        target = options["module"]
        no_async = options["no_async"]
        log_path = options["log_file"]
        limit = max(120, options["limit"])

        self.logger = _setup_file_logger(log_path)
        self._log("session_start", {"timestamp": datetime.now().isoformat()})
        self._print_header(limit, log_path)

        # Shared dummy df for all checks.
        self.dummy_df = _make_dummy_df(limit)

        all_checks = [
            ("System Resources", lambda: self._check_system_resources()),
            ("Dependency Health", lambda: self._check_dependency_health()),
            ("Data Fetcher", lambda: self._check_data_fetcher(limit)),
            ("Market Data", lambda: self._check_market_data()),
            ("Regime Detector", lambda: self._check_regime_detector()),
            ("Strategy Router", lambda: self._check_strategy_router()),
            ("Signal Engine", lambda: self._check_signal_engine()),
            ("Trade Filter", lambda: self._check_trade_filter()),
            ("Position Sizer", lambda: self._check_position_sizer()),
            ("Backtester", lambda: self._check_backtester()),
            ("Portfolio Backtester", lambda: self._check_portfolio_backtester(limit)),
            ("Trade Logger", lambda: self._check_trade_logger()),
            ("Analytics", lambda: self._check_analytics()),
            ("Trade Narrative", lambda: self._check_trade_narrative()),
            ("Structure Analyser", lambda: self._check_structure_analyser()),
            ("Strategy Scorer", lambda: self._check_strategy_scorer()),
        ]

        if target:
            all_checks = [
                (n, f) for n, f in all_checks
                if target.lower().replace("_", " ") in n.lower()
            ]
            if not all_checks:
                self.stdout.write(f"{RED}No module matching: {target}{RESET}")
                return

        results = []
        session_start = time.time()

        if no_async or len(all_checks) == 1:
            for name, fn in all_checks:
                r = self._run_single(name, fn, verbose)
                results.append(r)
                self._print_result(r, verbose)
        else:
            results = self._run_async(all_checks, verbose)

        self._print_summary(results, time.time() - session_start, log_path)

    # ── ASYNC ─────────────────────────────────────────────
    def _run_async(self, checks, verbose):
        results_map = {}
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(self._run_single, name, fn, verbose): (i, name)
                for i, (name, fn) in enumerate(checks)
            }
            for future in as_completed(futures):
                i, name = futures[future]
                try:
                    r = future.result()
                except Exception as e:
                    r = {
                        "name": name,
                        "status": "fail",
                        "message": str(e),
                        "detail": [traceback.format_exc()],
                        "elapsed": 0,
                        "throughput": 0,
                        "tips": FIX_TIPS.get(name, []),
                    }
                results_map[i] = r
                self._print_result(r, verbose)
        return [results_map[i] for i in sorted(results_map)]

    # ── RUN ONE CHECK ─────────────────────────────────────
    def _run_single(self, name, fn, verbose):
        start = time.time()
        try:
            status, message, detail = fn()
            elapsed = time.time() - start

            threshold = SLOW_THRESHOLDS.get(name, 2.0)
            tips = FIX_TIPS.get(name, []) if status != "pass" else []

            # Convert slow "pass" checks into warnings.
            if status == "pass" and elapsed > threshold:
                status = "warn"
                message = f"{message} (slow: {elapsed:.2f}s > {threshold:.2f}s)"
                tips = tips + [
                    "Consider caching this step",
                    "Check for repeated API calls or redundant recomputation",
                ]

            throughput = round(60 / elapsed, 1) if elapsed > 0.001 else 999
            r = {
                "name": name,
                "status": status,
                "message": message,
                "detail": detail or [],
                "elapsed": elapsed,
                "throughput": throughput,
                "tips": tips,
            }
        except Exception as e:
            elapsed = time.time() - start
            r = {
                "name": name,
                "status": "fail",
                "message": f"{type(e).__name__}: {str(e)[:120]}",
                "detail": [traceback.format_exc()],
                "elapsed": elapsed,
                "throughput": 0,
                "tips": FIX_TIPS.get(name, []),
            }

        self._log("check_result", {
            "name": r["name"],
            "status": r["status"],
            "message": r["message"],
            "elapsed": round(r["elapsed"], 4),
        })
        return r

    # ── PRINT RESULT ──────────────────────────────────────
    def _print_result(self, r, verbose):
        icon = (
            ICON_PASS if r["status"] == "pass"
            else ICON_WARN if r["status"] == "warn"
            else ICON_FAIL
        )
        self.stdout.write(
            f"  {icon}  {BOLD}{r['name']:<26}{RESET}  "
            f"{r['message']:<54}  {DIM}{r['elapsed']:.2f}s{RESET}"
        )
        if verbose:
            for line in r.get("detail", []):
                for subline in str(line).split("\n"):
                    if subline.strip():
                        self.stdout.write(f"          {DIM}→ {subline}{RESET}")
            if r["status"] != "pass" and r.get("tips"):
                self.stdout.write(f"          {YELLOW}💡 Fix suggestions:{RESET}")
                for tip in r["tips"]:
                    self.stdout.write(f"          {YELLOW}   • {tip}{RESET}")
            self.stdout.write("")

    # ── SUMMARY ───────────────────────────────────────────
    def _print_summary(self, results, elapsed, log_path):
        passed = sum(1 for r in results if r["status"] == "pass")
        warned = sum(1 for r in results if r["status"] == "warn")
        failed = sum(1 for r in results if r["status"] == "fail")
        total = len(results)

        # Simple weighted system health score.
        health_score = max(0, round(((passed * 2 + warned) / max(total * 2, 1)) * 100, 1))
        slowest = sorted(results, key=lambda x: x["elapsed"], reverse=True)[:3]

        self.stdout.write("")
        self.stdout.write(f"{BOLD}{CYAN}{'═' * 72}{RESET}")
        self.stdout.write(f"{BOLD}  SUMMARY{RESET}")
        self.stdout.write(f"{BOLD}{CYAN}{'═' * 72}{RESET}")
        self.stdout.write(f"  {GREEN}Passed  : {passed}/{total}{RESET}")
        self.stdout.write(f"  {YELLOW}Warnings: {warned}/{total}{RESET}")
        self.stdout.write(f"  {RED}Failed  : {failed}/{total}{RESET}")
        self.stdout.write(f"  {CYAN}Health  : {health_score}/100{RESET}")
        self.stdout.write(f"  {DIM}Time    : {elapsed:.2f}s   Log: {log_path}{RESET}")
        self.stdout.write("")

        if failed == 0 and warned == 0:
            self.stdout.write(f"  {GREEN}{BOLD}🟢  ALL SYSTEMS OPERATIONAL{RESET}")
        elif failed == 0:
            self.stdout.write(f"  {YELLOW}{BOLD}🟡  RUNNING WITH WARNINGS{RESET}")
        else:
            self.stdout.write(f"  {RED}{BOLD}🔴  {failed} ENGINE(S) FAILED{RESET}")
            for r in results:
                if r["status"] == "fail":
                    self.stdout.write(f"    {RED}✘  {r['name']}: {r['message']}{RESET}")

        self.stdout.write("")
        self.stdout.write(f"{BOLD}Slowest modules:{RESET}")
        for r in slowest:
            self.stdout.write(f"  {DIM}• {r['name']}: {r['elapsed']:.2f}s ({r['status']}){RESET}")

        self.stdout.write(f"{BOLD}{CYAN}{'═' * 72}{RESET}\n")
        self._log("session_summary", {
            "passed": passed,
            "warned": warned,
            "failed": failed,
            "health_score": health_score,
            "elapsed": round(elapsed, 3),
        })

    def _print_header(self, limit, log_path):
        self.stdout.write("")
        self.stdout.write(f"{BOLD}{CYAN}{'═' * 72}{RESET}")
        self.stdout.write(f"{BOLD}{CYAN}   QUANT BOT — FULL SYSTEM HEALTH MONITOR{RESET}")
        self.stdout.write(f"{BOLD}{CYAN}{'═' * 72}{RESET}")
        self.stdout.write(
            f"  {DIM}Candle limit: {limit}   "
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}"
        )
        self.stdout.write(f"  {DIM}Log: {log_path}{RESET}\n")

    def _log(self, event, data):
        self.logger.info(json.dumps({"event": event, **data}, default=str))

    # ══════════════════════════════════════════════════════
    # MODULE CHECKS — aligned to your current architecture
    # ══════════════════════════════════════════════════════

    def _check_system_resources(self):
        if psutil is None:
            return "warn", "psutil not installed — memory/CPU checks skipped", [
                "pip install psutil for RAM/CPU monitoring",
            ]

        proc = psutil.Process(os.getpid())
        rss_mb = proc.memory_info().rss / (1024 * 1024)
        cpu = proc.cpu_percent(interval=0.1)

        # Conservative thresholds.
        if rss_mb > 500:
            return "warn", f"High memory usage: {rss_mb:.1f} MB", [
                "Look for repeated backtests or duplicate JS refreshes",
                "Check for runaway loops or large in-memory caches",
            ]

        if cpu > 85:
            return "warn", f"High CPU usage: {cpu:.1f}%", [
                "Check for heavy repeated calculations on refresh",
                "Move expensive jobs to cache or background workers",
            ]

        return "pass", f"Memory {rss_mb:.1f} MB | CPU {cpu:.1f}%", []

    def _check_dependency_health(self):
        tips = []
        status = "pass"

        # Cache sanity.
        try:
            cache.set("health_check_ping", "ok", 10)
            ping = cache.get("health_check_ping")
            if ping != "ok":
                status = "warn"
                tips.append("Cache set/get mismatch — check cache backend")
        except Exception as e:
            return "fail", f"Cache health check failed: {e}", [traceback.format_exc()]

        # Database sanity.
        if connection is not None:
            try:
                connection.ensure_connection()
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            except Exception as e:
                return "fail", f"Database health check failed: {e}", [
                    traceback.format_exc(),
                    "Check DB connection settings and credentials",
                ]

        # Binance env vars for live mode.
        api_key = os.getenv("BINANCE_API_KEY")
        api_secret = os.getenv("BINANCE_API_SECRET")
        if not api_key or not api_secret:
            status = "warn"
            tips.append("BINANCE_API_KEY / BINANCE_API_SECRET are missing")

        # Internet reachability (non-fatal).
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=2).close()
        except Exception:
            status = "warn"
            tips.append("Internet check failed — live exchange connectivity may fail")

        if tips:
            return status, "Dependency health OK with warnings", tips
        return "pass", "Cache, DB and env checks passed", []

    def _check_data_fetcher(self, limit):
        from bot.data_fetcher import get_klines

        data = get_klines("BTCUSDT", "1h", total_candles=limit)

        if data is None:
            return "fail", "Returned None", [
                "Check Binance API connectivity",
                "Check API key / rate limits",
            ]

        if isinstance(data, pd.DataFrame):
            if data.empty:
                return "fail", "Returned empty DataFrame", [
                    "Check Binance API connectivity",
                    "Check symbol and interval values",
                ]
            df = data
            keys = list(df.columns)
            return "pass", f"{len(df)} candles fetched OK", [
                f"Fields: {', '.join(keys[:6])}"
            ]

        if not data:
            return "fail", "Returned empty data", [
                "Check Binance API connectivity",
                "Check API key in settings.py",
            ]

        if len(data) < 10:
            return "warn", f"Only {len(data)} candles returned", [
                f"Requested {limit}, got {len(data)}",
            ]

        first = data[0] if isinstance(data, list) else {}
        keys = list(first.keys()) if isinstance(first, dict) else []
        return "pass", f"{len(data)} candles fetched OK", [
            f"Fields: {', '.join(keys[:6]) if keys else 'non-dict rows'}"
        ]

    def _check_market_data(self):
        # Try your actual MarketData class, with a couple of fallbacks.
        try:
            MarketData = _safe_first_existing_attr(
                [
                    ("bot.engines.market_data", "MarketData"),
                    ("bot.engines.market_data", "DataFetcher"),
                    ("bot.data.market_data", "MarketData"),
                ],
                label="MarketData",
            )
        except ImportError:
            return "warn", "MarketData class not found — skipping class-level check", [
                "Check actual module path / class name",
            ]

        df = self.dummy_df
        required = ["open", "high", "low", "close", "volume"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            return "fail", f"Dummy df missing: {missing}", []

        # Instantiation varies by implementation; best-effort only.
        try:
            sig = inspect.signature(MarketData)
            params = sig.parameters
            if len(params) == 0:
                obj = MarketData()
            else:
                # Try the most common constructor styles.
                try:
                    obj = MarketData(exchange=None)
                except Exception:
                    try:
                        obj = MarketData(df)
                    except Exception:
                        obj = MarketData()
        except Exception as e:
            return "warn", f"MarketData import OK but constructor mismatch: {e}", [
                "Check constructor signature in bot/engines/market_data.py",
            ]

        return "pass", f"MarketData importable, dummy df has {len(df)} rows", [
            f"Class: {obj.__class__.__name__}",
            f"Columns: {', '.join(df.columns.tolist()[:5])}",
        ]

    def _check_regime_detector(self):
        from bot.engines.regime_detector import RegimeDetector

        detector = RegimeDetector()
        df = self.dummy_df

        try:
            result = detector.detect(df)
        except Exception as e:
            return "fail", f"detector.detect(df) raised: {e}", [traceback.format_exc()]

        if result is None:
            return "fail", "detect() returned None", []

        regime = getattr(result, "regime", None) or getattr(result, "dominant", None) or str(result)
        confidence = getattr(result, "confidence", None)

        valid = ["TRENDING_UP", "TRENDING_DOWN", "RANGING", "HIGH_VOLATILITY", "UNKNOWN"]
        if regime not in valid:
            return "warn", f"Regime '{regime}' not in standard list", [
                f"Expected one of: {valid}",
                "Check RegimeResult.regime attribute name",
            ]

        detail = [f"Regime: {regime}"]
        if confidence is not None:
            detail.append(f"Confidence: {confidence}")
        return "pass", f"Regime detected: {regime}", detail

    def _check_strategy_router(self):
        from bot.engines.regime_detector import RegimeDetector
        from bot.engines.strategy_router import StrategyRouter

        df = self.dummy_df
        detector = RegimeDetector()
        regime_result = detector.detect(df)
        router = StrategyRouter()

        try:
            result = router.route(df, regime_result)
        except Exception as e:
            return "fail", f"router.route() raised: {e}", [traceback.format_exc()]

        if result is None:
            return "fail", "route() returned None", []

        signal = result.get("signal", "NOT_FOUND") if isinstance(result, dict) else str(result)
        return "pass", f"Router returned signal: {signal}", [
            f"Result keys: {list(result.keys()) if isinstance(result, dict) else 'non-dict'}"
        ]

    def _check_signal_engine(self):
        try:
            from bot.engines.signal_engine import generate_signal
        except Exception:
            return "warn", "generate_signal not importable — skipping check", [
                "Check bot.engines.signal_engine",
            ]

        df = self.dummy_df
        i = len(df) // 2

        try:
            result = generate_signal(df, i)
        except Exception as e:
            return "fail", f"generate_signal(df, {i}) raised: {e}", [traceback.format_exc()]

        if result is None:
            return "warn", "generate_signal returned None — treated as HOLD", []

        signal = result.get("signal", result) if isinstance(result, dict) else result
        return "pass", f"Signal = {signal} at index {i}", []

    def _check_trade_filter(self):
        try:
            trade_quality_filter = _safe_first_existing_attr(
                [
                    ("bot.engines.trade_filter", "trade_quality_filter"),
                    ("bot.engines.trade_filter", "TradeFilter"),
                ],
                label="trade_quality_filter",
            )
        except ImportError:
            return "warn", "Trade filter not importable — skipping", [
                "Check bot.engines.trade_filter",
            ]

        dummy_signal = {
            "signal": "BUY",
            "atr": 1.5,
            "adx": 25.0,
            "spread": 0.001,
            "regime": "TRENDING_UP",
            "r_multiple": 2.0,
        }

        try:
            if inspect.isclass(trade_quality_filter):
                obj = trade_quality_filter()
                result = obj.evaluate(dummy_signal) if hasattr(obj, "evaluate") else obj(dummy_signal)
            else:
                result = trade_quality_filter(dummy_signal)
        except Exception as e:
            return "fail", f"trade filter raised: {e}", [traceback.format_exc()]

        if result is None:
            return "fail", "trade filter returned None", []

        return "pass", f"Filter result: {result}", [f"Input: {dummy_signal}"]

    def _check_position_sizer(self):
        try:
            PositionSizer = _safe_first_existing_attr(
                [
                    ("bot.risk.position_sizer", "PositionSizer"),
                    ("bot.engines.position_sizer", "PositionSizer"),
                ],
                label="PositionSizer",
            )
        except ImportError:
            return "warn", "PositionSizer not importable — skipping", [
                "Check bot.risk.position_sizer or bot.engines.position_sizer",
            ]

        try:
            sizer = PositionSizer(risk_pct=0.01)
        except Exception:
            try:
                sizer = PositionSizer()
            except Exception as e:
                return "fail", f"Cannot instantiate PositionSizer: {e}", [traceback.format_exc()]

        balance = 1000.0
        entry_price = 50000.0
        stop_price = 49000.0

        if entry_price == stop_price:
            return "fail", "entry == stop — division by zero risk", []

        try:
            if hasattr(sizer, "calculate"):
                size = sizer.calculate(balance, entry_price, stop_price)
            elif hasattr(sizer, "calculate_position_size"):
                size = sizer.calculate_position_size(balance, entry_price, stop_price)
            else:
                return "warn", "PositionSizer has no known sizing method", [
                    "Expected calculate() or calculate_position_size()",
                ]
        except Exception as e:
            return "fail", f"sizer sizing raised: {e}", [traceback.format_exc()]

        if size is None or size <= 0:
            return "fail", f"Invalid size: {size}", []

        risk_usd = balance * 0.01
        return "pass", f"Size = {size:.6f} (approx ${risk_usd:.2f} risk)", [
            f"Balance=${balance}, Entry=${entry_price}, SL=${stop_price}",
        ]

    def _quality_warnings(self, result):
        """
        Adds contextual warnings for suspicious backtest outputs.
        """
        warnings = []

        trades = int(result.get("total_trades", 0) or 0)
        win_rate = float(result.get("win_rate", 0) or 0)
        profit_factor = float(result.get("profit_factor", 0) or 0)
        max_drawdown = float(result.get("max_drawdown", 0) or 0)
        expectancy = float(result.get("expectancy", 0) or 0)
        avg_r = float(result.get("avg_r_multiple", 0) or 0)

        if trades < 20:
            warnings.append("Too few trades for reliable validation")
        if win_rate > 95:
            warnings.append("Suspiciously high win rate — possible overfitting")
        if profit_factor > 8:
            warnings.append("Suspiciously high profit factor — possible overfitting")
        if expectancy == 0 and trades > 0:
            warnings.append("Expectancy is flat — edge may be weak")
        if avg_r > 5:
            warnings.append("Unusually high average R — verify fills and stops")
        if max_drawdown == 0 and trades > 0:
            warnings.append("Zero drawdown on a live-like sample — verify realism")

        return warnings

    def _check_backtester(self):
        from bot.backtesting.backtester import Backtester

        df = self.dummy_df

        try:
            bt = Backtester(df)
            result = bt.run()
        except Exception as e:
            return "fail", f"Backtester(df).run() raised: {e}", [traceback.format_exc()]

        if result is None:
            return "fail", "run() returned None", []

        required = ["total_trades", "win_rate", "profit_factor"]
        missing = [k for k in required if k not in result]
        if missing:
            return "warn", f"Missing result keys: {missing}", [
                f"Keys present: {list(result.keys())[:10]}",
            ]

        trades = result.get("total_trades", 0)
        warnings = self._quality_warnings(result)

        if trades < 3:
            warnings.append("Only a few trades on dummy data — expected with synthetic input")

        if warnings:
            return "warn", (
                f"Trades={trades}  WR={result.get('win_rate', 0):.1f}%  "
                f"PF={result.get('profit_factor', 0):.2f}"
            ), warnings

        return "pass", (
            f"Trades={trades}  "
            f"WR={result.get('win_rate', 0):.1f}%  "
            f"PF={result.get('profit_factor', 0):.2f}"
        ), []

    def _check_portfolio_backtester(self, limit):
        try:
            PortfolioBacktester = _safe_first_existing_attr(
                [
                    ("bot.backtesting.portfolio_backtester", "PortfolioBacktester"),
                ],
                label="PortfolioBacktester",
            )
        except ImportError:
            return "warn", "PortfolioBacktester not importable — skipping", [
                "Check bot.backtesting.portfolio_backtester",
            ]

        # Best-effort constructor probing.
        try:
            try:
                pb = PortfolioBacktester()
            except TypeError:
                if ccxt is None:
                    return "warn", "ccxt not installed — cannot fully test portfolio backtester", [
                        "Install ccxt or adjust constructor signature",
                    ]
                exchange = ccxt.binance({})
                try:
                    pb = PortfolioBacktester(
                        exchange=exchange,
                        candle_limit=min(limit, 200),
                        initial_balance=5000,
                    )
                except Exception:
                    pb = PortfolioBacktester(symbols=["BTCUSDT", "ETHUSDT"])
        except Exception as e:
            return "fail", f"PortfolioBacktester constructor failed: {e}", [traceback.format_exc()]

        try:
            result = pb.run()
        except Exception as e:
            return "warn", f"PortfolioBacktester.run() raised: {e}", [
                traceback.format_exc(),
                "Constructor may require different arguments in your project",
            ]

        if result is None:
            return "fail", "run() returned None", []

        return "pass", "Portfolio backtester ran OK", [
            f"Result type: {type(result).__name__}",
        ]

    def _check_trade_logger(self):
        try:
            TradeLogger = _safe_first_existing_attr(
                [
                    ("bot.journal.trade_logger", "TradeLogger"),
                    ("bot.journal.trade_logger", "Logger"),
                ],
                label="TradeLogger",
            )
        except ImportError:
            return "warn", "TradeLogger not importable — skipping", [
                "Check bot.journal.trade_logger",
            ]

        try:
            logger = TradeLogger()
        except Exception as e:
            return "fail", f"Cannot instantiate TradeLogger: {e}", [traceback.format_exc()]

        dummy_signal = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "side": "BUY",
            "entry": 50000.0,
            "sl": 49000.0,
            "tp": 52000.0,
            "atr": 1.2,
            "regime": "TRENDING_UP",
            "r_multiple": 2.0,
        }
        dummy_fill = {
            "fill_price": 50000.0,
            "qty": 0.001,
            "fee": 0.05,
        }

        try:
            if hasattr(logger, "log_entry"):
                logger.log_entry(dummy_signal, dummy_fill, risk_amount=10.0)
            elif hasattr(logger, "record_entry"):
                logger.record_entry(dummy_signal, dummy_fill, risk_amount=10.0)
            else:
                return "warn", "TradeLogger has no known log method", [
                    "Expected log_entry() or record_entry()",
                ]
        except Exception as e:
            return "fail", f"log_entry() raised: {e}", [
                traceback.format_exc(),
                "Check log_entry() expected parameters match your call",
            ]

        return "pass", "TradeLogger accepted dummy data", [
            "No DB write expected — just method acceptance tested",
        ]

    def _check_analytics(self):
        try:
            TradeAnalytics = _safe_first_existing_attr(
                [
                    ("bot.engines.analytics_engine", "TradeAnalytics"),
                    ("bot.analytics", "TradeAnalytics"),
                ],
                label="TradeAnalytics",
            )
        except ImportError:
            return "warn", "TradeAnalytics not importable — skipping", [
                "Check bot.engines.analytics_engine",
            ]

        dummy_results = {
            "trades": [
                {"profit": 100, "r_multiple": 2.0, "direction": "LONG", "regime": "TRENDING_UP"},
                {"profit": -50, "r_multiple": -1.0, "direction": "LONG", "regime": "TRENDING_UP"},
                {"profit": 200, "r_multiple": 3.0, "direction": "SHORT", "regime": "RANGING"},
                {"profit": -50, "r_multiple": -1.0, "direction": "LONG", "regime": "RANGING"},
                {"profit": 150, "r_multiple": 2.5, "direction": "LONG", "regime": "TRENDING_UP"},
            ]
        }

        try:
            analytics = TradeAnalytics()
            if hasattr(analytics, "record_trade"):
                for t in dummy_results["trades"]:
                    analytics.record_trade({
                        "pnl": t["profit"],
                        "profit": t["profit"],
                        "rsi": 50,
                        "direction": t["direction"],
                        "reason": "TEST",
                        "structure": "UNKNOWN",
                    })
            result = analytics.summary() if hasattr(analytics, "summary") else None
        except Exception as e:
            return "fail", f"TradeAnalytics failed: {e}", [traceback.format_exc()]

        if result is None:
            return "warn", "TradeAnalytics.summary() not available", [
                "Ensure summary() exists and returns a dict",
            ]

        score = result.get("total_score", result.get("score", "?"))
        return "pass", f"Analytics summary OK — score/key: {score}", [
            f"Result keys: {list(result.keys())[:8]}",
        ]

    def _check_trade_narrative(self):
        try:
            TradeNarrativeGenerator = _safe_first_existing_attr(
                [
                    ("bot.engines.trade_narrative", "TradeNarrativeGenerator"),
                ],
                label="TradeNarrativeGenerator",
            )
        except ImportError:
            return "warn", "TradeNarrativeGenerator not importable — skipping", [
                "Check bot.engines.trade_narrative",
            ]

        gen = TradeNarrativeGenerator()
        dummy_signal = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "side": "BUY",
            "regime": "TRENDING_UP",
            "adx": 28.0,
            "atr": 1.2,
            "rr": 2.1,
            "entry": 50000.0,
            "sl": 49000.0,
            "tp": 52000.0,
            "strategy": "MovingAverageStrategy",
        }

        try:
            narrative = gen.generate_entry(dummy_signal, "BTCUSDT")
        except Exception as e:
            return "fail", f"generate_entry() raised: {e}", [traceback.format_exc()]

        if narrative is None:
            return "fail", "generate_entry() returned None", []

        text = str(narrative)
        return "pass", f"Narrative generated ({len(text)} chars)", [
            text[:100] + ("..." if len(text) > 100 else ""),
        ]

    def _check_structure_analyser(self):
        # Support both class-based and function-based structure modules.
        analyser = None
        mode = None

        try:
            StructureAnalyser = _safe_first_existing_attr(
                [
                    ("bot.engines.structure_analyser", "StructureAnalyser"),
                    ("bot.engines.structure_analyzer", "StructureAnalyser"),
                ],
                label="StructureAnalyser",
            )
            analyser = StructureAnalyser()
            mode = "class"
        except Exception:
            try:
                analyse_structure_breakdown = _safe_first_existing_attr(
                    [
                        ("bot.backtesting.backtester", "analyze_structure_breakdown"),
                    ],
                    label="analyze_structure_breakdown",
                )
                analyser = analyse_structure_breakdown
                mode = "function"
            except Exception:
                return "warn", "Structure analyser not importable — skipping", [
                    "Check class/function name in your structure module",
                ]

        df = self.dummy_df
        try:
            if mode == "class":
                if hasattr(analyser, "get_market_structure"):
                    result = analyser.get_market_structure(df)
                elif hasattr(analyser, "analyze"):
                    result = analyser.analyze(df)
                else:
                    return "warn", "StructureAnalyser has no known method", [
                        "Expected get_market_structure() or analyze()",
                    ]
            else:
                result = analyser(df)
        except Exception as e:
            return "fail", f"Structure analyser raised: {e}", [traceback.format_exc()]

        if result is None:
            return "fail", "Structure analyser returned None", []

        label = getattr(result, "label", None) or getattr(result, "structure", None) or str(result)
        return "pass", f"Structure: {label}", [
            f"Tested with {len(df)} candles",
        ]

    def _check_strategy_scorer(self):
        try:
            StrategyScorer = _safe_first_existing_attr(
                [
                    ("bot.engines.strategy_scorer", "StrategyScorer"),
                ],
                label="StrategyScorer",
            )
        except ImportError:
            return "warn", "StrategyScorer not importable — skipping", [
                "Check bot.engines.strategy_scorer",
            ]

        dummy_results = {
            "trades": [
                {"profit": 100, "r_multiple": 2.0, "direction": "LONG"},
                {"profit": -50, "r_multiple": -1.0, "direction": "LONG"},
                {"profit": 300, "r_multiple": 3.0, "direction": "LONG"},
                {"profit": -50, "r_multiple": -1.0, "direction": "SHORT"},
                {"profit": 150, "r_multiple": 2.5, "direction": "LONG"},
            ],
            "total_trades": 5,
            "win_rate": 60.0,
            "profit_factor": 1.8,
            "total_profit": 450.0,
        }

        try:
            scorer = StrategyScorer(dummy_results)
            result = scorer.evaluate()
        except Exception as e:
            return "fail", f"evaluate() raised: {e}", [traceback.format_exc()]

        if result is None:
            return "fail", "evaluate() returned None", []

        score = result.get("total_score", result.get("score", None))
        if score is None:
            return "warn", "Score key not found in evaluate() result", [
                f"Keys returned: {list(result.keys())}",
            ]

        try:
            score_f = float(score)
        except Exception:
            return "warn", f"Score is not numeric: {score}", []

        if not (0 <= score_f <= 100):
            return "warn", f"Score {score} outside 0-100", []

        return "pass", f"Strategy score = {score}/100", []