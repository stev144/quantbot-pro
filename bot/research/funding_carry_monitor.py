# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/funding_carry_monitor.py
#
# claude code changed: new file — 2026-10-22, "Futures Funding Intelligence" Phase 1 (owner brief). READ-ONLY,
# DESCRIPTIVE research: the historical cash flows of a HYPOTHETICAL market-neutral position
#
#       LONG Q units of the spot asset  +  SHORT Q units of the Binance USDⓈ-M perpetual
#
# held from an entry settlement to an exit settlement, Q = spot_notional / spot price at entry (quantities are
# matched in base-asset units; no rebalancing trades are modelled — see LIMITATIONS).
#
# REUSED, not re-implemented (single sources of truth):
#   bot.quant_core.funding.load_funding_series / funding_cost  — recorded funding, sign convention (positive
#       funding: longs pay shorts), fail-closed coverage (gap > 8 h ⇒ unavailable, a missing rate is never 0)
#   bot.quant_core.research_zones.ZONES                         — the sealed crypto holdout F = 2026-10-04 00:00 UTC
#   bot.config.execution_costs                                  — FEE_RATE (spot), PERP_TAKER_FEE_RATE, SLIPPAGE_RATE
#   bot.quant_core.provenance                                   — run id, input fingerprints, sidecar
#
# FORMULAS (all money in USD; rates are decimals per settlement; quantities in base-asset units):
#   funding to the short over (t0, t1]   F = Σ_k r_k · Q · S_k            S_k = spot close at or before settlement k
#                                         = −funding_cost(Q, S, r, t0, t1).cost
#   opening cost   C_open  = Q·S_0·(f_spot + s) + Q·P_0·(f_perp + s)      P = perp price proxy (mark price when
#   closing cost   C_close = Q·S_1·(f_spot + s) + Q·P_1·(f_perp + s)          recorded, else spot — labelled)
#   basis P&L      B = Q·(S_1 − S_0) − Q·(P_1 − P_0) = Q·[(P_0 − S_0) − (P_1 − S_1)]   (only with P = mark at BOTH ends)
#   net (funding-only scope)   N_A = F − C_open − C_close                   (basis EXCLUDED — stated)
#   net (full-position scope)  N_B = F + B − C_open − C_close               (mark price is a proxy for a fill)
#   capital at short leverage L:   K_L = Q·S_0 (spot leg) + Q·P_0 / L (initial margin of the short)
#   theoretical isolated-short liquidation (flat maintenance rate m, no top-ups, funding/fees ignored):
#       equity = Q·P_0/L − Q·(P − P_0) = m·Q·P   ⇒   P_liq = P_0 · (1 + 1/L) / (1 + m)
#   Margin is CAPITAL, never subtracted as a cost.  Annualised figure only for ≥ 365 observed days:
#       N / K_L × 365.25 / days  — an extrapolation of the observed period, labelled as such.
#
# TIMING (G4): a spot candle's close is known only at its CLOSE time (open + 1 h), so spot prices are indexed by
# close time — indexing by open time would price a settlement with a close from one hour later (look-ahead).
# A settlement whose most recent spot close is older than MAX_PRICE_AGE marks its month INCOMPLETE (no stale fill).
#
# CLASSIFICATION: DESCRIPTIVE. All data before F is PSEUDO_HOLDOUT (exposed, not claim-eligible); nothing at or
# after F is read. Not a yield, not a strategy, not a recommendation.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from bot.config.execution_costs import FEE_RATE, PERP_TAKER_FEE_RATE, SLIPPAGE_RATE
from bot.quant_core.funding import FUNDING_MODEL_VERSION, funding_cost, normalise_funding
from bot.quant_core.research_zones import ZONES, ResearchZones, to_utc

MONITOR_VERSION = "SQT-FUNDCARRY-1"
KIND = "FUNDING_CARRY_MONITOR"
CLASSIFICATION = "DESCRIPTIVE — PSEUDO_HOLDOUT history, not claim-eligible"
STATUS_LABEL = "Historical research estimate — not a live yield or trading recommendation."
MAX_PRICE_AGE = pd.Timedelta(hours=1, seconds=60)       # newest spot close must be ≤ 1 h (+ settlement jitter) old
MIN_DAYS_FOR_ANNUALISING = 365
DEFAULT_OUT_DIR = Path("research_data") / "funding_carry"
REPORT_NAME = "funding_carry_report.json"
MONTHLY_NAME = "funding_carry_monthly.csv"

