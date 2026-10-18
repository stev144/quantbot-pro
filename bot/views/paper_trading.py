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

_CACHE_KEY = "paper_trading_results_v1"  # claude code changed: new cache key
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

    # build per-pair JSON for JS charts
    pairs_data = {}
    for pair, r in results.items():
        if "error" not in r:
            pairs_data[pair] = {
                "equity_gated": r.get("equity_gated", []),
                "equity_free":  r.get("equity_free",  []),
            }

    pair_list = [p for p in results if "error" not in results[p]]

    context = {
        "error":       error,
        "results":     results,
        "pair_list":   pair_list,
        "pairs_json":  json.dumps(pairs_data),
    }
    return render(request, "paper_trading.html", context)
