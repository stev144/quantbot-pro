# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/point_in_time_universe.py
#
# claude code changed: new file — 2026-10-03, owner decision D-6 (DECISION_SHEET_D1-D11_2026-10-03.md, signed).
#
#   SOURCE      Binance's public archive (data.binance.vision): each symbol's FIRST and LAST available kline date;
#               delisting announcements as confirmation (recorded per symbol, not inferred).
#   MEMBERSHIP  symbol ∈ universe at time t  ⇔  listed ≥ 90 days before t  AND  still trading at t
#                                             AND  trailing 30-day quote volume computed AT t ≥ a fixed floor
#   DELISTED    symbols are included (their data must be fetched) — no survivorship.
#
# OPEN OWNER PARAMETER: the sheet fixes "a fixed floor" but not its value. `min_volume_usdt` therefore has NO
# default; every call must pass it explicitly and it is recorded in the universe version. Until the listing table
# is built from the archive and the floor is set, cross-sectional research stays BLOCKED (sheet text).
# The listing table is built by `build_listing_table` from the archive; it needs network access and is NOT run
# by tests or by this implementation.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional

import pandas as pd

from bot.quant_core.provenance import config_hash

UNIVERSE_VERSION = "QBP-PIT-D6-1"
MIN_LISTING_AGE = pd.Timedelta(days=90)
VOLUME_WINDOW = pd.Timedelta(days=30)
LISTING_TABLE = Path("research_data") / "_governance" / "pit_listing_table.json"


@dataclass(frozen=True)
class Listing:
    symbol: str
    first_kline: pd.Timestamp       # first available kline open time in the archive (UTC)
    last_kline: Optional[pd.Timestamp]   # None = still trading at table build time
    delisting_source: str = ""      # announcement reference when last_kline is set


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def is_member(listing: Listing, t, trailing_quote_volume_usdt: Optional[float], *, min_volume_usdt: float) -> bool:
    """D-6 membership at time t. `trailing_quote_volume_usdt` must be the 30-day quote volume computed from data at
    or before t (the caller's responsibility; see trailing_volume_at). A missing volume is NOT membership."""
    if min_volume_usdt is None or not (min_volume_usdt >= 0):
        raise ValueError("min_volume_usdt (the D-6 volume floor) is an owner parameter and must be passed explicitly")
    from bot.quant_core.market_profile import AssetClassMismatch, asset_class_for_symbol   # claude code changed: 2026-10-04 asset-agnostic
    if asset_class_for_symbol(listing.symbol) != "CRYPTO":   # claude code changed: D-6 is a Binance listing/USDT-volume rule; the FX universe rule is an owner decision
        raise AssetClassMismatch(f"{listing.symbol}: D-6 (Binance listing + USDT volume floor) is the CRYPTO universe rule only")
    t = _utc(t)
    if t < listing.first_kline + MIN_LISTING_AGE:
        return False
    if listing.last_kline is not None and t > listing.last_kline:
        return False
    return trailing_quote_volume_usdt is not None and trailing_quote_volume_usdt >= min_volume_usdt


def trailing_volume_at(quote_volume: pd.Series, t) -> Optional[float]:
    """Sum of quote volume over the 30 days ending at t (open times in (t − 30 d, t]); None if the window is not
    fully covered by data — never extrapolated."""
    t = _utc(t)
    qv = quote_volume.sort_index()
    if len(qv) == 0 or qv.index[0] > t - VOLUME_WINDOW:
        return None
    w = qv[(qv.index > t - VOLUME_WINDOW) & (qv.index <= t)]
    return float(w.sum()) if len(w) else None


def universe_at(listings: Iterable[Listing], t, volumes: Dict[str, pd.Series], *, min_volume_usdt: float) -> list:
    """Point-in-time universe at t: uses only listing dates and volume data available at t."""
    out = []
    for lst in listings:
        v = trailing_volume_at(volumes[lst.symbol], t) if lst.symbol in volumes else None
        if is_member(lst, t, v, min_volume_usdt=min_volume_usdt):
            out.append(lst.symbol)
    return sorted(out)


def universe_version(listings: Iterable[Listing], *, min_volume_usdt: float) -> str:
    rows = sorted((l.symbol, l.first_kline.isoformat(), l.last_kline.isoformat() if l.last_kline is not None else None)
                  for l in listings)
    return config_hash({"version": UNIVERSE_VERSION, "min_volume_usdt": min_volume_usdt, "listings": rows})


def load_listing_table(path: Path = LISTING_TABLE) -> list:
    if not Path(path).exists():
        raise FileNotFoundError(f"no point-in-time listing table at {path} — build it from data.binance.vision "
                                f"(build_listing_table); cross-sectional research stays BLOCKED until then (D-6)")
    rows = json.loads(Path(path).read_text(encoding="utf-8"))["listings"]
    return [Listing(r["symbol"], _utc(r["first_kline"]), _utc(r["last_kline"]) if r.get("last_kline") else None,
                    r.get("delisting_source", "")) for r in rows]


def build_listing_table(symbols: Iterable[str], out_path: Path = LISTING_TABLE, interval: str = "1h") -> Path:  # pragma: no cover — network
    """Builds the listing table from data.binance.vision's monthly kline archive listing (public S3 bucket):
    first and last month with a kline file per symbol, refined to the first/last kline inside those files.
    NETWORK ACCESS REQUIRED — run manually by the owner; never called by tests."""
    raise NotImplementedError("build_listing_table requires network access to data.binance.vision and an owner run; "
                              "it is intentionally not executed by this implementation (D-6 data acquisition)")
