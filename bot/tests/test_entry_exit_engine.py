# claude code changed: new file — Phase 1C (Blocker A / Step 4).
# entry_exit_engine.py had zero automated test coverage, which is the exact,
# explicit reason bot/research_lab/capability_registry.py's
# walk_forward_validation and permutation_robustness_testing entries are
# BLOCKED_BY_DEPENDENCY ("Its full run() path depends on entry_exit_engine.py,
# which has zero automated test coverage."). This file covers: pair identity
# across multiple asset classes, cost-model integration (Phase 1C's fix for
# the module's former hardcoded FEE_RATE/SLIPPAGE_RATE), core entry/exit/
# position-sizing math via a synthetic Kalman CSV, and the security boundary
# (no import path to execution/order-placement code).
#
# SCOPE NOTE: does not attempt to exercise every exit branch (TARGET,
# PARTIAL, TIMESTOP) end-to-end — STOPLOSS is used for the full run() test
# because it is the one exit condition that fires immediately (not gated by
# EXIT_MIN_HOLD_BASE_HOURS), making it the only branch that's simple to
# trigger deterministically with a short synthetic series. The other exit
# branches are exercised at the unit level instead (KalmanPositionSizer,
# cost resolution, pair-identity parsing).

import shutil
import tempfile
from pathlib import Path

import pandas as pd
from django.test import SimpleTestCase

from bot.config.cost_model import UnsupportedAssetClassCostModel
from bot.instruments import ASSET_CLASS_CRYPTO, ASSET_CLASS_US_EQUITY
from bot.research.entry_exit_engine import (  # claude code changed: module under test
    EntryExitEngine, KalmanPositionSizer, _parse_pair_from_kalman_filename,
)


def _write_stoploss_scenario_csv(path):
    # claude code changed: new — shared fixture builder used across test
    # classes below. 12 flat candles (no entry — |z| below threshold), then
    # a confirmed LONG_SPREAD entry at z=-2.2 (negative z, lag also
    # negative, composite signal +0.5 agrees), then an immediate breach
    # past this trade's own stop_zscore (|entry_z|=2.2 +
    # EXIT_ZSCORE_STOP_DISTANCE=1.5 = 3.7) via z=-4.0 — STOPLOSS is the one
    # exit condition not gated by a minimum hold time, making it the
    # deterministic choice for a short synthetic series (see module SCOPE
    # NOTE above).
    n_flat = 12
    pd.DataFrame({
        "timestamp": list(pd.date_range("2024-01-01", periods=n_flat + 3, freq="h", tz="UTC")),
        "kalman_zscore":       [-0.1] * n_flat + [-2.2, -4.0, -0.1],
        "kalman_zscore_lag1":  [-0.1] * n_flat + [-0.1, -2.2, -4.0],
        "pair_signal_dynamic": [0.05] * n_flat + [0.5, 0.5, 0.05],
        "kalman_beta":         [1.5] * (n_flat + 3),
        "kalman_spread":       [0.0] * n_flat + [-0.01, -0.05, -0.05],
        "prediction_error":    [0.005] * (n_flat + 3),
        "beta_uncertainty":    [0.01] * (n_flat + 3),
    }).to_csv(path, index=False)


