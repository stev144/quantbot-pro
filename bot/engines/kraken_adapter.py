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

from bot.engines.ccxt_spot_adapter import CcxtSpotAdapter   # claude code changed: 2026-10-22 — shared ccxt spot base (Coinbase reuses it)

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


class KrakenAdapter(CcxtSpotAdapter):   # claude code changed: 2026-10-22 — was ExchangeAdapter; generic methods moved verbatim to CcxtSpotAdapter
    """
    ExchangeAdapter implementation for Kraken. Same shape as BinanceAdapter:
    constructs and delegates to OrderManager/MarketData internally (in the
    shared CcxtSpotAdapter.__init__, which keeps the Step 8 venue-correct
    dry-run fee/slippage wiring) for everything that isn't venue-specific.
    """

    FALLBACK_QUOTES = FALLBACK_QUOTES   # claude code changed: 2026-10-22 — consumed by CcxtSpotAdapter.normalize_symbol()

    @property
    def venue_id(self) -> str:
        return "kraken"

    # claude code changed: 2026-10-22 — get_ticker/get_ohlcv/get_balance/place_order are inherited from CcxtSpotAdapter (moved verbatim)

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

    # claude code changed: 2026-10-22 — cancel/get order, order book (levels trimmed to [price, amount]), symbol/
    # quantity/price normalisation, validate_order, get_execution_costs and validate_connection are inherited from
    # CcxtSpotAdapter (moved verbatim; normalize_symbol now also refuses markets ccxt marks inactive — audit fix).


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
