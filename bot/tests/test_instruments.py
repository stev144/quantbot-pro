# claude code changed: new file — Multi-Asset Foundation Refactor Phase
# 1A. Tests for bot/instruments.py, the instrument identity + canonical
# symbol boundary (STEP 1) and the provider-path resolution boundary
# (STEP 2). No mocking, per this project's convention — reads the real
# data/BTC_USDT_1h.csv file exactly like the Research Lab tool layer does.

from django.test import SimpleTestCase

from bot.instruments import (
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_FOREX,
    ASSET_CLASS_US_EQUITY,
    ASSET_CLASSES,
    INSTRUMENT_REGISTRY,
    Instrument,
    UnknownInstrumentError,
    UnsupportedTimeframeError,
    candles_to_wall_clock,
    get_instrument,
    list_instruments,
    resolve_ohlcv_path,
    symbols_for_asset_class,
)


class InstrumentRegistryTest(SimpleTestCase):

    def test_every_registry_entry_is_crypto_today(self):
        """Real, honest current state — no US_EQUITY/FOREX data has ever
        been ingested by this platform. This test should be the FIRST
        thing to update, deliberately, the day that changes."""
        for instrument in INSTRUMENT_REGISTRY.values():
            self.assertEqual(instrument.asset_class, ASSET_CLASS_CRYPTO)

    def test_btc_usdt_has_correct_currency_metadata(self):
        instrument = get_instrument("BTC/USDT")
        self.assertIsNotNone(instrument)
        self.assertEqual(instrument.base_currency, "BTC")
        self.assertEqual(instrument.quote_currency, "USDT")
        self.assertEqual(instrument.venue, "binance")
        self.assertEqual(instrument.timeframe, "1h")

    def test_unknown_symbol_returns_none_not_a_guess(self):
        self.assertIsNone(get_instrument("AAPL"))
        self.assertIsNone(get_instrument("EUR/USD"))

    def test_list_instruments_filters_by_asset_class(self):
        crypto = list_instruments(ASSET_CLASS_CRYPTO)
        forex = list_instruments(ASSET_CLASS_FOREX)
        equity = list_instruments(ASSET_CLASS_US_EQUITY)
        self.assertEqual(len(crypto), len(INSTRUMENT_REGISTRY))
        self.assertEqual(forex, [])
        self.assertEqual(equity, [])

    def test_symbols_for_asset_class_matches_fetch_all_symbols(self):
        """claude code changed: the exact backward-compatibility guarantee
        — spec.py's SUPPORTED_ASSETS must still be the same 20 crypto
        symbols it always was, just sourced from the registry now instead
        of importing SYMBOLS directly."""
        from bot.fetch_all_symbols import SYMBOLS
        self.assertEqual(symbols_for_asset_class(ASSET_CLASS_CRYPTO), list(SYMBOLS))

    def test_all_three_target_asset_classes_are_real_values(self):
        """CRYPTO/US_EQUITY/FOREX must all be valid, branchable values —
        even though only CRYPTO has data — per the refactor brief's
        primary business requirement."""
        self.assertEqual(set(ASSET_CLASSES), {ASSET_CLASS_CRYPTO, ASSET_CLASS_US_EQUITY, ASSET_CLASS_FOREX})


class ResolveOhlcvPathTest(SimpleTestCase):

    def test_resolves_to_the_existing_unrenamed_csv_file(self):
        """claude code changed: the refactor brief's explicit instruction
        — no existing data file may be renamed. BTC/USDT must still
        resolve to data/BTC_USDT_1h.csv, byte-identical to the path
        fetch_all_symbols.symbol_to_filename() has always produced."""
        path = resolve_ohlcv_path("BTC/USDT")
        self.assertEqual(path.name, "BTC_USDT_1h.csv")
        self.assertTrue(path.exists())

    def test_unregistered_symbol_fails_closed(self):
        with self.assertRaises(UnknownInstrumentError):
            resolve_ohlcv_path("NOT_A_REAL_SYMBOL/USDT")

    def test_non_crypto_asset_class_fails_closed_not_guessed(self):
        """claude code changed: even if a caller manually constructs an
        Instrument for a non-CRYPTO asset class, resolve_ohlcv_path()
        must still refuse rather than guess a filename — there has never
        been a real data source for any other asset class."""
        fake_registry_entry = Instrument(canonical_symbol="AAPL", asset_class=ASSET_CLASS_US_EQUITY)
        self.assertEqual(fake_registry_entry.asset_class, ASSET_CLASS_US_EQUITY)
        with self.assertRaises(UnknownInstrumentError):
            resolve_ohlcv_path("AAPL")  # not in INSTRUMENT_REGISTRY at all — the real, current state


class CandlesToWallClockTest(SimpleTestCase):
    """claude code changed: new — Multi-Asset Foundation Refactor Phase
    1B, Objective 1. The single candles<->wall-clock-time boundary every
    research engine's timeframe-aware quantities (half-life, decay,
    rolling windows) should route through, rather than each inventing its
    own hours-per-candle assumption. Examples are the exact ones the
    Phase 1B brief itself specifies."""

    def test_1h_data_reports_hours_one_to_one(self):
        self.assertEqual(candles_to_wall_clock(12, "1h"), (12.0, "hours"))

    def test_4h_data_scales_hours_by_4(self):
        self.assertEqual(candles_to_wall_clock(12, "4h"), (48.0, "hours"))

    def test_1d_data_reports_days_one_to_one(self):
        self.assertEqual(candles_to_wall_clock(12, "1d"), (12.0, "days"))

    def test_sub_hour_timeframes_report_minutes(self):
        self.assertEqual(candles_to_wall_clock(30, "5m"), (150.0, "minutes"))
        self.assertEqual(candles_to_wall_clock(4, "15m"), (60.0, "minutes"))

    def test_unknown_timeframe_fails_closed_never_guesses(self):
        with self.assertRaises(UnsupportedTimeframeError):
            candles_to_wall_clock(12, "2h")

    def test_infinite_candles_converts_without_crashing(self):
        value, unit = candles_to_wall_clock(float("inf"), "1h")
        self.assertEqual(value, float("inf"))
        self.assertEqual(unit, "hours")
