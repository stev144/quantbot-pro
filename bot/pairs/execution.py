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
from bot.config.execution_costs import FEE_RATE   # claude code changed: Post-audit Phase 1 A2 — estimated exit fee in the liquidation mark
from bot.journal.models import MODE_DRY_RUN, MODE_LIVE, execution_mode_for   # claude code changed: Post-audit Phase 1 journal-mode separation

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
    leg_a_open: bool = True    # claude code changed: Post-audit Phase 1 A1/A3 — a failed close keeps exactly the legs still open
    leg_b_open: bool = True    # claude code changed: Post-audit Phase 1 A1/A3


# claude code changed: Post-audit Phase 1 A1 — incident states (bot.journal.models.ExecutionIncident)
NO_EXPOSURE = "NO_EXPOSURE"                        # nothing filled and both isolated accounts verified flat
UNWOUND_CONFIRMED = "UNWOUND_CONFIRMED"            # a leg filled, was unwound, both accounts re-read flat
MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"  # exchange state uncertain or unwind not confirmed → pair disabled across restarts
AMBIGUOUS_ORDER_EXCEPTIONS = (OrderStillOpenException, OrderUnconfirmedException)   # the order may still fill after we look


class PairsExecutionEngine:
    """One instance covers ALL pilot pairs on one Binance account —
    holds a single BinanceIsolatedMarginAdapter (one set of API
    credentials), a shared DrawdownGuard scoped to this pilot's own
    sub-portfolio, and per-pair open-position tracking."""

    def __init__(self, exchange, dry_run: bool = True):
        self.adapter = BinanceIsolatedMarginAdapter(exchange, dry_run=dry_run)
        self.trade_logger = PairsTradeLogger(execution_mode=execution_mode_for(dry_run))   # claude code changed: Post-audit Phase 1 — every row this engine writes carries LIVE / DRY_RUN
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

    # claude code changed: Post-audit Phase 1 A2 — NOT the drawdown-guard input any more:
    # collateral is refunded to spot after every close, so this isolated-account
    # sum reads ≈ 0 when flat. Kept as an exchange-state diagnostic only; the
    # guard uses strategy_equity() (strategy capital + journal P&L + open legs).
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

        # claude code changed: Post-audit Phase 1 A2 (C-05) — the drawdown guard is
        # enforced HERE, immediately before any transfer or order, on every entry
        # (was: computed only for the heartbeat log and never consulted).
        allowed, why = self.drawdown_entry_check(getattr(pair_config, "capital_usdt", None))   # claude code changed: Phase 1 regression fix — a config without capital fails CLOSED (strategy_equity rejects it) instead of raising AttributeError
        if not allowed:
            logger.critical(f"[PairsExecutionEngine] {pair_config.pair_name}: ENTRY REFUSED — {why}. No transfer, no order.")
            return None

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

        side_a, side_b = ("BUY", "SELL") if trade.direction == "LONG_SPREAD" else ("SELL", "BUY")   # claude code changed: Post-audit Phase 1 A1
        fill_a = fill_b = None   # claude code changed: Post-audit Phase 1 A1 — which legs are KNOWN to have filled
        try:
            # claude code changed: Post-audit Phase 1 A1 (C-04) — ANY exception after
            # the first order is submitted goes to _handle_open_failure (was: only
            # OrderStillOpen/OrderUnconfirmed/RateLimit were caught; any other
            # exchange error on leg B left leg A naked, unjournaled and untracked).
            fill_a = self.adapter.open_long(symbol_a, qty_a) if side_a == "BUY" else self.adapter.open_short(symbol_a, qty_a)[1]
            fill_b = self.adapter.open_long(symbol_b, qty_b) if side_b == "BUY" else self.adapter.open_short(symbol_b, qty_b)[1]
        except Exception as e:
            return self._handle_open_failure(pair_config, trade, pair_trade_id, (side_a, side_b), (fill_a, fill_b), e)

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

    def _handle_open_failure(self, pair_config, trade, pair_trade_id, sides, fills, error):   # claude code changed: Post-audit Phase 1 A1 — new
        """A pair entry failed after at least one order was submitted.
        Never a completed pair trade; never a silent retry. Steps:
          1. unwind from AUTHORITATIVE account state (whatever actually exists
             on either symbol is closed — this also covers a leg the exchange
             filled although the call raised);
          2. re-read both accounts: flat (no debt, base below the dust limit)
             or not;
          3. journal every leg KNOWN to have filled (an unwound leg gets its
             real unwind exit; an unconfirmed one stays OPEN, which startup
             reconciliation treats as exposure);
          4. record an ExecutionIncident: NO_EXPOSURE, UNWOUND_CONFIRMED, or
             MANUAL_REVIEW_REQUIRED (unwind unconfirmed, or the failing order's
             status is ambiguous so it may still fill later);
          5. refund collateral ONLY when confirmed flat — moving collateral out
             while a short may still be open would invite liquidation."""
        symbol_a, symbol_b = pair_config.binance_symbol_a, pair_config.binance_symbol_b
        name = pair_config.pair_name
        logger.critical(f"[PairsExecutionEngine] LEG_FAILURE: {name} failed mid-open "
                        f"({type(error).__name__}: {error}). Unwinding from exchange state.")
        confirmed, unwind_fills, exposure_found = self._attempt_partial_unwind(symbol_a, symbol_b)
        ambiguous = isinstance(error, AMBIGUOUS_ORDER_EXCEPTIONS)
        filled_any = any(f is not None for f in fills)
        if not confirmed or ambiguous:
            state = MANUAL_REVIEW_REQUIRED
        elif filled_any or exposure_found:
            state = UNWOUND_CONFIRMED
        else:
            state = NO_EXPOSURE
        legs_filled, legs_open = [], []
        for leg, symbol, side, fill, usdt in (("A", symbol_a, sides[0], fills[0], trade.leg_a_usdt),
                                               ("B", symbol_b, sides[1], fills[1], trade.leg_b_usdt)):
            if fill is None:
                continue
            legs_filled.append(f"{leg}:{side} {fill.filled_qty} @ {fill.fill_price}")
            self.trade_logger.log_leg_entry(pair_trade_id, leg, symbol, side, fill, usdt, name)
            u = unwind_fills.get(symbol)
            if confirmed and u is not None:
                ufill, interest = u
                self.trade_logger.log_leg_exit(pair_trade_id, leg, ufill, "PAIR_OPEN_UNWOUND",
                                               interest_qty=None if self.dry_run else interest, entry_fees=fill.fees,
                                               borrow_cost_status=self._borrow_status(side))
            else:
                legs_open.append(f"{leg}:{symbol}")
        if legs_open:   # claude code changed: Phase 1 review — a filled leg still OPEN in the journal (no unwind fill to close it with) can never be labelled UNWOUND_CONFIRMED
            state = MANUAL_REVIEW_REQUIRED
        if state != NO_EXPOSURE:
            self._mismatched_pairs.add(name)   # no new entries on this pair in this run
        if confirmed:
            self._refund_collateral_best_effort(symbol_a, symbol_b)
        self.trade_logger.log_incident(name, kind="OPEN_LEG_FAILURE", state=state, pair_trade_id=pair_trade_id,
                                       legs_filled="; ".join(legs_filled), legs_open_after="; ".join(legs_open),
                                       error=f"{type(error).__name__}: {error}")
        logger.critical(f"[PairsExecutionEngine] {name}: open failure recorded as {state}"
                        + (" — MANUAL INTERVENTION REQUIRED, pair disabled across restarts." if state == MANUAL_REVIEW_REQUIRED else "."))
        return None

    def _dust_limit(self, symbol: str) -> float:   # claude code changed: Post-audit Phase 1 A1 — base below this cannot be sold and is treated as flat
        rules = self.adapter.amount_rules(symbol)
        return float(rules.min_qty or rules.step or 0.0)

    def _attempt_partial_unwind(self, symbol_a: str, symbol_b: str):
        """claude code changed: Post-audit Phase 1 A1 — returns
        (confirmed_flat, {symbol: (fill, interest)}, exposure_found) instead of
        None. Closes whatever the isolated accounts actually hold (free base
        above the dust limit → sell; any debt → cover and repay), then RE-READS
        both accounts. Never raises: any failure means not confirmed."""
        fills, exposure_found, ok = {}, False, True
        for symbol in (symbol_a, symbol_b):
            try:
                account = self.adapter.get_isolated_account(symbol)
                dust = self._dust_limit(symbol)
                debt = account.base_borrowed + account.base_interest
                if debt > 0 and account.base_free > 0:   # claude code changed: Phase 1 review — borrowed-but-unsold base (open_short's sell failed after the borrow) is REPAID, never market-sold: selling it would open the very short being unwound, then need a cover buy
                    exposure_found = True
                    self.adapter.repay(symbol, min(account.base_free, debt))
                    account = self.adapter.get_isolated_account(symbol)
                if account.base_free > dust:
                    exposure_found = True
                    qty = sellable_quantity(account.base_free, account.base_free, self.adapter.amount_rules(symbol))
                    fills[symbol] = (self.adapter.close_long(symbol, qty), None)
                if account.base_borrowed + account.base_interest > 0:
                    exposure_found = True
                    fills[symbol] = self._cover_and_repay(symbol)   # claude code changed: was close_short(borrowed + interest): same base-fee / truncation shortfall as the normal close
            except Exception as e:
                ok = False
                logger.critical(f"[PairsExecutionEngine] Unwind attempt for {symbol} failed: {e}. MANUAL INTERVENTION REQUIRED.")
        for symbol in (symbol_a, symbol_b):   # verification re-read — an unwind is only "done" when the exchange says so
            try:
                a = self.adapter.get_isolated_account(symbol)
                if a.base_borrowed + a.base_interest > 0 or a.base_free > self._dust_limit(symbol):
                    ok = False
                    logger.critical(f"[PairsExecutionEngine] {symbol} NOT flat after unwind: free {a.base_free}, "
                                    f"borrowed {a.base_borrowed}, interest {a.base_interest}.")
            except Exception as e:
                ok = False
                logger.critical(f"[PairsExecutionEngine] Could not verify {symbol} after unwind: {e}.")
        return ok, fills, exposure_found

    def _borrow_status(self, side: str) -> str:   # claude code changed: Post-audit Phase 1 A4
        if side == "BUY":
            return "NOT_APPLICABLE"
        return "UNAVAILABLE" if self.dry_run else "KNOWN"

    # ── Pre-entry drawdown guard (C-05) ─────────────────────────────────
    # claude code changed: Post-audit Phase 1 A2 — new

    def strategy_equity(self, capital_usdt: float):
        """(current_equity, peak_equity) of THIS PILOT'S SUB-PORTFOLIO — the
        scope the DrawdownGuard is documented to cover (class docstring):

          equity = C + Σ realised net P&L of closed pilot legs (journal)
                     + Σ unrealised mark P&L of open pilot legs (marked at
                       the current ticker; borrow interest and exit costs
                       of the open legs are not included — unknown until
                       the close)
          peak   = max(C, C + running realised P&L at each point where a
                       pair trade's LAST leg closed, current equity) —
                       rebuilt from the journal on every call, so a restart
                       cannot re-seed it lower. (claude code changed: Phase 1
                       review — was after each closed LEG: leg A's realised
                       gain counted before leg B's offsetting loss, an equity
                       level the pilot never held.) Event order is the row's
                       updated_at — TradeRecord has no exit-timestamp field.

        C is the strategy capital (pair_config.capital_usdt). The former
        isolated-account sum (compute_mark_to_market_equity) cannot serve:
        collateral is refunded to spot after every close, so it reads ≈ 0
        when flat. Raises ValueError when equity cannot be established
        (unvalued realised P&L, a price that cannot be fetched) — the caller
        then blocks the entry (fail closed).

        claude code changed: Post-audit Phase 1 (master prompt) —
          * MODE SEPARATION: only journal rows of THIS engine's execution mode
            count (a LIVE guard never sees dry-run P&L and vice versa); any
            pilot row whose mode is UNKNOWN fails closed (it cannot be
            attributed). An engine whose own mode is not LIVE/DRY_RUN fails
            closed.
          * OPEN-LEG MARK on the same cash basis as realised net P&L: the entry
            fee is already paid, so it is deducted now (was: only at the close,
            an equity overstatement while open). In LIVE mode the borrow
            interest accrued so far on a short leg's isolated account is a
            known liability and is deducted at the mark; in DRY_RUN it is not
            modelled (borrow_cost_status=UNAVAILABLE) and is not deducted.
          * ORDERING of the realised path by closed_at (durable exit time),
            falling back to updated_at only for rows closed before closed_at
            existed.
          * LIQUIDATION-VALUE MARK (owner decision 2026-10-02, report §5.5;
            was ticker last): an open leg is valued at what closing it now
            would realise — a long at the BID, a short at the ASK (one ticker
            snapshot, adapter.get_bid_ask) — minus the estimated exit fee
            (FEE_RATE × q × mark). A missing, non-positive or crossed bid/ask
            raises → the entry is blocked (fail closed; never a fallback to
            last). Interest owed (LIVE shorts) is valued at the same ask."""
        from bot.journal.models import TradeRecord
        if not (capital_usdt and capital_usdt > 0):
            raise ValueError(f"strategy capital {capital_usdt!r} is not positive")
        from django.db.models.functions import Coalesce   # claude code changed: Post-audit Phase 1 — closed_at ordering
        mode = getattr(self.trade_logger, "execution_mode", None)   # claude code changed: Post-audit Phase 1 journal-mode separation
        if mode not in (MODE_LIVE, MODE_DRY_RUN):
            raise ValueError(f"engine execution mode {mode!r} is not LIVE or DRY_RUN — cannot select its journal rows")
        pilot_all = TradeRecord.objects.filter(strategy__startswith="PairsTrading_")
        n_unknown = pilot_all.exclude(execution_mode__in=(MODE_LIVE, MODE_DRY_RUN)).count()   # claude code changed: Post-audit Phase 1
        if n_unknown:
            raise ValueError(f"{n_unknown} pilot journal row(s) have execution_mode UNKNOWN — cannot tell whether they are "
                             f"live or simulated; classify them before trading")
        pilot = pilot_all.filter(execution_mode=mode)   # claude code changed: Post-audit Phase 1 — this engine's mode only
        closed = [(v, ptid if ptid is not None else ("row", rid))   # claude code changed: Phase 1 review — pair_trade_id for pair-completion peak points; a pre-0010 row without one is its own event
                  for v, ptid, rid in pilot.exclude(status="OPEN").order_by(Coalesce("closed_at", "updated_at"), "id")   # claude code changed: Post-audit Phase 1 — durable exit time
                  .values_list("net_pnl", "pair_trade_id", "id")]
        if any(v is None for v, _ in closed):
            raise ValueError("a closed pilot leg has unvalued net P&L (third-asset fee) — equity cannot be established")
        still_open = set(pilot.filter(status="OPEN").values_list("pair_trade_id", flat=True))   # claude code changed: Phase 1 review
        remaining = {}   # claude code changed: Phase 1 review — closed legs not yet seen, per pair trade
        for _, ptid in closed:
            remaining[ptid] = remaining.get(ptid, 0) + 1
        running, peak = float(capital_usdt), float(capital_usdt)
        for v, ptid in closed:
            running += float(v)
            remaining[ptid] -= 1
            if remaining[ptid] == 0 and ptid not in still_open:   # claude code changed: Phase 1 review — a peak point only once the pair trade is fully closed
                peak = max(peak, running)
        unrealised, interest_charged = 0.0, set()
        for leg in pilot.filter(status="OPEN"):
            bid, ask = self.adapter.get_bid_ask(leg.symbol)   # claude code changed: Post-audit Phase 1 A2 (owner decision) — liquidation value, was get_ticker_price (last)
            price = bid if leg.side == "BUY" else ask          # claude code changed: a long is closed by selling (bid), a short by buying back (ask)
            sign = 1.0 if leg.side == "BUY" else -1.0
            unrealised += sign * leg.quantity * (price - leg.entry_price)
            unrealised -= FEE_RATE * leg.quantity * price      # claude code changed: Post-audit Phase 1 A2 (owner decision) — estimated exit fee, same declared rate as the fill model
            unrealised -= float(leg.fee_entry or 0.0)   # claude code changed: Post-audit Phase 1 — entry fee already paid (same cash basis as realised net P&L)
            if leg.side == "SELL" and mode == MODE_LIVE and leg.symbol not in interest_charged:   # claude code changed: Post-audit Phase 1 — accrued borrow interest is a known liability in live mode (once per isolated account)
                interest_charged.add(leg.symbol)
                unrealised -= float(self.adapter.get_isolated_account(leg.symbol).base_interest) * price
        equity = running + unrealised
        return equity, max(peak, equity)

    def drawdown_entry_check(self, capital_usdt: float):
        """(allowed, reason). Existing DrawdownGuard semantics, unchanged:
        threshold MAX_DRAWDOWN_PCT, blocked when drawdown >= threshold
        (the boundary itself blocks), auto-clears on recovery. Fails closed
        when equity cannot be established."""
        try:
            equity, peak = self.strategy_equity(capital_usdt)
        except Exception as e:
            return False, f"drawdown guard cannot establish strategy equity ({e}) — failing closed"
        if equity <= 0:   # claude code changed: Phase 1 review — DrawdownGuard.update() returns its PREVIOUS state for a balance <= 0 (built for a failed-fetch 0.0), which on a fresh engine is "not tripped": a pilot that lost all its capital would have been allowed to enter
            return False, f"strategy equity ${equity:.2f} <= 0 (capital ${capital_usdt:.2f}) — failing closed"
        self.drawdown_guard.peak_balance = max(self.drawdown_guard.peak_balance, peak)
        tripped = self.drawdown_guard.update(equity)
        dd = self.drawdown_guard.current_drawdown_pct(equity)
        if tripped:
            return False, (f"DRAWDOWN GUARD TRIPPED: drawdown {dd:.2%} >= {self.drawdown_guard.max_drawdown_pct:.2%} "
                           f"(peak ${self.drawdown_guard.peak_balance:.2f}, equity ${equity:.2f})")
        return True, f"drawdown {dd:.2%} < {self.drawdown_guard.max_drawdown_pct:.2%}"

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

        # claude code changed: Post-audit Phase 1 A1/A3 — legs are closed and
        # journaled ONE AT A TIME, and ANY failure (was: only three exception
        # types; anything else desynchronised journal and position) leaves the
        # failed leg OPEN in both the journal and the tracked position, disables
        # the pair and records a MANUAL_REVIEW_REQUIRED incident. Nothing is ever
        # marked closed without a real fill.
        failures = []
        for leg, symbol, side, qty, fees, flag in (
                ("A", position.symbol_a, position.side_a, position.qty_a, position.entry_fees_a, "leg_a_open"),
                ("B", position.symbol_b, position.side_b, position.qty_b, position.entry_fees_b, "leg_b_open")):
            if not getattr(position, flag):
                continue
            try:
                fill, interest = self._close_leg(symbol, side, qty)   # claude code changed: LIVE-01 — also returns the exchange-reported interest owed on a short leg
            except Exception as e:
                failures.append((leg, symbol, e))
                continue
            self.trade_logger.log_leg_exit(position.pair_trade_id, leg, fill, exit_reason,
                                           interest_qty=None if self.dry_run else interest, entry_fees=fees,   # claude code changed: LIVE-01/LIVE-02; Phase 1 A4 — dry-run interest is unknown, not zero
                                           reconciliation_affected=reconciliation_affected, reconciliation_gaps=reconciliation_gaps,   # claude code changed: Stage 2D Rev B D-1
                                           borrow_cost_status=self._borrow_status(side))   # claude code changed: Post-audit Phase 1 A4
            setattr(position, flag, False)
        if failures:
            self._mismatched_pairs.add(pair_config.pair_name)
            blocked = all(isinstance(e, CloseQuantityUnavailableException) for _, _, e in failures)
            open_after = "; ".join(f"{leg}:{sym}" for leg, sym, _ in failures)
            self.trade_logger.log_incident(
                pair_config.pair_name, kind="CLOSE_BLOCKED_EXCHANGE_RULES" if blocked else "CLOSE_LEG_FAILURE",
                state=MANUAL_REVIEW_REQUIRED, pair_trade_id=position.pair_trade_id, legs_open_after=open_after,
                error=" | ".join(f"{leg}: {type(e).__name__}: {e}" for leg, _, e in failures))
            logger.critical(f"[PairsExecutionEngine] CLOSE FAILED for {pair_config.pair_name}: still open {open_after}. "
                            f"Pair disabled; MANUAL INTERVENTION REQUIRED. Collateral left in place.")
            return False

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
            self._require_closable(symbol, qty)   # claude code changed: Post-audit Phase 1 A3
            return self.adapter.close_long(symbol, qty), None
        return self._cover_and_repay(symbol)

    def _require_closable(self, symbol: str, qty: float) -> None:   # claude code changed: Post-audit Phase 1 A3 — new
        """Raise CloseQuantityUnavailableException BEFORE any order when a
        market close of `qty` would violate the exchange's minNotional at the
        current mark (minQty/step are already enforced by sellable_quantity /
        cover_buy_quantity). Dry run: the simulator has no exchange filters —
        min_notional is None and nothing is claimed."""
        min_notional = self.adapter.min_notional(symbol)
        if min_notional is None:
            return
        price = float(self.adapter.get_ticker_price(symbol))
        if qty * price < min_notional:
            raise CloseQuantityUnavailableException(
                f"{symbol}: close of {qty} at {price} = {qty * price:.6f} is below minNotional {min_notional} "
                f"— not closable by a market order; position kept, manual review required")

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
        self._require_closable(symbol, buy_qty)   # claude code changed: Post-audit Phase 1 A3 — a cover buy below minNotional is refused, never grossed up
        fill = self.adapter.market_buy_to_cover(symbol, buy_qty)
        after = self.adapter.get_isolated_account(symbol)
        self.adapter.repay(symbol, min(after.base_borrowed + after.base_interest, after.base_free))
        final = self.adapter.get_isolated_account(symbol)
        remaining_debt_or_raise(symbol, final.base_borrowed, final.base_interest)
        return fill, account.base_interest
