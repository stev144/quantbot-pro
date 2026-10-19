# claude code changed: new file — Kraken Multi-Venue Execution, Step 8.
# Tests the new per-venue execution cost table and lookup helper.
# claude code changed: market-agnostic infrastructure (2026-10-18) — added ForexCostProfile tests

from django.test import SimpleTestCase

from bot.config.execution_costs import (
    FEE_RATE, SLIPPAGE_RATE, get_venue_execution_costs,
    get_forex_execution_costs, ForexCostProfile,
)


class GetVenueExecutionCostsTest(SimpleTestCase):

    def test_binance_returns_default_global_rates(self):
        costs = get_venue_execution_costs("binance")
        self.assertEqual(costs["fee_rate"], FEE_RATE)
        self.assertEqual(costs["slippage_rate"], SLIPPAGE_RATE)

    def test_kraken_returns_its_own_fee_rate(self):
        costs = get_venue_execution_costs("kraken")
        self.assertEqual(costs["fee_rate"], 0.0026)
        self.assertEqual(costs["slippage_rate"], SLIPPAGE_RATE)

    def test_unknown_venue_falls_back_to_default(self):
        costs = get_venue_execution_costs("ibkr")
        self.assertEqual(costs["fee_rate"], FEE_RATE)
        self.assertEqual(costs["slippage_rate"], SLIPPAGE_RATE)


class ForexCostProfileTest(SimpleTestCase):  # claude code changed: market-agnostic infrastructure (2026-10-18)
    """Indicative forex profiles are registered at import time; swap rates intentionally None."""

    def test_eurusd_profile_registered(self):
        profile = get_forex_execution_costs("EUR/USD")
        self.assertIsInstance(profile, ForexCostProfile)
        self.assertEqual(profile.instrument_id, "EUR/USD")

    def test_eurusd_spread_is_nonzero(self):
        profile = get_forex_execution_costs("EUR/USD")
        self.assertIsNotNone(profile.spread_pips)
        self.assertGreater(profile.spread_pips, 0.0)

    def test_eurusd_commission_is_zero(self):
        # No-commission retail spot account assumption
        profile = get_forex_execution_costs("EUR/USD")
        self.assertEqual(profile.commission_per_lot, 0.0)

    def test_eurusd_cost_complete_false_until_swap_rates_added(self):
        # Swap rates are broker-specific and intentionally None — cost_complete must reflect this
        profile = get_forex_execution_costs("EUR/USD")
        self.assertFalse(profile.cost_complete)

    def test_eurusd_spread_within_retail_range(self):
        # Sanity check: typical retail spread 0.3-2.0 pips
        profile = get_forex_execution_costs("EUR/USD")
        self.assertGreaterEqual(profile.spread_pips, 0.3)
        self.assertLessEqual(profile.spread_pips, 2.0)

    def test_unknown_instrument_returns_placeholder_cost_complete_false(self):
        profile = get_forex_execution_costs("XAU/USD")
        self.assertIsInstance(profile, ForexCostProfile)
        self.assertFalse(profile.cost_complete)

    def test_registered_pairs_include_majors(self):
        for pair in ("EUR/USD", "GBP/USD", "USD/JPY"):
            with self.subTest(pair=pair):
                profile = get_forex_execution_costs(pair)
                self.assertIsNotNone(profile.spread_pips)

    def test_crypto_path_unchanged(self):
        # get_venue_execution_costs still returns crypto rates unaffected
        costs = get_venue_execution_costs("binance")
        self.assertEqual(costs["fee_rate"], FEE_RATE)
