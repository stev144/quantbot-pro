# ============================================================
# bot/engines/execution_coordinator.py
# claude code changed: new file — Kraken Multi-Venue Execution, Step 10.
# Owns one ExecutionEngine per enabled venue and routes execute_signal()
# calls to the correct one by signal["venue_id"] (optional — falls back
# to default_venue_id). Deliberately does NOT create a second risk
# engine or duplicate strategy logic: each venue keeps its own
# ExecutionEngine's existing PositionSizer/DrawdownGuard/TradeLogger —
# the only correct design given venues have separate balances/positions
# that can't share fixed-fractional sizing math.
#
# NOT wired into bot/core/bot_runner.py's main loop by this step — that
# loop is single-symbol/single-venue by design today (one exchange, one
# OHLCV fetch, one regime/signal per iteration); routing the LIVE loop
# across venues needs loop-level redesign (per-venue candle fetching,
# per-symbol venue assignment) that's a bigger, separate architectural
# decision than this step, and would risk pre-empting later steps (no
# arbitrage yet; dry-run/paper before live credentials). This class is
# built and fully tested as real, usable capability, not activated in
# the live path yet.
# ============================================================

import logging
from typing import Dict, Optional

from bot.engines.execution_engine import ExecutionEngine

logger = logging.getLogger(__name__)


class ExecutionCoordinator:
    """
    Routes signals to the correct venue's ExecutionEngine.

    engines: {venue_id: ExecutionEngine} — every venue this coordinator
    knows about, each with its own fully-independent risk/execution stack.
    default_venue_id: which engine handles a signal with no explicit
    signal["venue_id"] — every signal in this codebase today has no such
    key (StrategyRouter doesn't set one), so this is where 100% of
    existing signals route unless a caller opts in explicitly.
    """

    def __init__(self, engines: Dict[str, ExecutionEngine], default_venue_id: str):
        if default_venue_id not in engines:
            raise ValueError(
                f"default_venue_id {default_venue_id!r} is not in engines "
                f"{list(engines.keys())}"
            )
        self.engines = engines
        self.default_venue_id = default_venue_id

    def execute_signal(self, signal: dict) -> None:
        """
        Routes to signal.get("venue_id", self.default_venue_id). An
        unrecognized venue_id logs an error and touches no engine — fails
        closed rather than silently falling back to a different venue's
        risk stack for a signal that named one explicitly.
        """
        venue_id = signal.get("venue_id", self.default_venue_id)
        engine = self.engines.get(venue_id)

        if engine is None:
            logger.error(
                f"[ExecutionCoordinator] Unknown venue_id {venue_id!r} for "
                f"{signal.get('symbol', 'UNKNOWN')} — signal ignored. "
                f"Known venues: {list(self.engines.keys())}"
            )
            return

        engine.execute_signal(signal)

    def manage_positions(self) -> None:
        """
        Calls manage_positions() on EVERY registered venue's engine —
        positions can be open concurrently across venues regardless of
        which venue triggered this call, so every venue's own open
        positions need checking every time.
        """
        for engine in self.engines.values():
            engine.manage_positions()

    def get_engine(self, venue_id: str) -> Optional[ExecutionEngine]:
        return self.engines.get(venue_id)
