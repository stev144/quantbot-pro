# ============================================================
# bot/mt5_provider.py
# Data-Layer Audit — Step 4b.
#
# claude code changed: new file. The MT5 historical-bars provider — wraps
# bot/mt5_data_fetcher.py's EXISTING get_mt5_klines() unchanged. Same
# relationship BinanceKlinesProvider/YahooForexProvider have to their own
# fetchers: zero connection/pagination/timeframe-translation logic
# duplicated here (that all stays in mt5_data_fetcher.py, itself still an
# untested skeleton per its own module docstring — this file doesn't
# change that status, it only adds the same acquisition-contract wrapper
# every other provider already has).
#
# TIMEFRAME: passed straight through as the canonical string ("1h", "4h",
# "1d") — get_mt5_klines()'s own _mt5_timeframe() already translates that
# into the real mt5.TIMEFRAME_* constant internally and raises ValueError
# for anything else. No second translation table needed here; section 10
# of the audit brief's rule ("the research engine should never contain
# mt5.TIMEFRAME_H1") is satisfied by construction — this provider never
# imports the MetaTrader5 package at all.
#
# VOLUME SEMANTICS: MT5 reports real tick_volume (price-change count),
# not traded volume, but get_mt5_klines() deliberately keeps it in the
# REQUIRED 'volume' column rather than a separate one (documented in that
# function's own docstring, reused verbatim here) — so this provider does
# NOT claim DataCapability.TICK_VOLUME (that capability means a DISTINCT
# tick_volume column exists; MT5's fetcher doesn't currently emit one).
# Claiming it would overstate what CanonicalBars.optional_columns_present()
# can actually report for this provider today.
# ============================================================

from __future__ import annotations

from datetime import datetime, timezone
from typing import FrozenSet

from bot import mt5_data_fetcher
from bot.instruments import Instrument
from bot.market_data_provider import (
    REQUIRED_BAR_COLUMNS,
    CanonicalBars,
    DataCapability,
    MarketDataProvider,
)
from bot.research_lab.data_fingerprint import DatasetIdentity


class MT5Provider(MarketDataProvider):
    """claude code changed: new. The MT5 provider — wraps
    bot.mt5_data_fetcher.get_mt5_klines() unchanged. No connection/IPC
    logic lives here; see module docstring. Requires MT5_ENABLED plus real
    MT5_LOGIN/MT5_PASSWORD/MT5_SERVER (mt5_data_fetcher.connect()'s own
    gate) — raises MT5ConnectionError, not a silent empty result, when
    those aren't configured. Untested against a live terminal in THIS
    environment (none available), same honestly-disclosed status
    mt5_data_fetcher.py's own module docstring already carries."""

    provider_id = "mt5"

    def capabilities(self) -> FrozenSet[str]:
        return frozenset({DataCapability.HISTORICAL_BARS})

    def get_historical_bars(
        self,
        instrument: Instrument,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> CanonicalBars:
        self.require(DataCapability.HISTORICAL_BARS)

        now = datetime.now(timezone.utc)
        history_days = max(1, (now - start).days + 1)
        raw = mt5_data_fetcher.get_mt5_klines(instrument.canonical_symbol, interval=timeframe, history_days=history_days)

        bars_df = raw[(raw["timestamp"] >= start) & (raw["timestamp"] <= end)].reset_index(drop=True)
        bars_df = bars_df[list(REQUIRED_BAR_COLUMNS)]

        identity = DatasetIdentity(
            source="mt5_terminal_ipc",
            symbol=instrument.canonical_symbol,
            venue=self.provider_id,
            timeframe=timeframe,
            start_date=start.date().isoformat(),
            end_date=end.date().isoformat(),
            row_count=len(bars_df),
        )
        return CanonicalBars(data=bars_df, instrument=instrument, timeframe=timeframe, identity=identity)
