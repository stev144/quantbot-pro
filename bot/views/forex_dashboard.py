# ============================================================
# bot/views/forex_dashboard.py
# claude code changed: new file — Forex Research Dashboard mission.
# Forex's equivalent of bot/views/dashboard.py's dashboard() view: a
# real, first-class market/research dashboard rather than the
# hypothesis-entry-only page the navbar's Forex link used to open. No
# @login_required — matches every other dashboard/market page's
# confirmed no-auth-gating convention (config/settings.py deliberately
# removed LoginRequiredMiddleware platform-wide; Research Lab gates
# CAPABILITIES via ResearchEntitlementService, never page access).
#
# Deliberately reads only the local data/forex/*.csv cache (via
# bot.instruments.resolve_ohlcv_path(), never a hand-built path) — no
# live Yahoo Finance call happens inside this page's request, per the
# approved plan. A missing/empty local file produces an honest "no data
# cached yet" state (see forex_terminal_data.get_forex_data_provenance),
# never a fabricated fallback.
# ============================================================

import pandas as pd
from django.shortcuts import render

from bot.engines.regime_detector import RegimeDetector
from bot.instruments import ASSET_CLASS_FOREX, get_instrument, resolve_ohlcv_path, symbols_for_asset_class
from bot.views.forex_terminal_data import (
    get_forex_alpha_intelligence,
    get_forex_data_provenance,
    get_forex_execution_state,
    get_forex_portfolio_risk,
    get_forex_research_state,
    get_forex_system_health,
)
from bot.views.terminal_data import get_market_state


def _load_local_forex_ohlcv(symbol):
    """Read-only local cache read — never a network call. Returns an
    empty DataFrame (not None) for any missing/unreadable file, so
    callers have one honest "no data" shape to check."""
    try:
        path = resolve_ohlcv_path(symbol)
    except Exception:
        return pd.DataFrame()
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        return df
    except Exception:
        return pd.DataFrame()


def forex_dashboard(request):
    forex_symbols = symbols_for_asset_class(ASSET_CLASS_FOREX)

    symbol = request.GET.get("symbol") or (forex_symbols[0] if forex_symbols else None)
    instrument = get_instrument(symbol) if symbol else None

    if instrument is None or instrument.asset_class != ASSET_CLASS_FOREX:
        # claude code changed: fails closed on an unknown/non-Forex
        # symbol rather than silently substituting another one — matches
        # bot.instruments.resolve_ohlcv_path()'s own "never invent data"
        # principle, extended to symbol selection itself.
        return render(request, "forex_dashboard.html", {
            "error": f"'{symbol}' is not a registered Forex instrument.",
            "forex_symbols": forex_symbols,
        })

    df = _load_local_forex_ohlcv(symbol)

    regime_result = RegimeDetector().detect(df if not df.empty else None)
    market_state = get_market_state(
        regime_result,
        cross_section_dispersion={
            "available": False,
            "reason": "cross_section_engine.py has never been run against the Forex universe",
        },
    )

    context = {
        "symbol": symbol,
        "instrument": instrument,
        "forex_symbols": forex_symbols,
        "error": None,
        "provenance": get_forex_data_provenance(symbol, instrument, df),
        "market_state": market_state,
        "research_state": get_forex_research_state(),
        "alpha_intelligence": get_forex_alpha_intelligence(),
        "portfolio_risk": get_forex_portfolio_risk(),
        "execution": get_forex_execution_state(),
        "system_health": get_forex_system_health(),
    }
    return render(request, "forex_dashboard.html", context)
