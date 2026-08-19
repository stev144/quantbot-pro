# claude code changed: new file — Phase 1 of the research-driven strategy
# architecture refactor. Verifies StrategyRouter actually blocks
# MeanReversionStrategy (RSI/BB) from firing production trades by default,
# per bot/research/validated_feature_registry.py.

import numpy as np
import pandas as pd
from django.test import SimpleTestCase   # claude code changed: was TestCase — these tests touch no models/DB, SimpleTestCase is the correct base and needs no test database

from bot.backtesting.backtester import Backtester   # claude code changed: new — Phase 3 (UI pipeline view)
from bot.engines.strategy_router import StrategyRouter
from bot.engines.regime_detector import RegimeResult
from bot.engines.trade_narrative import TradeNarrativeGenerator   # claude code changed: new — Phase 2 (trade provenance)
from bot.strategies.moving_average import MovingAverageStrategy
from bot.strategies.mean_reversion_strategy import MeanReversionStrategy
from bot.research.validated_feature_registry import get_strategy_verdict, UNTESTED
from bot.templatetags.research_extras import verdict_class   # claude code changed: new — Phase 3 (UI pipeline view)


def _make_ohlcv_df(n=100, seed=7):
    # claude code changed: new — same synthetic-OHLCV pattern
    # bot/core/dry_run_test.py already uses for strategy smoke tests
    np.random.seed(seed)
    returns = np.random.normal(0, 0.01, n)
    close = 100.0 * np.exp(np.cumsum(returns))
    high = close * (1 + np.abs(np.random.normal(0, 0.005, n)))
    low = close * (1 - np.abs(np.random.normal(0, 0.005, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = np.random.uniform(1e6, 5e6, n)
    df = pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    })
    df.index = pd.date_range("2024-01-01", periods=n, freq="1h")
    return df


def _fake_regime(regime="RANGING"):
    # claude code changed: new — mirrors dry_run_test.py::test_mean_reversion_strategy's
    # hand-built RegimeResult, so the regime classification itself is never in
    # question. ADX=30 so TRENDING_UP/DOWN clears StrategyRouter's default
    # min_adx_for_trend=25 gate and actually reaches MovingAverageStrategy —
    # a lower ADX would be blocked by that unrelated gate before this test's
    # gate is even reached.
    return RegimeResult(
        regime=regime, confidence="HIGH", adx=30.0, atr_ratio=1.0,
        ema_spread_pct=0.1, bb_width_pct=1.5, adx_trending=True,
        volatility_extreme=False, summary=f"{regime} | HIGH | test",
    )


class ProductionGatingTest(SimpleTestCase):

    def test_get_strategy_verdict_fails_closed(self):
        verdict = get_strategy_verdict("SomeMadeUpStrategyThatDoesNotExist")
        self.assertFalse(verdict.production_eligible)
        self.assertEqual(verdict.research_verdict, UNTESTED)

    def test_mean_reversion_verdict_is_not_production_eligible(self):
        verdict = get_strategy_verdict("MeanReversionStrategy")
        self.assertFalse(verdict.production_eligible)
        self.assertIn("rsi", verdict.rejection_reason.lower())
        self.assertIn("bb_width", verdict.rejection_reason.lower())

    def test_rsi_cannot_independently_create_a_production_signal(self):
        df = _make_ohlcv_df()
        regime_result = _fake_regime("RANGING")

        # The raw strategy is still fully callable and well-formed in
        # isolation — its own mechanics are untouched by this gate.
        raw_signal = MeanReversionStrategy().evaluate(df, regime_result)
        self.assertIsInstance(raw_signal, dict)
        self.assertIn(raw_signal["signal"], ("BUY", "SELL", "NO_SIGNAL"))

        # The router, at its safe default, must never let RANGING reach the
        # strategy at all — regardless of what it would have returned.
        router = StrategyRouter()
        routed_signal = router.route(df, regime_result)

        self.assertEqual(routed_signal["signal"], "NO_SIGNAL")
        self.assertTrue(
            routed_signal["reason"].startswith("strategy_not_validated_"),
            f"unexpected reason: {routed_signal['reason']}",
        )
        self.assertEqual(router.route_counts["RANGING"], 0)
        self.assertEqual(router.route_counts["BLOCKED_UNVALIDATED"], 1)

    def test_override_flag_restores_old_behavior_with_warning(self):
        df = _make_ohlcv_df()
        regime_result = _fake_regime("RANGING")

        router = StrategyRouter(allow_unvalidated_strategies=True)
        with self.assertLogs("bot.engines.strategy_router", level="WARNING") as cm:
            routed_signal = router.route(df, regime_result)

        self.assertTrue(
            any("allow_unvalidated_strategies=True" in msg for msg in cm.output),
            f"expected override warning, got: {cm.output}",
        )
        # Override actually reaches the strategy now — RANGING counter moves,
        # BLOCKED_UNVALIDATED does not.
        self.assertEqual(router.route_counts["RANGING"], 1)
        self.assertEqual(router.route_counts["BLOCKED_UNVALIDATED"], 0)
        self.assertIn(routed_signal["signal"], ("BUY", "SELL", "NO_SIGNAL"))

    # claude code changed: was test_trending_regimes_unaffected, which
    # asserted the exact bug this Phase A remediation fixes (forensic-audit
    # finding P0-1) — that TRENDING_UP/DOWN were "unaffected by the RANGING-
    # only gate". That was true and was the problem: MovingAverageStrategy
    # has no more research evidence than MeanReversionStrategy did, and the
    # router dispatched to it anyway. Replaced with tests asserting the
    # opposite — TRENDING_UP/DOWN must be blocked by the same gate, by
    # default, until MovingAverageStrategy earns a real verdict.
    def test_trending_up_blocked_by_default(self):
        df = _make_ohlcv_df()
        regime_result = _fake_regime("TRENDING_UP")

        raw_signal = MovingAverageStrategy().check_signal(df)
        self.assertIsInstance(raw_signal, dict)  # raw strategy mechanics untouched

        router = StrategyRouter()
        routed_signal = router.route(df, regime_result)

        self.assertEqual(routed_signal["signal"], "NO_SIGNAL")
        self.assertTrue(
            routed_signal["reason"].startswith("strategy_not_validated_"),
            f"unexpected reason: {routed_signal['reason']}",
        )
        self.assertEqual(router.route_counts["TRENDING_UP"], 0)
        self.assertEqual(router.route_counts["BLOCKED_UNVALIDATED"], 1)

    def test_trending_down_blocked_by_default(self):
        df = _make_ohlcv_df()
        regime_result = _fake_regime("TRENDING_DOWN")

        router = StrategyRouter()
        routed_signal = router.route(df, regime_result)

        self.assertEqual(routed_signal["signal"], "NO_SIGNAL")
        self.assertTrue(
            routed_signal["reason"].startswith("strategy_not_validated_"),
            f"unexpected reason: {routed_signal['reason']}",
        )
        self.assertEqual(router.route_counts["TRENDING_DOWN"], 0)
        self.assertEqual(router.route_counts["BLOCKED_UNVALIDATED"], 1)

    def test_moving_average_verdict_is_untested_not_production_eligible(self):
        verdict = get_strategy_verdict("MovingAverageStrategy")
        self.assertFalse(verdict.production_eligible)
        self.assertEqual(verdict.research_verdict, UNTESTED)

    def test_trending_override_flag_restores_old_behavior_with_warning(self):
        df = _make_ohlcv_df()
        regime_result = _fake_regime("TRENDING_UP")

        router = StrategyRouter(allow_unvalidated_strategies=True)
        with self.assertLogs("bot.engines.strategy_router", level="WARNING") as cm:
            routed_signal = router.route(df, regime_result)

        self.assertTrue(
            any("allow_unvalidated_strategies=True" in msg for msg in cm.output),
            f"expected override warning, got: {cm.output}",
        )
        self.assertEqual(router.route_counts["TRENDING_UP"], 1)
        self.assertEqual(router.route_counts["BLOCKED_UNVALIDATED"], 0)
        self.assertIn(routed_signal["signal"], ("BUY", "SELL", "NO_SIGNAL"))

    def test_unknown_strategy_name_fails_closed_through_router_gate(self):
        # claude code changed: new — proves the router's shared gate
        # doesn't special-case known strategy names; an unrecognized name
        # would fail closed exactly like a real, evidenced REJECTED one.
        # Exercised indirectly: _check_production_eligibility() calls
        # get_strategy_verdict(), which is already proven fail-closed for
        # unknown names by test_get_strategy_verdict_fails_closed above —
        # this test instead proves route() itself never bypasses that call
        # for either strategy it can dispatch to.
        router = StrategyRouter()
        for regime in ("TRENDING_UP", "TRENDING_DOWN", "RANGING"):
            signal = router.route(_make_ohlcv_df(), _fake_regime(regime))
            self.assertEqual(signal["signal"], "NO_SIGNAL")
            self.assertTrue(signal["reason"].startswith("strategy_not_validated_"))
        self.assertEqual(router.route_counts["BLOCKED_UNVALIDATED"], 3)


# claude code changed: new class — Phase 2 (trade provenance). Verifies
# research-verdict info actually reaches both the router's blocked-signal
# dicts and TradeNarrativeGenerator's approved-trade narratives, not just
# that the underlying registry lookup works (already covered above).
class TradeProvenanceTest(SimpleTestCase):

    def test_blocked_signal_carries_matching_verdict_fields(self):
        df = _make_ohlcv_df()
        regime_result = _fake_regime("RANGING")
        expected = get_strategy_verdict("MeanReversionStrategy")

        router = StrategyRouter()
        routed_signal = router.route(df, regime_result)

        self.assertEqual(routed_signal["research_verdict"], expected.research_verdict)
        self.assertEqual(routed_signal["production_eligible"], expected.production_eligible)
        self.assertEqual(routed_signal["verdict_rejection_reason"], expected.rejection_reason)

    def test_unrelated_block_carries_no_fabricated_verdict(self):
        # high_volatility_blocked isn't decided by any strategy verdict —
        # the dict must not claim one.
        router = StrategyRouter()
        regime_result = RegimeResult(
            regime="HIGH_VOLATILITY", confidence="HIGH", adx=15.0, atr_ratio=5.0,
            ema_spread_pct=0.1, bb_width_pct=1.5, adx_trending=False,
            volatility_extreme=True, summary="HIGH_VOLATILITY | HIGH | test",
        )
        routed_signal = router.route(_make_ohlcv_df(), regime_result)

        self.assertEqual(routed_signal["signal"], "NO_SIGNAL")
        self.assertEqual(routed_signal["reason"], "high_volatility_blocked")
        self.assertEqual(routed_signal["research_verdict"], "")
        self.assertFalse(routed_signal["production_eligible"])
        self.assertEqual(routed_signal["verdict_rejection_reason"], "")

    def test_generate_entry_attaches_verdict_for_moving_average_strategy(self):
        # claude code changed: comment updated — MovingAverageStrategy now
        # has an explicit UNTESTED entry in STRATEGY_VERDICTS (Phase A of
        # the remediation program), and the router gates TRENDING regimes
        # on it too (see test_trending_up_blocked_by_default above). This
        # test now documents the narrative side of the same fact: its
        # structure-based logic has never been through
        # permutation/walk-forward testing any more than RSI/BB had, so the
        # narrative correctly says UNTESTED/not-eligible for it.
        signal = {
            "signal": "BUY", "entry": 100.0, "sl": 95.0, "tp": 115.0, "rsi": 55.0,
            "reason": "ema_buy_setup", "strategy": "MovingAverageStrategy",
            "regime": "TRENDING_UP", "regime_confidence": "HIGH",
            "adx": 30.0, "atr_ratio": 1.0, "ema_spread_pct": 0.5, "bb_width_pct": 1.5,
        }
        narrative = TradeNarrativeGenerator().generate_entry(signal, "BTC/USDT")

        self.assertEqual(narrative.research_verdict, UNTESTED)
        self.assertFalse(narrative.production_eligible)

        as_dict = TradeNarrativeGenerator().to_dict(narrative)
        self.assertEqual(as_dict["research_verdict"], UNTESTED)
        self.assertFalse(as_dict["production_eligible"])


# claude code changed: new class — Phase 3 (UI pipeline view). Verifies the
# verdict-class color mapping and the backtester's rejection_table()
# enrichment that the new dashboard columns depend on.
class UIPipelineTest(SimpleTestCase):

    def test_verdict_class_covers_the_registry_taxonomy(self):
        self.assertEqual(verdict_class("SUPPORTED"), "pos")
        self.assertEqual(verdict_class("REJECTED"), "neg")
        self.assertEqual(verdict_class("UNTESTED"), "dim")
        self.assertEqual(verdict_class("CONDITIONAL"), "neu")
        self.assertEqual(verdict_class("WEAK"), "warn")

    def test_rejection_table_carries_verdict_only_where_applicable(self):
        import pandas as pd

        df = pd.read_csv("data/BTC_USDT_1h.csv")
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df.set_index("timestamp", inplace=True)

        bt = Backtester(df=df.tail(2000))
        bt.run()
        table = bt.rejection_table()

        self.assertIn("strategy_not_validated_rejected", table)
        self.assertEqual(table["strategy_not_validated_rejected"]["research_verdict"], "REJECTED")
        self.assertFalse(table["strategy_not_validated_rejected"]["production_eligible"])

        # claude code changed: Phase A now also gates TRENDING_UP/DOWN, so a
        # 2000-candle window can legitimately produce a second
        # verdict-carrying reason — "strategy_not_validated_untested" for
        # MovingAverageStrategy — alongside the RANGING one. Both are
        # verdict-carrying reasons now, not just the RANGING one.
        verdict_carrying_reasons = {
            "strategy_not_validated_rejected",
            "strategy_not_validated_untested",
        }
        if "strategy_not_validated_untested" in table and table["strategy_not_validated_untested"]["count"] > 0:
            self.assertEqual(table["strategy_not_validated_untested"]["research_verdict"], "UNTESTED")
            self.assertFalse(table["strategy_not_validated_untested"]["production_eligible"])

        # A reason unrelated to either strategy verdict must not carry a
        # fabricated one.
        for reason, row in table.items():
            if reason not in verdict_carrying_reasons and row["count"] > 0:
                self.assertEqual(row.get("research_verdict", ""), "")
                self.assertFalse(row.get("production_eligible", False))


# claude code changed: new class — Phase A of the controlled remediation
# program. signal_engine.py is unused by the live/backtest pipeline (see its
# own header comment) but calls MovingAverageStrategy directly, so it needed
# the same gate closed for defense in depth — this proves it fires.
class SignalEngineGatingTest(SimpleTestCase):

    def test_generate_signal_blocked_by_default(self):
        from bot.engines.signal_engine import generate_signal

        df = _make_ohlcv_df(n=200)
        result = generate_signal(df, 100)

        self.assertEqual(result["signal"], "NO_SIGNAL")
        self.assertTrue(result["reason"].startswith("strategy_not_validated_"))
        self.assertEqual(result["research_verdict"], "UNTESTED")
        self.assertFalse(result["production_eligible"])

    def test_generate_signal_override_flag_bypasses_gate(self):
        from bot.engines.signal_engine import generate_signal

        df = _make_ohlcv_df(n=200)
        result = generate_signal(df, 100, allow_unvalidated_strategies=True)

        # No longer forced to NO_SIGNAL by the gate — whatever the raw
        # strategy/filter chain decides is allowed through.
        self.assertIn(result["signal"], ("BUY", "SELL", "NO_SIGNAL"))
        if result["signal"] == "NO_SIGNAL":
            self.assertNotEqual(result.get("reason", ""), "strategy_not_validated_untested")
