# ============================================================
# bot/instruments.py
# Multi-Asset Foundation Refactor — Phase 1A, STEP 1.
#
# claude code changed: new file. The single instrument-identity boundary
# the Multi-Asset Research Architecture Gate report called for: one place
# that knows what an instrument IS (asset_class, canonical symbol,
# currency/venue/timeframe metadata) and one place that knows how a
# canonical symbol maps to today's single data source's on-disk file.
#
# What this deliberately is NOT:
#   - A data provider. It wraps bot/fetch_all_symbols.py's existing
#     SYMBOLS/INTERVAL/symbol_to_filename() — it does not fetch, does not
#     touch the network, and does not rename a single existing CSV file
#     (per the refactor brief's explicit instruction).
#   - A second universe list. Every crypto instrument here is DERIVED from
#     fetch_all_symbols.SYMBOLS, not re-typed — the two can never drift
#     apart the way cointegration_engine.py's/cross_section_engine.py's/
#     contagion_engine.py's independent UNIVERSE copies already have
#     (see the architecture gate report, section 2). Those three engines'
#     own module-level defaults are intentionally left untouched this
#     phase — they're standalone runners outside the Research Lab's tool
#     layer, and the Research Lab's own run_cointegration_test tool
#     already bypasses CointegrationEngine's UNIVERSE default entirely
#     (it loads both legs' prices itself and calls _test_pair directly).
#   - A schema change to any stored data. INSTRUMENT_REGISTRY is built at
#     import time from constants already in the repo; nothing here is
#     persisted, so this file introduces zero migrations.
#
# Only CRYPTO is populated today — US_EQUITY/FOREX asset classes exist as
# real, valid enum values (so calling code can already branch on them
# correctly) but INSTRUMENT_REGISTRY has no US_EQUITY/FOREX rows, because
# no such data has ever been ingested. Pretending otherwise would violate
# the Research Lab's own "never invent data" principle at the instrument
# layer instead of the feature layer.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bot.fetch_all_symbols import INTERVAL, SYMBOLS, symbol_to_filename

ASSET_CLASS_CRYPTO = "CRYPTO"
ASSET_CLASS_US_EQUITY = "US_EQUITY"
ASSET_CLASS_FOREX = "FOREX"
ASSET_CLASSES = [ASSET_CLASS_CRYPTO, ASSET_CLASS_US_EQUITY, ASSET_CLASS_FOREX]

DATA_DIR = "data"


@dataclass(frozen=True)
class Instrument:
    """
    One instrument's identity. Every field beyond canonical_symbol/
    asset_class is Optional on purpose — section 3 of the refactor brief:
    "the abstraction must NOT force irrelevant fields onto every asset."
    A US_EQUITY instrument has no base/quote currency pair; it simply
    leaves those None rather than pretending to a crypto-style
    relationship it doesn't have.
    """

    canonical_symbol: str          # the ONE internal representation, e.g. "BTC/USDT", "AAPL", "EUR/USD"
    asset_class: str               # one of ASSET_CLASSES
    base_currency: Optional[str] = None    # "BTC", "EUR" — None for equities
    quote_currency: Optional[str] = None   # "USDT", "USD" — None for equities
    venue: Optional[str] = None            # data source/venue this instrument's OHLCV actually comes from, e.g. "binance"
    timeframe: Optional[str] = None        # native candle resolution this instrument's dataset is stored at, e.g. "1h"
    data_source: Optional[str] = None      # informational: which fetch pipeline populated this instrument's data


def _build_crypto_registry() -> Dict[str, Instrument]:
    """
    claude code changed: new. Derives every CRYPTO instrument from
    fetch_all_symbols.SYMBOLS — the real, currently-populated universe —
    rather than re-declaring it. base_currency/quote_currency are parsed
    from the canonical "BASE/QUOTE" form every symbol in SYMBOLS already
    uses (verified: all 20 are "*/USDT").
    """
    registry: Dict[str, Instrument] = {}
    for symbol in SYMBOLS:
        base, _, quote = symbol.partition("/")
        registry[symbol] = Instrument(
            canonical_symbol=symbol,
            asset_class=ASSET_CLASS_CRYPTO,
            base_currency=base or None,
            quote_currency=quote or None,
            venue="binance",
            timeframe=INTERVAL,
            data_source="fetch_all_symbols",
        )
    return registry


# claude code changed: new — the single instrument registry. Only CRYPTO
# rows exist today; US_EQUITY/FOREX are real, valid asset_class values
# with zero rows until a real data source for them is actually built
# (deliberately out of scope for this phase — see module docstring).
INSTRUMENT_REGISTRY: Dict[str, Instrument] = _build_crypto_registry()


def get_instrument(canonical_symbol: str) -> Optional[Instrument]:
    return INSTRUMENT_REGISTRY.get(canonical_symbol)


