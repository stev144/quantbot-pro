# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/quant_core/funding.py
#
# claude code changed: new file — 2026-10-03, owner decision (borrow row of DECISION_SHEET_D1-D11_2026-10-03.md,
# signed; owner choice: perpetual funding rates). The short leg of a pair is modelled as a USDT-M perpetual;
# its financing is the historical funding paid/received at each settlement while the short is held.
#
#   cash flow to a SHORT at settlement k:  +rate_k × notional_k   (positive funding: longs pay shorts)
#   financing COST of the short:           −Σ_k rate_k × notional_k over settlements in (t0, t1]
#   notional_k = |short qty| × last close at or before the settlement (perp ≈ spot; basis NOT modelled — flagged)
#
# COVERAGE (fail closed): the funding series must have a settlement at or before t0 and one at or after t1, and no
# gap longer than MAX_FUNDING_GAP between consecutive settlements spanning (t0, t1]. Binance settles every 8 h for
# most symbols and every 4 h for some, so no fixed grid is assumed. Missing coverage ⇒ BORROW_UNAVAILABLE; the pair
# is ineligible for canonical evidence. A missing rate is NEVER replaced by 0.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from bot.quant_core.research_zones import to_utc

FUNDING_MODEL_VERSION = "QBP-FUND-1"
# Recorded Binance settlement stamps jitter by milliseconds (BTC history: max gap 8 h 0 min 0.047 s), so the
# limit allows 60 s of jitter — far below a missing settlement, which adds a full 4–8 h.
FUNDING_TIMESTAMP_JITTER = pd.Timedelta(seconds=60)
MAX_FUNDING_GAP = pd.Timedelta(hours=8) + FUNDING_TIMESTAMP_JITTER


@dataclass(frozen=True)
class FundingCost:
    cost: Optional[float]             # financing cost of the short (negative = net income); None when unavailable
    complete: bool
    n_settlements: int
    reason: str = ""


def normalise_funding(series: pd.Series) -> pd.Series:
    """Funding rates indexed by settlement time (tz-aware UTC, sorted, unique). Accepts epoch-ms/s or datetimes."""
    s = pd.Series(series.to_numpy(float), index=to_utc(pd.Index(series.index)))
    s = s[~s.index.duplicated(keep="first")].sort_index()
    if s.isna().any():
        raise ValueError("funding series contains NaN rates — refuse rather than impute")
    return s


def funding_cost(short_qty: float, prices: pd.Series, funding: pd.Series, t0, t1) -> FundingCost:
    """Financing cost of holding |short_qty| units short from t0 (exclusive) to t1 (inclusive)."""
    t0, t1 = pd.Timestamp(t0), pd.Timestamp(t1)
    t0 = t0.tz_localize("UTC") if t0.tzinfo is None else t0.tz_convert("UTC")
    t1 = t1.tz_localize("UTC") if t1.tzinfo is None else t1.tz_convert("UTC")
    if t1 <= t0:
        return FundingCost(0.0, True, 0)
    if funding is None or len(funding) == 0:
        return FundingCost(None, False, 0, "no funding series")
    f = funding
    if f.index[0] > t0 or f.index[-1] < t1:
        return FundingCost(None, False, 0, f"funding history does not span ({t0}, {t1}]")
    lo = f.index.searchsorted(t0, side="right") - 1          # last settlement at or before t0
    hi = f.index.searchsorted(t1, side="left")               # first settlement at or after t1
    span = f.index[lo:hi + 1]
    if len(span) >= 2 and (span[1:] - span[:-1]).max() > MAX_FUNDING_GAP:
        return FundingCost(None, False, 0, f"funding gap > {MAX_FUNDING_GAP} inside ({t0}, {t1}]")
    inside = f[(f.index > t0) & (f.index <= t1)]
    if len(inside) == 0:
        return FundingCost(0.0, True, 0)                     # covered window with no settlement inside it
    px = prices.sort_index()
    pos = px.index.searchsorted(inside.index, side="right") - 1
    if (pos < 0).any():
        return FundingCost(None, False, 0, "no price at or before a settlement")
    notional = abs(float(short_qty)) * px.to_numpy(float)[pos]
    cost = -float((inside.to_numpy(float) * notional).sum())
    return FundingCost(cost, True, int(len(inside)))


def load_funding_series(symbol: str, data_dir="data") -> pd.Series:   # claude code changed: borrow row — recorded source loader
    """Recorded Binance USDT-M funding history written by bot/fetch_derivatives_data.py
    (<data_dir>/<SYMBOL>_funding.csv, columns timestamp, funding_rate, ...). Raises FileNotFoundError when the
    symbol has no recorded history — the caller must then treat financing as BORROW_UNAVAILABLE (pair ineligible)."""
    from pathlib import Path
    from bot.quant_core.market_profile import AssetClassMismatch, asset_class_for_symbol   # claude code changed: 2026-10-04 asset-agnostic
    if asset_class_for_symbol(symbol) != "CRYPTO":                                          # claude code changed: perp funding never finances a non-crypto leg
        raise AssetClassMismatch(f"{symbol} is {asset_class_for_symbol(symbol)}: recorded perpetual funding is a crypto financing source")
    path = Path(data_dir) / (symbol.replace("/", "_") + "_funding.csv")
    if not path.exists():
        raise FileNotFoundError(f"no recorded funding history for {symbol} at {path} — BORROW_UNAVAILABLE")
    df = pd.read_csv(path)
    return normalise_funding(pd.Series(df["funding_rate"].to_numpy(float), index=df["timestamp"]))
