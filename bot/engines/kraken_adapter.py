# ============================================================
# bot/engines/kraken_adapter.py
# claude code changed: new file — Kraken Multi-Venue Execution, Step 4.
# Makes Kraken conform to ExchangeAdapter (bot/engines/exchange_adapter.py),
# reusing OrderManager/MarketData exactly like BinanceAdapter (Step 3) does
# — both are confirmed exchange-agnostic (no Binance-specific branching
# anywhere in either), so nothing there needs duplicating for a second
# venue. Only place_stop_loss() gets its own explicit implementation — see
# that method's docstring for a real, verified discrepancy in ccxt's own
# Kraken source that isn't resolved without a live authenticated call.
#
# Not wired into bot/engines/execution_engine.py or bot/core/bot_runner.py
# yet — that's a later step, once there's an actual multi-venue execution
# coordinator to hand this to. build_kraken_adapter() below is a
# self-contained "get me a working adapter or None" entry point, not a
# main-loop integration.
# ============================================================

import logging
import os
from typing import Optional

import ccxt

from bot.engines.exchange_adapter import ExchangeAdapter
from bot.engines.order_manager import OrderManager
from bot.engines.market_data import MarketData
from bot.config.execution_costs import get_venue_execution_costs

logger = logging.getLogger(__name__)

# claude code changed: new — Step 5. Empirically confirmed via Kraken's real
# public load_markets() listing: of this project's 20 tracked symbols
# (data/*.csv), 7 (AAVE, APT, ARB, FIL, OP, UNI, XLM) are NOT listed under
# /USDT on Kraken at all, but ARE listed under /USD. This is a genuine
# quote-currency divergence from Binance, not a naming-format difference —
# ccxt already normalizes Kraken's native XBTUSDT-style IDs to the same
# "BASE/QUOTE" convention this codebase uses, confirmed for the 12/20
# symbols that ARE listed under /USDT on both venues. USD is the only
# fallback quote needed for this project's real symbol universe; not a
# general-purpose multi-quote router.
FALLBACK_QUOTES: list = ["USD"]


class KrakenAdapter(ExchangeAdapter):
    """
    ExchangeAdapter implementation for Kraken. Same shape as BinanceAdapter:
    constructs and delegates to OrderManager/MarketData internally for
    everything that isn't genuinely venue-specific.
    """

    def __init__(self, exchange, dry_run: bool = False):
        self.exchange = exchange
        self.dry_run = dry_run
        # claude code changed: Step 8 — fixes a real bug: OrderManager's
        # dry-run fill simulation previously always used the module-level,
        # Binance-modeled FEE_RATE/SLIPPAGE_RATE regardless of venue, so
        # Kraken's dry-run fills were silently charged Binance's 0.1% fee,
        # contradicting get_execution_costs() below (which already
        # advertised Kraken's real 0.26% rate). Now threaded through
        # explicitly so simulated fills actually reflect Kraken's own cost.
        costs = get_venue_execution_costs(self.venue_id)
        self.order_manager = OrderManager(
            exchange, dry_run=dry_run,
            fee_rate=costs["fee_rate"], slippage_rate=costs["slippage_rate"],
        )
        self.market_data = MarketData(exchange, dry_run=dry_run)

    @property
    def venue_id(self) -> str:
        return "kraken"

    # ── Market data ──────────────────────────────────────────────────

    def get_ticker(self, symbol: str) -> dict:
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
        # claude code changed: same bridge BinanceAdapter uses — OrderManager
        # is confirmed exchange-agnostic, reused unchanged here.
        effective_price = price if order_type == "limit" else None
        return self.order_manager.place_order(symbol, side, quantity, price=effective_price)

    def place_stop_loss(self, symbol: str, side: str, quantity: float, stop_price: float) -> dict:
        """
        Places a resting stop-loss-limit order on Kraken.

        claude code changed: new — deliberately NOT delegated to
        OrderManager.place_stop_loss(), unlike place_order() above. Reason:

        Read ccxt 4.5.51's actual installed kraken.py source directly (not
        assumed from docs). create_order()'s own docstring says:
            ":param float [params.stopLossPrice]: *margin only* the price
            that a stop loss order is triggered at"
        But order_request() (the method that actually builds the request,
        ccxt/kraken.py lines ~2042-2140) has ZERO margin/spot conditional
        logic — it processes params.stopLossPrice unconditionally:
        ordertype becomes 'stop-loss-limit', request['price'] = the trigger,
        request['price2'] = the original price arg. The call below is
        structurally identical to what OrderManager already sends Binance
        and would build a valid request either way — but whether Kraken's
        SERVER actually accepts a stop-loss-limit order on a spot pair (vs.
        rejecting it per the docstring's margin-only claim) cannot be
        verified without a live authenticated call, which this project's
        Kraken integration plan explicitly defers until after dry-run
        testing. This is flagged HERE, not silently inherited from
        OrderManager's Binance-specific comment, because that comment
        would misdescribe what's actually happening for this venue.

        Same "trigger price == limit price" simplification OrderManager's
        Binance implementation already documents and uses.
        """
        logger.info(
            f"[KrakenAdapter] Placing STOP LOSS | {side.upper()} | {symbol} | "
            f"Qty: {quantity} | Stop: {stop_price}"
        )

        if self.dry_run:
            logger.info(
                f"[KrakenAdapter] DRY RUN — no resting stop placed for {symbol}; "
                f"caller's own software-polled fallback (if any) must protect it"
            )
            return {
                "order_id": None,
                "stop_price": stop_price,
                "status": "dry_run_skipped",
                "symbol": symbol,
                "side": side.lower(),
            }

        # claude code changed: UNVERIFIED against a live Kraken account —
        # see docstring above. Do not treat this path as production-ready
        # until tested against real (or at minimum a Kraken-provided
        # sandbox, if one exists for spot — Kraken currently only offers a
        # futures demo environment, not spot) credentials.
        order = self.exchange.create_order(
            symbol=symbol,
            type="limit",
            side=side.lower(),
            amount=quantity,
            price=stop_price,
            params={"stopLossPrice": stop_price},
        )

        logger.info(
            f"[KrakenAdapter] Stop loss resting on exchange | "
            f"ID: {order.get('id')} | Trigger: {stop_price}"
        )

        return {
            "order_id": order.get("id"),
            "stop_price": stop_price,
            "status": order.get("status", "open"),
            "symbol": symbol,
            "side": side.lower(),
        }

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        return self.exchange.cancel_order(order_id, symbol)

    def get_order(self, order_id: str, symbol: str) -> dict:
        return self.exchange.fetch_order(order_id, symbol)

    def get_open_orders(self, symbol: str) -> list:
        return self.exchange.fetch_open_orders(symbol)

    def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        # claude code changed: new — Step 9. THE method that actually
        # matters for Kraken: ccxt's kraken.py fetch_order_book() returns
        # a third per-level element (a timestamp) on every bid/ask level —
        # verified empirically, not assumed — that Binance's does not.
        # Trimming to level[0]/level[1] here means no caller of this
        # contract method ever needs to know that.
        raw = self.exchange.fetch_order_book(symbol, limit)
        return {
            "symbol": symbol,
            "bids": [[level[0], level[1]] for level in raw["bids"]],
            "asks": [[level[0], level[1]] for level in raw["asks"]],
            "timestamp": raw.get("timestamp"),
        }

    # ── Normalization ────────────────────────────────────────────────

    def normalize_symbol(self, canonical_symbol: str) -> Optional[str]:
        # claude code changed: Step 5 — was a bare passthrough justified by
        # only 3 spot-checked symbols. Now checks real exchange.markets data
        # and applies a genuine, evidence-backed quote-currency fallback
        # (see FALLBACK_QUOTES above) instead of assuming Kraken always
        # mirrors Binance's /USDT convention.
        if not self.exchange.markets:
            # claude code changed: markets not loaded yet — can't validate.
            # Mirrors validate_order()'s existing graceful-degradation
            # pattern below; callers are already responsible for calling
            # load_markets()/validate_connection() before trading calls,
            # same as the live Binance path does today.
            return canonical_symbol

        if canonical_symbol in self.exchange.markets:
            return canonical_symbol

        base, _, _quote = canonical_symbol.partition("/")
        for fallback_quote in FALLBACK_QUOTES:
            candidate = f"{base}/{fallback_quote}"
            if candidate in self.exchange.markets:
                logger.warning(
                    f"[KrakenAdapter] {canonical_symbol} not listed on Kraken; "
                    f"falling back to {candidate}"
                )
                return candidate

        logger.warning(
            f"[KrakenAdapter] {canonical_symbol} has no equivalent listing on "
            f"Kraken (checked /USDT and fallback quotes {FALLBACK_QUOTES})"
        )
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
        # claude code changed: Step 8 — now reads from the real per-venue
        # table (bot/config/execution_costs.py's VENUE_EXECUTION_COSTS)
        # instead of the local KRAKEN_FEE_RATE constant (removed) +
        # imported SLIPPAGE_RATE. Same returned numbers as before this step.
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


