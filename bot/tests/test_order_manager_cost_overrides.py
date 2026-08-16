# claude code changed: new file — Kraken Multi-Venue Execution, Step 8.
# Confirms OrderManager's new optional fee_rate/slippage_rate constructor
# params: omitted -> unchanged global-default behavior (backward
# compatibility for ExecutionEngine's existing construction), given ->
# actually used in simulated dry-run fills.

from django.test import SimpleTestCase

from bot.config.execution_costs import FEE_RATE, SLIPPAGE_RATE
from bot.engines.order_manager import OrderManager


class OrderManagerCostOverrideDefaultsTest(SimpleTestCase):

    def test_no_overrides_uses_global_defaults(self):
        om = OrderManager(exchange=None, dry_run=True)
        self.assertEqual(om.fee_rate, FEE_RATE)
        self.assertEqual(om.slippage_rate, SLIPPAGE_RATE)

    def test_overrides_are_used_verbatim(self):
        om = OrderManager(exchange=None, dry_run=True, fee_rate=0.0026, slippage_rate=0.0009)
        self.assertEqual(om.fee_rate, 0.0026)
        self.assertEqual(om.slippage_rate, 0.0009)


class OrderManagerCostOverrideSimulatedFillTest(SimpleTestCase):

    def test_overridden_rates_actually_drive_the_simulated_fill(self):
        # claude code changed: price=50000 (not None) so _simulate_fill()
        # never needs a real ticker fetch — exchange=None is safe here.
        om = OrderManager(exchange=None, dry_run=True, fee_rate=0.0026, slippage_rate=0.001)
        result = om.place_order("BTC/USDT", "buy", 1.0, price=50000.0)

        expected_fill_price = round(50000.0 * (1 + 0.001), 8)
        expected_fee = round(expected_fill_price * 1.0 * 0.0026, 6)

        self.assertEqual(result["fill_price"], expected_fill_price)
        self.assertEqual(result["fee_usdt"], expected_fee)

    def test_default_rates_match_global_constants_in_a_simulated_fill(self):
        om = OrderManager(exchange=None, dry_run=True)
        result = om.place_order("BTC/USDT", "buy", 1.0, price=50000.0)

        expected_fill_price = round(50000.0 * (1 + SLIPPAGE_RATE), 8)
        expected_fee = round(expected_fill_price * 1.0 * FEE_RATE, 6)

        self.assertEqual(result["fill_price"], expected_fill_price)
        self.assertEqual(result["fee_usdt"], expected_fee)
