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

_CACHE_KEY = "paper_trading_results_v4"  # claude code changed: v4 — 1:30 leverage applied to simulation
_CACHE_TTL = 3600


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

    # build per-pair JSON for JS charts  # claude code changed: added drawdown + fill_dist + capital fields
    pairs_data = {}
    for pair, r in results.items():
        if "error" not in r:
            pairs_data[pair] = {
                "equity_gated":   r.get("equity_gated",   []),
                "equity_free":    r.get("equity_free",    []),
                "drawdown_gated": r.get("drawdown_gated", []),
                "drawdown_free":  r.get("drawdown_free",  []),
                "fill_dist":      r.get("fill_dist",      {}),
                "ladder_depth":   r.get("ladder_depth",   0),
            }

    pair_list = [p for p in results if "error" not in results[p]]

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
