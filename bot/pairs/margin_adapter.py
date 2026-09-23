# ============================================================
# bot/pairs/margin_adapter.py
# claude code changed: new file — live pairs-trading pilot, Step 3.
#
# Binance ISOLATED MARGIN execution — the one capability this codebase
# has never had (bot/engines/exchange_adapter.py's ABC is explicitly
# spot-only; see its module docstring's "get_position()" note: "this
# codebase trades spot only; there is no discrete position object to
# query... the way futures/margin APIs have one"). This does NOT
# implement that ABC — margin genuinely needs different methods
# (transfer/borrow/repay) that spot has no equivalent of, so this is a
# deliberate fork, not a violation of the existing contract.
#
# Required because the validated pairs strategy is market-neutral (long
# one leg, short the other — see bot/research/entry_exit_engine.py's
# TradeRecord docstring) and Binance spot cannot short. Isolated margin
# was chosen over cross margin (contains a blowup to one pair's own
# collateral, not the whole account) and over Futures (DODO/USDT has no
# futures listing, confirmed live — Futures can't cover all 3 pilot
# pairs uniformly).
#
# ccxt method names/signatures below were verified directly against the
# installed ccxt version's actual binance.py source (not assumed from
# docs), since a wrong param name here fails silently or expensively
# with real money:
#   - create_order(symbol, 'market', side, amount, params={'marginMode': 'isolated'})
#     -> binance.py's create_order_request sets request['isIsolated']=True
#        when marginMode=='isolated' (confirmed by reading the source).
#   - borrow_isolated_margin(symbol, code, amount) / repay_isolated_margin(...)
#     -> POST sapi/v1/margin/borrow-repay with isIsolated='TRUE'.
#   - transfer(code, amount, fromAccount, toAccount, params={'symbol': symbol})
#     -> 'spot'->'isolated' and 'isolated'->'spot' both require params['symbol']
#        (confirmed: binance.py's transfer() raises ArgumentsRequired
#        without it when either side is 'isolated').
#   - fetch_balance(params={'marginMode': 'isolated', 'symbols': [symbol]})
#     -> real isolated-account balance/loan state (sapiGetMarginIsolatedAccount).
#   - fetch_isolated_borrow_rate(symbol) -> real hourly interest rate,
#     a real cost this project's execution_costs.py has never modeled
#     (spot has nothing to borrow).
#
# DRY_RUN semantics match this project's existing convention exactly
# (OrderManager._simulate_fill()): dry_run=True never sends a write
# request (transfer/borrow/repay/order) to Binance — only read-only
# calls (get_ticker, get_isolated_account, get_borrow_rate) touch the
# real API, and order fills/interest are simulated using REAL prices/
# rates fetched from those read-only calls, not fabricated numbers.
#
# Order confirmation reuses OrderManager's existing exception types
# (OrderStillOpenException/OrderUnconfirmedException/RateLimitException)
# so a caller (bot/pairs/execution.py) handles a failed margin order leg
# identically to how it already handles a failed spot order — no new
# failure-mode vocabulary. Retry/backoff for the order-placement call
# itself is intentionally a single bounded loop, not OrderManager's full
# state machine — transfer/borrow/repay calls are single-attempt (they
# are lower-frequency, and Binance's borrow-repay endpoint is already
# idempotent per tranId on the exchange side).
# ============================================================

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import ccxt

from bot.config.execution_costs import FEE_RATE, SLIPPAGE_RATE
from bot.engines.order_manager import (
    OrderStillOpenException,
    OrderUnconfirmedException,
    RateLimitException,
)

logger = logging.getLogger(__name__)

ORDER_CONFIRM_MAX_ATTEMPTS = 5
ORDER_CONFIRM_POLL_SECONDS = 2
RATE_LIMIT_MAX_RETRIES = 3
RATE_LIMIT_BACKOFF_SECONDS = 5


@dataclass
class MarginFillResult:
    order_id: str
    symbol: str
    side: str
    filled_qty: float
    fill_price: float
    fee_usdt: float
    fee_asset: str


