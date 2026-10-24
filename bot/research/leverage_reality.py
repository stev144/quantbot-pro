# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/leverage_reality.py
#
# claude code changed: new file — 2026-10-22, "Leverage Reality" Phase 2 (owner brief). EDUCATIONAL. Two separate
# outputs that are never mixed:
#
#  (1) ladder_scenario() — a DETERMINISTIC, theoretical calculator for a LINEAR USDⓈ-margined perpetual (quantity in
#      base units, P&L in USDT). Inverse (coin-margined) contracts are NOT supported and are refused.
#      For a position of total quantity Q, weighted-average entry A, direction d (+1 long, −1 short), margin backing B
#      (isolated: the position's own margin; cross: the whole account equity after fees) and ONE flat maintenance
#      rate m, the position is liquidated when   B + d·Q·(P − A) = m·Q·P,   i.e.
#           LONG   P_liq = (Q·A − B) / (Q·(1 − m))
#           SHORT  P_liq = (Q·A + B) / (Q·(1 + m))
#      A step of margin M at leverage L adds notional M·L and quantity M·L / price. Fees are charged on notional
#      (taker rate, per side) and leave the wallet. This is a THEORETICAL approximation: real exchanges use tiered
#      maintenance margins, mark-price triggers, liquidation fees, partial liquidation and auto-deleveraging; the
#      liquidation price is a trigger level, not a guaranteed exit price.
#
#  (2) historical_*() — DESCRIPTIVE simulations on recorded Binance spot 1-hour candles strictly before the sealed
#      crypto holdout F (2026-10-04). Definitions:
#        WIPEOUT      equity at a bar's adverse extreme ≤ maintenance requirement (the exchange would liquidate);
#                     the whole margin backing the position is counted as lost.
#        TAKE_PROFIT  the bar's favourable extreme reached the take-profit level; filled exactly at that level.
#        UNRESOLVED   neither within the horizon — reported separately, never counted as a win or a loss.
#      Intrabar order is unknown in 1-hour bars, so two bounds are reported:
#        conservative  favourable side first using the PRE-bar average, then adverse fills and the liquidation check
#                      (a bar never fills a step AND takes profit on the new average);
#        optimistic    adverse fills first, then take profit on the updated average.
#      Ladder limit orders fill exactly at their level (no gap improvement); funding is EXCLUDED from the ladder
#      (cycles are short; for longs positive funding is a cost, so wipeouts are if anything understated) and
#      INCLUDED in the hold table from the recorded series. Spot candles stand in for the mark price.
#      Entries overlap in time and across correlated coins: shares are reported with a time-block bootstrap interval
#      (bot.quant_core.inference.time_block_bootstrap_mean_ci, descriptive) — they are NOT future probabilities.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from bot.config.execution_costs import PERP_TAKER_FEE_RATE
from bot.quant_core.research_zones import ZONES, ResearchZones, to_utc

VERSION = "SQT-LEVREAL-1"
KIND = "LEVERAGE_REALITY_HISTORICAL"
CLASSIFICATION = "DESCRIPTIVE — PSEUDO_HOLDOUT history, not claim-eligible; not a forecast"
MAX_LEVERAGE, MAX_STEPS = 125.0, 10
DEFAULT_MAINTENANCE = 0.005
DEFAULT_OUT_DIR = Path("research_data") / "leverage_reality"
SUMMARY_NAME = "historical_summary.json"
LONG, SHORT = "LONG", "SHORT"
ISOLATED, CROSS = "ISOLATED", "CROSS"
WIPEOUT, TAKE_PROFIT, UNRESOLVED = "WIPEOUT", "TAKE_PROFIT", "UNRESOLVED"


class ScenarioError(ValueError):
    """An input the calculator cannot model honestly — shown to the user, never silently adjusted."""


def liquidation_price(direction: str, qty: float, avg_entry: float, backing: float, maintenance: float) -> Optional[float]:
    """Theoretical liquidation price (see header). None when the backing already covers any price (long with
    B ≥ Q·A·(1 − m)… i.e. no positive solution)."""
    if qty <= 0 or avg_entry <= 0 or not (0 <= maintenance < 1):
        raise ScenarioError("quantity and average entry must be positive; maintenance in [0, 1)")
    if direction == LONG:
        p = (qty * avg_entry - backing) / (qty * (1 - maintenance))
        return p if p > 0 else None
    if direction == SHORT:
        return (qty * avg_entry + backing) / (qty * (1 + maintenance))
    raise ScenarioError("direction must be LONG or SHORT")