OK, UNAVAILABLE, INVALID = "OK", "UNAVAILABLE", "INVALID"
LIMITATIONS = (
    "Funding uses Binance's recorded settlement rates; the cash flow is priced at the spot close (perp ≈ spot), "
    "as in quant_core.funding — basis between spot and perp at each settlement is not modelled.",
    "Perp entry/exit prices use Binance's recorded MARK price (available from 2023-10-31) as a proxy for a fill; "
    "a mark price is an index-smoothed fair price, not an executable trade price.",
    "No rebalancing trades are modelled: quantities stay matched in base units, so the hedge stays delta-neutral "
    "in units but the short's margin is NOT topped up — the capital rows show what that would require.",
    "Liquidation is a theoretical isolated-margin approximation with one flat maintenance rate; Binance uses "
    "tiered maintenance margins, mark-price triggers and liquidation fees, so real thresholds differ.",
    "Historical breaches use spot 1-hour highs, not the perp mark price.",
    "Fees are base-tier taker assumptions; slippage is an assumption; spot borrow/interest and exchange "
    "counterparty risk are not modelled.",
    "All results are descriptive history (pseudo-holdout). They are not forecasts and not evidence of an edge.",
)


@dataclass(frozen=True)
class CarryConfig:
    spot_notional_usd: float = 10_000.0
    spot_fee_rate: float = FEE_RATE                 # Binance spot base taker, per side
    perp_fee_rate: float = PERP_TAKER_FEE_RATE      # Binance USDⓈ-M base taker, per side
    slippage_rate: float = SLIPPAGE_RATE            # per side, per leg (assumption)
    maintenance_margin_rate: float = 0.005          # flat theoretical rate (Binance tiers differ)
    short_leverages: tuple = (1.0, 2.0, 3.0)
    fee_multipliers: tuple = (0.5, 1.0, 2.0)        # cost-sensitivity grid
    slippage_grid: tuple = (0.0, 0.0005, 0.001)

    def __post_init__(self):
        if not (self.spot_notional_usd > 0 and math.isfinite(self.spot_notional_usd)):
            raise ValueError("spot_notional_usd must be a positive finite number")
        for name in ("spot_fee_rate", "perp_fee_rate", "slippage_rate", "maintenance_margin_rate"):
            v = getattr(self, name)
            if not (0 <= v < 0.1 and math.isfinite(v)):
                raise ValueError(f"{name} must be a decimal rate in [0, 0.1); got {v!r}")
        if not self.short_leverages or any(not (1 <= L <= 125) for L in self.short_leverages):
            raise ValueError("short_leverages must be within [1, 125]")

    def as_dict(self) -> dict:
        d = asdict(self)
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()}


# ── pure formulas (known-answer tested) ───────────────────────────────────────────────────────────────────────
def short_liquidation_price(entry_price: float, leverage: float, maintenance_rate: float) -> float:
    """Theoretical isolated SHORT liquidation price: P_0·(1 + 1/L)/(1 + m). See the module header derivation."""
    if not (entry_price > 0 and leverage >= 1 and 0 <= maintenance_rate < 1):
        raise ValueError("entry_price > 0, leverage ≥ 1 and 0 ≤ maintenance_rate < 1 are required")
    return entry_price * (1.0 + 1.0 / leverage) / (1.0 + maintenance_rate)


def round_trip_costs(qty: float, spot_in: float, perp_in: float, spot_out: float, perp_out: float,
                     spot_fee: float, perp_fee: float, slippage: float) -> Dict[str, float]:
    """Opening and closing cash costs of both legs (fees + slippage on notional, per side)."""
    open_ = qty * spot_in * (spot_fee + slippage) + qty * perp_in * (perp_fee + slippage)
    close = qty * spot_out * (spot_fee + slippage) + qty * perp_out * (perp_fee + slippage)
    return {"open": open_, "close": close, "total": open_ + close}


