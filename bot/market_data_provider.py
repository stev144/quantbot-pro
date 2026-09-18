# ============================================================
# bot/market_data_provider.py
# Data-Layer Audit — Step 2.
#
# claude code changed: new file. The canonical, asset-agnostic historical-
# bars acquisition contract the audit's Deliverable B proposed — the
# analogue, on the DATA-ACQUISITION side, of bot/engines/exchange_adapter.py's
# ExchangeAdapter on the LIVE-EXECUTION side. Nothing in the codebase
# depends on this yet — Step 3/4 wrap bot/data_fetcher.py,
# bot/forex_data_fetcher.py, bot/mt5_data_fetcher.py behind it. This step
# is the contract only, the same incremental approach ExchangeAdapter
# itself used (see that file's own header comment).
#
# WHY A NEW ABC, NOT A REUSE OF EXCHANGEADAPTER: ExchangeAdapter's
# get_ohlcv() is a LIVE, single-call, ccxt-shaped method (returns a raw
# list, no historical date range, no provenance, and no venue has ever
# implemented it for a non-ccxt provider) — a different job from "fetch
# years of historical bars for research, with provenance, from a provider
# that may not even be ccxt-based" (MT5 is IPC, Yahoo is REST-but-not-ccxt).
# Two separate, non-overlapping systems today (confirmed by the audit) —
# this file only formalizes the acquisition side.
#
# WHY abc.ABC: same reason ExchangeAdapter/bot.config.cost_model.CostModel
# use it — a provider missing a method is a hard TypeError at
# construction, not a silent AttributeError the first time a research run
# actually calls it.
#
# REUSE, NOT REINVENTION: CanonicalBars carries provenance via the
# EXISTING bot.research_lab.data_fingerprint.DatasetIdentity — the
# fingerprinting mechanism the audit found already exists but is wired
# into almost nothing, not a second hashing scheme invented here. This
# file is additive only: it does not touch bot/instruments.py's
# resolve_ohlcv_path()/INSTRUMENT_REGISTRY, and no existing fetcher or
# research engine is modified by this step.
# ============================================================

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import FrozenSet

import pandas as pd

from bot.instruments import Instrument
from bot.research_lab.data_fingerprint import DatasetIdentity

# claude code changed: new — the required-vs-optional field split section 3
# of the audit brief asked for. REQUIRED: every provider (Binance, Yahoo
# FX, MT5) genuinely has these. OPTIONAL: real for some venues (MT5's
# tick_volume/real_volume), meaningless/absent for others (Yahoo FX's
# public chart endpoint has no bid/ask) — never forced onto a provider
# that doesn't have it, and never fabricated by one that doesn't.
REQUIRED_BAR_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
OPTIONAL_BAR_COLUMNS = ("bid", "ask", "spread", "tick_volume", "real_volume")


class InvalidCanonicalBarsError(ValueError):
    """claude code changed: new — raised when a DataFrame handed to
    CanonicalBars is missing a REQUIRED_BAR_COLUMNS entry. Fails at
    construction time, not the first time some downstream consumer trips
    over a missing column three functions later."""


# claude code changed: new — eq=False (and therefore no generated
# __hash__ either) deliberately: the default dataclass __eq__ would
# compare the `data` DataFrame field with `==`, which raises
# ("truth value of a DataFrame is ambiguous") the instant two instances
# are ever compared or hashed. Identity-based equality (the inherited
# object default) is the correct, safe behavior here — nothing in this
# codebase needs CanonicalBars value-equality.
@dataclass(frozen=True, eq=False)
class CanonicalBars:
    """
    claude code changed: new. The canonical bar contract every
    MarketDataProvider.get_historical_bars() call must return — one shape,
    regardless of which provider produced it. `data` always carries every
    REQUIRED_BAR_COLUMNS; any OPTIONAL_BAR_COLUMNS present reflect real
    provider capability (see MarketDataProvider.capabilities()), never a
    provider-agnostic default or fabricated value — a provider without
    bid/ask simply omits those columns, it never fills them with 0/NaN and
    pretends otherwise.
    """

    data: pd.DataFrame
    instrument: Instrument
    timeframe: str
    identity: DatasetIdentity

    def __post_init__(self) -> None:
        missing = [c for c in REQUIRED_BAR_COLUMNS if c not in self.data.columns]
        if missing:
            raise InvalidCanonicalBarsError(
                f"CanonicalBars for {self.instrument.canonical_symbol!r} is missing "
                f"required column(s) {missing} — every provider must supply all of "
                f"{REQUIRED_BAR_COLUMNS}"
            )

    def fingerprint(self) -> str:
        """claude code changed: new — delegates to the EXISTING
        DatasetIdentity.fingerprint() (bot/research_lab/data_fingerprint.py)
        rather than a second hashing scheme."""
        return self.identity.fingerprint()

    def optional_columns_present(self) -> FrozenSet[str]:
        """claude code changed: new — which OPTIONAL_BAR_COLUMNS this
        particular dataset actually carries, so a caller can tell "the
        provider doesn't support this" apart from "the provider supports
        it but this particular payload happens to lack it"."""
        return frozenset(c for c in OPTIONAL_BAR_COLUMNS if c in self.data.columns)