@dataclass(frozen=True)
class ScenarioInput:
    account_equity: float
    entry_price: float
    leverage: float
    step_margins: tuple                   # USDT margin committed at each step (step 1 = the initial position)
    step_spacing_pct: float = 1.0         # adverse move between consecutive steps, % of the previous step price
    direction: str = LONG
    margin_mode: str = CROSS
    maintenance_rate: float = DEFAULT_MAINTENANCE
    taker_fee_rate: float = PERP_TAKER_FEE_RATE
    contract_type: str = "LINEAR"

    def validate(self) -> None:
        if self.contract_type != "LINEAR":
            raise ScenarioError("only linear USDⓈ-margined contracts are modelled (inverse contracts are not)")
        for name in ("account_equity", "entry_price", "leverage", "step_spacing_pct", "maintenance_rate", "taker_fee_rate"):
            v = getattr(self, name)
            if not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ScenarioError(f"{name} must be a finite number")
        if self.account_equity <= 0 or self.entry_price <= 0:
            raise ScenarioError("account equity and entry price must be positive")
        if not (1 <= self.leverage <= MAX_LEVERAGE):
            raise ScenarioError(f"leverage must be between 1× and {MAX_LEVERAGE:g}×")
        if not (1 <= len(self.step_margins) <= MAX_STEPS):
            raise ScenarioError(f"between 1 and {MAX_STEPS} ladder steps")
        if any((not isinstance(m, (int, float))) or not math.isfinite(m) or m <= 0 for m in self.step_margins):
            raise ScenarioError("every step margin must be a positive number")
        if len(self.step_margins) > 1 and not (0 < self.step_spacing_pct < 50):
            raise ScenarioError("step spacing must be between 0 % and 50 %")
        if self.direction not in (LONG, SHORT):
            raise ScenarioError("direction must be LONG or SHORT")
        if self.margin_mode not in (ISOLATED, CROSS):
            raise ScenarioError("margin mode must be ISOLATED or CROSS")
        if not (0 <= self.maintenance_rate < 0.5) or not (0 <= self.taker_fee_rate < 0.01):
            raise ScenarioError("maintenance rate must be in [0, 0.5) and the fee rate in [0, 0.01)")


def ladder_scenario(inp: ScenarioInput) -> Dict:
    """Step-by-step exposure, margin, equity and theoretical liquidation price of a DCA ladder. Stops at the step
    where (a) the account cannot pay the next step's margin and fee, or (b) the liquidation price would be reached
    before the next step's price — both reported, never silently skipped."""
    inp.validate()
    d = 1 if inp.direction == LONG else -1
    qty = cost = margin = fees = 0.0
    rows: List[Dict] = []
    stop = None
    price = inp.entry_price
    for k, m in enumerate(inp.step_margins, start=1):
        if k > 1:
            price = price * (1 - d * inp.step_spacing_pct / 100.0)
        notional = m * inp.leverage
        fee = notional * inp.taker_fee_rate
        # available balance = wallet − fees − margin in use (+ unrealised LOSS in cross mode; profits never count)
        unreal_now = d * qty * (price - cost / qty) if qty else 0.0                      # claude code changed: 2026-10-22
        available = inp.account_equity - fees - margin + (min(unreal_now, 0.0) if inp.margin_mode == CROSS else 0.0)
        if available + 1e-9 < m + fee:
            stop = {"step": k, "reason": f"insufficient available balance: step {k} needs ${m:,.2f} margin + ${fee:,.2f} fee, "
                                        f"${max(available, 0):,.2f} available"}
            break
        if rows:
            prev_liq = rows[-1]["liquidation_price"]
            if prev_liq is not None and ((d == 1 and price <= prev_liq) or (d == -1 and price >= prev_liq)):
                stop = {"step": k, "reason": f"liquidated before step {k} could fill (liquidation at {prev_liq:,.6g}, step at {price:,.6g})"}
                break
        q = notional / price
        qty += q; cost += q * price; margin += m; fees += fee
        avg = cost / qty
        backing = margin if inp.margin_mode == ISOLATED else inp.account_equity - fees
        liq = liquidation_price(inp.direction, qty, avg, backing, inp.maintenance_rate)
        unreal = d * qty * (price - avg)
        rows.append({
            "step": k, "price": price, "margin_added": m, "notional_added": notional, "qty_added": q,
            "cumulative_qty": qty, "cumulative_notional_at_entry": cost, "weighted_avg_entry": avg,
            "cumulative_margin": margin, "fees_paid": fees,
            "equity_at_step_price": inp.account_equity - fees + unreal,
            "free_equity": inp.account_equity - fees - margin + (unreal if inp.margin_mode == CROSS else 0.0),
            "liquidation_price": liq,
            "distance_to_liquidation_pct": None if liq is None else (liq / price - 1) * 100,
            "distance_from_first_entry_pct": None if liq is None else (liq / inp.entry_price - 1) * 100,
            "effective_leverage_on_equity": cost / inp.account_equity,
        })
    if not rows:
        raise ScenarioError(stop["reason"] if stop else "no step could be placed")
    last = rows[-1]
    sensitivity = []
    for mv in (1, 2, 3, 5, 7, 10, 15, 20, 30):
        p = inp.entry_price * (1 - d * mv / 100.0)
        eq = (inp.account_equity - fees + d * last["cumulative_qty"] * (p - last["weighted_avg_entry"])
              if inp.margin_mode == CROSS else last["cumulative_margin"] + d * last["cumulative_qty"] * (p - last["weighted_avg_entry"]))
        liq_hit = last["liquidation_price"] is not None and ((d == 1 and p <= last["liquidation_price"]) or (d == -1 and p >= last["liquidation_price"]))
        sensitivity.append({"adverse_move_pct_from_first_entry": mv, "price": p, "equity_or_position_margin": eq,
                            "liquidated": bool(liq_hit)})
    return {"input": {**inp.__dict__, "step_margins": list(inp.step_margins)}, "steps": rows, "stopped": stop,
            "sensitivity": sensitivity, "model": "THEORETICAL — flat maintenance rate, linear contract, no liquidation "
            "fee, no funding, liquidation price is a trigger level not an exit price"}