def list_instruments(asset_class: Optional[str] = None) -> List[Instrument]:
    instruments = list(INSTRUMENT_REGISTRY.values())
    if asset_class is not None:
        instruments = [i for i in instruments if i.asset_class == asset_class]
    return instruments


def symbols_for_asset_class(asset_class: str) -> List[str]:
    """claude code changed: new — replaces spec.py's former direct
    `list(SYMBOLS)` with a lookup that will mean something different once
    a second asset class actually has rows, without any caller needing to
    change how it asks the question."""
    return [i.canonical_symbol for i in list_instruments(asset_class)]


class UnknownInstrumentError(ValueError):
    """claude code changed: new — raised only when resolve_ohlcv_path() is
    asked to resolve a symbol with no registry entry at all. In normal
    operation this should be unreachable: every caller in the Research Lab
    only ever reaches this after ResearchSpec.validate_spec() has already
    confirmed the symbol is in the supported universe. Fail loud rather
    than silently guessing a filename for an instrument this platform has
    no registered identity for."""


def resolve_ohlcv_path(canonical_symbol: str) -> Path:
    """
    claude code changed: new. THE single provider/file-representation
    boundary — the exact seam section 4 of the refactor brief asked for.
    Before this function existed, bot/research_lab/data_availability.py's
    _ohlcv_path() and bot/research_lab/tools/_data.py's load_ohlcv() each
    independently called fetch_all_symbols.symbol_to_filename() themselves
    — two copies of the same provider-representation knowledge that could
    drift. Both now call this instead.

    Deliberately does NOT rename a single existing data/*.csv file — for
    CRYPTO this still resolves to exactly the same "BTC_USDT_1h.csv" path
    as before, via the same symbol_to_filename() function, unchanged.
    """
    instrument = get_instrument(canonical_symbol)
    if instrument is None:
        raise UnknownInstrumentError(
            f"'{canonical_symbol}' has no registered instrument identity — cannot resolve a data path for it"
        )
    if instrument.asset_class != ASSET_CLASS_CRYPTO:
        # claude code changed: new — fail-closed, not a guess. No non-CRYPTO
        # instrument has ever had a data source in this codebase; a
        # confident guessed path would violate the Research Lab's own
        # "never invent data" principle just as surely as inventing a
        # feature value would.
        raise UnknownInstrumentError(
            f"'{canonical_symbol}' is asset_class={instrument.asset_class}, which has no data source configured yet"
        )
    return Path(DATA_DIR) / symbol_to_filename(canonical_symbol)


# ============================================================
# Multi-Asset Foundation Refactor — Phase 1B, Objective 1/2.
#
# claude code changed: new. The timeframe/candle-count <-> wall-clock-time
# conversion boundary. Lives here, not in cointegration_engine.py or
# feature_calculator.py, for the same reason resolve_ohlcv_path() lives
# here: it is instrument/timeframe knowledge, and every research engine
# that needs "how much physical time does N candles represent" should
# import ONE answer to that question rather than each engine inventing
# its own hours-per-candle constant (which is exactly how
# cointegration_engine.py's half-life ended up silently mislabeled
# "hours" for any candle width — the value was always correct in
# CANDLES, only the wall-clock conversion was missing).
# ============================================================

# claude code changed: new — minutes represented by one candle at each
# timeframe. The one place this conversion factor is defined; extend this
# dict, never hardcode a second copy of it in a research engine.
TIMEFRAME_MINUTES_PER_CANDLE: Dict[str, float] = {
    "1m": 1.0, "5m": 5.0, "15m": 15.0, "30m": 30.0,
    "1h": 60.0, "4h": 240.0, "1d": 1440.0,
}

# claude code changed: new — which unit a timeframe's own wall-clock
# durations should be reported in. Not auto-scaled from the magnitude of
# any particular result (a 48-hour half-life on 4h candles is reported as
# "48 hours," not "2 days") — the unit follows the TIMEFRAME's natural
# resolution, which is what a researcher reading "half-life: 48" actually
# expects units for.
TIMEFRAME_NATIVE_UNIT: Dict[str, str] = {
    "1m": "minutes", "5m": "minutes", "15m": "minutes", "30m": "minutes",
    "1h": "hours", "4h": "hours", "1d": "days",
}

_MINUTES_PER_UNIT = {"minutes": 1.0, "hours": 60.0, "days": 1440.0}


class UnsupportedTimeframeError(ValueError):
    """claude code changed: new — raised by candles_to_wall_clock() for a
    timeframe string with no known candle width. Fail loud rather than
    silently assuming 1h, the exact bug this function exists to remove."""


