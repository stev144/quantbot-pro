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
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Optional, Tuple

import ccxt

from bot.config.execution_costs import FEE_RATE, SLIPPAGE_RATE
from bot.engines.order_manager import (
    OrderStillOpenException,
    OrderUnconfirmedException,
    RateLimitException,
)

logger = logging.getLogger(__name__)


# claude code changed: new — execution-quantity failures. Both subclass
# OrderUnconfirmedException so bot/pairs/execution.py's existing close/open
# handlers already catch them: the pair is disabled for manual review instead
# of an order being submitted for more than is held, or a loan silently left open.
class CloseQuantityUnavailableException(OrderUnconfirmedException):
    """The authoritative free balance cannot cover a valid close order."""


class RepaymentIncompleteException(OrderUnconfirmedException):
    """Borrowed principal and/or interest is still owed after the repay step."""


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
    fee_usdt: Optional[float]   # claude code changed: LIVE-02 — the fee's QUOTE-currency value (quote fees as-is, base-asset fees × this fill's price); None when any fee is in a third asset (e.g. BNB) or the exchange returned no fee information. Was ccxt fee.cost read as USDT regardless of currency.
    fee_asset: str              # claude code changed: LIVE-02 — the single fee currency, "MIXED" if several, "UNKNOWN" if none reported
    fees: Tuple[Tuple[float, str], ...] = ()   # claude code changed: new, LIVE-02 — raw (cost, currency) pairs exactly as the exchange reported them, one per currency


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


def extract_fees(order: dict) -> Tuple[Tuple[float, str], ...]:   # claude code changed: new, LIVE-02
    """Raw (cost, currency) fee pairs from a ccxt-parsed order, one per
    currency (ccxt aggregates fills per currency when building `fees`).
    Returns () when the order carries no fee information at all — which is
    what Binance's order-status (fetch_order) response gives, since it has
    no fills. An empty result means UNKNOWN, never zero."""
    fees = []
    for f in (order.get("fees") or []):
        if f and f.get("cost") is not None and f.get("currency"):
            fees.append((float(f["cost"]), str(f["currency"])))
    if not fees:
        f = order.get("fee") or {}
        if f.get("cost") is not None and f.get("currency"):
            fees.append((float(f["cost"]), str(f["currency"])))
    return tuple(fees)


def split_symbol(symbol: str) -> Tuple[str, str]:   # claude code changed: new, LIVE-02
    """"DODO/USDT" -> ("DODO", "USDT") for a ccxt unified spot symbol."""
    base, quote = symbol.split("/", 1)
    return base, quote.split(":", 1)[0]


def value_fees_in_quote(fees, base: str, quote: str, fill_price: float) -> Optional[float]:   # claude code changed: new, LIVE-02
    """Quote-currency value of raw fees charged on ONE fill:
         quote-currency fee -> its cost
         base-asset fee     -> cost × this fill's price (the rate the fee coins traded at)
         any other asset    -> None (e.g. BNB: no BNB price exists in the fill data,
                               and none is invented)
    () (no fee information) -> None: unknown is not zero."""
    if not fees:
        return None
    total = 0.0
    for cost, currency in fees:
        if currency == quote:
            total += cost
        elif currency == base:
            total += cost * fill_price
        else:
            return None
    return total


def build_fill_result(order_id: str, symbol: str, side: str, created: dict, status: dict) -> "MarginFillResult":   # claude code changed: new, LIVE-02
    """MarginFillResult from the creation response (fees) and the confirmed
    status (filled quantity, average price). Fees are taken from the creation
    response first; the status response is only a fallback."""
    fees = extract_fees(created) or extract_fees(status)
    fill_price = float(status["average"] or status["price"])
    base, quote = split_symbol(symbol)
    currencies = sorted({c for _, c in fees})
    return MarginFillResult(
        order_id=order_id,
        symbol=symbol,
        side=side,
        filled_qty=float(status["filled"]),
        fill_price=fill_price,
        fee_usdt=value_fees_in_quote(fees, base, quote, fill_price),
        fee_asset=currencies[0] if len(currencies) == 1 else ("MIXED" if currencies else "UNKNOWN"),
        fees=fees,
    )