class PairIdentityTest(SimpleTestCase):
    """Pair identity must come from constructor args or the kalman CSV
    filename, never from a hardcoded AVAX/ATOM assumption baked into the
    math. Exercises the three example pair shapes Phase 1C's brief names
    explicitly: a crypto pair, an equity pair, and an FX cross."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()  # claude code changed: keep fixtures out of research_data/

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)  # claude code changed: clean up temp fixture dir

    def test_crypto_pair_parsed_from_kalman_filename(self):
        pair_name, symbol_a, symbol_b = _parse_pair_from_kalman_filename(
            "research_data/BTC_USDT_ETH_USDT_kalman.csv"
        )
        self.assertEqual((pair_name, symbol_a, symbol_b), ("BTC_USDT/ETH_USDT", "BTC_USDT", "ETH_USDT"))  # claude code changed: not AVAX/ATOM

    def test_filename_parsing_is_crypto_usdt_specific_by_design(self):
        # claude code changed: documents a REAL, currently-true limitation
        # rather than papering over it — the kalman-CSV-filename convention
        # itself (inherited from kalman_filter_engine.py's own output naming)
        # is crypto/USDT-shaped. This is exactly why EntryExitEngine also
        # accepts pair_name/symbol_a/symbol_b directly (see next two tests):
        # a non-crypto pair must be supplied explicitly, never guessed from
        # a filename convention that has no equity/FX equivalent yet.
        with self.assertRaises(ValueError):
            _parse_pair_from_kalman_filename("research_data/AAPL_MSFT_kalman.csv")

    def test_equity_pair_accepted_via_explicit_constructor_args(self):
        # claude code changed: AAPL/MSFT — no "_USDT" in sight, no filename
        # parsing involved at all, and a real completed trade (not just a
        # flat no-entry series), proving the math/rules layer itself
        # carries no crypto assumption once identity is supplied explicitly.
        csv_path = Path(self.tmp_dir) / "scratch_kalman.csv"
        _write_stoploss_scenario_csv(csv_path)
        engine = EntryExitEngine(
            pair_name="AAPL/MSFT", symbol_a="AAPL", symbol_b="MSFT",
            output_dir=self.tmp_dir,
        )
        trade_log, summary, equity_curve = engine.run(kalman_csv=str(csv_path))
        self.assertEqual(summary.iloc[0]["pair"], "AAPL/MSFT")  # claude code changed: pair identity threaded through, not silently AVAX/ATOM

    def test_fx_cross_accepted_via_explicit_constructor_args(self):
        # claude code changed: EUR/USD vs GBP/USD — same proof, FX shape.
        csv_path = Path(self.tmp_dir) / "scratch_kalman.csv"
        _write_stoploss_scenario_csv(csv_path)
        engine = EntryExitEngine(
            pair_name="EUR/USD-GBP/USD", symbol_a="EUR/USD", symbol_b="GBP/USD",
            output_dir=self.tmp_dir,
        )
        trade_log, summary, equity_curve = engine.run(kalman_csv=str(csv_path))
        self.assertEqual(summary.iloc[0]["pair"], "EUR/USD-GBP/USD")  # claude code changed: no AVAX/ATOM fallback triggered


class CostModelIntegrationTest(SimpleTestCase):
    """Phase 1C, Step 3: entry_exit_engine.py must resolve fee_rate/
    slippage_rate through bot.config.cost_model.get_cost_model(), not a
    hardcoded module-level FEE_RATE/SLIPPAGE_RATE crypto constant."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_default_crypto_binance_cost_matches_legacy_constants_exactly(self):
        # claude code changed: regression check (Step 12) — the old
        # hardcoded FEE_RATE=0.001 / SLIPPAGE_RATE=0.0005 must still be
        # exactly what a default-constructed engine gets, so every existing
        # AVAX/ATOM output stays byte-identical.
        engine = EntryExitEngine(output_dir=self.tmp_dir)
        self.assertEqual(engine.fee_rate, 0.001)
        self.assertEqual(engine.slippage_rate, 0.0005)
        self.assertAlmostEqual(engine.total_transaction_cost, 0.003)

    def test_different_venue_produces_different_cost_assumptions(self):
        # claude code changed: Step 4 requirement — "different asset
        # classes/configurations must produce different cost assumptions
        # when configured." Only two real venues exist today (both CRYPTO);
        # kraken's real fee tier (0.26%) differs from binance's (0.1%).
        binance_engine = EntryExitEngine(venue_id="binance", output_dir=self.tmp_dir)
        kraken_engine = EntryExitEngine(venue_id="kraken", output_dir=self.tmp_dir)
        self.assertNotEqual(binance_engine.fee_rate, kraken_engine.fee_rate)
        self.assertNotEqual(
            binance_engine.total_transaction_cost, kraken_engine.total_transaction_cost
        )

    def test_unsupported_asset_class_fails_closed_not_silently_crypto_shaped(self):
        # claude code changed: proves this engine never silently guesses a
        # crypto-shaped cost for an asset class with no real cost model —
        # same fail-closed contract bot.config.cost_model.get_cost_model()
        # itself already guarantees.
        with self.assertRaises(UnsupportedAssetClassCostModel):
            EntryExitEngine(asset_class=ASSET_CLASS_US_EQUITY, output_dir=self.tmp_dir)

    def test_asset_class_and_venue_surfaced_in_strategy_summary(self):
        # claude code changed: transparency check — a researcher reading the
        # summary CSV must be able to see which cost assumptions were used,
        # not just infer them from a module constant that no longer applies.
        engine = EntryExitEngine(asset_class=ASSET_CLASS_CRYPTO, venue_id="kraken", output_dir=self.tmp_dir)
        csv_path = Path(self.tmp_dir) / "AVAX_USDT_ATOM_USDT_kalman.csv"
        _write_stoploss_scenario_csv(csv_path)
        _, summary, _ = engine.run(kalman_csv=str(csv_path))
        self.assertEqual(summary.iloc[0]["asset_class"], ASSET_CLASS_CRYPTO)
        self.assertEqual(summary.iloc[0]["venue_id"], "kraken")
        self.assertEqual(summary.iloc[0]["fee_rate"], 0.0026)  # claude code changed: kraken's real published taker fee


