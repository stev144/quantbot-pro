# bot/research/funding_carry_rule.py  # claude code changed: 2026-10-28 — new: crypto funding carry with a cost hurdle
"""A cost-hurdle switch for the funding carry position LONG spot + SHORT USDⓈ-M perpetual (same base quantity).
Mechanism: when perpetuals trade rich, longs pay shorts funding every settlement; the hedged position collects it
while staying roughly price-neutral. The monitor (funding_carry_monitor.py) values ONE hold from entry to exit; this
module asks the next question: does a rule that is IN only while recent funding clears the round-trip cost keep
enough of the funding after paying to switch?

Rule (declared before running; ENTRY_MULTIPLE = 1 primary, 3 = sensitivity only):
    F7_k        = Σ funding rates of the settlements in (t_k − 7 days, t_k]          (rates known at t_k)
    enter       when flat and F7_k ≥ ENTRY_MULTIPLE · RT      — last week's funding alone repaid a full round trip
    exit        when in and F7_k < 0                          — funding has turned against the position
    timing      a decision at settlement k takes effect after k: funding is first received at k+1
    RT          round-trip cost per unit notional = 2·(spot fee + perp taker fee + 2·slippage) = 0.5 % at base tier
    money       per $1 of notional at entry: funding_k = r_k · S_k / S_entry (paid on the perp, priced at spot ≈ perp);
                entry cost RT/2, exit cost RT/2 · S_exit / S_entry
    capital     spot notional + perp initial margin at LEVERAGE (1 + 1/L per $1 notional)
    liquidation theoretical isolated short P_liq = P_0 (1 + 1/L)/(1 + m) (monitor's formula), checked against spot
                1-hour highs while in, with no margin top-up — counted, not simulated
REUSED: funding_carry_monitor.load_spot / load_funding / short_liquidation_price / discover_symbols; costs from
bot.config.execution_costs. DESCRIPTIVE: pseudo-holdout history (nothing at or after the crypto seal F is read),
today's listed coins only (survivorship, D-6). Basis (perp − spot) P&L is not modelled.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from bot.config.execution_costs import FEE_RATE, PERP_TAKER_FEE_RATE, SLIPPAGE_RATE
from bot.research import funding_carry_monitor as M

RT = M.round_trip_costs(1.0, 1.0, 1.0, 1.0, 1.0, FEE_RATE, PERP_TAKER_FEE_RATE, SLIPPAGE_RATE)["total"]   # claude code changed: 2026-10-28 — from the monitor's own cost function (one source), = 0.005 at base tier
WINDOW = pd.Timedelta(days=7)
LEVERAGE = 2.0
MAINTENANCE = 0.004


def trailing_sum(rates: pd.Series, window: pd.Timedelta = WINDOW) -> pd.Series:
    """Sum of rates in (t − window, t] at each settlement t (time-based, so 4 h and 8 h schedules both work).
    NaN until a FULL window of history exists (a partial first week would understate the sum)."""
    r = rates.sort_index()
    s = r.rolling(window, closed="right").sum()
    return s.where(s.index >= r.index[0] + window) if len(r) else s   # claude code changed: 2026-10-28 — full window only


def run_rule(rates: pd.Series, close: pd.Series, high: Optional[pd.Series] = None, entry_multiple: float = 1.0,
             rt: float = RT, leverage: float = LEVERAGE, reset_runup: Optional[float] = None) -> Dict:
    """Simulate the switch on one coin. Returns trades and a per-settlement ledger (all per $1 entry notional).
    reset_runup: when the spot close has risen this fraction above the entry price, the hedge is closed and reopened
    at the current price (paying a full round trip) — the margin management a real short needs, since a fixed-
    quantity short is liquidated near +1/L. None = no reset (the liquidation count then shows the exposure)."""   # claude code changed: 2026-10-28
    rates = rates.sort_index()
    f7 = trailing_sum(rates)
    price = close.sort_index()
    p_at = price.reindex(rates.index, method="ffill")            # latest spot close at or before each settlement
    in_pos, entry_t, s_entry, acc = False, None, None, 0.0
    trades: List[Dict] = []
    rows = []
    ts = rates.index
    pending = None                                               # decision made at k, applied after k
    for k, t in enumerate(ts):
        s = p_at.iloc[k]
        if not np.isfinite(s):
            continue
        if pending == "ENTER":
            in_pos, entry_t, s_entry, acc = True, t, s, -rt / 2.0
        elif pending == "EXIT":
            acc -= rt / 2.0 * s / s_entry
            trades.append(_close_trade(entry_t, t, s_entry, s, acc, high, leverage))
            in_pos, acc = False, 0.0
        pending = None
        if in_pos and reset_runup is not None and s / s_entry - 1.0 >= reset_runup:   # re-hedge at the new price
            acc -= rt / 2.0 * s / s_entry
            trades.append({**_close_trade(entry_t, t, s_entry, s, acc, high, leverage), "reset": True})
            entry_t, s_entry, acc = t, s, -rt / 2.0
        cash = 0.0
        if in_pos and t > entry_t:
            cash = rates.iloc[k] * s / s_entry                   # funding of settlement t, received by the short
            acc += cash
        rows.append({"t": t, "in": in_pos, "f7": f7.iloc[k], "funding_cash": cash,
                     "margin_needed": (s / s_entry - 1.0) if in_pos else 0.0})   # extra margin to keep the short alive
        if not in_pos and f7.iloc[k] >= entry_multiple * rt:
            pending = "ENTER"
        elif in_pos and f7.iloc[k] < 0:
            pending = "EXIT"
    if in_pos:                                                   # still open at the data end: closed at the last price
        s = p_at.dropna().iloc[-1]
        acc -= rt / 2.0 * s / s_entry
        trades.append({**_close_trade(entry_t, ts[-1], s_entry, s, acc, high, leverage), "open_at_end": True})
    return {"trades": trades, "ledger": pd.DataFrame(rows).set_index("t") if rows else pd.DataFrame()}


def _close_trade(t0, t1, s0, s1, net, high, leverage) -> Dict:
    liq = M.short_liquidation_price(s0, leverage, MAINTENANCE)
    hi = high.loc[t0:t1].max() if high is not None and len(high.loc[t0:t1]) else np.nan
    return {"entry": t0, "exit": t1, "days": (t1 - t0).total_seconds() / 86400, "net": float(net),
            "funding_over_cost": None, "liq_price": liq, "max_spot_high": float(hi) if np.isfinite(hi) else None,
            "liquidation_breach": bool(np.isfinite(hi) and hi >= liq)}


def summarise_coin(symbol: str, res: Dict, entry_multiple: float, rt: float = RT) -> Dict:
    tr = pd.DataFrame(res["trades"])
    led = res["ledger"]
    out = {"symbol": symbol, "entry_multiple": entry_multiple, "n_trades": int(len(tr)),
           "pct_time_in": float(led["in"].mean() * 100) if len(led) else 0.0}
    if len(tr):
        out.update({"net_total": float(tr["net"].sum()), "net_per_trade": float(tr["net"].mean()),
                    "win_rate": float((tr["net"] > 0).mean()),
                    "funding_over_rt_per_trade": float(((tr["net"] + rt) / rt).mean()),   # gross funding ÷ round trip
                    "liquidation_breaches": int(tr["liquidation_breach"].sum())})
        tr["year"] = pd.DatetimeIndex(tr["exit"]).year
        out["net_by_exit_year"] = tr.groupby("year")["net"].sum().round(5).to_dict()
    return out


def run_universe(symbols: Optional[List[str]] = None, entry_multiples=(1.0, 3.0), data_dir="data") -> Dict:
    symbols = symbols or M.discover_symbols(data_dir)
    coins, skipped = [], {}
    for sym in symbols:
        sp, fu = M.load_spot(sym, data_dir), M.load_funding(sym, data_dir)
        if sp["status"] != M.OK or fu["status"] != M.OK:
            skipped[sym] = sp.get("reason") or fu.get("reason")
            continue
        for em in entry_multiples:
            coins.append(summarise_coin(sym, run_rule(fu["rates"], sp["close"], sp["high"], em), em))
    return {"coins": coins, "skipped": skipped, "rt": RT, "leverage": LEVERAGE}


def portfolio_by_year(coins: List[Dict], entry_multiple: float) -> pd.DataFrame:
    """Equal-weight across coins: mean net (per $1 notional) of trades closed in each year, and on capital at LEVERAGE."""
    rows = [{"symbol": c["symbol"], "year": y, "net": v} for c in coins if c["entry_multiple"] == entry_multiple
            for y, v in (c.get("net_by_exit_year") or {}).items()]
    n = len({c["symbol"] for c in coins if c["entry_multiple"] == entry_multiple})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).groupby("year")["net"].sum() / n          # coins with no trade that year count as 0
    return pd.DataFrame({"net_pct_notional": df * 100, "net_pct_capital": df / (1 + 1 / LEVERAGE) * 100}).round(3)
