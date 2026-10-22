# bot/research/fx_survival_replay.py
"""
FX DCA capital-survival replay — fixed-capital, ledger-based, margin-aware.   # claude code changed: 2026-10-21 — new file

What it answers: "if the QC-1 forex DCA strategy had been run on a real, fixed-size, leveraged retail account,
with the broker's own spread history, swap and margin rules, would the account have survived?"

Design (DCA_SURVIVAL_AUDIT_2026-10-21.md §5):
  * Strategy logic is NOT re-implemented. Every in-cycle decision goes through
    fx_fold_runner.decide_in_cycle() — the exact function the legacy page uses. Ladder specs come from
    build_ladder_spec_from_analysis() on each walk-forward fold's training rows (same as the page), ATR from
    compute_causal_atr_series(), sizing from forex_accounting.lots_from_allocation().
  * ONE continuous chronological replay from the first fold's test start to the data cutoff. No fold resets:
    a cycle keeps the ladder spec it was opened with and survives fold boundaries; open cycles at the end are
    reported, never dropped.
  * Account: USD balance + per-bar mark-to-market equity, margin used (ESMA 1:30 majors / 1:20 others, margin
    recomputed at the current base->USD rate), free margin, margin level. Orders that the free margin cannot
    fund are REJECTED and recorded — capital is never replenished.
  * Close-out: ESMA retail 50 % margin close-out, checked against each bar's adverse extreme (LONG: the bid low).
    Single position: closed at the price where margin level reaches the threshold, or at the open when the bar
    gaps through it. Several positions: all closed at their adverse extremes (conservative, labelled).
    Negative-balance protection (ESMA) floors the balance at 0 and records the shortfall.
  * Prices: MT5 OHLC are BID prices. LONG buys at ask = bid + spread (+ slippage), sells at bid (- slippage).
    Spread per bar = MT5 MqlRates.spread history (sidecar files from download_mt5_spread_history). Swap = the
    broker's CURRENT points/lot/day applied at every 17:00 New York rollover (x3 on the broker's triple day).
    Commission = UNAVAILABLE from MT5 -> 0 unless set explicitly (stress tests only).
  * Ledger: every cash movement (REALISED_TRADING_PNL, COMMISSION, SWAP, NEGATIVE_BALANCE_PROTECTION) is one
    timestamped row; balance == starting capital + sum(ledger) at every bar (asserted). Spread and slippage are
    embedded in fill prices and itemised in a separate attribution table, so they are never counted twice.

Not modelled (stated, not hidden): tick-level intrabar ordering (1 h bars), partial fills / depth, historical
swap and commission schedules, broker-specific liquidation engines beyond the ESMA rule.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from bot.research.forex_accounting import lots_from_allocation
from bot.research.fx_fold_runner import (
    FxLadderSpec,
    build_ladder_spec_from_analysis,
    compute_causal_atr_series,
    decide_in_cycle,
)
from bot.research.fx_holdout_guard import FX_RESEARCH_DATA_CUTOFF

IS_PAPER_ONLY: bool = True  # governance constant — historical simulation only, never live

_ROOT = Path(__file__).resolve().parents[2]
FX_DIR = _ROOT / "data" / "forex"
SPREAD_DIR = FX_DIR / "spreads"

# ── QC-1 strategy parameters (FX-QC-PROTOCOL-1; values mirror fx_multipair_runner / views/dca_engine.py) ──
QC1_ENTRY_NOTIONAL_USD = 3_000.0   # $100 margin x 30 (page display convention), level-1 notional
QC1_PROFIT_ACTIVATION = 0.005
QC1_TRAILING_PCT = 0.002
QC1_MAX_CYCLE_CANDLES = 72
QC1_MAE_HORIZON = 6

# ── ESMA retail CFD rules (ESMA Decision (EU) 2018/796, Art. 2 & Annex I) ──
ESMA_MAJOR_CURRENCIES = frozenset({"USD", "EUR", "JPY", "GBP", "CAD", "CHF"})
ESMA_LEVERAGE_MAJOR = 30.0
ESMA_LEVERAGE_OTHER = 20.0
ESMA_CLOSE_OUT_LEVEL = 0.50

# cycle outcomes (mutually exclusive) and account / order events (brief §8)
PROFITABLE_EXIT = "PROFITABLE_EXIT"
LOSING_EXIT = "LOSING_EXIT"
OPEN_UNRESOLVED_AT_DATA_END = "OPEN_UNRESOLVED_AT_DATA_END"
LIQUIDATED = "LIQUIDATED"
DCA_LADDER_EXHAUSTED = "DCA_LADDER_EXHAUSTED"
INSUFFICIENT_FREE_CASH = "INSUFFICIENT_FREE_CASH"     # free MARGIN for this leveraged account
ORDER_REJECTED = "ORDER_REJECTED"                     # below broker minimum volume
MARGIN_CALL = "MARGIN_CALL"
BANKRUPTCY_OR_INSOLVENCY = "BANKRUPTCY_OR_INSOLVENCY"
DATA_OR_EXECUTION_UNCERTAINTY = "DATA_OR_EXECUTION_UNCERTAINTY"


def esma_leverage(pair: str) -> float:
    base, quote = pair.split("/")
    return ESMA_LEVERAGE_MAJOR if {base, quote} <= ESMA_MAJOR_CURRENCIES else ESMA_LEVERAGE_OTHER


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class FxCostModel:
    spread_source: str = "MT5_BAR_HISTORY"     # "MT5_BAR_HISTORY" | "NONE"
    spread_multiplier: float = 1.0
    spread_floor_points: float = 0.0
    slippage_pips: float = 0.0                 # adverse, every fill, both sides; no evidence -> 0 in baseline
    commission_per_lot_side_usd: Optional[float] = None   # None = UNAVAILABLE (charged as 0, flagged)
    swap_source: str = "MT5_SNAPSHOT"          # "MT5_SNAPSHOT" | "NONE"
    swap_multiplier: float = 1.0

    @property
    def labels(self) -> Dict[str, str]:
        return {
            "spread": "ESTIMATED (MT5 per-bar spread history; typically bar minimum)" if self.spread_source != "NONE" else "NOT_MODELLED",
            "slippage": f"ASSUMED {self.slippage_pips} pips/fill" if self.slippage_pips else "ASSUMED_ZERO",
            "commission": "UNAVAILABLE (charged as 0)" if self.commission_per_lot_side_usd is None
                          else f"HYPOTHETICAL ${self.commission_per_lot_side_usd}/lot/side",
            "swap": "ESTIMATED (broker's current swap applied to all history)" if self.swap_source != "NONE" else "NOT_MODELLED",
            "liquidation_fee": "NONE (ESMA close-out carries no fee)",
        }


@dataclass(frozen=True)
class AccountConfig:
    starting_capital_usd: float
    close_out_level: float = ESMA_CLOSE_OUT_LEVEL
    margin_call_level: float = 1.00            # broker snapshot margin_so_call = 100 % (warning only)
    leverage_override: Optional[float] = None  # None -> ESMA per pair
    negative_balance_protection: bool = True
    fractional_lots: bool = False              # True only for the legacy-equivalence check


@dataclass(frozen=True)
class StrategyConfig:
    entry_notional_usd: float = QC1_ENTRY_NOTIONAL_USD
    direction: str = "LONG"
    profit_activation_pct: float = QC1_PROFIT_ACTIVATION
    trailing_pct: float = QC1_TRAILING_PCT
    max_cycle_candles: Optional[int] = QC1_MAX_CYCLE_CANDLES
    mae_horizon: int = QC1_MAE_HORIZON
    execution_delay_bars: int = 0             # stress: decisions execute this many bars late


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# Data
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass
class PairData:
    pair: str
    base: str
    quote: str
    ts: pd.DatetimeIndex
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    atr: np.ndarray
    spread_px: np.ndarray        # spread in price units per bar (before multiplier/floor)
    q2usd_open: np.ndarray       # USD per 1 unit of quote currency
    q2usd_close: np.ndarray
    b2usd_open: np.ndarray       # USD per 1 unit of base currency
    b2usd_close: np.ndarray
    pip_size: float
    point: float
    contract_size: float
    min_lot: float
    lot_step: float
    swap_long_points: float
    swap_short_points: float
    triple_weekday: int          # Python weekday (Mon=0) of the triple-swap rollover
    spec_schedule: List[Tuple[pd.Timestamp, FxLadderSpec, str]]   # (active_from, spec, fold label)
    replay_start: pd.Timestamp
    provenance: Dict = field(default_factory=dict)

    def spec_at(self, t: pd.Timestamp) -> Tuple[FxLadderSpec, str]:
        current = self.spec_schedule[0]
        for item in self.spec_schedule:
            if item[0] <= t:
                current = item
        return current[1], current[2]


def _read_ohlc(name: str, cutoff: pd.Timestamp) -> pd.DataFrame:
    path = FX_DIR / name
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df[df["timestamp"] < cutoff].sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    return df


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ccy_usd_series(ccy: str, index: pd.DatetimeIndex, cutoff: pd.Timestamp, self_pair: Optional[pd.DataFrame] = None,
                    self_role: Optional[str] = None) -> Tuple[np.ndarray, np.ndarray, str]:
    """USD per 1 unit of `ccy` at each bar's open and close, causal (last known value at or before the bar)."""
    if ccy == "USD":
        ones = np.ones(len(index))
        return ones, ones, "identity"
    for name, invert in ((f"{ccy}_USD_1h.csv", False), (f"USD_{ccy}_1h.csv", True)):
        if (FX_DIR / name).exists():
            d = _read_ohlc(name, cutoff).set_index("timestamp")
            o = d["open"].reindex(index, method="ffill").to_numpy(dtype=float)
            c = d["close"].reindex(index, method="ffill").to_numpy(dtype=float)
            if np.isnan(o).any() or np.isnan(c).any():
                raise ValueError(f"conversion series {name} does not cover the replay window for {ccy}")
            return (1.0 / o, 1.0 / c, name) if invert else (o, c, name)
    raise FileNotFoundError(f"no {ccy}/USD conversion series in {FX_DIR}")


