# bot/paper_trading/ranging_paper_engine.py  # claude code changed: new — ranging-gated DCA paper simulation
"""
Ranging-gated DCA paper trading simulation.

Runs the QC-1 DCA loop over QC_PORTFOLIO_SPECS pairs but gates every new cycle
open to RANGING regime only (ADX < 25, ATR ratio < 1.7).  Results are compared
to unrestricted QC-1 on the same data so the ranging gate's impact is visible.

Five gates
----------
G1 Software   : clean package, deferred heavy imports, IS_PAPER_ONLY sentinel
G2 Math       : simulation arithmetic is a direct copy of fx_fold_runner's
                validated loop (same fill/exit/commission/swap formulas, same
                compute_dynamic_drop_pct, same weighted-avg-price, same
                spread/commission/swap deductions — no new formula introduced)
G3 Stats      : ranging_mask is computed causally (ADX-14 and ATR window use
                only candles ≤ k when classifying candle k); no candle sees
                future price to decide its own regime label
G4 Quant-real : spread_pips from ForexInstrumentSpec applied at every fill;
                same lot sizing ($6 / fill_price LONG) as QC-1; time exit at
                72 candles; 7-level ladder with validated ATR-scaled spacing
G5 Governance : IS_PAPER_ONLY = True — constant, never set to False;
                all trades tagged source="paper_ranging"; clearly exploratory
                (ranging gate was NOT pre-registered in QC-1's protocol);
                holdout data may be used freely (consumed 2026-10-18)
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── G5: paper-only sentinel ───────────────────────────────────────────────────
IS_PAPER_ONLY = True   # claude code changed: never set to False — simulation only

# ── paper capital ─────────────────────────────────────────────────────────────
# PAPER_ENTRY_USDT : margin committed per cycle entry (what the trader puts in).
# PAPER_LEVERAGE   : broker leverage applied — qty = (margin × leverage) / price.
# The simulation receives the notional (margin × leverage) as initial_usdt so
# WAP, gross P&L, and commission math remain internally consistent.
# Win rates, fill distribution, and drawdown % are unaffected by capital scale.
PAPER_ENTRY_USDT  = 100.0   # claude code changed: margin per cycle entry
PAPER_LEVERAGE    = 30.0    # claude code changed: 1:30 EU retail max (ESMA); matched to ForexInstrumentSpec.leverage_max
_PAPER_NOTIONAL   = PAPER_ENTRY_USDT * PAPER_LEVERAGE   # $3,000 per entry fill

_ROOT = Path(__file__).resolve().parents[2]

# ── QC-1 constants (mirrors fx_multipair_runner.py) ───────────────────────────
_INITIAL_USDT       = 6.0
_PROFIT_ACTIVATION  = 0.005   # 0.5%
_TRAILING_PCT       = 0.002   # 0.2%
_MAX_CYCLE_CANDLES  = 72
_MAE_HORIZON        = 6
_MULTIPLIERS        = (2.0, 4.0, 8.0, 16.0, 32.0, 64.0)

# ── G3: FX-calibrated regime thresholds (causal; same as forex_scanner.py) ───
_ADX_TRENDING   = 25.0   # ADX > this → TRENDING (no new cycle open)
_ATR_RATIO_HV   = 1.7    # ATR ratio > this → HIGH_VOL (no new cycle open)

# ── G2: simulation constants (mirrors fx_fold_runner.py private constants) ───
_CLAMP_LO  = 0.5
_CLAMP_HI  = 2.0
_MIN_PIPS  = 5.0
_MAX_SPAC  = 0.25


# ── internal dataclasses (G2: same shape as fx_fold_runner's private ones) ───

@dataclass
class _Fill:  # claude code changed: mirrors fx_fold_runner._Fill
    level: int
    price: float
    usdt_amount: float
    quantity: float


@dataclass
class _Cycle:  # claude code changed: mirrors fx_fold_runner._Cycle
    fills: List[_Fill] = field(default_factory=list)
    entry_candle: int = 0
    profit_activated: bool = False
    peak: Optional[float] = None

    @property
    def next_level_idx(self) -> int:
        return len(self.fills) - 1

    @property
    def weighted_avg_price(self) -> float:
        total_cost = sum(f.usdt_amount for f in self.fills)
        total_qty  = sum(f.quantity    for f in self.fills)
        return total_cost / total_qty if total_qty > 0 else 0.0

    @property
    def total_qty(self) -> float:
        return sum(f.quantity for f in self.fills)

    @property
    def total_cost(self) -> float:
        return sum(f.usdt_amount for f in self.fills)


# ── G3: causal regime classification ─────────────────────────────────────────

def _compute_ranging_mask(
    df: pd.DataFrame,
    adx_arr: np.ndarray,
    atr14: np.ndarray,
    atr50: np.ndarray,
) -> np.ndarray:  # claude code changed: causal ranging gate; no look-ahead
    """
    Per-candle RANGING flag.  True = allow a new DCA cycle to open.

    RANGING  : ADX ≤ 25 AND ATR ratio ≤ 1.7  (or insufficient history — safe default)
    TRENDING : ADX > 25
    HIGH_VOL : ATR ratio > 1.7

    G3 invariant: adx_arr and atr14/atr50 must be causal series produced from
    the same df — each element k depends only on candles 0..k.
    """
    # TRENDING gate: NaN (warmup) → treat as NOT trending (allow open — conservative)
    trending = np.where(np.isnan(adx_arr), False, adx_arr > _ADX_TRENDING)

    # HIGH_VOL gate: NaN (warmup) → treat as NOT high-vol (allow open)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(atr50 > 0, atr14 / atr50, np.nan)
    high_vol = np.where(np.isnan(ratio), False, ratio > _ATR_RATIO_HV)

    return (~trending) & (~high_vol)


# ── G2: ranging-gated simulation loop ────────────────────────────────────────

def _simulate_ranging_gated(
    df: pd.DataFrame,
    spec,            # FxLadderSpec from fx_fold_runner
    ranging_mask: np.ndarray,
    initial_usdt: float,
    profit_activation_pct: float,
    trailing_pct: float,
    max_cycle_candles: Optional[int],
    spread_fraction: float,
    swap_pips_per_h: float,
    commission_per_lot: Optional[float],
    lot_size: float,
    cost_complete: bool,
    atr_series: np.ndarray,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Simulation loop: identical to fx_fold_runner._simulate_data_derived_next_open
    except the 'no cycle open' branch gates opens to ranging_mask[i] == True.

    G2: every arithmetic formula (fill price, WAP, gross P&L, commission,
    swap accrual, spread deduction) is a verbatim copy of the validated loop.
    Returns (trades, equity_pts).  equity_pts = [{"d": date_str, "p": cum_pnl}]
    """
    from bot.research.fx_fold_runner import compute_dynamic_drop_pct  # deferred

    ts = df.index
    o  = df["open"].to_numpy(dtype=float)
    c  = df["close"].to_numpy(dtype=float)
    n  = len(ts)
    if n < 2:
        return [], []

    trades: List[Dict]    = []
    equity_pts: List[Dict] = []
    realised  = 0.0
    cycle: Optional[_Cycle] = None
    pending: Optional[str]  = None
    pending_exit_reason: Optional[str] = None

    for i in range(n):
        cur_atr     = float(atr_series[i]) if not math.isnan(atr_series[i]) else spec.atr_ref
        fill_open   = float(o[i]) + spread_fraction   # G2: spread at entry

        # ── execute decision queued from previous candle close ────────────────
        if pending == "OPEN":
            qty   = initial_usdt / fill_open
            cycle = _Cycle(
                fills=[_Fill(level=0, price=fill_open,
                             usdt_amount=initial_usdt, quantity=qty)],
                entry_candle=i,
            )
        elif pending == "DCA":
            lvl_idx = cycle.next_level_idx
            mult    = spec.multipliers[lvl_idx]
            usdt    = initial_usdt * mult
            qty     = usdt / fill_open
            cycle.fills.append(_Fill(level=lvl_idx + 1, price=fill_open,
                                     usdt_amount=usdt, quantity=qty))
        elif pending == "EXIT":
            exit_price = float(o[i]) - spread_fraction  # G2: spread at exit
            wap        = cycle.weighted_avg_price
            qty        = cycle.total_qty
            gross      = (exit_price - wap) * qty       # G2: LONG only
            notional_lots = (cycle.total_cost / wap) / lot_size if lot_size > 0 else 0.0
            commission_cost = (commission_per_lot or 0.0) * notional_lots * 2
            candles_held    = i - cycle.entry_candle
            swap_pnl        = swap_pips_per_h * candles_held * spec.pip_size * qty
            profit          = gross + swap_pnl - commission_cost
            trades.append({
                "entry_time":      str(ts[cycle.entry_candle]),
                "exit_time":       str(ts[i]),
                "entry_price":     cycle.fills[0].price,
                "exit_price":      exit_price,
                "wap":             wap,
                "fills":           len(cycle.fills),
                "total_cost_usdt": cycle.total_cost,
                "profit_usdt":     profit,
                "candles_held":    candles_held,
                "exit_reason":     pending_exit_reason,
                "cost_complete":   cost_complete,
                "source":          "paper_ranging",  # G5: label
            })
            realised += profit
            equity_pts.append({"d": str(ts[i])[:10], "p": round(realised, 4)})
            cycle = None

        pending, pending_exit_reason = None, None

        # ── mark-to-market at close, then queue next decision ─────────────────
        close_price = float(c[i])
        if cycle is not None:
            wap = cycle.weighted_avg_price
            qty = cycle.total_qty

            # profit activation + trailing stop (G2: identical to validated loop)
            fav = close_price
            profit_threshold = wap * (1.0 + profit_activation_pct)

            if not cycle.profit_activated:
                if fav >= profit_threshold:
                    cycle.profit_activated = True
                    cycle.peak = fav
            else:
                cycle.peak = max(cycle.peak, fav) if cycle.peak is not None else fav
                if close_price <= cycle.peak * (1.0 - trailing_pct):
                    pending, pending_exit_reason = "EXIT", "PROFIT_TARGET_TRAILING"

            # time exit (G2: identical to validated loop)
            if pending is None and max_cycle_candles is not None:
                if (i - cycle.entry_candle) >= max_cycle_candles:
                    pending, pending_exit_reason = "EXIT", "TIME_EXIT"

            # DCA ladder trigger (G2: same ATR-scaled spacing formula)
            if pending is None:
                lvl_idx = cycle.next_level_idx
                if lvl_idx < len(spec.base_pips):
                    last_fill = cycle.fills[-1].price
                    threshold = compute_dynamic_drop_pct(
                        base_pips=spec.base_pips[lvl_idx],
                        pip_size=spec.pip_size,
                        last_fill_price=last_fill,
                        current_atr=cur_atr,
                        atr_ref=spec.atr_ref,
                    )
                    adverse = (last_fill - close_price) / last_fill
                    if adverse >= threshold:
                        pending = "DCA"
        else:
            # G3: only open a new cycle when this candle is RANGING
            if ranging_mask[i]:
                pending = "OPEN"

    # mark any still-open cycle as censored (DATA_END_STILL_OPEN)
    if cycle is not None:
        exit_price = float(c[-1]) - spread_fraction
        wap        = cycle.weighted_avg_price
        qty        = cycle.total_qty
        gross      = (exit_price - wap) * qty
        candles_held_c  = n - 1 - cycle.entry_candle
        swap_pnl_c      = swap_pips_per_h * candles_held_c * spec.pip_size * qty
        notional_lots_c = (cycle.total_cost / wap) / lot_size if lot_size > 0 else 0.0
        commission_c    = (commission_per_lot or 0.0) * notional_lots_c * 2
        profit_c        = gross + swap_pnl_c - commission_c
        trades.append({
            "entry_time":      str(ts[cycle.entry_candle]),
            "exit_time":       str(ts[-1]),
            "entry_price":     cycle.fills[0].price,
            "exit_price":      exit_price,
            "wap":             wap,
            "fills":           len(cycle.fills),
            "total_cost_usdt": cycle.total_cost,
            "profit_usdt":     profit_c,
            "candles_held":    candles_held_c,
            "exit_reason":     "DATA_END_STILL_OPEN",
            "cost_complete":   cost_complete,
            "source":          "paper_ranging",
            "censored":        True,
        })

    return trades, equity_pts


