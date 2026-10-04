# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/pairs_step17_null.py
#
# claude code changed: new file — 2026-10-03, owner decision D-4 (DECISION_SHEET_D1-D11_2026-10-03.md, signed):
# the Stage 2D Step-17 canonical pairs null.
#
#   H0   "no pair-trading relationship": leg B's dynamics are unrelated to leg A's timing.
#   NULL circular shift of leg B's LOG-RETURNS relative to leg A by s candles, s uniform on [720, n − 720]
#        (≥ 30 days), B's price path rebuilt from the shifted returns (same start price). Each leg keeps its own
#        volatility and autocorrelation; the cross-asset relationship (incl. cointegration) is destroyed.
#   STAT the D-3 primary endpoint, net mean return per trade, from the FULL canonical pipeline (causal Kalman
#        seed at the S start → canonical lifecycle → costs) rerun on every replicate.
#   TEST one-sided (an edge means MORE net return than the null): p = (1 + #{T* ≥ T}) / (B + 1), B = 999.
#   ROBUSTNESS independent stationary block bootstrap per leg (mean block length = owner parameter; NOT set).
#
# USE IS GATED: the sheet requires, BEFORE ANY USE, a size calibration on unrelated random-walk pairs (3–7 % at
# α = 5 %, R ≥ 500) and a power calibration on synthetic OU pairs. `step17_null_test(..., canonical=True)`
# refuses to run until a calibration artifact meeting those criteria exists (Step17NotCalibrated).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from bot.quant_core.inference import CONFIRMATORY_B

NULL_VERSION = "QBP-STEP17-D4-1"
MIN_SHIFT_CANDLES = 720
SIZE_BAND = (0.03, 0.07)
MIN_CALIBRATION_REPS = 500
CALIBRATION_ARTIFACT = Path("research_data") / "_governance" / "step17_null_calibration.json"


class Step17NotCalibrated(RuntimeError):
    """The D-4 null may not be used before its size/power calibration passes."""


def shift_leg_b(price_a: pd.Series, price_b: pd.Series, shift: int) -> pd.Series:
    """Leg B rebuilt from its log-returns circularly shifted by `shift` candles; same index and start price.
    Leg A is never touched (the caller keeps using the original series)."""
    if not price_a.index.equals(price_b.index):
        raise ValueError("legs must share one index")
    if not (isinstance(shift, (int, np.integer)) and 0 < shift < len(price_b) - 1):
        raise ValueError("shift must be an integer in (0, n − 1)")
    lb = np.log(price_b.to_numpy(float))
    if not np.all(np.isfinite(lb)):
        raise ValueError("leg B must be strictly positive and finite (fill gaps before the null)")
    r = np.diff(lb)
    shifted = np.concatenate([[lb[0]], lb[0] + np.cumsum(np.roll(r, int(shift)))])
    return pd.Series(np.exp(shifted), index=price_b.index, name=price_b.name)


def draw_shifts(n: int, b: int, rng: np.random.Generator, min_shift: int = MIN_SHIFT_CANDLES) -> np.ndarray:
    hi = n - 1 - min_shift                               # roll over the n − 1 returns
    if hi <= min_shift:
        raise ValueError(f"series of {n} candles is too short for shifts of at least {min_shift} candles")
    return rng.integers(min_shift, hi + 1, size=b)


def stationary_block_bootstrap(returns: np.ndarray, mean_block: float, rng: np.random.Generator) -> np.ndarray:
    """Politis–Romano stationary bootstrap of one return series (robustness null). `mean_block` has NO default:
    the owner has not fixed it (flagged)."""
    if not (mean_block and mean_block >= 1):
        raise ValueError("mean_block must be >= 1 (owner parameter — not set by the decision sheet)")
    r = np.asarray(returns, float)
    n, p = len(r), 1.0 / float(mean_block)
    out = np.empty(n)
    i = rng.integers(n)
    for t in range(n):
        out[t] = r[i]
        i = rng.integers(n) if rng.random() < p else (i + 1) % n
    return out


def require_calibrated(artifact: Path = CALIBRATION_ARTIFACT) -> dict:
    if not Path(artifact).exists():
        raise Step17NotCalibrated(f"no Step-17 calibration artifact at {artifact}; the D-4 null requires size "
                                  f"{SIZE_BAND} with R >= {MIN_CALIBRATION_REPS} and a power calibration before any use")
    cal = json.loads(Path(artifact).read_text(encoding="utf-8"))
    size, reps = cal.get("size"), cal.get("reps_size", 0)
    if cal.get("null_version") != NULL_VERSION or reps < MIN_CALIBRATION_REPS or size is None \
            or not (SIZE_BAND[0] <= size <= SIZE_BAND[1]) or "power" not in cal:
        raise Step17NotCalibrated(f"Step-17 calibration does not meet D-4 (version {cal.get('null_version')}, "
                                  f"R = {reps}, size = {size}, power present = {'power' in cal})")
    return cal