def basis_pnl(qty: float, spot_in: float, perp_in: float, spot_out: float, perp_out: float) -> float:
    """Long-spot + short-perp price P&L: Q·(S1 − S0) − Q·(P1 − P0)."""
    return qty * (spot_out - spot_in) - qty * (perp_out - perp_in)


def annualise(net: float, capital: float, days: float) -> Optional[float]:
    """net/capital scaled to 365.25 days — None for < MIN_DAYS_FOR_ANNUALISING days or a non-positive capital."""
    if capital is None or not (capital > 0) or days is None or days < MIN_DAYS_FOR_ANNUALISING:
        return None
    return net / capital * 365.25 / days


# ── data loading (fail closed, everything counted) ─────────────────────────────────────────────────────────────
def _stem(symbol: str) -> str:
    return symbol.replace("/", "_")


def load_spot(symbol: str, data_dir, zones: ResearchZones = ZONES) -> Dict:
    """Spot 1h candles indexed by CLOSE time. Sealed rows (open ≥ F) are excluded and counted; invalid rows
    (non-finite or non-positive close/high) are excluded and counted, never repaired."""
    path = Path(data_dir) / f"{_stem(symbol)}_1h.csv"
    if not path.exists():
        return {"status": UNAVAILABLE, "reason": f"no spot file {path.as_posix()}", "path": path}
    df = pd.read_csv(path, usecols=lambda c: c in ("timestamp", "high", "close"))
    if not {"timestamp", "high", "close"} <= set(df.columns):
        return {"status": INVALID, "reason": "spot file lacks timestamp/high/close", "path": path}
    opens = to_utc(pd.Index(df["timestamp"]))
    research, sealed = zones.split_sealed(df.assign(_open=opens), ts_col="_open")
    hi, cl = research["high"].to_numpy(float), research["close"].to_numpy(float)
    bad = ~(np.isfinite(hi) & np.isfinite(cl) & (hi > 0) & (cl > 0))
    research = research.loc[~bad]
    dup = research["_open"].duplicated(keep="first")
    research = research.loc[~dup]
    idx = pd.DatetimeIndex(research["_open"]).as_unit("ns") + pd.Timedelta(hours=1)   # close time (ns: one unit everywhere)
    zones.assert_no_sealed_data(pd.DatetimeIndex(research["_open"]), purpose=f"funding carry spot {symbol}")
    close = pd.Series(research["close"].to_numpy(float), index=idx).sort_index()
    high = pd.Series(research["high"].to_numpy(float), index=idx).sort_index()
    if len(close) == 0:
        return {"status": UNAVAILABLE, "reason": "no valid spot rows before the sealed holdout", "path": path}
    return {"status": OK, "close": close, "high": high, "path": path, "n_sealed_excluded": int(len(sealed)),
            "n_invalid_excluded": int(bad.sum()), "n_duplicates_excluded": int(dup.sum())}


