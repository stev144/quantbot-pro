# ============================================================
# bot/pairs/execution.py
# claude code changed: new file — live pairs-trading pilot, Step 4.
#
# PairsExecutionEngine: the two-leg orchestrator. Deliberately does NOT
# wrap or subclass bot/engines/execution_engine.py's ExecutionEngine —
# that class assumes plain spot buy/sell with no borrow/repay/transfer
# steps, and forcing margin semantics into it would corrupt the
# single-symbol regime bot's contract (see the approved plan at
# C:\Users\HP\.claude\plans\dazzling-zooming-origami.md). This is a new,
# parallel orchestrator built on bot/pairs/margin_adapter.py.
#
# Direction -> leg sides (from entry_exit_engine.py's TradeRecord
# docstring, confirmed by direct read, not assumed):
#   LONG_SPREAD  : LONG leg A (symbol_a)  + SHORT leg B (symbol_b) x beta
#   SHORT_SPREAD : SHORT leg A (symbol_a) + LONG leg B (symbol_b) x beta
#
# No cross-leg atomicity is possible (confirmed: Binance has no
# multi-leg atomic order type). If leg B fails after leg A opens, the
# compensating action is: immediately attempt to close leg A back out,
# log a LEG_MISMATCH critical alert, and refuse further trades on this
# pair until manually cleared (see _mismatched_pairs).
#
# Reuses bot/risk/drawdown_guard.py's DrawdownGuard AS-IS (not a new
# "MarkToMarketDrawdownGuard" class) — its update(balance)->bool logic
# is already generic; the only thing that needs to differ for a
# strategy holding concurrent positions is WHAT VALUE gets passed to
# update(), which is this module's job (compute_mark_to_market_equity),
# not the guard's. Writing a whole new guard class to change one call
# site's input would have been needless duplication.
# ============================================================

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from bot.engines.order_manager import (
    OrderStillOpenException,
    OrderUnconfirmedException,
    RateLimitException,
)
from bot.pairs.config import PairConfig
from bot.pairs.margin_adapter import BinanceIsolatedMarginAdapter, CloseQuantityUnavailableException, cover_buy_quantity, floor_to_step, remaining_debt_or_raise, sellable_quantity   # claude code changed: quantity helpers; Stage 2D Rev B step 8 — floor_to_step for entry executability
from bot.pairs.signal_engine import SignalDecision
from bot.pairs.trade_logger import PairsTradeLogger
from bot.risk.drawdown_guard import DrawdownGuard

logger = logging.getLogger(__name__)


@dataclass
class OpenPairPosition:
    pair_trade_id: uuid.UUID
    pair_name: str
    direction: str
    symbol_a: str
    symbol_b: str
    side_a: str  # "BUY" or "SELL"
    side_b: str
    qty_a: float
    qty_b: float
    entry_fees_a: tuple = ()   # claude code changed: new, LIVE-02 — raw (cost, currency) entry fees, carried to the exit log so a non-quote fee (e.g. BNB) is never re-read from TradeRecord.fee_entry as if it were USDT
    entry_fees_b: tuple = ()   # claude code changed: new, LIVE-02


