# ============================================================
# bot/research/orderbook_depth_engine.py
# claude code changed: new file — Phase 2F (Order-Book/L2 Microstructure
# Research Gate). Step 0's audit confirmed NO historical order-book source
# existed anywhere in this repo (ExchangeAdapter.get_order_book() is a
# LIVE single-snapshot fetch only, used for execution-side liquidity/venue
# comparison — bot/engines/liquidity.py, bot/engines/execution_comparison.py
# — never persisted, never historical). Step 2's feasibility gate confirmed:
#   - Binance SPOT has no order-book/depth archive at all
#     (data/spot/daily/ = aggTrades/klines/trades only).
#   - Binance's futures (USDT-margined perp) `bookTicker` archive (real
#     best-bid/ask ticks — the only source for true top-of-book imbalance
#     or quoted spread) is REAL but STOPS at 2023-12-31 and never resumes —
#     confirmed by bisecting the live listing, not assumed. Unusable for
#     this project's current research window.
#   - Binance's futures `bookDepth` archive IS real, historical (2023-01-01
#     through the present, both BTCUSDT/ETHUSDT symmetric), and small
#     (~34,560 rows/day/symbol) — but it is Binance's OWN PRE-AGGREGATED
#     cumulative depth/notional at fixed percentage bands from mid
#     (+/-0.2%, 1%, 2%, 3%, 4%, 5%), sampled ~every 30s. NOT raw per-price
#     L2 levels — no book reconstruction is possible or needed from it.
#
# This engine therefore implements ONLY the narrowed, data-feasible family
# (Phase 2F Step 2's conclusion): depth_imbalance_2pct and
# depth_concentration, both derived from bookDepth alone. It does NOT
# implement top-of-book imbalance or quoted spread — no data source exists
# for either under this project's current constraints (see module docstring
# above), and inventing one via websocket/diff-depth streaming is an
# explicit hard constraint violation for this phase.
#
# Cross-venue caveat (same one this project already accepts for funding/OI
# in derivatives_engine.py): this is USDT-margined PERPETUAL FUTURES
# microstructure, not spot — this project's actual traded/quoted price
# series (data_fetcher.py, bot_runner.py) is Binance SPOT. A futures
# depth/liquidity signal is a proxy for spot microstructure, not a direct
# measurement of it.
# ============================================================

import logging
import time
import zipfile
from io import BytesIO
from typing import Optional

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

ARCHIVE_BASE_URL = "https://data.binance.vision/data/futures/um/daily/bookDepth"
ARCHIVE_REQUEST_TIMEOUT_S = 60
MAX_TRANSIENT_RETRIES = 3   # claude code changed: same bounded-retry discipline as trade_data.py's fetch_agg_trades_from_archive

# claude code changed: the exact bands Binance's own bookDepth archive
# publishes — confirmed against a real downloaded file
# (BTCUSDT-bookDepth-2026-08-20.csv), not assumed from documentation.
DEPTH_BANDS_PCT = [0.2, 1.0, 2.0, 3.0, 4.0, 5.0]

DEPTH_COLUMNS = ["timestamp", "percentage", "depth", "notional"]


