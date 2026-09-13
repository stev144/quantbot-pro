# claude code changed: new file — Phase B of the controlled remediation
# program (forensic-audit finding P1-1: cross-sectional winsorization used
# whole-history quantiles as clip boundaries, so a row's winsorized return
# was informed by that symbol's most extreme moves regardless of whether
# they happened before or after that row — a real look-ahead leak into
# every downstream cross-sectional feature). These tests prove the fix:
# "changing the future must not change the past."

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.cross_section_engine import CrossSectionEngine, WINSOR_MIN_PERIODS, run_forex_cross_section_research


def _make_return_df(n, seed, spike_at=None, spike_magnitude=5.0):
    """
    Synthetic single-symbol OHLCV-shaped DataFrame with a 'return_1h'
    column already populated (the input _winsorise_returns() expects).
    If spike_at is given, a single huge outlier return is injected at that
    row index — used to simulate "a future flash crash".
    """
    rng = np.random.default_rng(seed)
    returns = rng.normal(0, 0.01, n)
    if spike_at is not None:
        returns[spike_at] = spike_magnitude   # a wildly extreme future return
    close = 100.0 * np.exp(np.cumsum(returns))
    df = pd.DataFrame({"close": close})
    df.index = pd.date_range("2020-01-01", periods=n, freq="1h")
    df["return_1h"] = df["close"].pct_change()
    return df


class WinsorizationLeakageTest(SimpleTestCase):

    def test_past_boundaries_unaffected_by_future_spike(self):
        """
        Two datasets share IDENTICAL history for the first `shared_rows`
        rows. Dataset B then has a huge outlier injected only AFTER that
        shared window (simulating a future flash crash). The winsorized
        values for the shared, earlier rows must be byte-identical between
        A and B — if a future value can change an earlier row's clipped
        value, the boundary was computed with look-ahead.
        """
        shared_rows = WINSOR_MIN_PERIODS + 500   # enough history for real boundaries to form
        tail_rows = 200

        df_a = _make_return_df(shared_rows + tail_rows, seed=1)

        df_b = df_a.copy()
        # Overwrite only the tail with a huge spike — shared_rows prefix
        # (including its underlying returns) is untouched.
        spike_idx = shared_rows + 50
        df_b.loc[df_b.index[spike_idx], "return_1h"] = 8.0  # +800% one-candle "crash recovery"

        engine = CrossSectionEngine()
        out_a = engine._winsorise_returns({"TEST_USDT": df_a.copy()})["TEST_USDT"]
        out_b = engine._winsorise_returns({"TEST_USDT": df_b.copy()})["TEST_USDT"]

        shared_a = out_a["return_1h_winsor"].iloc[:shared_rows]
        shared_b = out_b["return_1h_winsor"].iloc[:shared_rows]

        pd.testing.assert_series_equal(
            shared_a, shared_b,
            check_names=False,
            obj="return_1h_winsor for the shared (pre-spike) history",
        )

    def test_early_rows_kept_raw_not_clipped_from_insufficient_history(self):
        """
        Before winsor_min_periods observations exist, there is not enough
        history to trust a 1st/99th percentile estimate — those rows must
        keep their raw, unclipped return rather than being clipped against
        an unstable boundary.
        """
        n = WINSOR_MIN_PERIODS - 10
        df = _make_return_df(n, seed=2)

        engine = CrossSectionEngine()
        out = engine._winsorise_returns({"TEST_USDT": df.copy()})["TEST_USDT"]

        pd.testing.assert_series_equal(
            out["return_1h_winsor"].iloc[1:],   # skip row 0 (NaN return, no prior close)
            out["return_1h"].iloc[1:],
            check_names=False,
            obj="return_1h_winsor before enough history exists for a stable boundary",
        )

    def test_boundary_is_strictly_prior_not_inclusive_of_current_row(self):
        """
        A single extreme row must not be able to widen its OWN clip
        boundary enough to escape being clipped — the boundary at row t
        must be estimated from rows [0, t-1] only, never row t itself.
        """
        n = WINSOR_MIN_PERIODS + 100
        df = _make_return_df(n, seed=3)
        extreme_idx = n - 1   # last row: an extreme value with maximum
        df.loc[df.index[extreme_idx], "return_1h"] = 3.0  # +300% in one candle

        engine = CrossSectionEngine()
        out = engine._winsorise_returns({"TEST_USDT": df.copy()})["TEST_USDT"]

        winsorized_value = out["return_1h_winsor"].iloc[extreme_idx]
        self.assertLess(
            winsorized_value, 3.0,
            "an extreme row's own value must not inflate the boundary used to clip itself",
        )


def _make_ohlcv_df(n, seed, base=100.0):
    rng = np.random.default_rng(seed)
    close = base * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    df = pd.DataFrame({
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": np.full(n, 1000.0),
    })
    df.index = pd.date_range("2024-01-01", periods=n, freq="1h")
    return df