def candles_to_wall_clock(n_candles: float, timeframe: str) -> Tuple[float, str]:
    """
    claude code changed: new. The single conversion point from "N candles"
    to "how much physical time is that," honest about which unit it's
    reporting in. Examples (from the Phase 1B brief itself):

        candles_to_wall_clock(12, "1h") -> (12.0, "hours")
        candles_to_wall_clock(12, "4h") -> (48.0, "hours")
        candles_to_wall_clock(12, "1d") -> (12.0, "days")

    Raises UnsupportedTimeframeError for any timeframe not in
    TIMEFRAME_MINUTES_PER_CANDLE — never guesses a conversion factor.
    """
    if timeframe not in TIMEFRAME_MINUTES_PER_CANDLE:
        raise UnsupportedTimeframeError(
            f"'{timeframe}' has no known candle width — cannot convert {n_candles} candles to wall-clock time"
        )
    unit = TIMEFRAME_NATIVE_UNIT[timeframe]
    total_minutes = n_candles * TIMEFRAME_MINUTES_PER_CANDLE[timeframe]
    return total_minutes / _MINUTES_PER_UNIT[unit], unit


# ============================================================
# Multi-Asset Foundation Refactor — Phase 1B, Objective 3.
#
# claude code changed: new. The annualization-factor boundary —
# "how many `timeframe`-candles occur in one year, under `asset_class`'s
# trading-calendar convention." feature_calculator.py's realized_vol used
# a hardcoded sqrt(252) (the US-equity daily-bar convention) regardless of
# timeframe OR asset class — wrong even for this platform's own crypto/1h
# data (crypto trades 24/7/365, and 1h candles need scaling by 24, not by
# nothing). Lives here rather than in feature_calculator.py for the same
# reason every other timeframe/instrument fact does: one answer, not one
# per research engine that happens to need it.
# ============================================================

# claude code changed: new — trading days/year per asset class's market
# structure. CRYPTO is a real, uncontroversial fact (24/7/365 market).
# US_EQUITY (252) is the standard NYSE/NASDAQ trading-day convention.
# FOREX (260 = 5 days/week x 52 weeks) is a conventional approximation,
# not a real session calendar this platform has ever modeled — flagged
# explicitly rather than presented as equally solid as the other two.
TRADING_DAYS_PER_YEAR: Dict[str, float] = {
    ASSET_CLASS_CRYPTO: 365.0,
    ASSET_CLASS_US_EQUITY: 252.0,
    ASSET_CLASS_FOREX: 260.0,  # convention, not a modeled session calendar — see note above
}


class UnsupportedAnnualizationError(ValueError):
    """claude code changed: new — raised by periods_per_year() when the
    (timeframe, asset_class) pair can't be honestly annualized with what
    this platform currently knows. This is expected and correct for any
    intraday US_EQUITY/FOREX timeframe today: annualizing sub-daily bars
    requires knowing the market's real session length (e.g. ~6.5h/day for
    US equities), which this platform has no session-calendar model for
    yet — and since no US_EQUITY/FOREX data has ever been ingested,
    nothing currently reachable actually hits this path."""


def periods_per_year(timeframe: str, asset_class: str) -> float:
    """
    claude code changed: new. How many `timeframe`-candles occur in one
    year under `asset_class`'s trading-calendar convention — the single
    correct replacement for a hardcoded annualization constant. Matches
    the Phase 1B brief's own worked examples exactly:

        periods_per_year("1d", ASSET_CLASS_US_EQUITY) -> 252.0
        periods_per_year("1d", ASSET_CLASS_CRYPTO)    -> 365.0
        periods_per_year("1h", ASSET_CLASS_CRYPTO)    -> 8760.0  (24 x 365)

    Raises UnsupportedTimeframeError / UnsupportedAnnualizationError
    rather than guessing — see UnsupportedAnnualizationError's own
    docstring for exactly which cases fail closed and why.
    """
    if timeframe not in TIMEFRAME_MINUTES_PER_CANDLE:
        raise UnsupportedTimeframeError(f"'{timeframe}' has no known candle width")
    if asset_class not in TRADING_DAYS_PER_YEAR:
        raise UnsupportedAnnualizationError(f"no trading-calendar convention defined for asset_class='{asset_class}'")

    if timeframe == "1d":
        # claude code changed: one candle IS one trading day — no session-
        # length ambiguity regardless of asset class.
        return TRADING_DAYS_PER_YEAR[asset_class]

    if asset_class == ASSET_CLASS_CRYPTO:
        # claude code changed: crypto trades every minute of every
        # calendar day — candles/day is a pure function of candle width,
        # no session-hours model needed.
        candles_per_day = 1440.0 / TIMEFRAME_MINUTES_PER_CANDLE[timeframe]
        return TRADING_DAYS_PER_YEAR[asset_class] * candles_per_day

    # claude code changed: US_EQUITY/FOREX at an intraday timeframe needs
    # a real market-session-length model (equities don't trade 24h/day)
    # this platform doesn't have — fail closed, never guess a session
    # length. Unreachable with real data today (no non-CRYPTO data
    # exists), but the correct behavior the day it does.
    raise UnsupportedAnnualizationError(
        f"intraday annualization for asset_class='{asset_class}' at timeframe='{timeframe}' "
        f"requires a market-session-length model this platform does not have yet"
    )