def load_funding(symbol: str, data_dir, zones: ResearchZones = ZONES) -> Dict:
    """Recorded funding (bot/fetch_derivatives_data.py) with explicit validation, then the canonical
    normalise_funding(). Non-finite rates, malformed timestamps or conflicting duplicates ⇒ INVALID (no imputation)."""
    path = Path(data_dir) / f"{_stem(symbol)}_funding.csv"
    if not path.exists():
        return {"status": UNAVAILABLE, "reason": f"no recorded funding {path.as_posix()} — BORROW_UNAVAILABLE", "path": path}
    raw = pd.read_csv(path)
    if not {"timestamp", "funding_rate"} <= set(raw.columns):
        return {"status": INVALID, "reason": "funding file lacks timestamp/funding_rate", "path": path}
    ts_num = pd.to_numeric(raw["timestamp"], errors="coerce")
    rate = pd.to_numeric(raw["funding_rate"], errors="coerce")
    n_bad_ts = int(ts_num.isna().sum())
    n_bad_rate = int((~np.isfinite(rate.to_numpy(float))).sum())
    if n_bad_ts or n_bad_rate:
        return {"status": INVALID, "path": path,
                "reason": f"{n_bad_ts} malformed timestamp(s), {n_bad_rate} non-finite rate(s) — refused, never imputed"}
    when = pd.to_datetime(ts_num.astype("int64"), unit="ms", utc=True)
    frame = pd.DataFrame({"t": when, "rate": rate.to_numpy(float),
                          "mark": pd.to_numeric(raw.get("mark_price"), errors="coerce") if "mark_price" in raw else np.nan})
    dups = frame.duplicated("t", keep=False)
    if dups.any() and frame.loc[dups].groupby("t")["rate"].nunique().max() > 1:
        return {"status": INVALID, "path": path, "reason": "duplicate settlement timestamps with conflicting rates"}
    n_dup = int(frame.duplicated("t", keep="first").sum())
    frame = frame.drop_duplicates("t", keep="first")
    research, sealed = zones.split_sealed(frame, ts_col="t")
    zones.assert_no_sealed_data(pd.DatetimeIndex(research["t"]), purpose=f"funding carry funding {symbol}")
    if len(research) == 0:
        return {"status": UNAVAILABLE, "reason": "no funding settlements before the sealed holdout", "path": path}
    t_ns = pd.DatetimeIndex(research["t"]).as_unit("ns")   # one time unit for every index in this module
    rates = normalise_funding(pd.Series(research["rate"].to_numpy(float), index=t_ns))
    mark = pd.Series(research["mark"].to_numpy(float), index=t_ns).sort_index()
    mark = mark[np.isfinite(mark.to_numpy(float)) & (mark.to_numpy(float) > 0)]
    return {"status": OK, "rates": rates, "mark": mark, "path": path, "n_duplicates_excluded": n_dup,
            "n_sealed_excluded": int(len(sealed))}


def _price_at(close: pd.Series, t: pd.Timestamp) -> Optional[float]:
    """Most recent spot close at or before t, only if it is at most MAX_PRICE_AGE old (no stale prices)."""
    pos = close.index.searchsorted(t, side="right") - 1
    if pos < 0 or t - close.index[pos] > MAX_PRICE_AGE:
        return None
    return float(close.iloc[pos])


MARK_SPOT_BOUNDS = (0.8, 1.25)   # claude code changed: 2026-10-22 — contract-unit guard (e.g. 1000SHIB perp: mark = 1000 × spot)


def _usable_mark(mark: pd.Series, close: pd.Series, t) -> Optional[float]:
    """Recorded mark price at settlement t, only when it is in the SAME unit as spot (ratio within
    MARK_SPOT_BOUNDS). A perp quoted per 1000 units, or a broken print, is refused — never rescaled by guesswork."""   # claude code changed: 2026-10-22
    if t not in mark.index:
        return None
    s = _price_at(close, t)
    m = float(mark.loc[t])
    if s is None or not (MARK_SPOT_BOUNDS[0] <= m / s <= MARK_SPOT_BOUNDS[1]):
        return None
    return m


# ── per-asset analysis ─────────────────────────────────────────────────────────────────────────────────────────
def _month_rows(rates: pd.Series, close: pd.Series, high: pd.Series, t_entry, t_exit) -> List[Dict]:
    """Funding per UNIT of base asset to the short, by calendar month (settlements in (month start, next start]
    shifted by 1 ms so a 00:00 settlement on day 1 belongs to its own month). A month with a funding gap or a
    stale spot price is INCOMPLETE and carries no number."""
    eps = pd.Timedelta(milliseconds=1)   # claude code changed: 2026-10-22 — ms (indexes are ns; 1 ms < any settlement spacing)
    out = []
    for per in pd.period_range(t_entry.tz_convert(None).to_period("M"), t_exit.tz_convert(None).to_period("M"), freq="M"):
        m0 = per.start_time.tz_localize("UTC") - eps
        m1 = (per + 1).start_time.tz_localize("UTC") - eps
        w0, w1 = max(t_entry, m0), min(t_exit, m1)
        if w1 <= w0:
            continue
        inside = rates[(rates.index > w0) & (rates.index <= w1)]
        stale = [t for t in inside.index if _price_at(close, t) is None]
        fc = funding_cost(1.0, close, rates, w0, w1)
        row = {"month": str(per), "n_settlements": int(len(inside))}
        if not fc.complete or stale:
            row.update(complete=False, unit_funding=None,
                       reason=fc.reason if not fc.complete else f"{len(stale)} settlement(s) without a ≤1 h spot price")
        else:
            row.update(complete=True, unit_funding=-fc.cost, reason="")
        start_px = _price_at(close, w0) if w0 > close.index[0] else None
        hw = high[(high.index > w0) & (high.index <= w1)]
        row["max_rise_from_month_start"] = (float(hw.max()) / start_px - 1.0) if (start_px and len(hw)) else None
        out.append(row)
    return out