def step17_null_test(price_a: pd.Series, price_b: pd.Series, statistic: Callable[[pd.Series, pd.Series], float], *,
                     seed: int, n_perm: int = CONFIRMATORY_B, min_shift: int = MIN_SHIFT_CANDLES,
                     canonical: bool = True, artifact: Path = CALIBRATION_ARTIFACT) -> dict:
    """One-sided circular-shift null test of `statistic` (higher = more edge). canonical=True enforces the D-4
    calibration gate; canonical=False is for calibration/tests only and its result says so."""
    if canonical:
        require_calibrated(artifact)
    rng = np.random.default_rng(seed)
    t_obs = float(statistic(price_a, price_b))
    shifts = draw_shifts(len(price_b), n_perm, rng, min_shift)
    t_null = np.array([float(statistic(price_a, shift_leg_b(price_a, price_b, int(s)))) for s in shifts])
    if not np.all(np.isfinite(t_null)) or not np.isfinite(t_obs):
        return {"status": "UNTESTABLE", "reason": "non-finite statistic on the real data or a replicate",
                "null_version": NULL_VERSION, "p_value": None, "canonical": canonical}
    p = (1 + int(np.sum(t_null >= t_obs))) / (n_perm + 1)
    return {"status": "OK", "null_version": NULL_VERSION, "statistic": t_obs, "p_value": p, "n_perm": n_perm,
            "min_shift": min_shift, "seed": seed, "canonical": canonical,
            "label": None if canonical else "CALIBRATION/TEST ONLY — NOT EVIDENCE"}


def canonical_net_mean_return_per_trade(price_a: pd.Series, price_b: pd.Series, *, pair_name: str = "X_USDT/Y_USDT",
                                        symbol_a: str = "X_USDT", symbol_b: str = "Y_USDT", **entry_exit_kwargs) -> float:
    """The D-3 primary endpoint from the full canonical pipeline (run_canonical → EntryExitEngine CANONICAL),
    in a temporary directory. Returns NaN when no trade occurs (a replicate with no trades is not scored)."""
    import logging
    from bot.research.entry_exit_engine import EntryExitEngine
    from bot.research.kalman_filter_engine import KalmanFilterEngine
    prev = logging.root.manager.disable
    logging.disable(logging.WARNING)
    try:
        with tempfile.TemporaryDirectory() as d:
            KalmanFilterEngine(pair_name=pair_name, symbol_a=symbol_a, symbol_b=symbol_b, ols_beta=1.0, ols_alpha=0.0) \
                .run_canonical(price_a, price_b, output_dir=d)
            kc = Path(d) / "canonical" / f"{symbol_a}_{symbol_b}_kalman.csv"
            EntryExitEngine(pair_name=pair_name, symbol_a=symbol_a, symbol_b=symbol_b, output_dir=d,
                            rule_set="CANONICAL", **entry_exit_kwargs).run(kalman_csv=str(kc))
            tl = pd.read_csv(next(Path(d).glob("*_trade_log.csv")))
    finally:
        logging.disable(prev)
    # claude code changed: 2026-10-04 — the statistic is the D-3 primary endpoint ON ZONE V: decisions inside
    # zone V that are label-complete (t ≤ V_end − (d + H) candles, decision-lock §2.3). Was the mean over the
    # whole trade log (selection-zone trades included), which is not the registered endpoint.
    sub = zone_v_label_complete(tl)
    if len(sub) == 0:
        return float("nan")
    return float(sub["net_pnl_pct"].astype(float).mean())


def zone_v_label_complete(trade_log: pd.DataFrame, protocol=None, zones=None) -> pd.DataFrame:   # claude code changed: new
    """Trades whose ENTRY DECISION lies in zone V and is label-complete inside V (t ≤ V_end − (d + H))."""
    from bot.quant_core.protocol import CANONICAL
    from bot.quant_core.research_zones import ZONES
    protocol, zones = protocol or CANONICAL, zones or ZONES
    if len(trade_log) == 0:
        return trade_log
    t = pd.to_datetime(trade_log["entry_decision_timestamp"], utc=True)
    last = zones.V1 - pd.Timedelta(hours=protocol.execution_delay_candles + protocol.require_max_holding())
    return trade_log.loc[((t >= zones.V0) & (t <= last)).to_numpy()]
