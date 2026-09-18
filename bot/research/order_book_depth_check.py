# ============================================================
# bot/research/order_book_depth_check.py
#
# claude code changed: new file. Closes the one open item
# research_data/model_governance_log.md's "Closing the two remaining
# cautions" entry (2026-09-16) left explicitly unresolved: "no real
# order-book depth data for these specific tokens has been pulled — that
# would replace this multiplier sweep with an actual number." That entry
# stress-tested the 7-pair cross-sectional portfolio's edge against
# 1x/2x/3x/5x/10x/20x multiples of the modeled SLIPPAGE_RATE (0.05%,
# bot/config/execution_costs.py). This script replaces the multiplier
# assumption with a REAL measurement: walks live Binance order-book depth
# (bot.engines.execution_comparison.get_venue_quote — the existing
# execution-comparison infrastructure, not a new fetch path) for the
# portfolio's actual token universe, across a range of realistic order
# sizes, and reports the measured slippage as a real multiple of the
# modeled rate — the same "multiplier" framing the governance log already
# used, now backed by data instead of an assumption.
#
# Universe: the 10 unique legs across the 7 pairs the log names (DODO/FIDA,
# 1INCH/FIDA, MINA/ONG, AVA/PHA, COTI/ONG, FIL/AVA, AVA/NEO) — DODO, FIDA,
# 1INCH, MINA, ONG, AVA, PHA, COTI, FIL, NEO, all vs USDT (this project's
# quote currency throughout).
#
# Order sizes: $1k/$5k/$10k/$25k/$50k notional per leg. $10k matches
# STRATEGY_CAPITAL_USDT (bot/research/entry_exit_engine.py) — the
# single-pair backtest capital assumption used throughout this research
# pipeline (walk_forward_engine.py, permutation_test_engine.py all default
# to it) — the other sizes bracket it to show where the edge would start
# degrading at larger size, the exact "capacity limit" question the
# governance log flagged as unanswered.
#
# Read-only against the exchange: uses BinanceAdapter's public order-book
# endpoint only (no API keys, no orders placed) — same public-data pattern
# bot/core/dry_run_test.py already uses for connectivity checks.
# ============================================================

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import ccxt
import pandas as pd

from bot.config.execution_costs import SLIPPAGE_RATE
from bot.engines.binance_adapter import BinanceAdapter
from bot.engines.execution_comparison import _walk_book
from bot.engines.liquidity import compute_liquidity_snapshot

logger = logging.getLogger(__name__)

if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

# The 10 unique legs across the log's 7-pair portfolio (DODO/FIDA,
# 1INCH/FIDA, MINA/ONG, AVA/PHA, COTI/ONG, FIL/AVA, AVA/NEO).
UNIVERSE_SYMBOLS: List[str] = [
    "DODO/USDT", "FIDA/USDT", "1INCH/USDT", "MINA/USDT", "ONG/USDT",
    "AVA/USDT", "PHA/USDT", "COTI/USDT", "FIL/USDT", "NEO/USDT",
]

ORDER_SIZES_USDT: List[float] = [1_000.0, 5_000.0, 10_000.0, 25_000.0, 50_000.0]

ORDER_BOOK_DEPTH_LIMIT = 1000   # ccxt/Binance-supported level count — deep enough for a thin altcoin book


@dataclass(frozen=True)
class DepthMeasurement:
    symbol: str
    side: str
    order_size_usdt: float
    available: bool
    unavailable_reason: str
    mid_price: Optional[float]
    spread_pct: Optional[float]
    measured_slippage_pct: Optional[float]
    modeled_slippage_pct: float
    measured_vs_modeled_multiple: Optional[float]


def _measure_symbol(adapter: BinanceAdapter, symbol: str, order_sizes: List[float]) -> List[DepthMeasurement]:
    """
    Fetches ONE order-book snapshot per symbol (not per size/side — a
    single snapshot is reused across every size/side combination below,
    both to minimize live API calls against a public rate limit and so
    every size for a given symbol is measured against the exact same
    book, not a book that moved between calls).
    """
    if not adapter.exchange.markets:
        adapter.exchange.load_markets()

    normalized = adapter.normalize_symbol(symbol)
    if normalized is None:
        return [
            DepthMeasurement(
                symbol=symbol, side=side, order_size_usdt=size, available=False,
                unavailable_reason=f"not listed on {adapter.venue_id}",
                mid_price=None, spread_pct=None, measured_slippage_pct=None,
                modeled_slippage_pct=SLIPPAGE_RATE, measured_vs_modeled_multiple=None,
            )
            for size in order_sizes for side in ("buy", "sell")
        ]

    try:
        order_book = adapter.get_order_book(normalized, limit=ORDER_BOOK_DEPTH_LIMIT)
        snapshot = compute_liquidity_snapshot(order_book)
    except Exception as e:
        reason = f"order book fetch/parse failed: {type(e).__name__}: {e}"
        return [
            DepthMeasurement(
                symbol=symbol, side=side, order_size_usdt=size, available=False,
                unavailable_reason=reason,
                mid_price=None, spread_pct=None, measured_slippage_pct=None,
                modeled_slippage_pct=SLIPPAGE_RATE, measured_vs_modeled_multiple=None,
            )
            for size in order_sizes for side in ("buy", "sell")
        ]

    results: List[DepthMeasurement] = []
    for size in order_sizes:
        quantity = size / snapshot.mid_price
        for side in ("buy", "sell"):
            levels = order_book["asks"] if side == "buy" else order_book["bids"]
            fill_price = _walk_book(levels, quantity)
            if fill_price is None:
                results.append(DepthMeasurement(
                    symbol=normalized, side=side, order_size_usdt=size, available=False,
                    unavailable_reason=f"insufficient book depth within {ORDER_BOOK_DEPTH_LIMIT} levels for ${size:,.0f}",
                    mid_price=snapshot.mid_price, spread_pct=snapshot.spread_pct,
                    measured_slippage_pct=None, modeled_slippage_pct=SLIPPAGE_RATE,
                    measured_vs_modeled_multiple=None,
                ))
                continue

            measured_slippage_pct = abs(fill_price - snapshot.mid_price) / snapshot.mid_price
            results.append(DepthMeasurement(
                symbol=normalized, side=side, order_size_usdt=size, available=True,
                unavailable_reason="",
                mid_price=snapshot.mid_price, spread_pct=snapshot.spread_pct,
                measured_slippage_pct=measured_slippage_pct, modeled_slippage_pct=SLIPPAGE_RATE,
                measured_vs_modeled_multiple=measured_slippage_pct / SLIPPAGE_RATE,
            ))

    return results