def analyse_asset(symbol: str, cfg: CarryConfig, data_dir="data", zones: ResearchZones = ZONES) -> Dict:
    base = {"symbol": symbol, "classification": CLASSIFICATION, "status_label": STATUS_LABEL}
    spot, fund = load_spot(symbol, data_dir, zones), load_funding(symbol, data_dir, zones)
    for name, d in (("spot", spot), ("funding", fund)):
        if d["status"] != OK:
            return {**base, "status": d["status"], "reason": f"{name}: {d['reason']}"}
    close, high, rates, mark = spot["close"], spot["high"], fund["rates"], fund["mark"]
    usable = rates[(rates.index >= close.index[0]) & (rates.index <= close.index[-1])]
    if len(usable) < 2:
        return {**base, "status": UNAVAILABLE, "reason": "spot and funding histories do not overlap"}
    t_entry, t_exit = usable.index[0], usable.index[-1]
    s0, s1 = _price_at(close, t_entry), _price_at(close, t_exit)
    if s0 is None or s1 is None:
        return {**base, "status": UNAVAILABLE, "reason": "no ≤1 h spot price at the entry or exit settlement"}
    qty = cfg.spot_notional_usd / s0
    months = _month_rows(rates, close, high, t_entry, t_exit)
    complete = [m for m in months if m["complete"]]
    for m in months:
        m["funding_usd"] = None if m["unit_funding"] is None else qty * m["unit_funding"]
    gross = float(sum(m["funding_usd"] for m in complete))
    m0, m1 = _usable_mark(mark, close, t_entry), _usable_mark(mark, close, t_exit)   # claude code changed: 2026-10-22 — unit-guarded
    p0_proxy = m0 if m0 is not None else s0
    p1_proxy = m1 if m1 is not None else s1
    costs = round_trip_costs(qty, s0, p0_proxy, s1, p1_proxy, cfg.spot_fee_rate, cfg.perp_fee_rate, cfg.slippage_rate)
    net_a = gross - costs["total"]
    days = (t_exit - t_entry).total_seconds() / 86400.0
    max_high = float(high[(high.index > t_entry) & (high.index <= t_exit)].max())
    capital = []
    for L in cfg.short_leverages:
        margin = qty * p0_proxy / L
        k = cfg.spot_notional_usd + margin
        p_liq = short_liquidation_price(p0_proxy, L, cfg.maintenance_margin_rate)
        capital.append({"short_leverage": L, "initial_margin_usd": margin, "total_capital_usd": k,
                        "theoretical_liquidation_price": p_liq,
                        "liquidation_distance_pct": (p_liq / p0_proxy - 1) * 100,
                        "breached_without_topups": bool(max_high >= p_liq),
                        "net_on_capital_pct": net_a / k * 100,
                        "annualised_pct": None if annualise(net_a, k, days) is None else annualise(net_a, k, days) * 100})
    rises = [m["max_rise_from_month_start"] for m in months if m["max_rise_from_month_start"] is not None]
    worst_rise = max(rises) if rises else None
    sensitivity = [{"fee_multiplier": fm, "slippage_rate": sl,
                    "net_usd": gross - round_trip_costs(qty, s0, p0_proxy, s1, p1_proxy, cfg.spot_fee_rate * fm,
                                                       cfg.perp_fee_rate * fm, sl)["total"]}
                   for fm in cfg.fee_multipliers for sl in cfg.slippage_grid]
    full = _full_position(qty, close, rates, mark, t_entry, cfg, cfg.spot_notional_usd)
    worst = min(complete, key=lambda m: m["funding_usd"]) if complete else None
    return {
        **base, "status": OK, "reason": "",
        "period": {"entry_settlement": t_entry.isoformat(), "exit_settlement": t_exit.isoformat(), "days": days},
        "n_settlements": int(sum(m["n_settlements"] for m in complete)),
        "months_total": len(months), "months_complete": len(complete),
        "months_incomplete": [{"month": m["month"], "reason": m["reason"]} for m in months if not m["complete"]],
        "quantity_base_units": qty, "spot_entry": s0, "spot_exit": s1,
        "perp_price_proxy": {"entry": p0_proxy, "exit": p1_proxy,
                             "source": {(True, True): "MARK_PRICE", (False, False): "SPOT_PROXY"}.get(
                                 (m0 is not None, m1 is not None), "MIXED (mark at one end, spot proxy at the other)"),
                             "mark_unit_mismatch": bool((t_entry in mark.index and m0 is None) or (t_exit in mark.index and m1 is None))},   # claude code changed: 2026-10-22   # claude code changed: 2026-10-22 — honest label when only one end has a mark
        "scope_A_funding_only": {
            "label": "Funding minus round-trip costs; basis P&L EXCLUDED",
            "gross_funding_usd": gross, "costs_usd": costs, "net_usd": net_a,
            "net_pct_of_spot_notional": net_a / cfg.spot_notional_usd * 100,
            "worst_month": None if worst is None else {"month": worst["month"], "funding_usd": worst["funding_usd"]},
            "negative_month_pct": (100.0 * sum(m["funding_usd"] < 0 for m in complete) / len(complete)) if complete else None,
        },
        "scope_B_full_position": full,
        "capital_and_liquidation": {
            "rows": capital, "max_rise_after_entry_pct": (max_high / p0_proxy - 1) * 100,
            "worst_monthly_rise_pct": None if worst_rise is None else worst_rise * 100,
            "collateral_needed_monthly_topup_pct_of_notional":
                None if worst_rise is None else (worst_rise + cfg.maintenance_margin_rate * (1 + worst_rise)) * 100,
            "note": "Capital, not cost. Breach = spot 1h high ≥ theoretical liquidation price with no margin top-ups.",
        },
        "cost_sensitivity": sensitivity,
        "months": [{k: m[k] for k in ("month", "n_settlements", "complete", "funding_usd", "max_rise_from_month_start", "reason")}
                   for m in months],
        "data": {"spot_file": spot["path"].as_posix(), "funding_file": fund["path"].as_posix(),
                 "spot_last_close": close.index[-1].isoformat(), "funding_last_settlement": rates.index[-1].isoformat(),
                 "excluded": {"spot_sealed": spot["n_sealed_excluded"], "spot_invalid": spot["n_invalid_excluded"],
                              "spot_duplicates": spot["n_duplicates_excluded"],
                              "funding_sealed": fund["n_sealed_excluded"], "funding_duplicates": fund["n_duplicates_excluded"]}},
    }


