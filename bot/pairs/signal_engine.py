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

import math   # claude code changed: Stage 2D Rev B

import numpy as np
import pandas as pd   # claude code changed: Stage 2D Rev B — grid arithmetic on candle timestamps

from bot.pairs.config import PairConfig, MIN_POSITION_USDT_OVERRIDE   # claude code changed: Stage 2D Rev B step 8 — KELLY_SAFETY_FRACTION removed; Steps 9–10 — DISABLE_TARGET_EXIT/EXIT_TIME_STOP_HOURS no longer used (canonical lifecycle: H from the protocol)
from bot.quant_core import lifecycle   # claude code changed: Stage 2D Rev B Steps 9–10 — the canonical lifecycle shared with research
from bot.quant_core.protocol import CANONICAL, CanonicalProtocol   # claude code changed: Stage 2D Rev B
from bot.research.entry_exit_engine import ENTRY_ZSCORE_STRONG   # claude code changed: Stage 2D Rev B step 8 — descriptive strength label only
from bot.pairs.kalman_online import NativeStepResult, native_from_step   # claude code changed: Stage 2D Rev B — was kalman_step/RollingZScore (gap-unaware, prior-based)
from bot.quant_core.kalman import CanonicalKalman, CanonicalZScore   # claude code changed: Stage 2D Rev B — the same filter/z object research uses
from bot.research.kalman_filter_engine import KalmanState   # claude code changed: Stage 2D Rev B — ZSCORE_WINSOR_LIMIT no longer needed (no z stop-loss)
from bot.research.entry_exit_engine import EntryExitEngine, TradeRecord as ResearchTradeRecord


