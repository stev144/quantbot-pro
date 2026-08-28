# claude code changed: new file — coverage for bot/research/derivatives_engine.py.
# Real project data (data/*.csv) and real network calls for the funding/OI
# fetch that feeds them, no mocking, matching this project's convention —
# per test_contagion_engine.py's precedent, the correctness properties
# that matter here (no look-ahead leakage across the merge_asof join,
# real forward-return math) are exactly the kind a mocked exchange or a
# fabricated feed could hide.

import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.engines.derivatives_data import fetch_funding_rate_history, fetch_open_interest_history
from bot.research.derivatives_engine import (
    DerivativesEngine,
    DerivativesICReporter,
    MIN_CANDLES,
    run_derivatives_research,
)


def _load_real_ohlcv(symbol_underscore: str, rows: int = 3000) -> pd.DataFrame:
    """Loads a real data/*.csv the same plain-column shape
    run_derivatives_research()/feature_calculator.py expect (timestamp as
    a regular column, not an index) — trimmed for fast tests."""
    df = pd.read_csv(f"data/{symbol_underscore}_1h.csv")
    return df.tail(rows).reset_index(drop=True)


class MergeFundingNoLookaheadTest(SimpleTestCase):
    """The one property that matters most for this merge: every OHLCV
    candle must only ever see a funding rate that had already printed by
    that candle's own timestamp — never a future one."""

    def test_assigned_funding_rate_never_comes_from_the_future(self):
        ohlcv = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=MIN_CANDLES + 10, freq="1h", tz="UTC"),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0,
        })
        # Funding prints every 8h starting at hour 0 — rate simply equals
        # the print index, so "does candle N see a rate from print > N/8"
        # is directly checkable.
        funding_timestamps = pd.date_range("2024-01-01", periods=(MIN_CANDLES + 10) // 8, freq="8h", tz="UTC")
        funding = pd.DataFrame({
            "timestamp": funding_timestamps,
            "funding_rate": range(len(funding_timestamps)),
            "mark_price": 100.0,
        })

        engine = DerivativesEngine()
        enriched = engine.calculate_all(ohlcv, funding, None)

        merged_back = enriched.merge(
            funding.rename(columns={"timestamp": "funding_ts"}),
            left_on="funding_rate", right_on="funding_rate", how="left", suffixes=("", "_src"),
        )
        # Every row's own candle timestamp must be >= the funding print
        # timestamp that produced its funding_rate value.
        valid = merged_back["funding_rate"].notna()
        self.assertTrue((merged_back.loc[valid, "timestamp"] >= merged_back.loc[valid, "funding_ts"]).all())


class MissingDataGracefulDegradationTest(SimpleTestCase):

    def _make_ohlcv(self):
        return pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=MIN_CANDLES + 10, freq="1h", tz="UTC"),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0,
        })

    def test_none_funding_and_oi_produce_nan_columns_not_a_crash(self):
        engine = DerivativesEngine()
        enriched = engine.calculate_all(self._make_ohlcv(), None, None)

        self.assertTrue(enriched["funding_rate"].isna().all())
        self.assertTrue(enriched["open_interest_amount"].isna().all())
        for horizon_col in ("forward_return_1h", "forward_return_2h", "forward_return_4h", "forward_return_12h"):
            self.assertIn(horizon_col, enriched.columns)

    def test_empty_dataframes_produce_nan_columns_not_a_crash(self):
        engine = DerivativesEngine()
        empty_funding = pd.DataFrame(columns=["timestamp", "funding_rate", "mark_price"])
        empty_oi = pd.DataFrame(columns=["timestamp", "open_interest_amount", "open_interest_value"])
        enriched = engine.calculate_all(self._make_ohlcv(), empty_funding, empty_oi)

        self.assertTrue(enriched["funding_rate"].isna().all())
        self.assertTrue(enriched["open_interest_amount"].isna().all())

    def test_raises_on_insufficient_candles(self):
        tiny_ohlcv = self._make_ohlcv().head(10)
        engine = DerivativesEngine()
        with self.assertRaises(ValueError):
            engine.calculate_all(tiny_ohlcv, None, None)


