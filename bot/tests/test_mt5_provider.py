# ============================================================
# bot/tests/test_mt5_provider.py
# Data-Layer Audit — Step 4b.
#
# claude code changed: new file. No MT5 terminal/account exists in this
# environment (confirmed: MT5_ENABLED is unset), matching
# bot/mt5_data_fetcher.py's own honestly-disclosed "untested against a
# live terminal" status. This file proves what CAN be proven without one:
# the contract is satisfied, capabilities are declared correctly, and —
# the one real, deterministic, no-network behavior available here — that
# an unconfigured MT5 connection fails with a clear MT5ConnectionError
# rather than a silent empty result. A live-terminal equivalence test
# (matching test_binance_klines_provider.py's/test_yahoo_forex_provider.py's
# pattern) should be added the day a real demo account is actually wired
# in, not faked here.
# ============================================================

import os

from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from bot.instruments import get_instrument
from bot.market_data_provider import (
    DataCapability,
    MarketDataProvider,
    UnsupportedCapabilityError,  # claude code changed: Data-Layer Audit Step 6
)
from bot.mt5_data_fetcher import MT5ConnectionError
from bot.mt5_provider import MT5Provider


class MT5ProviderContractTest(SimpleTestCase):

    def test_is_a_valid_market_data_provider(self):
        provider = MT5Provider()
        self.assertIsInstance(provider, MarketDataProvider)
        self.assertEqual(provider.provider_id, "mt5")

    def test_declares_only_historical_bars_not_tick_volume(self):
        """claude code changed: new — MT5's real tick_volume is folded
        into the REQUIRED 'volume' column by get_mt5_klines() rather than
        exposed as a distinct optional column, so this provider must NOT
        claim DataCapability.TICK_VOLUME — see mt5_provider.py's own
        module docstring."""
        provider = MT5Provider()
        self.assertEqual(provider.capabilities(), frozenset({DataCapability.HISTORICAL_BARS}))
        self.assertFalse(provider.supports(DataCapability.TICK_VOLUME))
        self.assertFalse(provider.supports(DataCapability.BID_ASK))

    def test_requesting_tick_volume_fails_explicitly_not_silently(self):
        """claude code changed: new — Data-Layer Audit Step 6 ("Test 7 —
        Capability failure"). This must raise even though MT5 genuinely
        HAS real tick data underneath — it isn't exposed as a distinct
        tick_volume column today (see this provider's own module
        docstring), so claiming the capability would be a lie regardless
        of the underlying data's real richness. No network/connection
        needed — require() checks the declared capability set, never
        attempts a connection."""
        provider = MT5Provider()
        with self.assertRaises(UnsupportedCapabilityError):
            provider.require(DataCapability.TICK_VOLUME)


class MT5ProviderUnconfiguredConnectionTest(SimpleTestCase):
    """claude code changed: new — the one real, deterministic, no-network
    behavior verifiable in an environment with no MT5 terminal: a request
    against an unconfigured MT5 connection must fail loudly, never
    silently return empty/fabricated data."""

    def test_fails_closed_when_mt5_is_not_enabled(self):
        self.assertIsNone(os.getenv("MT5_ENABLED"))  # documents the precondition this test relies on
        provider = MT5Provider()
        instrument = get_instrument("EUR/USD")
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=6)
        with self.assertRaises(MT5ConnectionError):
            provider.get_historical_bars(instrument, "1h", start, end)