def _full_position(qty_a, close, rates, mark, t_entry_a, cfg: CarryConfig, notional) -> Dict:
    """Scope B over the window where a recorded mark price exists at BOTH ends (perp leg priced, basis included)."""
    m = mark[(mark.index >= max(t_entry_a, close.index[0])) & (mark.index <= close.index[-1])]
    m = m[m.index.isin(rates.index)]
    if len(m) < 2:
        return {"status": UNAVAILABLE, "reason": "no recorded mark price at two settlements — basis cannot be modelled"}
    t0, t1 = m.index[0], m.index[-1]
    s0, s1 = _price_at(close, t0), _price_at(close, t1)
    if s0 is None or s1 is None:
        return {"status": UNAVAILABLE, "reason": "no ≤1 h spot price at the mark-price window ends"}
    qty = notional / s0
    fc = funding_cost(qty, close, rates, t0, t1)
    if not fc.complete:
        return {"status": UNAVAILABLE, "reason": f"funding incomplete inside the mark-price window: {fc.reason}"}
    stale = [t for t in rates[(rates.index > t0) & (rates.index <= t1)].index if _price_at(close, t) is None]
    if stale:
        return {"status": UNAVAILABLE, "reason": f"{len(stale)} settlement(s) without a ≤1 h spot price"}
    p0, p1 = _usable_mark(mark, close, t0), _usable_mark(mark, close, t1)   # claude code changed: 2026-10-22 — unit guard
    if p0 is None or p1 is None:
        return {"status": UNAVAILABLE, "reason": "recorded mark price is not in spot units at the window ends "
                                                 "(e.g. a 1000-unit perp contract) — basis not modelled"}
    costs = round_trip_costs(qty, s0, p0, s1, p1, cfg.spot_fee_rate, cfg.perp_fee_rate, cfg.slippage_rate)
    b = basis_pnl(qty, s0, p0, s1, p1)
    funding = -fc.cost
    net = funding + b - costs["total"]
    return {"status": OK, "label": "Funding + basis P&L (mark-price proxy) − round-trip costs",
            "entry_settlement": t0.isoformat(), "exit_settlement": t1.isoformat(),
            "days": (t1 - t0).total_seconds() / 86400.0, "n_settlements": fc.n_settlements,
            "funding_usd": funding, "basis_pnl_usd": b, "costs_usd": costs, "net_usd": net,
            "net_pct_of_spot_notional": net / notional * 100}