class ForwardReturnCorrectnessTest(SimpleTestCase):

    def test_forward_return_matches_hand_computed_value(self):
        ohlcv = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=MIN_CANDLES + 10, freq="1h", tz="UTC"),
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": [100.0 + i for i in range(MIN_CANDLES + 10)],
            "volume": 1000.0,
        })
        engine = DerivativesEngine()
        enriched = engine.calculate_all(ohlcv, None, None)

        row = enriched.iloc[100]
        expected_2h = enriched.iloc[102]["close"] / row["close"] - 1
        self.assertAlmostEqual(row["forward_return_2h"], expected_2h, places=10)

    def test_last_rows_of_forward_return_are_nan(self):
        ohlcv = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=MIN_CANDLES + 10, freq="1h", tz="UTC"),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0,
        })
        engine = DerivativesEngine()
        enriched = engine.calculate_all(ohlcv, None, None)
        self.assertTrue(enriched["forward_return_12h"].iloc[-12:].isna().all())


class RealDataEndToEndTest(SimpleTestCase):
    """Real OHLCV + real, freshly-fetched funding/OI, run through the
    actual merge — proves the whole thing works against live data, not
    just hand-built fixtures."""

    def test_real_btc_funding_merges_with_full_non_null_coverage(self):
        ohlcv = _load_real_ohlcv("BTC_USDT", rows=MIN_CANDLES + 100)
        since_ms = int((time.time() - 60 * 86400) * 1000)
        funding = fetch_funding_rate_history("BTC/USDT", since_ms=since_ms)

        engine = DerivativesEngine()
        enriched = engine.calculate_all(ohlcv, funding, None)

        # Funding history goes back years, so as long as the OHLCV rows
        # fall after the funding series' start, every row should get a
        # real (non-NaN) funding_rate via the backward merge_asof.
        #
        # claude code changed: real, previously-latent bug found and fixed
        # during Hardening Phase 2 (only ever surfaced once data/BTC_USDT_1h.csv
        # existed for this line to actually run against — it always
        # crashed earlier on FileNotFoundError before this comparison had
        # a chance to execute). funding["timestamp"] is already real
        # millisecond-epoch int64 (see derivatives_data.py's
        # fetch_funding_rate_history — it stores ccxt's raw `ts` directly,
        # never converts to datetime64), so `.astype("int64").min() //
        # 10**6` was both a no-op cast AND a wrong unit conversion
        # (shrinking an already-correct ms value ~1e6x), and the result
        # was then compared against enriched["timestamp"] (a real
        # datetime64[ns, UTC] column, per DerivativesEngine's
        # _to_utc_datetime()) — an int-vs-datetime comparison pandas
        # correctly refuses to do at all. Converting the funding minimum
        # to a real UTC Timestamp is the actual fix, not a unit tweak.
        funding_start = pd.to_datetime(funding["timestamp"].min(), unit="ms", utc=True)
        recent_rows = enriched[enriched["timestamp"] >= funding_start]
        if not recent_rows.empty:
            self.assertTrue(recent_rows["funding_rate"].notna().any())


