# ============================================================
# bot/tests/test_yahoo_forex_provider.py
# Data-Layer Audit — Step 4a.
#
# claude code changed: new file. No mocking, per this project's testing
# convention — uses a real, small window against Yahoo's live public
# chart endpoint. Proves YahooForexProvider wraps
# bot.forex_data_fetcher.get_forex_klines() correctly, filters to the
# requested window, and fails closed (not silently) for an unsupported
# timeframe.
# ============================================================

from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from bot.instruments import get_instrument
from bot.market_data_provider import (
    CanonicalBars,
    DataCapability,
    MarketDataProvider,
    UnsupportedCapabilityError,  # claude code changed: Data-Layer Audit Step 6
)
from bot.yahoo_forex_provider import YahooForexProvider


class YahooForexProviderContractTest(SimpleTestCase):

    def test_is_a_valid_market_data_provider(self):
        provider = YahooForexProvider()
        self.assertIsInstance(provider, MarketDataProvider)
        self.assertEqual(provider.provider_id, "yahoo_finance")

    def test_declares_only_historical_bars(self):
        provider = YahooForexProvider()
        self.assertEqual(provider.capabilities(), frozenset({DataCapability.HISTORICAL_BARS}))
        self.assertFalse(provider.supports(DataCapability.BID_ASK))

    def test_requesting_bid_ask_fails_explicitly_not_silently(self):
        """claude code changed: new — Data-Layer Audit Step 6 ("Test 7 —
        Capability failure"). Yahoo's public FX chart endpoint has no
        bid/ask field at all — this must raise, never return a fabricated
        or zero-filled bid/ask column."""
        provider = YahooForexProvider()
        with self.assertRaises(UnsupportedCapabilityError):
            provider.require(DataCapability.BID_ASK)

    def test_unsupported_timeframe_fails_closed(self):
        """claude code changed: new — "4h" is a real canonical timeframe
        (bot.instruments.TIMEFRAME_MINUTES_PER_CANDLE has it) but Yahoo's
        chart endpoint doesn't natively serve it for FX; this must raise,
        never silently send an interval Yahoo might misinterpret. No
        network call should happen — the guard runs before any fetch."""
        provider = YahooForexProvider()
        instrument = get_instrument("EUR/USD")
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=6)
        with self.assertRaises(ValueError):
            provider.get_historical_bars(instrument, "4h", start, end)


class YahooForexProviderLiveEquivalenceTest(SimpleTestCase):
    """claude code changed: new — a real week-long window (wide enough to
    survive a weekend closure without returning zero rows) against live
    Yahoo Finance."""

    def setUp(self):
        self.end = datetime.now(timezone.utc)
        self.start = self.end - timedelta(days=7)

    def test_returns_real_data_filtered_to_the_requested_window(self):
        provider = YahooForexProvider()
        instrument = get_instrument("EUR/USD")

        bars = provider.get_historical_bars(instrument, "1h", self.start, self.end)

        self.assertIsInstance(bars, CanonicalBars)
        self.assertGreater(len(bars.data), 0)
        self.assertTrue((bars.data["timestamp"] >= self.start).all())
        self.assertTrue((bars.data["timestamp"] <= self.end).all())

    def test_provenance_identity_matches_the_real_request(self):
        provider = YahooForexProvider()
        instrument = get_instrument("EUR/USD")
        bars = provider.get_historical_bars(instrument, "1h", self.start, self.end)
        self.assertEqual(bars.identity.venue, "yahoo_finance")
        self.assertEqual(bars.identity.symbol, "EUR/USD")
        self.assertEqual(bars.identity.row_count, len(bars.data))