# ── run + persist ──────────────────────────────────────────────────────────────────────────────────────────────
def discover_symbols(data_dir="data") -> List[str]:
    return sorted(p.name.replace("_funding.csv", "").replace("_", "/")
                  for p in Path(data_dir).glob("*_USDT_funding.csv"))


def run_monitor(symbols: Optional[Sequence[str]] = None, cfg: CarryConfig = CarryConfig(), data_dir="data",
                out_dir=DEFAULT_OUT_DIR, zones: ResearchZones = ZONES, write: bool = True) -> Dict:
    from bot.quant_core.provenance import code_state, config_hash, file_sha256, input_files_fingerprint, make_run_id
    symbols = list(symbols) if symbols is not None else discover_symbols(data_dir)
    assets = [analyse_asset(s, cfg, data_dir, zones) for s in symbols]
    inputs = [Path(data_dir) / f"{_stem(s)}{suffix}" for s in symbols for suffix in ("_1h.csv", "_funding.csv")]
    fp = input_files_fingerprint([p for p in inputs if p.exists()])
    code = code_state()
    cfg_d = cfg.as_dict()
    module_sha = file_sha256(__file__)   # claude code changed: 2026-10-22 — an untracked/edited module is not in the git digest; pin its bytes
    run_id = make_run_id(version=MONITOR_VERSION, funding_model=FUNDING_MODEL_VERSION, config=config_hash(cfg_d),
                         data=fp["version"], zones=zones.zones_hash(), commit=code.commit, dirty=code.dirty_digest,
                         module=module_sha)
    report = {
        "kind": KIND, "monitor_version": MONITOR_VERSION, "funding_model_version": FUNDING_MODEL_VERSION,
        "run_id": run_id, "classification": CLASSIFICATION, "status_label": STATUS_LABEL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code": {"commit": code.commit, "dirty": code.dirty, "module_sha256": module_sha},   # claude code changed: 2026-10-22
        "zones": {"holdout_start_F": zones.F.isoformat(), "zones_hash": zones.zones_hash(),
                  "rule": "no observation at or after F is read; all earlier history is PSEUDO_HOLDOUT"},
        "config": cfg_d, "inputs_version": fp["version"], "limitations": list(LIMITATIONS),
        "n_assets": len(assets), "n_ok": sum(a["status"] == OK for a in assets), "assets": assets,
    }
    if write:
        out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
        rp = out / REPORT_NAME
        rp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        rows = [{"symbol": a["symbol"], **m} for a in assets if a["status"] == OK for m in a["months"]]
        mp = out / MONTHLY_NAME
        pd.DataFrame(rows).to_csv(mp, index=False)
        from bot.quant_core.provenance import write_research_output_provenance
        for art in (rp, mp):
            write_research_output_provenance(art, kind=KIND, producer=__name__, inputs=fp,
                                             config={**cfg_d, "monitor_version": MONITOR_VERSION}, code=code)
    return report


def load_report(out_dir=DEFAULT_OUT_DIR) -> Optional[Dict]:
    """The persisted report for the UI (None when no run exists — the page says so, it never recomputes)."""
    p = Path(out_dir) / REPORT_NAME
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
