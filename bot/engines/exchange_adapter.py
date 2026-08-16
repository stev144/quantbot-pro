# ============================================================
# bot/engines/exchange_adapter.py
# claude code changed: new file — Kraken Multi-Venue Execution, Step 2.
# Defines ExchangeAdapter, the venue-agnostic execution contract every
# exchange (Binance today, Kraken next, Interactive Brokers/Saxo later)
# must implement. Nothing in the codebase depends on this yet — Step 3
# makes Binance conform to it (bot/engines/order_manager.py and
# bot/engines/market_data.py still talk to a raw ccxt exchange object
# directly today), Step 4 adds Kraken. This step is the contract only.
#
# WHY abc.ABC: a subclass that's missing a method raises TypeError at
# instantiation, not a silent AttributeError mid-trade the first time
# that code path runs live. That's the enforcement mechanism behind the
# project's "Kraken must NOT provide a bypass" requirement — a missing
# method is a hard error before the adapter can even be constructed.
#
# METHOD LIST — deliberately not the full generic exchange-API surface.
# Built from what bot/engines/order_manager.py and bot/engines/market_data.py
# actually call on the exchange today (confirmed by reading every call
# site, not assumed):
#   get_ticker      -> MarketData.get_price()'s exchange.fetch_ticker()
#   get_ohlcv       -> MarketData.get_ohlcv()'s exchange.fetch_ohlcv()
#   get_balance     -> MarketData.get_balance()'s exchange.fetch_balance()
#                      (currency is now a required param — market_data.py
#                      hardcodes "USDT" today; see get_balance() docstring)
#   place_order     -> OrderManager.place_order()'s exchange.create_order()
#   place_stop_loss -> OrderManager.place_stop_loss() — THE one method
#                      Step 1's investigation proved is genuinely
#                      venue-specific: it passes params={"stopLossPrice":
#                      stop_price}, which ccxt only translates to Binance's
#                      native STOP_LOSS_LIMIT order type. Kraken's ccxt
#                      params for a stop order are shaped differently, so
#                      this cannot be a shared, param-passthrough method —
#                      every venue must implement its own translation.
#   cancel_order    -> OrderManager's exchange.cancel_order()
#   get_order       -> OrderManager's exchange.fetch_order() (confirmation/polling)
#   get_open_orders -> OrderManager's recovery scan + PositionTracker's
#                      reconcile_with_exchange(), both exchange.fetch_open_orders()
#
# Three methods from the project's suggested list are deliberately NOT
# included, each with a reason (not a blind implement-everything pass):
#   get_order_book()   — zero call sites anywhere in the live path today
#                         (confirmed). That's Step 9 (Order Book/Liquidity
#                         Integration)'s explicit job, not this step's.
#   get_position()     — this codebase trades spot only; there is no
#                         discrete "position" object to query from an
#                         exchange the way futures/margin APIs have one.
#                         bot/engines/position_tracker.py's in-memory dict
#                         + reconcile_with_exchange() (via get_open_orders)
#                         already serves this role. A method with nothing
#                         real behind it would be a stub, not a contract.
#   get_account_state() — no current consumer. validate_connection() below
#                         + get_balance() already cover exactly what
#                         bot_runner.py's check_components() and
#                         bot/core/dry_run_test.py::test_exchange_connection()
#                         check today (load_markets(), a ticker fetch, a
#                         balance fetch) — nothing calls for a broader
#                         "account state" blob.
#
# RETURN SHAPES: plain dicts, matching this codebase's existing convention
# (signal dicts, position dicts, OrderManager's _build_result() dict) —
# not a new dataclass system. Steps 6/7 introduce formal OrderIntent/
# ExecutionResult types later; until then these methods accept the same
# primitive parameters OrderManager already passes today.
# ============================================================

from abc import ABC, abstractmethod
from typing import Optional


