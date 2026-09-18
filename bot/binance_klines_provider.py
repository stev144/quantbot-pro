# ============================================================
# bot/binance_klines_provider.py
# Data-Layer Audit — Step 3.
#
# claude code changed: new file. The first concrete MarketDataProvider —
# a thin wrapper around bot/data_fetcher.py's EXISTING get_klines_by_date(),
# not a reimplementation. Mirrors bot/engines/binance_adapter.py's own
# relationship to OrderManager/MarketData: this class holds zero fetch
# logic of its own (no requests calls, no pagination, no retry handling —
# all of that stays exactly where it already lives, in data_fetcher.py,
# untouched by this step). Its only job is translating between the
# canonical MarketDataProvider contract and data_fetcher.py's existing,
# battle-tested shape.
#
# NORMALIZATION PERFORMED HERE (section 8 of the audit brief — "the
# adapter layer should normalize provider-specific data into the
# canonical representation, provider-specific quirks must be handled in
# the adapter, not propagated throughout research code"):
#   - get_klines_by_date() returns a DataFrame with "timestamp" as the
#     INDEX (data_fetcher.py's own long-standing convention) — CanonicalBars
#     requires "timestamp" as a COLUMN (REQUIRED_BAR_COLUMNS). Reset here.
#   - Binance's own interval strings ("1h", "4h", "1d", ...) already ARE
#     the canonical timeframe strings bot.instruments.TIMEFRAME_MINUTES_PER_CANDLE
#     uses — the one provider where this translation is the identity
#     function, not a coincidence to rely on for MT5/Yahoo (Step 4).
#
# claude code changed: Data-Layer Audit Step "wire into fetch_all_symbols.py"
# — REVISED from Step 3's original behavior, which trimmed the result down
# to exactly REQUIRED_BAR_COLUMNS, silently dropping Binance's real
# qav/num_trades/taker_base_vol/taker_quote_vol/close_time/ignore fields.
# That was over-normalization: CanonicalBars.__post_init__ only requires
# REQUIRED_BAR_COLUMNS to be PRESENT, it never forbids extra real columns
# — and bot/fetch_all_symbols.py's existing prepare_dataframe_for_csv()
# has deliberately persisted qav/num_trades/taker_base_vol to every
# data/*.csv file since Phase 2B Step 1 (see that function's own
# docstring: "classified 'safe to persist immediately'... not
# accumulating fields for their own sake"). Silently dropping them the
# moment fetch_all_symbols.py started routing through this provider would
# have been a real, unrequested data-loss regression. Now ALL of
# _build_ohlcv_dataframe()'s columns pass through untouched; only the
# index→column reset happens here. optional_columns_present() still
# correctly reports an empty set for these (qav/num_trades/taker_base_vol
# aren't in OPTIONAL_BAR_COLUMNS' closed cross-venue vocabulary — they're
# Binance-specific raw passthrough, a different, legitimate category).
# ============================================================

from __future__ import annotations

from datetime import datetime
from typing import FrozenSet

import pandas as pd

from bot import data_fetcher
from bot.instruments import Instrument
from bot.market_data_provider import (
    REQUIRED_BAR_COLUMNS,
    CanonicalBars,
    DataCapability,
    MarketDataProvider,
)
from bot.research_lab.data_fingerprint import DatasetIdentity