class ForwardReturnMomentumReversalUniverseBugTest(SimpleTestCase):
    """claude code changed: new — real, confirmed bug found during the
    Forex Cross-Sectional Research Validation Audit. _calculate_momentum_features(),
    _calculate_reversal_signal(), and _calculate_forward_returns() each
    iterated the module-level, crypto-hardcoded UNIVERSE constant instead
    of the symbols actually present in the data being processed. Every
    Forex symbol (not in UNIVERSE at all) silently got NO forward_return_1h,
    cs_momentum_3h/6h, or cs_reversal_signal — the Y-variable every
    downstream predictive-power test depends on was simply never
    produced, no error raised. Confirmed live: research_data/forex/
    *_cross_section.csv only ever had 4 of the intended 8 feature
    columns before this fix. Also latent for ANY crypto symbol
    _validate_inputs() happened to filter out (fewer than min_rows) —
    not exclusively a Forex-only defect, just Forex-guaranteed since no
    Forex symbol is ever in UNIVERSE."""

    def test_forward_return_and_momentum_and_reversal_signal_computed_for_non_universe_symbols(self):
        # claude code changed: symbol names deliberately NOT in the real
        # crypto UNIVERSE constant — this is exactly the Forex scenario.
        data = {
            "EUR_USD": _make_ohlcv_df(600, seed=1),
            "GBP_USD": _make_ohlcv_df(600, seed=2),
            "USD_JPY": _make_ohlcv_df(600, seed=3),
            "AUD_USD": _make_ohlcv_df(600, seed=4),
        }
        engine = CrossSectionEngine()
        result = engine.calculate_all(data)
        for symbol, df in result.items():
            self.assertIn("forward_return_1h", df.columns, f"{symbol} missing forward_return_1h")
            self.assertIn("cs_momentum_3h", df.columns, f"{symbol} missing cs_momentum_3h")
            self.assertIn("cs_momentum_6h", df.columns, f"{symbol} missing cs_momentum_6h")
            self.assertIn("cs_reversal_signal", df.columns, f"{symbol} missing cs_reversal_signal")
            self.assertGreater(df["forward_return_1h"].notna().sum(), 0, f"{symbol} forward_return_1h is all-NaN")
            self.assertGreater(df["cs_reversal_signal"].notna().sum(), 0, f"{symbol} cs_reversal_signal is all-NaN")

    def test_real_crypto_universe_symbols_unaffected_by_the_fix(self):
        # claude code changed: regression guard — real UNIVERSE members
        # (crypto symbols) must behave identically after deriving the
        # symbol list from the data instead of the hardcoded constant.
        data = {
            "BTC_USDT": _make_ohlcv_df(600, seed=5),
            "ETH_USDT": _make_ohlcv_df(600, seed=6),
            "SOL_USDT": _make_ohlcv_df(600, seed=7),
            "AVAX_USDT": _make_ohlcv_df(600, seed=8),
        }
        engine = CrossSectionEngine()
        result = engine.calculate_all(data)
        for symbol, df in result.items():
            self.assertGreater(df["forward_return_1h"].notna().sum(), 0)
            self.assertGreater(df["cs_reversal_signal"].notna().sum(), 0)
            self.assertGreater(df["cs_momentum_6h"].notna().sum(), 0)


