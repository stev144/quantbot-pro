# ============================================================
# bot/research/parity_replay.py
# claude code changed: new file — Stage 2D Phase O (Stage 2C §17).
#
# Deterministic research/live parity replay. Feeds IDENTICAL hourly candles
# through
#   * the research path: KalmanFilterEngine (batch) + EntryExitEngine (scan)
#   * the live path:     bot.pairs.signal_engine.PairSignalEngine (per candle)
# and compares them field by field. No exchange, no orders, no files outside a
# temporary directory. It MEASURES divergence; it never adjusts either side.
#
# Parity requirements come from Stage 2C §17. Where the two implementations
# still differ by design (decision clock, prior vs posterior uncertainty, exit
# rules), the report says so explicitly — those differences are tracked as
# open owner decisions, not hidden.
# ============================================================

from __future__ import annotations

import math
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from bot.pairs.config import PairConfig
from bot.pairs.signal_engine import PairSignalEngine
from bot.research.entry_exit_engine import EntryExitEngine
from bot.research.kalman_filter_engine import KalmanFilterEngine


@dataclass
class FieldParity:
    name: str
    max_abs_diff: float
    n_compared: int
    tolerance: float

    @property
    def matches(self) -> bool:
        return self.n_compared > 0 and self.max_abs_diff <= self.tolerance


@dataclass
class ParityReport:
    fields: Dict[str, FieldParity] = field(default_factory=dict)
    decision_lag_candles: Optional[int] = None          # live decides on candle t−lag's values
    live_entries: List[Tuple[pd.Timestamp, str]] = field(default_factory=list)       # (candle processed, direction)
    research_entries: List[Tuple[pd.Timestamp, str]] = field(default_factory=list)   # (decision cutoff, direction)
    live_exits: List[Tuple[pd.Timestamp, str]] = field(default_factory=list)
    research_exits: List[Tuple[pd.Timestamp, str]] = field(default_factory=list)     # (decision cutoff, reason)
    first_state_divergence: Optional[pd.Timestamp] = None

    def summary(self) -> pd.DataFrame:
        return pd.DataFrame([{"field": f.name, "max_abs_diff": f.max_abs_diff, "n": f.n_compared,
                              "tolerance": f.tolerance, "matches": f.matches} for f in self.fields.values()])


def _config(hedge_ratio: float, intercept: float, win_rate: float, capital_usdt: float) -> PairConfig:
    return PairConfig("X_USDT/Y_USDT", "X_USDT", "Y_USDT", "X/USDT", "Y/USDT",
                      hedge_ratio, intercept, win_rate, capital_usdt, True, "", True)


def _research_batch(close_a: pd.Series, close_b: pd.Series, hedge_ratio: float, intercept: float) -> pd.DataFrame:
    la, lb = np.log(close_a), np.log(close_b)
    eng = KalmanFilterEngine(pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT",
                             ols_beta=hedge_ratio, ols_alpha=intercept)
    batch = eng._run_filter(la, lb, eng._initialise_state(la, lb))
    batch = eng._calculate_dynamic_spread(batch, la, lb)
    batch = eng._calculate_dynamic_zscore(batch)
    return batch


def _cmp(name: str, a: np.ndarray, b: np.ndarray, tol: float) -> FieldParity:
    m = np.isfinite(a) & np.isfinite(b)
    diff = float(np.max(np.abs(a[m] - b[m]))) if m.any() else math.inf
    return FieldParity(name, diff, int(m.sum()), tol)


