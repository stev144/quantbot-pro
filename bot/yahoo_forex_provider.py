# ============================================================
# bot/yahoo_forex_provider.py
# Data-Layer Audit — Step 4a.
#
# claude code changed: new file. The Yahoo Finance FX historical-bars
# provider — wraps bot/forex_data_fetcher.py's EXISTING get_forex_klines()
# unchanged. Same relationship BinanceKlinesProvider has to
# bot/data_fetcher.py: zero fetch/network logic duplicated here.
#
# TIMEFRAME: deliberately 1h-only, not a full canonical-timeframe
# passthrough. get_forex_klines() accepts an `interval` string but never
# validates it — it's sent straight to Yahoo's chart-endpoint params.
# forex_data_fetcher.py's own architecture (its INTERVAL constant, its
# SYMBOLS/filename convention, every test that exercises it) is 1h-only in
# practice; Yahoo's chart endpoint doesn't natively support "4h" (crypto's
# canonical set includes it, FX here doesn't). Silently passing an
# unsupported interval through would risk a confusing empty/wrong result
# rather than a clear failure — this provider raises explicitly instead,
# matching this codebase's "fail closed, never guess" convention
# (bot.instruments.UnsupportedTimeframeError is the same idea, applied to
# a different boundary).
#
# DATE-RANGE ADAPTATION: get_forex_klines(symbol, interval, history_days)
# has no native start/end parameter (unlike data_fetcher.get_klines_by_date())
# — it always fetches "the most recent N days." This provider computes a
# covering history_days from `start`, then filters the result down to the
# real requested [start, end] window — the same overfetch-then-filter
# pattern data_fetcher.get_klines itself uses internally (df.tail()),
# just applied at the provider boundary instead of inside the fetcher.
# ============================================================

from __future__ import annotations

from datetime import datetime, timezone
from typing import FrozenSet

from bot import forex_data_fetcher
from bot.instruments import Instrument
from bot.market_data_provider import (
    REQUIRED_BAR_COLUMNS,
    CanonicalBars,
    DataCapability,
    MarketDataProvider,
)
from bot.research_lab.data_fingerprint import DatasetIdentity

_SUPPORTED_TIMEFRAME = "1h"


class YahooForexProvider(MarketDataProvider):
    """claude code changed: new. The Yahoo Finance FX provider — wraps
    bot.forex_data_fetcher.get_forex_klines() unchanged. No fetch/network
    logic lives here; see module docstring."""

    provider_id = "yahoo_finance"

    def capabilities(self) -> FrozenSet[str]:
        """claude code changed: new — HISTORICAL_BARS only. Yahoo's public
        chart endpoint reports FX volume as always 0 (confirmed live,
        documented in forex_data_fetcher.py's own module docstring) — not
        a real traded-volume signal, and there is no bid/ask/tick_volume
        field in this provider's response at all."""
        return frozenset({DataCapability.HISTORICAL_BARS})

    def get_historical_bars(
        self,
        instrument: Instrument,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> CanonicalBars:
        self.require(DataCapability.HISTORICAL_BARS)
        if timeframe != _SUPPORTED_TIMEFRAME:
            raise ValueError(
                f"YahooForexProvider only supports timeframe={_SUPPORTED_TIMEFRAME!r} "
                f"(got {timeframe!r}) — forex_data_fetcher.py's acquisition pipeline "
                f"is 1h-only; see this module's docstring"
            )

        now = datetime.now(timezone.utc)
        history_days = max(1, (now - start).days + 1)
        raw = forex_data_fetcher.get_forex_klines(instrument.canonical_symbol, interval=timeframe, history_days=history_days)

        # claude code changed: new — get_forex_klines() always returns a
        # DataFrame with the full named-column shape (built via
        # pd.DataFrame({"timestamp": ..., "open": ..., ...}) internally),
        # even when zero rows survive cleaning — never a bare, columnless
        # pd.DataFrame() the way data_fetcher.py's Binance path can. No
        # empty-frame special case is needed here.
        bars_df = raw[(raw["timestamp"] >= start) & (raw["timestamp"] <= end)].reset_index(drop=True)
        bars_df = bars_df[list(REQUIRED_BAR_COLUMNS)]

        identity = DatasetIdentity(
            source="yahoo_finance_chart_api",
            symbol=instrument.canonical_symbol,
            venue=self.provider_id,
            timeframe=timeframe,
            start_date=start.date().isoformat(),
            end_date=end.date().isoformat(),
            row_count=len(bars_df),
        )
        return CanonicalBars(data=bars_df, instrument=instrument, timeframe=timeframe, identity=identity)
