# claude code changed: new file — Kraken Multi-Venue Execution, Step 12.
# Proves "do not create a second risk engine" is actually satisfied by
# Step 10's design: risk POLICY (risk_pct/max_risk_pct/max_drawdown_pct)
# is genuinely inherited from one config source across every venue's
# ExecutionEngine, while risk STATE (peak_balance/tripped, instance
# identity) is correctly independent per venue. Real ccxt instances,
# dry-run, no mocking, matching this project's convention.

import ccxt
from django.test import TestCase

from bot.config.risk import DEFAULT_RISK_PCT, MAX_DRAWDOWN_PCT, MAX_RISK_PCT
from bot.engines.binance_adapter import BinanceAdapter
from bot.engines.execution_engine import ExecutionEngine
from bot.engines.kraken_adapter import KrakenAdapter


def _make_engines():
    binance_engine = ExecutionEngine(ccxt.binance(), dry_run=True)
    kraken_engine = ExecutionEngine(
        ccxt.kraken(), dry_run=True, adapter=KrakenAdapter(ccxt.kraken(), dry_run=True)
    )
    return binance_engine, kraken_engine


class RiskPolicyInheritanceTest(TestCase):

    def test_risk_pct_matches_config_across_both_venues(self):
        binance_engine, kraken_engine = _make_engines()

        self.assertEqual(binance_engine.position_sizer.risk_pct, DEFAULT_RISK_PCT)
        self.assertEqual(kraken_engine.position_sizer.risk_pct, DEFAULT_RISK_PCT)
        self.assertEqual(binance_engine.position_sizer.risk_pct, kraken_engine.position_sizer.risk_pct)

    def test_max_risk_pct_matches_config_across_both_venues(self):
        binance_engine, kraken_engine = _make_engines()

        self.assertEqual(binance_engine.position_sizer.max_risk_pct, MAX_RISK_PCT)
        self.assertEqual(kraken_engine.position_sizer.max_risk_pct, MAX_RISK_PCT)

    def test_max_drawdown_pct_matches_config_across_both_venues(self):
        binance_engine, kraken_engine = _make_engines()

        self.assertEqual(binance_engine.drawdown_guard.max_drawdown_pct, MAX_DRAWDOWN_PCT)
        self.assertEqual(kraken_engine.drawdown_guard.max_drawdown_pct, MAX_DRAWDOWN_PCT)


class RiskStateIndependenceTest(TestCase):

    def test_position_sizer_and_drawdown_guard_are_separate_instances(self):
        binance_engine, kraken_engine = _make_engines()

        self.assertIsNot(binance_engine.position_sizer, kraken_engine.position_sizer)
        self.assertIsNot(binance_engine.drawdown_guard, kraken_engine.drawdown_guard)

    def test_one_venues_tripped_drawdown_guard_does_not_affect_the_other(self):
        binance_engine, kraken_engine = _make_engines()

        # Trip Binance's guard with a real drawdown sequence.
        binance_engine.drawdown_guard.update(1000.0)   # establishes peak
        binance_engine.drawdown_guard.update(800.0)    # 20% drawdown — trips

        self.assertTrue(binance_engine.drawdown_guard.tripped)
        self.assertEqual(binance_engine.drawdown_guard.peak_balance, 1000.0)

        # Kraken's guard must be completely unaffected.
        self.assertFalse(kraken_engine.drawdown_guard.tripped)
        self.assertEqual(kraken_engine.drawdown_guard.peak_balance, 0.0)

    def test_runtime_risk_pct_mutation_does_not_leak_across_venues(self):
        binance_engine, kraken_engine = _make_engines()

        binance_engine.position_sizer.set_risk_pct(0.02)

        self.assertEqual(binance_engine.position_sizer.risk_pct, 0.02)
        self.assertEqual(kraken_engine.position_sizer.risk_pct, DEFAULT_RISK_PCT)
