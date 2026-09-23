# ============================================================
# bot/views/pairs_pilot_data.py
# claude code changed: new file — live Pairs Pilot monitor. Real
# TradeRecord query for the 3 pilot pairs (bot/pairs/config.py's
# PAIR_NAMES), one equity curve + drawdown curve per pair — distinct
# from bot/views/pairs_performance_data.py, which visualizes OFFLINE
# research_data/*.csv backtest output, not live trades. Mirrors
# bot/views/terminal_data.py::get_execution_state()'s pattern (real
# TradeRecord query, no derived/fabricated numbers) but grouped per pair
# instead of flat across the whole account, and per-pair-trade instead
# of per-leg (a pair trade is two TradeRecord rows — see
# bot/pairs/trade_logger.py — that must be combined before they mean
# anything as one trade's P&L).
# ============================================================

from bot.journal.models import TradeRecord
from bot.pairs.config import PAIR_NAMES


def get_pairs_pilot_state():
    """One entry per pilot pair. n_trades/equity_curve/drawdown_curve
    are all built from CLOSED pair-trades only (both legs no longer
    OPEN) — a pair-trade with only one leg closed so far is reported
    separately as a mismatched/in-progress leg, never partially folded
    into the equity curve, since that would understate its real P&L."""
    pairs_data = []
    for pair_name in PAIR_NAMES:
        strategy_tag = f"PairsTrading_{pair_name}"
        records = list(TradeRecord.objects.filter(strategy=strategy_tag).order_by("id"))

        groups = {}
        for r in records:
            groups.setdefault(r.pair_trade_id, []).append(r)

        closed_trades = []
        open_pair_trade_ids = []
        for pair_trade_id, legs in groups.items():
            if len(legs) == 2 and all(l.status != "OPEN" for l in legs):
                net_pnl = sum((l.net_pnl or 0.0) for l in legs)
                closed_at = max(l.updated_at for l in legs)
                closed_trades.append({"pair_trade_id": pair_trade_id, "net_pnl": net_pnl, "closed_at": closed_at})
            else:
                open_pair_trade_ids.append(pair_trade_id)
        closed_trades.sort(key=lambda t: t["closed_at"])

        equity_curve = []
        cum = 0.0
        for t in closed_trades:
            cum += t["net_pnl"]
            equity_curve.append(round(cum, 4))

        drawdown_curve = []  # dollar drawdown from running peak — see module note on why $ not % here
        peak = 0.0
        for eq in equity_curve:
            peak = max(peak, eq)
            drawdown_curve.append(round(peak - eq, 4))

        wins = sum(1 for t in closed_trades if t["net_pnl"] > 0)
        losses = sum(1 for t in closed_trades if t["net_pnl"] <= 0)
        n_trades = len(closed_trades)

        pairs_data.append({
            "pair_name": pair_name,
            "slug": pair_name.replace("/", "_").replace("_USDT", ""),
            "display_name": pair_name.replace("_USDT", "").replace("/", " / "),
            "n_trades": n_trades,
            "open_legs_count": sum(len(groups[pid]) for pid in open_pair_trade_ids),
            "net_pnl": round(sum(t["net_pnl"] for t in closed_trades), 4),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / n_trades * 100, 1) if n_trades else None,
            "max_drawdown": max(drawdown_curve) if drawdown_curve else 0.0,
            "equity_curve": equity_curve,
            "drawdown_curve": drawdown_curve,
        })
    return pairs_data
