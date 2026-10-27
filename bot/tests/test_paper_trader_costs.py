# bot/tests/test_paper_trader_costs.py  # claude code changed: 2026-10-27 — new: Paper Trader MT5 cost snapshot + drawdown %
"""The GBP/JPY MT5 cost snapshot reaches the Paper Trader only, and the drawdown % follows the owner rule."""
from django.test import SimpleTestCase

from bot.research.forex_instrument_spec import (
    PAPER_BROKER_COSTS,
    PAPER_COST_SNAPSHOT_SOURCE,
    QC_PORTFOLIO_SPECS,
    with_paper_broker_costs,
)
from bot.views.paper_trading import max_drawdown_pct


class PaperBrokerCostSnapshotTest(SimpleTestCase):
    def test_gbpjpy_gets_the_mt5_values(self):
        spec = with_paper_broker_costs("GBP/JPY", QC_PORTFOLIO_SPECS["GBP/JPY"])
        self.assertEqual(spec.spread_typical_pips, 1.5)
        self.assertEqual(spec.commission_per_lot, 0.0)
        self.assertEqual(spec.swap_long, -0.527)    # −5.27 points/day, 10 points per pip
        self.assertEqual(spec.swap_short, -3.583)   # −35.83 points/day
        self.assertEqual(spec.source, PAPER_COST_SNAPSHOT_SOURCE)
        self.assertTrue(spec.is_cost_complete())

    def test_shared_specs_are_not_changed(self):
        # the frozen DCA Engine page, walk-forward validator and live runner read QC_PORTFOLIO_SPECS directly
        shared = QC_PORTFOLIO_SPECS["GBP/JPY"]
        self.assertIsNone(shared.spread_typical_pips)
        self.assertIsNone(shared.swap_long)
        self.assertFalse(shared.is_cost_complete())

    def test_pairs_without_a_snapshot_are_returned_unchanged(self):
        for pair, spec in QC_PORTFOLIO_SPECS.items():
            if pair not in PAPER_BROKER_COSTS:
                self.assertIs(with_paper_broker_costs(pair, spec), spec)


class MaxDrawdownPctTest(SimpleTestCase):
    def test_percent_of_running_peak(self):
        eq = [{"d": "a", "p": 100.0}, {"d": "b", "p": 200.0}, {"d": "c", "p": 150.0}, {"d": "d", "p": 300.0}]
        self.assertEqual(max_drawdown_pct(eq), -25.0)   # 200 → 150

    def test_deeper_fall_against_a_smaller_peak_wins(self):
        eq = [{"d": "a", "p": 50.0}, {"d": "b", "p": 10.0}, {"d": "c", "p": 400.0}, {"d": "d", "p": 320.0}]
        self.assertEqual(max_drawdown_pct(eq), -80.0)   # 50 → 10, not 400 → 320 (−20%)

    def test_no_positive_peak_gives_none(self):
        self.assertIsNone(max_drawdown_pct([{"d": "a", "p": -5.0}, {"d": "b", "p": -9.0}]))
        self.assertIsNone(max_drawdown_pct([]))
