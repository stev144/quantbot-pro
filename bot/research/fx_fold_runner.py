"""
bot/research/fx_fold_runner.py
Walk-forward fold runner for FX DCA calibration (FX-DCA-PROTOCOL-1-A3).

Orchestrates: train data -> per-fold ATR_ref + MAE quantiles ->
              data-derived ATR-scaled ladder -> simulate on test data.

The inner simulation loop here parallels _simulate_fx_next_open in
dca_engine.py but replaces fixed DCALevel.drop_pct with ATR-scaled
spacing computed per-fill from the locked pip thresholds. It does NOT
call or modify any existing simulation function.

Usage:
    from bot.research.fx_fold_runner import run_fx_fold_1_eurusd
    result = run_fx_fold_1_eurusd()
    print_fold_result(result)
"""
# claude code changed: new file — 2026-10-17; FX-DCA-PROTOCOL-1-A3 fold 1 runner

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── protocol constants (from v3/A2, locked in v4/A3) ──────────────────────────
_ATR_WINDOW = 24            # claude code changed: per v3 Step 1
_CLAMP_LO = 0.5             # claude code changed: per v3 Step 3
_CLAMP_HI = 2.0             # claude code changed: per v3 Step 3
_MIN_PIPS = 5.0             # claude code changed: per v3 Step 4
_MAX_SPACING = 0.25         # claude code changed: per v3 Step 5
_MULTIPLIERS = (2.0, 4.0, 8.0, 16.0, 32.0, 64.0)   # claude code changed: levels 2-7, per protocol; unchanged from v1

# ── risk-control constants (v5/A4) ────────────────────────────────────────────
MAX_CYCLE_CANDLES = 720         # claude code changed: v5/A4 — time exit after 30×H=24 candles (30 trading days); see FX_DCA_PROTOCOL_2026-10-16_v5.md Part 3B §1
_MAX_LEVELS = 7                 # claude code changed: v5/A4 — explicit cap: 1 entry + 6 DCA add-ons; already enforced by len(spec.base_pips); documented here per §2
_MAX_DEPLOYED_MULTIPLIER = 127  # claude code changed: v5/A4 — 1+2+4+8+16+32+64; max notional = initial_usdt × 127 = 762 USDT at initial_usdt=6


@dataclass(frozen=True)
class FxLadderSpec:
    """
    Data-derived ladder spec for one pair/direction/fold.

    base_pips: 6-element tuple — level-2 through level-7 base spacings in pips
    (correspond to p40/p60/p75/p85/p92/p97 of the H=24 MAE distribution,
    computed from the fold's training rows only — per-fold, never full-calibration).
    atr_ref: full-price ATR_ref from the same training rows.
    pip_size: instrument pip size (e.g. 0.0001 for EUR/USD, 0.01 for USD/JPY).
    """  # claude code changed: new dataclass — holds per-fold data-derived ladder parameters
    pair: str
    direction: str               # "LONG" | "SHORT"
    pip_size: float
    atr_ref: float               # price-unit ATR_ref from training data
    base_pips: Tuple[float, ...] # 6 values: p40/p60/p75/p85/p92/p97
    multipliers: Tuple[float, ...] = _MULTIPLIERS

    def __post_init__(self):  # claude code changed: basic validation
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError(f"FxLadderSpec.direction must be 'LONG' or 'SHORT', got {self.direction!r}")
        if len(self.base_pips) != 6:
            raise ValueError(f"FxLadderSpec.base_pips must have 6 elements (levels 2-7), got {len(self.base_pips)}")
        if len(self.multipliers) != 6:
            raise ValueError(f"FxLadderSpec.multipliers must have 6 elements, got {len(self.multipliers)}")
        if self.atr_ref <= 0:
            raise ValueError(f"FxLadderSpec.atr_ref must be positive, got {self.atr_ref}")
        if self.pip_size <= 0:
            raise ValueError(f"FxLadderSpec.pip_size must be positive, got {self.pip_size}")


def compute_dynamic_drop_pct(
    base_pips: float,
    pip_size: float,
    last_fill_price: float,
    current_atr: float,
    atr_ref: float,
) -> float:
    """
    Compute the triggered spacing fraction per v3 Steps 3-5.

    triggered_spacing = base_pips * pip_size / last_fill_price
                        * clamp(current_ATR / ATR_ref, 0.5, 2.0)
    then max(triggered, 5 * pip_size / last_fill_price)
    then min(triggered, 0.25)
    """  # claude code changed: new — implements v3 Steps 3-5 exactly
    base_spacing = base_pips * pip_size / last_fill_price
    scale = max(_CLAMP_LO, min(_CLAMP_HI, current_atr / atr_ref))
    triggered = base_spacing * scale
    min_spacing = _MIN_PIPS * pip_size / last_fill_price
    triggered = max(triggered, min_spacing)
    triggered = min(triggered, _MAX_SPACING)
    return triggered


