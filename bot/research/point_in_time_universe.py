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

import numpy as np   # claude code changed: 2026-10-07 — archive parsing
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


def trailing_volume_at(quote_volume: pd.Series, t, interval: Optional[pd.Timedelta] = None) -> Optional[float]:
    """Sum of quote volume of the candles that CLOSED within the 30 days ending at t (open ≥ t − 30 d and
    open + interval ≤ t); None if the window is not fully covered by data — never extrapolated.
    claude code changed: 2026-10-07 — look-ahead fix. Was open times in (t − 30 d, t]: the candle OPENING at t (whose
    volume trades after t — a whole day for daily candles) was counted as known at t. `interval` is inferred from
    the series spacing when not given."""
    t = _utc(t)
    qv = quote_volume.sort_index()
    if len(qv) == 0 or qv.index[0] > t - VOLUME_WINDOW:
        return None
    if interval is None:   # claude code changed: 2026-10-07 look-ahead fix
        if len(qv) < 2:
            return None
        interval = pd.Series(qv.index).diff().dropna().min()
    w = qv[(qv.index >= t - VOLUME_WINDOW) & (qv.index + interval <= t)]   # claude code changed: 2026-10-07 look-ahead fix — closed candles only
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


# ── data.binance.vision archive access ─────────────────────────────────────────────────────────────────────
# claude code changed: 2026-10-07 — owner sign-off (DECISION_SHEET_D5_D6_DCA_PROPOSAL_2026-10-07.md item 3) authorises
# the archive download. Raw monthly files are cached under ARCHIVE_DIR (never overwritten). No month at or after the
# sealed holdout's month is ever requested (F = 2026-10-04): see _refuse_sealed_month.
ARCHIVE_BASE = "https://data.binance.vision"
LISTING_API = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
ARCHIVE_DIR = Path("research_data") / "pit_archive"
KLINE_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades",
                 "taker_base", "taker_quote", "ignore"]
STABLE_OR_FIAT_BASES = frozenset({"USDC", "BUSD", "TUSD", "USDP", "PAX", "DAI", "FDUSD", "UST", "USTC", "SUSD",
                                  "USDS", "USDSB", "EUR", "GBP", "AUD", "BRL", "TRY", "RUB", "NGN", "UAH", "BIDR",
                                  "IDRT", "BVND", "AEUR", "EURI", "XUSD", "USD1", "PAXG"})   # PAXG: gold-pegged
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


def _refuse_sealed_month(ym: str):   # claude code changed: 2026-10-07
    from bot.quant_core.research_zones import HoldoutSealedError, ZONES
    if pd.Timestamp(ym + "-01", tz="UTC") >= ZONES.F.normalize().replace(day=1):
        raise HoldoutSealedError(f"month {ym} reaches the sealed holdout (F = {ZONES.F.date()}) — never downloaded")


def _get(url: str, timeout: int = 60) -> bytes:   # pragma: no cover — network
    import time
    import urllib.request
    for attempt in range(4):
        try:
            return urllib.request.urlopen(url, timeout=timeout).read()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))


def _list_prefixes(prefix: str) -> list:   # pragma: no cover — network
    import re
    out, marker = [], ""
    while True:
        from urllib.parse import quote   # claude code changed: 2026-10-07 — the archive holds non-ASCII symbol names
        xml = _get(f"{LISTING_API}?delimiter=/&prefix={quote(prefix)}" + (f"&marker={quote(marker)}" if marker else "")).decode()
        out += re.findall(r"<Prefix>([^<]+)</Prefix>", xml)[1 if not marker else 0:]
        out += re.findall(r"<Key>([^<]+)</Key>", xml)
        if "<IsTruncated>true</IsTruncated>" not in xml:
            return out
        nm = re.search(r"<NextMarker>([^<]+)</NextMarker>", xml)
        marker = nm.group(1) if nm else (out[-1] if out else "")


def archive_usdt_symbols() -> list:   # pragma: no cover — network
    """Every USDT spot symbol that ever had monthly klines in the archive (delisted ones included)."""
    pre = "data/spot/monthly/klines/"
    syms = sorted({p[len(pre):].strip("/") for p in _list_prefixes(pre) if p.startswith(pre)})
    return [s for s in syms if s.endswith("USDT") and s != "USDT"]


def archive_months(symbol: str, interval: str) -> list:   # pragma: no cover — network
    pre = f"data/spot/monthly/klines/{symbol}/{interval}/"
    return sorted(k[len(pre) + len(symbol) + len(interval) + 2:-4] for k in _list_prefixes(pre)
                  if k.endswith(".zip") and not k.endswith(".CHECKSUM"))