@dataclass
class IsolatedAccountState:
    symbol: str
    base_asset: str
    quote_asset: str
    base_free: float
    base_borrowed: float
    base_interest: float
    quote_free: float
    quote_borrowed: float
    quote_interest: float


class BinanceIsolatedMarginAdapter:
    """One instance per Binance account/credential set — NOT per pair.
    Every method takes `symbol` (ccxt unified, e.g. "DODO/USDT") since a
    stat-arb pair's two legs are two independent isolated-margin
    accounts on Binance (see module docstring in bot/pairs/execution.py
    for the full sequencing this drives).
    """

    def __init__(self, exchange: ccxt.binance, dry_run: bool = True):
        self.exchange = exchange
        self.dry_run = dry_run
        # dry-run-only simulated ledger: {symbol: {"quote_free", "base_free", "base_borrowed"}}
        # never consulted when dry_run=False — real state always comes from Binance.
        # Exists so dry-run mode works with ZERO real API credentials, matching
        # this project's existing convention (MarketData.get_balance() returns
        # DRY_RUN_PAPER_BALANCE, never a real authenticated call, when dry_run=True) —
        # sapiGetMarginIsolatedAccount is a signed/private endpoint even though it's
        # read-only, so calling it for real in dry-run would defeat that convention.
        self._sim_state: dict = {}

    def _sim(self, symbol: str) -> dict:
        return self._sim_state.setdefault(symbol, {"quote_free": 0.0, "base_free": 0.0, "base_borrowed": 0.0})

    # ── Read-only (always real, even in dry-run) ──────────────────────

    def get_isolated_account(self, symbol: str) -> IsolatedAccountState:
        """Real isolated-margin account state for one symbol when
        dry_run=False — this is what pairs_bot_runner.py's startup
        reconciliation gate checks against DB-recorded open trades
        before allowing new ones. In dry-run, returns this adapter's own
        simulated ledger instead of calling Binance's real (signed,
        credential-requiring) endpoint — see __init__'s docstring."""
        if self.dry_run:
            sim = self._sim(symbol)
            market = self.exchange.market(symbol)
            return IsolatedAccountState(
                symbol=symbol,
                base_asset=market["base"],
                quote_asset=market["quote"],
                base_free=sim["base_free"],
                base_borrowed=sim["base_borrowed"],
                base_interest=0.0,  # not modeled in simulation — see get_borrow_rate() for real rate lookups
                quote_free=sim["quote_free"],
                quote_borrowed=0.0,  # this design only ever borrows the BASE asset (the shorted leg), never quote
                quote_interest=0.0,
            )
        balance = self.exchange.fetch_balance(params={"marginMode": "isolated", "symbols": [symbol]})
        market = self.exchange.market(symbol)
        base, quote = market["base"], market["quote"]
        info = balance.get("info", {})
        assets = info.get("assets", [])
        row = next((a for a in assets if a.get("symbol") == market["id"]), None)
        if row is None:
            raise ValueError(f"No isolated margin account found for {symbol} — is it isolated-margin-enabled on this account?")
        base_info = row.get("baseAsset", {})
        quote_info = row.get("quoteAsset", {})
        return IsolatedAccountState(
            symbol=symbol,
            base_asset=base,
            quote_asset=quote,
            base_free=float(base_info.get("free", 0.0)),
            base_borrowed=float(base_info.get("borrowed", 0.0)),
            base_interest=float(base_info.get("interest", 0.0)),
            quote_free=float(quote_info.get("free", 0.0)),
            quote_borrowed=float(quote_info.get("borrowed", 0.0)),
            quote_interest=float(quote_info.get("interest", 0.0)),
        )

    def get_borrow_rate(self, symbol: str) -> float:
        """Real hourly interest rate for the BASE asset of this isolated
        symbol (the asset a short leg would borrow). Binance returns a
        daily rate; this returns it as an hourly fraction since the
        strategy's exit_time_stop_hours=4 makes hourly the natural unit
        for cost estimation."""
        rate = self.exchange.fetch_isolated_borrow_rate(symbol)
        daily_rate = float(rate.get("rate") or rate.get("info", {}).get("dailyInterest", 0.0))
        return daily_rate / 24.0

    def get_ticker_price(self, symbol: str) -> float:
        ticker = self.exchange.fetch_ticker(symbol)
        return float(ticker["last"])

    # ── Collateral transfer ────────────────────────────────────────────

    def transfer_in(self, symbol: str, quote_amount: float) -> dict:
        """Moves quote-asset (USDT) collateral from spot into this
        symbol's isolated margin account."""
        market = self.exchange.market(symbol)
        quote = market["quote"]
        if self.dry_run:
            logger.info(f"[MarginAdapter:DRY_RUN] transfer_in {quote_amount} {quote} -> {symbol} isolated")
            self._sim(symbol)["quote_free"] += quote_amount
            return {"simulated": True, "symbol": symbol, "amount": quote_amount}
        return self.exchange.transfer(quote, quote_amount, "spot", "isolated", params={"symbol": symbol})

    def transfer_out(self, symbol: str, quote_amount: float) -> dict:
        """Moves quote-asset (USDT) collateral back from this symbol's
        isolated margin account to spot. Only safe to call once both the
        base and quote positions are flat (no open long/short, no
        outstanding loan) — callers (bot/pairs/execution.py) must
        confirm that via get_isolated_account() first."""
        market = self.exchange.market(symbol)
        quote = market["quote"]
        if self.dry_run:
            logger.info(f"[MarginAdapter:DRY_RUN] transfer_out {quote_amount} {quote} <- {symbol} isolated")
            self._sim(symbol)["quote_free"] -= quote_amount
            return {"simulated": True, "symbol": symbol, "amount": quote_amount}
        return self.exchange.transfer(quote, quote_amount, "isolated", "spot", params={"symbol": symbol})

    # ── Order placement (shared retry/confirm path) ────────────────────

    def _place_margin_market_order(self, symbol: str, side: str, quantity: float) -> MarginFillResult:
        if self.dry_run:
            return self._simulate_fill(symbol, side, quantity)

        for attempt in range(RATE_LIMIT_MAX_RETRIES):
            try:
                order = self.exchange.create_order(
                    symbol, "market", side, quantity, params={"marginMode": "isolated"}
                )
                break
            except ccxt.RateLimitExceeded as e:
                if attempt == RATE_LIMIT_MAX_RETRIES - 1:
                    raise RateLimitException(f"{symbol} margin {side} order: rate limit exhausted after {RATE_LIMIT_MAX_RETRIES} attempts: {e}")
                time.sleep(RATE_LIMIT_BACKOFF_SECONDS)
        else:
            raise RateLimitException(f"{symbol} margin {side} order: rate limit exhausted")

        order_id = order["id"]
        for _ in range(ORDER_CONFIRM_MAX_ATTEMPTS):
            status = self.exchange.fetch_order(order_id, symbol, params={"marginMode": "isolated"})
            if status["status"] == "closed":
                return MarginFillResult(
                    order_id=order_id,
                    symbol=symbol,
                    side=side,
                    filled_qty=float(status["filled"]),
                    fill_price=float(status["average"] or status["price"]),
                    fee_usdt=float((status.get("fee") or {}).get("cost", 0.0)),
                    fee_asset=(status.get("fee") or {}).get("currency", "USDT"),
                )
            if status["status"] == "canceled":
                raise OrderUnconfirmedException(f"{symbol} margin {side} order {order_id} was canceled before fill")
            time.sleep(ORDER_CONFIRM_POLL_SECONDS)

        raise OrderStillOpenException(f"{symbol} margin {side} order {order_id} still open after {ORDER_CONFIRM_MAX_ATTEMPTS} confirmation attempts")

    def _simulate_fill(self, symbol: str, side: str, quantity: float) -> MarginFillResult:
        """Matches OrderManager._simulate_fill()'s convention: real
        reference price via a real ticker fetch, adverse slippage
        applied in the trader's disfavor, fee on notional. No real
        order/borrow/repay/transfer touches Binance in this path."""
        reference_price = self.get_ticker_price(symbol)
        if side == "buy":
            fill_price = reference_price * (1 + SLIPPAGE_RATE)
        else:
            fill_price = reference_price * (1 - SLIPPAGE_RATE)
        fee_usdt = round(fill_price * quantity * FEE_RATE, 6)
        logger.info(f"[MarginAdapter:DRY_RUN] {side} {quantity} {symbol} @ {fill_price:.6f} (fee ${fee_usdt:.4f})")
        return MarginFillResult(
            order_id=f"dryrun-{symbol}-{side}-{time.time_ns()}",
            symbol=symbol,
            side=side,
            filled_qty=quantity,
            fill_price=fill_price,
            fee_usdt=fee_usdt,
            fee_asset="USDT",
        )

    # ── Leg lifecycle ────────────────────────────────────────────────

    def open_long(self, symbol: str, quantity: float) -> MarginFillResult:
        """Buys on isolated margin using only the transferred-in
        collateral — no borrow needed for a long leg."""
        fill = self._place_margin_market_order(symbol, "buy", quantity)
        if self.dry_run:
            sim = self._sim(symbol)
            sim["base_free"] += fill.filled_qty
            sim["quote_free"] -= fill.fill_price * fill.filled_qty + fill.fee_usdt
        return fill

    def close_long(self, symbol: str, quantity: float) -> MarginFillResult:
        fill = self._place_margin_market_order(symbol, "sell", quantity)
        if self.dry_run:
            sim = self._sim(symbol)
            sim["base_free"] -= fill.filled_qty
            sim["quote_free"] += fill.fill_price * fill.filled_qty - fill.fee_usdt
        return fill

    def open_short(self, symbol: str, base_quantity: float) -> tuple[dict, MarginFillResult]:
        """Borrows base_quantity of the base asset, then sells it —
        the actual short. Returns (borrow_result, sell_fill)."""
        market = self.exchange.market(symbol)
        base = market["base"]
        if self.dry_run:
            logger.info(f"[MarginAdapter:DRY_RUN] borrow {base_quantity} {base} on {symbol} isolated")
            self._sim(symbol)["base_borrowed"] += base_quantity
            borrow_result = {"simulated": True, "symbol": symbol, "asset": base, "amount": base_quantity}
        else:
            borrow_result = self.exchange.borrow_isolated_margin(symbol, base, base_quantity)
        fill = self._place_margin_market_order(symbol, "sell", base_quantity)
        if self.dry_run:
            # the sold units were borrowed, not held free — only quote_free
            # (sale proceeds) moves here; base_borrowed was already recorded above.
            self._sim(symbol)["quote_free"] += fill.fill_price * fill.filled_qty - fill.fee_usdt
        return borrow_result, fill

    def close_short(self, symbol: str, base_quantity: float) -> tuple[MarginFillResult, dict]:
        """Buys back base_quantity of the base asset, then repays the
        loan. Returns (buy_fill, repay_result). Callers must repay the
        REAL outstanding loan amount (principal + accrued interest, from
        get_isolated_account()), not just the original borrowed
        quantity — interest accrues hourly and this method does not
        look it up itself, by design: the caller (execution.py) already
        has to call get_isolated_account() right before this to size the
        buy-back correctly, so it passes the authoritative repay amount
        as base_quantity directly."""
        fill = self._place_margin_market_order(symbol, "buy", base_quantity)
        market = self.exchange.market(symbol)
        base = market["base"]
        if self.dry_run:
            logger.info(f"[MarginAdapter:DRY_RUN] repay {base_quantity} {base} on {symbol} isolated")
            sim = self._sim(symbol)
            sim["quote_free"] -= fill.fill_price * fill.filled_qty + fill.fee_usdt
            sim["base_borrowed"] -= base_quantity
            repay_result = {"simulated": True, "symbol": symbol, "asset": base, "amount": base_quantity}
        else:
            repay_result = self.exchange.repay_isolated_margin(symbol, base, base_quantity)
        return fill, repay_result