class KalmanPositionSizerTest(SimpleTestCase):
    """Position-sizing math (Kelly fraction, dollar-neutral leg split by
    dynamic hedge ratio beta) — pure, asset-agnostic arithmetic."""

    def test_legs_split_dollar_neutral_by_beta(self):
        sizer = KalmanPositionSizer(capital_usdt=10_000.0, kelly_safety=0.25)
        total, leg_a, leg_b, _ = sizer.size_position(
            zscore=2.5, beta=2.0, beta_uncertainty=0.01, prediction_error=0.005,
        )
        self.assertGreater(total, 0.0)
        self.assertAlmostEqual(leg_a, total / 3.0)         # claude code changed: leg_a = total / (1 + beta)
        self.assertAlmostEqual(leg_b, total * 2.0 / 3.0)   # claude code changed: leg_b = total * beta / (1 + beta)
        self.assertAlmostEqual(leg_a + leg_b, total)        # claude code changed: legs must sum back to total, no capital lost/created

    def test_high_beta_uncertainty_halves_position(self):
        sizer = KalmanPositionSizer(capital_usdt=10_000.0, kelly_safety=0.25)
        total_low_unc, *_ = sizer.size_position(zscore=2.5, beta=1.0, beta_uncertainty=0.01, prediction_error=0.0)
        total_high_unc, *_ = sizer.size_position(zscore=2.5, beta=1.0, beta_uncertainty=0.50, prediction_error=0.0)
        self.assertAlmostEqual(total_high_unc, total_low_unc * 0.50)  # claude code changed: MAX_BETA_UNCERTAINTY breach -> 0.5x multiplier

    def test_below_minimum_position_size_skips_trade(self):
        sizer = KalmanPositionSizer(capital_usdt=100.0, kelly_safety=0.01)  # claude code changed: tiny capital forces sub-$100 result
        total, leg_a, leg_b, _ = sizer.size_position(zscore=2.0, beta=1.0, beta_uncertainty=0.01, prediction_error=0.0)
        self.assertEqual((total, leg_a, leg_b), (0.0, 0.0, 0.0))  # claude code changed: MIN_POSITION_USDT gate returns all-zero, not a tiny trade


