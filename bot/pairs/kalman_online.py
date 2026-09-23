# ============================================================
# bot/pairs/kalman_online.py
# claude code changed: new file — live pairs-trading pilot, Step 1.
#
# Online (single-candle-at-a-time) equivalent of
# bot/research/kalman_filter_engine.py's KalmanFilterEngine._run_filter(),
# which only exists as a full-batch loop over a pre-loaded CSV. This
# module extracts that loop body's math into a pure step() function that
# carries state (KalmanState, reused from kalman_filter_engine.py — not
# reinvented) between calls, so a live bot can feed it one new real
# candle per hour.
#
# Reused verbatim from the research module (not duplicated as separate
# constants that could drift): PROCESS_NOISE_BETA/ALPHA, OBSERVATION_NOISE,
# ZSCORE_WINSOR_LIMIT, and KalmanState itself.
#
# pair_signal_dynamic = -kalman_zscore "by construction" is copied from
# kalman_filter_engine.py line ~1646 (results["pair_signal_dynamic"] = -z)
# — confirmed by direct read, not assumed.
# ============================================================

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from bot.research.kalman_filter_engine import (
    KalmanState,
    PROCESS_NOISE_ALPHA,
    PROCESS_NOISE_BETA,
    OBSERVATION_NOISE,
    ZSCORE_WINSOR_LIMIT,
    ZSCORE_MIN_PERIODS,
    WARMUP_CANDLES,
)

ZSCORE_WINDOW = 504  # 3 weeks of 1h candles — matches kalman_filter_engine.py exactly

_Q = np.diag([PROCESS_NOISE_BETA, PROCESS_NOISE_ALPHA])
_R = OBSERVATION_NOISE
_I2 = np.eye(2)


@dataclass
class NativeStepResult:
    """The un-lagged ("native") output of one Kalman step, before the
    1-hour execution lag (see PairSignalEngine) is applied to the
    decision columns. spread/price fields here are what the lag
    convention leaves un-shifted; everything else gets buffered one
    candle before being used for a live entry/exit decision."""

    beta_pred:        float   # tradeable hedge ratio (pre-update, leakage-free)
    alpha_pred:       float
    spread:           float   # = prediction_error, by construction — see docstring below
    prediction_error: float
    beta_uncertainty: float
    is_warmup:        bool    # first WARMUP_CANDLES (168) candles — spread untrusted, matches kalman_filter_engine.py's own is_warmup masking
    zscore:           Optional[float]     # None until ZSCORE_MIN_PERIODS candles have accumulated
    pair_signal:      Optional[float]     # -zscore, None while zscore is None
    zscore_lag1:      Optional[float]     # native zscore one candle before this one


def kalman_step(state: KalmanState, price_a: float, price_b: float, candle_index: int) -> tuple[KalmanState, NativeStepResult]:
    """One sequential Kalman update, math identical to
    KalmanFilterEngine._run_filter()'s loop body (F=identity, so
    theta_pred == theta going in). Returns the updated state plus the
    native (un-lagged) step output.

    spread == prediction_error here by construction: both are
    log(price_a) - alpha_pred - beta_pred*log(price_b), i.e. the
    observation innovation. Verified against
    _calculate_dynamic_spread()'s formula directly — not assumed.

    candle_index is 0-based and counts candles since this pair's live
    signal engine started (NOT since the pair's historical listing) —
    it only drives the is_warmup flag, matching kalman_filter_engine.py's
    own `is_warmup = np.arange(n) < WARMUP_CANDLES` masking. Verified by
    replaying this function against DODO_USDT/FIDA_USDT's full historical
    kalman.csv: without warmup-gating the z-score rolling window, results
    diverge from the reference by up to 0.86 during the transient
    warmup-to-full-window period (rows < ~900) before converging to
    exact (1e-13) agreement; gating on is_warmup exactly like the
    reference eliminates that divergence.
    """
    theta_pred = state.theta.copy()          # F = I
    P_pred = state.P + _Q

    log_a = math.log(price_a)
    log_b = math.log(price_b)
    H = np.array([log_b, 1.0])

    innovation = log_a - H @ theta_pred
    S = H @ P_pred @ H + _R
    K = P_pred @ H / S

    theta_post = theta_pred + K * innovation
    KH = np.outer(K, H)
    P_post = (_I2 - KH) @ P_pred @ (_I2 - KH).T + np.outer(K, K) * _R

    new_state = KalmanState(theta=theta_post, P=P_post)

    result = NativeStepResult(
        beta_pred=float(theta_pred[0]),
        alpha_pred=float(theta_pred[1]),
        spread=float(innovation),
        prediction_error=float(innovation),
        beta_uncertainty=float(P_pred[0, 0]),
        is_warmup=candle_index < WARMUP_CANDLES,
        zscore=None,       # filled in by PairSignalEngine, which owns the rolling window
        pair_signal=None,
        zscore_lag1=None,
    )
    return new_state, result


class RollingZScore:
    """Trailing window z-score matching pandas'
    .rolling(window=504, min_periods=168).mean()/.std() exactly —
    including current value in the window, which is what pandas' default
    (non-shifted) rolling call does."""

    def __init__(self, window: int = ZSCORE_WINDOW, min_periods: int = ZSCORE_MIN_PERIODS):
        self.window = window
        self.min_periods = min_periods
        self._values: deque = deque(maxlen=window)

    def update(self, spread: float) -> Optional[float]:
        self._values.append(spread)
        if len(self._values) < self.min_periods:
            return None
        arr = np.array(self._values)
        std = arr.std(ddof=1) if len(arr) > 1 else 0.0
        if std == 0.0:
            return None
        z = (spread - arr.mean()) / std
        return float(np.clip(z, -ZSCORE_WINSOR_LIMIT, ZSCORE_WINSOR_LIMIT))
