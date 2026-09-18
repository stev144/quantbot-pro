# ============================================================
# bot/tests/test_binance_klines_provider.py
# Data-Layer Audit — Step 3.
#
# claude code changed: new file. No mocking, per this project's testing
# convention — uses a real, small, recent window against live Binance.
# Proves BinanceKlinesProvider wraps bot.data_fetcher.get_klines_by_date()
# WITHOUT changing its output: the exact behavior-preservation guarantee
# this step exists to prove (Step 3's own stated goal).
# ============================================================

from datetime import datetime, timedelta, timezone

import pandas as pd
from django.test import SimpleTestCase

from bot import data_fetcher
from bot.binance_klines_provider import BinanceKlinesProvider
from bot.instruments import get_instrument
from bot.market_data_provider import (
    REQUIRED_BAR_COLUMNS,
    CanonicalBars,
    DataCapability,
    MarketDataProvider,
    UnsupportedCapabilityError,  # claude code changed: Data-Layer Audit Step 6
)


class BinanceKlinesProviderContractTest(SimpleTestCase):

    def test_is_a_valid_market_data_provider(self):
        provider = BinanceKlinesProvider()
        self.assertIsInstance(provider, MarketDataProvider)
        self.assertEqual(provider.provider_id, "binance")

    def test_declares_only_historical_bars(self):
        """claude code changed: new — Binance's raw kline response has no
        bid/ask/tick_volume/real_volume field (confirmed by
        data_fetcher._build_ohlcv_dataframe()'s fixed column list)."""
        provider = BinanceKlinesProvider()
        self.assertEqual(provider.capabilities(), frozenset({DataCapability.HISTORICAL_BARS}))
        self.assertFalse(provider.supports(DataCapability.BID_ASK))
        self.assertFalse(provider.supports(DataCapability.TICK_VOLUME))

    def test_requesting_bid_ask_fails_explicitly_not_silently(self):
        """claude code changed: new — Data-Layer Audit Step 6 (the audit
        brief's own "Test 7 — Capability failure"): a caller that requires
        a capability this provider doesn't have must get a clear,
        catchable error, never fabricated/default bid-ask data."""
        provider = BinanceKlinesProvider()
        with self.assertRaises(UnsupportedCapabilityError):
            provider.require(DataCapability.BID_ASK)


class BinanceKlinesProviderLiveEquivalenceTest(SimpleTestCase):
    """claude code changed: new — the actual Step 3 proof: the provider
    and a direct data_fetcher call, against the identical real window,
    must return byte-identical output."""

    def setUp(self):
        self.end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        self.start = self.end - timedelta(hours=6)

    def test_returns_byte_identical_ohlcv_to_direct_data_fetcher_call(self):
        provider = BinanceKlinesProvider()
        instrument = get_instrument("BTC/USDT")

        bars = provider.get_historical_bars(instrument, "1h", self.start, self.end)
        direct = data_fetcher.get_klines_by_date("BTC/USDT", "1h", self.start, self.end)

        self.assertIsInstance(bars, CanonicalBars)
        self.assertGreater(len(bars.data), 0)
        # claude code changed: was trimmed to REQUIRED_BAR_COLUMNS on both
        # sides — now a stronger, full-column equivalence check, matching
        # the provider's revised "don't drop real extra columns" behavior
        # (see binance_klines_provider.py's module docstring).
        pd.testing.assert_frame_equal(
            bars.data.reset_index(drop=True), direct.reset_index().reset_index(drop=True)
        )

    def test_required_columns_are_a_subset_of_the_full_result(self):
        """claude code changed: new — CanonicalBars still guarantees
        REQUIRED_BAR_COLUMNS are present even though the provider no
        longer trims down to exactly that set."""
        provider = BinanceKlinesProvider()
        instrument = get_instrument("BTC/USDT")
        bars = provider.get_historical_bars(instrument, "1h", self.start, self.end)
        for col in REQUIRED_BAR_COLUMNS:
            self.assertIn(col, bars.data.columns)
        # claude code changed: new — the real extra columns
        # fetch_all_symbols.py's prepare_dataframe_for_csv() depends on
        # must still be there; this is the regression this change exists
        # to prevent.
        for col in ("qav", "num_trades", "taker_base_vol"):
            self.assertIn(col, bars.data.columns)

    def test_provenance_identity_matches_the_real_request(self):
        provider = BinanceKlinesProvider()
        instrument = get_instrument("BTC/USDT")
        bars = provider.get_historical_bars(instrument, "1h", self.start, self.end)
        self.assertEqual(bars.identity.venue, "binance")
        self.assertEqual(bars.identity.symbol, "BTC/USDT")
        self.assertEqual(bars.identity.row_count, len(bars.data))
        self.assertEqual(bars.fingerprint(), bars.identity.fingerprint())

    def test_empty_result_still_has_canonical_shape(self):
        """claude code changed: new — a window before Binance existed
        (deterministic empty result, no live-network flakiness risk) must
        still produce a valid, correctly-shaped CanonicalBars, not a bare
        empty frame CanonicalBars.__post_init__ would reject."""
        provider = BinanceKlinesProvider()
        instrument = get_instrument("BTC/USDT")
        far_past_start = datetime(2005, 1, 1, tzinfo=timezone.utc)
        far_past_end = datetime(2005, 1, 2, tzinfo=timezone.utc)

        bars = provider.get_historical_bars(instrument, "1h", far_past_start, far_past_end)
        self.assertEqual(len(bars.data), 0)
        self.assertEqual(list(bars.data.columns), list(REQUIRED_BAR_COLUMNS))