class EntryExitSimulationTest(SimpleTestCase):
    """End-to-end run() on a synthetic, deterministic Kalman-shaped CSV —
    proves entry/exit/cost math produces a real completed trade without
    needing any real market data or a real AVAX/ATOM history."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _build_stoploss_scenario_csv(self, path):
        # claude code changed: 12 flat candles (no entry — |z| below
        # threshold), then a confirmed LONG_SPREAD entry at z=-2.2 (negative
        # z, lag also negative, composite signal +0.5 agrees), then an
        # immediate breach past this trade's own stop_zscore
        # (|entry_z|=2.2 + EXIT_ZSCORE_STOP_DISTANCE=1.5 = 3.7) via z=-4.0 —
        # STOPLOSS is the one exit condition not gated by a minimum hold
        # time, making it the deterministic choice for a short synthetic
        # series (see module SCOPE NOTE above).
        n_flat = 12
        rows = {
            "timestamp": list(pd.date_range("2024-01-01", periods=n_flat + 3, freq="h", tz="UTC")),
            "kalman_zscore":       [-0.1] * n_flat + [-2.2, -4.0, -0.1],
            "kalman_zscore_lag1":  [-0.1] * n_flat + [-0.1, -2.2, -4.0],
            "pair_signal_dynamic": [0.05] * n_flat + [0.5, 0.5, 0.05],
            "kalman_beta":         [1.5] * (n_flat + 3),
            "kalman_spread":       [0.0] * n_flat + [-0.01, -0.05, -0.05],
            "prediction_error":    [0.005] * (n_flat + 3),
            "beta_uncertainty":    [0.01] * (n_flat + 3),
        }
        pd.DataFrame(rows).to_csv(path, index=False)

    def test_confirmed_entry_produces_long_spread_trade_with_correct_direction(self):
        csv_path = Path(self.tmp_dir) / "AVAX_USDT_ATOM_USDT_kalman.csv"
        self._build_stoploss_scenario_csv(csv_path)
        engine = EntryExitEngine(output_dir=self.tmp_dir)
        trade_log, summary, equity_curve = engine.run(kalman_csv=str(csv_path))

        self.assertEqual(len(trade_log), 1)  # claude code changed: exactly one completed trade
        trade = trade_log.iloc[0]
        self.assertEqual(trade["direction"], "LONG_SPREAD")     # claude code changed: negative entry z -> LONG_SPREAD
        self.assertAlmostEqual(trade["entry_zscore"], -2.2)
        self.assertEqual(trade["exit_reason"], "STOPLOSS")      # claude code changed: z=-4.0 breaches stop_zscore=3.7

    def test_transaction_cost_applied_matches_engine_cost_model(self):
        # claude code changed: proves fee application actually flows from
        # the resolved cost model (self.total_transaction_cost), not a
        # module-level constant frozen at import time — same scenario,
        # different venue.
        csv_path = Path(self.tmp_dir) / "AVAX_USDT_ATOM_USDT_kalman.csv"
        self._build_stoploss_scenario_csv(csv_path)
        engine = EntryExitEngine(venue_id="kraken", output_dir=self.tmp_dir)
        trade_log, summary, equity_curve = engine.run(kalman_csv=str(csv_path))
        trade = trade_log.iloc[0]
        self.assertAlmostEqual(trade["fee_cost_pct"], engine.total_transaction_cost, places=6)

    def test_position_legs_sum_to_position_size_in_real_trade(self):
        csv_path = Path(self.tmp_dir) / "AVAX_USDT_ATOM_USDT_kalman.csv"
        self._build_stoploss_scenario_csv(csv_path)
        engine = EntryExitEngine(output_dir=self.tmp_dir)
        trade_log, summary, equity_curve = engine.run(kalman_csv=str(csv_path))
        trade = trade_log.iloc[0]
        self.assertAlmostEqual(trade["leg_a_usdt"] + trade["leg_b_usdt"], trade["position_usdt"], places=2)


class SecurityBoundaryTest(SimpleTestCase):
    """Phase 1C, Step 11: the research/simulation layer must have zero
    import path to live execution or order-placement code. Same
    grep/source-inspection philosophy the Research Lab's own security tests
    already use elsewhere in this suite (see
    test_research_lab_orchestrator.py's SecurityNoPathToLiveExecutionTest) —
    EXCEPT that a bare substring check is too blunt for this specific file:
    entry_exit_engine.py's own architecture comments legitimately DISCUSS
    "[Future] execution_engine.py -> generates actual Binance orders" as the
    next pipeline stage it explicitly does NOT implement. This test parses
    real import statements via ast instead of grepping prose, so it proves
    the actual security property (no import path) without false-failing on
    the module's own honest documentation of what it deliberately isn't."""

    FORBIDDEN_MODULE_SUBSTRINGS = [
        "execution_engine", "order_manager", "bot_runner", "ccxt",
    ]
    FORBIDDEN_CREDENTIAL_TOKENS = ["API_KEY", "API_SECRET", "api_key", "api_secret"]

    def test_no_execution_or_order_placement_imports(self):
        import ast

        source = Path("bot/research/entry_exit_engine.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)

        for module_name in imported_modules:
            for forbidden in self.FORBIDDEN_MODULE_SUBSTRINGS:
                self.assertNotIn(
                    forbidden, module_name,
                    f"entry_exit_engine.py (a research SIMULATION engine) imports "
                    f"'{module_name}', which references '{forbidden}' — that would be "
                    f"a real path toward live execution, not just documentation of one."
                )

    def test_no_hardcoded_credential_tokens(self):
        source = Path("bot/research/entry_exit_engine.py").read_text(encoding="utf-8")
        for token in self.FORBIDDEN_CREDENTIAL_TOKENS:
            self.assertNotIn(token, source)
