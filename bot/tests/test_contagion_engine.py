# claude code changed: new file — audit fix for bot/research/contagion_engine.py.
#
# Real project data throughout (data/*.csv, already on disk — 50,000 real
# candles per symbol), no synthetic/fabricated series, matching this
# project's established testing convention. Targets the exact bugs found
# during audit: ALTCOIN_SYMBOLS/BTC_SYMBOL format inconsistency (a stray
# typo — 'ETH/USDT]' — plus a slash-vs-underscore mismatch with the
# project's canonical symbol convention) that made
# run_contagion_research()'s file loader silently fail to find any
# altcoin CSV. Also covers the engine's core correctness (forward-return
# look-ahead safety, divergence formula) against real, independently
# recomputed values — not just "does it run."

import tempfile
from pathlib import Path

import pandas as pd
from django.test import SimpleTestCase

from bot.research.contagion_engine import (
    ALTCOIN_SYMBOLS,
    BTC_SYMBOL,
    ContagionEngine,
    DivergenceICReporter,
    _symbol_to_csv_stem,
    run_contagion_research,
)


def _load_real_csv(symbol_underscore: str, rows: int = 3000) -> pd.DataFrame:
    """
    Loads a real data/*.csv file the same way run_contagion_research()
    does (timestamp -> UTC DatetimeIndex, numeric coercion), trimmed to a
    recent window for fast tests — mirrors the real-data .tail(N) pattern
    already established in test_backtester_venue_selection.py.
    """
    df = pd.read_csv(f"data/{symbol_underscore}_1h.csv")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df.set_index("timestamp", inplace=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["close"], inplace=True)
    df.sort_index(inplace=True)
    return df.tail(rows)


class SymbolListCorrectnessTest(SimpleTestCase):
    """Regression tests for the exact bugs found during audit."""

    def test_btc_symbol_uses_canonical_slash_format(self):
        # claude code changed: was "BTC_USDT" — the mismatch with every
        # altcoin below (all slash-format) was the root inconsistency.
        self.assertEqual(BTC_SYMBOL, "BTC/USDT")

    def test_no_altcoin_symbol_has_a_stray_bracket(self):
        # claude code changed: was 'ETH/USDT]' — a literal typo.
        for symbol in ALTCOIN_SYMBOLS:
            self.assertNotIn("]", symbol)
            self.assertNotIn("[", symbol)

    def test_eth_usdt_is_present_and_correctly_formed(self):
        self.assertIn("ETH/USDT", ALTCOIN_SYMBOLS)

    def test_every_altcoin_symbol_uses_slash_format(self):
        for symbol in ALTCOIN_SYMBOLS:
            self.assertIn("/", symbol)
            self.assertNotIn("_", symbol)

    def test_altcoin_list_matches_fetch_all_symbols_universe(self):
        # claude code changed: real, project-specific compatibility check
        # (not generic) — this list should track bot/fetch_all_symbols.py's
        # own SYMBOLS list (the project's actual tracked universe), minus
        # BTC/USDT, which is the reference asset here, not an altcoin.
        from bot.fetch_all_symbols import SYMBOLS as FETCH_SYMBOLS

        expected = set(FETCH_SYMBOLS) - {BTC_SYMBOL}
        self.assertEqual(set(ALTCOIN_SYMBOLS), expected)


class SymbolToCsvStemTest(SimpleTestCase):
    """THE regression test for the actual file-loading bug."""

    def test_converts_slash_to_underscore(self):
        self.assertEqual(_symbol_to_csv_stem("BNB/USDT"), "BNB_USDT")
        self.assertEqual(_symbol_to_csv_stem("BTC/USDT"), "BTC_USDT")

    def test_constructed_path_matches_a_real_data_file(self):
        # claude code changed: the exact failure mode confirmed during
        # audit — Path("data") / f"{symbol}_1h.csv" on a slash-format
        # symbol silently resolved to a nonexistent subdirectory
        # (data/BNB/USDT_1h.csv instead of data/BNB_USDT_1h.csv).
        for symbol in ["BTC/USDT", "ETH/USDT", "BNB/USDT"]:
            stem = _symbol_to_csv_stem(symbol)
            path = Path("data") / f"{stem}_1h.csv"
            self.assertTrue(path.exists(), f"{path} should exist for real project data")