@dataclass
class SignalDecision:
    action: str  # "NONE" | "ENTER" | "HOLD" | "EXIT"
    reason: str = ""
    trade: Optional[ResearchTradeRecord] = None
    native: Optional[NativeStepResult] = None
    direction: Optional[str] = None
    sized_usdt: Optional[float] = None
    rejection: Optional[object] = None   # claude code changed: Stage 2D Rev B D-2 — the lifecycle.EntrySignMismatch behind a C1 rejection, for durable journaling


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

    def __init__(self, pair_config: PairConfig, theta0=None, protocol: CanonicalProtocol = CANONICAL):   # claude code changed: Stage 2D Rev B C6 — θ₀ from the causal seed record when given; protocol explicit
        self.pair_config = pair_config
        self.protocol = protocol   # claude code changed: Stage 2D Rev B

        # claude code changed: Stage 2D Rev B C6 — the live runner passes θ₀ from
        # the validated CAUSAL seed record. The config fallback (cointegration
        # hedge_ratio/intercept, a look-ahead fit) exists for offline harnesses
        # only; the runner never trades a pair without a causal seed.
        theta_0 = np.array(theta0 if theta0 is not None else [pair_config.hedge_ratio, pair_config.intercept], dtype=float)
        self._kf = CanonicalKalman(theta_0, protocol)   # claude code changed: Stage 2D Rev B — θ₀ from the seed, P₀ = I, Q/R/warmup/g_max from the canonical protocol (was a KalmanState + kalman_step)

        self._zscore_roller = CanonicalZScore(protocol)   # claude code changed: Stage 2D Rev B — segment-aware, NaN slots for missing/warmup candles (was RollingZScore)
        self._native_zscore_lag1_carry: Optional[float] = None  # native zscore one GRID candle behind "now"
        self._prev_native: Optional[NativeStepResult] = None    # the 1h-lagged decision bundle
        self._last_ts: Optional[pd.Timestamp] = None   # claude code changed: Stage 2D Rev B — open time of the last grid candle stepped
        self.candle_anomalies: list = []                # claude code changed: Stage 2D Rev B (D7) — rejected duplicate/out-of-order/off-grid candles, recorded
        self.missing_candles_stepped = 0               # claude code changed: Stage 2D Rev B (D7) — grid candles with no data, stepped as prediction-only

        # claude code changed: Stage 2D Rev B Steps 9–11 — canonical lifecycle state
        self._last_observed: Optional[tuple] = None    # (open time, close_a, close_b) of the last OBSERVED candle — the next exit-decision cutoff
        self._last_exit_fill_ts: Optional[pd.Timestamp] = None   # the exit-fill close is never an entry-decision cutoff
        self.entry_sign_mismatches: list = []          # C1 — rejected entries, recorded (lifecycle.EntrySignMismatch)
        self.requires_reconciliation = False           # C5 — a live polling gap: entries blocked until reconciled
        self.reconciliation_gaps: list = []            # C5 — the grid timestamps live did not receive

        self.engine = EntryExitEngine(   # claude code changed: Stage 2D Rev B — CANONICAL rule set (was the legacy target/stop/partial/time-stop-hours rules)
            rule_set="CANONICAL",
            protocol=protocol,
            capital_usdt=pair_config.capital_usdt,
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
        return float(self._kf.theta[0])   # claude code changed: Stage 2D Rev B — posterior β(t|t) of the canonical filter

    @property
    def _state(self) -> KalmanState:   # claude code changed: Stage 2D Rev B — read-only view kept for diagnostics/harness code
        return KalmanState(theta=self._kf.theta.copy(), P=self._kf.P.copy())

    def _grid_step(self, log_a: float, log_b: float, timestamp) -> NativeStepResult:   # claude code changed: Stage 2D Rev B — one hourly grid candle (NaN = missing)
        native = native_from_step(self._kf.step(log_a, log_b), timestamp)
        # z input: the innovation, masked to NaN on missing/warmup/invalid
        # candles exactly as research masks kalman_spread before its rolling.
        z_in = native.spread if (native.observed and not native.is_warmup) else math.nan
        native.zscore = self._zscore_roller.update(z_in, native.segment)
        native.zscore_lag1 = self._native_zscore_lag1_carry   # previous GRID candle's z (None if it had none, as research's shift(1) gives NaN)
        self._native_zscore_lag1_carry = native.zscore
        native.pair_signal = -native.zscore if native.zscore is not None else None
        return native

    def _check_sequence(self, timestamp) -> Optional[int]:   # claude code changed: Stage 2D Rev B (D7) — number of missing grid candles before `timestamp`, or None = reject
        if timestamp is None or self._last_ts is None:
            return 0
        ts = pd.Timestamp(timestamp)
        hours = (ts - self._last_ts) / pd.Timedelta(hours=1)
        if ts != ts.floor("h") or hours != int(hours) or hours < 1:
            kind = "duplicate" if hours == 0 else "out_of_order" if hours < 0 else "unexpected_interval"
            self.candle_anomalies.append({"timestamp": ts, "previous": self._last_ts, "kind": kind})
            return None
        return int(hours) - 1

    def _advance_state(self, price_a: float, price_b: float, timestamp=None) -> Optional[NativeStepResult]:
        """Shared by process_candle() and bootstrap_candle(): advances the
        canonical filter over the hourly GRID up to `timestamp` (missing grid
        candles in between are prediction-only steps, D8), then this candle;
        updates the segment-aware z-score and the 1h lag buffer. Returns the
        1h-lagged decision bundle (the previous GRID candle's values — z-less
        when that candle was missing/warmup). The caller has already rejected
        duplicate/out-of-order/off-grid timestamps (_check_sequence). Never
        touches self.engine.current_trade."""
        # claude code changed: Stage 2D Rev B — was a gap-unaware kalman_step
        # over whatever candles arrived (a missing hour was silently
        # compressed into one step, Phase O D8).
        missing = self._check_sequence(timestamp) or 0
        for k in range(missing):
            ts_k = self._last_ts + pd.Timedelta(hours=k + 1)
            self._prev_native = self._grid_step(math.nan, math.nan, ts_k)
            self.missing_candles_stepped += 1
        native = self._grid_step(math.log(price_a), math.log(price_b), timestamp)
        if timestamp is not None:
            self._last_ts = pd.Timestamp(timestamp)
        elif self._last_ts is not None:
            self._last_ts = self._last_ts + pd.Timedelta(hours=1)
        decision = self._prev_native
        self._prev_native = native
        return decision

    def bootstrap_candle(self, price_a: float, price_b: float, timestamp=None) -> None:   # claude code changed: Stage 2D Rev B — optional candle open time so history gaps are stepped on the grid
        """Feed one historical candle purely to warm up Kalman/z-score/lag
        state — never evaluates entry/exit rules and never touches
        self.engine.current_trade. Use bootstrap_from_history() for a
        full historical replay before live polling starts."""
        if self._check_sequence(timestamp) is None:   # claude code changed: Stage 2D Rev B (D7) — reject, recorded in candle_anomalies
            return
        self._advance_state(price_a, price_b, timestamp)   # claude code changed: Stage 2D Rev B
        self._last_observed = (self._last_ts, float(price_a), float(price_b))   # claude code changed: Stage 2D Rev B Steps 9–10

    def bootstrap_from_history(self, price_pairs: list) -> None:
        """price_pairs: chronologically ordered list of (price_a, price_b)
        tuples — typically the last ~1000 real closed candles fetched
        fresh from the exchange at startup (see
        bot/core/pairs_bot_runner.py's bootstrap_session()). After this,
        the engine can generate a real signal on its very first live
        candle instead of waiting ~1 week for WARMUP_CANDLES + the
        z-score rolling window's min_periods to accumulate from a cold
        start."""
        for row in price_pairs:   # claude code changed: Stage 2D Rev B — accepts (price_a, price_b) or (timestamp, price_a, price_b)
            if len(row) == 3:
                self.bootstrap_candle(row[1], row[2], timestamp=row[0])
            else:
                self.bootstrap_candle(*row)

    def abort_entry(self, trade) -> None:   # claude code changed: Stage 2D Rev B D-3 — new
        """Undo an ENTER the caller did NOT execute (eligibility/health refusal
        or an execution failure): no position exists, so the engine must not
        keep holding/exiting a phantom trade. The aborted trade's fill candle
        counts like an exit fill (no entry decision on it)."""
        if trade is not None and self.engine.current_trade is trade:
            self.engine.current_trade = None
            self._last_exit_fill_ts = pd.Timestamp(trade.entry_timestamp)

    def confirm_gap_reconciled(self) -> None:   # claude code changed: Stage 2D Rev B C5 — new
        """Clear the C5 block. ONLY for a reconciler that has established from
        authoritative exchange history that the missed grid candles are
        genuinely absent there (so the prediction-only steps taken were
        correct). Backfill/reconciliation itself is a later stage; nothing
        in this module calls this."""
        self.requires_reconciliation = False

    def process_candle(self, timestamp, price_a: float, price_b: float) -> SignalDecision:
        """Feed one newly CLOSED live candle c. Canonical lifecycle
        (bot/quant_core/lifecycle.py, identical to research):

          exits  — decision at cutoff p = the previous OBSERVED candle,
                   p ≥ E + 1 (C2), fill at close(c) = first observed candle
                   after p (later than p + 1 → exit-fill candle missing, C3);
          entries — decision at cutoff t = c − 1 (the previous GRID candle,
                   must be observed, have a z, not be the last exit-fill
                   candle), |z| ≥ threshold, C1 sign check on
                   h_t = ln P_A,t − β(t|t) ln P_B,t − α(t|t), fixed-fraction
                   sizing, fill at close(c).
        A grid gap seen while polling live (C5) blocks entries until
        reconciled; the filter steps the gap as missing candles meanwhile."""
        # claude code changed: Stage 2D Rev B Steps 9–11 — rewritten for the
        # canonical lifecycle (was EntryExitEngine._check_entry_conditions /
        # _check_exit_conditions: lag confirmation, target/z-stop/4 h time stop
        # decided and filled on the same candle).
        missing = self._check_sequence(timestamp)
        if missing is None:   # (D7) duplicate/out-of-order/off-grid: state NOT advanced, recorded
            return SignalDecision(action="NONE", reason="rejected_candle:" + self.candle_anomalies[-1]["kind"], native=self._prev_native)
        ts = pd.Timestamp(timestamp)
        if missing and self._last_ts is not None:   # C5 — live cannot tell an exchange gap from a missed poll
            self.requires_reconciliation = True
            self.reconciliation_gaps.extend(self._last_ts + pd.Timedelta(hours=k + 1) for k in range(missing))
        prev_obs = self._last_observed
        decision = self._advance_state(price_a, price_b, timestamp)
        self._last_observed = (ts, float(price_a), float(price_b))

        trade = self.engine.current_trade
        if trade is not None:
            if prev_obs is None:
                return SignalDecision(action="HOLD", trade=trade, native=self._prev_native)
            p_ts, p_a, p_b = prev_obs
            h_p = lifecycle.held_spread(p_a, p_b, trade.entry_beta, trade.entry_alpha)
            reason = lifecycle.exit_decision(trade.direction, h_p, lifecycle.candles_between(trade.entry_timestamp, p_ts), self.protocol)
            if not reason:
                return SignalDecision(action="HOLD", trade=trade, native=self._prev_native)
            gap = list(pd.date_range(p_ts + lifecycle.HOUR, ts - lifecycle.HOUR, freq="h")) if ts - p_ts > lifecycle.HOUR else []
            trade.exit_decision_timestamp = p_ts
            trade.exit_timestamp = ts
            trade.exit_zscore = decision.zscore if (decision is not None and decision.timestamp == p_ts and decision.zscore is not None) else math.nan
            trade.exit_reason = reason
            trade.exit_price_a, trade.exit_price_b = float(price_a), float(price_b)
            trade.hours_held = (ts - trade.entry_timestamp).total_seconds() / 3600.0
            trade.holding_candles = lifecycle.candles_between(trade.entry_timestamp, ts)
            trade.exit_fill_candle_missing = bool(gap)
            trade.missing_exit_fill_timestamps = ";".join(t.isoformat() for t in gap)
            trade.h_compliant = (not gap) and trade.holding_candles <= self.protocol.require_max_holding()
            trade.data_complete = not gap
            # claude code changed: Stage 2D Rev B D-1 — an exit taken while the C5
            # reconciliation flag is active is permanently marked (and persisted
            # by the runner/trade logger); it is never clean parity evidence.
            trade.reconciliation_affected = bool(self.requires_reconciliation)
            trade.reconciliation_gaps = ";".join(pd.Timestamp(g).isoformat() for g in self.reconciliation_gaps) if self.requires_reconciliation else ""
            self.engine.current_trade = None
            self._last_exit_fill_ts = ts
            return SignalDecision(action="EXIT", reason=reason, trade=trade, native=self._prev_native)

        if decision is None or not decision.observed:
            return SignalDecision(action="NONE", reason="missing_decision_candle" if decision is not None else "warmup", native=self._prev_native)
        if decision.zscore is None:
            return SignalDecision(action="NONE", reason="warmup", native=self._prev_native)
        if self._last_exit_fill_ts is not None and decision.timestamp <= self._last_exit_fill_ts:
            return SignalDecision(action="NONE", reason="exit_fill_candle_not_a_decision_cutoff", native=self._prev_native)
        direction = lifecycle.entry_direction(decision.zscore, self.protocol)
        if direction is None:
            return SignalDecision(action="NONE", reason="no_entry_signal", native=self._prev_native)
        if self.requires_reconciliation:   # C5 — never open a trade on a state that may have missed candles
            return SignalDecision(action="NONE", reason="reconciliation_required", native=self._prev_native)
        if prev_obs is None or prev_obs[0] != decision.timestamp:
            return SignalDecision(action="NONE", reason="missing_decision_candle", native=self._prev_native)
        _, t_a, t_b = prev_obs
        h_t = lifecycle.held_spread(t_a, t_b, decision.beta_post, decision.alpha_post)
        if not lifecycle.entry_sign_valid(direction, h_t):   # C1
            mismatch = lifecycle.EntrySignMismatch(
                decision.timestamp, direction, decision.zscore, h_t, decision.alpha_post, decision.beta_post, t_a, t_b)
            self.entry_sign_mismatches.append(mismatch)
            return SignalDecision(action="NONE", reason=lifecycle.ENTRY_SIGN_MISMATCH, direction=direction,
                                  native=self._prev_native, rejection=mismatch)   # claude code changed: Stage 2D Rev B D-2 — the runner journals it
        sized = self.engine.sizer.size(decision.beta_post)
        if not sized.ok:
            return SignalDecision(action="NONE", reason="sizing_skip: " + sized.reason, native=self._prev_native)
        total, leg_a, leg_b = sized.value
        self._trade_id_counter += 1
        trade = ResearchTradeRecord(
            trade_id=self._trade_id_counter,
            entry_timestamp=ts,                            # fill candle E = t + d
            entry_decision_timestamp=decision.timestamp,   # cutoff t
            entry_zscore=decision.zscore,
            entry_signal=decision.pair_signal,
            entry_beta=decision.beta_post,                 # β_e = β(t|t)
            entry_alpha=decision.alpha_post,               # α_e = α(t|t)
            entry_spread=decision.spread,
            entry_held_spread=h_t,
            entry_price_a=float(price_a),                  # fill-candle closes (reference; real fills are logged by execution)
            entry_price_b=float(price_b),
            direction=direction,
            position_usdt=total,
            leg_a_usdt=leg_a,
            leg_b_usdt=leg_b,
            kelly_fraction=float("nan"),
            allocation_fraction=self.engine.sizer.fraction,
            executability="UNVERIFIED",                    # verified by bot/pairs/execution.py in real mode
            signal_strength="STRONG" if abs(decision.zscore) >= ENTRY_ZSCORE_STRONG else "NORMAL",
            stop_zscore=math.nan, target_zscore=math.nan, min_hold_hours=math.nan,   # no z stop / target / min hold
            max_adverse_zscore=decision.zscore,
            max_favorable_zscore=decision.zscore,
            prediction_error_at_entry=decision.prediction_error,
            beta_uncertainty_at_entry=decision.beta_uncertainty,
        )
        self.engine.current_trade = trade
        return SignalDecision(action="ENTER", direction=direction, trade=trade, native=self._prev_native)
