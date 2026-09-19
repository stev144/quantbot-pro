# claude code changed: new file — Multi-Asset Foundation Refactor Phase
# 1A. Tests for bot/instruments.py, the instrument identity + canonical
# symbol boundary (STEP 1) and the provider-path resolution boundary
# (STEP 2). No mocking, per this project's convention — reads the real
# data/BTC_USDT_1h.csv file exactly like the Research Lab tool layer does.

import json  # claude code changed: Data-Layer Audit Step 5 — read raw marker JSON to check the fingerprint section
import os  # claude code changed: Data-Layer Audit Step 1 — cleanup for provenance marker tests
from pathlib import Path  # claude code changed: Data-Layer Audit Step 1

from django.test import SimpleTestCase

from bot import instruments  # claude code changed: Data-Layer Audit Step 1 — need module access for _build_forex_registry()
from bot.research_lab.data_fingerprint import DatasetIdentity  # claude code changed: Data-Layer Audit Step 5 — recompute the expected fingerprint independently
from bot.instruments import (
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_FOREX,
    ASSET_CLASS_US_EQUITY,
    ASSET_CLASSES,
    DATA_DIR,  # claude code changed: Data-Layer Audit Step 1
    INSTRUMENT_REGISTRY,
    Instrument,
    UnknownInstrumentError,
    UnsupportedTimeframeError,
    candles_to_wall_clock,
    forex_symbol_to_filename,  # claude code changed: Data-Layer Audit Step 1
    get_instrument,
    list_instruments,
    read_provenance_marker,  # claude code changed: Data-Layer Audit Step 1
    resolve_ohlcv_path,
    symbols_for_asset_class,
    write_provenance_marker,  # claude code changed: Data-Layer Audit Step 1
)


class InstrumentRegistryTest(SimpleTestCase):

    def test_every_registry_entry_is_crypto_or_forex_today(self):
        """claude code changed: was test_every_registry_entry_is_crypto_today,
        asserting every entry is CRYPTO — this test's OWN prior docstring
        predicted this exact day ("this test should be the FIRST thing to
        update, deliberately, the day that changes") but was never
        actually updated when the Forex Multi-Asset Integration mission
        added real FOREX rows — a real, previously-undiscovered gap only
        surfaced now that a full-suite run finally completed. US_EQUITY
        genuinely remains unpopulated — still real, still honest to assert."""
        for instrument in INSTRUMENT_REGISTRY.values():
            self.assertIn(instrument.asset_class, (ASSET_CLASS_CRYPTO, ASSET_CLASS_FOREX))
            self.assertNotEqual(instrument.asset_class, ASSET_CLASS_US_EQUITY)

    def test_btc_usdt_has_correct_currency_metadata(self):
        instrument = get_instrument("BTC/USDT")
        self.assertIsNotNone(instrument)
        self.assertEqual(instrument.base_currency, "BTC")
        self.assertEqual(instrument.quote_currency, "USDT")
        self.assertEqual(instrument.venue, "binance")
        self.assertEqual(instrument.timeframe, "1h")

    def test_eur_usd_has_correct_currency_metadata(self):
        """claude code changed: new — the Forex-registry equivalent of the
        BTC/USDT check above, which this file never gained when FOREX
        rows were added."""
        instrument = get_instrument("EUR/USD")
        self.assertIsNotNone(instrument)
        self.assertEqual(instrument.base_currency, "EUR")
        self.assertEqual(instrument.quote_currency, "USD")
        self.assertEqual(instrument.venue, "yahoo_finance")
        self.assertEqual(instrument.timeframe, "1h")

    def test_unknown_symbol_returns_none_not_a_guess(self):
        """claude code changed: was self.assertIsNone(get_instrument("EUR/USD"))
        — EUR/USD is a real, registered FOREX instrument now, not an
        unknown symbol; asserting it's None was itself the stale check.
        AAPL (a real, genuinely unregistered US_EQUITY symbol — no
        US_EQUITY data has ever been ingested) is still a correct example."""
        self.assertIsNone(get_instrument("AAPL"))
        self.assertIsNone(get_instrument("NOT_A_REAL_SYMBOL/USDT"))

    def test_list_instruments_filters_by_asset_class(self):
        """claude code changed: was asserting forex==[] and crypto==the
        WHOLE registry — both false since real FOREX rows exist. Counts
        match this platform's own real, current universes
        (bot.fetch_all_symbols.SYMBOLS / bot.forex_data_fetcher.SYMBOLS)
        rather than a hardcoded assumption that predates either.

        claude code changed: was a hardcoded `len(forex) == 8` — stale
        the moment the Forex universe was widened to the complete 28-pair
        G8-currency cross matrix (closing the USD-overlap structural
        finding from the cross-sectional research audit). Compares
        against the real registry-derived universe size instead of a
        frozen literal, so this test survives any future universe
        change without needing another manual edit."""
        crypto = list_instruments(ASSET_CLASS_CRYPTO)
        forex = list_instruments(ASSET_CLASS_FOREX)
        equity = list_instruments(ASSET_CLASS_US_EQUITY)
        self.assertEqual(len(crypto) + len(forex), len(INSTRUMENT_REGISTRY))
        self.assertEqual(len(forex), len(symbols_for_asset_class(ASSET_CLASS_FOREX)))
        self.assertGreaterEqual(len(forex), 8)  # never fewer than the documented default majors
        self.assertTrue(all(i.asset_class == ASSET_CLASS_FOREX for i in forex))
        self.assertEqual(equity, [])

    def test_symbols_for_asset_class_matches_fetch_all_symbols(self):
        """claude code changed: was a strict equality — the exact backward-
        compatibility guarantee that spec.py's SUPPORTED_ASSETS is sourced
        from the registry, not a second hand-typed copy of SYMBOLS. Relaxed
        to "every live SYMBOLS entry is present, in the same relative order"
        (assertEqual on the SYMBOLS-only subsequence) now that the registry
        also carries bot.instruments.RESEARCH_ONLY_CRYPTO_SYMBOLS — real
        instruments with real historical data, deliberately outside SYMBOLS
        itself (see that constant's own comment: they're not part of the
        live, freshness-monitored universe, so they don't belong in SYMBOLS,
        but bot.instruments still needs to resolve their identity for
        already-produced research artifacts that reference them)."""
        from bot.fetch_all_symbols import SYMBOLS
        from bot.instruments import RESEARCH_ONLY_CRYPTO_SYMBOLS
        registry_symbols = symbols_for_asset_class(ASSET_CLASS_CRYPTO)
        self.assertEqual([s for s in registry_symbols if s not in RESEARCH_ONLY_CRYPTO_SYMBOLS], list(SYMBOLS))
        self.assertEqual(len(registry_symbols), len(SYMBOLS) + len(RESEARCH_ONLY_CRYPTO_SYMBOLS))

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


