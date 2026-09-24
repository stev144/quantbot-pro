# ============================================================
# bot/pairs/signal_engine.py
# claude code changed: new file — live pairs-trading pilot, Step 1.
#
# Ties together bot/pairs/kalman_online.py (online hedge-ratio/spread/
# z-score tracking) with bot/research/entry_exit_engine.py's EXISTING,
# validated entry/exit rule methods (_check_entry_conditions,
# _check_exit_conditions) and its KalmanPositionSizer — reused directly,
# not reimplemented, per this project's "inspect before duplicating"
# convention. Adds the one piece that exists nowhere in committed code:
# the 1-hour decision/execution lag (see PairSignalEngine.process_candle
# docstring).
#
# This module produces trading DECISIONS only (ENTER/HOLD/EXIT + sizing).
# It places no orders and touches no exchange — that's bot/pairs/execution.py
# (not yet built; see the approved plan at
# C:\Users\HP\.claude\plans\dazzling-zooming-origami.md).
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from bot.pairs.config import PairConfig, MIN_POSITION_USDT_OVERRIDE, DISABLE_TARGET_EXIT, EXIT_TIME_STOP_HOURS, KELLY_SAFETY_FRACTION
from bot.pairs.kalman_online import kalman_step, RollingZScore, NativeStepResult
from bot.research.kalman_filter_engine import KalmanState, ZSCORE_WINSOR_LIMIT
from bot.research.entry_exit_engine import EntryExitEngine, TradeRecord as ResearchTradeRecord


@dataclass
class SignalDecision:
    action: str  # "NONE" | "ENTER" | "HOLD" | "EXIT"
    reason: str = ""
    trade: Optional[ResearchTradeRecord] = None
    native: Optional[NativeStepResult] = None
    direction: Optional[str] = None
    sized_usdt: Optional[float] = None


