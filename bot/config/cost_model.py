# ============================================================
# bot/config/cost_model.py
# Multi-Asset Foundation Refactor — Phase 1A, STEP 8.
#
# claude code changed: new file. The cost-model BOUNDARY the refactor
# brief's section 13 asked for — CryptoCostModel/EquityCostModel/
# ForexCostModel behind a common interface. This phase implements exactly
# one of those three (crypto), wrapping bot.config.execution_costs's
# existing VENUE_EXECUTION_COSTS/get_venue_execution_costs() UNCHANGED —
# same constants, same values, same behavior for every existing caller.
#
# What this file deliberately does NOT do:
#   - It does not modify bot/config/execution_costs.py. OrderManager,
#     the backtester, and bot/engines/simulation.py keep importing
#     FEE_RATE/SLIPPAGE_RATE/get_venue_execution_costs() directly, exactly
#     as before — this is additive infrastructure, not a rewire of a
#     "major research engine" (a STOP condition this refactor's own rules
#     name explicitly).
#   - It does not invent equity or FX fee/spread/rollover numbers. Asking
#     for a cost model for US_EQUITY or FOREX raises, on purpose — see
#     UnsupportedAssetClassCostModel below. A silent Binance-shaped
#     fallback for an asset class that has never had a real venue is
#     exactly the "fails open, not closed" risk the architecture gate
#     report's risk register flagged; this boundary exists specifically
#     so that mistake has nowhere to hide once a real Research Lab tool
#     eventually adopts it.
# ============================================================

from __future__ import annotations

import abc
from typing import Dict

from bot.config.execution_costs import get_venue_execution_costs
from bot.instruments import ASSET_CLASS_CRYPTO, ASSET_CLASS_FOREX, ASSET_CLASS_US_EQUITY


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


def get_cost_model(asset_class: str, venue_id: str = "binance") -> CostModel:
    """
    claude code changed: new — the factory a future Research Lab tool
    would call instead of importing get_venue_execution_costs() directly,
    once a capability needs to be asset-class-aware about cost. Fails
    CLOSED for any asset class without a real implementation, rather than
    the silent Binance-shaped fallback get_venue_execution_costs() itself
    still has for an unrecognized *venue* (that existing fallback is
    intentionally left as-is — see module docstring — this factory is a
    stricter NEW boundary sitting in front of it, not a change to it).
    """
    if asset_class == ASSET_CLASS_CRYPTO:
        return CryptoCostModel(venue_id)
    if asset_class in (ASSET_CLASS_US_EQUITY, ASSET_CLASS_FOREX):
        raise UnsupportedAssetClassCostModel(
            f"No cost model is implemented for {asset_class} yet — this is a real, "
            f"honest gap, not a value to guess at with crypto-modeled numbers."
        )
    raise UnsupportedAssetClassCostModel(f"'{asset_class}' is not a recognized asset class")
