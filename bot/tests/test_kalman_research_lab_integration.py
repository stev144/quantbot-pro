# claude code changed: new file — Phase 1D (Kalman Research Integration).
# Covers the NEW integration surface added this phase:
#   - KalmanFilterEngine's explicit-seed constructor bypass (symbol_a/
#     symbol_b/ols_beta/ols_alpha), which lets a caller skip
#     cointegration_pairs.csv entirely.
#   - KalmanFilterEngine.run_on_prices() — the pure computation entry
#     point a Research Lab tool call uses.
#   - bot.research_lab.tools.research_tools.run_kalman_pairs_test — the
#     Research Lab tool itself.
# Does NOT re-test the core Kalman recursion/leakage-free spread/
# winsorisation/forward-return math — that's already covered by
# bot/tests/test_kalman_filter_engine.py and is unchanged by this phase.
# Uses deterministic synthetic data throughout (Phase 1D, Objective 14) —
# no real data/*.csv fixtures required.

from unittest.mock import patch

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.instruments import ASSET_CLASS_CRYPTO, Instrument
from bot.research.kalman_filter_engine import KalmanFilterEngine


def _synthetic_pair(n=600, true_beta=1.3, true_alpha=0.2, seed=7, freq="1h"):
    """A log-price pair with a KNOWN linear relationship — real cointegration
    by construction, not fetched data. Returns (price_a, price_b) as raw
    (non-log) close-price Series, matching what load_ohlcv()/_load_prices()
    would hand the engine."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n, freq=freq)
    log_b = pd.Series(np.cumsum(rng.normal(0, 0.01, n)) + 4.0, index=idx)
    log_a = true_alpha + true_beta * log_b + rng.normal(0, 0.001, n)
    return np.exp(log_a), np.exp(log_b)   # claude code changed: back to raw price space — run_on_prices()/_load_prices() both take raw close prices, not logs


class ExplicitSeedBypassTest(SimpleTestCase):
    """Phase 1D, Objective 8: a Research Lab tool call must be able to
    construct KalmanFilterEngine for an arbitrary pair without a
    pre-existing cointegration_pairs.csv row for it."""

    def test_explicit_seed_skips_cointegration_pairs_csv_entirely(self):
        # claude code changed: no cointegration_pairs_csv path given, and
        # none exists on disk at the default path relative to this test's
        # cwd — if this succeeds, the explicit-seed path truly bypassed
        # load_pair_config()'s file read.
        engine = KalmanFilterEngine(
            symbol_a="BTC/USDT", symbol_b="ETH/USDT",
            ols_beta=1.3, ols_alpha=0.2,
        )
        self.assertEqual(engine.symbol_a, "BTC/USDT")
        self.assertEqual(engine.symbol_b, "ETH/USDT")
        self.assertEqual(engine.pair_name, "BTC/USDT/ETH/USDT")  # claude code changed: derived from explicit symbols, not the stale AVAX/ATOM default
        self.assertEqual(engine.ols_beta, 1.3)
        self.assertEqual(engine.ols_alpha, 0.2)

    def test_pair_name_not_silently_left_at_avax_atom_default(self):
        # claude code changed: regression test for the identity-mismatch bug
        # caught while building this path — self.pair_name must never say
        # AVAX/ATOM while self.symbol_a/self.symbol_b say something else.
        engine = KalmanFilterEngine(symbol_a="AAPL", symbol_b="MSFT", ols_beta=1.0, ols_alpha=0.0)
        self.assertNotIn("AVAX", engine.pair_name)
        self.assertNotIn("ATOM", engine.pair_name)
        self.assertEqual(engine.pair_name, "AAPL/MSFT")

    def test_explicit_pair_name_override_is_respected(self):
        engine = KalmanFilterEngine(
            pair_name="Custom Label", symbol_a="EUR/USD", symbol_b="GBP/USD",
            ols_beta=1.0, ols_alpha=0.0,
        )
        self.assertEqual(engine.pair_name, "Custom Label")

    def test_partial_explicit_seed_raises_value_error(self):
        # claude code changed: only symbol_a given — must fail loud, never
        # silently mix an explicit value with a stale AVAX/ATOM default for
        # the missing fields.
        with self.assertRaises(ValueError):
            KalmanFilterEngine(symbol_a="BTC/USDT")

    def test_half_life_h_defaults_honestly_to_nan_when_omitted(self):
        engine = KalmanFilterEngine(symbol_a="BTC/USDT", symbol_b="ETH/USDT", ols_beta=1.0, ols_alpha=0.0)
        self.assertTrue(np.isnan(engine.half_life_h))  # claude code changed: "not supplied," never silently substituted


class RunOnPricesTest(SimpleTestCase):
    """KalmanFilterEngine.run_on_prices() — the pure computation entry
    point, exercised on a non-AVAX/ATOM pair with a known relationship."""

    def test_runs_on_arbitrary_pair_and_recovers_known_beta(self):
        price_a, price_b = _synthetic_pair(n=600, true_beta=1.3, true_alpha=0.2)
        engine = KalmanFilterEngine(
            symbol_a="X/USDT", symbol_b="Y/USDT",
            ols_beta=1.3, ols_alpha=0.2,   # claude code changed: seeded with the TRUE relationship, as a real cointegration test would produce
            process_noise_beta=1e-4, process_noise_alpha=1e-4,
            warmup_candles=100,
        )
        result = engine.run_on_prices(price_a, price_b)

        self.assertIn("kalman_beta_pred", result.columns)
        self.assertIn("kalman_zscore", result.columns)
        post_warmup = result.loc[~result["is_warmup"]]
        self.assertGreater(len(post_warmup), 0)
        self.assertAlmostEqual(post_warmup["kalman_beta_pred"].mean(), 1.3, delta=0.1)  # claude code changed: converges near the known true beta

    def test_does_not_write_any_output_files(self):
        # claude code changed: run_on_prices() must never touch disk — a
        # Research Lab tool call has no research_data/ output-file contract.
        import os
        before = set(os.listdir(".")) if os.path.isdir(".") else set()
        price_a, price_b = _synthetic_pair(n=600)  # claude code changed: >= warmup(50)+500 minimum _align_prices() enforces
        engine = KalmanFilterEngine(symbol_a="X/USDT", symbol_b="Y/USDT", ols_beta=1.3, ols_alpha=0.2, warmup_candles=50)
        engine.run_on_prices(price_a, price_b)
        after = set(os.listdir(".")) if os.path.isdir(".") else set()
        self.assertEqual(before, after)


class RunKalmanPairsTestToolTest(SimpleTestCase):
    """bot.research_lab.tools.research_tools.run_kalman_pairs_test — the
    Research Lab tool itself. load_ohlcv() is patched at its point of use
    so this needs no real data/*.csv fixture (Phase 1D, Objective 14)."""

    def _patched_load_ohlcv(self, price_a, price_b, timeframe_a="1h", timeframe_b="1h"):
        instrument_a = Instrument(canonical_symbol="X/USDT", asset_class=ASSET_CLASS_CRYPTO, timeframe=timeframe_a)
        instrument_b = Instrument(canonical_symbol="Y/USDT", asset_class=ASSET_CLASS_CRYPTO, timeframe=timeframe_b)
        df_a = pd.DataFrame({"close": price_a})
        df_a.attrs["instrument"] = instrument_a
        df_b = pd.DataFrame({"close": price_b})
        df_b.attrs["instrument"] = instrument_b

        def _fake_load_ohlcv(asset):
            return df_a if asset == "X/USDT" else df_b
        return _fake_load_ohlcv

    def test_cointegrated_synthetic_pair_returns_observation_evidence_and_verdict(self):
        from bot.research_lab.tools.research_tools import run_kalman_pairs_test
        price_a, price_b = _synthetic_pair(n=700, true_beta=1.3, true_alpha=0.2)  # claude code changed: >= default warmup(168)+500 minimum

        with patch(
            "bot.research_lab.tools.research_tools.load_ohlcv",
            side_effect=self._patched_load_ohlcv(price_a, price_b),
        ):
            result = run_kalman_pairs_test("X/USDT", "Y/USDT")

        self.assertIn("observation", result)
        self.assertIn("statistical_evidence", result)
        self.assertIn("verdict", result)
        self.assertIn(result["verdict"], ("SUPPORTED", "REJECTED", "INCONCLUSIVE"))  # claude code changed: deterministic function of the evidence, never free text
        self.assertIsNotNone(result["observation"])
        self.assertIn("dynamic_hedge_ratio", result["observation"])
        self.assertIn("config", result)
        self.assertEqual(result["config"]["process_noise_beta"], result["config"]["process_noise_beta"])  # sanity: present and self-consistent

    def test_timeframe_mismatch_returns_inconclusive_not_a_crash(self):
        from bot.research_lab.tools.research_tools import run_kalman_pairs_test
        price_a, price_b = _synthetic_pair(n=300)

        with patch(
            "bot.research_lab.tools.research_tools.load_ohlcv",
            side_effect=self._patched_load_ohlcv(price_a, price_b, timeframe_a="1h", timeframe_b="4h"),
        ):
            result = run_kalman_pairs_test("X/USDT", "Y/USDT")

        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertIn("timeframe mismatch", result["reject_reason"])
        self.assertIsNone(result["observation"])

    def test_identical_inputs_produce_identical_output_deterministic(self):
        # claude code changed: Phase 1D, Objective 13 (reproducibility) —
        # the Kalman recursion and the OLS seed are both fully deterministic
        # (no RNG anywhere in this path), so two calls on the same inputs
        # must produce byte-identical numeric results.
        from bot.research_lab.tools.research_tools import run_kalman_pairs_test
        price_a, price_b = _synthetic_pair(n=700, true_beta=1.1, true_alpha=0.1, seed=3)  # claude code changed: >= default warmup(168)+500 minimum

        with patch(
            "bot.research_lab.tools.research_tools.load_ohlcv",
            side_effect=self._patched_load_ohlcv(price_a, price_b),
        ):
            result_1 = run_kalman_pairs_test("X/USDT", "Y/USDT")
            result_2 = run_kalman_pairs_test("X/USDT", "Y/USDT")

        self.assertEqual(result_1["observation"], result_2["observation"])
        self.assertEqual(result_1["statistical_evidence"], result_2["statistical_evidence"])
        self.assertEqual(result_1["verdict"], result_2["verdict"])

    def test_no_execution_or_credential_path(self):
        # claude code changed: Phase 1D, Objective 16 — direct check on
        # this new tool function specifically (broader research_lab-wide
        # coverage already exists in test_research_lab_orchestrator.py's
        # SecurityNoPathToLiveExecutionTest, which scans this same module).
        import ast
        import inspect

        from bot.research_lab.tools import research_tools
        source = inspect.getsource(research_tools)
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        for module_name in imported:
            for forbidden in ("execution_engine", "order_manager", "bot_runner", "ccxt"):
                self.assertNotIn(forbidden, module_name)
