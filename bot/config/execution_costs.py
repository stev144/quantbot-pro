# ============================================================
# bot/config/execution_costs.py
# claude code changed: new file — bot/config package did not exist,
# so bot/engines/market_data.py's "from bot.config.execution_costs
# import CANDLE_CACHE_TTL" raised ImportError at module load time,
# which crashed bot_runner.py before run_bot() could ever execute
# (see architecture audit, finding C1).
# ============================================================

# Seconds a fetched OHLCV candle set is considered fresh before
# MarketData.get_ohlcv() re-fetches from the exchange. Short by design:
# this cache exists to collapse duplicate calls made in quick succession
# within the same loop iteration, not to span the 3600s candle interval
# bot_runner.py itself sleeps for. Override per-instance via
# MarketData(exchange, cache_ttl=...) if a different value is needed.
CANDLE_CACHE_TTL = 5

# claude code changed: new — fixes architecture audit finding M3.
# bot/engines/simulation.py previously defined these as its own local
# constants, disconnected from anything real (they're assumptions used
# only to simulate backtest fills, since there's no real exchange fill
# to read costs from during a backtest). Moved here so backtest cost
# assumptions live next to the live cache config, in one config package,
# instead of scattered across whichever file happens to need a number.

# Simulated slippage — price difference between signal and actual fill
# during backtesting. 0.0005 = 0.05%, a rough estimate for mid-cap crypto.
SLIPPAGE_RATE = 0.0005

# Simulated exchange fee — Binance standard taker fee, charged on both
# entry and exit during backtesting. 0.001 = 0.1%.
FEE_RATE = 0.001

# claude code changed: new — Kraken Multi-Venue Execution, Step 8. Real,
# per-venue cost table. Additive only — FEE_RATE/SLIPPAGE_RATE above are
# UNCHANGED and still consumed directly by bot/engines/simulation.py's
# backtester and by OrderManager's default (no-override) dry-run path.
# This table is what BinanceAdapter/KrakenAdapter.get_execution_costs()
# now read from, instead of each adapter importing/hardcoding its own
# rate — kraken's fee_rate here is the exact same value that used to live
# as a standalone KRAKEN_FEE_RATE constant in kraken_adapter.py, only
# relocated, not changed.
VENUE_EXECUTION_COSTS = {
    "binance": {
        "fee_rate": FEE_RATE,
        "slippage_rate": SLIPPAGE_RATE,
    },
    "kraken": {
        # Kraken's published lowest-tier (<$10k 30-day volume) spot taker
        # fee, per Kraken's public fee schedule — a first-pass, publicly-
        # documented estimate, not fetched from a live account's actual
        # negotiated tier.
        "fee_rate": 0.0026,
        # No Kraken-specific slippage data exists yet — shares the same
        # estimate as Binance until real data justifies differentiating it.
        "slippage_rate": SLIPPAGE_RATE,
    },
}


def get_venue_execution_costs(venue_id: str) -> dict:
    """
    Returns {"fee_rate": float, "slippage_rate": float} for venue_id.
    Falls back to the default (Binance-modeled) global rates for an
    unrecognized venue_id — fails soft to a known-good default rather
    than KeyError, since a missing table entry should degrade gracefully
    rather than crash order/cost-model construction.
    """
    return VENUE_EXECUTION_COSTS.get(
        venue_id, {"fee_rate": FEE_RATE, "slippage_rate": SLIPPAGE_RATE}
    )


# ── Forex cost profile (Workstream C) ─────────────────────────────────────────────────────────────────────
# claude code changed: new — 2026-10-16, Workstream C. Additive only: all existing crypto constants and
# get_venue_execution_costs() are UNCHANGED. A separate, structured forex cost profile that tracks whether
# all material costs are available. cost_complete=False when any of: spread, commission, or swap rates are
# unknown — simulations must surface this label rather than silently understate true cost.

from dataclasses import dataclass  # claude code changed: deferred import to avoid changing module header
from typing import Optional as _Optional  # claude code changed: non-conflicting alias