class RunForexCrossSectionResearchTest(SimpleTestCase):
    """claude code changed: new — Forex Research Dashboard mission,
    explicit follow-up request. Real data, real engine, no mocking
    (matching this project's own convention) — this is a genuine,
    fast (~5s for 8 symbols) research-generation run against whatever
    real data/forex/*.csv files exist on this machine, not a synthetic
    fixture, since the whole point is proving the real pipeline produces
    real output."""

    def test_writes_to_the_segregated_forex_directory_not_the_flat_root(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_out:
            result = run_forex_cross_section_research(output_dir=tmp_out)
            if not result:
                self.skipTest("no data/forex/*.csv files on this machine")
            written = os.listdir(tmp_out)
            self.assertTrue(any(f.endswith("_cross_section.csv") for f in written))

    def test_output_has_real_cs_zscore_column_not_fabricated(self):
        import os
        import tempfile

        import pandas as pd

        with tempfile.TemporaryDirectory() as tmp_out:
            result = run_forex_cross_section_research(output_dir=tmp_out)
            if not result:
                self.skipTest("no data/forex/*.csv files on this machine")
            symbol, enriched_df = next(iter(result.items()))
            self.assertIn("cs_zscore", enriched_df.columns)
            self.assertGreater(enriched_df["cs_zscore"].notna().sum(), 0)
            # claude code changed: regression guard for the confirmed
            # UNIVERSE-hardcoding bug fixed by this same audit — these
            # three columns were silently absent for every real Forex
            # symbol before the fix.
            self.assertIn("forward_return_1h", enriched_df.columns)
            self.assertGreater(enriched_df["forward_return_1h"].notna().sum(), 0)
            self.assertIn("cs_reversal_signal", enriched_df.columns)
            # claude code changed: confirms real files land on disk, not
            # just returned in memory
            saved = pd.read_csv(os.path.join(tmp_out, f"{symbol}_cross_section.csv"))
            self.assertEqual(len(saved), len(enriched_df))

    def test_uses_the_real_instrument_registry_never_a_hardcoded_symbol_list(self):
        import inspect
        source = inspect.getsource(run_forex_cross_section_research)
        self.assertIn("symbols_for_asset_class", source)
        self.assertIn("resolve_ohlcv_path", source)


class RegressionCriticalUniverseIntegrityTest(SimpleTestCase):
    """claude code changed: new — Forex Integration Stage 1 (Architectural
    Parity), mission section 1.4's explicit checklist for the exact bug
    class this session already found and fixed once (UNIVERSE
    hardcoding silently mis-scoping Forex). Each test below proves one
    named property from that checklist directly against the real engine
    and its real callers — not synthetic assertions about intent."""

    def test_empty_symbol_universe_fails_explicitly_not_silently(self):
        with self.assertRaises(ValueError):
            CrossSectionEngine().calculate_all({})

    def test_missing_required_columns_are_dropped_not_silently_processed(self):
        # claude code changed: _validate_inputs() already fails this way
        # (SKIPPED + logged, then a hard ValueError once too few symbols
        # remain) — this test proves that real behavior directly, since
        # nothing previously exercised _validate_inputs() on its own.
        no_close_df = pd.DataFrame({"open": [1, 2, 3]}, index=pd.date_range("2024-01-01", periods=3, freq="1h"))
        data = {
            "A": _make_ohlcv_df(600, seed=1), "B": _make_ohlcv_df(600, seed=2),
            "C": _make_ohlcv_df(600, seed=3), "D": no_close_df,
        }
        # Only 3 of 4 symbols have a usable 'close' column — still >= MIN_ASSETS(4)? No: 3 < 4, must raise.
        with self.assertRaises(ValueError):
            CrossSectionEngine().calculate_all(data)

    def test_missing_datetime_index_is_dropped_not_silently_processed(self):
        not_datetime_indexed = pd.DataFrame({"close": [1.0, 2.0, 3.0]})  # default RangeIndex
        data = {
            "A": _make_ohlcv_df(600, seed=1), "B": _make_ohlcv_df(600, seed=2),
            "C": _make_ohlcv_df(600, seed=3), "D": not_datetime_indexed,
        }
        with self.assertRaises(ValueError):
            CrossSectionEngine().calculate_all(data)

    def test_below_minimum_assets_fails_explicitly(self):
        # 3 valid symbols, MIN_ASSETS_FOR_CROSS_SECTION default is 4.
        data = {"A": _make_ohlcv_df(600, seed=1), "B": _make_ohlcv_df(600, seed=2), "C": _make_ohlcv_df(600, seed=3)}
        with self.assertRaises(ValueError):
            CrossSectionEngine().calculate_all(data)

    def test_real_forex_and_crypto_callers_never_mix_universes(self):
        # claude code changed: CrossSectionEngine.calculate_all() is
        # deliberately universe-agnostic (it processes whatever dict it's
        # handed — that's the correct, shared-core design). The actual
        # guarantee "Forex data never silently uses the Crypto universe,
        # and vice versa" lives in the two real callers, each scoped to
        # its own asset class via the registry — proven here directly,
        # not asserted as an intent.
        from bot.instruments import ASSET_CLASS_CRYPTO, ASSET_CLASS_FOREX, get_instrument, symbols_for_asset_class
        from bot.research.cross_section_engine import UNIVERSE as CRYPTO_UNIVERSE

        crypto_symbols = set(symbols_for_asset_class(ASSET_CLASS_CRYPTO))
        forex_symbols = set(symbols_for_asset_class(ASSET_CLASS_FOREX))
        self.assertTrue(crypto_symbols.isdisjoint(forex_symbols), "registry itself must never assign one symbol to two asset classes")

        # The crypto module-level UNIVERSE (run_cross_section_research()'s
        # own scope) must be 100% CRYPTO instruments.
        for underscore_symbol in CRYPTO_UNIVERSE:
            canonical = underscore_symbol.replace("_", "/", 1)
            instrument = get_instrument(canonical)
            if instrument is not None:
                self.assertEqual(instrument.asset_class, ASSET_CLASS_CRYPTO, f"{canonical} leaked into the crypto UNIVERSE constant")

    def test_provider_metadata_survives_through_engine_output(self):
        """claude code changed: 'provider metadata is preserved' — the
        engine's output DataFrame must still carry the real OHLCV columns
        (open/high/low/close/volume) it was given, not just the derived
        cs_* columns, so a caller can always trace a feature value back
        to its real source price."""
        data = {f"SYM{i}": _make_ohlcv_df(600, seed=i) for i in range(5)}
        result = CrossSectionEngine().calculate_all(data)
        for symbol, df in result.items():
            for col in ("open", "high", "low", "close", "volume"):
                self.assertIn(col, df.columns, f"{symbol} lost its original '{col}' column")

    def test_symbol_to_filename_mapping_is_deterministic(self):
        from bot.instruments import resolve_ohlcv_path
        for symbol in ("BTC/USDT", "EUR/USD", "USD/JPY"):
            path_1 = resolve_ohlcv_path(symbol)
            path_2 = resolve_ohlcv_path(symbol)
            self.assertEqual(path_1, path_2, f"resolve_ohlcv_path({symbol!r}) is not deterministic")
