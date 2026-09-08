# ============================================================
# bot/config/cost_model.py
# Multi-Asset Foundation Refactor — Phase 1A, STEP 8.
#
# claude code changed: new file. The cost-model BOUNDARY the refactor
# brief's section 13 asked for — CryptoCostModel/EquityCostModel/
# ForexCostModel behind a common interface. This phase implements
# crypto, wrapping bot.config.execution_costs's existing
# VENUE_EXECUTION_COSTS/get_venue_execution_costs() UNCHANGED — same
# constants, same values, same behavior for every existing caller.
# Forex Multi-Asset Integration later added ForexCostModel — a real,
# spread-based estimate, not a Binance-shaped guess — once Forex OHLCV
# data actually existed to derive a reference price from. US_EQUITY
# remains unimplemented (raises) — no equity data has ever been
# ingested, so there is nothing real to derive a cost estimate from yet.
#
# What this file deliberately does NOT do:
#   - It does not modify bot/config/execution_costs.py. OrderManager,
#     the backtester, and bot/engines/simulation.py keep importing
#     FEE_RATE/SLIPPAGE_RATE/get_venue_execution_costs() directly, exactly
#     as before — this is additive infrastructure, not a rewire of a
#     "major research engine" (a STOP condition this refactor's own rules
#     name explicitly).
#   - It does not invent equity fee/commission numbers or a session-length
#     model. Asking for a cost model for US_EQUITY still raises, on
#     purpose — see UnsupportedAssetClassCostModel below. A silent
#     Binance-shaped fallback for an asset class that has never had a
#     real venue is exactly the "fails open, not closed" risk the
#     architecture gate report's risk register flagged; this boundary
#     exists specifically so that mistake has nowhere to hide once a real
#     Research Lab tool eventually adopts it.
# ============================================================

from __future__ import annotations

import abc
from typing import Dict

from bot.config.execution_costs import get_venue_execution_costs
from bot.instruments import ASSET_CLASS_CRYPTO, ASSET_CLASS_FOREX, ASSET_CLASS_US_EQUITY


class ForexCostModelDataError(RuntimeError):
    """claude code changed: new — Forex Multi-Asset Integration. Raised by
    ForexCostModel when it cannot compute a real cost estimate for a pair
    because that pair has no OHLCV data ingested yet. Fails closed rather
    than guessing a reference price — see ForexCostModel's own docstring."""


class UnsupportedAssetClassCostModel(NotImplementedError):
    """claude code changed: new — raised by get_cost_model() for any asset
    class with no real cost model yet. NotImplementedError, not a bare
    Exception: this is genuinely "not built yet," not a runtime data
    error, and callers should be able to catch it as exactly that."""


class CostModel(abc.ABC):
    """claude code changed: new. The common interface every asset class's
    cost model will eventually implement. Shaped as a fraction-of-notional
    fee + slippage today because that's the only shape any real
    implementation (crypto) has — CLAUDE.md's own Future-proofing note
    already flags that a per-share/flat commission model (equities) or a
    spread+rollover model (FX) won't fit this shape unchanged; extending
    it is future work for whichever phase actually builds those, not
    speculative work to do now."""

    @abc.abstractmethod
    def get_costs(self) -> Dict[str, float]:
        """Returns {"fee_rate": float, "slippage_rate": float}."""
        raise NotImplementedError


class CryptoCostModel(CostModel):
    """claude code changed: new. Thin wrapper around the existing,
    unchanged get_venue_execution_costs() — never a second cost table.
    Preserves current crypto behavior and every existing constant exactly."""

    def __init__(self, venue_id: str = "binance"):
        self.venue_id = venue_id

    def get_costs(self) -> Dict[str, float]:
        return get_venue_execution_costs(self.venue_id)