def parse_kline_csv(text: str) -> pd.DataFrame:   # claude code changed: 2026-10-07
    """One archive month → OHLCV + quote_volume indexed by UTC open time. Handles the archive's switch from
    millisecond to MICROSECOND timestamps (spot files from 2025)."""
    import io
    df = pd.read_csv(io.StringIO(text), header=None, names=KLINE_COLUMNS, float_precision="round_trip")
    if len(df) and not str(df["open_time"].iloc[0]).lstrip("-").isdigit():   # a header row (some later files)
        df = df.iloc[1:].astype({c: float for c in KLINE_COLUMNS if c not in ("open_time", "close_time", "trades")})
    ot = df["open_time"].astype("int64")
    unit = np.where(ot > 10**14, "us", "ms")
    if len(set(unit)) > 1:
        raise ValueError("mixed timestamp units inside one archive file")
    idx = pd.to_datetime(ot, unit=unit[0] if len(unit) else "ms", utc=True)
    out = df[["open", "high", "low", "close", "volume", "quote_volume"]].astype(float)
    out.index = pd.DatetimeIndex(idx, name="timestamp")
    return out


def fetch_month(symbol: str, interval: str, ym: str, cache_dir: Path = ARCHIVE_DIR) -> Optional[pd.DataFrame]:   # pragma: no cover — network
    """One monthly archive file (cached raw CSV text). None when the archive has no file for that month."""
    import io
    import zipfile
    _refuse_sealed_month(ym)
    path = Path(cache_dir) / symbol / interval / f"{symbol}-{interval}-{ym}.csv"
    if not path.exists():
        try:
            from urllib.parse import quote   # claude code changed: 2026-10-07 — non-ASCII symbol names
            raw = _get(f"{ARCHIVE_BASE}/data/spot/monthly/klines/{quote(symbol)}/{interval}/{quote(symbol)}-{interval}-{ym}.zip")
        except Exception as e:
            if "404" in str(e):
                return None
            raise
        z = zipfile.ZipFile(io.BytesIO(raw))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(z.read(z.namelist()[0]).decode())
    return parse_kline_csv(path.read_text(encoding="utf-8"))


def is_excluded_base(symbol: str, all_bases: Iterable[str]) -> str:   # claude code changed: 2026-10-07 (signed exclusions)
    """'' when eligible; otherwise the reason (stablecoin/fiat/pegged, or leveraged token)."""
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    if base in STABLE_OR_FIAT_BASES:
        return "STABLECOIN_FIAT_OR_PEGGED"
    bases = set(all_bases)
    for suf in LEVERAGED_SUFFIXES:
        if base.endswith(suf) and base[:-len(suf)] in bases:
            return "LEVERAGED_TOKEN"
    return ""


def top_n_universe_at(listings: Iterable[Listing], t, volumes: Dict[str, pd.Series], n: int,
                      excluded: Optional[Dict[str, str]] = None) -> list:   # claude code changed: 2026-10-07 (signed top-N)
    """Signed D-6 + top-N: D-6 membership (listed ≥ 90 days, still trading at t) with no floor, ranked by trailing
    30-day quote volume of candles CLOSED by t; the top n symbols (ties broken by symbol name). Uses only
    information available at t."""
    ranked = []
    for lst in listings:
        if excluded and excluded.get(lst.symbol):
            continue
        v = trailing_volume_at(volumes[lst.symbol], t) if lst.symbol in volumes else None
        if is_member(lst, t, v, min_volume_usdt=0.0):
            ranked.append((-v, lst.symbol))
    return [s for _, s in sorted(ranked)[:n]]


def build_listing_table(symbols: Iterable[str], out_path: Path = LISTING_TABLE, interval: str = "1d",
                        workers: int = 8) -> Path:  # pragma: no cover — network
    """Listing table from the archive: first/last month with a kline file per symbol, refined to the first/last
    kline inside those files. last_kline = None when the last file is the latest month before the seal (still
    trading as far as the pre-F archive shows). Delisting announcements are not machine-readable: the source is
    recorded as ARCHIVE_END_ONLY (not confirmed by an announcement)."""
    from bot.quant_core.research_zones import ZONES
    seal_month = ZONES.F.strftime("%Y-%m")
    from concurrent.futures import ThreadPoolExecutor
    prev_month = (pd.Timestamp(seal_month + "-01") - pd.offsets.MonthBegin(1)).strftime("%Y-%m")

    def one(s):
        months = [m for m in archive_months(s, interval) if m < seal_month]
        if not months:
            return None
        first = fetch_month(s, interval, months[0])
        last = fetch_month(s, interval, months[-1])
        if first is None or last is None or not len(first) or not len(last):
            return None
        still = months[-1] >= prev_month
        return {"symbol": s, "first_kline": first.index[0].isoformat(),
                "last_kline": None if still else last.index[-1].isoformat(),
                "delisting_source": "" if still else "ARCHIVE_END_ONLY", "n_months": len(months)}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        rows = sorted((r for r in ex.map(one, list(symbols)) if r), key=lambda r: r["symbol"])
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": UNIVERSE_VERSION, "built_at": pd.Timestamp.now(tz="UTC").isoformat(),
               "source": f"{ARCHIVE_BASE} monthly {interval} klines", "listings": rows}
    with open(out_path, "x", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    return Path(out_path)