class ContagionEngineRealDataTest(SimpleTestCase):
    """
    ContagionEngine.calculate_all() against real BTC/ETH/BNB history —
    proves the engine actually produces correct output, not just that it
    runs without crashing.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.raw_data = {
            "BTC/USDT": _load_real_csv("BTC_USDT"),
            "ETH/USDT": _load_real_csv("ETH_USDT"),
            "BNB/USDT": _load_real_csv("BNB_USDT"),
        }

    def _fresh_engine(self):
        # claude code changed: fresh instance per test — calculate_all()
        # mutates self.altcoin_symbols in place (_validate_inputs()), so
        # sharing one engine across tests would leak state between them.
        return ContagionEngine(
            btc_symbol="BTC/USDT",
            altcoin_symbols=["ETH/USDT", "BNB/USDT"],
        )

    def test_produces_expected_columns_for_every_configured_window(self):
        engine = self._fresh_engine()
        enriched = engine.calculate_all(dict(self.raw_data))   # shallow copy — don't let this test's mutation leak

        eth = enriched["ETH/USDT"]
        for window in engine.divergence_windows:
            self.assertIn(f"divergence_{window}h", eth.columns)
            self.assertIn(f"divergence_zscore_{window}h", eth.columns)
        self.assertIn("catch_up_signal", eth.columns)
        self.assertIn("divergence_direction", eth.columns)
        self.assertIn("divergence_magnitude", eth.columns)
        for label in engine.forward_horizons:
            self.assertIn(label, eth.columns)

    def test_forward_return_label_matches_hand_computed_real_sum(self):
        # claude code changed: THE look-ahead-safety correctness test —
        # verifies forward_return_2h at a real row equals the actual sum
        # of the NEXT 2 real hourly returns, computed independently here
        # (not reusing the engine's own logic) — not just "the engine ran."
        engine = self._fresh_engine()
        enriched = engine.calculate_all(dict(self.raw_data))
        eth = enriched["ETH/USDT"]

        returns = eth["return_1h"]
        row = 1000   # arbitrary interior row, far from either edge

        expected_2h = returns.iloc[row + 1] + returns.iloc[row + 2]
        actual_2h = eth["forward_return_2h"].iloc[row]

        self.assertAlmostEqual(actual_2h, expected_2h, places=10)

    def test_last_rows_of_forward_return_are_nan(self):
        # No future data exists beyond the last real candle.
        engine = self._fresh_engine()
        enriched = engine.calculate_all(dict(self.raw_data))
        eth = enriched["ETH/USDT"]

        self.assertTrue(eth["forward_return_12h"].iloc[-1:].isna().all())

    def test_divergence_formula_matches_real_recomputed_returns(self):
        # claude code changed: verifies the actual divergence formula
        # (btc_cumret - alt_cumret) against real, independently
        # recomputed BTC/ETH returns — not just that a column exists.
        engine = self._fresh_engine()
        data_copy = dict(self.raw_data)
        enriched = engine.calculate_all(data_copy)
        eth = enriched["ETH/USDT"]

        row = 2000
        btc_1h = self.raw_data["BTC/USDT"]["close"].pct_change()
        eth_1h = self.raw_data["ETH/USDT"]["close"].pct_change()

        expected_div_1h = btc_1h.iloc[row] - eth_1h.iloc[row]
        actual_div_1h = eth["divergence_1h"].iloc[row]

        # Winsorisation may clip extreme rows — only assert exact equality
        # when the value wasn't clipped (within the engine's own winsor bounds).
        clean = eth["divergence_1h"].dropna()
        lower, upper = clean.quantile(0.01), clean.quantile(0.99)
        if lower <= expected_div_1h <= upper:
            self.assertAlmostEqual(actual_div_1h, expected_div_1h, places=8)


class ValidateInputsGuardTest(SimpleTestCase):

    def test_missing_btc_raises(self):
        engine = ContagionEngine(btc_symbol="BTC/USDT", altcoin_symbols=["ETH/USDT"])
        with self.assertRaises(ValueError):
            engine.calculate_all({"ETH/USDT": _load_real_csv("ETH_USDT")})

    def test_altcoin_with_too_few_candles_is_skipped_not_crashed(self):
        engine = ContagionEngine(btc_symbol="BTC/USDT", altcoin_symbols=["ETH/USDT", "BNB/USDT"])
        data = {
            "BTC/USDT": _load_real_csv("BTC_USDT"),
            "ETH/USDT": _load_real_csv("ETH_USDT"),
            "BNB/USDT": _load_real_csv("BNB_USDT", rows=10),   # below MIN_CANDLES=500
        }

        enriched = engine.calculate_all(data)

        self.assertNotIn("BNB/USDT", engine.altcoin_symbols)   # filtered out by _validate_inputs()
        self.assertIn("ETH/USDT", engine.altcoin_symbols)
        self.assertNotIn("catch_up_signal", enriched["BNB/USDT"].columns)   # never processed
        self.assertIn("catch_up_signal", enriched["ETH/USDT"].columns)


class DivergenceICReporterTest(SimpleTestCase):

    def test_report_runs_and_returns_sane_shape(self):
        engine = ContagionEngine(btc_symbol="BTC/USDT", altcoin_symbols=["ETH/USDT", "BNB/USDT"])
        enriched = engine.calculate_all({
            "BTC/USDT": _load_real_csv("BTC_USDT"),
            "ETH/USDT": _load_real_csv("ETH_USDT"),
            "BNB/USDT": _load_real_csv("BNB_USDT"),
        })

        result = DivergenceICReporter.report(
            enriched, altcoin_symbols=["ETH/USDT", "BNB/USDT"], forward_col="forward_return_2h",
        )

        self.assertFalse(result.empty)
        for col in ("symbol", "feature", "ic", "pvalue", "n", "significant"):
            self.assertIn(col, result.columns)
        self.assertTrue((result["ic"].abs() <= 1.0001).all())   # Spearman IC is bounded [-1, 1]


class RunContagionResearchEndToEndTest(SimpleTestCase):
    """
    Proves the FULL standalone pipeline (real file loading -> engine ->
    real file saving) works end-to-end with the bug fixed — using a
    temporary directory with real data copied in, never touching the
    actual data/research_data project directories.
    """

    def test_loads_real_csvs_and_saves_output_without_touching_project_dirs(self):
        with tempfile.TemporaryDirectory() as tmp_input, tempfile.TemporaryDirectory() as tmp_output:
            # Copy a small, real subset into the temp "data" dir — proves
            # the fixed path construction actually finds real files, not
            # just that the helper function returns the right string.
            for symbol in ["BTC_USDT", "ETH_USDT", "BNB_USDT"]:
                src = Path("data") / f"{symbol}_1h.csv"
                dst = Path(tmp_input) / f"{symbol}_1h.csv"
                dst.write_bytes(src.read_bytes())

            # claude code changed: run_contagion_research() reads
            # ALTCOIN_SYMBOLS as a module global (both for the input-load
            # loop and the output-save loop) — temporarily narrowed to 2
            # symbols so this test only needs to copy 3 real CSVs instead
            # of the full 20-symbol universe, restored in finally so
            # nothing leaks into other tests.
            import bot.research.contagion_engine as ce
            original_altcoins = ce.ALTCOIN_SYMBOLS
            try:
                ce.ALTCOIN_SYMBOLS = ["ETH/USDT", "BNB/USDT"]
                enriched = run_contagion_research(
                    data_dir=tmp_input, output_dir=tmp_output, interval="1h",
                )
            finally:
                ce.ALTCOIN_SYMBOLS = original_altcoins

            self.assertIn("ETH/USDT", enriched)
            self.assertIn("BNB/USDT", enriched)
            self.assertIn("catch_up_signal", enriched["ETH/USDT"].columns)

            self.assertTrue((Path(tmp_output) / "ETH_USDT_contagion.csv").exists())
            self.assertTrue((Path(tmp_output) / "BNB_USDT_contagion.csv").exists())