def latest_broker_snapshot(spread_dir: Path = SPREAD_DIR) -> Tuple[Dict, Path]:
    snaps = sorted(spread_dir.glob("broker_cost_snapshot_*.json"))
    if not snaps:
        raise FileNotFoundError("no broker cost snapshot — run bot.mt5_data_fetcher.download_mt5_spread_history() first")
    return json.loads(snaps[-1].read_text()), snaps[-1]


def build_pair_data(pair: str, *, cutoff: pd.Timestamp = FX_RESEARCH_DATA_CUTOFF, strategy: StrategyConfig = StrategyConfig(),
                    snapshot: Optional[Dict] = None) -> PairData:
    """Load one pair, frozen at `cutoff`, with the page's walk-forward spec schedule (5 folds + consumed holdout)."""
    from bot.research.forex_instrument_spec import QC_PORTFOLIO_CSV_FILES, QC_PORTFOLIO_SPECS
    from bot.research.fx_fold_splitter import FxFold, make_fx_splits
    from bot.research.fx_movement_analysis import analyse_pair

    if cutoff > FX_RESEARCH_DATA_CUTOFF:
        raise ValueError(f"cutoff {cutoff} is past the sealed FX boundary {FX_RESEARCH_DATA_CUTOFF}")
    if snapshot is None:
        snapshot, snap_path = latest_broker_snapshot()
    else:
        snap_path = None
    sym = snapshot["symbols"][pair]
    if sym.get("swap_mode") != 1:
        raise ValueError(f"{pair}: swap_mode {sym.get('swap_mode')} is not POINTS (1) — conversion not implemented")

    csv_name = QC_PORTFOLIO_CSV_FILES.get(pair) or pair.replace("/", "_") + "_1h.csv"
    pip_size = QC_PORTFOLIO_SPECS[pair].pip_size if pair in QC_PORTFOLIO_SPECS else round(sym["point"] * 10, 10)
    df = _read_ohlc(csv_name, cutoff)

    splits = make_fx_splits(df)
    folds = list(splits.folds) + [FxFold(fold_num=6, train=splits.calibration, embargo=splits.calibration_embargo,
                                         test=splits.holdout)]
    schedule = []
    for f in folds:
        analysis = analyse_pair(f.train.reset_index(drop=True), pip_size=pip_size, pair=pair)
        spec = build_ladder_spec_from_analysis(analysis, strategy.direction, pip_size, pair, mae_horizon=strategy.mae_horizon)
        schedule.append((pd.Timestamp(f.test["timestamp"].iloc[0]), spec, "holdout" if f.fold_num == 6 else f"fold{f.fold_num}"))

    atr_full = compute_causal_atr_series(df)
    start = schedule[0][0]
    keep = (df["timestamp"] >= start).to_numpy()
    d = df[keep].reset_index(drop=True)
    index = pd.DatetimeIndex(d["timestamp"])
    base, quote = pair.split("/")

    sp_path = SPREAD_DIR / (pair.replace("/", "_") + "_1h_spread.csv")
    sp = pd.read_csv(sp_path)
    sp["timestamp"] = pd.to_datetime(sp["timestamp"], utc=True)
    sp = sp.drop_duplicates("timestamp").set_index("timestamp").sort_index()
    pts = sp["spread_points"].reindex(index)
    n_missing = int(pts.isna().sum())
    pts = pts.ffill().bfill().to_numpy(dtype=float)

    qo, qc, qsrc = _ccy_usd_series(quote, index, cutoff)
    bo, bc, bsrc = _ccy_usd_series(base, index, cutoff)

    rollover_day = int(sym.get("swap_rollover3days", 3))   # MT5: 0=Sunday ... 3=Wednesday
    return PairData(
        pair=pair, base=base, quote=quote, ts=index,
        o=d["open"].to_numpy(float), h=d["high"].to_numpy(float), l=d["low"].to_numpy(float), c=d["close"].to_numpy(float),
        atr=atr_full[keep], spread_px=pts * float(sym["point"]),
        q2usd_open=qo, q2usd_close=qc, b2usd_open=bo, b2usd_close=bc,
        pip_size=pip_size, point=float(sym["point"]), contract_size=float(sym["trade_contract_size"]),
        min_lot=float(sym["volume_min"]), lot_step=float(sym["volume_step"]),
        swap_long_points=float(sym["swap_long"]), swap_short_points=float(sym["swap_short"]),
        triple_weekday=(rollover_day - 1) % 7,
        spec_schedule=schedule, replay_start=start,
        provenance={
            "ohlc_file": csv_name, "ohlc_sha256": _file_sha256(FX_DIR / csv_name),
            "spread_file": sp_path.name, "spread_sha256": _file_sha256(sp_path), "spread_bars_missing_ffilled": n_missing,
            "quote_usd_source": qsrc, "base_usd_source": bsrc,
            "broker_snapshot": snap_path.name if snap_path else "injected",
            "cutoff": str(cutoff), "replay_start": str(start), "replay_end": str(index[-1]), "bars": len(index),
            "folds": [(str(s[0]), s[2]) for s in schedule],
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# Engine state
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass
class _Fill:
    level: int
    ts: pd.Timestamp
    ref_price: float        # bid at the fill (bar open)
    exec_price: float       # what was actually paid / received
    units: float
    lots: float
    spread_usd: float
    slippage_usd: float
    commission_usd: float
    notional_usd: float

    @property
    def price(self) -> float:   # decide_in_cycle() reads fills[-1].price — the executed price, as in the legacy loop
        return self.exec_price


@dataclass
class _LiveCycle:
    cycle_id: int
    pair: str
    spec: FxLadderSpec
    fold_label: str
    entry_candle: int
    entry_ts: pd.Timestamp
    fills: List[_Fill] = field(default_factory=list)
    profit_activated: bool = False
    peak: Optional[float] = None
    swap_usd: float = 0.0
    min_unreal_usd: float = 0.0
    max_margin_usd: float = 0.0
    max_notional_usd: float = 0.0
    rejected_attempts: int = 0
    exhausted_ts: Optional[pd.Timestamp] = None
    total_units: float = 0.0
    total_lots: float = 0.0
    weighted_avg_price: float = 0.0

    def add_fill(self, f: "_Fill") -> None:
        cost = self.weighted_avg_price * self.total_units + f.exec_price * f.units
        self.fills.append(f)
        self.total_units += f.units
        self.total_lots += f.lots
        self.weighted_avg_price = cost / self.total_units if self.total_units > 0 else 0.0

    @property
    def next_level_idx(self) -> int:
        return len(self.fills) - 1


@dataclass
class _PairState:
    pos: int = 0
    cycle: Optional[_LiveCycle] = None
    pending: Optional[Tuple[str, Optional[str], int]] = None   # (kind, exit_reason, execute_at_index)
    last_ts: Optional[pd.Timestamp] = None
    last_close_idx: int = -1
    last_rejection: Optional[Tuple] = None


class _Account:
    def __init__(self, starting: float):
        self.starting = float(starting)
        self.balance = float(starting)
        self.ledger: List[Dict] = []
        self.insolvent = False

    def post(self, ts, kind: str, amount: float, pair: Optional[str], cycle_id: Optional[int], note: str = "") -> None:
        self.balance += amount
        self.ledger.append({"timestamp": ts, "type": kind, "amount_usd": amount, "pair": pair, "cycle_id": cycle_id,
                            "balance_after": self.balance, "note": note})


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# Replay
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def rollover_schedule(start: pd.Timestamp, end: pd.Timestamp) -> Tuple[np.ndarray, np.ndarray]:
    """17:00 America/New_York rollover instants Mon-Fri between start and end: (UTC int64 ns, NY weekday)."""
    days = pd.date_range(start.tz_convert("America/New_York").normalize().tz_localize(None) - pd.Timedelta(days=1),
                         end.tz_convert("America/New_York").normalize().tz_localize(None) + pd.Timedelta(days=1), freq="D")
    days = days[days.weekday < 5]
    ny = (days + pd.Timedelta(hours=17)).tz_localize("America/New_York")
    return ny.tz_convert("UTC").as_unit("ns").asi8, np.asarray(ny.weekday)   # ns — must match Timestamp.value


def run_replay(pairs: Sequence[PairData], account_cfg: AccountConfig, cost: FxCostModel = FxCostModel(),
               strategy: StrategyConfig = StrategyConfig(), label: str = "") -> Dict:
    """Chronological replay of all `pairs` sharing ONE account. Returns rows/ledger/cycles/events/fills + config."""
    if strategy.direction not in ("LONG", "SHORT"):
        raise ValueError("direction must be LONG or SHORT")
    sign = 1.0 if strategy.direction == "LONG" else -1.0
    acct = _Account(account_cfg.starting_capital_usd)
    states = {p.pair: _PairState() for p in pairs}
    lev = {p.pair: account_cfg.leverage_override or esma_leverage(p.pair) for p in pairs}
    pmap = {p.pair: p for p in pairs}
    comm_rate = cost.commission_per_lot_side_usd or 0.0
    slip_px = {p.pair: cost.slippage_pips * p.pip_size for p in pairs}

    def spread_at(p: PairData, i: int) -> float:
        if cost.spread_source == "NONE":
            return 0.0
        return max(p.spread_px[i] * cost.spread_multiplier, cost.spread_floor_points * p.point)

    timeline = sorted(set().union(*[set(p.ts) for p in pairs]))
    roll_ns, roll_wd = rollover_schedule(timeline[0], timeline[-1]) if timeline else (np.array([], dtype=np.int64), np.array([]))
    cycles_out: List[Dict] = []
    fills_out: List[Dict] = []
    events: List[Dict] = []
    rows: List[Dict] = []
    next_cycle_id = 1
    margin_call_active = False
    cum = {"gross": 0.0, "spread": 0.0, "slippage": 0.0, "commission": 0.0, "swap": 0.0}

    def event(ts, kind, pair=None, cycle_id=None, **kw):
        events.append({"timestamp": ts, "type": kind, "pair": pair, "cycle_id": cycle_id, **kw})

    def mark_price(p: PairData, i: int, which: str) -> float:
        px = {"open": p.o, "close": p.c, "low": p.l, "high": p.h}[which][i]
        return px

    def unreal_usd(p: PairData, cyc: _LiveCycle, bid: float, i: int, rate: float) -> float:
        """Liquidation value of the open position at `bid` (LONG sells at bid - slip; SHORT buys at bid+spread+slip)."""
        if sign > 0:
            exit_px = bid - slip_px[p.pair]
        else:
            exit_px = bid + spread_at(p, i) + slip_px[p.pair]
        return sign * (exit_px - cyc.weighted_avg_price) * cyc.total_units * rate

    def current_index(p: PairData) -> int:
        return states[p.pair].last_close_idx

    def margin_used(prices: Optional[Dict[str, Tuple[int, str]]] = None) -> float:
        """Margin of all open positions; base->USD rate taken at the same (index, open|close) mark as equity_with()."""
        m = 0.0
        for p in pairs:
            cyc = states[p.pair].cycle
            if cyc is None:
                continue
            i, which = (prices or {}).get(p.pair, (current_index(p), "close"))
            rate = (p.b2usd_open if which == "open" else p.b2usd_close)[max(i, 0)]
            m += cyc.total_units * rate / lev[p.pair]
        return m

    def equity_with(prices: Dict[str, Tuple[int, str]]) -> float:
        """balance + sum of unrealised, each open pair marked at prices[pair] = (index, 'open'|'close'|'low'|'high')."""
        e = acct.balance
        for p in pairs:
            cyc = states[p.pair].cycle
            if cyc is None:
                continue
            i, which = prices.get(p.pair, (current_index(p), "close"))
            rate = (p.q2usd_open if which == "open" else p.q2usd_close)[i]
            e += unreal_usd(p, cyc, mark_price(p, i, which), i, rate)
        return e

    def close_cycle(p: PairData, i: int, ts, ref_bid: float, reason: str, outcome_override: Optional[str] = None,
                    rate: Optional[float] = None, extra: Optional[Dict] = None) -> Dict:
        st = states[p.pair]
        cyc = st.cycle
        rate = p.q2usd_open[i] if rate is None else rate
        units = cyc.total_units
        if sign > 0:
            exec_px = ref_bid - slip_px[p.pair]
            exit_spread_usd = 0.0
        else:
            exec_px = ref_bid + spread_at(p, i) + slip_px[p.pair]
            exit_spread_usd = spread_at(p, i) * units * rate
        exit_slip_usd = slip_px[p.pair] * units * rate
        realised = sign * (exec_px - cyc.weighted_avg_price) * units * rate
        acct.post(ts, "REALISED_TRADING_PNL", realised, p.pair, cyc.cycle_id, reason)
        lots = cyc.total_lots
        exit_comm = comm_rate * lots
        if exit_comm:
            acct.post(ts, "COMMISSION", -exit_comm, p.pair, cyc.cycle_id, "exit")
        spread_usd = sum(f.spread_usd for f in cyc.fills) + exit_spread_usd
        slip_usd = sum(f.slippage_usd for f in cyc.fills) + exit_slip_usd
        comm_usd = sum(f.commission_usd for f in cyc.fills) + exit_comm
        gross = realised + spread_usd + slip_usd
        net = realised - comm_usd + cyc.swap_usd
        cum["gross"] += gross; cum["spread"] += exit_spread_usd; cum["slippage"] += exit_slip_usd; cum["commission"] += exit_comm
        outcome = outcome_override or (PROFITABLE_EXIT if net > 0 else LOSING_EXIT)
        rec = {
            "cycle_id": cyc.cycle_id, "pair": p.pair, "direction": strategy.direction, "fold_spec": cyc.fold_label,
            "entry_ts": cyc.entry_ts, "entry_price": cyc.fills[0].exec_price, "exit_ts": ts, "exit_ref_price": ref_bid,
            "exit_exec_price": exec_px, "exit_reason": reason, "outcome": outcome, "fills": len(cyc.fills),
            "lots": lots, "units": units, "wap": cyc.weighted_avg_price, "duration_h": (ts - cyc.entry_ts).total_seconds() / 3600,
            "gross_pnl_usd": gross, "spread_usd": spread_usd, "slippage_usd": slip_usd, "commission_usd": comm_usd,
            "swap_usd": cyc.swap_usd, "net_pnl_usd": net, "realised_trading_usd": realised,
            "mae_usd": min(cyc.min_unreal_usd, realised), "peak_margin_usd": cyc.max_margin_usd,
            "peak_notional_usd": cyc.max_notional_usd, "rejected_order_attempts": cyc.rejected_attempts,
            "ladder_exhausted": cyc.exhausted_ts is not None,
            "notional_traded_usd": sum(f.notional_usd for f in cyc.fills) + units * p.b2usd_open[i],
        }
        if extra:
            rec.update(extra)
        cycles_out.append(rec)
        st.cycle = None
        return rec

    def try_fill(p: PairData, i: int, ts, kind: str, bar: Dict[str, int]) -> None:
        nonlocal next_cycle_id
        st = states[p.pair]
        cyc = st.cycle
        if kind == "OPEN":
            spec, fold_label = p.spec_at(ts)
            level, mult = 0, 1.0
        else:
            spec, fold_label = cyc.spec, cyc.fold_label
            level = len(cyc.fills)
            mult = spec.multipliers[level - 1]
        target = strategy.entry_notional_usd * mult
        b2u = p.b2usd_open[i]
        if account_cfg.fractional_lots:
            units = target / b2u
            lots = units / p.contract_size
        elif kind == "OPEN":
            inst = _SizingSpec(p.pair, p.contract_size, p.lot_step, p.min_lot)
            try:   # entry: broker lot grid, floored (never up-sized)
                lots = lots_from_allocation(allocated_capital=target, entry_price=b2u, spec=inst, leverage=1.0)
            except ValueError as exc:
                if st.last_rejection != ("MIN_VOLUME", None):
                    event(ts, ORDER_REJECTED, p.pair, None, level=1, reason=str(exc)[:160])
                    st.last_rejection = ("MIN_VOLUME", None)
                return
            units = lots * p.contract_size
        else:   # add-on: exact multiple of the EXECUTED entry size, so the 1:2:4:8:16 ladder shape is preserved
            lots = round(cyc.fills[0].lots * mult, 10)
            units = lots * p.contract_size
        need = units * b2u / lev[p.pair]
        marks = {q: (j, "open") for q, j in bar.items()}   # pairs without a bar now stay at their last close
        eq_open = equity_with(marks)
        used = margin_used(marks)
        free = eq_open - used
        if need > free + 1e-9:
            key = (cyc.cycle_id if cyc else None, level)
            if st.last_rejection != key:   # one event per (cycle, level) / per flat spell; attempts still counted
                event(ts, INSUFFICIENT_FREE_CASH, p.pair, key[0], level=level + 1,
                      required_margin_usd=need, free_margin_usd=free, equity_usd=eq_open)
                st.last_rejection = key
            if cyc:
                cyc.rejected_attempts += 1
            return
        st.last_rejection = None
        ref = p.o[i]
        rate = p.q2usd_open[i]
        sp = spread_at(p, i)
        if sign > 0:
            exec_px = ref + sp + slip_px[p.pair]
            sp_usd = sp * units * rate
        else:
            exec_px = ref - slip_px[p.pair]
            sp_usd = 0.0
        sl_usd = slip_px[p.pair] * units * rate
        comm = comm_rate * lots
        if kind == "OPEN":
            cyc = _LiveCycle(cycle_id=next_cycle_id, pair=p.pair, spec=spec, fold_label=fold_label, entry_candle=i, entry_ts=ts)
            next_cycle_id += 1
            st.cycle = cyc
        if comm:
            acct.post(ts, "COMMISSION", -comm, p.pair, cyc.cycle_id, f"fill L{level + 1}")
        f = _Fill(level=level, ts=ts, ref_price=ref, exec_price=exec_px, units=units, lots=lots, spread_usd=sp_usd,
                  slippage_usd=sl_usd, commission_usd=comm, notional_usd=units * b2u)
        cyc.add_fill(f)
        cum["spread"] += sp_usd; cum["slippage"] += sl_usd; cum["commission"] += comm
        cyc.max_notional_usd = max(cyc.max_notional_usd, cyc.total_units * b2u)
        cyc.max_margin_usd = max(cyc.max_margin_usd, cyc.total_units * b2u / lev[p.pair])
        fills_out.append({"cycle_id": cyc.cycle_id, "pair": p.pair, "timestamp": ts, "level": level + 1, "lots": lots,
                          "ref_price": ref, "exec_price": exec_px, "spread_usd": sp_usd, "slippage_usd": sl_usd,
                          "commission_usd": comm, "notional_usd": units * b2u, "margin_required_usd": need,
                          "free_margin_before_usd": free})
        if len(cyc.fills) == 1 + len(spec.multipliers):
            cyc.exhausted_ts = ts
            event(ts, DCA_LADDER_EXHAUSTED, p.pair, cyc.cycle_id, level=len(cyc.fills),
                  notional_usd=cyc.total_units * b2u, margin_usd=cyc.total_units * b2u / lev[p.pair])

    def entry_margin_needed(p: PairData, i: int) -> float:
        b2u = p.b2usd_close[i]
        if account_cfg.fractional_lots:
            return strategy.entry_notional_usd / lev[p.pair]
        try:
            lots = lots_from_allocation(strategy.entry_notional_usd, b2u, _SizingSpec(p.pair, p.contract_size, p.lot_step, p.min_lot), 1.0)
        except ValueError:
            lots = p.min_lot
        return lots * p.contract_size * b2u / lev[p.pair]

    def check_insolvency(ts, bar: Dict[str, int], rule: str) -> None:
        if acct.insolvent or any(states[q.pair].cycle is not None for q in pairs):
            return
        needs = min(entry_margin_needed(q, bar.get(q.pair, max(current_index(q), 0))) for q in pairs)
        if acct.balance < needs:
            acct.insolvent = True
            event(ts, BANKRUPTCY_OR_INSOLVENCY, balance_usd=acct.balance, min_entry_margin_usd=needs, rule=rule)

    for ts in timeline:
        bar = {}
        for p in pairs:
            st = states[p.pair]
            if st.pos < len(p.ts) and p.ts[st.pos] == ts:
                bar[p.pair] = st.pos

        # 0. swap at rollovers between this pair's previous bar and this bar (positions held before this bar's fills)
        if cost.swap_source != "NONE":
            for name, i in bar.items():
                p, st = pmap[name], states[name]
                cyc = st.cycle
                if cyc is not None and st.last_ts is not None:
                    pts = p.swap_long_points if sign > 0 else p.swap_short_points
                    a_ns = max(st.last_ts, cyc.fills[0].ts).value
                    lo, hi = np.searchsorted(roll_ns, a_ns, "right"), np.searchsorted(roll_ns, ts.value, "right")
                    for k in range(lo, hi):   # rollovers in (max(prev bar, first fill), this bar]
                        mult = 3 if roll_wd[k] == p.triple_weekday else 1
                        amt = pts * p.point * p.contract_size * cyc.total_lots * mult * p.q2usd_close[max(i - 1, 0)] * cost.swap_multiplier
                        if amt:
                            acct.post(ts, "SWAP", amt, name, cyc.cycle_id, f"rollover {pd.Timestamp(roll_ns[k], tz='UTC').isoformat()} x{mult}")
                            cyc.swap_usd += amt
                            cum["swap"] += amt

        # 1. scheduled executions at this bar's open — exits first (they free margin), then entries/add-ons
        for phase in ("EXIT", "FILL"):
            for name, i in bar.items():
                p, st = pmap[name], states[name]
                if st.pending is None or st.pending[2] > i:
                    continue
                kind, reason, _ = st.pending
                if phase == "EXIT" and kind == "EXIT" and st.cycle is not None:
                    st.pending = None
                    close_cycle(p, i, ts, p.o[i], reason)
                elif phase == "FILL" and kind in ("OPEN", "DCA"):
                    st.pending = None
                    if kind == "DCA" and st.cycle is None:
                        continue
                    if kind == "OPEN" and acct.insolvent:
                        continue
                    try_fill(p, i, ts, kind, bar)
        for name, i in bar.items():
            states[name].last_close_idx = i   # from here on this bar is the pair's "current" bar

        # 2. ESMA close-out against this bar's adverse extreme
        open_pairs = [q for q in pairs if states[q.pair].cycle is not None]
        if open_pairs:
            worst = {q.pair: (bar[q.pair], "low" if sign > 0 else "high") if q.pair in bar else (current_index(q), "close")
                     for q in open_pairs}
            used = margin_used({q: (j, "close") for q, j in bar.items()})
            eq_worst = equity_with(worst)
            for q in open_pairs:   # track each cycle's MAE at its own adverse extreme
                i_q, which = worst[q.pair]
                cyc_q = states[q.pair].cycle
                u = unreal_usd(q, cyc_q, mark_price(q, i_q, which), i_q, q.q2usd_close[i_q])
                cyc_q.min_unreal_usd = min(cyc_q.min_unreal_usd, u)
            if used > 0 and eq_worst / used <= account_cfg.close_out_level:
                eq_before = equity_with({q.pair: (bar[q.pair], "open") for q in open_pairs if q.pair in bar})
                remaining = list(open_pairs)
                # ESMA close-out, broker practice: close the LARGEST losing position first, re-check the margin level,
                # stop as soon as it is back above the threshold. The last remaining position closes at its exact trigger.
                while remaining:
                    used = margin_used({q: (j, "close") for q, j in bar.items()})
                    if used <= 0 or equity_with(worst) / used > account_cfg.close_out_level:
                        break
                    q = min(remaining, key=lambda x: unreal_usd(x, states[x.pair].cycle, mark_price(x, *worst[x.pair]),
                                                                worst[x.pair][0], x.q2usd_close[worst[x.pair][0]]))
                    remaining.remove(q)
                    cyc_q = states[q.pair].cycle
                    i_q = bar.get(q.pair, current_index(q))
                    rate = q.q2usd_close[i_q]
                    if not remaining and q.pair in bar and sign > 0:
                        units = cyc_q.total_units
                        trigger = cyc_q.weighted_avg_price + slip_px[q.pair] + \
                            (account_cfg.close_out_level * used - acct.balance) / (units * rate)
                        gap = q.o[i_q] <= trigger
                        exec_bid = q.o[i_q] if gap else trigger
                        rule = "margin level <= {:.0%} (ESMA close-out){}".format(account_cfg.close_out_level, "; gapped through at open" if gap else "")
                        confidence = "1h bar: trigger reached intrabar; exact path unknown" + ("; fill at open (gap)" if gap else "; fill at trigger price (no slippage beyond model)")
                    else:
                        exec_bid = mark_price(q, i_q, worst[q.pair][1])
                        trigger = exec_bid
                        rule = "margin level <= {:.0%} (ESMA close-out)".format(account_cfg.close_out_level)
                        confidence = ("several positions: largest loser closed first at its bar extreme, others re-checked "
                                      "(conservative approximation — assumes the pairs' extremes coincide)"
                                      if remaining else "short/no-bar position closed at bar extreme (approximation)")
                    floating_before = unreal_usd(q, cyc_q, exec_bid, i_q, rate)
                    rec = close_cycle(q, i_q, ts, exec_bid, "MARGIN_CLOSE_OUT", outcome_override=LIQUIDATED, rate=rate, extra={
                        "trigger_price": trigger, "equity_before_usd": eq_before, "margin_used_usd": used,
                        "maintenance_requirement_usd": account_cfg.close_out_level * used,
                        "floating_loss_before_usd": floating_before, "rule": rule, "confidence": confidence,
                    })
                    event(ts, LIQUIDATED, q.pair, rec["cycle_id"], equity_before_usd=eq_before, margin_used_usd=used,
                          net_pnl_usd=rec["net_pnl_usd"], rule=rule)
                    event(ts, DATA_OR_EXECUTION_UNCERTAINTY, q.pair, rec["cycle_id"], note=confidence)
                    states[q.pair].pending = None
                if acct.balance < 0 and account_cfg.negative_balance_protection:
                    acct.post(ts, "NEGATIVE_BALANCE_PROTECTION", -acct.balance, None, None, "ESMA negative balance protection")
                    event(ts, "NEGATIVE_BALANCE_PROTECTION", shortfall_usd=acct.ledger[-1]["amount_usd"])
                check_insolvency(ts, bar, "after close-out: balance below the margin of the strategy's entry order; no deposits are modelled")

        if bar:
            check_insolvency(ts, bar, "flat account: balance below the margin of the strategy's entry order; no deposits are modelled")

        # 3. mark at close + decisions
        for name, i in bar.items():
            p, st = pmap[name], states[name]
            cyc = st.cycle
            st.last_ts = ts
            st.pos = i + 1   # advance before any early exit: a pending (delayed) order must not freeze the pair
            if st.pending is not None:
                continue
            if cyc is not None:
                atr = p.atr[i] if not math.isnan(p.atr[i]) else cyc.spec.atr_ref
                pending, reason = decide_in_cycle(cyc, p.c[i], i, cyc.spec, atr,
                                                  strategy.profit_activation_pct, strategy.trailing_pct, strategy.max_cycle_candles)
                if pending:
                    st.pending = (pending, reason, i + 1 + strategy.execution_delay_bars)
            elif not acct.insolvent:
                st.pending = ("OPEN", None, i + 1 + strategy.execution_delay_bars)

        # 4. account row
        eq_close = equity_with({})
        used = margin_used()
        level = eq_close / used if used > 0 else None
        if level is not None and level < account_cfg.margin_call_level and not margin_call_active:
            margin_call_active = True
            event(ts, MARGIN_CALL, margin_level=level, equity_usd=eq_close, margin_used_usd=used)
        elif margin_call_active and (level is None or level >= account_cfg.margin_call_level):
            margin_call_active = False
        open_notional = sum(states[q.pair].cycle.total_units * q.b2usd_close[current_index(q)]
                            for q in pairs if states[q.pair].cycle is not None)
        eq_low = equity_with({q.pair: (bar[q.pair], "low" if sign > 0 else "high") for q in pairs
                              if states[q.pair].cycle is not None and q.pair in bar})
        rows.append({"timestamp": ts, "balance": acct.balance, "equity": eq_close, "equity_intrabar_worst": eq_low,
                     "unrealised": eq_close - acct.balance, "margin_used": used, "free_margin": eq_close - used,
                     "margin_level": level, "open_notional_usd": open_notional,
                     "open_cycles": sum(1 for q in pairs if states[q.pair].cycle is not None),
                     "cum_spread": cum["spread"], "cum_slippage": cum["slippage"], "cum_commission": cum["commission"],
                     "cum_swap": cum["swap"], "cum_net_pnl": eq_close - acct.starting})

    # 5. unresolved cycles at the data end — reported, never dropped; counterfactual close kept separate
    for p in pairs:
        st = states[p.pair]
        cyc = st.cycle
        if cyc is None:
            continue
        i = current_index(p)
        rate = p.q2usd_close[i]
        mtm = unreal_usd(p, cyc, p.c[i], i, rate)
        spread_usd = sum(f.spread_usd for f in cyc.fills)
        slip_usd = sum(f.slippage_usd for f in cyc.fills) + slip_px[p.pair] * cyc.total_units * rate
        comm_usd = sum(f.commission_usd for f in cyc.fills)
        cycles_out.append({
            "cycle_id": cyc.cycle_id, "pair": p.pair, "direction": strategy.direction, "fold_spec": cyc.fold_label,
            "entry_ts": cyc.entry_ts, "entry_price": cyc.fills[0].exec_price, "exit_ts": None, "exit_ref_price": None,
            "exit_exec_price": None, "exit_reason": None, "outcome": OPEN_UNRESOLVED_AT_DATA_END, "fills": len(cyc.fills),
            "lots": cyc.total_lots, "units": cyc.total_units, "wap": cyc.weighted_avg_price,
            "duration_h": (p.ts[i] - cyc.entry_ts).total_seconds() / 3600,
            "gross_pnl_usd": mtm + spread_usd + slip_usd, "spread_usd": spread_usd, "slippage_usd": slip_usd,
            "commission_usd": comm_usd, "swap_usd": cyc.swap_usd, "net_pnl_usd": mtm - comm_usd + cyc.swap_usd,
            "realised_trading_usd": None, "mtm_unrealised_usd": mtm, "mae_usd": min(cyc.min_unreal_usd, mtm),
            "peak_margin_usd": cyc.max_margin_usd, "peak_notional_usd": cyc.max_notional_usd,
            "rejected_order_attempts": cyc.rejected_attempts, "ladder_exhausted": cyc.exhausted_ts is not None,
            "notional_traded_usd": sum(f.notional_usd for f in cyc.fills),
            "counterfactual_close_net_usd": mtm - comm_usd - comm_rate * cyc.total_lots + cyc.swap_usd,
        })
        event(p.ts[i], OPEN_UNRESOLVED_AT_DATA_END, p.pair, cyc.cycle_id, mtm_usd=mtm, fills=len(cyc.fills))

    result = {
        "label": label,
        "rows": pd.DataFrame(rows),
        "ledger": pd.DataFrame(acct.ledger, columns=["timestamp", "type", "amount_usd", "pair", "cycle_id", "balance_after", "note"]),
        "cycles": pd.DataFrame(cycles_out),
        "fills": pd.DataFrame(fills_out),
        "events": pd.DataFrame(events),
        "starting_capital": acct.starting,
        "insolvent": acct.insolvent,
        "config": {"account": dataclasses.asdict(account_cfg), "cost": dataclasses.asdict(cost), "cost_labels": cost.labels,
                   "strategy": dataclasses.asdict(strategy), "leverage": lev, "pairs": [p.pair for p in pairs]},
        "provenance": {p.pair: p.provenance for p in pairs},
    }
    reconcile(result)
    return result


@dataclass(frozen=True)
class _SizingSpec:
    """Minimal duck-typed ForexInstrumentSpec for lots_from_allocation() — broker values from the MT5 snapshot."""
    canonical_symbol: str
    contract_size: float
    volume_increment: float
    min_volume: float


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# Reconciliation & metrics
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

RECONCILE_TOL_USD = 1e-6


def reconcile(result: Dict) -> Dict:
    """Raise AssertionError unless every balance/equity figure ties back to the ledger and the cycle table."""
    rows, ledger, cycles = result["rows"], result["ledger"], result["cycles"]
    start = result["starting_capital"]
    tol = max(RECONCILE_TOL_USD, abs(start) * 1e-12)   # float64 resolution scales with the account size
    led_sum = ledger["amount_usd"].sum() if len(ledger) else 0.0
    final_balance = rows["balance"].iloc[-1] if len(rows) else start
    assert abs(start + led_sum - final_balance) < tol, "balance != start + sum(ledger)"
    if len(ledger):   # per-row: balance == start + ledger entries up to that timestamp
        lc = ledger.groupby("timestamp")["amount_usd"].sum().cumsum()
        aligned = lc.reindex(rows["timestamp"], method="ffill").fillna(0.0).to_numpy()
        assert np.abs(start + aligned - rows["balance"].to_numpy()).max() < 10 * tol, "row balance != ledger running sum"
    if len(cycles):
        ident = cycles["gross_pnl_usd"] - cycles["spread_usd"] - cycles["slippage_usd"] - cycles["commission_usd"] + cycles["swap_usd"]
        assert np.abs(ident - cycles["net_pnl_usd"]).max() < tol, "cycle net != gross - costs + swap"
        nbp = ledger.loc[ledger["type"] == "NEGATIVE_BALANCE_PROTECTION", "amount_usd"].sum() if len(ledger) else 0.0
        final_equity = rows["equity"].iloc[-1]
        assert abs(start + cycles["net_pnl_usd"].sum() + nbp - final_equity) < 10 * tol, "final equity != start + sum(cycle net) + NBP"
    return {"ok": True}


def _safe_ratio(num: float, den: float, pct: bool = True):
    if den is None or not np.isfinite(den) or den <= 0:
        return None
    return num / den * (100.0 if pct else 1.0)


def _longest_recovery_hours(ts: pd.Series, eq: pd.Series) -> Tuple[float, bool]:
    peak_val, peak_t, longest, unrecovered = -np.inf, None, 0.0, False
    for t, v in zip(ts, eq):
        if v >= peak_val:
            if peak_t is not None:
                longest = max(longest, (t - peak_t).total_seconds() / 3600)
            peak_val, peak_t = v, t
    if peak_t is not None and eq.iloc[-1] < peak_val:
        tail = (ts.iloc[-1] - peak_t).total_seconds() / 3600
        if tail > longest:
            longest, unrecovered = tail, True
    return longest, unrecovered


def compute_metrics(result: Dict) -> Dict:
    rows, cycles, events, ledger = result["rows"], result["cycles"], result["events"], result["ledger"]
    start = result["starting_capital"]
    eq = rows["equity"]
    eq_worst = rows["equity_intrabar_worst"]
    peak = eq.cummax().clip(lower=start)
    dd = eq - peak
    dd_pct = dd / peak * 100
    peak_w = np.maximum.accumulate(np.maximum(eq.to_numpy(), start))
    ddw = eq_worst.to_numpy() - peak_w
    closed = cycles[cycles["outcome"] != OPEN_UNRESOLVED_AT_DATA_END] if len(cycles) else cycles
    openc = cycles[cycles["outcome"] == OPEN_UNRESOLVED_AT_DATA_END] if len(cycles) else cycles
    ev = events["type"].value_counts().to_dict() if len(events) else {}

    gross_pos = float(closed.loc[closed["gross_pnl_usd"] > 0, "gross_pnl_usd"].sum()) if len(closed) else 0.0
    gross_neg = float(closed.loc[closed["gross_pnl_usd"] < 0, "gross_pnl_usd"].sum()) if len(closed) else 0.0
    gross_all = float(cycles["gross_pnl_usd"].sum()) if len(cycles) else 0.0
    swap = float(cycles["swap_usd"].sum()) if len(cycles) else 0.0
    costs = {k: float(cycles[c].sum()) if len(cycles) else 0.0 for k, c in
             (("spread", "spread_usd"), ("slippage", "slippage_usd"), ("commission", "commission_usd"))}
    swap_cost = max(-swap, 0.0)       # financing is a cost only when negative; a carry credit is reported separately
    swap_credit = max(swap, 0.0)
    total_costs = costs["spread"] + costs["slippage"] + costs["commission"] + swap_cost
    net_all = float(cycles["net_pnl_usd"].sum()) if len(cycles) else 0.0
    notional = float(cycles["notional_traded_usd"].sum()) if len(cycles) else 0.0
    net_pos = float(closed.loc[closed["net_pnl_usd"] > 0, "net_pnl_usd"].sum()) if len(closed) else 0.0
    net_neg = float(closed.loc[closed["net_pnl_usd"] < 0, "net_pnl_usd"].sum()) if len(closed) else 0.0
    nbp = float(ledger.loc[ledger["type"] == "NEGATIVE_BALANCE_PROTECTION", "amount_usd"].sum()) if len(ledger) else 0.0
    end_eq = float(eq.iloc[-1])
    rec_h, rec_unrec = _longest_recovery_hours(rows["timestamp"], eq)
    liq = cycles[cycles["outcome"] == LIQUIDATED] if len(cycles) else cycles

    def by(col):
        if not len(closed):
            return {}
        g = closed.groupby(col)
        return {str(k): {"cycles": int(len(v)), "gross": float(v["gross_pnl_usd"].sum()),
                         "costs": float(v["spread_usd"].sum() + v["slippage_usd"].sum() + v["commission_usd"].sum()),
                         "swap": float(v["swap_usd"].sum()), "net": float(v["net_pnl_usd"].sum())} for k, v in g}

    month_col = closed["exit_ts"].apply(lambda t: pd.Timestamp(t).strftime("%Y-%m")) if len(closed) else None
    if len(closed):
        closed = closed.assign(month=month_col)

    return {
        "starting_capital": start,
        "ending_balance": float(rows["balance"].iloc[-1]),
        "ending_equity": end_eq,
        "gross_realised_pnl": float(closed["gross_pnl_usd"].sum()) if len(closed) else 0.0,
        "unrealised_pnl_open_cycles": float(openc["mtm_unrealised_usd"].sum()) if len(openc) else 0.0,
        "gross_trading_pnl_all": gross_all,
        "costs": {**costs, "financing_cost": swap_cost, "liquidation_fees": 0.0, "total": total_costs},
        "swap_net": swap, "swap_credit": swap_credit,
        "net_pnl": net_all, "negative_balance_protection_credit": nbp,
        "net_return_pct": (end_eq - start) / start * 100,
        "gross_return_pct": (gross_all + nbp) / start * 100,
        "cost_drag_pp": (gross_all - net_all) / start * 100,
        "cost_to_gross_profit_pct": _safe_ratio(total_costs, gross_pos),
        "cost_pct_of_gross_absolute_pnl": _safe_ratio(total_costs, gross_pos + abs(gross_neg)),
        "cost_pct_of_notional": _safe_ratio(total_costs, notional),
        "profit_retention_pct": (net_all / gross_all * 100) if gross_all > 0 else None,
        "traded_notional_usd": notional,
        "gross_positive_pnl": gross_pos, "gross_negative_pnl": gross_neg,
        "gross_profit_factor": (gross_pos / abs(gross_neg)) if gross_neg < 0 else None,
        "net_profit_factor": (net_pos / abs(net_neg)) if net_neg < 0 else None,
        "costs_turned_profit_into_loss": bool(gross_all > 0 and net_all <= 0),
        "max_equity_drawdown_pct": float(dd_pct.min()),
        "max_drawdown_usd": float(dd.min()),
        "max_drawdown_intrabar_usd": float(ddw.min()),
        "max_drawdown_intrabar_pct": float((ddw / peak_w * 100).min()),   # % of the running equity peak, same basis as max_equity_drawdown_pct
        "longest_drawdown_hours": rec_h, "longest_drawdown_unrecovered": rec_unrec,
        "max_capital_deployed_notional_usd": float(rows["open_notional_usd"].max()),
        "max_margin_used_usd": float(rows["margin_used"].max()),
        "min_free_margin_usd": float(rows["free_margin"].min()),
        "min_margin_level_pct": float(rows["margin_level"].dropna().min() * 100) if rows["margin_level"].notna().any() else None,
        "max_simultaneous_open_cycles": int(rows["open_cycles"].max()),
        "completed_cycles": int(len(closed)), "unresolved_cycles": int(len(openc)),
        "ladder_exhaustion_count": int(cycles["ladder_exhausted"].sum()) if len(cycles) else 0,
        "ladder_exhaustion_rate_pct": float(cycles["ladder_exhausted"].mean() * 100) if len(cycles) else 0.0,
        "capital_insufficiency_events": int(ev.get(INSUFFICIENT_FREE_CASH, 0)),
        "order_rejections_min_volume": int(ev.get(ORDER_REJECTED, 0)),
        "margin_call_events": int(ev.get(MARGIN_CALL, 0)),
        "liquidation_count": int(len(liq)),
        "insolvent": bool(result["insolvent"]),
        "longest_cycle_hours": float(cycles["duration_h"].max()) if len(cycles) else 0.0,
        "worst_floating_loss_usd": float(cycles["mae_usd"].min()) if len(cycles) else 0.0,
        "worst_realised_cycle_loss_usd": float(closed["net_pnl_usd"].min()) if len(closed) else 0.0,
        "win_rate_before_costs_pct": float((closed["gross_pnl_usd"] > 0).mean() * 100) if len(closed) else None,
        "win_rate_after_costs_pct": float((closed["net_pnl_usd"] > 0).mean() * 100) if len(closed) else None,
        "net_expectancy_per_cycle_usd": float(closed["net_pnl_usd"].mean()) if len(closed) else None,
        "cost_per_completed_cycle_usd": float((closed["spread_usd"] + closed["slippage_usd"] + closed["commission_usd"]).mean()) if len(closed) else None,
        "by_pair": by("pair"), "by_fills": by("fills"), "by_month": by("month") if len(closed) else {},
        "by_fold_spec": by("fold_spec"),
        "outcomes": cycles["outcome"].value_counts().to_dict() if len(cycles) else {},
        "events": ev,
    }
