# ============================================================
# risk/drawdown_guard.py
# claude code changed: new — portfolio-level drawdown kill switch.
#
# This answers a different question than PositionSizer:
# PositionSizer limits how much ONE trade can lose.
# DrawdownGuard limits how much the WHOLE ACCOUNT can lose before new
# risk-taking stops entirely — the two are complementary, not
# overlapping (a strategy sized correctly per-trade can still ruin an
# account over many trades if its edge quietly breaks).
#
# Scope, stated plainly:
# - Tracks peak observed balance and blocks NEW entries once drawdown
#   from that peak breaches bot.config.risk.MAX_DRAWDOWN_PCT.
# - Does NOT touch exit logic — ExecutionEngine.manage_positions() is
#   untouched, so stop-loss/take-profit on already-open positions keep
#   firing even while this guard is tripped. A kill switch that also
#   blocked exits would trap the account in a bad position, which is
#   the opposite of what a kill switch is for.
# - Auto-clears once drawdown recovers back below the threshold,
#   rather than requiring a manual operator reset. Chosen because this
#   project has no paging/on-call infrastructure (see CLAUDE.md) — a
#   switch that stays tripped forever until someone remembers to find
#   and reset it is a worse failure mode than one that re-arms safely
#   once the account has genuinely recovered.
# - State is in-memory only, scoped to one ExecutionEngine instance —
#   it does NOT persist across bot restarts. That is a real, known gap
#   (a restart mid-drawdown re-seeds the peak to the current, already
#   depressed balance, silently re-arming trading right when the
#   switch should still be blocking it). Not fixed here: this project
#   has never completed a live trade (DRY_RUN defaults True, TradeRecord
#   has zero rows), so there is no real balance history to persist yet.
#   Revisit once live trading actually begins — see CLAUDE.md's Phase 2
#   deferral note for OrderManager failure history / PositionSizer
#   calculator, same reasoning applies here.
# - Uses MarketData.get_balance() — FREE USDT balance, the same value
#   PositionSizer already sizes trades against, not total account
#   equity. This is accurate for the bot's current design (bot_runner.py
#   trades exactly one SYMBOL, so at most one position is ever open at
#   a time — free balance IS the account's resting state between
#   trades). If the bot is ever extended to hold multiple concurrent
#   positions across symbols, this must switch to mark-to-market total
#   equity (free balance + value of all open positions) — otherwise
#   capital merely LOCKED in open positions would look like a loss and
#   could trip the switch with zero real drawdown.
#
# - claude code changed: new — Kraken Multi-Venue Execution, Step 12.
#   Multi-venue note: ExecutionEngine now optionally takes an `adapter`
#   (Step 10), and ExecutionCoordinator (also Step 10) can hold one
#   ExecutionEngine per venue, each with its OWN DrawdownGuard instance.
#   This is exactly the correct design, not a gap: each guard only ever
#   reads its own venue's MarketData.get_balance(), so a position open
#   on Kraken has zero effect on Binance's guard math and vice versa —
#   the free-balance approximation above stays valid independently PER
#   VENUE, as long as that individual venue still holds at most one
#   position at a time (still true today; verified directly in
#   bot/tests/test_risk_controls_inheritance.py, which also confirms two
#   engines never accidentally share a DrawdownGuard instance). There is
#   deliberately no aggregate/portfolio-wide drawdown view across venues
#   — each venue is a separate real balance/account, so per-venue limits
#   are the economically correct ones, not a missing feature.
# ============================================================

import logging

from bot.config.risk import MAX_DRAWDOWN_PCT

logger = logging.getLogger(__name__)


class DrawdownGuard:
    """
    Portfolio-level kill switch. Call update(current_balance) before
    every entry decision; check its return value (or .tripped) to
    decide whether new entries are allowed.
    """

    def __init__(self, max_drawdown_pct=MAX_DRAWDOWN_PCT):
        self.max_drawdown_pct = max_drawdown_pct
        self.peak_balance = 0.0
        self.tripped = False

        logger.info(
            f"[DrawdownGuard] Initialised | "
            f"Max drawdown before new entries blocked: "
            f"{self.max_drawdown_pct * 100:.1f}%"
        )

    def current_drawdown_pct(self, current_balance) -> float:
        """
        Returns current drawdown from peak as a fraction (0.10 = 10%).
        Returns 0.0 if no peak has been observed yet.
        """
        if self.peak_balance <= 0:
            return 0.0

        return max(0.0, (self.peak_balance - current_balance) / self.peak_balance)

    def update(self, current_balance) -> bool:
        """
        Updates the peak balance watermark, recalculates drawdown, and
        flips self.tripped accordingly. Returns True if new entries are
        currently blocked (drawdown >= threshold), False otherwise.

        current_balance: latest free USDT balance from MarketData.get_balance()
        """

        # Guard: a bad (zero/negative) balance reading must not corrupt
        # the peak watermark or be treated as a real drawdown — a failed
        # fetch already returns 0.0 from MarketData.get_balance(), and
        # ExecutionEngine.execute_signal() already aborts the trade on
        # that case before this is ever called with a bad value. This
        # check exists so DrawdownGuard is safe to call directly too.
        if current_balance is None or current_balance <= 0:
            return self.tripped

        if current_balance > self.peak_balance:
            self.peak_balance = current_balance

        drawdown_pct = self.current_drawdown_pct(current_balance)

        was_tripped = self.tripped
        self.tripped = drawdown_pct >= self.max_drawdown_pct

        if self.tripped and not was_tripped:
            logger.critical(
                f"[DrawdownGuard] KILL SWITCH TRIPPED — drawdown "
                f"{drawdown_pct * 100:.2f}% >= {self.max_drawdown_pct * 100:.1f}% limit "
                f"(peak: ${self.peak_balance:.2f}, current: ${current_balance:.2f}). "
                f"New entries blocked. Existing open positions are unaffected "
                f"and will still close normally on stop-loss/take-profit."
            )
        elif was_tripped and not self.tripped:
            logger.info(
                f"[DrawdownGuard] Kill switch CLEARED — drawdown recovered to "
                f"{drawdown_pct * 100:.2f}% (below {self.max_drawdown_pct * 100:.1f}% "
                f"limit). New entries re-enabled."
            )

        return self.tripped
