# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/forex_universe.py
# Workstream E — Forex universe validation for DCA research
#
# claude code changed: new file — 2026-10-16. Reuses bot.quant_core.market_profile.FX_UNIVERSE (OD-FX-7,
# owner-delegated 2026-10-06) and bot.instruments.INSTRUMENT_REGISTRY. Does NOT invent a new universe
# definition or apply Binance listing/USDT-volume rules to forex.
#
# build_forex_universe() validates a proposed pair list against:
#   1. FX_UNIVERSE membership (the owner's 51-pair floating universe, OD-FX-7)
#   2. bot.instruments.INSTRUMENT_REGISTRY registration (symbol known)
#   3. Data availability (CSV exists on disk)
#
# Returns only pairs that pass all three checks. Rejected pairs are documented with reasons.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from bot.instruments import ASSET_CLASS_FOREX, get_instrument, resolve_ohlcv_path, UnknownInstrumentError  # claude code changed: reuse single-source instrument registry
from bot.quant_core.market_profile import FX_UNIVERSE, in_universe, FOREX_PROFILE  # claude code changed: reuse OD-FX-7 universe and calendar


def build_forex_universe(
    pairs: List[str],
    require_data: bool = True,
) -> Tuple[List[str], Dict[str, str]]:
    """
    Validate a list of forex pair symbols against the owner's FX universe and data availability.

    Returns:
        (accepted, rejected)
        accepted: list of pairs that passed all checks, in the input order
        rejected: dict mapping rejected pair symbol -> reason string

    Checks applied (in order):
        1. FX_UNIVERSE membership (OD-FX-7, owner-delegated): the owner's 51-pair floating universe
        2. Instrument registry: symbol is registered as FOREX in bot.instruments.INSTRUMENT_REGISTRY
        3. Data availability (only when require_data=True): OHLCV CSV exists on disk

    Does NOT apply:
        - Binance listing rules
        - USDT-volume floors
        - Trailing-volume filters
        - Any look-ahead survivorship restriction

    require_data=False is useful for unit tests where no real CSV files exist.
    """  # claude code changed: new — Workstream E; reuses FX_UNIVERSE (OD-FX-7), no Binance rules
    accepted: List[str] = []
    rejected: Dict[str, str] = {}

    for pair in pairs:
        # Normalize symbol (accept both "EUR/USD" and "EUR_USD" forms)
        canon = pair if "/" in pair else pair.replace("_", "/", 1)

        # Check 1 — FX_UNIVERSE membership (OD-FX-7)
        if not in_universe(FOREX_PROFILE, canon):
            rejected[pair] = f"not in FX_UNIVERSE (OD-FX-7): {canon!r}"
            continue

        # Check 2 — instrument registry
        instrument = get_instrument(canon)
        if instrument is None:
            rejected[pair] = f"not in INSTRUMENT_REGISTRY: {canon!r}"
            continue
        if instrument.asset_class != ASSET_CLASS_FOREX:
            rejected[pair] = f"registered as {instrument.asset_class}, not FOREX: {canon!r}"
            continue

        # Check 3 — data availability (optional)
        if require_data:
            try:
                path = resolve_ohlcv_path(canon)
            except UnknownInstrumentError as e:
                rejected[pair] = f"cannot resolve data path: {e}"
                continue
            if not path.exists():
                rejected[pair] = f"no OHLCV data on disk at {path} — run forex_data_fetcher or mt5_data_fetcher first"
                continue

        accepted.append(canon)

    return accepted, rejected


def build_dca_target_universe(require_data: bool = True) -> Tuple[List[str], Dict[str, str]]:
    """
    Validate the five DCA research target pairs against the forex universe.
    Returns (accepted, rejected) — a pair may be rejected if its data hasn't been fetched yet.
    """  # claude code changed: new — convenience wrapper for the five intended DCA research pairs
    from bot.research.forex_instrument_spec import PLACEHOLDER_SPECS
    return build_forex_universe(list(PLACEHOLDER_SPECS.keys()), require_data=require_data)
