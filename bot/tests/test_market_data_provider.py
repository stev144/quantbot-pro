# ============================================================
# bot/tests/test_market_data_provider.py
# Data-Layer Audit — Step 2.
#
# claude code changed: new file. Confirms the MarketDataProvider contract
# is enforced in both directions (an incomplete subclass can't be
# instantiated, a complete one can — the exact pattern
# bot/tests/test_exchange_adapter.py already uses for ExchangeAdapter),
# and that CanonicalBars/capabilities()/require() behave as documented.
# No real provider is wrapped yet (that's Step 3/4) — a tiny in-memory
# dummy provider proves the contract itself, same approach
# test_exchange_adapter.py's own DummyAdapter uses.
# ============================================================

from datetime import datetime, timezone

import pandas as pd
from django.test import SimpleTestCase

from bot.instruments import get_instrument
from bot.market_data_provider import (
    REQUIRED_BAR_COLUMNS,
    CanonicalBars,
    DataCapability,
    InvalidCanonicalBarsError,
    MarketDataProvider,
    UnsupportedCapabilityError,
)
from bot.research_lab.data_fingerprint import DatasetIdentity


def _make_identity(row_count: int) -> DatasetIdentity:
    return DatasetIdentity(
        source="dummy_test_source", symbol="BTC/USDT", venue="dummy",
        timeframe="1h", start_date="2026-01-01", end_date="2026-01-02",
        row_count=row_count,
    )


class DummyProvider(MarketDataProvider):
    provider_id = "dummy"

    def capabilities(self):
        return frozenset({DataCapability.HISTORICAL_BARS})

    def get_historical_bars(self, instrument, timeframe, start, end):
        df = pd.DataFrame({
            "timestamp": pd.to_datetime(["2026-01-01T00:00:00Z"]),
            "open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05], "volume": [100.0],
        })
        return CanonicalBars(data=df, instrument=instrument, timeframe=timeframe, identity=_make_identity(len(df)))


class MarketDataProviderContractTest(SimpleTestCase):

    def test_cannot_instantiate_the_bare_interface(self):
        with self.assertRaises(TypeError):
            MarketDataProvider()

    def test_incomplete_subclass_cannot_be_instantiated(self):
        class IncompleteProvider(MarketDataProvider):
            provider_id = "incomplete"
            # get_historical_bars/capabilities deliberately left unimplemented

        with self.assertRaises(TypeError):
            IncompleteProvider()

    def test_fully_implemented_subclass_can_be_instantiated_and_fetches(self):
        provider = DummyProvider()
        instrument = get_instrument("BTC/USDT")
        bars = provider.get_historical_bars(
            instrument, "1h",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        self.assertIsInstance(bars, CanonicalBars)
        self.assertEqual(bars.instrument.canonical_symbol, "BTC/USDT")
        self.assertEqual(bars.timeframe, "1h")

    def test_supports_and_require(self):
        provider = DummyProvider()
        self.assertTrue(provider.supports(DataCapability.HISTORICAL_BARS))
        self.assertFalse(provider.supports(DataCapability.BID_ASK))
        provider.require(DataCapability.HISTORICAL_BARS)  # must not raise
        with self.assertRaises(UnsupportedCapabilityError):
            provider.require(DataCapability.BID_ASK)


class CanonicalBarsTest(SimpleTestCase):

    def test_missing_required_column_fails_closed(self):
        df = pd.DataFrame({"timestamp": [1], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]})  # no volume
        instrument = get_instrument("BTC/USDT")
        with self.assertRaises(InvalidCanonicalBarsError):
            CanonicalBars(data=df, instrument=instrument, timeframe="1h", identity=_make_identity(1))

    def test_all_required_columns_present_constructs_cleanly(self):
        df = pd.DataFrame({c: [1] for c in REQUIRED_BAR_COLUMNS})
        instrument = get_instrument("BTC/USDT")
        bars = CanonicalBars(data=df, instrument=instrument, timeframe="1h", identity=_make_identity(1))
        self.assertEqual(bars.optional_columns_present(), frozenset())

    def test_optional_columns_present_reports_real_capability(self):
        df = pd.DataFrame({c: [1] for c in REQUIRED_BAR_COLUMNS})
        df["tick_volume"] = [500]
        instrument = get_instrument("BTC/USDT")
        bars = CanonicalBars(data=df, instrument=instrument, timeframe="1h", identity=_make_identity(1))
        self.assertEqual(bars.optional_columns_present(), frozenset({"tick_volume"}))

    def test_fingerprint_delegates_to_dataset_identity(self):
        df = pd.DataFrame({c: [1] for c in REQUIRED_BAR_COLUMNS})
        instrument = get_instrument("BTC/USDT")
        identity = _make_identity(1)
        bars = CanonicalBars(data=df, instrument=instrument, timeframe="1h", identity=identity)
        self.assertEqual(bars.fingerprint(), identity.fingerprint())

    def test_two_instances_are_identity_compared_not_dataframe_compared(self):
        """claude code changed: new — proves the eq=False choice on
        CanonicalBars actually works: comparing two instances (or hashing
        one) must not try to == a DataFrame, which raises
        "truth value of a DataFrame is ambiguous"."""
        df = pd.DataFrame({c: [1] for c in REQUIRED_BAR_COLUMNS})
        instrument = get_instrument("BTC/USDT")
        identity = _make_identity(1)
        a = CanonicalBars(data=df, instrument=instrument, timeframe="1h", identity=identity)
        b = CanonicalBars(data=df, instrument=instrument, timeframe="1h", identity=identity)
        self.assertNotEqual(a, b)  # distinct objects, identity-compared — must not raise
        self.assertEqual(a, a)
        hash(a)  # must not raise
