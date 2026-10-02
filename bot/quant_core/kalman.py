# ============================================================
# bot/quant_core/kalman.py
# claude code changed: new file — Stage 2D Rev B steps 3–5: the ONE Kalman
# recursion and gap state machine used by both research
# (KalmanFilterEngine._run_filter) and live (kalman_online.kalman_step).
#
# Model (STAGE2D_CANONICAL_PROTOCOL.md §2): θ = [β, α]′, random-walk state,
# x_t = [y_t, 1]·θ_t + v_t.  Per hourly grid candle:
#   prior      θ(t|t−1) = θ(t−1|t−1),  P(t|t−1) = P(t−1|t−1) + Q
#   innovation e = x − H θ(t|t−1),      S = H P(t|t−1) H′ + R
#   gain       K = P(t|t−1) H′ / S
#   posterior  θ(t|t) = θ(t|t−1) + K e
#   Joseph     P(t|t) = (I − KH) P(t|t−1) (I − KH)′ + K R K′
# A MISSING candle is a prediction-only step (no update), so g missing
# candles give P + g·Q. A run longer than g_max invalidates the state; the
# next observed candle re-seeds with the canonical θ₀, P₀ and restarts warmup.
# ============================================================

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from bot.quant_core.protocol import CANONICAL, CanonicalProtocol

_I2 = np.eye(2)


@dataclass(frozen=True)
class StepResult:
    observed: bool
    reseeded: bool                 # this candle re-initialised the state (after an invalidating gap)
    state_valid: bool              # False while inside a gap longer than g_max
    in_warmup: bool                # candles since (re)seed < warmup (no signal allowed)
    segment: int                   # increments at every (re)seed
    theta_prior: np.ndarray
    P_prior: np.ndarray
    innovation: float              # NaN when not observed
    innovation_variance: float     # S; NaN when not observed
    gain: np.ndarray               # K (NaN vector when not observed)
    theta_post: np.ndarray
    P_post: np.ndarray
    filtered_residual: float       # x − H θ(t|t) = e·R/S (NaN when not observed)


@dataclass(frozen=True)
class Update:
    innovation: float
    innovation_variance: float
    gain: np.ndarray
    theta_post: np.ndarray
    P_post: np.ndarray
    filtered_residual: float


def measurement_update(theta_prior: np.ndarray, P_prior: np.ndarray, log_a: float, log_b: float,
                       r: float) -> Update:
    """The single measurement update (innovation, S, gain, posterior, Joseph
    covariance). Every Kalman path in the repo calls this; none re-implements it."""
    H = np.array([log_b, 1.0])
    e = log_a - H @ theta_prior
    S = float(H @ P_prior @ H + r)
    K = P_prior @ H / S
    theta_post = theta_prior + K * e
    KH = np.outer(K, H)
    P_post = (_I2 - KH) @ P_prior @ (_I2 - KH).T + np.outer(K, K) * r
    return Update(float(e), S, K, theta_post, P_post, float(log_a - H @ theta_post))


@dataclass
class CanonicalKalman:
    """Stateful canonical filter for one pair, advanced one GRID candle at a
    time. Research and live both drive this object."""
    theta0: np.ndarray
    protocol: CanonicalProtocol = CANONICAL
    theta: np.ndarray = field(init=False)
    P: np.ndarray = field(init=False)
    consecutive_missing: int = field(init=False, default=0)
    valid: bool = field(init=False, default=True)
    candles_since_seed: int = field(init=False, default=0)
    segment: int = field(init=False, default=0)
    _seeded: bool = field(init=False, default=False)

    def __post_init__(self):
        self.theta0 = np.asarray(self.theta0, dtype=float).copy()
        if self.theta0.shape != (2,) or not np.isfinite(self.theta0).all():
            raise ValueError("theta0 must be a finite [beta, alpha] pair")
        self._reset_state()

    @property
    def Q(self) -> np.ndarray:
        return np.diag([self.protocol.q_beta, self.protocol.q_alpha])

    def _reset_state(self):
        self.theta = self.theta0.copy()
        self.P = _I2 * self.protocol.p0_scale
        self.candles_since_seed = 0
        self.consecutive_missing = 0
        self.valid = True

    def step(self, log_a: float, log_b: float) -> StepResult:
        """Advance one hourly grid candle. NaN log_a or log_b = missing candle."""
        observed = math.isfinite(log_a) and math.isfinite(log_b)
        reseeded = False
        if not observed:
            self.consecutive_missing += 1
            if self.consecutive_missing > self.protocol.gap_max_candles:
                self.valid = False
            theta_prior, P_prior = self.theta.copy(), self.P + self.Q
            self.P = P_prior                                   # prediction only: P(t|t) = P(t|t−1)
            nan2 = np.full(2, np.nan)
            res = StepResult(False, False, self.valid, True if not self.valid else self._warm(),
                             self.segment, theta_prior, P_prior, math.nan, math.nan, nan2,
                             self.theta.copy(), self.P.copy(), math.nan)
            self.candles_since_seed += 1
            return res

        if not self.valid or not self._seeded:
            if self._seeded:                                   # invalidated by a long gap → canonical re-seed
                self.segment += 1
                reseeded = True
            self._reset_state()
            self._seeded = True
        self.consecutive_missing = 0

        theta_prior = self.theta.copy()                        # F = I
        P_prior = self.P + self.Q
        u = measurement_update(theta_prior, P_prior, log_a, log_b, self.protocol.r)
        self.theta, self.P = u.theta_post, u.P_post
        res = StepResult(True, reseeded, True, self._warm(), self.segment, theta_prior, P_prior,
                         u.innovation, u.innovation_variance, u.gain.copy(), u.theta_post.copy(),
                         u.P_post.copy(), u.filtered_residual)
        self.candles_since_seed += 1
        return res

    def _warm(self) -> bool:
        return self.candles_since_seed < self.protocol.warmup_candles


class CanonicalZScore:
    """Causal z of the innovation over the trailing z_window_candles GRID
    candles of the current state segment (current candle included, ddof=1),
    needing z_min_valid valid values; clipped to ±z_clip. Missing, warmup and
    invalid candles occupy a slot as NaN, exactly like pandas
    rolling(window, min_periods) over a NaN-bearing grid column."""

    def __init__(self, protocol: CanonicalProtocol = CANONICAL):
        from collections import deque
        self.p = protocol
        self._values = deque(maxlen=protocol.z_window_candles)
        self._segment: Optional[int] = None

    @property
    def min_periods(self) -> int:
        return self.p.z_min_valid

    @property
    def n_valid(self) -> int:
        """Valid (non-NaN) values currently in the window."""
        return sum(1 for v in self._values if math.isfinite(v))

    def update(self, value: float, segment: int) -> Optional[float]:
        if self._segment is not None and segment != self._segment:
            self._values.clear()                               # re-seed: the old segment's history is not used
        self._segment = segment
        self._values.append(value if math.isfinite(value) else math.nan)
        arr = np.array(self._values, dtype=float)
        valid = arr[np.isfinite(arr)]
        if not math.isfinite(value) or len(valid) < self.p.z_min_valid:
            return None
        sd = valid.std(ddof=1)
        if not (sd > 0):
            return None
        return float(np.clip((value - valid.mean()) / sd, -self.p.z_clip, self.p.z_clip))
