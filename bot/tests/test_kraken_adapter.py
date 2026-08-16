# claude code changed: new file — Kraken Multi-Venue Execution, Step 4.
# Mirrors test_binance_adapter.py's structure and no-mocking convention —
# real ccxt.kraken() calls throughout, public endpoints only (load_markets(),
# dry-run fills that use fetch_ticker()). No live authenticated call is made
# anywhere in this file — the margin-only stop-loss question documented in
# kraken_adapter.py stays explicitly unverified, on purpose.
#
# claude code changed: Step 5 added real normalize_symbol() coverage below
# (KrakenAdapterNormalizationTest) — direct-listing, /USD fallback, and
# fully-unlisted cases, all against real load_markets() data.

import os
from unittest.mock import patch

import ccxt
from django.test import SimpleTestCase

from bot.engines.exchange_adapter import ExchangeAdapter
from bot.engines.kraken_adapter import KrakenAdapter, build_kraken_adapter


class KrakenAdapterContractTest(SimpleTestCase):

    def test_is_a_valid_instantiable_exchange_adapter(self):
        adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)
        self.assertIsInstance(adapter, ExchangeAdapter)
        self.assertEqual(adapter.venue_id, "kraken")


class KrakenAdapterDryRunDelegationTest(SimpleTestCase):

    def setUp(self):
        self.adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)

    def test_market_order_delegates_to_shared_order_manager(self):
        # Confirms OrderManager (reused, not reimplemented) handles Kraken
        # exactly like Binance in dry-run — same simulated-fill shape.
        result = self.adapter.place_order("BTC/USDT", "buy", "market", 0.001)
        for key in ("fill_price", "filled_qty", "fee_usdt", "order_id", "slippage_pct"):
            self.assertIn(key, result)
        self.assertTrue(result["order_id"].startswith("DRYRUN-"))

    def test_place_stop_loss_uses_krakenadapters_own_dry_run_path(self):
        # Not OrderManager's — KrakenAdapter.place_stop_loss() is its own
        # implementation (see its docstring for why). Confirm its dry-run
        # short-circuit shape matches Binance's for consistency.
        result = self.adapter.place_stop_loss("BTC/USDT", "sell", 0.001, 45000.0)
        self.assertIsNone(result["order_id"])
        self.assertEqual(result["status"], "dry_run_skipped")
        self.assertEqual(result["stop_price"], 45000.0)


class KrakenAdapterNormalizationTest(SimpleTestCase):

    def test_normalize_matches_ccxt_precision_helpers_directly(self):
        exchange = ccxt.kraken()
        exchange.load_markets()   # public endpoint, no API keys needed
        adapter = KrakenAdapter(exchange, dry_run=True)

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

    def test_normalize_symbol_confirms_a_directly_listed_symbol(self):
        # claude code changed: Step 5 — normalize_symbol now checks real
        # exchange.markets data. BTC/USDT is directly listed on Kraken, no
        # fallback needed.
        exchange = ccxt.kraken()
        exchange.load_markets()
        adapter = KrakenAdapter(exchange, dry_run=True)
        self.assertEqual(adapter.normalize_symbol("BTC/USDT"), "BTC/USDT")

    def test_normalize_symbol_falls_back_to_usd_quote(self):
        # claude code changed: Step 5 — real, empirically-confirmed case:
        # AAVE/USDT is not listed on Kraken, but AAVE/USD is.
        exchange = ccxt.kraken()
        exchange.load_markets()
        adapter = KrakenAdapter(exchange, dry_run=True)
        self.assertEqual(adapter.normalize_symbol("AAVE/USDT"), "AAVE/USD")

    def test_normalize_symbol_returns_none_when_truly_unlisted(self):
        # claude code changed: Step 5 — real case: MATIC has no Kraken
        # listing under any quote currency (Polygon rebranded to POL,
        # Kraken tracks the new ticker, not the old MATIC base symbol).
        exchange = ccxt.kraken()
        exchange.load_markets()
        adapter = KrakenAdapter(exchange, dry_run=True)
        self.assertIsNone(adapter.normalize_symbol("MATIC/USDT"))

    def test_normalize_symbol_passes_through_when_markets_not_loaded(self):
        adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)
        self.assertEqual(adapter.normalize_symbol("BTC/USDT"), "BTC/USDT")