def replay(close_a: pd.Series, close_b: pd.Series, *, hedge_ratio: float, intercept: float,
           validated_win_rate: float, capital_usdt: float, live_skip: Tuple[int, ...] = (),
           tolerance: float = 1e-10) -> ParityReport:
    """Replay identical candles through research and live.

    close_a/close_b: candle closes on one hourly index (candle open times).
    live_skip: candle positions the live engine never receives (simulates a
    skipped candle); research always sees every candle.
    tolerance: declared numeric equality tolerance for float fields."""
    if not close_a.index.equals(close_b.index):
        raise ValueError("close_a and close_b must share one index")
    n = len(close_a)
    report = ParityReport()
    batch = _research_batch(close_a, close_b, hedge_ratio, intercept)

    # ── live: pure state stepping (natives) on the candles it actually receives ──
    cfg = _config(hedge_ratio, intercept, validated_win_rate, capital_usdt)
    state_engine = PairSignalEngine(cfg)
    nat = {k: np.full(n, np.nan) for k in ("beta_pred", "spread", "z", "unc", "dec_z")}
    for i in range(n):
        if i in live_skip:
            continue
        dec = state_engine._advance_state(float(close_a.iloc[i]), float(close_b.iloc[i]))
        x = state_engine._prev_native
        nat["beta_pred"][i], nat["spread"][i], nat["unc"][i] = x.beta_pred, x.spread, x.beta_uncertainty
        nat["z"][i] = np.nan if x.zscore is None else x.zscore
        nat["dec_z"][i] = np.nan if (dec is None or dec.zscore is None) else dec.zscore

    post_warmup = ~batch["is_warmup"].to_numpy() if "is_warmup" in batch else np.ones(n, bool)
    report.fields["beta_prior"] = _cmp("beta_prior", nat["beta_pred"], batch["kalman_beta_pred"].to_numpy(), tolerance)
    report.fields["innovation"] = _cmp("innovation", np.where(post_warmup, nat["spread"], np.nan),
                                       batch["kalman_spread"].to_numpy(), tolerance)
    report.fields["zscore"] = _cmp("zscore", nat["z"], batch["kalman_zscore"].to_numpy(), tolerance)
    # Stage 2C §17 requires ONE uncertainty definition (posterior P_{t|t}[0,0]):
    report.fields["beta_uncertainty"] = _cmp("beta_uncertainty", nat["unc"], batch["beta_uncertainty"].to_numpy(), tolerance)
    # Decision clock: does live decide at candle t on candle t's z (parity) ...
    report.fields["decision_zscore_same_candle"] = _cmp("decision_zscore_same_candle", nat["dec_z"],
                                                        batch["kalman_zscore"].to_numpy(), tolerance)
    # ... and what lag actually reproduces it (measured, not assumed)
    bz = batch["kalman_zscore"].to_numpy()
    for lag in range(0, 4):
        shifted = np.r_[np.full(lag, np.nan), bz[:n - lag]] if lag else bz
        if _cmp("lag", nat["dec_z"], shifted, tolerance).matches:
            report.decision_lag_candles = lag
            break
    diffs = np.abs(nat["beta_pred"] - batch["kalman_beta_pred"].to_numpy())
    bad = np.where(np.isfinite(diffs) & (diffs > tolerance))[0]
    report.first_state_divergence = close_a.index[bad[0]] if len(bad) else None

    # ── live: full decision path (entries/exits) ──────────────────────────────
    live = PairSignalEngine(cfg)
    for i in range(n):
        if i in live_skip:
            continue
        ts = close_a.index[i]
        d = live.process_candle(ts, float(close_a.iloc[i]), float(close_b.iloc[i]))
        if d.action == "ENTER":
            report.live_entries.append((ts, d.direction))
        elif d.action == "EXIT":
            report.live_exits.append((ts, d.reason))

    # ── research: EntryExitEngine with the live engine's own parameters ────────
    tmp = Path(tempfile.mkdtemp())
    try:
        csv = tmp / "X_USDT_Y_USDT_kalman.csv"
        frame = pd.DataFrame({
            "timestamp": close_a.index,
            "kalman_zscore": batch["kalman_zscore"].to_numpy(),
            "kalman_zscore_lag1": batch["kalman_zscore_lag1"].to_numpy(),
            "pair_signal_dynamic": -batch["kalman_zscore"].to_numpy(),
            "kalman_beta": batch["kalman_beta"].to_numpy(),
            "kalman_spread": batch["kalman_spread"].to_numpy(),
            "prediction_error": batch["prediction_error"].to_numpy(),
            "beta_uncertainty": batch["beta_uncertainty"].to_numpy(),
            "close_a": close_a.to_numpy(), "close_b": close_b.to_numpy(),
        })
        frame.to_csv(csv, index=False)
        le = live.engine
        research = EntryExitEngine(
            output_dir=str(tmp / "out"), validated_win_rate=validated_win_rate,
            capital_usdt=le.sizer.capital, kelly_safety=le.sizer.kelly_safety,
            min_position_usdt=le.sizer.min_position_usdt, exit_time_stop_hours=le.exit_time_stop,
            disable_target_exit=le.disable_target_exit,
            pair_name="X_USDT/Y_USDT", symbol_a="X_USDT", symbol_b="Y_USDT",
        )
        trades, _, _ = research.run(kalman_csv=str(csv))
        for _, t in trades.iterrows():
            report.research_entries.append((pd.Timestamp(t["entry_decision_timestamp"]), t["direction"]))
            report.research_exits.append((pd.Timestamp(t["exit_decision_timestamp"]), t["exit_reason"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return report