# ── historical, descriptive ────────────────────────────────────────────────────────────────────────────────────
def _load_spot(path: Path, zones: ResearchZones):
    df = pd.read_csv(path, usecols=lambda c: c in ("timestamp", "open", "high", "low", "close"))
    opens = to_utc(pd.Index(df["timestamp"]))
    keep = np.asarray(opens < zones.F, dtype=bool)
    df, opens = df.loc[keep].reset_index(drop=True), opens[keep]
    zones.assert_no_sealed_data(opens, purpose=f"leverage reality {path.name}")
    a = {k: df[k].to_numpy(float) for k in ("open", "high", "low", "close")}
    ok = np.isfinite(a["high"]) & np.isfinite(a["low"]) & np.isfinite(a["close"]) & (a["low"] > 0)
    if not ok.all():
        a = {k: v[ok] for k, v in a.items()}
        opens = opens[ok]
    return opens, a


def _ladder_cycle(h, l, c, i, H, margins, L, s, tp, maint, fee, equity, conservative: bool):
    """One LONG cross-margin ladder from close[i]. Returns (outcome, pnl, fills)."""
    levels = [c[i] * (1 - k * s) for k in range(len(margins))]
    qty = cost = fees = 0.0
    nf = 0

    used = 0.0
    stuck = False

    def fill(k):
        nonlocal qty, cost, fees, nf, used
        q = margins[k] * L / levels[k]
        qty += q; cost += q * levels[k]; fees += fee * margins[k] * L; nf += 1; used += margins[k]

    def fundable(k):   # claude code changed: 2026-10-22 — same rule as ladder_scenario (cross: unrealised loss reduces it)
        avail = equity - fees - used + min(qty * levels[k] - cost, 0.0)
        return avail >= margins[k] * (1 + L * fee)

    def wiped(px):
        return equity - fees + qty * px - cost <= maint * qty * px

    fill(0)
    end = min(i + 1 + H, len(c))
    for t in range(i + 1, end):
        if conservative:
            tp_px = cost / qty * (1 + tp)
            if h[t] >= tp_px:
                return TAKE_PROFIT, qty * tp_px - cost - fees - fee * qty * tp_px, nf
        while not stuck and nf < len(margins) and l[t] <= levels[nf]:
            if wiped(levels[nf]):
                return WIPEOUT, -equity, nf
            if not fundable(nf):
                stuck = True                       # the ladder cannot fund this step; the position runs as filled
                break
            fill(nf)
        if wiped(l[t]):
            return WIPEOUT, -equity, nf
        if not conservative:
            tp_px = cost / qty * (1 + tp)
            if h[t] >= tp_px:
                return TAKE_PROFIT, qty * tp_px - cost - fees - fee * qty * tp_px, nf
    return UNRESOLVED, None, nf