def compute_causal_atr_series(df: pd.DataFrame, window: int = _ATR_WINDOW) -> np.ndarray:
    """
    24-candle causal rolling ATR for the simulation loop.

    ATR per candle = max(H-L, |H-prev_C|, |L-prev_C|)
    Rolling ATR = mean of previous `window` ATR values (causal, no lookahead).
    Returns NaN for the first (window-1) rows.
    """  # claude code changed: new — produces causal ATR series for test data
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    prev_c = np.empty_like(close)
    prev_c[0] = close[0]
    prev_c[1:] = close[:-1]
    atr_raw = np.maximum.reduce([high - low, np.abs(high - prev_c), np.abs(low - prev_c)])
    return pd.Series(atr_raw).rolling(window, min_periods=window).mean().to_numpy()


_ADX_PERIOD = 14          # Wilder's canonical non-trending threshold period (v6/A5)
_ADX_WARMUP = 2 * _ADX_PERIOD  # first 28 candles blocked (2 full Wilder smoothing cycles)
_ADX_THRESHOLD = 25.0     # Wilder's canonical non-trending threshold (v6/A5 A5-2)


def compute_wilder_adx(df: pd.DataFrame, period: int = _ADX_PERIOD) -> np.ndarray:
    """
    Causal Wilder's ADX(14). No look-ahead — safe to use inside simulation loop.

    Returns NaN for the first (2*period - 2) rows (warmup). NaN correctly
    suppresses the ADX gate (NaN ≤ 25 evaluates False).

    Algorithm:
      TR    = max(H-L, |H-prev_C|, |L-prev_C|)
      +DM   = H-prev_H  if > 0 and > (prev_L-L), else 0
      -DM   = prev_L-L  if > 0 and > (H-prev_H), else 0
      Wilder-smooth TR/+DM/-DM over `period` bars
      +DI   = 100 * smooth(+DM) / smooth(TR)
      -DI   = 100 * smooth(-DM) / smooth(TR)
      DX    = 100 * |+DI - -DI| / (+DI + -DI)
      ADX   = Wilder-smooth(DX) over `period` bars
    """  # claude code changed: v6/A5 A5-2 ADX entry gate — causal Wilder ADX(14)
    high  = df["high"].to_numpy(dtype=float)
    low   = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    n = len(close)

    result = np.full(n, np.nan)
    if n < 2 * period:
        return result

    prev_high  = np.concatenate([[high[0]],  high[:-1]])
    prev_low   = np.concatenate([[low[0]],   low[:-1]])
    prev_close = np.concatenate([[close[0]], close[:-1]])

    tr      = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    up_move = high - prev_high
    dn_move = prev_low - low
    plus_dm  = np.where((up_move > dn_move) & (up_move > 0.0), up_move, 0.0)
    minus_dm = np.where((dn_move > up_move) & (dn_move > 0.0), dn_move, 0.0)

    # Wilder initial sum then incremental smoothing
    tr_s  = np.zeros(n)
    pd_s  = np.zeros(n)
    md_s  = np.zeros(n)
    tr_s[period - 1]  = tr[:period].sum()
    pd_s[period - 1]  = plus_dm[:period].sum()
    md_s[period - 1]  = minus_dm[:period].sum()
    for i in range(period, n):
        tr_s[i]  = tr_s[i-1]  - tr_s[i-1]  / period + tr[i]
        pd_s[i]  = pd_s[i-1]  - pd_s[i-1]  / period + plus_dm[i]
        md_s[i]  = md_s[i-1]  - md_s[i-1]  / period + minus_dm[i]

    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di  = np.where(tr_s > 0, 100.0 * pd_s / tr_s, 0.0)
        minus_di = np.where(tr_s > 0, 100.0 * md_s / tr_s, 0.0)
        sum_di   = plus_di + minus_di
        dx       = np.where(sum_di > 0.0, 100.0 * np.abs(plus_di - minus_di) / sum_di, 0.0)

    # ADX = Wilder smooth of DX; first valid DX at index period-1
    adx_start = 2 * period - 2   # 0-indexed; first ADX value
    if adx_start < n:
        result[adx_start] = dx[period - 1 : adx_start + 1].mean()
        for i in range(adx_start + 1, n):
            result[i] = (result[i-1] * (period - 1) + dx[i]) / period
    return result


