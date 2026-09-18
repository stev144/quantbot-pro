# ============================================================
# bot/research/forex_cointegration_scan.py
#
# claude code changed: new file. Forex's equivalent of
# cointegration_engine.py's own run_cointegration_research() driver —
# reuses CointegrationEngine and bot.research.data_access.load_ohlcv_via_provider
# UNCHANGED (zero new statistical logic), just scoped to the Forex
# universe instead of cointegration_engine.py's own crypto UNIVERSE
# constant. Kept as a separate driver rather than adding asset_class
# plumbing to run_cointegration_research() itself, since that function is
# already tested against crypto's real 5-year/live-fetch defaults and
# this script's defaults are deliberately different (see HISTORY_DAYS
# below) — two small, independently-testable drivers over one function
# with diverging default behavior per asset class.
#
# Written to close a real, concrete gap: no Forex pair has ever been run
# through the cointegration -> Kalman -> entry/exit pipeline, so
# bot/views/pairs_performance.py's "Pairs Performance" page — which reads
# real entry_exit_engine.py output from research_data/*_equity_curve.csv —
# has never had a Forex pair to show, unlike crypto's DODO/FIDA etc.
# This script is step 1 of populating that: find which Forex pairs are
# genuinely cointegrated at all.
#
# Output is a SEPARATE file (forex_cointegration_pairs.csv, not
# cointegration_pairs.csv) so this never overwrites or gets confused with
# the crypto 100-coin scan cointegration_pairs.csv already on disk.
# ============================================================

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from bot.instruments import ASSET_CLASS_FOREX, symbols_for_asset_class
from bot.research.cointegration_engine import (
    MIN_OOS_CANDLES,
    TRAINING_WINDOW,
    CointegrationEngine,
    _rejection_status_label,
)
from bot.research.data_access import load_ohlcv_via_provider

logger = logging.getLogger(__name__)

if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

# claude code changed: new — the 28 major/cross pairs this project has
# real 1h OHLCV for (data/forex/*.csv), derived from the single
# instrument registry (bot.instruments), same "derive, never re-type"
# principle cointegration_engine.py's own UNIVERSE constant uses.
FOREX_UNIVERSE: List[str] = [
    s.replace("/", "_") for s in symbols_for_asset_class(ASSET_CLASS_FOREX)
]

# claude code changed: new — Yahoo Finance's real, documented intraday
# history ceiling (bot/forex_data_fetcher.py's own HISTORY_DAYS=729
# constant, "a real, documented provider limit, not a guess"). Crypto's
# run_cointegration_research() defaults to a 5-year lookback because
# Binance can serve that; Forex/Yahoo cannot, so this driver's default
# is deliberately different rather than reusing crypto's constant and
# silently truncating.
FOREX_HISTORY_DAYS = 729


def _resample_ohlcv(df: pd.DataFrame, hours: int) -> pd.DataFrame:
    """
    claude code changed: new — real OHLCV aggregation of the SAME fetched
    1h data (open=first, high=max, low=min, close=last, volume=sum), not
    new/fabricated data. Exists because MAX_HALF_LIFE_HOURS
    (cointegration_engine.py) is expressed in raw CANDLES, with an
    explicit inline comment tying that to "5 days at 1h" — a unit
    baked in around crypto's only-ever-1h pipeline. Running the identical
    120-candle ceiling against 1h Forex data silently reuses that 5-day
    real-world cutoff for an asset class whose macro/carry-driven mean
    reversion is structurally slower; resampling to 4h before testing
    makes the SAME 120-candle threshold represent 20 real days instead —
    a deliberate, documented unit correction, not a loosened bar.
    """
    return (
        df.resample(f"{hours}h")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["close"])
    )


