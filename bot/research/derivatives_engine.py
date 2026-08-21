# ═══════════════════════════════════════════════════════════════════════════════
# bot/research/derivatives_engine.py
# claude code changed: new — Funding Rate + Open Interest feature engine.
#
# Merges bot/engines/derivatives_data.py's funding rate / open interest
# history onto a symbol's OHLCV candles and computes research features
# from them, following the exact same research-first discipline as
# contagion_engine.py: this engine does NOT generate trading signals, it
# generates candidate features for feature_validator.py to test. Nothing
# here is wired into StrategyRouter/bot_runner.py.
#
# Two real data-shape differences from every other feature source in this
# project, both load-bearing for how this engine works:
#
#   - Funding rate prints every 8h, not every 1h like OHLCV. Computing a
#     rolling z-score directly on a step-repeated (forward-filled-onto-1h)
#     series would understate real variance — most consecutive rows would
#     be identical, artificially shrinking the rolling std. So z-score/
#     cumulative/change features are computed on the NATIVE 8h funding
#     series first, then merge_asof'd onto the 1h OHLCV grid — never the
#     other way around.
#   - Open interest history only goes back ~30 days (Binance's real API
#     limit — see derivatives_data.py). Merged onto years of OHLCV
#     history, OI columns are real for the most recent ~30 days and NaN
#     everywhere before that. This is not a bug: feature_validator.py
#     already drops NaN rows per-feature, so an OI feature is simply
#     validated on whatever real observations exist for it, honestly
#     fewer than the funding/price features get.
#
# Both merges use merge_asof(direction="backward") — each OHLCV candle is
# matched to the most recent funding print / OI reading that had already
# happened by that candle's own timestamp. Never a future value bleeding
# into the past.
# ═══════════════════════════════════════════════════════════════════════════════

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)

FUNDING_ZSCORE_LOOKBACK = 90    # 90 funding prints @ 8h = 30 days
FUNDING_ZSCORE_MIN_PERIODS = 21  # ~1 week of funding prints

OI_CHANGE_WINDOWS_HOURS: List[int] = [1, 24]

# Same convention as contagion_engine.py's FORWARD_HORIZONS — the
# validator tests each one, we don't assume which horizon has edge.
FORWARD_HORIZONS: Dict[str, int] = {
    "forward_return_1h":  1,
    "forward_return_2h":  2,
    "forward_return_4h":  4,
    "forward_return_12h": 12,
}

MIN_CANDLES = 500


def _to_utc_datetime(series: pd.Series) -> pd.Series:
    """Timestamps arrive as either epoch-ms ints (derivatives_data.py's
    output) or already-parsed datetimes (OHLCV CSVs) — normalize both to
    a real, timezone-aware datetime at a fixed (ns) resolution, since
    merge_asof requires its two join keys to share the exact same dtype,
    including time unit (confirmed empirically: OHLCV's parsed strings
    land on datetime64[us, UTC] while funding/OI's unit="ms" parse lands
    on datetime64[ms, UTC] — same semantic timestamps, different dtypes,
    which merge_asof rejects outright rather than silently upcasting)."""
    if pd.api.types.is_numeric_dtype(series):
        parsed = pd.to_datetime(series, unit="ms", utc=True)
    else:
        parsed = pd.to_datetime(series, utc=True)
    return parsed.astype("datetime64[ns, UTC]")