class BinanceKlinesProvider(MarketDataProvider):
    """claude code changed: new. The Binance historical-bars provider —
    wraps bot.data_fetcher.get_klines_by_date() unchanged. No fetch/retry/
    pagination logic lives here; see module docstring."""

    provider_id = "binance"

    def capabilities(self) -> FrozenSet[str]:
        """claude code changed: new — HISTORICAL_BARS only. Binance's raw
        kline response has no bid/ask/tick_volume/real_volume fields
        (confirmed by _build_ohlcv_dataframe()'s own fixed column list) —
        claiming those here would violate DataCapability's own "never a
        speculative superset" rule."""
        return frozenset({DataCapability.HISTORICAL_BARS})

    def get_historical_bars(
        self,
        instrument: Instrument,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> CanonicalBars:
        self.require(DataCapability.HISTORICAL_BARS)

        raw = data_fetcher.get_klines_by_date(instrument.canonical_symbol, timeframe, start, end)

        if raw.empty:
            # claude code changed: new — an empty result still needs the
            # canonical SHAPE (named columns), not just an empty frame —
            # CanonicalBars.__post_init__ checks for column presence by
            # name, which a bare pd.DataFrame() (data_fetcher.py's own
            # empty-result convention) doesn't have.
            bars_df = pd.DataFrame(columns=list(REQUIRED_BAR_COLUMNS))
        else:
            # claude code changed: was `[list(REQUIRED_BAR_COLUMNS)]` — see
            # module docstring. Keeps every real column
            # _build_ohlcv_dataframe() already produced; only the
            # index→column reset is this provider's job.
            bars_df = raw.reset_index()

        identity = DatasetIdentity(
            source="binance_spot_klines",
            symbol=instrument.canonical_symbol,
            venue=self.provider_id,
            timeframe=timeframe,
            start_date=start.date().isoformat(),
            end_date=end.date().isoformat(),
            row_count=len(bars_df),
        )
        return CanonicalBars(data=bars_df, instrument=instrument, timeframe=timeframe, identity=identity)

    def get_recent_bars(
        self,
        instrument: Instrument,
        timeframe: str,
        total_candles: int,
        use_cache: bool = True,
        cache_ttl_seconds: int = data_fetcher.DEFAULT_CACHE_TTL_SECONDS,
    ) -> CanonicalBars:
        """
        claude code changed: new — Data-Layer Audit "wire into the
        dashboard too". Binance-specific, deliberately NOT part of the
        abstract MarketDataProvider contract: "most recent N candles" is
        a Binance/dashboard-specific query shape (Yahoo/MT5's own
        fetchers only support the date-range shape get_historical_bars()
        already covers), the same way KrakenAdapter.place_stop_loss() is
        its own implementation beyond ExchangeAdapter's shared surface
        (per CLAUDE.md's own documented precedent for provider-specific
        extensions).

        Wraps data_fetcher.get_klines() — NOT get_klines_by_date() the
        way get_historical_bars() does — for one load-bearing reason:
        get_klines()'s cache entries use the key format
        f"klines_{normalized}_{interval}_{total_candles}", and
        data_fetcher.get_last_known_klines() (bot/views/dashboard.py's
        stale-data fallback for a live Binance outage) reads from THAT
        exact namespace. get_klines_by_date() writes into a completely
        different key format
        (f"klines_date_..._{start_ms}_{end_ms}_..."). Routing this
        through get_klines_by_date() instead would silently starve
        get_last_known_klines() of any cache entry to fall back to,
        breaking the dashboard's degraded-mode UX the first time live
        Binance access actually failed — confirmed by reading
        get_last_known_klines()'s own docstring and cache-key
        construction directly, not assumed.
        """
        self.require(DataCapability.HISTORICAL_BARS)

        raw = data_fetcher.get_klines(
            instrument.canonical_symbol, interval=timeframe, total_candles=total_candles,
            use_cache=use_cache, cache_ttl_seconds=cache_ttl_seconds,
        )

        if raw is None or raw.empty:
            bars_df = pd.DataFrame(columns=list(REQUIRED_BAR_COLUMNS))
            start_date, end_date = "unknown", "unknown"
        else:
            bars_df = raw.reset_index()
            start_date = str(bars_df["timestamp"].iloc[0].date())
            end_date = str(bars_df["timestamp"].iloc[-1].date())

        identity = DatasetIdentity(
            source="binance_spot_klines",
            symbol=instrument.canonical_symbol,
            venue=self.provider_id,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            row_count=len(bars_df),
        )
        return CanonicalBars(data=bars_df, instrument=instrument, timeframe=timeframe, identity=identity)
