# ============================================================
# bot/tests/test_forex_crypto_cross_asset_proof.py
#
# Forex Multi-Asset Integration — Milestone E.
#
# claude code changed: new file. The mission's own explicit requirement:
# prove the Research Lab is genuinely multi-asset by running the SAME
# UNMODIFIED shared engines against both Crypto and Forex data — not a
# claim of a profitable edge, a proof of code-sharing. Every engine
# exercised here is imported and called exactly as any real caller would,
# with zero test-only branching on asset class inside the engines
# themselves (if a test needed to special-case Forex inside an engine to
# make it pass, that would be evidence the engine ISN'T actually
# asset-agnostic — the opposite of what this file exists to prove).
#
# Skips gracefully (not fails) if Forex data hasn't been fetched yet on
# this machine (bot/forex_data_fetcher.py must be run first) — matching
# this project's own "no mocking, real data" test convention while still
# distinguishing "data not present" from "code broken."
# ============================================================

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.instruments import resolve_ohlcv_path
from bot.research.cointegration_engine import CointegrationEngine
from bot.research.cross_sectional_permutation_test import run_cross_sectional_permutation_test
from bot.research.oos_validator import WalkForwardConfig, evaluate_feature_oos
from bot.research_lab.tools._data import load_ohlcv

FOREX_PAIR_A = "EUR/USD"
FOREX_PAIR_B = "GBP/USD"
CRYPTO_PAIR_A = "BTC/USDT"
CRYPTO_PAIR_B = "ETH/USDT"


def _forex_data_available() -> bool:
    return resolve_ohlcv_path(FOREX_PAIR_A).exists() and resolve_ohlcv_path(FOREX_PAIR_B).exists()


def _skip_reason() -> str:
    return (
        f"Forex OHLCV data not present at {resolve_ohlcv_path(FOREX_PAIR_A)} — "
        f"run `python -m bot.forex_data_fetcher` first. This is a data-availability "
        f"skip, not a code defect (mission Phase 16's own PASS/WARN/FAIL/SKIP distinction)."
    )


class CointegrationEngineCrossAssetProofTest(SimpleTestCase):
    """
    claude code changed: new. CointegrationEngine._test_pair() run against
    BOTH a real crypto pair and a real Forex pair, using the exact same
    engine instance and method — zero code path difference. This is the
    "technical compatibility" half of the proof; the actual cointegration
    verdict (real or not) is reported honestly, not claimed as a finding.
    """

    def test_same_engine_runs_on_crypto_pair(self):
        engine = CointegrationEngine()
        df_a = load_ohlcv(CRYPTO_PAIR_A)
        df_b = load_ohlcv(CRYPTO_PAIR_B)
        result = engine._test_pair(CRYPTO_PAIR_A, CRYPTO_PAIR_B, df_a["close"], df_b["close"])
        self.assertIsNotNone(result)
        self.assertEqual(result.symbol_a, CRYPTO_PAIR_A)
        self.assertFalse(np.isnan(result.adf_pvalue))

    def test_same_engine_runs_on_forex_pair(self):
        if not _forex_data_available():
            self.skipTest(_skip_reason())
        engine = CointegrationEngine()   # claude code changed: SAME class, SAME default construction as the crypto test above
        df_a = load_ohlcv(FOREX_PAIR_A)
        df_b = load_ohlcv(FOREX_PAIR_B)
        result = engine._test_pair(FOREX_PAIR_A, FOREX_PAIR_B, df_a["close"], df_b["close"])
        self.assertIsNotNone(result)
        self.assertEqual(result.symbol_a, FOREX_PAIR_A)
        self.assertFalse(np.isnan(result.adf_pvalue))
        # claude code changed: honest reporting, not a claim of a finding —
        # EUR/USD vs GBP/USD is a well-known FX relative-value pair
        # (Phase 7's own example), but this test's job is to prove the
        # ENGINE runs on Forex data, not that it found a real edge.
        print(
            f"\n[cross-asset proof] EUR/USD vs GBP/USD cointegration: "
            f"adf_pvalue={result.adf_pvalue:.4f}, coint_pvalue={result.coint_pvalue:.4f}, "
            f"half_life={result.half_life:.1f} candles, is_cointegrated={result.is_cointegrated}"
        )


