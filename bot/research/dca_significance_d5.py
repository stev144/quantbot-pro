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


# claude code changed: 2026-10-07 — owner sign-off of DECISION_SHEET_D5_D6_DCA_PROPOSAL_2026-10-07.md ("accept all"):
# the two open D-5 definitions are now fixed.
MEAN_BLOCK_H = 24                       # claude code changed: 2026-10-07 D-5 item 2 (signed) — stationary-bootstrap mean block
SENSITIVITY_BLOCKS_H = (6, 72)          # claude code changed: 2026-10-07 D-5 item 2 (signed) — reported, never used for selection
D5_DECISION_REF = "DECISION_SHEET_D5_D6_DCA_PROPOSAL_2026-10-07.md (owner sign-off 2026-10-07)"   # claude code changed: 2026-10-07


def matched_exposure_benchmark_mtm(df: pd.DataFrame, config, equity: pd.DataFrame) -> dict:   # claude code changed: 2026-10-07 D-5 item 1 (signed)
    """The signed benchmark: a CONSTANT USDT exposure in the same coin equal to the DCA run's time-weighted average
    deployed capital (mean of `capital_deployed` over every candle of the run, 0 while out of a cycle), bought at the
    first open and sold at the last close with the run's own fee and slippage rates."""
    from bot.engines.simulation import apply_slippage
    cap = float(equity["capital_deployed"].astype(float).mean()) if len(equity) else 0.0
    if cap <= 0:
        return {"avg_deployed_capital": cap, "mtm": 0.0}
    side = config.direction
    entry = apply_slippage(float(df["open"].iloc[0]), side, True, config.slippage_rate)
    exit_ = apply_slippage(float(df["close"].iloc[-1]), side, False, config.slippage_rate)
    units = cap / entry
    gross = units * (exit_ - entry) if side == "LONG" else units * (entry - exit_)
    fees = (cap + units * exit_) * config.fee_rate
    return {"avg_deployed_capital": cap, "mtm": float(gross - fees), "entry_fill": entry, "exit_fill": exit_}


def d5_excess_statistic(df: pd.DataFrame, config) -> dict:   # claude code changed: 2026-10-07 D-5 item 1 (signed)
    """(DCA MTM − matched-exposure benchmark MTM) / DCA maximum deployed capital — the signed D-5 test statistic."""
    from bot.research.dca_engine import simulate_dca_path, summarize_dca_cycles
    trades, equity = simulate_dca_path(df, config)
    s = summarize_dca_cycles(trades, config)
    dca_mtm = float(s.get("total_mark_to_market_pnl", 0.0)) if s.get("n_cycles") else 0.0
    dep = float(s.get("max_capital_deployed_any_cycle") or 0.0)
    bench = matched_exposure_benchmark_mtm(df, config, equity)
    return {"statistic": ((dca_mtm - bench["mtm"]) / dep) if dep else None, "dca_mtm": dca_mtm,
            "benchmark_mtm": bench["mtm"], "avg_deployed_capital": bench["avg_deployed_capital"],
            "max_deployed_capital": dep, "n_cycles": s.get("n_cycles", 0),
            "n_censored_cycles": s.get("n_censored_cycles", 0)}


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
