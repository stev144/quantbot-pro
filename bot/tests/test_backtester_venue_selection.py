# claude code changed: new file — Kraken Multi-Venue Execution, Step 14.
# venue_id changes ONLY the applied cost model (fees/slippage) — the
# underlying price data is always whatever df already contains (Binance-
# sourced in this project). A venue_id="kraken" backtest means "this same
# price history, simulated with Kraken's real fee schedule," never a real
# historical Kraken backtest — this project never fetches or fabricates
# Kraken OHLCV history.

import pandas as pd
from django.test import SimpleTestCase

from bot.backtesting.backtester import Backtester, backtest
from bot.engines.simulation import apply_slippage, calc_fees


class ApplySlippageOverrideTest(SimpleTestCase):

    def test_omitted_rate_uses_global_default(self):
        from bot.config.execution_costs import SLIPPAGE_RATE
        result = apply_slippage(100.0, "LONG", True)
        self.assertAlmostEqual(result, round(100.0 * (1 + SLIPPAGE_RATE), 8))

    def test_explicit_rate_overrides_default(self):
        result = apply_slippage(100.0, "LONG", True, slippage_rate=0.01)
        self.assertAlmostEqual(result, round(100.0 * 1.01, 8))


class CalcFeesOverrideTest(SimpleTestCase):

    def test_omitted_rate_uses_global_default(self):
        from bot.config.execution_costs import FEE_RATE
        result = calc_fees(100.0, 105.0, 1.0)
        expected = round(100.0 * 1.0 * FEE_RATE + 105.0 * 1.0 * FEE_RATE, 6)
        self.assertAlmostEqual(result, expected)

    def test_explicit_rate_overrides_default(self):
        result = calc_fees(100.0, 105.0, 1.0, fee_rate=0.0026)
        expected = round(100.0 * 1.0 * 0.0026 + 105.0 * 1.0 * 0.0026, 6)
        self.assertAlmostEqual(result, expected)


def _load_btc_df():
    df = pd.read_csv("data/BTC_USDT_1h.csv")
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df.set_index("timestamp", inplace=True)
    return df.tail(2000)


class BacktesterVenueSelectionTest(SimpleTestCase):
    """
    Real-data end-to-end, mirroring test_production_gating.py::UIPipelineTest's
    established Backtester(df=df.tail(2000)) pattern.
    """

    def test_default_venue_id_is_binance(self):
        bt = Backtester(df=_load_btc_df())
        results = bt.run()
        self.assertEqual(results["venue_id"], "binance")

    def test_omitted_and_explicit_binance_produce_identical_fees(self):
        df = _load_btc_df()
        default_results = Backtester(df=df).run()
        explicit_results = Backtester(df=df, venue_id="binance").run()

        self.assertEqual(default_results["total_fees_paid"], explicit_results["total_fees_paid"])
        self.assertEqual(default_results["fee_rate"], explicit_results["fee_rate"])

    def test_kraken_venue_produces_higher_fees_than_binance(self):
        df = _load_btc_df()
        binance_results = Backtester(df=df, venue_id="binance").run()
        kraken_results = Backtester(df=df, venue_id="kraken").run()

        self.assertEqual(kraken_results["venue_id"], "kraken")
        self.assertEqual(kraken_results["fee_rate"], 0.0026)
        self.assertEqual(binance_results["fee_rate"], 0.001)

        # Same trade sequence (identical price data, identical strategy
        # logic) — only the fee rate differs, so Kraken's total fees paid
        # must be measurably higher whenever any trades occurred.
        if binance_results["total_trades"] > 0:
            self.assertGreater(kraken_results["total_fees_paid"], binance_results["total_fees_paid"])

    def test_free_function_backtest_accepts_venue_id(self):
        df = _load_btc_df()
        results = backtest(df, venue_id="kraken")
        self.assertEqual(results["venue_id"], "kraken")
        self.assertEqual(results["fee_rate"], 0.0026)

    def test_empty_results_also_carries_venue_fields(self):
        # Fewer than 100 candles -> _empty_results() path.
        tiny_df = _load_btc_df().tail(5)
        results = Backtester(df=tiny_df, venue_id="kraken").run()
        self.assertEqual(results["venue_id"], "kraken")
        self.assertEqual(results["fee_rate"], 0.0026)
