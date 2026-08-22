# claude code changed: new file — Research Lab MVP, section 21 test
# requirements for the policy gate: valid request, oversized request,
# unsupported tool, high-risk request.

from django.test import SimpleTestCase

from bot.research_lab.data_availability import DataAvailabilityReport, DataRequirementCheck
from bot.research_lab.policy_gate import (
    evaluate_policy, is_tool_allowed, MAX_FEATURES_PER_EXPERIMENT,
    MAX_TARGET_HORIZON_CANDLES, MAX_CONCURRENT_EXPERIMENTS_PER_USER,
)
from bot.research_lab.spec import ResearchSpec


def _spec(**overrides):
    defaults = dict(
        hypothesis_text="test",
        asset="BTC/USDT",
        timeframe="1h",
        target={"type": "forward_return", "horizon": 24},
        risk_tier="LOW",
    )
    defaults.update(overrides)
    return ResearchSpec(**defaults)


def _available_report():
    return DataAvailabilityReport(all_available=True, checks=[DataRequirementCheck("ohlcv:BTC/USDT", True, "ok")])


def _unavailable_report():
    return DataAvailabilityReport(all_available=False, checks=[DataRequirementCheck("ohlcv:XYZ", False, "missing")])


class ValidRequestTest(SimpleTestCase):

    def test_low_risk_valid_request_is_allowed(self):
        decision = evaluate_policy(_spec(), _available_report())
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reasons, [])
        self.assertFalse(decision.requires_approval)

    def test_medium_risk_valid_request_requires_approval_but_is_allowed(self):
        decision = evaluate_policy(_spec(risk_tier="MEDIUM"), _available_report())
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.requires_approval)


class DataUnavailableTest(SimpleTestCase):

    def test_missing_data_blocks_the_request(self):
        decision = evaluate_policy(_spec(), _unavailable_report())
        self.assertFalse(decision.allowed)
        self.assertTrue(any("data not available" in r for r in decision.reasons))


class OversizedRequestTest(SimpleTestCase):

    def test_too_many_features_blocks_the_request(self):
        spec = _spec(features=[f"f{i}" for i in range(MAX_FEATURES_PER_EXPERIMENT + 1)])
        decision = evaluate_policy(spec, _available_report())
        self.assertFalse(decision.allowed)
        self.assertTrue(any("feature limit" in r for r in decision.reasons))

    def test_excessive_horizon_blocks_the_request(self):
        spec = _spec(target={"type": "forward_return", "horizon": MAX_TARGET_HORIZON_CANDLES + 1})
        decision = evaluate_policy(spec, _available_report())
        self.assertFalse(decision.allowed)
        self.assertTrue(any("horizon" in r for r in decision.reasons))

    def test_concurrency_limit_blocks_the_request(self):
        decision = evaluate_policy(_spec(), _available_report(), active_experiment_count=MAX_CONCURRENT_EXPERIMENTS_PER_USER)
        self.assertFalse(decision.allowed)
        self.assertTrue(any("concurrency limit" in r for r in decision.reasons))

    def test_multiple_violations_are_all_reported_together(self):
        spec = _spec(
            features=[f"f{i}" for i in range(MAX_FEATURES_PER_EXPERIMENT + 1)],
            target={"type": "forward_return", "horizon": MAX_TARGET_HORIZON_CANDLES + 1},
        )
        decision = evaluate_policy(spec, _unavailable_report())
        self.assertFalse(decision.allowed)
        self.assertGreaterEqual(len(decision.reasons), 3)  # claude code changed: data + features + horizon, not just the first failure


class HighRiskRequestTest(SimpleTestCase):

    def test_high_risk_tier_is_never_allowed(self):
        decision = evaluate_policy(_spec(risk_tier="HIGH"), _available_report())
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.allowed_tools, [])

    def test_high_risk_tier_blocks_even_with_perfect_data_and_small_scope(self):
        decision = evaluate_policy(_spec(risk_tier="HIGH", features=["rsi"]), _available_report())
        self.assertFalse(decision.allowed)


class UnsupportedToolTest(SimpleTestCase):

    def test_unlisted_tool_name_is_never_allowed_at_any_risk_tier(self):
        for tier in ("LOW", "MEDIUM", "HIGH"):
            self.assertFalse(is_tool_allowed("run_arbitrary_python", tier))  # claude code changed: not a real tool name, must never be reachable

    def test_low_tier_tool_allowed_at_low_risk(self):
        self.assertTrue(is_tool_allowed("run_statistical_test", "LOW"))

    def test_medium_tier_tool_not_allowed_at_low_risk(self):
        self.assertFalse(is_tool_allowed("run_backtest", "LOW"))

    def test_medium_tier_tool_allowed_at_medium_risk(self):
        self.assertTrue(is_tool_allowed("run_backtest", "MEDIUM"))

    def test_no_tool_allowed_at_high_risk(self):
        for tool in ("inspect_dataset", "run_statistical_test", "run_backtest"):
            self.assertFalse(is_tool_allowed(tool, "HIGH"))


class AllowedToolsListTest(SimpleTestCase):

    def test_low_risk_decision_only_lists_low_tier_tools(self):
        decision = evaluate_policy(_spec(risk_tier="LOW"), _available_report())
        self.assertIn("run_statistical_test", decision.allowed_tools)
        self.assertNotIn("run_backtest", decision.allowed_tools)  # claude code changed: MEDIUM-tier tool must not leak into a LOW-tier plan

    def test_medium_risk_decision_lists_both_tiers(self):
        decision = evaluate_policy(_spec(risk_tier="MEDIUM"), _available_report())
        self.assertIn("run_statistical_test", decision.allowed_tools)
        self.assertIn("run_backtest", decision.allowed_tools)
