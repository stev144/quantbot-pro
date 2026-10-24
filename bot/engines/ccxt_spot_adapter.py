# ============================================================
# bot/engines/ccxt_spot_adapter.py
# claude code changed: new file — 2026-10-22 (owner: "add Coinbase" + "audit Kraken"). The venue-agnostic part
# of a ccxt SPOT adapter, moved VERBATIM out of bot/engines/kraken_adapter.py so a third venue (Coinbase) does not
# duplicate it. KrakenAdapter and CoinbaseAdapter subclass this; each keeps only what is genuinely venue-specific
# (venue_id, FALLBACK_QUOTES, place_stop_loss, factory). BinanceAdapter is deliberately NOT changed (live path).
#
# Two audit fixes over the original Kraken copy (both disclosed, both tested):
#   1. normalize_symbol() refuses a market ccxt marks active=False (delisted/halted) instead of returning it.
#   2. get_order_book() trims every level to [price, amount] for ANY venue (Kraken and Coinbase Exchange both
#      append extra per-level fields); behaviour for Kraken is unchanged.
# ============================================================

import logging
from abc import abstractmethod
from typing import List, Optional

from bot.config.execution_costs import get_venue_execution_costs
from bot.engines.exchange_adapter import ExchangeAdapter
from bot.engines.market_data import MarketData
from bot.engines.order_manager import OrderManager

logger = logging.getLogger(__name__)


class CcxtSpotAdapter(ExchangeAdapter):
    """Shared ccxt spot implementation; OrderManager/MarketData are reused, never re-implemented."""

    FALLBACK_QUOTES: List[str] = []    # quote currencies tried, in order, when BASE/QUOTE is not listed

    def __init__(self, exchange, dry_run: bool = False):
        self.exchange = exchange
        self.dry_run = dry_run
        costs = get_venue_execution_costs(self.venue_id)   # venue-correct dry-run fills (Kraken Step 8 fix)
        self.order_manager = OrderManager(exchange, dry_run=dry_run,
                                          fee_rate=costs["fee_rate"], slippage_rate=costs["slippage_rate"])
        self.market_data = MarketData(exchange, dry_run=dry_run)

    @property
    @abstractmethod
    def venue_id(self) -> str: ...

    @abstractmethod
    def place_stop_loss(self, symbol: str, side: str, quantity: float, stop_price: float) -> dict: ...

    # ── Market data ──────────────────────────────────────────────────
    def get_ticker(self, symbol: str) -> dict:
        ticker = self.exchange.fetch_ticker(symbol)
        return {"last": ticker.get("last"), "bid": ticker.get("bid"), "ask": ticker.get("ask"),
                "timestamp": ticker.get("timestamp")}

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list:
        return self.market_data.get_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def get_balance(self, currency: str) -> float:
        return self.market_data.get_balance(currency)

    # ── Orders ───────────────────────────────────────────────────────
    def place_order(self, symbol: str, side: str, order_type: str, quantity: float, price: Optional[float] = None) -> dict:
        effective_price = price if order_type == "limit" else None
        return self.order_manager.place_order(symbol, side, quantity, price=effective_price)

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        return self.exchange.cancel_order(order_id, symbol)

    def get_order(self, order_id: str, symbol: str) -> dict:
        return self.exchange.fetch_order(order_id, symbol)

    def get_open_orders(self, symbol: str) -> list:
        return self.exchange.fetch_open_orders(symbol)

    def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        raw = self.exchange.fetch_order_book(symbol, limit)
        return {"symbol": symbol,
                "bids": [[level[0], level[1]] for level in raw["bids"]],
                "asks": [[level[0], level[1]] for level in raw["asks"]],
                "timestamp": raw.get("timestamp")}

    # ── Normalization ────────────────────────────────────────────────
    def _listed(self, symbol: str) -> bool:
        market = self.exchange.markets.get(symbol)
        return market is not None and market.get("active") is not False   # claude code changed: audit fix 1

    def normalize_symbol(self, canonical_symbol: str) -> Optional[str]:
        if not self.exchange.markets:
            # markets not loaded — cannot validate; callers must load_markets()/validate_connection() first
            # (unchanged Kraken behaviour, kept so existing callers are unaffected; compare_venues loads them)
            return canonical_symbol
        if self._listed(canonical_symbol):
            return canonical_symbol
        base, _, _quote = canonical_symbol.partition("/")
        for fallback_quote in self.FALLBACK_QUOTES:
            candidate = f"{base}/{fallback_quote}"
            if self._listed(candidate):
                logger.warning(f"[{type(self).__name__}] {canonical_symbol} not listed on {self.venue_id}; "
                               f"falling back to {candidate}")
                return candidate
        logger.warning(f"[{type(self).__name__}] {canonical_symbol} has no active equivalent listing on "
                       f"{self.venue_id} (checked fallback quotes {self.FALLBACK_QUOTES})")
        return None

    def normalize_quantity(self, symbol: str, quantity: float) -> float:
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
        return get_venue_execution_costs(self.venue_id)

    # ── Connection health ────────────────────────────────────────────
    def validate_connection(self) -> tuple:
        try:
            self.exchange.load_markets()
        except Exception as e:
            return False, f"load_markets() failed: {type(e).__name__}: {e}"
        try:
            self.exchange.fetch_balance()
        except Exception as e:
            return False, f"fetch_balance() failed (check API credentials): {type(e).__name__}: {e}"
        return True, "connected"