@dataclass(frozen=True)
class ForexCostProfile:
    """
    Structured forex execution cost profile for DCA research simulation.

    All fields default to None = unavailable. cost_complete property returns
    False when any material cost field is None.

    spread_pips: typical bid/ask spread at market open; broker- and pair-specific.
    commission_per_lot: round-trip commission in account currency; None = no-commission
                        account OR unknown (callers must know which — set 0.0 for confirmed
                        no-commission, not None).
    slippage_pips: expected execution slippage; secondary cost, may be unknown.
    swap_long_pips_per_day: daily rollover charge for long positions.
    swap_short_pips_per_day: daily rollover charge for short positions.
    """  # claude code changed: new — structured cost profile, replaces ad-hoc dict for forex
    instrument_id: str
    spread_pips: _Optional[float] = None
    commission_per_lot: _Optional[float] = None
    slippage_pips: _Optional[float] = None
    swap_long_pips_per_day: _Optional[float] = None
    swap_short_pips_per_day: _Optional[float] = None

    @property
    def cost_complete(self) -> bool:  # claude code changed: False when any material cost is unknown — same gate as dca_engine summarize_dca_cycles
        return (
            self.spread_pips is not None
            and self.commission_per_lot is not None
            and self.swap_long_pips_per_day is not None
            and self.swap_short_pips_per_day is not None
        )


# claude code changed: indicative forex cost profiles — market-agnostic infrastructure audit (2026-10-18).
# Spread values are typical retail figures sourced from publicly published broker pricing pages
# (OANDA/IG/Forex.com typical spreads, not a specific live feed). commission_per_lot=0.0 reflects
# no-commission retail spot accounts; if a commission account is used, update the relevant profile.
# Swap/rollover rates are broker-specific and cannot be reliably estimated without a live account —
# they are intentionally left None so cost_complete remains False until real values are recorded.
# This is not a silent default: cost_complete=False propagates to simulations that check it.
# Additive only; never referenced by existing crypto code paths.
_FOREX_COST_PROFILES: dict = {}  # populated by register_forex_cost_profile()


def _init_indicative_forex_profiles() -> None:
    """Register indicative profiles for the three most-traded FX pairs.
    Swap rates are not populated (None) — they require a specific broker contract.
    cost_complete is therefore False until swap rates are added."""
    for _profile in [
        ForexCostProfile(
            instrument_id="EUR/USD",
            spread_pips=0.8,          # typical retail; range 0.5-1.2 depending on session
            commission_per_lot=0.0,   # no-commission retail spot account assumed
            slippage_pips=0.1,        # conservative estimate; most liquid FX pair
            swap_long_pips_per_day=None,   # broker-specific; None = cost_complete=False
            swap_short_pips_per_day=None,  # broker-specific; None = cost_complete=False
        ),
        ForexCostProfile(
            instrument_id="GBP/USD",
            spread_pips=1.0,
            commission_per_lot=0.0,
            slippage_pips=0.1,
            swap_long_pips_per_day=None,
            swap_short_pips_per_day=None,
        ),
        ForexCostProfile(
            instrument_id="USD/JPY",
            spread_pips=0.7,
            commission_per_lot=0.0,
            slippage_pips=0.1,
            swap_long_pips_per_day=None,
            swap_short_pips_per_day=None,
        ),
        ForexCostProfile(
            instrument_id="GBP/JPY",
            spread_pips=1.5,
            commission_per_lot=0.0,
            slippage_pips=0.2,
            swap_long_pips_per_day=None,
            swap_short_pips_per_day=None,
        ),
        ForexCostProfile(
            instrument_id="EUR/JPY",
            spread_pips=1.0,
            commission_per_lot=0.0,
            slippage_pips=0.1,
            swap_long_pips_per_day=None,
            swap_short_pips_per_day=None,
        ),
    ]:
        _FOREX_COST_PROFILES[_profile.instrument_id] = _profile


_init_indicative_forex_profiles()  # claude code changed: populate at import time


def register_forex_cost_profile(profile: ForexCostProfile) -> None:  # claude code changed: new — registration point for when broker data arrives
    """Register a forex cost profile so get_forex_execution_costs() can find it."""
    _FOREX_COST_PROFILES[profile.instrument_id] = profile


def get_forex_execution_costs(instrument_id: str) -> ForexCostProfile:  # claude code changed: new — Workstream C entry point
    """
    Returns the ForexCostProfile for instrument_id, or a placeholder with all costs=None
    when no real profile has been registered. Never returns None — always a ForexCostProfile,
    always queryable for cost_complete status. A profile with cost_complete=False signals
    that the simulation must not claim full cost accuracy.
    """
    return _FOREX_COST_PROFILES.get(
        instrument_id,
        ForexCostProfile(instrument_id=instrument_id)  # all costs=None → cost_complete=False
    )