class OosValidatorCrossAssetProofTest(SimpleTestCase):
    """
    claude code changed: new. evaluate_feature_oos() (the Type A shared
    evaluator, oos_validator.py) run against BOTH crypto and Forex
    OHLCV-derived data — proving the OOS/fold/purge infrastructure itself
    has zero asset-class awareness, exactly as its own docstring claims.
    """

    def _run_feature_oos(self, symbol: str):
        df = load_ohlcv(symbol).reset_index()
        df["feature"] = df["close"].pct_change(24)          # a trivial, asset-agnostic momentum feature
        df["forward_return"] = df["close"].pct_change(1).shift(-1)
        df = df.dropna(subset=["feature", "forward_return"])
        config = WalkForwardConfig(mode="expanding", min_train_periods=500, test_periods=200, horizon=1, min_test_periods=50, seed=7)
        return evaluate_feature_oos(df, "feature", "forward_return", "timestamp", config)

    def test_same_evaluator_runs_on_crypto(self):
        result = self._run_feature_oos(CRYPTO_PAIR_A)
        self.assertGreater(result.n_folds_total, 0)

    def test_same_evaluator_runs_on_forex(self):
        if not _forex_data_available():
            self.skipTest(_skip_reason())
        result = self._run_feature_oos(FOREX_PAIR_A)
        self.assertGreater(result.n_folds_total, 0)
        mean_ic = result.aggregate.get("mean_ic")
        print(f"\n[cross-asset proof] EUR/USD momentum-feature OOS: n_folds={result.n_folds_total}, mean_ic={mean_ic}")


class CrossSectionalPermutationCrossAssetProofTest(SimpleTestCase):
    """
    claude code changed: new. run_cross_sectional_permutation_test() —
    the SAME function real crypto cross-sectional research uses (see
    bot/tests/test_oos_validator.py's CrossSectionalPermutationSyntheticProofTest) —
    run against a real long-format panel built directly from whichever
    Forex pairs are currently in bot.forex_data_fetcher.SYMBOLS (the live,
    persisted universe selection — NOT a fixed 8 majors; that universe was
    deliberately widened to a 28-pair full G8-currency cross matrix on
    2026-09-13, so this test's panel size moves whenever that selection
    does, on purpose). Deliberately does NOT go through
    cross_section_engine.py (that file's compute_cross_section_features()
    still hardcodes a CRYPTO-only universe, an honestly-recorded, separate
    gap — see capability_registry.py's cross_sectional_research entry) —
    this test builds the panel itself from real data to prove the
    EVALUATOR/PERMUTATION machinery is asset-agnostic independent of that
    unrelated wiring gap. Uses a tiny n_permutations purely for test speed.
    """

    def test_real_forex_panel_runs_through_the_same_evaluator_crypto_uses(self):
        from bot.forex_data_fetcher import SYMBOLS as FOREX_SYMBOLS

        if not _forex_data_available():
            self.skipTest(_skip_reason())

        rows = []
        for symbol in FOREX_SYMBOLS:
            df = load_ohlcv(symbol).reset_index()
            df["feature"] = df["close"].pct_change(24)
            df["forward_return"] = df["close"].pct_change(1).shift(-1)
            df = df.dropna(subset=["feature", "forward_return"])
            df["asset"] = symbol
            rows.append(df[["timestamp", "asset", "feature", "forward_return"]])
        panel = pd.concat(rows, ignore_index=True)

        config = WalkForwardConfig(mode="expanding", min_train_periods=500, test_periods=200, horizon=1, min_test_periods=50, seed=7)
        result = run_cross_sectional_permutation_test(
            panel, "timestamp", "asset", "feature", "forward_return", config,
            top_k=2, n_permutations=5, random_seed=7,
        )
        self.assertIn("real", result)
        self.assertIn("verdict", result)
        # claude code changed: was a hardcoded "(8 majors)" label — went
        # stale the moment the universe widened to 28 pairs on 2026-09-13
        # without this print statement being updated, silently mislabeling
        # a structurally different (3.5x larger, non-USD-dominant) panel as
        # the original 8-majors one. Reports the real, current count instead.
        print(f"\n[cross-asset proof] Forex cross-sectional ({len(FOREX_SYMBOLS)} pairs) real Sharpe: {result['real'].get('sharpe_ratio')}")