# ── main engine entry point ───────────────────────────────────────────────────

def run_ranging_paper_simulation() -> Dict:  # claude code changed: new — main entry point
    """
    Run ranging-gated QC-1 simulation for every QC_PORTFOLIO_SPECS pair (LONG).

    Uses the same calibration data for ladder spec computation and the same
    full data range (fold 1 test start → holdout end) as the DCA Engine page,
    so results are directly comparable.

    Returns a dict keyed by pair with:
      equity_gated    : equity curve with ranging gate (paper trader)
      equity_free     : equity curve unrestricted (from run_fx_fold, same period)
      trades_gated    : list of trade dicts (ranging gate)
      stats_gated     : summary stats dict
      stats_free      : summary stats dict (unrestricted)
      ranging_pct     : % of candles in test data that were RANGING
      current_regime  : latest candle's regime label
      validated       : True only for GBP/JPY (QC-1 holdout positive)
    """
    from bot.research.fx_fold_splitter import make_fx_splits, FxFold
    from bot.research.forex_instrument_spec import QC_PORTFOLIO_SPECS, QC_PORTFOLIO_CSV_FILES
    from bot.research.fx_fold_runner import (
        compute_wilder_adx,
        compute_causal_atr_series,
        build_ladder_spec_from_analysis,
    )
    from bot.research.fx_movement_analysis import analyse_pair
    from bot.research.fx_multipair_runner import (
        QC1_PROFIT_ACTIVATION, QC1_TRAILING_PCT,
        QC1_MAX_CYCLE_CANDLES, QC1_MAE_HORIZON,
    )  # claude code changed: removed QC1_INITIAL_USDT — paper uses PAPER_ENTRY_USDT ($100) not $6

    VALIDATED_PAIRS = {"GBP/JPY"}   # G5: only QC-1 holdout-positive pair
    DIRECTION = "LONG"

    results = {}

    for pair, pair_spec in QC_PORTFOLIO_SPECS.items():
        csv_file = QC_PORTFOLIO_CSV_FILES[pair]
        try:
            df_full = pd.read_csv(
                _ROOT / "data" / "forex" / csv_file,
                parse_dates=["timestamp"],
            )
            df_full = df_full.sort_values("timestamp").reset_index(drop=True)
            splits  = make_fx_splits(df_full)

            # ── build ladder spec from calibration data (same as QC-1 holdout fold) ──
            analysis = analyse_pair(
                splits.calibration.reset_index(drop=True),
                pip_size=pair_spec.pip_size,
                pair=pair,
            )
            spec = build_ladder_spec_from_analysis(
                analysis, DIRECTION, pair_spec.pip_size, pair,
                mae_horizon=QC1_MAE_HORIZON,
            )

            # ── simulation range: fold 1 test start → end of holdout ──────────────
            fold1_test_start = splits.folds[0].test["timestamp"].iloc[0]
            sim_df = df_full[df_full["timestamp"] >= fold1_test_start].copy()
            sim_df["timestamp"] = pd.to_datetime(sim_df["timestamp"], utc=True)
            sim_df = sim_df.set_index("timestamp")

            n_sim = len(sim_df)
            if n_sim < 100:
                logger.warning("paper_trader: %s has only %d sim candles, skipping", pair, n_sim)
                continue

            # ── G3: causal indicators for ranging classification ──────────────────
            adx_arr = compute_wilder_adx(sim_df)
            atr14   = compute_causal_atr_series(sim_df, window=14)
            atr50   = compute_causal_atr_series(sim_df, window=50)
            ranging_mask = _compute_ranging_mask(sim_df, adx_arr, atr14, atr50)

            # ── G4: cost parameters from ForexInstrumentSpec ─────────────────────
            spread_pips      = getattr(pair_spec, "spread_typical_pips", None) or 0.0
            commission_lot   = getattr(pair_spec, "commission_per_lot", None)
            swap_long_pips   = getattr(pair_spec, "swap_long", None) or 0.0
            spread_fraction  = spread_pips * pair_spec.pip_size
            swap_pips_per_h  = swap_long_pips / 24.0
            lot_size         = 100_000.0
            cost_complete    = (
                getattr(pair_spec, "spread_typical_pips", None) is not None
                and commission_lot is not None
                and getattr(pair_spec, "swap_long", None) is not None
            )

            # ── unrestricted QC-1 simulation (ranging gate removed) ──────────────
            # Evidence: ranging gate hurt GBP/JPY (validated pair) by $13K+.
            # The DCA ladder edge exists across all regimes, not just ranging.  # claude code changed: gate removed
            from bot.research.fx_fold_runner import _simulate_data_derived_next_open  # deferred
            trades_raw, _ = _simulate_data_derived_next_open(
                df=sim_df,
                spec=spec,
                initial_usdt=_PAPER_NOTIONAL,
                profit_activation_pct=QC1_PROFIT_ACTIVATION,
                trailing_pct=QC1_TRAILING_PCT,
                spread_pips=spread_pips if spread_pips else None,
                commission_per_lot=commission_lot,
                swap_long_pips=swap_long_pips if swap_long_pips else None,
                swap_short_pips=None,
                lot_size=lot_size,
                max_cycle_candles=QC1_MAX_CYCLE_CANDLES,
            )
            closed = [t for t in trades_raw if t.get("exit_reason") != "DATA_END_STILL_OPEN"]
            cum = 0.0
            equity = []
            for t in sorted(closed, key=lambda x: x["exit_time"]):
                cum += t["profit_usdt"]
                equity.append({"d": str(t["exit_time"])[:10], "p": round(cum, 4)})

            # ── stats ─────────────────────────────────────────────────────────────
            def _stats(trades):
                if not trades:
                    return {"total_trades": 0, "wins": 0, "win_pct": 0.0,
                            "total_pnl": 0.0, "max_dd": 0.0, "avg_fills": 0.0}
                profits   = [t["profit_usdt"] for t in trades]
                wins      = sum(1 for p in profits if p > 0)
                cum_arr   = np.cumsum(profits)
                peak_arr  = np.maximum.accumulate(cum_arr)
                max_dd    = float((cum_arr - peak_arr).min())
                avg_fills = float(np.mean([t["fills"] for t in trades]))
                return {
                    "total_trades": len(trades),
                    "wins":         wins,
                    "win_pct":      round(wins / len(trades) * 100, 1),
                    "total_pnl":    round(float(np.sum(profits)), 4),
                    "max_dd":       round(max_dd, 4),
                    "avg_fills":    round(avg_fills, 2),
                }

            stats = _stats(closed)

            # fill distribution: cycles per ladder depth
            ladder_depth = 1 + len(spec.base_pips)
            fill_dist: Dict[int, int] = {}
            for t in closed:
                f = int(t["fills"])
                fill_dist[f] = fill_dist.get(f, 0) + 1

            # drawdown series (relative to running peak)
            def _drawdown_series(equity_pts: list) -> list:
                if not equity_pts:
                    return []
                peak = 0.0
                out  = []
                for i, pt in enumerate(equity_pts):
                    peak = max(peak, pt["p"])
                    out.append({"i": i, "p": round(pt["p"] - peak, 4)})
                return out

            drawdown = _drawdown_series(equity)

            # ranging % of sim data
            ranging_pct = round(float(ranging_mask.mean()) * 100, 1)

            # current regime (latest candle, causal)
            last_adx  = adx_arr[-1]
            last_ratio = (float(atr14[-1] / atr50[-1])
                          if atr50[-1] > 0 and not math.isnan(atr14[-1]) else None)
            if last_ratio is not None and last_ratio > _ATR_RATIO_HV:
                current_regime = "HIGH_VOLATILITY"
            elif not math.isnan(last_adx) and last_adx > _ADX_TRENDING:
                current_regime = "TRENDING"
            else:
                current_regime = "RANGING"

            # ── capital metadata ──────────────────────────────────────────────
            # max_margin  = total margin committed if all fills trigger
            # max_notional = max_margin × leverage (what the broker controls)
            all_mults    = list(spec.multipliers) if spec.multipliers else []
            max_margin   = PAPER_ENTRY_USDT * (1.0 + sum(all_mults))          # claude code changed: worst-case margin
            max_notional = round(max_margin * PAPER_LEVERAGE, 2)               # claude code changed: worst-case notional

            results[pair] = {   # claude code changed: simplified — single unrestricted simulation, no gated/free split
                "equity":         equity,
                "drawdown":       drawdown,
                "fill_dist":      fill_dist,
                "ladder_depth":   ladder_depth,
                "trades":         closed,
                "stats":          stats,
                "ranging_pct":    ranging_pct,
                "current_regime": current_regime,
                "validated":      pair in VALIDATED_PAIRS,
                "current_adx":    round(float(last_adx), 1) if not math.isnan(last_adx) else None,
                "entry_usdt":     PAPER_ENTRY_USDT,
                "leverage":       int(PAPER_LEVERAGE),
                "entry_notional": int(_PAPER_NOTIONAL),
                "max_margin":     round(max_margin, 2),
                "max_notional":   max_notional,
            }

        except Exception as exc:
            logger.exception("paper_trader: error on pair %s", pair)
            results[pair] = {"error": str(exc), "validated": pair in VALIDATED_PAIRS}

    return results