class ForexCostModel(CostModel):
    """
    claude code changed: new — Forex Multi-Asset Integration. A real,
    conservative, publicly-documented retail-FX-spread-based cost
    estimate, in the same {"fee_rate", "slippage_rate"} shape
    CryptoCostModel already returns. FX retail cost is dominated by the
    bid-ask spread itself, not a separate exchange fee (unlike crypto's
    maker/taker model) — so fee_rate is always 0.0 here and the whole
    estimate is expressed as slippage_rate, matching how a spread is
    actually paid (crossed once per side of a trade).

    spread_pips defaults to 1.0 pip — a conservative, publicly-documented
    estimate for a standard, no-commission retail account on a major pair
    like EUR/USD (real retail spreads on majors commonly range ~0.6-2
    pips depending on account type). Matches this codebase's own existing
    convention for exactly this kind of number — see
    bot/config/execution_costs.py's Kraken fee comment: "a first-pass,
    publicly-documented estimate, not fetched from a live account's
    actual negotiated tier." NOT a claim that every pair/broker/account
    has this exact spread.

    A pip is a fixed absolute price increment, not already a percentage —
    converting it to a rate needs a real reference price. Rather than
    guess one, this reads the pair's own most recent real close price
    from its actually-ingested OHLCV data (via
    bot.instruments.resolve_ohlcv_path), per this codebase's "never
    invent data" principle. Raises ForexCostModelDataError if that pair
    has no data ingested yet, rather than guessing a price level.
    """

    _JPY_QUOTE_PIP_SIZE = 0.01
    _DEFAULT_PIP_SIZE = 0.0001

    def __init__(self, pair: str, spread_pips: float = 1.0):
        self.pair = pair
        self.spread_pips = spread_pips

    def _pip_size(self) -> float:
        return self._JPY_QUOTE_PIP_SIZE if self.pair.endswith("/JPY") else self._DEFAULT_PIP_SIZE

    def _reference_price(self) -> float:
        from bot.instruments import UnknownInstrumentError, resolve_ohlcv_path
        try:
            path = resolve_ohlcv_path(self.pair)
        except UnknownInstrumentError as e:
            raise ForexCostModelDataError(f"cannot compute a real FX cost estimate for '{self.pair}': {e}") from e
        if not path.exists():
            raise ForexCostModelDataError(
                f"cannot compute a real FX cost estimate for '{self.pair}': no OHLCV data has been "
                f"ingested yet at {path} — run bot/forex_data_fetcher.py first"
            )
        import pandas as pd
        close = pd.read_csv(path, usecols=["close"])["close"]
        if close.empty:
            raise ForexCostModelDataError(f"'{self.pair}' OHLCV file at {path} is empty")
        return float(close.iloc[-1])

    def get_costs(self) -> Dict[str, float]:
        price = self._reference_price()
        return {"fee_rate": 0.0, "slippage_rate": (self.spread_pips * self._pip_size()) / price}


def get_cost_model(asset_class: str, venue_id: str = "binance", symbol: str = None) -> CostModel:
    """
    claude code changed: the factory a future Research Lab tool would
    call instead of importing get_venue_execution_costs() directly, once
    a capability needs to be asset-class-aware about cost. Fails CLOSED
    for any asset class without a real implementation, rather than the
    silent Binance-shaped fallback get_venue_execution_costs() itself
    still has for an unrecognized *venue* (that existing fallback is
    intentionally left as-is — see module docstring — this factory is a
    stricter NEW boundary sitting in front of it, not a change to it).

    `symbol` is new, additive, and CRYPTO-irrelevant (ignored) — FOREX's
    cost model needs the actual traded pair (to look up a real reference
    price), not a venue_id, so it's a required keyword for
    asset_class=FOREX rather than overloading venue_id's meaning.
    """
    if asset_class == ASSET_CLASS_CRYPTO:
        return CryptoCostModel(venue_id)
    if asset_class == ASSET_CLASS_FOREX:
        if not symbol:
            raise ValueError("get_cost_model(asset_class=FOREX) requires a `symbol` (e.g. 'EUR/USD') to look up a real reference price")
        return ForexCostModel(pair=symbol)
    if asset_class == ASSET_CLASS_US_EQUITY:
        raise UnsupportedAssetClassCostModel(
            f"No cost model is implemented for {asset_class} yet — this is a real, "
            f"honest gap, not a value to guess at with crypto-modeled numbers."
        )
    raise UnsupportedAssetClassCostModel(f"'{asset_class}' is not a recognized asset class")
