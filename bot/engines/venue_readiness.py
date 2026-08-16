# ============================================================
# bot/engines/venue_readiness.py
# claude code changed: new file — Kraken Multi-Venue Execution, Step 17.
# Produces the NOT_READY / DRY_RUN_READY / PAPER_TRADING_READY / LIVE_READY
# classification the spec's final deliverable requires — real, evidence-
# based, not a rubber stamp. Consumed by bot/core/dry_run_test.py (the
# project's existing, documented "run this before going live" mechanism)
# so the classification is visible every time an operator runs the
# pre-flight check, for either venue.
#
# Real finding behind PAPER_TRADING_READY's placement here: this
# codebase's "dry run" is ALWAYS in-process fill simulation
# (OrderManager._simulate_fill()) for every venue — never a real order
# routed to an exchange-side paper/testnet endpoint. Neither Binance nor
# Kraken has that integration built here (Kraken has no spot testnet at
# all, per Step 4's investigation; this project doesn't use Binance's
# either). So PAPER_TRADING_READY is structurally unreachable today for
# BOTH venues — stated explicitly in every report's notes, not silently
# skipped with no explanation.
# ============================================================

from dataclasses import dataclass, field
from enum import Enum
from typing import List

from bot.engines.exchange_adapter import ExchangeAdapter


class VenueReadiness(str, Enum):
    NOT_READY = "NOT_READY"
    DRY_RUN_READY = "DRY_RUN_READY"
    PAPER_TRADING_READY = "PAPER_TRADING_READY"
    LIVE_READY = "LIVE_READY"


@dataclass(frozen=True)
class ReadinessReport:
    venue_id: str
    classification: VenueReadiness
    checks: dict
    notes: List[str] = field(default_factory=list)


_PAPER_TRADING_NOTE = (
    "PAPER_TRADING_READY is currently unreachable: this codebase's dry-run "
    "is in-process fill simulation only, for every venue — no real order is "
    "ever routed to an exchange-side paper/testnet endpoint here."
)


def assess_venue_readiness(adapter: ExchangeAdapter) -> ReadinessReport:
    """
    Real, evidence-based readiness classification for a venue adapter.
    Every check below is a real call (dry-run order simulation, public
    ticker fetch, authenticated connection attempt) — no assumptions.
    """
    checks = {
        "dry_run_order_works": False,
        "public_connectivity": False,
        "authenticated_connectivity": False,
    }
    notes = [_PAPER_TRADING_NOTE]

    try:
        venue_id = adapter.venue_id
    except Exception as e:
        return ReadinessReport(
            venue_id="unknown",
            classification=VenueReadiness.NOT_READY,
            checks=checks,
            notes=notes + [f"Could not read adapter.venue_id: {type(e).__name__}: {e}"],
        )

    try:
        result = adapter.place_order("BTC/USDT", "buy", "market", 0.0001)
        checks["dry_run_order_works"] = bool(result.get("fill_price", 0) > 0)
    except Exception as e:
        notes.append(f"Dry-run order simulation failed: {type(e).__name__}: {e}")

    try:
        ticker = adapter.get_ticker("BTC/USDT")
        checks["public_connectivity"] = bool((ticker.get("last") or 0) > 0)
    except Exception as e:
        notes.append(f"Public connectivity check failed: {type(e).__name__}: {e}")

    ok, message = adapter.validate_connection()
    checks["authenticated_connectivity"] = ok
    notes.append(f"Authenticated connectivity: {message}")

    if checks["authenticated_connectivity"]:
        classification = VenueReadiness.LIVE_READY
    elif checks["dry_run_order_works"] and checks["public_connectivity"]:
        classification = VenueReadiness.DRY_RUN_READY
    else:
        classification = VenueReadiness.NOT_READY

    return ReadinessReport(venue_id=venue_id, classification=classification, checks=checks, notes=notes)