# ── Execution quantity rules ─────────────────────────────────────────
# claude code changed: new. Exchange semantics these rely on (verified in the installed ccxt
# source, ccxt/binance.py): Binance spot/margin fees are 'feeSide': 'get' —
# charged in the asset RECEIVED (base on a buy, quote on a sell) unless paid
# in a third asset such as BNB; order quantities are cut DOWN to the LOT_SIZE
# stepSize (amount_to_precision, TRUNCATE). So a buy of N base units can
# deliver less than N, and a buy of exactly the debt can land below it.
# Quantities are therefore taken from the authoritative isolated-account
# balance/debt, never reconstructed from the entry fill.

@dataclass(frozen=True)
class AmountRules:
    step: Optional[float]      # LOT_SIZE stepSize; None = no rounding (dry run)
    min_qty: Optional[float]   # LOT_SIZE minQty


# Binance quantities have at most 8 decimals; snapping to 1e-12 only removes
# binary floating-point noise (e.g. 0.30000000000000004) so it cannot move a
# value across a step boundary. It is not a trading-precision rounding.
_NOISE = Decimal("1e-12")


def _step_round(qty: float, step: Optional[float], mode) -> float:
    if not step:
        return qty
    q = Decimal(str(qty)).quantize(_NOISE)
    s = Decimal(str(step))
    return float((q / s).to_integral_value(rounding=mode) * s)


def floor_to_step(qty: float, step: Optional[float]) -> float:
    return _step_round(qty, step, ROUND_FLOOR)


def ceil_to_step(qty: float, step: Optional[float]) -> float:
    return _step_round(qty, step, ROUND_CEILING)


def sellable_quantity(requested_qty: float, free_base: float, rules: AmountRules) -> float:
    """Quantity to SELL when closing a long: never more than the authoritative
    free balance (a base-asset buy fee leaves less than was filled), floored to
    the step so the order cannot exceed what is held. Raises
    CloseQuantityUnavailableException rather than submitting an invalid order."""
    q = floor_to_step(min(requested_qty, free_base), rules.step)
    if q <= 0 or (rules.min_qty and q < rules.min_qty):
        raise CloseQuantityUnavailableException(
            f"cannot close: requested {requested_qty}, free {free_base}, step {rules.step}, min {rules.min_qty} -> {q}"
        )
    return q


def cover_buy_quantity(debt_qty: float, free_base: float, fee_buffer_rate: float, rules: AmountRules) -> float:
    """Quantity to BUY to cover a short. The loan (principal + interest, as
    reported by the exchange) must be repaid in full from base that is actually
    free, so: need = debt − base already free; if the buy fee is charged in the
    base asset at rate r, a buy of q delivers q·(1−r), so buy need/(1−r); round
    UP to the step (ccxt would otherwise truncate below the debt); never below
    the minimum quantity. Returns 0.0 when free base already covers the debt."""
    if not (0.0 <= fee_buffer_rate < 1.0):
        raise ValueError(f"fee_buffer_rate must be in [0, 1), got {fee_buffer_rate}")
    need = debt_qty - free_base
    if need <= 0:
        return 0.0
    q = ceil_to_step(need / (1.0 - fee_buffer_rate), rules.step)
    if rules.min_qty and q < rules.min_qty:
        q = ceil_to_step(rules.min_qty, rules.step)
    return q