# claude code changed: new — the closed capability vocabulary section 7 of
# the audit brief asked for. Plain string constants, not an Enum — matches
# this codebase's own existing convention (signal dicts use plain string
# keys/values throughout; ExchangeAdapter's own method list is a fixed set
# of names, not a formal capability registry). Kept to exactly what this
# step's real providers can honestly claim — no speculative
# "order_book"/"streaming" entries for capabilities no data-ACQUISITION
# fetcher in this codebase has ever implemented (order-book normalization
# already exists, but on the EXECUTION side — bot/engines/liquidity.py —
# not here; extend this only once a real acquisition provider needs it).
class DataCapability:
    HISTORICAL_BARS = "historical_bars"
    TICK_VOLUME = "tick_volume"
    REAL_VOLUME = "real_volume"
    BID_ASK = "bid_ask"


class UnsupportedCapabilityError(NotImplementedError):
    """claude code changed: new — raised by MarketDataProvider.require()
    when a caller asks for a capability the provider doesn't declare.
    NotImplementedError, not a bare Exception or a silently-empty result:
    matches this codebase's "fail explicitly rather than fabricate data"
    principle (bot/config/cost_model.py's UnsupportedAssetClassCostModel
    is the same pattern, same reasoning)."""


class MarketDataProvider(abc.ABC):
    """
    claude code changed: new. The venue-agnostic historical-bars
    acquisition contract every provider (a future BinanceKlinesProvider,
    YahooForexProvider, MT5Provider — Steps 3/4) will implement. Nothing
    in the codebase constructs one of these yet; this class exists so it
    CAN be implemented against, and so its contract can be tested in
    isolation before any real fetcher is wrapped in it.
    """

    @property
    @abc.abstractmethod
    def provider_id(self) -> str:
        """Short lowercase provider identifier, e.g. "binance",
        "yahoo_finance", "mt5" — becomes CanonicalBars.identity's venue."""
        raise NotImplementedError

    @abc.abstractmethod
    def capabilities(self) -> FrozenSet[str]:
        """Returns the real, honest set of DataCapability values this
        provider supports. Never a speculative superset — see
        DataCapability's own docstring."""
        raise NotImplementedError

    def supports(self, capability: str) -> bool:
        """claude code changed: new — convenience wrapper, not itself
        abstract (every provider gets this for free from capabilities())."""
        return capability in self.capabilities()

    def require(self, capability: str) -> None:
        """claude code changed: new — the capability-failure boundary
        section 7 of the audit brief asked for: "fail clearly when a
        requested capability is unavailable rather than receiving
        fake/default data." Concrete providers should call this before
        attempting anything they don't unconditionally support."""
        if not self.supports(capability):
            raise UnsupportedCapabilityError(
                f"{self.provider_id!r} does not support {capability!r}"
            )

    @abc.abstractmethod
    def get_historical_bars(
        self,
        instrument: Instrument,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> CanonicalBars:
        """Fetches historical OHLCV (plus whatever OPTIONAL_BAR_COLUMNS
        this provider genuinely supports) for `instrument` between `start`
        and `end`, in `timeframe`'s canonical form (the same strings
        bot.instruments.TIMEFRAME_MINUTES_PER_CANDLE already uses — "1h",
        "4h", "1d", ...). A concrete provider translates `timeframe` into
        its own vocabulary internally (a ccxt interval string, an
        mt5.TIMEFRAME_* constant, ...) — callers of this method never see
        that translation, matching section 10 of the audit brief. Must
        raise UnsupportedCapabilityError (via require()) rather than
        return empty/fabricated data for a combination it cannot honestly
        serve."""
        raise NotImplementedError
