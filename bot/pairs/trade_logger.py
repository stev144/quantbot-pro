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
from typing import Optional

from bot.journal.models import TradeRecord
from bot.pairs.margin_adapter import MarginFillResult

logger = logging.getLogger(__name__)


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
                fee_entry=fill.fee_usdt,
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

    def log_leg_exit(self, pair_trade_id: uuid.UUID, leg: str, fill: MarginFillResult, exit_reason: str) -> Optional[TradeRecord]:
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

            if record.side == "BUY":
                gross_pnl = (fill.fill_price - record.entry_price) * record.quantity
            else:
                gross_pnl = (record.entry_price - fill.fill_price) * record.quantity
            net_pnl = gross_pnl - record.fee_entry - fill.fee_usdt
            r_multiple = (net_pnl / record.risk_amount) if record.risk_amount else 0.0

            record.exit_price = fill.fill_price
            record.fee_exit = fill.fee_usdt
            record.exit_reason = exit_reason
            record.gross_pnl = round(gross_pnl, 4)
            record.net_pnl = round(net_pnl, 4)
            record.r_multiple = round(r_multiple, 4)
            record.status = "WIN" if net_pnl > 0 else "LOSS"
            record.save()

            logger.info(
                f"[PairsTradeLogger] Leg {leg} exit logged | pair_trade_id={pair_trade_id} | "
                f"Outcome: {record.status} | Net: ${net_pnl:.2f} | Reason: {exit_reason}"
            )
            return record
        except Exception as e:
            logger.critical(
                f"[PairsTradeLogger] FAILED to log leg {leg} exit for pair_trade_id="
                f"{pair_trade_id}: {e}. P&L unrecorded — check database."
            )
            return None

    def get_open_legs(self, pair_trade_id: uuid.UUID) -> list:
        return list(TradeRecord.objects.filter(pair_trade_id=pair_trade_id, status="OPEN").order_by("leg"))
