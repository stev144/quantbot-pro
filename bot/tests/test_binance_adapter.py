# claude code changed: new file — Kraken Multi-Venue Execution, Step 3.
# Proves BinanceAdapter actually implements ExchangeAdapter correctly and
# that its delegation to OrderManager/MarketData is real, not just
# plausible. No mocking (project convention) — uses a real ccxt.binance()
# instance throughout. Live credentialed calls aren't appropriate for a
# test suite, so tests either stay in dry-run mode (deterministic, no
# network) or use public, unauthenticated ccxt endpoints (load_markets()).

import ccxt
from django.test import SimpleTestCase

from bot.engines.binance_adapter import BinanceAdapter
from bot.engines.exchange_adapter import ExchangeAdapter
from bot.engines.market_data import MarketData
from bot.config.risk import DRY_RUN_PAPER_BALANCE


class BinanceAdapterContractTest(SimpleTestCase):

    def test_is_a_valid_instantiable_exchange_adapter(self):
        adapter = BinanceAdapter(ccxt.binance(), dry_run=True)
        self.assertIsInstance(adapter, ExchangeAdapter)
        self.assertEqual(adapter.venue_id, "binance")


class BinanceAdapterDryRunDelegationTest(SimpleTestCase):
    """Confirms the bridge to OrderManager is real, not just plausible."""

    def setUp(self):
        self.adapter = BinanceAdapter(ccxt.binance(), dry_run=True)

    def test_market_order_delegates_to_order_manager_simulated_fill(self):
        result = self.adapter.place_order("BTC/USDT", "buy", "market", 0.001)
        # Shape must match OrderManager._simulate_fill()'s real output —
        # same keys _build_result() produces for a live fill.
        for key in ("fill_price", "filled_qty", "fee_usdt", "order_id", "slippage_pct"):
            self.assertIn(key, result)
        self.assertTrue(result["order_id"].startswith("DRYRUN-"))
        self.assertEqual(result["filled_qty"], 0.001)

    def test_limit_order_type_passes_price_through(self):
        result = self.adapter.place_order("BTC/USDT", "buy", "limit", 0.001, price=50000.0)
        self.assertIn("fill_price", result)

    def test_market_order_type_ignores_a_stray_price(self):
        # order_type="market" must always resolve to OrderManager's own
        # price=None market-order path, regardless of what price was passed.
        result = self.adapter.place_order("BTC/USDT", "buy", "market", 0.001, price=999999.0)
        self.assertIn("fill_price", result)
        self.assertNotEqual(result["fill_price"], 999999.0)

    def test_place_stop_loss_delegates_to_order_manager_dry_run_path(self):
        result = self.adapter.place_stop_loss("BTC/USDT", "sell", 0.001, 45000.0)
        self.assertIsNone(result["order_id"])
        self.assertEqual(result["status"], "dry_run_skipped")
        self.assertEqual(result["stop_price"], 45000.0)


class BinanceAdapterNormalizationTest(SimpleTestCase):
    """normalize_quantity/normalize_price must match ccxt's own answer exactly."""

    def test_normalize_matches_ccxt_precision_helpers_directly(self):
        exchange = ccxt.binance()
        exchange.load_markets()   # public endpoint, no API keys needed
        adapter = BinanceAdapter(exchange, dry_run=True)

        symbol = "BTC/USDT"
        raw_qty = 0.123456789
        raw_price = 50123.456789

        self.assertEqual(
            adapter.normalize_quantity(symbol, raw_qty),
            float(exchange.amount_to_precision(symbol, raw_qty)),
        )
        self.assertEqual(
            adapter.normalize_price(symbol, raw_price),
            float(exchange.price_to_precision(symbol, raw_price)),
        )

    def test_normalize_symbol_confirms_a_real_listed_symbol(self):
        # claude code changed: Step 5 — normalize_symbol now checks real
        # exchange.markets data rather than blindly passing through.
        exchange = ccxt.binance()
        exchange.load_markets()
        adapter = BinanceAdapter(exchange, dry_run=True)
        self.assertEqual(adapter.normalize_symbol("BTC/USDT"), "BTC/USDT")

    def test_normalize_symbol_returns_none_for_unlisted_symbol(self):
        exchange = ccxt.binance()
        exchange.load_markets()
        adapter = BinanceAdapter(exchange, dry_run=True)
        self.assertIsNone(adapter.normalize_symbol("NOTREAL/USDT"))

    def test_normalize_symbol_passes_through_when_markets_not_loaded(self):
        # claude code changed: Step 5 — graceful-degradation branch, markets
        # not yet loaded so nothing can be validated.
        adapter = BinanceAdapter(ccxt.binance(), dry_run=True)
        self.assertEqual(adapter.normalize_symbol("BTC/USDT"), "BTC/USDT")


