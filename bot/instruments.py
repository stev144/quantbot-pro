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
from typing import Dict, List, Optional

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
