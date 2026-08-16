# claude code changed: new file — Kraken Multi-Venue Execution, Step 8.
# Tests the new per-venue execution cost table and lookup helper.

from django.test import SimpleTestCase

from bot.config.execution_costs import FEE_RATE, SLIPPAGE_RATE, get_venue_execution_costs


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