# ============================================================
# FACTORY — env-var driven, self-contained
# ============================================================

def build_kraken_adapter() -> Optional["KrakenAdapter"]:
    """
    Reads KRAKEN_ENABLED/KRAKEN_DRY_RUN/KRAKEN_API_KEY/KRAKEN_API_SECRET
    via bare os.getenv() — matching bot_runner.py's existing Binance
    credential convention exactly (Step 1 confirmed this project uses
    os.getenv() for exchange credentials specifically, decouple.config()
    only for Django-level settings — this follows the former, not the
    latter, since it's an exchange credential).

    Returns None if disabled, or if enabled for live (non-dry-run) trading
    without credentials — never constructs a half-working adapter. This
    function is the actual mechanism behind "disabled Kraken cannot
    execute" and "missing credentials cannot execute": there's no adapter
    object to call at all in either case, not a flag checked at call time
    that a future caller could forget to check.

    Not called anywhere in the live trading loop yet — that wiring is a
    later step, once a multi-venue execution coordinator exists to use it.
    """
    enabled = os.getenv("KRAKEN_ENABLED", "false").strip().lower() in ("1", "true", "yes")
    if not enabled:
        logger.info("[KrakenAdapter] KRAKEN_ENABLED not set — Kraken adapter not constructed.")
        return None

    dry_run = os.getenv("KRAKEN_DRY_RUN", "true").strip().lower() in ("1", "true", "yes")

    api_key = os.getenv("KRAKEN_API_KEY", "")
    api_secret = os.getenv("KRAKEN_API_SECRET", "")

    if not dry_run and (not api_key or not api_secret):
        logger.critical(
            "[KrakenAdapter] KRAKEN_ENABLED=true and KRAKEN_DRY_RUN=false, "
            "but KRAKEN_API_KEY/KRAKEN_API_SECRET are missing. Refusing to "
            "construct a live adapter without credentials — Kraken will not "
            "be available this run."
        )
        return None

    exchange = ccxt.kraken({"apiKey": api_key, "secret": api_secret})

    logger.info(
        f"[KrakenAdapter] Constructed | dry_run={dry_run} | "
        f"credentials_present={bool(api_key and api_secret)}"
    )

    return KrakenAdapter(exchange, dry_run=dry_run)