class InstrumentMarketMetadataTest(SimpleTestCase):
    """claude code changed: new — Forex Integration Stage 1
    (Architectural Parity). Covers the new, optional, asset-specific
    Instrument fields (pip_size/contract_size/min_lot/lot_step/
    swap_long/swap_short/trading_sessions/broker_server/execution_mode)."""

    def test_crypto_instrument_has_all_new_fields_none(self):
        from bot.instruments import get_instrument
        btc = get_instrument("BTC/USDT")
        self.assertIsNotNone(btc)
        for field in ("pip_size", "contract_size", "min_lot", "lot_step",
                      "swap_long", "swap_short", "trading_sessions", "broker_server", "execution_mode"):
            self.assertIsNone(getattr(btc, field), f"CRYPTO instrument unexpectedly has {field} set")

    def test_forex_non_jpy_pair_uses_the_default_pip_size(self):
        from bot.instruments import get_instrument
        eurusd = get_instrument("EUR/USD")
        self.assertEqual(eurusd.pip_size, 0.0001)
        self.assertEqual(eurusd.contract_size, 100_000.0)
        self.assertEqual(eurusd.min_lot, 0.01)
        self.assertEqual(eurusd.lot_step, 0.01)

    def test_forex_jpy_quoted_pair_uses_the_larger_pip_size(self):
        from bot.instruments import get_instrument
        usdjpy = get_instrument("USD/JPY")
        self.assertEqual(usdjpy.pip_size, 0.01)

    def test_broker_specific_fields_are_honestly_none_pending_stage_2(self):
        """claude code changed: these must never be guessed — a real
        broker/MT5 connection (Stage 2) is required to populate them
        honestly."""
        from bot.instruments import get_instrument
        eurusd = get_instrument("EUR/USD")
        self.assertIsNone(eurusd.swap_long)
        self.assertIsNone(eurusd.swap_short)
        self.assertIsNone(eurusd.trading_sessions)
        self.assertIsNone(eurusd.broker_server)
        self.assertIsNone(eurusd.execution_mode)


