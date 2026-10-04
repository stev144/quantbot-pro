# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/dca_significance_d5.py
#
# claude code changed: new file — 2026-10-03, owner decision D-5 (DECISION_SHEET_D1-D11_2026-10-03.md, signed):
# the DCA significance redesign. Replaces the two-statistic intersection-union test that had ≈ 0 power (F-08).
#
#   STATISTIC   ONE number: MTM net return per unit of maximum deployed capital, open positions liquidated at
#               the data end (dca_engine._mtm_statistics()[1] — reused, not re-implemented)
#   BENCHMARK   a matched-exposure buy-and-hold                        — OWNER DEFINITION PENDING (see below)
#   NULL        stationary block-bootstrapped return paths            — MEAN BLOCK LENGTH PENDING (see below)
#   EFFECTS     planted mean reversion at ladder scale: amplitude 5 % and 10 %; half-lives 24 h, 72 h, 168 h
#   RULE        market data ONLY if calibration shows ≥ 80 % power at the smallest effect of interest;
#               otherwise DCA stays DESCRIPTIVE ONLY, permanently.
#
# OPEN DEFINITIONS (not chosen silently — every one is a required argument with no default):
#   (1) mean block length of the stationary bootstrap;
#   (2) "matched exposure": match the DCA's deployed-capital PATH over time, or its AVERAGE exposure;
#   (3) "amplitude": implemented as the STATIONARY STANDARD DEVIATION of the planted OU log-price component
#       (`amplitude_definition="stationary_sd"`) — confirm or replace;
#   (4) "smallest effect": implemented strictly as amplitude 5 % at EVERY listed half-life.
# Until the owner fixes (1) and (2) the calibration cannot run, so the market test is gated shut.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable, Dict, Optional

import numpy as np
import pandas as pd

D5_VERSION = "QBP-DCA-D5-1"
AMPLITUDES = (0.05, 0.10)
HALF_LIVES_H = (24, 72, 168)
POWER_TARGET = 0.80
SMALLEST_AMPLITUDE = min(AMPLITUDES)
CALIBRATION_ARTIFACT = Path("research_data") / "_governance" / "dca_d5_calibration.json"


class DCANotCalibrated(RuntimeError):
    """D-5: no market-data DCA significance test before calibration shows >= 80 % power at the smallest effect."""


def d5_statistic(df: pd.DataFrame, config) -> float:
    """MTM net return per unit of maximum deployed capital (liquidation mark for open positions)."""
    from bot.research.dca_engine import _mtm_statistics
    return float(_mtm_statistics(df, config)[1])


def planted_mean_reversion_path(n: int, amplitude: float, half_life_h: float, rng: np.random.Generator, *,
                                rw_sigma: float = 0.01, start_price: float = 100.0,
                                amplitude_definition: str = "stationary_sd") -> pd.DataFrame:
    """Hourly OHLC-close frame: log P_t = random walk + OU deviation x_t with half-life `half_life_h` and stationary
    standard deviation `amplitude` (definition (3) above). amplitude = 0 is the null (pure random walk)."""
    if amplitude_definition != "stationary_sd":
        raise ValueError("only amplitude_definition='stationary_sd' is implemented (owner confirmation pending)")
    phi = math.exp(-math.log(2) / half_life_h)
    eps_sd = amplitude * math.sqrt(1 - phi ** 2)
    x = np.zeros(n)
    x[0] = rng.normal(0, amplitude) if amplitude > 0 else 0.0
    shocks = rng.normal(0, 1, n)
    for t in range(1, n):
        x[t] = phi * x[t - 1] + eps_sd * shocks[t]
    lp = math.log(start_price) + np.cumsum(rng.normal(0, rw_sigma, n)) + x
    close = np.exp(lp)
    idx = pd.date_range("2022-01-01", periods=n, freq="h", tz="UTC")
    opens = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, close), "low": np.minimum(opens, close),
                         "close": close, "volume": 1.0}, index=idx)


def require_power(artifact: Path = CALIBRATION_ARTIFACT) -> dict:
    """Gate for any market-data DCA significance run (D-5 rule)."""
    if not Path(artifact).exists():
        raise DCANotCalibrated(f"no D-5 calibration artifact at {artifact}: DCA stays descriptive only until "
                               f"calibration shows >= {POWER_TARGET:.0%} power at amplitude {SMALLEST_AMPLITUDE:.0%}")
    cal = json.loads(Path(artifact).read_text(encoding="utf-8"))
    if cal.get("version") != D5_VERSION:
        raise DCANotCalibrated(f"calibration artifact version {cal.get('version')} != {D5_VERSION}")
    power = cal.get("power", {})
    needed = [f"amp{SMALLEST_AMPLITUDE}_hl{h}" for h in HALF_LIVES_H]
    short = {k: power.get(k) for k in needed if power.get(k) is None or power[k] < POWER_TARGET}
    if short:
        raise DCANotCalibrated(f"power below {POWER_TARGET:.0%} (or missing) at the smallest effect: {short} — "
                               f"DCA remains DESCRIPTIVE ONLY (D-5)")
    return cal


def run_d5_calibration(*, mean_block: float, benchmark: Callable[[pd.DataFrame, object], float], config,
                       reps: int, n_boot: int, n_candles: int, seed: int,
                       out_path: Path = CALIBRATION_ARTIFACT) -> Dict:   # pragma: no cover — compute-heavy, owner-gated
    """Size at amplitude 0 and power at every (amplitude, half-life). `mean_block` and `benchmark` are the open
    owner definitions (1) and (2) — no defaults on purpose. Not executed by this implementation."""
    if not (mean_block and mean_block >= 1) or benchmark is None:
        raise ValueError("mean_block and benchmark are owner definitions (D-5 open items 1–2); pass them explicitly")
    raise NotImplementedError("D-5 calibration is gated on the owner fixing the bootstrap mean block length and the "
                              "matched-exposure benchmark definition; it is intentionally not run")