def run_forex_cointegration_scan(
    output_dir: str = "research_data",
    output_filename: str = "forex_cointegration_pairs.csv",
    interval: str = "1h",
    universe: Optional[List[str]] = None,
    start=None,
    end=None,
    resample_hours: Optional[int] = None,
) -> pd.DataFrame:
    """
    Fetches every Forex universe symbol via the canonical
    MarketDataProvider contract (YahooForexProvider, routed through
    load_ohlcv_via_provider), runs the same CointegrationEngine crypto
    uses, and saves real pair-test results — no cherry-picking, every
    pair in the universe is tested.

    `resample_hours`: if given, real OHLCV data is aggregated from the
    fetched 1h bars to this candle width before testing (see
    _resample_ohlcv's docstring for why this matters for Forex
    specifically). None (default) tests native 1h, matching crypto's own
    convention exactly.

    claude code changed: new — when resample_hours is given,
    TRAINING_WINDOW/MIN_OOS_CANDLES (both raw-candle constants, same
    "calibrated for 1h" situation MAX_HALF_LIFE_HOURS was in) are scaled
    down by the same factor, so they represent the SAME real-world
    duration at the coarser candle width instead of silently demanding
    4x the wall-clock history a 4h-candle count implies. Passing
    resample_hours=4 without this would trade one unit-mismatch
    (half-life) for another (training window) — this closes both with
    one consistent principle: convert candle-count constants when the
    candle width changes, don't just reuse crypto's 1h-calibrated raw
    counts. CointegrationEngine.__init__ already exposes
    training_window/min_oos_candles as constructor overrides explicitly
    "for sensitivity analysis" (see its own docstring) — this uses that
    existing, designed-for-this mechanism, not a new backdoor.
    """
    if end is None:
        end = datetime.now(timezone.utc)
    if start is None:
        start = end - timedelta(days=FOREX_HISTORY_DAYS)

    symbols = universe or FOREX_UNIVERSE
    scale = resample_hours or 1
    training_window = max(1, TRAINING_WINDOW // scale)
    min_oos_candles = max(1, MIN_OOS_CANDLES // scale)

    logger.info("=" * 70)
    logger.info("FOREX COINTEGRATION SCAN — STANDALONE RUN")
    logger.info("=" * 70)
    logger.info(f"  Universe : {len(symbols)} symbols")
    logger.info(f"  Window   : {start.date()} -> {end.date()}")
    if resample_hours:
        logger.info(f"  Resampled to {resample_hours}h candles before testing")
        logger.info(f"  training_window={training_window} (was {TRAINING_WINDOW}), min_oos_candles={min_oos_candles} (was {MIN_OOS_CANDLES}) — same real-world duration at {resample_hours}h")

    data: Dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        df = load_ohlcv_via_provider(symbol, ASSET_CLASS_FOREX, interval, start, end)
        if df is not None:
            if resample_hours:
                df = _resample_ohlcv(df, resample_hours)
            data[symbol] = df
            logger.info(f"  Loaded {symbol}: {len(df):,} candles")

    engine = CointegrationEngine(universe=symbols, training_window=training_window, min_oos_candles=min_oos_candles)
    pairs_df, spread_data = engine.run_all(data)

    Path(output_dir).mkdir(exist_ok=True)
    if not pairs_df.empty:
        out_path = Path(output_dir) / output_filename
        pairs_df.to_csv(out_path, index=False)
        logger.info(f"\n  Saved: {out_path}")

        logger.info("\n" + "=" * 70)
        logger.info("ALL PAIRS — TEST RESULTS")
        logger.info("=" * 70)
        logger.info(
            f"\n{'Pair':20} {'Coint_p':>9} {'ADF_p':>8} "
            f"{'β':>8} {'HL(h)':>8} {'Status':>16}"
        )
        logger.info("-" * 70)
        for _, row in pairs_df.sort_values("coint_pvalue").iterrows():
            status = _rejection_status_label(row)
            logger.info(
                f"  {row['pair_name']:20} "
                f"{row['coint_pvalue']:>9.4f} "
                f"{row['adf_pvalue']:>8.4f} "
                f"{row['hedge_ratio']:>8.4f} "
                f"{row['half_life_hours']:>8.1f} "
                f"{status:>16}"
            )

        n_pass = int(pairs_df["passes_filters"].sum())
        logger.info(f"\n  {n_pass}/{len(pairs_df)} pairs passed all filters.")

    return pairs_df


if __name__ == "__main__":
    run_forex_cointegration_scan()