def remaining_debt_or_raise(symbol: str, borrowed: float, interest: float) -> float:
    """Post-repay check: any principal or interest still owed is an error, never silent."""
    remaining = borrowed + interest
    if remaining > 0:
        raise RepaymentIncompleteException(
            f"{symbol}: {remaining} base still owed after repay (borrowed {borrowed}, interest {interest})"
        )
    return 0.0


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
                # claude code changed: LIVE-02 — fees now come from the order-CREATION
                # response (`order`), which ccxt requests with newOrderRespType=FULL
                # for spot/margin and which carries per-fill commission +
                # commissionAsset. The fetch_order status response read before has
                # no fills, so ccxt returns fee=None there and this used to record a
                # $0 fee labelled USDT (or, if a fee was present, its raw amount as
                # USDT whatever its currency). See build_fill_result().
                fill = build_fill_result(order_id, symbol, side, order, status)
                if fill.fee_usdt is None:
                    logger.critical(
                        f"[MarginAdapter] {symbol} {side} order {order_id}: fee could not be valued in quote currency "
                        f"(raw fees: {fill.fees or 'none reported'}). Recorded P&L for this leg will be marked UNVALUED."
                    )
                return fill
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
            fees=((fee_usdt, "USDT"),),   # claude code changed: new, LIVE-02 — the simulated fee is charged in USDT
        )

    # ── Execution quantity inputs ────────────────────────────────────
    # claude code changed: new

    def amount_rules(self, symbol: str) -> AmountRules:
        """LOT_SIZE step/min from ccxt market metadata. Dry run: none — the
        simulator neither truncates quantities nor charges base-asset fees."""
        if self.dry_run:
            return AmountRules(step=None, min_qty=None)
        market = self.exchange.market(symbol)
        return AmountRules(step=market["precision"]["amount"], min_qty=(market.get("limits") or {}).get("amount", {}).get("min"))

    def min_notional(self, symbol: str) -> Optional[float]:   # claude code changed: Stage 2D Rev B step 8 — exchange minNotional for the entry executability check
        """MIN_NOTIONAL (quote) from ccxt market metadata. Dry run: None — the
        simulator applies no exchange filters. Real mode: raises rather than
        guessing when the metadata has no value."""
        if self.dry_run:
            return None
        cost_min = ((self.exchange.market(symbol).get("limits") or {}).get("cost") or {}).get("min")
        if cost_min is None:
            raise ValueError(f"{symbol}: no minNotional (limits.cost.min) in market metadata — cannot verify executability")
        return float(cost_min)

    def fee_buffer_rate(self, symbol: str) -> float:
        """Worst-case base-asset fee rate used to size a short-cover buy: the
        market taker rate from ccxt metadata (fees are 'feeSide': 'get'). Dry
        run: 0 — simulated fees are always USDT. Raises rather than guessing
        when the metadata has no rate."""
        if self.dry_run:
            return 0.0
        rate = self.exchange.market(symbol).get("taker")
        if rate is None:
            raise ValueError(f"{symbol}: no taker fee rate in market metadata — cannot size the cover buy safely")
        return float(rate)

    def market_buy_to_cover(self, symbol: str, quantity: float) -> MarginFillResult:
        """Buy base to repay a short (no repay here — see repay())."""
        fill = self._place_margin_market_order(symbol, "buy", quantity)
        if self.dry_run:
            sim = self._sim(symbol)
            sim["base_free"] += fill.filled_qty
            sim["quote_free"] -= fill.fill_price * fill.filled_qty + fill.fee_usdt
        return fill

    def repay(self, symbol: str, base_quantity: float) -> dict:
        """Repay base_quantity of the borrowed base asset from free base."""
        base = self.exchange.market(symbol)["base"]
        if self.dry_run:
            logger.info(f"[MarginAdapter:DRY_RUN] repay {base_quantity} {base} on {symbol} isolated")
            sim = self._sim(symbol)
            sim["base_free"] -= base_quantity
            sim["base_borrowed"] -= base_quantity
            return {"simulated": True, "symbol": symbol, "asset": base, "amount": base_quantity}
        return self.exchange.repay_isolated_margin(symbol, base, base_quantity)

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
        base_quantity = floor_to_step(base_quantity, self.amount_rules(symbol).step)   # claude code changed: borrow exactly what the (step-truncated) sell will sell — previously the unrounded amount was borrowed and the remainder sat idle accruing interest. No-op in dry run.
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
        # claude code changed: now composed of market_buy_to_cover() + repay(); same result
        # as before in dry run. Kept for compatibility — bot/pairs/execution.py no
        # longer calls it: it sizes the buy with cover_buy_quantity() and repays
        # only what is actually free (see PairsExecutionEngine._cover_and_repay).
        fill = self.market_buy_to_cover(symbol, base_quantity)
        repay_result = self.repay(symbol, base_quantity)
        return fill, repay_result