class ProvenanceMarkerTest(SimpleTestCase):
    """claude code changed: new — Data-Layer Audit Step 1. Proves
    bot/instruments.py's FOREX registry reports the REAL fetcher that
    wrote a symbol's CSV (via a sidecar marker) instead of a hardcoded
    "yahoo_finance" literal — the fix needed before bot/mt5_data_fetcher.py
    can safely overwrite data/forex/*.csv files without every downstream
    consumer silently keeping the stale Yahoo attribution. No mocking, per
    this project's convention: exercises real file I/O against a throwaway
    path, and against EUR/USD's real (but temporary, cleaned-up) marker."""

    def setUp(self):
        self.dummy_csv = Path("data/forex/__test_provenance_dummy_1h.csv")
        self.dummy_marker = self.dummy_csv.with_suffix(".provenance.json")
        self.eurusd_csv = Path(DATA_DIR) / "forex" / forex_symbol_to_filename("EUR/USD")
        self.eurusd_marker = self.eurusd_csv.with_suffix(".provenance.json")

    def tearDown(self):
        for marker in (self.dummy_marker, self.eurusd_marker):
            if marker.exists():
                os.remove(marker)

    def test_read_provenance_marker_returns_none_when_absent(self):
        self.assertIsNone(read_provenance_marker(self.dummy_csv))

    def test_write_then_read_round_trips(self):
        write_provenance_marker(self.dummy_csv, venue="mt5", data_source="mt5_data_fetcher")
        marker = read_provenance_marker(self.dummy_csv)
        self.assertEqual(marker, {"venue": "mt5", "data_source": "mt5_data_fetcher"})

    def test_forex_registry_falls_back_to_yahoo_when_no_marker_exists(self):
        """claude code changed: the byte-for-byte-preserved-behavior guarantee
        — every data/forex/*.csv written before this change has no marker."""
        self.assertFalse(self.eurusd_marker.exists())
        registry = instruments._build_forex_registry()
        self.assertEqual(registry["EUR/USD"].venue, "yahoo_finance")
        self.assertEqual(registry["EUR/USD"].data_source, "forex_data_fetcher")

    def test_forex_registry_reports_mt5_once_mt5_has_written_the_marker(self):
        """claude code changed: the actual bug this step fixes — once MT5
        writes a real marker for EUR/USD's CSV, the registry must reflect
        that immediately, never keep reporting the stale Yahoo default."""
        write_provenance_marker(self.eurusd_csv, venue="mt5", data_source="mt5_data_fetcher")
        registry = instruments._build_forex_registry()
        self.assertEqual(registry["EUR/USD"].venue, "mt5")
        self.assertEqual(registry["EUR/USD"].data_source, "mt5_data_fetcher")
        # claude code changed: every OTHER symbol is unaffected — this isn't
        # a global flag, it's per-file.
        self.assertEqual(registry["GBP/USD"].venue, "yahoo_finance")

    def test_marker_without_fingerprint_kwargs_has_no_fingerprint_section(self):
        """claude code changed: new — Data-Layer Audit Step 5. Backward-
        compatibility guarantee: a caller that only ever passes
        venue/data_source (every Step 1 call site, and any future one that
        genuinely can't supply the full identity) writes exactly the same
        shape as before — no "fingerprint" key at all, not a null/empty one."""
        write_provenance_marker(self.dummy_csv, venue="mt5", data_source="mt5_data_fetcher")
        with open(self.dummy_marker) as f:
            raw = json.load(f)
        self.assertNotIn("fingerprint", raw)
        self.assertNotIn("dataset_identity", raw)

    def test_marker_with_full_identity_embeds_a_real_fingerprint(self):
        """claude code changed: new — Data-Layer Audit Step 5. The actual
        fix: when a fetcher supplies the full dataset identity, the marker
        embeds a real sha256 fingerprint computed via the EXISTING
        DatasetIdentity.fingerprint() (bot/research_lab/data_fingerprint.py)
        — not a second, independently-invented hash."""
        write_provenance_marker(
            self.dummy_csv, venue="mt5", data_source="mt5_data_fetcher",
            source="mt5_terminal_ipc", symbol="EUR/USD", timeframe="1h",
            start_date="2026-01-01", end_date="2026-01-02", row_count=48,
        )
        with open(self.dummy_marker) as f:
            raw = json.load(f)
        expected_identity = DatasetIdentity(
            source="mt5_terminal_ipc", symbol="EUR/USD", venue="mt5", timeframe="1h",
            start_date="2026-01-01", end_date="2026-01-02", row_count=48,
        )
        self.assertEqual(raw["fingerprint"], expected_identity.fingerprint())
        self.assertEqual(raw["dataset_identity"]["row_count"], 48)

    def test_marker_with_partial_identity_kwargs_omits_fingerprint(self):
        """claude code changed: new — Data-Layer Audit Step 5. ALL identity
        fields are required together (a fingerprint computed from a
        partial identity would be actively misleading, not merely
        incomplete) — omitting even one (row_count here) must behave
        exactly like supplying none."""
        write_provenance_marker(
            self.dummy_csv, venue="mt5", data_source="mt5_data_fetcher",
            source="mt5_terminal_ipc", symbol="EUR/USD", timeframe="1h",
            start_date="2026-01-01", end_date="2026-01-02",
            # row_count deliberately omitted
        )
        with open(self.dummy_marker) as f:
            raw = json.load(f)
        self.assertNotIn("fingerprint", raw)
