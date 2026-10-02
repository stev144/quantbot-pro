# ============================================================
# bot/pairs/trade_logger.py
# claude code changed: new file — live pairs-trading pilot.
#
# Does NOT call bot.journal.trade_logger.TradeLogger.log_entry()/log_exit()
# directly — their signal-dict shape assumes price-level sl/tp are
# meaningful (both are non-nullable FloatFields on TradeRecord with no
# default), which doesn't fit this strategy: exits are z-score-threshold
# based (stop_zscore/target_zscore/time-stop — see
# bot/research/entry_exit_engine.py's TradeRecord), not price-level
# based. Forcing a fabricated sl/tp price into those fields would mislead
# any dashboard/analytics code that reads them expecting a real stop
# level. Instead this writes TradeRecord rows directly, setting sl=tp=
# entry_price with an explicit, documented meaning: "no price-level
# stop/target applies to this leg" — the real exit rule is recorded in
# exit_reason (STOPLOSS/TIMESTOP, from EntryExitEngine's own vocabulary).
#
# Each pair trade is two TradeRecord rows (one per leg), linked by a
# shared pair_trade_id (see migration 0010_traderecord_leg_traderecord_pair_trade_id.py).
# ============================================================

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple

from bot.journal.models import TradeRecord
from bot.pairs.margin_adapter import MarginFillResult, split_symbol, value_fees_in_quote   # claude code changed: LIVE-02 helpers

logger = logging.getLogger(__name__)


# ── Live leg accounting (LIVE-01 / LIVE-02, Quantitative Reality Audit 2026-09-29) ──
# claude code changed: new. Realised P&L from the ACTUAL asset flows of one leg:
#
#   d = +1 for a leg opened with BUY (long), −1 for SELL (borrowed short)
#   quote cash  = d · (Q_exit·P_exit − Q_entry·P_entry)
#   base left   = d · (Q_entry − Q_exit) − (base-asset fees) − I
#                 (I = base units of interest owed on a short; 0 for a long)
#   net         = quote cash − (quote-currency fees) + base left · P_exit
#
# For a short, Q_exit is the quantity actually bought back (principal +
# interest), so the cost of the interest coins is included (LIVE-01). A
# base-asset fee changes quantities, not cash, so it is counted exactly once:
# through "base left" (LIVE-02). Any base left over / still owed is valued at
# the exit price and reported as residual_base (≈0 when the leg is flat).
# gross = net + fee values, so net = gross − fee_entry − fee_exit holds for the
# stored columns. A fee in any other asset (e.g. BNB) cannot be valued from
# fill data: net is None, never a guess.


@dataclass(frozen=True)
class LegPnL:   # claude code changed: new
    gross_pnl: float
    net_pnl: Optional[float]           # None when any fee could not be valued in quote currency
    fee_entry_quote: Optional[float]
    fee_exit_quote: Optional[float]
    residual_base: float               # base units left (+) or still owed (−) after the round trip
    interest_qty: float
    interest_inferred: bool            # True when a short's interest was not supplied and was inferred from the buy-back
    valued: bool
    unvalued_fees: Tuple[Tuple[float, str], ...]


def compute_leg_pnl(entry_side: str, entry_qty: float, entry_price: float, entry_fees,
                    exit_qty: float, exit_price: float, exit_fees, base: str, quote: str,
                    interest_qty: Optional[float] = None) -> LegPnL:   # claude code changed: new
    if entry_side not in ("BUY", "SELL"):
        raise ValueError(f"entry_side must be BUY or SELL, got {entry_side!r}")
    d = 1.0 if entry_side == "BUY" else -1.0
    all_fees = tuple(entry_fees) + tuple(exit_fees)
    quote_fees = sum(c for c, ccy in all_fees if ccy == quote)
    base_fee_entry = sum(c for c, ccy in entry_fees if ccy == base)
    base_fee_exit = sum(c for c, ccy in exit_fees if ccy == base)
    unvalued = tuple((c, ccy) for c, ccy in all_fees if ccy not in (base, quote))

    inferred = False
    if d > 0:
        interest = 0.0
    elif interest_qty is None:
        # Assume the buy-back exactly repaid principal + interest (residual 0).
        interest = (exit_qty - entry_qty) - base_fee_entry - base_fee_exit
        inferred = True
    else:
        interest = float(interest_qty)

    cash = d * (exit_qty * exit_price - entry_qty * entry_price)
    residual = d * (entry_qty - exit_qty) - base_fee_entry - base_fee_exit - interest
    gross = cash + residual * exit_price + base_fee_entry * entry_price + base_fee_exit * exit_price

    fee_entry_quote = value_fees_in_quote(tuple(entry_fees), base, quote, entry_price)
    fee_exit_quote = value_fees_in_quote(tuple(exit_fees), base, quote, exit_price)
    valued = fee_entry_quote is not None and fee_exit_quote is not None
    net = (gross - fee_entry_quote - fee_exit_quote) if valued else None
    return LegPnL(gross_pnl=gross, net_pnl=net, fee_entry_quote=fee_entry_quote, fee_exit_quote=fee_exit_quote,
                  residual_base=residual, interest_qty=interest, interest_inferred=inferred,
                  valued=valued, unvalued_fees=unvalued)