class PairSignalEngine:
    """Live, per-candle equivalent of EntryExitEngine._scan_candles()'s
    inner loop for exactly one pair. Owns:
      - the online Kalman state (bot/pairs/kalman_online.py)
      - a rolling z-score window
      - the 1-hour decision/execution lag buffer
      - one EntryExitEngine instance (reused for its validated
        _check_entry_conditions/_check_exit_conditions methods and its
        KalmanPositionSizer — never reimplemented here)
    """

    def __init__(self, pair_config: PairConfig):
        self.pair_config = pair_config

        theta_0 = np.array([pair_config.hedge_ratio, pair_config.intercept])
        P_0 = np.eye(2) * 1.0  # matches KalmanFilterEngine._initialise_state()'s own seed exactly
        self._state = KalmanState(theta=theta_0, P=P_0)

        self._zscore_roller = RollingZScore()
        self._native_zscore_lag1_carry: Optional[float] = None  # native zscore one step behind "now"
        self._prev_native: Optional[NativeStepResult] = None    # the 1h-lagged decision bundle
        self._candle_index = 0  # 0-based count of candles fed since this engine started; drives is_warmup

        self.engine = EntryExitEngine(
            disable_target_exit=DISABLE_TARGET_EXIT,
            exit_time_stop_hours=EXIT_TIME_STOP_HOURS,
            capital_usdt=pair_config.capital_usdt,
            kelly_safety=KELLY_SAFETY_FRACTION,
            validated_win_rate=pair_config.validated_win_rate,
            min_position_usdt=MIN_POSITION_USDT_OVERRIDE,   # claude code changed: was NOT threaded through — see module docstring below; KalmanPositionSizer.size_position() has its OWN internal floor check (entry_exit_engine.py) that ran before this file's downstream check ever got a chance to matter, silently zeroing every trade at this pilot's ~$33/pair capital. Fixed at the source (entry_exit_engine.py now takes this as a real constructor param) rather than patched around here.
            pair_name=pair_config.pair_name,
            symbol_a=pair_config.symbol_a,
            symbol_b=pair_config.symbol_b,
        )
        self._min_position_usdt = MIN_POSITION_USDT_OVERRIDE
        self._trade_id_counter = 0

    @property
    def open_trade(self) -> Optional[ResearchTradeRecord]:
        return self.engine.current_trade

    @property
    def current_beta(self) -> float:
        # claude code changed: new — live beta-drift guard. Exposes the
        # online Kalman filter's current tracked hedge ratio publicly so
        # pairs_bot_runner.py can compare it against the pair's seed
        # hedge_ratio without reaching into self._state directly. Real
        # need found 2026-09-24: a fresh 19-symbol cointegration re-scan
        # plus a same-window bootstrap replication showed MINA/ONG's
        # recent-data beta has flipped sign relative to its seed
        # (+1.625 -> -0.307) while the pair still passes the STATIC,
        # full-history cointegration test — the online state and the
        # static gate can disagree, and nothing was watching the online
        # side before this.
        return float(self._state.theta[0])

    def _advance_state(self, price_a: float, price_b: float) -> Optional[NativeStepResult]:
        """Shared by process_candle() and bootstrap_candle(): runs the
        Kalman step, warmup-gated z-score update, and 1h lag buffering —
        everything EXCEPT entry/exit rule evaluation. Returns the
        1h-lagged decision bundle to act on this call (or None during
        warmup/before any history has accumulated). Isolating this here
        means bootstrap_candle() can prime this engine's state from
        historical data without ever touching self.engine.current_trade —
        a historical replay must never open/close a "real" paper trade,
        only warm up the underlying signal machinery."""
        new_state, native = kalman_step(self._state, price_a, price_b, self._candle_index)
        self._state = new_state
        self._candle_index += 1

        # claude code changed: gate the rolling z-score window on is_warmup,
        # matching kalman_filter_engine.py's own NaN-masking of the first
        # WARMUP_CANDLES candles' spread — see kalman_step()'s docstring for
        # the verified historical-replay divergence this fixes.
        if native.is_warmup:
            native.zscore_lag1 = self._native_zscore_lag1_carry
            native.zscore = None
        else:
            native.zscore = self._zscore_roller.update(native.spread)
            native.zscore_lag1 = self._native_zscore_lag1_carry
            self._native_zscore_lag1_carry = native.zscore
        native.pair_signal = -native.zscore if native.zscore is not None else None

        decision = self._prev_native
        self._prev_native = native
        return decision

    def bootstrap_candle(self, price_a: float, price_b: float) -> None:
        """Feed one historical candle purely to warm up Kalman/z-score/lag
        state — never evaluates entry/exit rules and never touches
        self.engine.current_trade. Use bootstrap_from_history() for a
        full historical replay before live polling starts."""
        self._advance_state(price_a, price_b)

    def bootstrap_from_history(self, price_pairs: list) -> None:
        """price_pairs: chronologically ordered list of (price_a, price_b)
        tuples — typically the last ~1000 real closed candles fetched
        fresh from the exchange at startup (see
        bot/core/pairs_bot_runner.py's bootstrap_session()). After this,
        the engine can generate a real signal on its very first live
        candle instead of waiting ~1 week for WARMUP_CANDLES + the
        z-score rolling window's min_periods to accumulate from a cold
        start."""
        for price_a, price_b in price_pairs:
            self.bootstrap_candle(price_a, price_b)

    def process_candle(self, timestamp, price_a: float, price_b: float) -> SignalDecision:
        """Feed one new real 1h candle (both legs' close prices). Returns
        the decision for THIS candle, which was computed using the
        PREVIOUS candle's decision variables (zscore, zscore_lag1,
        pair_signal_dynamic, beta, prediction_error, beta_uncertainty) —
        reproducing the offline *_kalman_LAG1h.csv convention: decision
        columns lagged one candle, spread/fill price NOT lagged. See the
        approved plan's "1-hour execution lag" section for why this
        asymmetric shift is required and where it came from.
        """
        decision = self._advance_state(price_a, price_b)

        if decision is None or decision.zscore is None or decision.zscore_lag1 is None:
            return SignalDecision(action="NONE", reason="warmup", native=self._prev_native)

        if self.engine.current_trade is None:
            entered = self.engine._check_entry_conditions(
                zscore=decision.zscore,
                zscore_lag=decision.zscore_lag1,
                signal=decision.pair_signal,
                beta_uncert=decision.beta_uncertainty,
                pred_error=decision.prediction_error,
            )
            if not entered:
                return SignalDecision(action="NONE", reason="no_entry_signal", native=self._prev_native)

            total, leg_a, leg_b, strength = self.engine.sizer.size_position(
                zscore=decision.zscore,
                beta=decision.beta_pred,
                beta_uncertainty=decision.beta_uncertainty,
                prediction_error=decision.prediction_error,
            )
            # claude code changed: this is now a redundant/defensive check, not
            # the primary enforcement — size_position() itself already floors
            # to exactly 0.0 below MIN_POSITION_USDT_OVERRIDE (passed into
            # EntryExitEngine's constructor above), so total is always either
            # 0.0 or >= self._min_position_usdt here. Kept as a harmless,
            # explicit invariant check rather than trusting that silently.
            if total < self._min_position_usdt:
                return SignalDecision(action="NONE", reason="sized_below_floor", sized_usdt=total, native=self._prev_native)

            direction = "SHORT_SPREAD" if decision.zscore > 0 else "LONG_SPREAD"
            stop_zscore = min(abs(decision.zscore) + self.engine.exit_stop_distance, ZSCORE_WINSOR_LIMIT)
            target_zscore = -np.sign(decision.zscore) * self.engine.exit_target_overshoot * abs(decision.zscore)
            min_hold_hours = self.engine.exit_min_hold_base_hours * abs(decision.zscore) / self.engine.entry_threshold

            self._trade_id_counter += 1
            trade = ResearchTradeRecord(
                trade_id=self._trade_id_counter,
                entry_timestamp=timestamp,
                entry_zscore=decision.zscore,
                entry_signal=decision.pair_signal,
                entry_beta=decision.beta_pred,
                entry_spread=self._prev_native.spread,
                direction=direction,
                position_usdt=total,
                leg_a_usdt=leg_a,
                leg_b_usdt=leg_b,
                kelly_fraction=self.engine.sizer.safe_kelly,
                signal_strength=strength,
                stop_zscore=stop_zscore,
                target_zscore=target_zscore,
                min_hold_hours=min_hold_hours,
                max_adverse_zscore=decision.zscore,
                max_favorable_zscore=decision.zscore,
                prediction_error_at_entry=decision.prediction_error,
                beta_uncertainty_at_entry=decision.beta_uncertainty,
            )
            self.engine.current_trade = trade
            return SignalDecision(action="ENTER", direction=direction, trade=trade, native=self._prev_native)

        exit_reason = self.engine._check_exit_conditions(
            ts=timestamp,
            zscore=decision.zscore,
            spread=self._prev_native.spread,
            idx=0,
        )
        if exit_reason:
            closed_trade = self.engine.current_trade
            closed_trade.exit_timestamp = timestamp
            closed_trade.exit_zscore = decision.zscore
            closed_trade.exit_reason = exit_reason
            closed_trade.hours_held = (timestamp - closed_trade.entry_timestamp).total_seconds() / 3600.0
            self.engine.current_trade = None
            return SignalDecision(action="EXIT", reason=exit_reason, trade=closed_trade, native=self._prev_native)

        return SignalDecision(action="HOLD", trade=self.engine.current_trade, native=self._prev_native)