# ── fill / cycle bookkeeping (minimal, self-contained for this runner) ─────────

@dataclass
class _Fill:
    level: int          # 0 = entry, 1-6 = add-ons
    price: float
    usdt_amount: float
    quantity: float


@dataclass
class _Cycle:
    fills: List[_Fill] = field(default_factory=list)
    entry_candle: int = 0
    profit_activated: bool = False
    peak: Optional[float] = None    # peak favourable price since activation

    @property
    def next_level_idx(self) -> int:   # claude code changed: 0-indexed into spec.base_pips
        return len(self.fills) - 1

    @property
    def weighted_avg_price(self) -> float:
        total_cost = sum(f.usdt_amount for f in self.fills)
        total_qty = sum(f.quantity for f in self.fills)
        return total_cost / total_qty if total_qty > 0 else 0.0

    @property
    def total_qty(self) -> float:
        return sum(f.quantity for f in self.fills)

    @property
    def total_cost(self) -> float:
        return sum(f.usdt_amount for f in self.fills)


def _simulate_data_derived_next_open(
    df: pd.DataFrame,
    spec: FxLadderSpec,
    initial_usdt: float = 6.0,
    profit_activation_pct: float = 0.025,
    trailing_pct: float = 0.005,
    spread_pips: Optional[float] = None,
    commission_per_lot: Optional[float] = None,
    swap_long_pips: Optional[float] = None,
    swap_short_pips: Optional[float] = None,
    lot_size: float = 100_000.0,
    max_cycle_candles: Optional[int] = MAX_CYCLE_CANDLES,
    adx_gate: bool = False,  # claude code changed: v6/A5 A5-2 — True activates ADX(14)≤25 entry gate
) -> Tuple[List[Dict], pd.DataFrame]:
    """
    NEXT_OPEN simulation loop with data-derived ATR-scaled ladder spacing.

    Parallels _simulate_fx_next_open in dca_engine.py but replaces fixed
    DCALevel.drop_pct with compute_dynamic_drop_pct() evaluated at each
    level-trigger check. Does NOT call or delegate to dca_engine functions.

    df must have DatetimeIndex (UTC-aware) and columns: open, high, low, close.
    spread_pips / commission_per_lot / swap_*_pips from ForexInstrumentSpec;
    None means COST_INCOMPLETE.
    max_cycle_candles: force-exit a cycle at next open after this many candles.
    None disables the time exit (reproduces v4/A3 behaviour). Default=MAX_CYCLE_CANDLES.
    adx_gate: v6/A5 A5-2 entry filter. When True, new cycle opens are suppressed
    whenever ADX(14) > _ADX_THRESHOLD (25) OR within the first _ADX_WARMUP (28)
    candles of the fold (NaN ADX values are treated as > threshold). Default False
    preserves v5/A4 behaviour for all callers that don't pass adx_gate=True.
    """  # claude code changed: v6/A5 — added adx_gate parameter for ADX(14)≤25 entry gate (A5-2)
    needed = {"open", "high", "low", "close"}
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(f"_simulate_data_derived_next_open: missing columns {missing}")

    cost_complete = (
        spread_pips is not None
        and commission_per_lot is not None
        and swap_long_pips is not None
        and swap_short_pips is not None
    )  # claude code changed: mirrors FxDcaConfig.is_cost_complete() logic

    # pre-compute causal ATR series for the test data
    atr_series = compute_causal_atr_series(df)

    # pre-compute causal ADX(14) series — only used when adx_gate=True
    adx_series = compute_wilder_adx(df) if adx_gate else None  # claude code changed: v6/A5 A5-2 — computed once, checked per candle

    # use last observed ATR_ref if initial candles lack ATR history
    _fallback_atr = spec.atr_ref

    ts = df.index
    o = df["open"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    n = len(ts)
    if n < 2:
        return [], pd.DataFrame(columns=["timestamp", "realised", "unrealised", "equity", "in_trade"])

    spread_fraction = (spread_pips or 0.0) * spec.pip_size  # claude code changed: spread cost per unit
    swap_pips_per_h = (
        (swap_long_pips if spec.direction == "LONG" else swap_short_pips) or 0.0
    ) / 24.0  # claude code changed: convert daily pips to per-hour

    trades: List[Dict] = []
    rows: List[tuple] = []
    realised = 0.0
    cycle: Optional[_Cycle] = None
    pending: Optional[str] = None   # "OPEN" | "DCA" | "EXIT"
    pending_exit_reason: Optional[str] = None

    for i in range(n):
        current_atr = float(atr_series[i]) if not math.isnan(atr_series[i]) else _fallback_atr
        fill_price_open = float(o[i]) + spread_fraction   # claude code changed: spread applied at fill

        # ── execute decision from previous candle's close ──────────────────
        if pending == "OPEN":
            qty = initial_usdt / fill_price_open
            cycle = _Cycle(
                fills=[_Fill(level=0, price=fill_price_open, usdt_amount=initial_usdt, quantity=qty)],
                entry_candle=i,
            )
        elif pending == "DCA":
            lvl_idx = cycle.next_level_idx               # 0-indexed into base_pips / multipliers
            mult = spec.multipliers[lvl_idx]
            usdt = initial_usdt * mult
            qty = usdt / fill_price_open
            cycle.fills.append(_Fill(level=lvl_idx + 1, price=fill_price_open, usdt_amount=usdt, quantity=qty))
        elif pending == "EXIT":
            # exit fill at open (NEXT_OPEN model)
            exit_price = float(o[i]) - spread_fraction if spec.direction == "LONG" else float(o[i]) + spread_fraction  # claude code changed: spread on exit
            wap = cycle.weighted_avg_price
            qty = cycle.total_qty
            if spec.direction == "LONG":
                gross = (exit_price - wap) * qty
            else:
                gross = (wap - exit_price) * qty
            # commission: per-lot charge at both entry and exit
            notional_lots = (cycle.total_cost / wap) / lot_size if lot_size > 0 else 0.0
            commission_cost = (commission_per_lot or 0.0) * notional_lots * 2  # entry + exit  # claude code changed: round-trip commission
            # swap P&L: MT5 sign convention — negative pips/day = cost to holder,
            # positive = credit. P&L impact = swap_pips_per_h * candles * pip_size * qty.
            # Correct formula: profit = gross + swap_pnl - commission
            # (swap_pnl is negative for EUR/USD LONG — reduces profit; positive for SHORT — adds to profit)
            candles_held = i - cycle.entry_candle
            swap_pnl = swap_pips_per_h * candles_held * spec.pip_size * qty  # claude code changed: sign fix — MT5 sign convention; was -swap_cost
            profit = gross + swap_pnl - commission_cost  # claude code changed: sign fix — add swap_pnl (not subtract)
            trades.append({
                "entry_candle": cycle.entry_candle,
                "exit_candle": i,
                "entry_time": str(ts[cycle.entry_candle]),
                "exit_time": str(ts[i]),
                "entry_price": cycle.fills[0].price,
                "exit_price": exit_price,
                "wap": wap,
                "fills": len(cycle.fills),
                "total_cost_usdt": cycle.total_cost,
                "profit_usdt": profit,
                "gross_usdt": gross,
                "commission_usdt": commission_cost,
                "swap_pnl_usdt": swap_pnl,  # claude code changed: renamed from swap_cost; positive = credit, negative = cost
                "candles_held": candles_held,
                "exit_reason": pending_exit_reason,
                "cost_complete": cost_complete,  # claude code changed: protocol label on each trade
                "direction": spec.direction,
            })
            realised += profit
            cycle = None
        pending, pending_exit_reason = None, None

        # ── mark-to-market at close, then decide ───────────────────────────
        close_price = float(c[i])
        if cycle is not None:
            wap = cycle.weighted_avg_price
            qty = cycle.total_qty
            if spec.direction == "LONG":
                unreal = (close_price - wap) * qty
            else:
                unreal = (wap - close_price) * qty
            rows.append((ts[i], realised, unreal, realised + unreal, True))

            # profit target + trailing stop
            if spec.direction == "LONG":
                fav_price = close_price
                profit_threshold = wap * (1.0 + profit_activation_pct)
            else:
                fav_price = close_price
                profit_threshold = wap * (1.0 - profit_activation_pct)

            if not cycle.profit_activated:
                if (spec.direction == "LONG" and fav_price >= profit_threshold) or \
                   (spec.direction == "SHORT" and fav_price <= profit_threshold):
                    cycle.profit_activated = True
                    cycle.peak = fav_price
            else:
                if cycle.peak is None:
                    cycle.peak = fav_price
                if spec.direction == "LONG":
                    cycle.peak = max(cycle.peak, fav_price)
                    trail_trigger = cycle.peak * (1.0 - trailing_pct)
                    if close_price <= trail_trigger:
                        pending, pending_exit_reason = "EXIT", "PROFIT_TARGET_TRAILING"
                else:
                    cycle.peak = min(cycle.peak, fav_price)
                    trail_trigger = cycle.peak * (1.0 + trailing_pct)
                    if close_price >= trail_trigger:
                        pending, pending_exit_reason = "EXIT", "PROFIT_TARGET_TRAILING"

            # time exit check (v5/A4): fires after profit check, before ladder DCA
            if pending is None and max_cycle_candles is not None:  # claude code changed: v5/A4 — time-based exit per Part 3B §1
                if (i - cycle.entry_candle) >= max_cycle_candles:  # claude code changed: v5/A4 — candles_held >= MAX_CYCLE_CANDLES
                    pending = "EXIT"  # claude code changed: v5/A4 — schedules exit at NEXT_OPEN
                    pending_exit_reason = "TIME_EXIT"  # claude code changed: v5/A4 — distinct label from PROFIT_TARGET_TRAILING

            # ladder level trigger check (ATR-scaled)
            if pending is None:
                lvl_idx = cycle.next_level_idx
                if lvl_idx < len(spec.base_pips):
                    last_fill_price = cycle.fills[-1].price
                    threshold = compute_dynamic_drop_pct(
                        base_pips=spec.base_pips[lvl_idx],
                        pip_size=spec.pip_size,
                        last_fill_price=last_fill_price,
                        current_atr=current_atr,
                        atr_ref=spec.atr_ref,
                    )  # claude code changed: dynamic ATR-scaled spacing per v3 Steps 3-5
                    if spec.direction == "LONG":
                        adverse = (last_fill_price - close_price) / last_fill_price
                    else:
                        adverse = (close_price - last_fill_price) / last_fill_price
                    if adverse >= threshold:
                        pending = "DCA"
        else:
            rows.append((ts[i], realised, 0.0, realised, False))
            # v6/A5 ADX gate: suppress new cycle open if ADX > 25 or in warmup  # claude code changed: v6/A5 A5-2 — NaN (warmup) evaluates as blocked (NaN ≤ 25 = False)
            if adx_gate:
                adx_val = adx_series[i]
                if not (adx_val <= _ADX_THRESHOLD):  # catches NaN and > threshold
                    pass  # stay flat — do not set pending
                else:
                    pending = "OPEN"
            else:
                pending = "OPEN"

    # mark any still-open cycle as censored
    if cycle is not None:
        wap = cycle.weighted_avg_price
        qty = cycle.total_qty
        exit_price = float(c[-1]) - spread_fraction if spec.direction == "LONG" else float(c[-1]) + spread_fraction
        if spec.direction == "LONG":
            gross = (exit_price - wap) * qty
        else:
            gross = (wap - exit_price) * qty
        trades.append({
            "entry_candle": cycle.entry_candle,
            "exit_candle": n - 1,
            "entry_time": str(ts[cycle.entry_candle]),
            "exit_time": str(ts[-1]),
            "entry_price": cycle.fills[0].price,
            "exit_price": exit_price,
            "wap": wap,
            "fills": len(cycle.fills),
            "total_cost_usdt": cycle.total_cost,
            "profit_usdt": gross,
            "gross_usdt": gross,
            "commission_usdt": 0.0,
            "swap_usdt": 0.0,
            "candles_held": n - 1 - cycle.entry_candle,
            "exit_reason": "DATA_END_STILL_OPEN",
            "cost_complete": cost_complete,
            "direction": spec.direction,
            "censored": True,
        })

    mtm = pd.DataFrame(rows, columns=["timestamp", "realised", "unrealised", "equity", "in_trade"])
    return trades, mtm


def build_ladder_spec_from_analysis(
    analysis: dict,
    direction: str,
    pip_size: float,
    pair: str = "",
) -> FxLadderSpec:
    """
    Build FxLadderSpec from the output of fx_movement_analysis.analyse_pair().

    Extracts H=24 MAE quantiles (p40/p60/p75/p85/p92/p97) in pip units
    and the ATR_ref for the given direction.
    """  # claude code changed: new — converts analyse_pair() output to FxLadderSpec
    mae_pips = analysis[direction][24]["mae"]["pips"]  # claude code changed: H=24 per v3 Step 2
    base_pips = (
        mae_pips["p40"],
        mae_pips["p60"],
        mae_pips["p75"],
        mae_pips["p85"],
        mae_pips["p92"],
        mae_pips["p97"],
    )  # claude code changed: levels 2-7 per v3 Table (p40=L2, p60=L3, p75=L4, p85=L5, p92=L6, p97=L7)
    return FxLadderSpec(
        pair=pair or analysis.get("pair", ""),
        direction=direction,
        pip_size=pip_size,
        atr_ref=analysis["atr_ref"],
        base_pips=tuple(float(p) for p in base_pips),
    )


def run_fx_fold(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    pair: str,
    direction: str,
    pip_size: float,
    pair_spec,
    initial_usdt: float = 6.0,
    profit_activation_pct: float = 0.025,
    trailing_pct: float = 0.005,
    fold_num: int = 0,
    max_cycle_candles: Optional[int] = MAX_CYCLE_CANDLES,
    apply_v6_gates: bool = False,  # claude code changed: v6/A5 — True activates carry gate (A5-1) + ADX(14)≤25 gate (A5-2)
) -> Dict:  # claude code changed: v5/A4 — added max_cycle_candles; v6/A5 — added apply_v6_gates
    """
    Run one FX walk-forward fold: compute per-fold ladder spec from training rows,
    then simulate on test rows only.

    Parameters
    ----------
    train_df : calibration training rows for this fold (no holdout data)
    test_df  : calibration test rows for this fold (no holdout data)
    pair     : e.g. "EUR/USD"
    direction: "LONG" or "SHORT"
    pip_size : instrument pip size (0.0001 for EUR/USD, 0.01 for USD/JPY)
    pair_spec: ForexInstrumentSpec from MT5_SPECS (for cost fields)
    fold_num : fold number (1-indexed, for labelling)

    Returns
    -------
    dict with keys: fold_num, pair, direction, spec, trades, mtm, summary
    """  # claude code changed: new — top-level fold runner; train->spec->simulate->results
    from bot.research.fx_movement_analysis import analyse_pair  # claude code changed: import deferred — avoids circular import
    from bot.research.forex_instrument_spec import ForexInstrumentSpec  # claude code changed: type check

    # extract cost fields from pair_spec (needed before carry gate check)
    spread = getattr(pair_spec, "spread_typical_pips", None)
    commission = getattr(pair_spec, "commission_per_lot", None)
    swap_long = getattr(pair_spec, "swap_long", None)
    swap_short = getattr(pair_spec, "swap_short", None)

    # v6/A5 carry gate (A5-1): suppress fold if direction's swap is negative  # claude code changed: v6/A5 A5-1 — carry gate; checked before training analysis for speed
    if apply_v6_gates:
        swap_for_direction = swap_long if direction == "LONG" else swap_short
        if swap_for_direction is not None and swap_for_direction < 0.0:
            # still need spec for the return dict — compute it minimally
            _analysis = analyse_pair(
                train_df.reset_index(drop=True) if train_df.index.dtype != object else train_df,
                pip_size=pip_size, pair=pair)
            _spec = build_ladder_spec_from_analysis(_analysis, direction, pip_size, pair)
            empty_summary = {"n_trades": 0, "total_pnl": 0.0, "win_rate": None,
                             "final_equity": 0.0, "max_drawdown": 0.0}
            return {
                "fold_num": fold_num, "pair": pair, "direction": direction,
                "spec": _spec,
                "trades": [], "mtm": pd.DataFrame(
                    columns=["timestamp","realised","unrealised","equity","in_trade"]),
                "summary": empty_summary, "train_rows": len(train_df),
                "test_rows": len(test_df), "max_cycle_candles": max_cycle_candles,
                "apply_v6_gates": apply_v6_gates,
                "carry_gate_blocked": True,  # claude code changed: v6/A5 A5-1 — signals that fold was suppressed by carry gate
            }

    # compute per-fold ATR_ref and MAE distribution from training rows only
    analysis = analyse_pair(train_df.reset_index(drop=True) if train_df.index.dtype != object else train_df,
                            pip_size=pip_size, pair=pair)  # claude code changed: per-fold; never uses holdout data

    spec = build_ladder_spec_from_analysis(analysis, direction, pip_size, pair)

    # prepare test_df with DatetimeIndex
    sim_df = test_df.copy()
    if "timestamp" in sim_df.columns:
        sim_df["timestamp"] = pd.to_datetime(sim_df["timestamp"], utc=True)
        sim_df = sim_df.set_index("timestamp")
    elif not isinstance(sim_df.index, pd.DatetimeIndex):
        raise ValueError("test_df must have a 'timestamp' column or DatetimeIndex")

    trades, mtm = _simulate_data_derived_next_open(
        sim_df, spec,
        initial_usdt=initial_usdt,
        profit_activation_pct=profit_activation_pct,
        trailing_pct=trailing_pct,
        spread_pips=spread,
        commission_per_lot=commission,
        swap_long_pips=swap_long,
        swap_short_pips=swap_short,
        max_cycle_candles=max_cycle_candles,
        adx_gate=apply_v6_gates,  # claude code changed: v6/A5 A5-2 — pass ADX gate flag through
    )  # claude code changed: v5/A4 — pass max_cycle_candles; v6/A5 — pass adx_gate

    summary = _summarise_trades(trades, spec, mtm=mtm)  # claude code changed: pass mtm for equity stats

    return {
        "fold_num": fold_num,
        "pair": pair,
        "direction": direction,
        "spec": spec,
        "analysis": analysis,
        "trades": trades,
        "mtm": mtm,
        "summary": summary,
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "max_cycle_candles": max_cycle_candles,  # claude code changed: v5/A4 — record time-exit parameter in result
        "apply_v6_gates": apply_v6_gates,  # claude code changed: v6/A5 — record gate state in result
    }


def compute_equity_stats(mtm: pd.DataFrame, trades: List[Dict]) -> Dict:
    """
    Compute equity-curve statistics from the per-candle MTM DataFrame.

    The equity column already includes unrealized P&L every candle, so open-at-end
    positions are naturally included — this is the true economic equity, not just
    the realised P&L of closed trades.

    Returns
    -------
    dict with:
      final_equity       : equity at the last candle (realised + open-cycle MTM)
      min_equity         : lowest equity seen during the fold
      max_drawdown       : largest peak-to-trough drop (negative number; 0 if flat)
      max_drawdown_pct   : max_drawdown / absolute_peak (fraction; NaN if peak <= 0)
      open_at_end_count  : number of cycles still open at period end
      open_at_end_gross  : total gross P&L of open-at-end cycles (unrealised)
    """  # claude code changed: new — equity curve stats including unrealized open positions
    if len(mtm) == 0:
        return {
            "final_equity": 0.0, "min_equity": 0.0,
            "max_drawdown": 0.0, "max_drawdown_pct": float("nan"),
            "open_at_end_count": 0, "open_at_end_gross": 0.0,
        }
    eq = mtm["equity"].to_numpy(dtype=float)
    peak = np.maximum.accumulate(eq)
    drawdown = eq - peak              # always <= 0
    max_dd = float(drawdown.min())    # most negative value = worst drawdown
    abs_peak = float(np.abs(peak).max()) if float(peak.max()) > 0 else float("nan")
    max_dd_pct = max_dd / abs_peak if not math.isnan(abs_peak) and abs_peak > 0 else float("nan")

    open_end = [t for t in trades if t.get("exit_reason") == "DATA_END_STILL_OPEN"]
    open_gross = sum(t.get("gross_usdt", 0.0) for t in open_end)

    return {
        "final_equity": float(eq[-1]),
        "min_equity": float(eq.min()),
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "open_at_end_count": len(open_end),
        "open_at_end_gross": open_gross,
    }  # claude code changed: new — v4 protocol equity stats


def _summarise_trades(trades: List[Dict], spec: FxLadderSpec,
                      mtm: Optional[pd.DataFrame] = None) -> Dict:
    """Aggregate trade list into a summary dict."""  # claude code changed: updated — accepts mtm for equity stats
    closed = [t for t in trades if t.get("exit_reason") != "DATA_END_STILL_OPEN"]
    total = len(closed)
    eq_stats = compute_equity_stats(mtm if mtm is not None else pd.DataFrame(), trades)
    if total == 0:
        return {
            "total_trades": 0, "win_rate": float("nan"),
            "avg_profit_usdt": float("nan"), "total_profit_usdt": 0.0,
            "avg_fills": float("nan"), "avg_candles_held": float("nan"),
            "cost_complete": None,
            **eq_stats,
            "pair": spec.pair, "direction": spec.direction,
        }
    profits = [t["profit_usdt"] for t in closed]
    wins = sum(1 for p in profits if p > 0)
    return {
        "total_trades": total,
        "win_rate": wins / total,
        "avg_profit_usdt": float(np.mean(profits)),
        "median_profit_usdt": float(np.median(profits)),
        "total_profit_usdt": float(np.sum(profits)),
        "avg_fills": float(np.mean([t["fills"] for t in closed])),
        "avg_candles_held": float(np.mean([t["candles_held"] for t in closed])),
        "cost_complete": all(t["cost_complete"] for t in closed),
        **eq_stats,
        "pair": spec.pair,
        "direction": spec.direction,
    }


def print_fold_result(result: Dict) -> None:
    """Print human-readable fold result including equity/drawdown."""  # claude code changed: updated — equity stats added
    s = result["summary"]
    sp = result["spec"]
    print(f"\n{'='*70}")
    print(f"  Fold {result['fold_num']}  |  {result['pair']}  |  {result['direction']}")
    print(f"  Train: {result['train_rows']:,} rows  |  Test: {result['test_rows']:,} rows")
    print(f"  ATR_ref: {sp.atr_ref:.6f} ({sp.atr_ref/sp.pip_size:.2f} pips)")
    print(f"  Base pips (L2-L7): {', '.join(f'{p:.1f}' for p in sp.base_pips)}")
    print(f"{'='*70}")
    print(f"  -- Closed-trade summary --")
    print(f"  Trades closed   : {s['total_trades']}")
    if s["total_trades"] > 0:
        print(f"  Win rate        : {s['win_rate']:.1%}  [NOTE: 100% by construction — no stop-loss]")
        print(f"  Avg profit      : {s['avg_profit_usdt']:.4f} USD")
        print(f"  Median profit   : {s['median_profit_usdt']:.4f} USD")
        print(f"  Total realised  : {s['total_profit_usdt']:.4f} USD")
        print(f"  Avg fills/cycle : {s['avg_fills']:.2f}")
        print(f"  Avg hold        : {s['avg_candles_held']:.0f}h")
    print(f"  -- Equity curve (realised + unrealised every candle) --")
    print(f"  Final equity    : {s['final_equity']:+.4f} USD")
    print(f"  Min equity      : {s['min_equity']:+.4f} USD")
    dd = s['max_drawdown']
    dd_pct = s['max_drawdown_pct']
    dd_pct_s = f"  ({dd_pct:.1%})" if not math.isnan(dd_pct) else ""
    print(f"  Max drawdown    : {dd:+.4f} USD{dd_pct_s}")
    open_n = s['open_at_end_count']
    open_g = s['open_at_end_gross']
    print(f"  Open at end     : {open_n} cycle(s),  gross MTM = {open_g:+.4f} USD")
    print(f"{'='*70}\n")


# ── convenience entry point for fold 1 EUR/USD LONG ───────────────────────────

def run_fx_fold_1_eurusd(direction: str = "LONG") -> Dict:
    """
    Load EUR/USD data, split per protocol, run fold 1 simulation.
    Calibration data only — holdout never touched.
    """  # claude code changed: new — convenience entry point for fold 1 EUR/USD
    _ROOT = Path(__file__).resolve().parents[2]
    csv = _ROOT / "data/forex/EURUSD_1h.csv"

    from bot.research.fx_fold_splitter import make_fx_splits
    from bot.research.forex_instrument_spec import MT5_EURUSD

    df = pd.read_csv(csv, parse_dates=["timestamp"])
    splits = make_fx_splits(df)

    fold = splits.folds[0]   # fold 1 (0-indexed)
    return run_fx_fold(
        train_df=fold.train,
        test_df=fold.test,
        pair="EUR/USD",
        direction=direction,
        pip_size=0.0001,
        pair_spec=MT5_EURUSD,
        fold_num=1,
    )


if __name__ == "__main__":
    import sys
    direction = sys.argv[1].upper() if len(sys.argv) > 1 else "LONG"
    print(f"\nFX DCA Fold 1 — EUR/USD {direction}")
    print("Protocol: FX-DCA-PROTOCOL-1-A3 (calibration data only; holdout sealed)")
    result = run_fx_fold_1_eurusd(direction=direction)
    print_fold_result(result)
    # show first 10 trades
    print("First 10 closed trades:")
    closed = [t for t in result["trades"] if t.get("exit_reason") != "DATA_END_STILL_OPEN"]
    for t in closed[:10]:
        print(f"  [{t['entry_time'][:16]} -> {t['exit_time'][:16]}]  "
              f"fills={t['fills']}  profit={t['profit_usdt']:+.4f}  "
              f"reason={t['exit_reason']}  held={t['candles_held']}h")