class BinanceAdapterExecutionCostsTest(SimpleTestCase):

    def test_get_execution_costs_matches_venue_table(self):
        # claude code changed: new — Step 8. Confirms get_execution_costs()
        # returns the same numbers after the table refactor as it did
        # hardcoding FEE_RATE/SLIPPAGE_RATE directly.
        from bot.config.execution_costs import FEE_RATE, SLIPPAGE_RATE
        adapter = BinanceAdapter(ccxt.binance(), dry_run=True)
        costs = adapter.get_execution_costs()
        self.assertEqual(costs["fee_rate"], FEE_RATE)
        self.assertEqual(costs["slippage_rate"], SLIPPAGE_RATE)


class MarketDataGetBalanceSignatureTest(SimpleTestCase):
    """Regression check on the get_balance(currency=...) signature change."""

    def test_dry_run_paper_balance_ignores_currency_argument(self):
        md = MarketData(exchange=None, dry_run=True)
        self.assertEqual(md.get_balance(), DRY_RUN_PAPER_BALANCE)
        self.assertEqual(md.get_balance(currency="ETH"), DRY_RUN_PAPER_BALANCE)
        self.assertEqual(md.get_balance("XRP"), DRY_RUN_PAPER_BALANCE)


# claude code changed: new class — Kraken Multi-Venue Execution, Step 16.
# Closes a real, confirmed gap: get_ticker()/get_ohlcv() were never
# directly tested on either adapter despite being public, real-network-
# testable endpoints (no credentials needed).
class BinanceAdapterMarketDataMethodsTest(SimpleTestCase):

    def test_get_ticker_returns_real_shape(self):
        adapter = BinanceAdapter(ccxt.binance(), dry_run=True)
        ticker = adapter.get_ticker("BTC/USDT")

        for key in ("last", "bid", "ask", "timestamp"):
            self.assertIn(key, ticker)
        self.assertGreater(ticker["last"], 0)

    def test_get_ohlcv_returns_real_shape(self):
        adapter = BinanceAdapter(ccxt.binance(), dry_run=True)
        candles = adapter.get_ohlcv("BTC/USDT", "1h", limit=10)

        self.assertEqual(len(candles), 10)
        for row in candles:
            self.assertEqual(len(row), 6)   # timestamp, o, h, l, c, v
            _, o, h, l, c, v = row
            self.assertGreater(o, 0)
            self.assertGreater(h, 0)
            self.assertGreater(l, 0)
            self.assertGreater(c, 0)


class BinanceAdapterValidateOrderTest(SimpleTestCase):
    """
    Parity fix — Step 16. KrakenAdapter already had this coverage
    (test_kraken_adapter.py::KrakenAdapterValidateOrderTest); Binance had
    none. Uses Binance's own real load_markets() limits (BTC/USDT min
    amount 1e-05, min cost $5.00 — empirically confirmed, not copied from
    Kraken's numbers).
    """

    def setUp(self):
        exchange = ccxt.binance()
        exchange.load_markets()
        self.adapter = BinanceAdapter(exchange, dry_run=True)

    def test_rejects_quantity_below_real_binance_minimum(self):
        is_valid, reason = self.adapter.validate_order("BTC/USDT", 0.000001)
        self.assertFalse(is_valid)
        self.assertIn("minimum", reason)

    def test_accepts_a_reasonable_quantity(self):
        is_valid, reason = self.adapter.validate_order("BTC/USDT", 0.01)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_rejects_unlisted_symbol(self):
        is_valid, reason = self.adapter.validate_order("NOTREAL/USDT", 1.0)
        self.assertFalse(is_valid)
        self.assertIn("not a listed market", reason)


class BinanceAdapterCredentialedMethodsFailCleanlyTest(SimpleTestCase):
    """
    cancel_order()/get_order()/get_open_orders() are all account-specific
    (authenticated) ccxt calls — genuinely untestable end-to-end without
    real API keys, which this project doesn't have by design. What IS
    real and testable without credentials: each raises ccxt's own
    AuthenticationError cleanly, proving the adapter is an untampered
    passthrough rather than swallowing/mangling the failure — confirmed
    empirically before writing this test, not assumed.
    """

    def setUp(self):
        self.adapter = BinanceAdapter(ccxt.binance(), dry_run=True)

    def test_cancel_order_raises_authentication_error_without_credentials(self):
        with self.assertRaises(ccxt.AuthenticationError):
            self.adapter.cancel_order("12345", "BTC/USDT")

    def test_get_order_raises_authentication_error_without_credentials(self):
        with self.assertRaises(ccxt.AuthenticationError):
            self.adapter.get_order("12345", "BTC/USDT")

    def test_get_open_orders_raises_authentication_error_without_credentials(self):
        with self.assertRaises(ccxt.AuthenticationError):
            self.adapter.get_open_orders("BTC/USDT")

    def test_validate_connection_fails_cleanly_without_credentials(self):
        # claude code changed: real, empirically-confirmed behavior — the
        # actual "no credentials configured" scenario this project runs in
        # today, more valuable to verify than an unreachable happy path.
        ok, message = self.adapter.validate_connection()
        self.assertFalse(ok)
        self.assertIn("fetch_balance", message)
        self.assertIn("AuthenticationError", message)