class KrakenAdapterValidateOrderTest(SimpleTestCase):

    def setUp(self):
        exchange = ccxt.kraken()
        exchange.load_markets()
        self.adapter = KrakenAdapter(exchange, dry_run=True)

    def test_rejects_quantity_below_real_kraken_minimum(self):
        # BTC/USDT's real, empirically-confirmed minimum on Kraken is
        # 5e-05 -- this is not a made-up threshold.
        is_valid, reason = self.adapter.validate_order("BTC/USDT", 0.00000001)
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


class KrakenAdapterExecutionCostsTest(SimpleTestCase):

    def test_get_execution_costs_matches_venue_table(self):
        # claude code changed: new — Step 8. Confirms get_execution_costs()
        # returns the same numbers after the table refactor as it did with
        # the old local KRAKEN_FEE_RATE constant (removed).
        adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)
        costs = adapter.get_execution_costs()
        self.assertEqual(costs["fee_rate"], 0.0026)

    def test_dry_run_fill_actually_uses_krakens_fee_rate_not_binances(self):
        # claude code changed: new — Step 8. THE regression test that would
        # have caught the real bug: before this step, OrderManager's
        # _simulate_fill() always used the module-level, Binance-modeled
        # FEE_RATE (0.001) regardless of which adapter constructed it, so
        # this dry-run fill was silently charged Binance's fee even though
        # get_execution_costs() already advertised Kraken's 0.26% rate.
        adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)
        result = adapter.place_order("BTC/USDT", "buy", "market", 1.0)

        # fee_usdt = fill_price * quantity * fee_rate -> back out the
        # effective rate actually applied and confirm it's Kraken's, not
        # Binance's 0.001.
        effective_fee_rate = result["fee_usdt"] / (result["fill_price"] * 1.0)
        self.assertAlmostEqual(effective_fee_rate, 0.0026, places=6)
        self.assertNotAlmostEqual(effective_fee_rate, 0.001, places=4)


class BuildKrakenAdapterFactoryTest(SimpleTestCase):

    def test_disabled_returns_none(self):
        with patch.dict(os.environ, {"KRAKEN_ENABLED": "false"}, clear=False):
            self.assertIsNone(build_kraken_adapter())

    def test_unset_defaults_to_disabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KRAKEN_ENABLED", None)
            self.assertIsNone(build_kraken_adapter())

    def test_enabled_dry_run_default_needs_no_credentials(self):
        env = {"KRAKEN_ENABLED": "true"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("KRAKEN_API_KEY", None)
            os.environ.pop("KRAKEN_API_SECRET", None)
            os.environ.pop("KRAKEN_DRY_RUN", None)   # defaults to true
            adapter = build_kraken_adapter()
            self.assertIsInstance(adapter, KrakenAdapter)
            self.assertTrue(adapter.dry_run)

    def test_enabled_live_without_credentials_fails_safely(self):
        env = {"KRAKEN_ENABLED": "true", "KRAKEN_DRY_RUN": "false"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("KRAKEN_API_KEY", None)
            os.environ.pop("KRAKEN_API_SECRET", None)
            self.assertIsNone(build_kraken_adapter())


# claude code changed: new class — Kraken Multi-Venue Execution, Step 16.
# Closes a real, confirmed gap: get_ticker()/get_ohlcv() were never
# directly tested on either adapter despite being public, real-network-
# testable endpoints (no credentials needed).
class KrakenAdapterMarketDataMethodsTest(SimpleTestCase):

    def test_get_ticker_returns_real_shape(self):
        adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)
        ticker = adapter.get_ticker("BTC/USDT")

        for key in ("last", "bid", "ask", "timestamp"):
            self.assertIn(key, ticker)
        self.assertGreater(ticker["last"], 0)

    def test_get_ohlcv_returns_real_shape(self):
        adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)
        candles = adapter.get_ohlcv("BTC/USDT", "1h", limit=10)

        self.assertEqual(len(candles), 10)
        for row in candles:
            self.assertEqual(len(row), 6)   # timestamp, o, h, l, c, v
            _, o, h, l, c, v = row
            self.assertGreater(o, 0)
            self.assertGreater(h, 0)
            self.assertGreater(l, 0)
            self.assertGreater(c, 0)


class KrakenAdapterCredentialedMethodsFailCleanlyTest(SimpleTestCase):
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
        self.adapter = KrakenAdapter(ccxt.kraken(), dry_run=True)

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
