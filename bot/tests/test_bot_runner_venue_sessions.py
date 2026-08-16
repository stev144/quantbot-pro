# claude code changed: new file — wiring Kraken into bot_runner.py.
# Tests process_venue_candle() directly — the standalone, testable
# function extracted from run_bot()'s main loop body — proving per-venue
# isolation (one venue's failure never affects another's state in the
# same pass) without needing to run the infinite live loop itself. Real
# ccxt instances, dry-run, no mocking, matching this project's convention.

import importlib.util
import os

import ccxt
from django.test import TestCase

# claude code changed: bot_runner.py isn't a normal importable package
# module (it lives under bot/core/ and does sys.path/Django setup as a
# side effect of being imported) — loaded the same way dry_run_test.py's
# own import pattern already handles this, via importlib from its file
# path, so this test doesn't need a second sys.path/django.setup() dance.
_BOT_RUNNER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "bot", "core", "bot_runner.py",
)
_spec = importlib.util.spec_from_file_location("bot_runner_under_test", _BOT_RUNNER_PATH)
bot_runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bot_runner)

from bot.engines.binance_adapter import BinanceAdapter
from bot.engines.execution_engine import ExecutionEngine
from bot.engines.kraken_adapter import KrakenAdapter
from bot.engines.regime_detector import RegimeDetector
from bot.engines.strategy_router import StrategyRouter
from bot.engines.trade_narrative import TradeNarrativeGenerator


def _make_session(venue_id, exchange, adapter):
    engine = ExecutionEngine(exchange, dry_run=True, adapter=adapter)
    engine.position_tracker.is_reconciled = True
    return bot_runner.VenueSession(
        venue_id=venue_id,
        exchange=exchange,
        symbol="XRP/USDT",
        engine=engine,
        detector=RegimeDetector(),
        router=StrategyRouter(
            allow_longs=True, allow_shorts=True,
            require_high_confidence=False, min_adx_for_trend=20,
        ),
        narrator=TradeNarrativeGenerator(),
    )


class ProcessVenueCandleRealDataTest(TestCase):

    def test_binance_session_processes_successfully(self):
        exchange = ccxt.binance()
        session = _make_session("binance", exchange, BinanceAdapter(exchange, dry_run=True))

        status = bot_runner.process_venue_candle(session)

        self.assertEqual(status, "ok")
        self.assertNotEqual(session.last_regime, "UNKNOWN")
        self.assertIn(session.last_signal, ("BUY", "SELL", "NO_SIGNAL"))

    def test_kraken_session_processes_independently(self):
        exchange = ccxt.kraken()
        session = _make_session("kraken", exchange, KrakenAdapter(exchange, dry_run=True))

        status = bot_runner.process_venue_candle(session)

        self.assertEqual(status, "ok")
        self.assertNotEqual(session.last_regime, "UNKNOWN")


class ProcessVenueCandleIsolationTest(TestCase):
    """Proves one venue's failure never affects another's in the same pass."""

    def test_broken_exchange_returns_network_error_without_raising(self):
        class BrokenExchange:
            def fetch_ohlcv(self, symbol, timeframe, limit):
                raise ccxt.NetworkError("simulated connectivity failure")

        broken = BrokenExchange()
        engine = ExecutionEngine(exchange=None, dry_run=True)
        engine.position_tracker.is_reconciled = True
        session = bot_runner.VenueSession(
            venue_id="kraken", exchange=broken, symbol="XRP/USDT",
            engine=engine, detector=RegimeDetector(),
            router=StrategyRouter(), narrator=TradeNarrativeGenerator(),
        )

        status = bot_runner.process_venue_candle(session)

        self.assertEqual(status, "network_error")
        self.assertEqual(session.last_regime, "UNKNOWN")   # never reached regime detection

    def test_one_broken_session_does_not_affect_a_healthy_ones_state(self):
        class BrokenExchange:
            def fetch_ohlcv(self, symbol, timeframe, limit):
                raise ccxt.NetworkError("simulated connectivity failure")

        broken_engine = ExecutionEngine(exchange=None, dry_run=True)
        broken_engine.position_tracker.is_reconciled = True
        broken_session = bot_runner.VenueSession(
            venue_id="kraken", exchange=BrokenExchange(), symbol="XRP/USDT",
            engine=broken_engine, detector=RegimeDetector(),
            router=StrategyRouter(), narrator=TradeNarrativeGenerator(),
        )

        binance_exchange = ccxt.binance()
        healthy_session = _make_session("binance", binance_exchange, BinanceAdapter(binance_exchange, dry_run=True))

        broken_status = bot_runner.process_venue_candle(broken_session)
        healthy_status = bot_runner.process_venue_candle(healthy_session)

        self.assertEqual(broken_status, "network_error")
        self.assertEqual(healthy_status, "ok")
        self.assertNotEqual(healthy_session.last_regime, "UNKNOWN")


class CheckComponentsOptionalPriceCheckerTest(TestCase):

    def test_price_checker_omitted_still_passes(self):
        exchange = ccxt.kraken()
        session = _make_session("kraken", exchange, KrakenAdapter(exchange, dry_run=True))

        ok = bot_runner.check_components(
            session.engine, session.detector, session.router, session.narrator,
        )

        self.assertTrue(ok)