class ExchangeAdapter(ABC):
    """
    Venue-agnostic execution contract. Every venue (BinanceAdapter today,
    KrakenAdapter next) implements every method below. Nothing else in
    the codebase should ever branch on which venue it's talking to —
    that's the entire point of this class existing.
    """

    # ── Identity ─────────────────────────────────────────────────────

    @property
    @abstractmethod
    def venue_id(self) -> str:
        """Short lowercase venue identifier, e.g. "binance", "kraken" —
        for TradeRecord.venue, logging, and execution_group_id correlation
        (Step 10)."""
        raise NotImplementedError

    # ── Market data ──────────────────────────────────────────────────

    @abstractmethod
    def get_ticker(self, symbol: str) -> dict:
        """Returns {"last": float, "bid": float, "ask": float, "timestamp": int}
        for the canonical symbol (e.g. "BTC/USDT"). Mirrors
        MarketData.get_price()'s current exchange.fetch_ticker() call."""
        raise NotImplementedError

    @abstractmethod
    def get_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list:
        """Returns a list of [timestamp, open, high, low, close, volume]
        rows, matching ccxt's unified fetch_ohlcv() shape — mirrors
        MarketData.get_ohlcv()'s current call exactly."""
        raise NotImplementedError

    @abstractmethod
    def get_balance(self, currency: str) -> float:
        """Returns free balance for the given currency. currency is a
        required parameter — market_data.py's current get_balance()
        hardcodes "USDT", which this contract deliberately does not
        repeat, since a second venue may be funded/quoted differently."""
        raise NotImplementedError

    # ── Orders ───────────────────────────────────────────────────────

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
    ) -> dict:
        """Places an order. Returns a dict shaped like OrderManager's
        current _build_result() output (fill_price, filled_qty, fee_usdt,
        order_id, slippage_pct, ...) so Step 3's Binance adapter can wrap
        the existing logic close to unchanged."""
        raise NotImplementedError

    @abstractmethod
    def place_stop_loss(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
    ) -> dict:
        """Places a resting stop-loss order. THE one method every venue
        must implement independently — see module docstring for why this
        can't be a shared, param-passthrough implementation."""
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_order(self, order_id: str, symbol: str) -> dict:
        """Fetches current order state — mirrors OrderManager's
        exchange.fetch_order() polling/confirmation calls."""
        raise NotImplementedError

    @abstractmethod
    def get_open_orders(self, symbol: str) -> list:
        """Mirrors OrderManager's recovery scan and PositionTracker's
        reconcile_with_exchange(), both currently exchange.fetch_open_orders()."""
        raise NotImplementedError

    @abstractmethod
    def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        """Returns {"symbol": str, "bids": [[price, amount], ...],
        "asks": [[price, amount], ...], "timestamp": Optional[int]}.

        claude code changed: new — Kraken Multi-Venue Execution, Step 9.
        Deferred here since Step 2 (zero call sites existed at that time).
        Every bid/ask level MUST be normalized to exactly [price, amount]
        by the implementation — verified empirically that Kraken's ccxt
        fetch_order_book() returns a THIRD per-level element (a timestamp)
        that Binance's does not, on every level, both sides, confirmed on
        repeated fetches. A naive `price, amount = level` unpack would work
        on Binance and crash on Kraken. This contract exists specifically
        so callers never have to know that."""
        raise NotImplementedError

    # ── Normalization ────────────────────────────────────────────────
    # Real gap Step 1 found: nothing in the codebase normalizes symbols
    # or quantity/price precision today. The canonical symbol
    # (e.g. "BTC/USDT") flows unchanged from bot_runner.py straight into
    # exchange.create_order() with no translation layer at all.

    @abstractmethod
    def normalize_symbol(self, canonical_symbol: str) -> Optional[str]:
        """Canonical "BASE/QUOTE" symbol -> this venue's own symbol
        representation, or None if this symbol has no tradeable equivalent
        on this venue under any known quote-currency fallback.

        claude code changed: Step 5 — was a bare str return (passthrough-
        only). Step 5's empirical audit of this project's real 20-symbol
        universe against Kraken's live market listing found 7 symbols
        (AAVE, APT, ARB, FIL, OP, UNI, XLM) are listed on Kraken only under
        /USD, not /USDT, and 1 (MATIC) has no Kraken listing at all under
        any quote currency (Polygon rebranded to POL; Kraken tracks the new
        ticker). A str-only return couldn't represent "not available here"
        distinctly from a real symbol, so callers had no way to detect and
        skip an unsupported symbol before hitting a confusing ccxt error.
        Implementations must check real exchange.markets data, not assume
        format parity with Binance."""
        raise NotImplementedError

    @abstractmethod
    def normalize_quantity(self, symbol: str, quantity: float) -> float:
        """Rounds/floors quantity to this venue's real lot-size/precision
        rules for this symbol. Replaces bot/risk/position_sizer.py's
        current hardcoded 4-decimal floor, which Step 1 found applies to
        every symbol/venue identically today — a pre-existing gap, not
        introduced by Kraken."""
        raise NotImplementedError

    @abstractmethod
    def normalize_price(self, symbol: str, price: float) -> float:
        """Rounds price to this venue's real tick-size/precision rules
        for this symbol."""
        raise NotImplementedError

    @abstractmethod
    def validate_order(
        self, symbol: str, quantity: float, price: Optional[float] = None
    ) -> tuple[bool, str]:
        """Pre-flight check against this venue's real order constraints
        (min notional, min quantity, symbol actually listed here, etc.)
        before an order is placed. Returns (is_valid, reason) — reason is
        "" when is_valid is True."""
        raise NotImplementedError

    # ── Costs ────────────────────────────────────────────────────────
    # Real gap: bot/config/execution_costs.py's FEE_RATE = 0.001 is one
    # bare constant, comment-labeled "Binance standard taker fee," shared
    # by both live dry-run fills and the backtester. Not venue-aware.

    @abstractmethod
    def get_execution_costs(self, symbol: Optional[str] = None) -> dict:
        """Returns {"fee_rate": float, "slippage_rate": float} for this
        venue (optionally symbol-specific, where a venue's fee schedule
        varies by pair). Step 8 wires this into the shared cost model
        that bot/engines/simulation.py and OrderManager._simulate_fill()
        currently both import a single FEE_RATE/SLIPPAGE_RATE from."""
        raise NotImplementedError

    # ── Connection health ────────────────────────────────────────────

    @abstractmethod
    def validate_connection(self) -> tuple[bool, str]:
        """Confirms credentials/connectivity are good — mirrors what
        bot_runner.py's check_components() and
        bot/core/dry_run_test.py::test_exchange_connection() already do
        for Binance today (load_markets(), a ticker fetch, a balance
        fetch). Returns (is_ok, message)."""
        raise NotImplementedError
