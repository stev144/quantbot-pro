# ============================================================
# bot/research/data_access.py
#
# claude code changed: new file. Shared research-engine data-loading
# helper — extracted from bot/research/regime_conditional_pairs.py's
# original _load_ohlcv_via_provider()/_PROVIDERS (Data-Layer Architecture
# Audit, DATA_LAYER_ARCHITECTURE_AUDIT.md step F.3) the moment a second
# engine needed the identical logic, per this project's own
# "inspect before duplicating" convention — one shared implementation,
# not six copies across cointegration_engine.py/contagion_engine.py/
# entry_exit_engine.py/kalman_filter_engine.py/permutation_test_engine.py/
# regime_conditional_pairs.py.
#
# Routes every research-engine driver through the canonical
# MarketDataProvider contract (bot/market_data_provider.py) instead of
# each engine reading data/*.csv directly. Still asset-class-scoped
# (CRYPTO -> BinanceKlinesProvider, FOREX -> YahooForexProvider by
# default) — adding a third asset class means adding one more entry to
# _PROVIDERS, not a new copy of this function.
#
# claude code changed: MT5 data-layer integration. FOREX now resolves to
# MT5Provider instead of YahooForexProvider when MT5_ENABLED is set —
# same env-var-gated pattern bot.engines.kraken_adapter.build_kraken_adapter()
# already uses for KRAKEN_ENABLED, and exactly the "testing data -> real
# data, zero other code changes" upgrade path bot/mt5_data_fetcher.py's own
# module docstring describes: every research engine that already calls
# load_ohlcv_via_provider(symbol, ASSET_CLASS_FOREX, ...) — cointegration_engine.py,
# contagion_engine.py, kalman_filter_engine.py, regime_conditional_pairs.py,
# forex_cointegration_scan.py — starts receiving real MT5 broker data the
# moment MT5_ENABLED=true and real credentials are configured, with no
# call site changed. MT5_ENABLED unset (the default, and every environment
# this has run in so far) preserves the exact prior behavior — Yahoo,
# unchanged. Resolved per-call (_resolve_provider()), not frozen into the
# module-level dict at import time, so the env var can be toggled between
# calls (matters for tests, and for turning MT5 on without a process restart).
# ============================================================

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Dict, Optional

import pandas as pd

from bot.binance_klines_provider import BinanceKlinesProvider
from bot.instruments import ASSET_CLASS_CRYPTO, ASSET_CLASS_FOREX, get_instrument
from bot.market_data_provider import MarketDataProvider
from bot.mt5_provider import MT5Provider
from bot.yahoo_forex_provider import YahooForexProvider

logger = logging.getLogger(__name__)

_PROVIDERS: Dict[str, MarketDataProvider] = {
    ASSET_CLASS_CRYPTO: BinanceKlinesProvider(),
    ASSET_CLASS_FOREX: YahooForexProvider(),
}

_MT5_PROVIDER = MT5Provider()


def _mt5_enabled() -> bool:
    return os.getenv("MT5_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def _resolve_provider(asset_class: str) -> MarketDataProvider:
    if asset_class == ASSET_CLASS_FOREX and _mt5_enabled():
        return _MT5_PROVIDER
    return _PROVIDERS[asset_class]


def load_ohlcv_via_provider(
    symbol: str,
    asset_class: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> Optional[pd.DataFrame]:
    """
    claude code changed: new (extracted, not duplicated — see module
    docstring). Resolves a research engine's underscore-joined symbol
    ("BTC_USDT") to a registered Instrument (bot.instruments.get_instrument)
    and fetches it through the canonical MarketDataProvider contract for
    `asset_class`. Returns a DataFrame indexed by timestamp — the same
    shape every engine's old CSV-reading loader already produced — or
    None if the instrument isn't registered, the provider raises, or the
    result is empty, logged in each case rather than raised, matching
    every calling engine's existing "skip and warn" convention for a
    missing/bad symbol.
    """
    canonical_symbol = symbol.replace("_", "/", 1)
    instrument = get_instrument(canonical_symbol)
    if instrument is None:
        logger.warning(f"  {symbol}: no Instrument registered for {canonical_symbol!r}")
        return None

    provider = _resolve_provider(asset_class)
    try:
        bars = provider.get_historical_bars(instrument, timeframe, start, end)
    except Exception as e:
        logger.warning(f"  {symbol}: provider fetch failed ({e})")
        return None

    if bars.data.empty:
        logger.warning(f"  {symbol}: provider returned no data for [{start.date()}, {end.date()}]")
        return None

    df = bars.data.set_index("timestamp").sort_index()
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["close"], inplace=True)
    return df
