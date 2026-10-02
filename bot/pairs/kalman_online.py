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
    ZSCORE_WINDOW,   # claude code changed: Stage 2D Rev B — was a re-declared literal 504 here; now the one protocol value
)
from bot.quant_core.kalman import StepResult, measurement_update   # claude code changed: Stage 2D Rev B — the one measurement update shared with research

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

    beta_pred:        float   # PRIOR β(t|t−1) — diagnostics only since Stage 2D Rev B (decisions use beta_post, owner D1/D2)  # claude code changed
    alpha_pred:       float   # PRIOR α(t|t−1)  # claude code changed: Stage 2D Rev B — diagnostics only
    spread:           float   # = prediction_error (innovation), by construction — the z-score input
    prediction_error: float
    beta_uncertainty: float   # POSTERIOR P(t|t)[0,0] (variance)  # claude code changed: Stage 2D Rev B — was the prior P(t|t−1)[0,0] (Phase O D1)
    is_warmup:        bool    # since the last (re)seed, or the state is invalid  # claude code changed: Stage 2D Rev B — was candles since engine start
    zscore:           Optional[float]     # None until ZSCORE_MIN_PERIODS valid candles have accumulated in this segment
    pair_signal:      Optional[float]     # -zscore, None while zscore is None
    zscore_lag1:      Optional[float]     # native zscore one GRID candle before this one (None if that candle had none)
    beta_post:        float = math.nan    # claude code changed: Stage 2D Rev B — θ(t|t)[0], the decision β (D1/D2)
    alpha_post:       float = math.nan    # claude code changed: Stage 2D Rev B — θ(t|t)[1], the decision α
    beta_uncertainty_prior: float = math.nan   # claude code changed: Stage 2D Rev B — P(t|t−1)[0,0], diagnostics
    alpha_uncertainty: float = math.nan   # claude code changed: Stage 2D Rev B — P(t|t)[1,1]
    innovation_variance: float = math.nan # claude code changed: Stage 2D Rev B — S
    gain_beta:        float = math.nan    # claude code changed: Stage 2D Rev B — K[0]
    gain_alpha:       float = math.nan    # claude code changed: Stage 2D Rev B — K[1]
    filtered_residual: float = math.nan   # claude code changed: Stage 2D Rev B — x − Hθ(t|t) = e·R/S
    observed:         bool = True         # claude code changed: Stage 2D Rev B — False for a grid candle with no data
    state_valid:      bool = True         # claude code changed: Stage 2D Rev B — False inside a gap > g_max
    segment:          int = 0             # claude code changed: Stage 2D Rev B — increments at every re-seed
    timestamp:        object = None       # claude code changed: Stage 2D Rev B — candle open time this result belongs to


def native_from_step(st: StepResult, timestamp=None) -> NativeStepResult:   # claude code changed: Stage 2D Rev B — live view of one canonical step
    return NativeStepResult(
        beta_pred=float(st.theta_prior[0]), alpha_pred=float(st.theta_prior[1]),
        spread=st.innovation, prediction_error=st.innovation,
        beta_uncertainty=float(st.P_post[0, 0]),
        is_warmup=bool(st.in_warmup or not st.state_valid),
        zscore=None, pair_signal=None, zscore_lag1=None,
        beta_post=float(st.theta_post[0]), alpha_post=float(st.theta_post[1]),
        beta_uncertainty_prior=float(st.P_prior[0, 0]), alpha_uncertainty=float(st.P_post[1, 1]),
        innovation_variance=st.innovation_variance,
        gain_beta=float(st.gain[0]), gain_alpha=float(st.gain[1]),
        filtered_residual=st.filtered_residual, observed=st.observed,
        state_valid=st.state_valid, segment=st.segment, timestamp=timestamp,
    )


# claude code changed: Stage 2D Rev B — a gap-UNAWARE single update kept for
# monitor_engine.py (beta-drift monitoring only, no trading). The trading path
# (PairSignalEngine) drives bot/quant_core/kalman.CanonicalKalman instead,
# which adds the D8 gap policy; both call the same measurement_update().
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
    u = measurement_update(theta_pred, P_pred, math.log(price_a), math.log(price_b), _R)   # claude code changed: Stage 2D Rev B — was a second hand-written copy of the update
    new_state = KalmanState(theta=u.theta_post, P=u.P_post)
    result = NativeStepResult(
        beta_pred=float(theta_pred[0]),
        alpha_pred=float(theta_pred[1]),
        spread=u.innovation,
        prediction_error=u.innovation,
        beta_uncertainty=float(u.P_post[0, 0]),   # claude code changed: Stage 2D Rev B — posterior (was P_pred[0, 0])
        is_warmup=candle_index < WARMUP_CANDLES,
        zscore=None,       # filled in by the caller, which owns the rolling window
        pair_signal=None,
        zscore_lag1=None,
        beta_post=float(u.theta_post[0]), alpha_post=float(u.theta_post[1]),   # claude code changed: Stage 2D Rev B
        beta_uncertainty_prior=float(P_pred[0, 0]), alpha_uncertainty=float(u.P_post[1, 1]),   # claude code changed: Stage 2D Rev B
        innovation_variance=u.innovation_variance, gain_beta=float(u.gain[0]), gain_alpha=float(u.gain[1]),   # claude code changed: Stage 2D Rev B
        filtered_residual=u.filtered_residual,   # claude code changed: Stage 2D Rev B
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