@dataclass(frozen=True)
class ShortCloseAllocation:   # claude code changed: new
    closed_principal: float
    closed_interest: float
    repay_qty: float
    remaining_principal: float
    remaining_interest: float


def allocate_short_close(principal_qty: float, interest_qty: float, closed_principal: float) -> ShortCloseAllocation:   # claude code changed: new
    """Closing part of a short: interest is allocated pro rata to the principal
    closed. The live executor closes whole legs only; this exists so partial
    accounting is defined and tested, not because the live path uses it."""
    if not (0.0 < closed_principal <= principal_qty):
        raise ValueError(f"closed_principal must be in (0, {principal_qty}], got {closed_principal}")
    closed_interest = interest_qty * closed_principal / principal_qty
    return ShortCloseAllocation(
        closed_principal=closed_principal, closed_interest=closed_interest,
        repay_qty=closed_principal + closed_interest,
        remaining_principal=principal_qty - closed_principal,
        remaining_interest=interest_qty - closed_interest,
    )


class PairsTradeLogger:
    def log_leg_entry(
        self,
        pair_trade_id: uuid.UUID,
        leg: str,
        symbol: str,
        side: str,
        fill: MarginFillResult,
        risk_usdt: float,
        pair_name: str,
        venue: str = "binance",
    ) -> Optional[TradeRecord]:
        try:
            record = TradeRecord.objects.create(
                symbol=symbol,
                side=side,
                strategy=f"PairsTrading_{pair_name}",
                entry_price=fill.fill_price,
                sl=fill.fill_price,  # not meaningful — see module docstring
                tp=fill.fill_price,  # not meaningful — see module docstring
                quantity=fill.filled_qty,
                fee_entry=self._storable_fee(fill),   # claude code changed: LIVE-02 — quote-currency value, never a raw non-quote amount
                order_id=fill.order_id,
                risk_amount=risk_usdt,
                venue=venue,
                pair_trade_id=pair_trade_id,
                leg=leg,
                status="OPEN",
            )
            logger.info(
                f"[PairsTradeLogger] Leg {leg} entry logged | DB ID: {record.id} | "
                f"{symbol} {side} | pair_trade_id={pair_trade_id} | "
                f"Fill: {fill.fill_price} | Qty: {fill.filled_qty}"
            )
            return record
        except Exception as e:
            logger.critical(
                f"[PairsTradeLogger] FAILED to log leg {leg} entry for {symbol} "
                f"(pair_trade_id={pair_trade_id}): {e}. Position is live but unrecorded."
            )
            return None

    @staticmethod
    def _storable_fee(fill: MarginFillResult) -> float:   # claude code changed: new, LIVE-02
        """TradeRecord.fee_entry/fee_exit are non-null USDT columns. Store the
        quote-currency value when known; otherwise store only the part that CAN
        be valued (quote/base fees) — a raw BNB amount is never written into a
        USDT column. The unvalued remainder is logged and makes net P&L None."""
        if fill.fee_usdt is not None:
            return fill.fee_usdt
        base, quote = split_symbol(fill.symbol)
        valuable = tuple((c, ccy) for c, ccy in fill.fees if ccy in (base, quote))
        if fill.fees and len(valuable) != len(fill.fees):
            logger.critical(f"[PairsTradeLogger] {fill.symbol} fill {fill.order_id}: unvalued fee(s) {fill.fees} — not stored as USDT.")
        return value_fees_in_quote(valuable, base, quote, fill.fill_price) or 0.0

    def log_leg_exit(self, pair_trade_id: uuid.UUID, leg: str, fill: MarginFillResult, exit_reason: str,
                     interest_qty: Optional[float] = None, entry_fees=None,
                     reconciliation_affected: bool = False, reconciliation_gaps: str = "") -> Optional[TradeRecord]:   # claude code changed: LIVE-01/LIVE-02 — new optional args; old call signature still valid. Stage 2D Rev B D-1 — reconciliation provenance
        try:
            record = (
                TradeRecord.objects.filter(pair_trade_id=pair_trade_id, leg=leg, status="OPEN")
                .order_by("-id")
                .first()
            )
            if record is None:
                logger.critical(
                    f"[PairsTradeLogger] No OPEN TradeRecord found for pair_trade_id="
                    f"{pair_trade_id} leg={leg}. Cannot log exit."
                )
                return None

            # claude code changed: LIVE-01/LIVE-02 — was (exit−entry)·record.quantity − fees, which
            # (a) priced a short's buy-back at the ORIGINAL quantity, ignoring the interest
            # coins bought to repay the loan, and (b) subtracted fill.fee_usdt as USDT
            # whatever its currency. Now computed from the actual asset flows.
            base, quote = split_symbol(record.symbol)
            entry_fee_list = tuple(entry_fees) if entry_fees else ((record.fee_entry, quote),)   # older records: fee_entry is USDT
            exit_fee_list = fill.fees if fill.fees else (((fill.fee_usdt, quote),) if fill.fee_usdt is not None else ())
            pnl = compute_leg_pnl(record.side, record.quantity, record.entry_price, entry_fee_list,
                                  fill.filled_qty, fill.fill_price, exit_fee_list, base, quote,
                                  interest_qty=interest_qty)

            record.exit_price = fill.fill_price
            record.fee_exit = pnl.fee_exit_quote if pnl.fee_exit_quote is not None else self._storable_fee(fill)
            record.exit_reason = exit_reason
            record.reconciliation_affected = bool(reconciliation_affected)   # claude code changed: Stage 2D Rev B D-1 — durable, survives restart
            record.reconciliation_gaps = reconciliation_gaps or ""           # claude code changed: Stage 2D Rev B D-1
            record.gross_pnl = round(pnl.gross_pnl, 4)
            if pnl.valued:
                record.net_pnl = round(pnl.net_pnl, 4)
                record.r_multiple = round(pnl.net_pnl / record.risk_amount, 4) if record.risk_amount else 0.0
                record.status = "WIN" if pnl.net_pnl > 0 else "LOSS"
            else:
                record.net_pnl = None      # unknown, not zero — see compute_leg_pnl()
                record.r_multiple = None
                record.status = "UNVALUED"
                logger.critical(
                    f"[PairsTradeLogger] Leg {leg} ({record.symbol}) net P&L UNVALUED: fee(s) not valuable in {quote} "
                    f"(unvalued: {pnl.unvalued_fees or 'no fee information'}). Gross ${pnl.gross_pnl:.4f} recorded; net left empty."
                )
            if abs(pnl.residual_base) > 1e-9 * max(record.quantity, fill.filled_qty, 1.0):
                logger.warning(
                    f"[PairsTradeLogger] Leg {leg} ({record.symbol}) not flat after exit: {pnl.residual_base:+.8f} {base} "
                    f"{'still owed' if pnl.residual_base < 0 else 'left over'} — valued at the exit price in net P&L."
                )
            record.save()

            logger.info(
                f"[PairsTradeLogger] Leg {leg} exit logged | pair_trade_id={pair_trade_id} | "
                f"Outcome: {record.status} | Net: {'$%.4f' % pnl.net_pnl if pnl.valued else 'UNVALUED'} | "
                f"interest={pnl.interest_qty:.8f} {base}{' (inferred)' if pnl.interest_inferred else ''} | Reason: {exit_reason}"
            )
            return record
        except Exception as e:
            logger.critical(
                f"[PairsTradeLogger] FAILED to log leg {leg} exit for pair_trade_id="
                f"{pair_trade_id}: {e}. P&L unrecorded — check database."
            )
            return None

    def log_signal_rejection(self, pair_name: str, mismatch, *, methodology_version: str, protocol_hash: str,
                             data_version: str = "", venue: str = "binance"):   # claude code changed: Stage 2D Rev B D-2 — new
        """Persist one C1 ENTRY_SIGN_MISMATCH (bot.quant_core.lifecycle.EntrySignMismatch)
        to the journal. Idempotent per (pair, venue, decision candle, reason).
        No order, trade or P&L exists for it."""
        from bot.journal.models import SignalRejection
        ts = mismatch.decision_timestamp
        try:
            row, _ = SignalRejection.objects.get_or_create(
                pair_name=pair_name, venue=venue, decision_timestamp=ts, reason=mismatch.reason,
                defaults=dict(intended_direction=mismatch.intended_direction, zscore=float(mismatch.zscore),
                              held_spread=float(mismatch.held_spread), alpha_e=float(mismatch.alpha_e),
                              beta_e=float(mismatch.beta_e), price_a=float(mismatch.price_a), price_b=float(mismatch.price_b),
                              methodology_version=methodology_version, protocol_hash=protocol_hash,
                              data_version=data_version or ""))
            return row
        except Exception as e:   # the decision is already a no-trade; a journal failure must be loud, not fatal
            logger.critical(f"[PairsTradeLogger] FAILED to persist {mismatch.reason} for {pair_name} at {ts}: {e}")
            return None

    def get_open_legs(self, pair_trade_id: uuid.UUID) -> list:
        return list(TradeRecord.objects.filter(pair_trade_id=pair_trade_id, status="OPEN").order_by("leg"))