def fetch_book_depth_archive(symbol: str, date: str) -> pd.DataFrame:
    """
    Downloads one UTC day of Binance USDT-margined-perpetual bookDepth
    snapshots for `symbol` (e.g. "BTC/USDT") from the public archive.
    `date` is "YYYY-MM-DD".

    Returns columns: timestamp (tz-aware UTC), percentage (float, negative
    = bid side / positive = ask side, per DEPTH_BANDS_PCT), depth (base-asset
    cumulative quantity from mid to that band), notional (cumulative USD
    value). Empty DataFrame (never None, never raised) for a genuine 404
    (not yet published / no data that day) — matches trade_data.py's
    established missing-data convention for this exact archive family.
    """
    market_symbol = symbol.replace("/", "")
    url = f"{ARCHIVE_BASE_URL}/{market_symbol}/{market_symbol}-bookDepth-{date}.zip"

    response = None
    last_error = None
    for attempt in range(1, MAX_TRANSIENT_RETRIES + 1):
        try:
            response = requests.get(url, timeout=ARCHIVE_REQUEST_TIMEOUT_S)
            break
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < MAX_TRANSIENT_RETRIES:
                logger.warning(f"fetch_book_depth_archive({symbol}, {date}): transient error (attempt {attempt}/{MAX_TRANSIENT_RETRIES}): {e}")
                time.sleep(1.5 * attempt)
    if response is None:
        raise last_error

    if response.status_code == 404:
        logger.warning(f"fetch_book_depth_archive({symbol}, {date}): no archive file (404) — not yet published or no data that day")
        return pd.DataFrame(columns=DEPTH_COLUMNS)
    response.raise_for_status()

    with zipfile.ZipFile(BytesIO(response.content)) as zf:
        inner_name = zf.namelist()[0]
        with zf.open(inner_name) as f:
            raw = pd.read_csv(f)   # claude code changed: unlike aggTrades, this archive DOES ship a header row (timestamp,percentage,depth,notional) — confirmed against the real file

    if raw.empty:
        return pd.DataFrame(columns=DEPTH_COLUMNS)

    df = pd.DataFrame(columns=DEPTH_COLUMNS)
    df["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True)
    df["percentage"] = pd.to_numeric(raw["percentage"], errors="coerce")
    df["depth"] = pd.to_numeric(raw["depth"], errors="coerce")
    df["notional"] = pd.to_numeric(raw["notional"], errors="coerce")
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


class OrderBookDepthEngine:
    """
    Candle-aligned microstructure features computed strictly from
    bookDepth's own fixed-percentage-band cumulative depth/notional — the
    narrowed family Phase 2F Step 2 concluded was actually obtainable.
    Every number here is reproducible from the raw per-day CSV, nothing
    derived-then-discarded — same discipline trade_flow_engine.py and
    derivatives_engine.py already established.
    """

    def __init__(self, imbalance_band_pct: float = 2.0, concentration_near_pct: float = 0.2, concentration_far_pct: float = 5.0):
        if imbalance_band_pct not in DEPTH_BANDS_PCT:
            raise ValueError(f"imbalance_band_pct must be one of {DEPTH_BANDS_PCT}, got {imbalance_band_pct}")
        if concentration_near_pct not in DEPTH_BANDS_PCT or concentration_far_pct not in DEPTH_BANDS_PCT:
            raise ValueError(f"concentration bands must be in {DEPTH_BANDS_PCT}")
        self.imbalance_band_pct = imbalance_band_pct
        self.concentration_near_pct = concentration_near_pct
        self.concentration_far_pct = concentration_far_pct

    def _pivot_snapshots(self, depth_df: pd.DataFrame) -> pd.DataFrame:
        """
        Reshapes the long (timestamp, percentage, depth, notional) rows into
        one row per real snapshot timestamp, with explicit
        bid_notional_<pct>/ask_notional_<pct> columns. Pure reshape — no
        information added or discarded, every value traceable back to one
        raw row.
        """
        work = depth_df.copy()
        work["side"] = np.where(work["percentage"] < 0, "bid", "ask")
        work["band"] = work["percentage"].abs()

        wide = work.pivot_table(index="timestamp", columns=["side", "band"], values="notional", aggfunc="first")
        wide.columns = [f"{side}_notional_{band}" for side, band in wide.columns]
        wide = wide.sort_index()
        wide.reset_index(inplace=True)
        return wide

    def compute_features(self, depth_df: pd.DataFrame, candle_index: pd.DatetimeIndex) -> pd.DataFrame:
        """
        Produces one row per candle in `candle_index`, causally aligned via
        merge_asof(direction="backward") — a candle at time t may only see
        the most recent bookDepth snapshot with timestamp <= t, exactly the
        same causal-merge convention derivatives_engine.py's funding/OI
        merges already use and are already tested for (Phase 2E's
        MergeFundingNoLookaheadTest / OpenInterestNoLookaheadTest precedent).

        Columns: depth_imbalance_2pct, depth_concentration. A candle before
        the first real snapshot, or with no available snapshot at all
        (empty depth_df), gets NaN — never fabricated/backfilled.
        """
        result = pd.DataFrame(index=candle_index)
        result.index.name = "timestamp"

        if depth_df.empty:
            result["depth_imbalance_2pct"] = np.nan
            result["depth_concentration"] = np.nan
            result.reset_index(inplace=True)
            return result

        wide = self._pivot_snapshots(depth_df)

        imb_bid_col = f"bid_notional_{self.imbalance_band_pct}"
        imb_ask_col = f"ask_notional_{self.imbalance_band_pct}"
        near_bid_col = f"bid_notional_{self.concentration_near_pct}"
        near_ask_col = f"ask_notional_{self.concentration_near_pct}"
        far_bid_col = f"bid_notional_{self.concentration_far_pct}"
        far_ask_col = f"ask_notional_{self.concentration_far_pct}"

        imb_denom = wide[imb_bid_col] + wide[imb_ask_col]
        wide["depth_imbalance_2pct"] = (wide[imb_bid_col] - wide[imb_ask_col]) / imb_denom.replace(0, np.nan)

        near_total = wide[near_bid_col] + wide[near_ask_col]
        far_total = wide[far_bid_col] + wide[far_ask_col]
        wide["depth_concentration"] = near_total / far_total.replace(0, np.nan)

        merge_cols = ["timestamp", "depth_imbalance_2pct", "depth_concentration"]
        candle_df = pd.DataFrame({"timestamp": candle_index})

        # claude code changed: real bug found during Phase 2F's first
        # end-to-end run on live data (not caught by this module's own
        # synthetic-fixture tests, which happened to construct both sides
        # at the same resolution). pandas >= 2.x/3.x's datetime64 dtype now
        # carries an explicit resolution (ms/us/ns), and merge_asof raises
        # ("incompatible merge keys") rather than silently upcasting when
        # the two sides differ — which they do here in practice:
        # OHLCV-derived candle_index is typically datetime64[ms] (built via
        # pd.to_datetime(..., unit="ms")), while this archive's own
        # timestamp strings parse to datetime64[us] by default. Normalizing
        # both sides to the same resolution immediately before the merge
        # is the fix — never a silent value change, only a unit alignment.
        target_unit = "us"
        candle_df["timestamp"] = candle_df["timestamp"].dt.as_unit(target_unit)
        wide = wide.copy()
        wide["timestamp"] = wide["timestamp"].dt.as_unit(target_unit)

        merged = pd.merge_asof(candle_df, wide[merge_cols], on="timestamp", direction="backward")
        return merged