def measure_universe_depth(
    symbols: List[str] = UNIVERSE_SYMBOLS,
    order_sizes: List[float] = ORDER_SIZES_USDT,
) -> pd.DataFrame:
    """
    Real, live measurement — not a backtest, not a simulation. Every row
    is one (symbol, side, order_size) combination's actual current
    Binance order-book depth. Run this close in time to when its output
    will be interpreted; a stale run is a snapshot of a moment, not a
    durable constant the way the modeled 0.05% SLIPPAGE_RATE is treated
    as.
    """
    exchange = ccxt.binance()
    adapter = BinanceAdapter(exchange, dry_run=True)

    logger.info("=" * 70)
    logger.info("ORDER-BOOK DEPTH MEASUREMENT — 7-pair portfolio universe")
    logger.info("=" * 70)
    logger.info(f"  Symbols     : {len(symbols)}")
    logger.info(f"  Order sizes : {[f'${s:,.0f}' for s in order_sizes]}")
    logger.info(f"  Modeled slippage_rate (baseline) : {SLIPPAGE_RATE:.4%}")

    rows: List[DepthMeasurement] = []
    for symbol in symbols:
        logger.info(f"\n  Fetching {symbol}...")
        rows.extend(_measure_symbol(adapter, symbol, order_sizes))

    df = pd.DataFrame([r.__dict__ for r in rows])
    return df


def summarize_by_size(df: pd.DataFrame) -> pd.DataFrame:
    """
    Worst-case (max) measured slippage across every symbol/side at each
    order size — the portfolio-level number that maps directly onto the
    governance log's own multiplier table (1x/2x/3x/.../20x of modeled
    SLIPPAGE_RATE), now measured instead of assumed.
    """
    available = df[df["available"]]
    if available.empty:
        return pd.DataFrame(columns=["order_size_usdt", "worst_measured_slippage_pct", "worst_measured_multiple", "n_available", "n_total"])

    summary = (
        available.groupby("order_size_usdt")
        .agg(
            worst_measured_slippage_pct=("measured_slippage_pct", "max"),
            mean_measured_slippage_pct=("measured_slippage_pct", "mean"),
            worst_measured_multiple=("measured_vs_modeled_multiple", "max"),
            n_available=("symbol", "count"),
        )
        .reset_index()
    )
    n_total = df.groupby("order_size_usdt").size().rename("n_total")
    summary = summary.merge(n_total, left_on="order_size_usdt", right_index=True)
    return summary.sort_values("order_size_usdt")


if __name__ == "__main__":
    df = measure_universe_depth()

    output_dir = Path("research_data")
    output_dir.mkdir(exist_ok=True)
    out_path = output_dir / "order_book_depth_measurement.csv"
    df.to_csv(out_path, index=False)
    logger.info(f"\n  Saved: {out_path}")

    summary = summarize_by_size(df)

    logger.info("\n" + "=" * 70)
    logger.info("WORST-CASE MEASURED SLIPPAGE BY ORDER SIZE (across all symbols/sides)")
    logger.info("=" * 70)
    logger.info(
        f"\n{'Size':>10} {'Worst measured':>16} {'Mean measured':>15} "
        f"{'Worst x modeled':>17} {'Available':>12}"
    )
    logger.info("-" * 75)
    for _, row in summary.iterrows():
        logger.info(
            f"  ${row['order_size_usdt']:>8,.0f} "
            f"{row['worst_measured_slippage_pct']:>15.4%} "
            f"{row['mean_measured_slippage_pct']:>14.4%} "
            f"{row['worst_measured_multiple']:>16.2f}x "
            f"{int(row['n_available']):>7}/{int(row['n_total']):<3}"
        )

    unavailable = df[~df["available"]]
    if not unavailable.empty:
        logger.info(f"\n  {len(unavailable)} (symbol, side, size) combination(s) unavailable:")
        for symbol, reason in unavailable[["symbol", "unavailable_reason"]].drop_duplicates().values:
            logger.info(f"    {symbol}: {reason}")

    logger.info(f"\n  Measured at: {datetime.now(timezone.utc).isoformat()}")