class PairsExecutionEngine:
    """One instance covers ALL pilot pairs on one Binance account —
    holds a single BinanceIsolatedMarginAdapter (one set of API
    credentials), a shared DrawdownGuard scoped to this pilot's own
    sub-portfolio, and per-pair open-position tracking."""

    def __init__(self, exchange, dry_run: bool = True):
        self.adapter = BinanceIsolatedMarginAdapter(exchange, dry_run=dry_run)
        self.trade_logger = PairsTradeLogger()
        self.drawdown_guard = DrawdownGuard()
        self.dry_run = dry_run
        self._open_positions: dict = {}  # pair_name -> OpenPairPosition
        self._mismatched_pairs: set = set()  # pairs a LEG_MISMATCH has disabled — manual clear required

    def is_pair_tradeable(self, pair_name: str) -> bool:
        if pair_name in self._mismatched_pairs:
            logger.warning(f"[PairsExecutionEngine] {pair_name} is disabled after a prior LEG_MISMATCH — refusing new trades until manually cleared.")
            return False
        return True

    def disable_pair(self, pair_name: str, reason: str) -> None:
        """Public entry point for a caller (e.g. pairs_bot_runner.py's
        startup reconciliation) to disable a pair for a reason other than
        a live LEG_MISMATCH — e.g. unreconciled OPEN state found at
        startup. Same effect as a LEG_MISMATCH: is_pair_tradeable()
        returns False until manually cleared."""
        logger.critical(f"[PairsExecutionEngine] Disabling {pair_name}: {reason}")
        self._mismatched_pairs.add(pair_name)

    def has_open_position(self, pair_name: str) -> bool:
        return pair_name in self._open_positions

    # ── Mark-to-market equity (feeds the reused DrawdownGuard) ──────────

    def compute_mark_to_market_equity(self, pair_configs: list[PairConfig]) -> float:
        """free collateral + unrealized value of every open leg, across
        every isolated-margin account this pilot uses — minus outstanding
        borrowed value. Real reads only (get_isolated_account/get_ticker_price),
        never derived from in-memory assumptions, so a crash/restart still
        gets an accurate number on the next call."""
        total = 0.0
        seen_symbols = set()
        for cfg in pair_configs:
            for symbol in (cfg.binance_symbol_a, cfg.binance_symbol_b):
                if symbol in seen_symbols:
                    continue
                seen_symbols.add(symbol)
                try:
                    account = self.adapter.get_isolated_account(symbol)
                except Exception as e:
                    logger.error(f"[PairsExecutionEngine] Could not fetch isolated account for {symbol} while computing equity: {e}")
                    continue
                price = self.adapter.get_ticker_price(symbol)
                base_value = (account.base_free - account.base_borrowed - account.base_interest) * price
                quote_value = account.quote_free - account.quote_borrowed - account.quote_interest
                total += base_value + quote_value
        return total

    # ── Open ─────────────────────────────────────────────────────────

    def open_pair_trade(self, pair_config: PairConfig, decision: SignalDecision) -> Optional[uuid.UUID]:
        if decision.action != "ENTER" or decision.trade is None:
            raise ValueError("open_pair_trade requires an ENTER decision with a trade")
        if not self.is_pair_tradeable(pair_config.pair_name):
            return None
        if pair_config.pair_name in self._open_positions:
            logger.error(f"[PairsExecutionEngine] {pair_config.pair_name} already has an open position — refusing to open a second.")
            return None

        trade = decision.trade
        symbol_a, symbol_b = pair_config.binance_symbol_a, pair_config.binance_symbol_b
        pair_trade_id = uuid.uuid4()

        # claude code changed: Stage 2D Rev B step 8 — executability BEFORE any
        # transfer or order: both legs floored to stepSize and checked against
        # minQty / minNotional at the current marks (real mode). Was qty =
        # leg_usdt / price with no flooring or minimum check.
        try:
            qty_a, qty_b, executability = self._entry_quantities(trade, symbol_a, symbol_b)
        except Exception as e:
            logger.error(f"[PairsExecutionEngine] {pair_config.pair_name}: ENTRY REFUSED — not executable: {e}. No transfer, no order.")
            return None
        trade.executability = executability

        try:
            self.adapter.transfer_in(symbol_a, trade.leg_a_usdt)
            self.adapter.transfer_in(symbol_b, trade.leg_b_usdt)
        except Exception as e:
            logger.critical(f"[PairsExecutionEngine] {pair_config.pair_name}: collateral transfer-in failed before any order placed: {e}. No position opened.")
            return None

        try:
            if trade.direction == "LONG_SPREAD":   # claude code changed: Stage 2D Rev B step 8 — quantities come from _entry_quantities (floored + checked)
                side_a, side_b = "BUY", "SELL"
                fill_a = self.adapter.open_long(symbol_a, qty_a)
                _borrow_b, fill_b = self.adapter.open_short(symbol_b, qty_b)
            else:  # SHORT_SPREAD
                side_a, side_b = "SELL", "BUY"
                _borrow_a, fill_a = self.adapter.open_short(symbol_a, qty_a)
                fill_b = self.adapter.open_long(symbol_b, qty_b)
        except (OrderStillOpenException, OrderUnconfirmedException, RateLimitException) as e:
            logger.critical(
                f"[PairsExecutionEngine] LEG_MISMATCH: {pair_config.pair_name} failed mid-open ({e}). "
                f"Attempting to unwind whatever opened; disabling this pair until manually cleared."
            )
            self._mismatched_pairs.add(pair_config.pair_name)
            self._attempt_partial_unwind(symbol_a, symbol_b)
            self._refund_collateral_best_effort(symbol_a, symbol_b)
            return None

        self.trade_logger.log_leg_entry(pair_trade_id, "A", symbol_a, side_a, fill_a, trade.leg_a_usdt, pair_config.pair_name)
        self.trade_logger.log_leg_entry(pair_trade_id, "B", symbol_b, side_b, fill_b, trade.leg_b_usdt, pair_config.pair_name)

        self._open_positions[pair_config.pair_name] = OpenPairPosition(
            pair_trade_id=pair_trade_id,
            pair_name=pair_config.pair_name,
            direction=trade.direction,
            symbol_a=symbol_a,
            symbol_b=symbol_b,
            side_a=side_a,
            side_b=side_b,
            qty_a=fill_a.filled_qty,
            qty_b=fill_b.filled_qty,
            entry_fees_a=fill_a.fees,   # claude code changed: new, LIVE-02
            entry_fees_b=fill_b.fees,   # claude code changed: new, LIVE-02
        )
        logger.info(f"[PairsExecutionEngine] Opened {pair_config.pair_name} {trade.direction} | pair_trade_id={pair_trade_id}")
        return pair_trade_id

    def _entry_quantities(self, trade, symbol_a: str, symbol_b: str):   # claude code changed: Stage 2D Rev B step 8 — new
        """(qty_a, qty_b, executability). Real mode: each leg floored to the
        exchange stepSize and required to clear minQty and minNotional at the
        current mark, else ValueError (the caller refuses the entry before any
        transfer). Dry run: unfloored, marked UNVERIFIED — the simulator has no
        exchange filters, so executability is not claimed."""
        out = []
        for symbol, leg_usdt in ((symbol_a, trade.leg_a_usdt), (symbol_b, trade.leg_b_usdt)):
            if not (leg_usdt > 0):
                raise ValueError(f"{symbol}: non-positive leg notional {leg_usdt}")
            price = self.adapter.get_ticker_price(symbol)
            raw = leg_usdt / price
            if self.dry_run:
                out.append(raw)
                continue
            rules = self.adapter.amount_rules(symbol)
            qty = floor_to_step(raw, rules.step)
            if qty <= 0 or (rules.min_qty is not None and qty < rules.min_qty):
                raise ValueError(f"{symbol}: floored quantity {qty} below minQty {rules.min_qty} (raw {raw}, step {rules.step})")
            min_notional = self.adapter.min_notional(symbol)
            if qty * price < min_notional:
                raise ValueError(f"{symbol}: notional {qty * price:.6f} below minNotional {min_notional}")
            out.append(qty)
        return out[0], out[1], ("UNVERIFIED (dry run)" if self.dry_run else "VERIFIED")

    def _attempt_partial_unwind(self, symbol_a: str, symbol_b: str) -> None:
        """Best-effort close of whichever leg(s) actually opened before
        the failure. Never raises — a failure here is logged, not
        propagated, since the caller is already in its own failure path
        and the pair is already marked disabled for manual review."""
        for symbol in (symbol_a, symbol_b):
            try:
                account = self.adapter.get_isolated_account(symbol)
                if account.base_free > 0:
                    self.adapter.close_long(symbol, account.base_free)
                if account.base_borrowed > 0:
                    self._cover_and_repay(symbol)   # claude code changed: was close_short(borrowed + interest): same base-fee / truncation shortfall as the normal close
            except Exception as e:
                logger.critical(f"[PairsExecutionEngine] Unwind attempt for {symbol} also failed: {e}. MANUAL INTERVENTION REQUIRED.")

    def _refund_collateral_best_effort(self, symbol_a: str, symbol_b: str) -> None:
        for symbol in (symbol_a, symbol_b):
            try:
                account = self.adapter.get_isolated_account(symbol)
                if account.quote_free > 0:
                    self.adapter.transfer_out(symbol, account.quote_free)
            except Exception as e:
                logger.critical(f"[PairsExecutionEngine] Collateral refund for {symbol} failed: {e}. MANUAL INTERVENTION REQUIRED — funds may be stranded in isolated account.")

    # ── Close ────────────────────────────────────────────────────────

    def close_pair_trade(self, pair_config: PairConfig, exit_reason: str, *, reconciliation_affected: bool = False,
                         reconciliation_gaps: str = "") -> bool:   # claude code changed: Stage 2D Rev B D-1 — reconciliation provenance passed through to both legs' journal rows
        position = self._open_positions.get(pair_config.pair_name)
        if position is None:
            logger.error(f"[PairsExecutionEngine] No tracked open position for {pair_config.pair_name} — cannot close.")
            return False

        try:
            fill_a, interest_a = self._close_leg(position.symbol_a, position.side_a, position.qty_a)   # claude code changed: LIVE-01 — also returns the exchange-reported interest owed on a short leg
            fill_b, interest_b = self._close_leg(position.symbol_b, position.side_b, position.qty_b)   # claude code changed: LIVE-01
        except (OrderStillOpenException, OrderUnconfirmedException, RateLimitException) as e:
            logger.critical(
                f"[PairsExecutionEngine] LEG_MISMATCH on close: {pair_config.pair_name} ({e}). "
                f"Disabling this pair until manually cleared — position may be partially closed."
            )
            self._mismatched_pairs.add(pair_config.pair_name)
            return False

        self.trade_logger.log_leg_exit(position.pair_trade_id, "A", fill_a, exit_reason,
                                       interest_qty=interest_a, entry_fees=position.entry_fees_a,   # claude code changed: LIVE-01/LIVE-02
                                       reconciliation_affected=reconciliation_affected, reconciliation_gaps=reconciliation_gaps)   # claude code changed: Stage 2D Rev B D-1
        self.trade_logger.log_leg_exit(position.pair_trade_id, "B", fill_b, exit_reason,
                                       interest_qty=interest_b, entry_fees=position.entry_fees_b,   # claude code changed: LIVE-01/LIVE-02
                                       reconciliation_affected=reconciliation_affected, reconciliation_gaps=reconciliation_gaps)   # claude code changed: Stage 2D Rev B D-1

        self._refund_collateral_best_effort(position.symbol_a, position.symbol_b)
        del self._open_positions[pair_config.pair_name]
        logger.info(f"[PairsExecutionEngine] Closed {pair_config.pair_name} | pair_trade_id={position.pair_trade_id} | reason={exit_reason}")
        return True

    def _close_leg(self, symbol: str, opened_side: str, opened_qty: float):
        """opened_side is the side used to OPEN this leg ("BUY"=long,
        "SELL"=short via borrow). Closing a short must repay the REAL
        outstanding loan (principal + accrued interest, from a fresh
        get_isolated_account() call) rather than just the original
        borrowed quantity — interest accrues hourly and this is the one
        place that must look it up, per margin_adapter.close_short()'s
        own docstring."""
        # claude code changed: LIVE-01 — now returns (fill, interest_qty). interest_qty is
        # the ACTUAL interest Binance reports as owed on this isolated account
        # (base units), so the logger can account for buying it back; None for
        # a long leg (nothing borrowed).
        if opened_side == "BUY":
            # claude code changed: sell the AUTHORITATIVE free balance, not the gross entry fill:
            # a buy fee charged in the base asset ('feeSide': 'get') leaves less
            # than was filled, and selling the gross amount is rejected by Binance.
            account = self.adapter.get_isolated_account(symbol)
            qty = sellable_quantity(opened_qty, account.base_free, self.adapter.amount_rules(symbol))
            if qty < opened_qty:
                logger.warning(f"[PairsExecutionEngine] {symbol}: closing {qty} of {opened_qty} entered — "
                               f"free balance {account.base_free} (base-asset fee and/or lot-step rounding).")
            return self.adapter.close_long(symbol, qty), None
        return self._cover_and_repay(symbol)

    def _cover_and_repay(self, symbol: str):   # claude code changed: new
        """Close a short from authoritative exchange state:
          1. debt = borrowed + interest now owed (isolated account)
          2. buy cover_buy_quantity(): debt minus base already free, grossed up
             for a possible base-asset buy fee, rounded UP to the lot step
          3. repay min(debt now owed, base actually free)
          4. re-read the account: any principal/interest still owed raises
             RepaymentIncompleteException (pair disabled) — never silent.
        Returns (buy_fill, interest_qty) — interest as reported before the buy."""
        account = self.adapter.get_isolated_account(symbol)
        debt = account.base_borrowed + account.base_interest
        buy_qty = cover_buy_quantity(debt, account.base_free, self.adapter.fee_buffer_rate(symbol),
                                     self.adapter.amount_rules(symbol))
        if buy_qty <= 0:
            raise CloseQuantityUnavailableException(
                f"{symbol}: free base {account.base_free} already covers debt {debt} — no cover order to log; manual review required")
        fill = self.adapter.market_buy_to_cover(symbol, buy_qty)
        after = self.adapter.get_isolated_account(symbol)
        self.adapter.repay(symbol, min(after.base_borrowed + after.base_interest, after.base_free))
        final = self.adapter.get_isolated_account(symbol)
        remaining_debt_or_raise(symbol, final.base_borrowed, final.base_interest)
        return fill, account.base_interest
