# claude code changed: new file — Research Lab MVP, section 21 test
# requirements for research tools: valid parameters, invalid parameters,
# deterministic output, failure handling. Uses BTC/USDT's real, committed
# data/BTC_USDT_1h.csv — no mocking, per this project's hard convention.

from django.test import SimpleTestCase

from bot.research_lab.tools import run_tool


class InspectDatasetTest(SimpleTestCase):

    def test_valid_asset_returns_real_dataset_shape(self):
        result = run_tool("inspect_dataset", "LOW", asset="BTC/USDT")
        self.assertEqual(result.status, "success")
        self.assertGreater(result.output["n_observations"], 0)

    def test_unknown_asset_fails_closed_not_crashes(self):
        result = run_tool("inspect_dataset", "LOW", asset="NOT_A_REAL_SYMBOL/USDT")
        self.assertEqual(result.status, "error")
        self.assertIn("no OHLCV file", result.error)


class CalculateFeatureTest(SimpleTestCase):

    def test_valid_feature_returns_real_stats(self):
        result = run_tool("calculate_feature", "LOW", asset="BTC/USDT", feature_name="rsi")
        self.assertEqual(result.status, "success")
        self.assertIsNotNone(result.output["mean"])
        self.assertGreater(result.output["n_valid"], 0)

    def test_unrecognized_feature_fails_closed(self):
        result = run_tool("calculate_feature", "LOW", asset="BTC/USDT", feature_name="made_up_xyz")
        self.assertEqual(result.status, "error")


class StatisticalTestTest(SimpleTestCase):

    def test_deterministic_output_same_seed_same_result(self):
        r1 = run_tool("run_statistical_test", "LOW", asset="BTC/USDT", feature_name="rsi", horizon=4, random_seed=42)
        r2 = run_tool("run_statistical_test", "LOW", asset="BTC/USDT", feature_name="rsi", horizon=4, random_seed=42)
        self.assertEqual(r1.status, "success")
        self.assertEqual(r1.output["ic"], r2.output["ic"])  # claude code changed: same seed must reproduce identically, section 9/16
        self.assertEqual(r1.output["block_permutation_p_value"], r2.output["block_permutation_p_value"])

    def test_unsupported_horizon_fails_closed(self):
        result = run_tool("run_statistical_test", "LOW", asset="BTC/USDT", feature_name="rsi", horizon=7)
        self.assertEqual(result.status, "error")
        self.assertIn("not supported", result.error)


class FdrCorrectionTest(SimpleTestCase):

    def test_valid_request_returns_corrected_and_raw_p_values(self):
        result = run_tool("run_fdr_correction", "LOW", asset="BTC/USDT", feature_name="rsi", horizon=4)
        self.assertEqual(result.status, "success")
        self.assertIn("fdr_adjusted_p_value", result.output)
        self.assertIn("raw_p_value", result.output)


class CointegrationTestTest(SimpleTestCase):

    def test_valid_pair_returns_real_engle_granger_result(self):
        result = run_tool("run_cointegration_test", "MEDIUM", asset_a="AVAX/USDT", asset_b="ATOM/USDT")
        self.assertEqual(result.status, "success")
        self.assertIn("adf_pvalue", result.output)
        self.assertIn("hedge_ratio", result.output)


class BacktestTest(SimpleTestCase):

    def test_valid_asset_returns_real_backtest_metrics(self):
        result = run_tool("run_backtest", "MEDIUM", asset="BTC/USDT")
        self.assertEqual(result.status, "success")
        self.assertIn("win_rate", result.output)
        self.assertIn("sharpe_ratio", result.output)


class RiskTierEnforcementTest(SimpleTestCase):

    def test_medium_tier_tool_blocked_at_low_risk_tier(self):
        result = run_tool("run_backtest", "LOW", asset="BTC/USDT")
        self.assertEqual(result.status, "error")
        self.assertIn("not an allowed tool", result.error)

    def test_no_tool_reachable_at_high_risk_tier(self):
        result = run_tool("inspect_dataset", "HIGH", asset="BTC/USDT")
        self.assertEqual(result.status, "error")

    def test_unregistered_tool_name_fails_closed(self):
        result = run_tool("run_arbitrary_python", "LOW")
        self.assertEqual(result.status, "error")