def _bootstrap_share(flags: np.ndarray, times: pd.DatetimeIndex, seed: int) -> Dict:
    from bot.quant_core.inference import time_block_bootstrap_mean_ci
    r = time_block_bootstrap_mean_ci(flags.astype(float), times, block_hours=30 * 24, seed=seed)
    return {"lo": r.get("lo"), "hi": r.get("hi"), "ok": r.get("ok"), "n_blocks": r.get("n_blocks"),
            "method": "time-block bootstrap, 30-day calendar blocks across all coins (descriptive)"}


def historical_ladder(files: Sequence[Path], *, equity=1000.0, margins=(100.0, 200.0, 300.0, 400.0),
                      leverages=(20.0, 50.0), spacings=(0.01, 0.02), tp=0.01, maint=DEFAULT_MAINTENANCE,
                      fee=PERP_TAKER_FEE_RATE, horizon_h=24 * 30, step_h=48, zones: ResearchZones = ZONES) -> List[Dict]:
    data = []
    for f in files:
        opens, a = _load_spot(Path(f), zones)
        if len(a["close"]) > horizon_h + 200:
            data.append((Path(f).name, opens, a))
    out = []
    for L in leverages:
        for s in spacings:
            for conservative in (True, False):
                outcomes, pnls, times = [], [], []
                for name, opens, a in data:
                    for i in range(200, len(a["close"]) - 1, step_h):
                        o, p, _ = _ladder_cycle(a["high"], a["low"], a["close"], i, horizon_h, margins, L, s, tp, maint, fee, equity, conservative)
                        outcomes.append(o); pnls.append(p); times.append(opens[i] + pd.Timedelta(hours=1))
                oc = np.array(outcomes)
                resolved = oc != UNRESOLVED
                wipe = oc == WIPEOUT
                times = pd.DatetimeIndex(times)
                pr = np.array([p for p, r in zip(pnls, resolved) if r], float)
                share = float(wipe[resolved].mean()) if resolved.any() else None
                out.append({
                    "leverage": L, "spacing_pct": s * 100, "bound": "conservative" if conservative else "optimistic",
                    "cycles": int(len(oc)), "unresolved_at_30d": int((~resolved).sum()),
                    "wipeout_share_of_resolved": share,
                    "wipeout_share_interval": _bootstrap_share(wipe[resolved], times[resolved], seed=int(L * 1000 + s * 1e4 + conservative)),
                    "cycles_per_wipeout": (1 / share) if share else None,
                    "avg_take_profit_usd": float(pr[pr > -equity].mean()) if (pr > -equity).any() else None,
                    "mean_result_per_resolved_cycle_usd": float(pr.mean()) if len(pr) else None,
                })
    return out


