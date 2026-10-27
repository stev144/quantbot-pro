# bot/views/paper_trading.py  # claude code changed: new — Ranging Paper Trader dashboard
"""
Django view for the Ranging Paper Trader page.

Runs the ranging-gated DCA simulation for every QC_PORTFOLIO_SPECS pair and
compares it against the unrestricted QC-1 baseline on the same data.  Cached
1 hour — first load takes ~20 s.

Governance (G5): IS_PAPER_ONLY=True enforced in the engine module.  Results are
exploratory — the ranging gate was NOT pre-registered in QC-1's protocol.
"""
import json
import logging

from django.core.cache import cache
from django.shortcuts import render

logger = logging.getLogger(__name__)

_CACHE_KEY = "paper_trading_results_v6"  # claude code changed: v6 — 2026-10-27 GBP/JPY MT5 costs (was v5: ranging gate removed)
_CACHE_TTL = 3600


def cached_paper_summary():  # claude code changed: 2026-10-25 — summary card for the Markets page (phone)
    """Totals of the Paper Trader's last cached run, or None when it has not run in this server process.
    Reads the cache only — never starts the simulation (up to a minute), so the Markets page stays fast."""
    results = cache.get(_CACHE_KEY)
    if not results:
        return None
    ok = [r for r in results.values() if "error" not in r]
    if not ok:
        return None
    return {
        "net_pnl":     round(sum(r["stats"]["total_pnl"] for r in ok), 2),
        "net_pnl_abs": abs(round(sum(r["stats"]["total_pnl"] for r in ok), 2)),   # the template shows the sign itself
        "trades":      sum(r["stats"]["total_trades"] for r in ok),
        "pairs":       len(ok),
        "open_cycles": sum(1 for r in ok if r.get("open_position")),
        "open_pnl":    round(sum(r["open_position"]["profit_usdt"] for r in ok if r.get("open_position")), 2),
    }


def max_drawdown_pct(equity):  # claude code changed: 2026-10-27 — drawdown % in brackets (owner rule)
    """Deepest fall of the cumulative-P&L curve as a % of its running peak (no account capital exists here).
    None when the curve never had a positive peak."""
    peak, worst = None, None
    for pt in equity or []:
        p = pt["p"]
        peak = p if peak is None else max(peak, p)
        if peak > 0:
            pct = (p - peak) / peak * 100
            worst = pct if worst is None else min(worst, pct)
    return round(worst, 1) if worst is not None else None


def paper_trading(request):  # claude code changed: new — ranging paper trader view
    error   = None
    results = cache.get(_CACHE_KEY)

    if results is None:
        try:
            from bot.paper_trading.ranging_paper_engine import run_ranging_paper_simulation
            results = run_ranging_paper_simulation()
            cache.set(_CACHE_KEY, results, _CACHE_TTL)
        except Exception as exc:
            logger.exception("paper_trading view: simulation failed")
            error   = str(exc)
            results = {}

    # build per-pair JSON for JS charts  # claude code changed: v5 — single equity/drawdown (no gated/free split)
    pairs_data = {}
    for pair, r in results.items():
        if "error" not in r:
            pairs_data[pair] = {
                "equity":     r.get("equity",    []),
                "equity_settled": r.get("equity_settled", []),   # claude code changed: 2026-10-27 — balance (closed trades) line
                "drawdown":   r.get("drawdown",  []),
                "fill_dist":  r.get("fill_dist", {}),
                "ladder_depth": r.get("ladder_depth", 0),
            }

    pair_list = [p for p in results if "error" not in results[p]]
    for p in pair_list:   # claude code changed: 2026-10-27 — max drawdown as % of peak cumulative P&L
        results[p]["stats"]["max_dd_pct"] = max_drawdown_pct(results[p].get("equity"))

    # aggregate capital stats for page header  # claude code changed: leverage-aware capital summary
    entry_usdt   = None
    leverage     = None
    entry_notional = None
    max_margins  = []
    for r in results.values():
        if "error" not in r:
            entry_usdt     = r.get("entry_usdt")
            leverage       = r.get("leverage")
            entry_notional = r.get("entry_notional")
            mm = r.get("max_margin")
            if mm:
                max_margins.append(mm)
    total_max_margin = round(sum(max_margins), 2) if max_margins else None
    # stats key changed from stats_gated/stats_free to stats (single simulation)

    context = {
        "error":             error,
        "results":           results,
        "pair_list":         pair_list,
        "pairs_json":        json.dumps(pairs_data),
        "entry_usdt":        entry_usdt,        # claude code changed: margin per cycle
        "leverage":          leverage,          # claude code changed: 30
        "entry_notional":    entry_notional,    # claude code changed: $3,000
        "total_max_margin":  total_max_margin,  # claude code changed: sum of max margin across all pairs
    }
    return render(request, "paper_trading.html", context)