class DerivativesEngine:
    """
    Funding rate + open interest feature engine for a single symbol.

    Hypothesis: funding rate (crowd positioning/leverage cost) and open
    interest (aggregate leveraged exposure) carry information about
    forward price moves that spot OHLCV alone cannot — e.g. extreme
    positive funding (crowded longs) preceding mean-reversion, or open
    interest rising while price stalls (leveraged buildup ahead of a
    squeeze). This engine computes the candidate features; it does not
    decide whether the hypothesis is true — feature_validator.py does.
    """

    def __init__(
        self,
        funding_zscore_lookback: int = FUNDING_ZSCORE_LOOKBACK,
        funding_zscore_min_periods: int = FUNDING_ZSCORE_MIN_PERIODS,
        oi_change_windows_hours: List[int] = None,
        forward_horizons: Dict[str, int] = None,
    ) -> None:
        self.funding_zscore_lookback = funding_zscore_lookback
        self.funding_zscore_min_periods = funding_zscore_min_periods
        self.oi_change_windows_hours = oi_change_windows_hours or OI_CHANGE_WINDOWS_HOURS
        self.forward_horizons = forward_horizons or FORWARD_HORIZONS

    # ─────────────────────────────────────────────────────────────────
    # PUBLIC: calculate_all()
    # ─────────────────────────────────────────────────────────────────

    def calculate_all(
        self,
        ohlcv_df: pd.DataFrame,
        funding_df: Optional[pd.DataFrame],
        oi_df: Optional[pd.DataFrame],
    ) -> pd.DataFrame:
        """
        ohlcv_df   : columns [timestamp, open, high, low, close, volume, ...]
        funding_df : columns [timestamp, funding_rate, mark_price] or None/empty
        oi_df      : columns [timestamp, open_interest_amount, open_interest_value] or None/empty

        Returns ohlcv_df enriched with funding_/oi_ feature columns and
        forward_return_* labels. Never mutates the inputs.
        """
        if len(ohlcv_df) < MIN_CANDLES:
            raise ValueError(
                f"calculate_all: need at least {MIN_CANDLES} candles, got {len(ohlcv_df)}"
            )

        df = ohlcv_df.copy()
        df["timestamp"] = _to_utc_datetime(df["timestamp"])
        df.sort_values("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)

        df = self._merge_funding(df, funding_df)
        df = self._merge_open_interest(df, oi_df)
        df = self._add_forward_returns(df)

        return df

    # ─────────────────────────────────────────────────────────────────
    # FUNDING RATE
    # ─────────────────────────────────────────────────────────────────

    def _merge_funding(self, df: pd.DataFrame, funding_df: Optional[pd.DataFrame]) -> pd.DataFrame:
        if funding_df is None or funding_df.empty:
            logger.info("  No funding rate data — funding_* columns will be all-NaN")
            df["funding_rate"] = np.nan
            df["funding_rate_zscore"] = np.nan
            df["funding_rate_change"] = np.nan
            df["funding_rate_cumulative_24h"] = np.nan
            return df

        fdf = funding_df.copy()
        fdf["timestamp"] = _to_utc_datetime(fdf["timestamp"])
        fdf.sort_values("timestamp", inplace=True)
        fdf.reset_index(drop=True, inplace=True)

        # claude code changed: derived features computed on the NATIVE 8h
        # cadence — see module docstring for why this must happen before
        # merging onto the 1h grid, not after.
        fdf["funding_rate_change"] = fdf["funding_rate"].diff()
        fdf["funding_rate_cumulative_24h"] = fdf["funding_rate"].rolling(window=3, min_periods=1).sum()

        roll_mean = fdf["funding_rate"].rolling(
            window=self.funding_zscore_lookback, min_periods=self.funding_zscore_min_periods
        ).mean()
        roll_std = fdf["funding_rate"].rolling(
            window=self.funding_zscore_lookback, min_periods=self.funding_zscore_min_periods
        ).std()
        fdf["funding_rate_zscore"] = (fdf["funding_rate"] - roll_mean) / roll_std.replace(0, np.nan)

        merge_cols = ["timestamp", "funding_rate", "funding_rate_change",
                      "funding_rate_cumulative_24h", "funding_rate_zscore"]
        df = pd.merge_asof(df, fdf[merge_cols], on="timestamp", direction="backward")
        return df

    # ─────────────────────────────────────────────────────────────────
    # OPEN INTEREST
    # ─────────────────────────────────────────────────────────────────

    def _merge_open_interest(self, df: pd.DataFrame, oi_df: Optional[pd.DataFrame]) -> pd.DataFrame:
        if oi_df is None or oi_df.empty:
            logger.info("  No open interest data — oi_* columns will be all-NaN")
            df["open_interest_amount"] = np.nan
            for hours in self.oi_change_windows_hours:
                df[f"oi_change_{hours}h"] = np.nan
            df["oi_price_interaction"] = np.nan
            return df

        odf = oi_df.copy()
        odf["timestamp"] = _to_utc_datetime(odf["timestamp"])
        odf.sort_values("timestamp", inplace=True)
        odf.reset_index(drop=True, inplace=True)

        # oi_df is already ~1h cadence (fetch_open_interest_history's
        # timeframe param), so pct_change windows map directly to hours.
        for hours in self.oi_change_windows_hours:
            odf[f"oi_change_{hours}h"] = odf["open_interest_amount"].pct_change(periods=hours)

        merge_cols = ["timestamp", "open_interest_amount"] + [f"oi_change_{h}h" for h in self.oi_change_windows_hours]
        df = pd.merge_asof(df, odf[merge_cols], on="timestamp", direction="backward")

        # claude code changed: continuous interaction term — price return
        # times OI's own 1h change. Positive = price and leveraged
        # exposure moving together (trend-confirming); negative = OI
        # building while price stalls/reverses (classic pre-squeeze
        # divergence). Kept as one numeric feature (not a categorical
        # flag) so feature_validator.py's IC/quartile machinery applies
        # to it exactly like every other feature.
        df["oi_price_interaction"] = df["close"].pct_change() * df["oi_change_1h"]

        return df

    # ─────────────────────────────────────────────────────────────────
    # FORWARD RETURNS (labels)
    # ─────────────────────────────────────────────────────────────────

    def _add_forward_returns(self, df: pd.DataFrame) -> pd.DataFrame:
        for label, periods in self.forward_horizons.items():
            df[label] = df["close"].shift(-periods) / df["close"] - 1
        return df


# ═══════════════════════════════════════════════════════════════════════════════
# QUICK IC REPORTER
# ═══════════════════════════════════════════════════════════════════════════════

class DerivativesICReporter:
    """
    Fast Spearman-IC pre-screen for funding_/oi_ features, mirroring
    contagion_engine.DivergenceICReporter's method exactly. Not a
    replacement for feature_validator.py's full institutional pipeline —
    a cheap first look before committing to it.
    """

    @staticmethod
    def report(
        data: Dict[str, pd.DataFrame],
        symbols: List[str] = None,
        forward_col: str = "forward_return_2h",
    ) -> pd.DataFrame:
        symbols = symbols or list(data.keys())
        results = []

        for symbol in symbols:
            if symbol not in data:
                continue

            df = data[symbol]
            if forward_col not in df.columns:
                logger.warning(f"  {symbol}: {forward_col} not found, skipping IC report")
                continue

            fwd = df[forward_col]
            feature_cols = [c for c in df.columns if c.startswith("funding_") or c.startswith("oi_")]

            for feature in feature_cols:
                feat = df[feature]
                valid = feat.notna() & fwd.notna()
                n = valid.sum()
                if n < 100:
                    continue

                ic, pvalue = stats.spearmanr(feat[valid].values, fwd[valid].values)
                results.append({
                    "symbol": symbol, "feature": feature,
                    "ic": round(float(ic), 6), "pvalue": round(float(pvalue), 6),
                    "n": int(n), "significant": pvalue < 0.05,
                })

        if not results:
            logger.warning("No IC results computed. Check data and forward_col.")
            return pd.DataFrame()

        results_df = pd.DataFrame(results)
        results_df["abs_ic"] = results_df["ic"].abs()
        results_df = results_df.sort_values("abs_ic", ascending=False)
        results_df = results_df.drop(columns=["abs_ic"])
        return results_df


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_derivatives_research(
    data_dir: str = "data",
    output_dir: str = "research_data",
    interval: str = "1h",
) -> Dict[str, pd.DataFrame]:
    """
    Loads each symbol's OHLCV + funding + OI CSVs (from
    fetch_all_symbols.py / fetch_derivatives_data.py), runs
    DerivativesEngine, saves enriched output, and prints a quick IC
    report. Symbols with no funding/OI CSV on disk are still processed —
    they just get all-NaN funding_/oi_ columns (see calculate_all()).

    Run from project root: python -m bot.research.derivatives_engine
    """
    from pathlib import Path
    from bot.fetch_all_symbols import SYMBOLS

    logger.info("=" * 70)
    logger.info("DERIVATIVES ENGINE — FUNDING RATE + OPEN INTEREST — STANDALONE RUN")
    logger.info("=" * 70)

    engine = DerivativesEngine()
    enriched: Dict[str, pd.DataFrame] = {}

    for symbol in SYMBOLS:
        stem = symbol.replace("/", "_")
        ohlcv_path = Path(data_dir) / f"{stem}_{interval}.csv"
        funding_path = Path(data_dir) / f"{stem}_funding.csv"
        oi_path = Path(data_dir) / f"{stem}_oi.csv"

        if not ohlcv_path.exists():
            logger.warning(f"  {symbol}: OHLCV not found at {ohlcv_path}, skipping")
            continue

        try:
            ohlcv_df = pd.read_csv(ohlcv_path)
            funding_df = pd.read_csv(funding_path) if funding_path.exists() else None
            oi_df = pd.read_csv(oi_path) if oi_path.exists() else None

            df = engine.calculate_all(ohlcv_df, funding_df, oi_df)
            enriched[symbol] = df
            logger.info(
                f"  {symbol}: {len(df):,} candles | "
                f"funding rows={0 if funding_df is None else len(funding_df)} | "
                f"oi rows={0 if oi_df is None else len(oi_df)}"
            )
        except Exception as e:
            logger.error(f"  {symbol}: failed — {e}")

    logger.info("\n" + "=" * 70)
    logger.info("QUICK IC REPORT — forward_return_2h")
    logger.info("=" * 70)

    ic_results = DerivativesICReporter.report(enriched, forward_col="forward_return_2h")
    if not ic_results.empty:
        top = ic_results.head(20)
        logger.info(f"\n{'Symbol':12} {'Feature':30} {'IC':>8} {'p-value':>10} {'N':>8} {'Sig':>5}")
        logger.info("-" * 76)
        for _, row in top.iterrows():
            sig_marker = "Y" if row["significant"] else " "
            logger.info(
                f"  {row['symbol']:12} {row['feature']:30} "
                f"{row['ic']:>+8.4f} {row['pvalue']:>10.6f} {row['n']:>8,} {sig_marker:>5}"
            )

    Path(output_dir).mkdir(exist_ok=True)
    for symbol, df in enriched.items():
        stem = symbol.replace("/", "_")
        out = Path(output_dir) / f"{stem}_derivatives.csv"
        df.to_csv(out, index=False)
        logger.info(f"  Saved: {out}")

    logger.info("\n" + "=" * 70)
    logger.info("DERIVATIVES ENGINE — COMPLETE")
    logger.info("=" * 70)

    return enriched


if __name__ == "__main__":
    run_derivatives_research()
