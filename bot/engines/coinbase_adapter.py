# ============================================================
# bot/engines/coinbase_adapter.py
# claude code changed: new file — 2026-10-22 (owner: "add Coinbase … only three [venues] will be enough"). Third
# ExchangeAdapter: Coinbase Advanced Trade through ccxt "coinbase" (verified 2026-10-22 against the live PUBLIC API:
# 1,156 markets, order-book levels already [price, amount], amount/cost limits populated; "coinbaseexchange" was
# rejected — it returns 20k+ un-trimmed levels with no amount limits).
#
# Venue-specific facts (verified on the live public listing, not assumed):
#   - Many alts are listed only against USD (e.g. AAVE/USDT absent, AAVE/USD present); USDC is the second fallback.
#     A fallback changes the QUOTE currency — the Venue Comparison page labels it.
#   - Fee: base-tier taker 0.60 % per secondary sources; UNVERIFIED (official page blocked) — VENUE_FEE_PROVENANCE.
#
# SAFETY: only dry-run fill simulation and public market data are exercised in this project. A live stop-loss is
# REFUSED (raises) — the Coinbase Advanced stop-limit request shape has not been verified against an authenticated
# account. Not wired into bot_runner.py; build_coinbase_adapter() refuses a live adapter without credentials.
# ============================================================

import logging
import os
from typing import Optional

import ccxt

from bot.engines.ccxt_spot_adapter import CcxtSpotAdapter

logger = logging.getLogger(__name__)

FALLBACK_QUOTES: list = ["USD", "USDC"]


class CoinbaseLiveStopUnverified(RuntimeError):
    """A live resting stop on Coinbase was requested; its request shape is unverified, so it is refused."""


class CoinbaseAdapter(CcxtSpotAdapter):
    FALLBACK_QUOTES = FALLBACK_QUOTES

    @property
    def venue_id(self) -> str:
        return "coinbase"

    def place_stop_loss(self, symbol: str, side: str, quantity: float, stop_price: float) -> dict:
        logger.info(f"[CoinbaseAdapter] STOP LOSS requested | {side.upper()} | {symbol} | Qty: {quantity} | Stop: {stop_price}")
        if self.dry_run:
            return {"order_id": None, "stop_price": stop_price, "status": "dry_run_skipped",
                    "symbol": symbol, "side": side.lower()}
        raise CoinbaseLiveStopUnverified(
            "Live Coinbase stop-loss placement is disabled: the Advanced Trade stop-limit request has not been "
            "verified against an authenticated account. Protect the position another way.")


def build_coinbase_adapter() -> Optional[CoinbaseAdapter]:
    """COINBASE_ENABLED / COINBASE_DRY_RUN (default true) / COINBASE_API_KEY / COINBASE_API_SECRET via os.getenv(),
    the same convention as build_kraken_adapter(). None when disabled, or when live without credentials."""
    enabled = os.getenv("COINBASE_ENABLED", "false").strip().lower() in ("1", "true", "yes")
    if not enabled:
        logger.info("[CoinbaseAdapter] COINBASE_ENABLED not set — Coinbase adapter not constructed.")
        return None
    dry_run = os.getenv("COINBASE_DRY_RUN", "true").strip().lower() in ("1", "true", "yes")
    api_key, api_secret = os.getenv("COINBASE_API_KEY", ""), os.getenv("COINBASE_API_SECRET", "")
    if not dry_run and (not api_key or not api_secret):
        logger.critical("[CoinbaseAdapter] COINBASE_DRY_RUN=false but COINBASE_API_KEY/COINBASE_API_SECRET are missing — "
                        "refusing to construct a live adapter.")
        return None
    exchange = ccxt.coinbase({"apiKey": api_key, "secret": api_secret})
    logger.info(f"[CoinbaseAdapter] Constructed | dry_run={dry_run} | credentials_present={bool(api_key and api_secret)}")
    return CoinbaseAdapter(exchange, dry_run=dry_run)