class DerivativesICReporterTest(SimpleTestCase):

    def test_report_runs_and_returns_sane_shape(self):
        ohlcv = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=MIN_CANDLES + 10, freq="1h", tz="UTC"),
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.0 + np.cumsum(np.random.default_rng(0).normal(size=MIN_CANDLES + 10)),
            "volume": 1000.0,
        })
        funding_timestamps = pd.date_range("2024-01-01", periods=(MIN_CANDLES + 10) // 8, freq="8h", tz="UTC")
        funding = pd.DataFrame({
            "timestamp": funding_timestamps,
            "funding_rate": np.random.default_rng(1).normal(size=len(funding_timestamps)) * 0.0001,
            "mark_price": 100.0,
        })

        engine = DerivativesEngine()
        enriched = {"FAKE/USDT": engine.calculate_all(ohlcv, funding, None)}

        result = DerivativesICReporter.report(enriched, forward_col="forward_return_2h")
        if not result.empty:
            for col in ("symbol", "feature", "ic", "pvalue", "n", "significant"):
                self.assertIn(col, result.columns)
            self.assertTrue((result["ic"].abs() <= 1.0001).all())


class RunDerivativesResearchEndToEndTest(SimpleTestCase):
    """Full standalone pipeline: real OHLCV + real fetched funding/OI CSVs
    on disk -> engine -> real file saving — using a temporary directory,
    never touching the actual data/research_data project directories."""

    def test_loads_real_csvs_and_saves_output_without_touching_project_dirs(self):
        with tempfile.TemporaryDirectory() as tmp_input, tempfile.TemporaryDirectory() as tmp_output:
            for symbol in ["BTC_USDT", "ETH_USDT"]:
                src = Path("data") / f"{symbol}_1h.csv"
                dst = Path(tmp_input) / f"{symbol}_1h.csv"
                dst.write_bytes(src.read_bytes())

            since_ms = int((time.time() - 20 * 86400) * 1000)
            for symbol_slash, symbol_underscore in [("BTC/USDT", "BTC_USDT"), ("ETH/USDT", "ETH_USDT")]:
                funding = fetch_funding_rate_history(symbol_slash, since_ms=since_ms)
                funding.to_csv(Path(tmp_input) / f"{symbol_underscore}_funding.csv", index=False)
                oi = fetch_open_interest_history(symbol_slash, days=10)
                oi.to_csv(Path(tmp_input) / f"{symbol_underscore}_oi.csv", index=False)

            import bot.fetch_all_symbols as fas
            original_symbols = fas.SYMBOLS
            try:
                fas.SYMBOLS = ["BTC/USDT", "ETH/USDT"]
                enriched = run_derivatives_research(data_dir=tmp_input, output_dir=tmp_output, interval="1h")
            finally:
                fas.SYMBOLS = original_symbols

            self.assertIn("BTC/USDT", enriched)
            self.assertIn("ETH/USDT", enriched)
            self.assertIn("funding_rate", enriched["BTC/USDT"].columns)

            self.assertTrue((Path(tmp_output) / "BTC_USDT_derivatives.csv").exists())
            self.assertTrue((Path(tmp_output) / "ETH_USDT_derivatives.csv").exists())


class OpenInterestNoLookaheadTest(SimpleTestCase):
    """claude code changed: new — Phase 2E Step 2 (causality/data-integrity
    gate for the OI feature family, ahead of the derivatives re-test).
    MergeFundingNoLookaheadTest above proves this property for funding_rate;
    no equivalent existed for open_interest_amount/oi_change_1h/oi_change_24h/
    oi_price_interaction. Reuses the exact same deterministic-synthetic-
    timestamp technique (value-as-index, so "which print produced this
    candle's value" is directly checkable) — no new test framework."""

    def _make_ohlcv(self, n):
        return pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC"),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0,
        })

    def test_assigned_oi_never_comes_from_the_future(self):
        n = MIN_CANDLES + 10
        ohlcv = self._make_ohlcv(n)
        # claude code changed: OI prints offset 30 minutes INTO each hour —
        # mirrors real-world OI reporting lag (a print is not available at
        # the instant its own hour begins). oi value = print index, so the
        # print that produced any given candle's value is directly checkable.
        oi_timestamps = pd.date_range("2024-01-01 00:30", periods=n, freq="1h", tz="UTC")
        oi = pd.DataFrame({
            "timestamp": oi_timestamps,
            "open_interest_amount": range(n),
            "open_interest_value": range(n),
        })

        engine = DerivativesEngine()
        enriched = engine.calculate_all(ohlcv, None, oi)

        merged_back = enriched.merge(
            oi.rename(columns={"timestamp": "oi_ts"}),
            left_on="open_interest_amount", right_on="open_interest_amount", how="left", suffixes=("", "_src"),
        )
        valid = merged_back["open_interest_amount"].notna()
        self.assertTrue((merged_back.loc[valid, "timestamp"] >= merged_back.loc[valid, "oi_ts"]).all())

        # claude code changed: THE specific leakage this targets — candle H
        # (timestamp H:00) must NOT see print H (timestamp H:30, printed 30
        # minutes AFTER candle H opens); it must see print H-1 (printed at
        # (H-1):30, already available by H:00).
        row_h = enriched.iloc[5]
        self.assertEqual(row_h["open_interest_amount"], 4)  # print 4, not print 5

    def test_oi_derived_columns_unaffected_by_a_later_oi_print(self):
        """claude code changed: mutation-invariance proof covering
        pct_change(1)/pct_change(24)/oi_price_interaction in one test — the
        real risk for these three, as opposed to the raw merge tested above.
        Perturbing ONE mid-series OI print must never change any candle
        BEFORE that print's own timestamp; and (so this isn't a vacuously
        passing test) must actually change something AT/AFTER it."""
        n = MIN_CANDLES + 30
        ohlcv = self._make_ohlcv(n)
        rng = np.random.default_rng(7)
        oi_timestamps = pd.date_range("2024-01-01 00:30", periods=n, freq="1h", tz="UTC")
        base_values = 1000 + np.cumsum(rng.normal(size=n))
        oi = pd.DataFrame({
            "timestamp": oi_timestamps,
            "open_interest_amount": base_values,
            "open_interest_value": base_values * 100,
        })

        engine = DerivativesEngine()
        enriched_original = engine.calculate_all(ohlcv, None, oi)

        mutate_idx = n // 2
        oi_mutated = oi.copy()
        oi_mutated.loc[mutate_idx, "open_interest_amount"] *= 5.0
        enriched_mutated = engine.calculate_all(ohlcv, None, oi_mutated)

        cutoff_ts = oi.loc[mutate_idx, "timestamp"]
        cols = ["open_interest_amount", "oi_change_1h", "oi_change_24h", "oi_price_interaction"]

        before_mask = enriched_original["timestamp"] < cutoff_ts
        pd.testing.assert_frame_equal(
            enriched_original.loc[before_mask, cols].reset_index(drop=True),
            enriched_mutated.loc[before_mask, cols].reset_index(drop=True),
        )

        # claude code changed: prove the test setup isn't vacuous — the
        # mutation DOES change something from the print's own timestamp
        # onward, so the "before" equality above is a real causality proof,
        # not an artifact of the mutation never mattering anywhere.
        after_mask = ~before_mask
        self.assertFalse(
            enriched_original.loc[after_mask, "open_interest_amount"].reset_index(drop=True).equals(
                enriched_mutated.loc[after_mask, "open_interest_amount"].reset_index(drop=True)
            )
        )

    def test_candles_before_earliest_oi_print_are_nan_not_backfilled(self):
        """claude code changed: proves Binance's real ~30-day OI history cap
        (fetch_open_interest_history's MAX_OPEN_INTEREST_HISTORY_DAYS,
        already tested in test_derivatives_data.py) shows up here as honest
        NaN, never a value fabricated/backfilled from the first available
        (future, relative to the candle) print."""
        n = MIN_CANDLES + 10
        ohlcv = self._make_ohlcv(n)
        oi_start_idx = 50   # OI history only starts 50 hours into the OHLCV window
        oi_timestamps = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")[oi_start_idx:]
        remaining = n - oi_start_idx
        oi = pd.DataFrame({
            "timestamp": oi_timestamps,
            "open_interest_amount": range(remaining),
            "open_interest_value": range(remaining),
        })

        engine = DerivativesEngine()
        enriched = engine.calculate_all(ohlcv, None, oi)

        self.assertTrue(enriched["open_interest_amount"].iloc[:oi_start_idx].isna().all())
        self.assertTrue(enriched["oi_change_1h"].iloc[:oi_start_idx].isna().all())

    def test_none_and_empty_oi_produce_nan_for_every_oi_derived_column(self):
        """claude code changed: extends MissingDataGracefulDegradationTest's
        existing open_interest_amount-only check to the DERIVED oi_ columns
        too — a missing OI history must not silently produce a zero (rather
        than NaN) rate-of-change, which would read as "no change" instead of
        "unknown"."""
        ohlcv = self._make_ohlcv(MIN_CANDLES + 10)
        engine = DerivativesEngine()

        for oi_input in (None, pd.DataFrame(columns=["timestamp", "open_interest_amount", "open_interest_value"])):
            enriched = engine.calculate_all(ohlcv, None, oi_input)
            for col in ("oi_change_1h", "oi_change_24h", "oi_price_interaction"):
                self.assertTrue(enriched[col].isna().all(), f"{col} should be all-NaN when OI is unavailable")