def historical_hold(files: Sequence[Path], data_dir, *, leverages=(1, 3, 5, 10, 20, 50), horizon_h=24 * 30, step_h=24,
                    maint=DEFAULT_MAINTENANCE, fee=PERP_TAKER_FEE_RATE, zones: ResearchZones = ZONES) -> List[Dict]:
    """Isolated LONG held 30 days from random entries. Liquidated when the spot low reaches
    P0·(1 − 1/L)/(1 − m) (isolated long, B = Q·P0/L); funding from the recorded series, entry/exit fees on notional."""
    from bot.quant_core.funding import load_funding_series
    recs = []
    for f in files:
        sym = Path(f).name.replace("_1h.csv", "").replace("_", "/")
        try:
            fund = load_funding_series(sym, data_dir)
        except (FileNotFoundError, ValueError):
            continue
        fund = fund[fund.index < zones.F]
        opens, a = _load_spot(Path(f), zones)
        n = len(a["close"])
        if n < horizon_h + 200:
            continue
        lows = pd.Series(a["low"])
        fut_min = lows[::-1].rolling(horizon_h, min_periods=horizon_h).min()[::-1].shift(-1).to_numpy()
        fcum = fund.cumsum()
        from bot.quant_core.funding import MAX_FUNDING_GAP
        gap_after = np.r_[np.asarray((fund.index[1:] - fund.index[:-1]) > MAX_FUNDING_GAP), False]   # claude code changed: 2026-10-22
        gaps_cum = np.cumsum(gap_after)                 # claude code changed: 2026-10-22 — a window spanning a funding gap is skipped
        for i in range(200, n - horizon_h - 1, step_h):
            t0, t1 = opens[i] + pd.Timedelta(hours=1), opens[i + horizon_h] + pd.Timedelta(hours=1)
            lo_k, hi_k = fcum.index.searchsorted(t0, "right"), fcum.index.searchsorted(t1, "right")
            if lo_k == 0 or hi_k >= len(fcum):        # funding must cover the window — never assume zero
                continue
            n_gaps = gaps_cum[hi_k - 1] - (gaps_cum[lo_k - 2] if lo_k >= 2 else 0)   # gaps between settlements lo_k−1 … hi_k
            if n_gaps > 0:   # claude code changed: 2026-10-22 — a missing settlement inside (t0, t1] — never assume zero
                continue
            fsum = float(fcum.iloc[hi_k - 1] - fcum.iloc[lo_k - 1])
            recs.append((t0, a["close"][i], fut_min[i], a["close"][i + horizon_h], fsum))
    R = pd.DataFrame(recs, columns=["t0", "p0", "min_low", "p1", "funding_sum"])
    out = []
    for L in leverages:
        if L == 1:
            liq = np.zeros(len(R), bool)
            ret = R.p1 / R.p0 - 1 - 2 * fee
        else:
            p_liq = R.p0 * (1 - 1 / L) / (1 - maint)
            liq = (R.min_low <= p_liq).to_numpy()
            ret = np.where(liq, -1.0, L * (R.p1 / R.p0 - 1) - L * R.funding_sum - 2 * fee * L)
        ret = pd.Series(ret)
        out.append({"leverage": L, "windows": int(len(R)), "liquidated_share": float(liq.mean()) if len(R) else None,
                    "liquidated_share_interval": _bootstrap_share(liq, pd.DatetimeIndex(R.t0), seed=int(L)) if len(R) else None,
                    "median_return_on_margin_pct": float(ret.median() * 100) if len(R) else None,
                    "mean_return_on_margin_pct": float(ret.mean() * 100) if len(R) else None,
                    "share_positive": float((ret > 0).mean()) if len(R) else None})
    return out


def run_historical(data_dir="data", out_dir=DEFAULT_OUT_DIR, zones: ResearchZones = ZONES, write=True,
                   files: Optional[Sequence[Path]] = None) -> Dict:
    from bot.quant_core.provenance import code_state, file_sha256, input_files_fingerprint, make_run_id, write_research_output_provenance
    files = sorted(files if files is not None else Path(data_dir).glob("*_USDT_1h.csv"))
    fp = input_files_fingerprint(files)
    code = code_state()
    module_sha = file_sha256(__file__)
    config = {"version": VERSION, "ladder": {"equity": 1000.0, "margins": [100, 200, 300, 400], "leverages": [20, 50],
              "spacings_pct": [1, 2], "take_profit_pct_over_avg": 1, "horizon_days": 30, "entry_every_h": 48},
              "hold": {"leverages": [1, 3, 5, 10, 20, 50], "horizon_days": 30, "entry_every_h": 24},
              "maintenance_rate": DEFAULT_MAINTENANCE, "taker_fee_rate": PERP_TAKER_FEE_RATE}
    run_id = make_run_id(config=json.dumps(config, sort_keys=True), data=fp["version"], zones=zones.zones_hash(),
                         commit=code.commit, dirty=code.dirty_digest, module=module_sha)
    ladder = historical_ladder(files, zones=zones)
    hold = historical_hold(files, data_dir, zones=zones)
    res = {"kind": KIND, "version": VERSION, "run_id": run_id, "classification": CLASSIFICATION,
           "generated_at": datetime.now(timezone.utc).isoformat(), "config": config,
           "data": {"source": "Binance spot 1h candles (recorded), funding from data/*_funding.csv",
                    "n_files": len(files), "inputs_version": fp["version"], "before": zones.F.isoformat()},
           "code": {"commit": code.commit, "dirty": code.dirty, "module_sha256": module_sha},
           "ladder": ladder, "hold": hold,
           "warnings": ["Past shares are not future probabilities.",
                        "Spot candles stand in for the mark price; real liquidations use mark price and tiered margins.",
                        "Coins listed today only (survivorship): delisted coins that collapsed are missing, which flatters leverage.",
                        "Windows overlap and coins move together; intervals use 30-day time blocks and are descriptive."]}
    if write:
        out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
        p = out / SUMMARY_NAME
        p.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
        write_research_output_provenance(p, kind=KIND, producer=__name__, inputs=fp, config=config, code=code)
    return res


def load_historical(out_dir=DEFAULT_OUT_DIR) -> Optional[Dict]:
    p = Path(out_dir) / SUMMARY_NAME
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
