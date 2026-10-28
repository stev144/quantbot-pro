# bot/research/fx_carry_accounting.py  # claude code changed: 2026-10-28 — new: FX carry accounting (step 1 of the carry study)
"""Live FX carry accounting: for every forex pair the current broker offers, what holding it LONG or SHORT pays or
costs per year (the broker's own swap), against the cost of getting in and out (the live spread) and the price risk
carried while waiting (realised volatility).

Mechanism-first, not a backtest: nothing here measures past strategy returns. Inputs are the broker's CURRENT contract
terms (MT5 symbol_info / tick) and recent realised volatility. Descriptive: a positive carry is a risk premium that
pays slowly and can be wiped out by a sudden move (e.g. the August 2024 yen-carry unwind).

Definitions (per side s ∈ {long, short}; swap_mode must be POINTS, the only unit converted — others are refused):
    carry_pct_yr_s   = swap_s · point / mid · 365 · 100
                       (MT5 applies 5 rollovers a week with one tripled = 7 swap-days per 7 calendar days, so a
                       calendar-year factor of 365 is exact on average)
    round_trip_pct   = (ask − bid) / mid · 100           — one full spread = cost of entering and exiting at market
    vol_ann_pct      = std(daily log returns, last 60 trading days, ddof 1) · √260 · 100
    carry_to_vol_s   = carry_pct_yr_s / vol_ann_pct      — carry earned per unit of yearly price risk; under uncovered
                       interest parity the expected price change offsets carry, so this is an UPPER-bound ratio
    days_to_3x_cost_s = 3 · round_trip_pct / (carry_pct_yr_s / 365)   — days of carry needed to pass the owner's
                       signed economic pre-screen (edge ≥ 3 × round-trip cost); only when carry_pct_yr_s > 0
    broker_markup_pct_yr = −(carry_long + carry_short) / 2   — in a fair market long and short carry are equal and
                       opposite; the gap is what the broker keeps, per side
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SWAP_MODE_POINTS = 1          # MT5 SYMBOL_SWAP_MODE_POINTS
CALENDAR_DAYS = 365.0
FX_TRADING_DAYS = 260.0
VOL_WINDOW_DAYS = 60          # about one quarter of trading days
COST_MULTIPLE = 3.0           # owner's signed economic pre-screen (DECISION_SHEET_D1-D11, "Economic pre-screen")
FX_DIR = Path(__file__).resolve().parents[2] / "data" / "forex"


@dataclass(frozen=True)
class BrokerTerms:
    symbol: str               # canonical, e.g. "USD/JPY"
    mt5_symbol: str
    point: float
    digits: int
    contract_size: float
    swap_mode: int
    swap_long: float          # in points per lot per day (when swap_mode == POINTS)
    swap_short: float
    triple_day: Optional[int]
    bid: float
    ask: float
    as_of: str


def carry_row(t: BrokerTerms, vol_ann_pct: Optional[float]) -> Dict:
    """Carry accounting for one pair. Never guesses: an unsupported swap unit or a bad quote gives None values and a
    `problem` string."""
    row: Dict = {"symbol": t.symbol, "as_of": t.as_of, "swap_mode": t.swap_mode, "vol_ann_pct": vol_ann_pct,
                 "problem": None}
    mid = (t.bid + t.ask) / 2.0
    if not (t.bid > 0 and t.ask >= t.bid):
        row["problem"] = f"bad quote bid={t.bid} ask={t.ask}"
        return row
    row["round_trip_pct"] = (t.ask - t.bid) / mid * 100.0
    if t.swap_mode != SWAP_MODE_POINTS:
        row["problem"] = f"swap_mode {t.swap_mode} is not POINTS — not converted (would need the broker's formula)"
        return row
    for side, swap in (("long", t.swap_long), ("short", t.swap_short)):
        c = swap * t.point / mid * CALENDAR_DAYS * 100.0
        row[f"carry_{side}_pct_yr"] = c
        row[f"carry_to_vol_{side}"] = (c / vol_ann_pct) if vol_ann_pct else None
        row[f"days_to_3x_cost_{side}"] = (COST_MULTIPLE * row["round_trip_pct"] / (c / CALENDAR_DAYS)) if c > 0 else None
    cl, cs = row["carry_long_pct_yr"], row["carry_short_pct_yr"]
    row["broker_markup_pct_yr"] = -(cl + cs) / 2.0
    row["paid_side"] = "LONG" if cl > 0 else "SHORT" if cs > 0 else "NONE"
    best = max(cl, cs)
    row["best_carry_pct_yr"] = best
    row["best_carry_to_vol"] = (best / vol_ann_pct) if (vol_ann_pct and best > 0) else None
    return row


def realised_vol_ann_pct(hourly_close: pd.Series, window_days: int = VOL_WINDOW_DAYS) -> Optional[float]:
    """Annualised volatility of daily log returns (last `window_days` complete trading days). Forex weekends have no
    candles, so daily closes come from days that traded."""
    daily = hourly_close.astype(float).resample("1D").last().dropna()
    r = np.log(daily).diff().dropna().tail(window_days)
    if len(r) < window_days // 2:
        return None
    return float(r.std(ddof=1) * math.sqrt(FX_TRADING_DAYS) * 100.0)


def load_hourly_close(symbol: str, through: Optional[pd.Timestamp] = None) -> Optional[pd.Series]:
    """Hourly closes from data/forex, stopping at `through` (default: the FX research cutoff, so no sealed-holdout
    candle is read)."""
    from bot.research.fx_holdout_guard import FX_RESEARCH_DATA_CUTOFF
    through = through or (pd.Timestamp(FX_RESEARCH_DATA_CUTOFF) - pd.Timedelta(hours=1))
    p = FX_DIR / f"{symbol.replace('/', '_')}_1h.csv"
    if not p.exists():
        return None
    d = pd.read_csv(p, usecols=["timestamp", "close"])
    d.index = pd.to_datetime(d.pop("timestamp"), utc=True)
    return d["close"].sort_index().loc[:through]


def fetch_broker_terms(symbols: List[str]) -> Dict[str, BrokerTerms]:
    """One attach to the running MT5 terminal (read-only, no login), raw contract terms and the live tick for each
    symbol. A symbol the broker does not offer is skipped and logged."""
    from bot import mt5_data_fetcher as mf
    mt5 = mf._attach_running_terminal()
    out: Dict[str, BrokerTerms] = {}
    try:
        for s in symbols:
            ms = mf._to_mt5_symbol(s)
            if not mt5.symbol_select(ms, True):
                logger.info("carry: %s not offered by this broker", s)
                continue
            info, tick = mt5.symbol_info(ms), mt5.symbol_info_tick(ms)
            if info is None or tick is None:
                logger.info("carry: no info/tick for %s", s)
                continue
            out[s] = BrokerTerms(symbol=s, mt5_symbol=ms, point=float(info.point), digits=int(info.digits),
                                 contract_size=float(info.trade_contract_size), swap_mode=int(info.swap_mode),
                                 swap_long=float(info.swap_long), swap_short=float(info.swap_short),
                                 triple_day=getattr(info, "swap_rollover3days", None),
                                 bid=float(tick.bid), ask=float(tick.ask),
                                 as_of=pd.Timestamp(int(tick.time), unit="s", tz="UTC").isoformat())
        server = getattr(mt5.account_info(), "server", None)
    finally:
        mt5.shutdown()
    logger.info("carry: %d symbols from broker %s", len(out), server)
    return out


def build_carry_table(terms: Dict[str, BrokerTerms]) -> pd.DataFrame:
    rows = []
    for s, t in terms.items():
        close = load_hourly_close(s)
        vol = realised_vol_ann_pct(close) if close is not None and len(close) else None
        rows.append(carry_row(t, vol))
    df = pd.DataFrame(rows).set_index("symbol")
    return df.sort_values("best_carry_to_vol", ascending=False, na_position="last") if "best_carry_to_vol" in df else df


def forex_universe() -> List[str]:
    return sorted(p.name[:-len("_1h.csv")].replace("_", "/") for p in FX_DIR.glob("*_1h.csv"))


def terms_to_dict(terms: Dict[str, BrokerTerms]) -> Dict[str, Dict]:
    return {k: asdict(v) for k, v in terms.items()}
