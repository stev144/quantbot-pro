# ============================================================
# bot/engines/binance_adapter.py
# claude code changed: new file — Kraken Multi-Venue Execution, Step 3.
# Makes Binance conform to ExchangeAdapter (bot/engines/exchange_adapter.py)
# by WRAPPING the existing OrderManager/MarketData classes, not replacing
# them. Both already contain substantial, battle-tested Binance-execution
# logic (typed exceptions, retry/backoff, dry-run simulation, rate-limit
# handling) — reimplementing any of that here would be exactly the "large
# rewrite" the project's Kraken-integration spec says to avoid, and would
# risk its one non-negotiable constraint: "Binance continues working
# exactly as before."
#
# bot/engines/execution_engine.py and bot/core/bot_runner.py are NOT
# touched or rewired to use this class yet — they still talk to a raw
# ccxt exchange object directly, exactly as before this file existed.
# Wiring the live path through ExchangeAdapter is a later step (once a
# second venue exists to justify it) — not this one.
# ============================================================

from typing import Optional

from bot.engines.exchange_adapter import ExchangeAdapter
from bot.engines.order_manager import OrderManager
from bot.engines.market_data import MarketData
from bot.config.execution_costs import get_venue_execution_costs


class BinanceAdapter(ExchangeAdapter):
    """
    ExchangeAdapter implementation for Binance. Constructs and delegates
    to the existing OrderManager/MarketData classes internally — same
    classes ExecutionEngine already uses — so behavior is provably
    identical to today's live path, not a parallel reimplementation.
    """

    def __init__(self, exchange, dry_run: bool = False):
        self.exchange = exchange
        self.dry_run = dry_run
        # claude code changed: Step 8 — passes Binance's own execution
        # costs into OrderManager so its dry-run fill simulation is
        # sourced from the same per-venue table get_execution_costs()
        # advertises below, instead of OrderManager's bare global default.
        # Values are unchanged (still the Binance-modeled FEE_RATE/
        # SLIPPAGE_RATE), only now explicitly threaded through rather than
        # implicitly relied upon.
        costs = get_venue_execution_costs(self.venue_id)
        # claude code changed: reuses the exact classes ExecutionEngine
        # already constructs — all retry/backoff/dry-run logic lives
        # there unchanged, this class only bridges the contract shape.
        self.order_manager = OrderManager(
            exchange, dry_run=dry_run,
            fee_rate=costs["fee_rate"], slippage_rate=costs["slippage_rate"],
        )
        self.market_data = MarketData(exchange, dry_run=dry_run)

    @property
    def venue_id(self) -> str:
        return "binance"

    # ── Market data ──────────────────────────────────────────────────

    def get_ticker(self, symbol: str) -> dict:
        # claude code changed: new — direct ccxt unified call. MarketData
        # only exposes a processed float (get_price()), not the full
        # bid/ask/timestamp shape this contract requires, so this reads
        # fetch_ticker() directly rather than adding an unused capability
        # to MarketData itself.
        ticker = self.exchange.fetch_ticker(symbol)
        return {
            "last": ticker.get("last"),
            "bid": ticker.get("bid"),
            "ask": ticker.get("ask"),
            "timestamp": ticker.get("timestamp"),
        }

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list:
        return self.market_data.get_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def get_balance(self, currency: str) -> float:
        return self.market_data.get_balance(currency)

    # ── Orders ───────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
    ) -> dict:
        # claude code changed: bridges the contract's explicit order_type
        # to OrderManager.place_order()'s existing `price is None` market/
        # limit inference — OrderManager itself is unchanged. A "market"
        # order_type always passes price=None regardless of what was given,
        # matching OrderManager's own documented behavior exactly.
        effective_price = price if order_type == "limit" else None
        return self.order_manager.place_order(symbol, side, quantity, price=effective_price)

    def place_stop_loss(self, symbol: str, side: str, quantity: float, stop_price: float) -> dict:
        # claude code changed: signature already matches OrderManager's
        # place_stop_loss() exactly — zero bridging needed. This is the
        # one method Step 1's investigation proved is genuinely
        # venue-specific (Binance's params={"stopLossPrice": ...} ccxt
        # translation) — Kraken's adapter must implement this differently.
        return self.order_manager.place_stop_loss(symbol, side, quantity, stop_price)

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        return self.exchange.cancel_order(order_id, symbol)

    def get_order(self, order_id: str, symbol: str) -> dict:
        return self.exchange.fetch_order(order_id, symbol)

    def get_open_orders(self, symbol: str) -> list:
        return self.exchange.fetch_open_orders(symbol)

    def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        # claude code changed: new — Step 9. Trims each level to exactly
        # [price, amount] even though Binance's ccxt levels are already
        # 2 elements — keeps this identical in shape/behavior to
        # KrakenAdapter's implementation rather than relying on Binance's
        # levels happening to already be the right length.
        raw = self.exchange.fetch_order_book(symbol, limit)
        return {
            "symbol": symbol,
            "bids": [[level[0], level[1]] for level in raw["bids"]],
            "asks": [[level[0], level[1]] for level in raw["asks"]],
            "timestamp": raw.get("timestamp"),
        }

    # ── Normalization ────────────────────────────────────────────────

    def normalize_symbol(self, canonical_symbol: str) -> Optional[str]:
        # claude code changed: Step 5 — was a bare passthrough. Binance's
        # "BASE/QUOTE" format is already this codebase's canonical
        # convention (confirmed in Step 1: SYMBOL = "XRP/USDT" flows
        # unchanged into every layer), and this project's data/*.csv
        # universe is USDT-native by construction (it was pulled FROM
        # Binance). No quote-currency fallback is needed here unlike
        # KrakenAdapter. But this now checks real exchange.markets data
        # instead of blindly trusting the format, for contract consistency
        # with ExchangeAdapter.normalize_symbol()'s Optional[str] return —
        # an unlisted symbol fails loudly here too, not silently downstream.
        if not self.exchange.markets:
            return canonical_symbol
        if canonical_symbol in self.exchange.markets:
            return canonical_symbol
        return None

    def normalize_quantity(self, symbol: str, quantity: float) -> float:
        # claude code changed: new — fixes a real, pre-existing gap Step 1
        # found: nothing in the live path reads exchange.markets[symbol]
        # ['precision'] anywhere; PositionSizer hardcodes a 4-decimal floor
        # for every symbol identically. ccxt's own amount_to_precision()
        # reads the exchange's real per-symbol lot-size rules generically —
        # not a Binance-specific reimplementation, just finally calling
        # what ccxt already provides.
        return float(self.exchange.amount_to_precision(symbol, quantity))

    def normalize_price(self, symbol: str, price: float) -> float:
        return float(self.exchange.price_to_precision(symbol, price))

    def validate_order(self, symbol: str, quantity: float, price: Optional[float] = None) -> tuple:
        market = self.exchange.markets.get(symbol) if self.exchange.markets else None
        if market is None:
            return False, f"{symbol} is not a listed market on {self.venue_id}"

        limits = market.get("limits", {})
        min_amount = (limits.get("amount") or {}).get("min")
        if min_amount is not None and quantity < min_amount:
            return False, f"quantity {quantity} below {self.venue_id}'s minimum {min_amount} for {symbol}"

        min_cost = (limits.get("cost") or {}).get("min")
        if min_cost is not None and price is not None and (quantity * price) < min_cost:
            return False, f"order notional below {self.venue_id}'s minimum {min_cost} for {symbol}"

        return True, ""

    # ── Costs ────────────────────────────────────────────────────────

    def get_execution_costs(self, symbol: Optional[str] = None) -> dict:
        # claude code changed: Step 8 — now reads from the real per-venue
        # table (bot/config/execution_costs.py's VENUE_EXECUTION_COSTS)
        # instead of importing FEE_RATE/SLIPPAGE_RATE directly. Same
        # returned numbers as before this step.
        return get_venue_execution_costs(self.venue_id)

    # ── Connection health ────────────────────────────────────────────

    def validate_connection(self) -> tuple:
        # claude code changed: new — mirrors what bot_runner.py's
        # check_components() and dry_run_test.py::test_exchange_connection()
        # already do for Binance (load_markets(), a balance fetch), without
        # hardcoding a specific trading symbol into a generic adapter.
        try:
            self.exchange.load_markets()
        except Exception as e:
            return False, f"load_markets() failed: {type(e).__name__}: {e}"

        try:
            self.exchange.fetch_balance()
        except Exception as e:
            return False, f"fetch_balance() failed (check API credentials): {type(e).__name__}: {e}"

        return True, "connected"
