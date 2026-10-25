# claude code changed: new file — 2026-10-25, Phase A recorder configuration (research-only).
# Every venue keeps its OWN instrument identifier; base/quote are explicit. Comparability across venues is computed
# from the quote currencies — USD and USDT are never equated and no conversion rate is invented.
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

VENUES = ("binance", "kraken", "coinbase")

# Official PUBLIC market-data endpoints (verified 2026-10-25 against each exchange's documentation; see
# bot/research/cross_exchange/feeds.py for the per-venue procedure and the doc sources).
ENDPOINTS = {
    "binance": {"ws": "wss://stream.binance.com:9443/stream", "rest_depth": "https://api.binance.com/api/v3/depth",
                "rest_time": "https://api.binance.com/api/v3/time"},
    "kraken": {"ws": "wss://ws.kraken.com/v2", "rest_time": "https://api.kraken.com/0/public/Time"},
    "coinbase": {"ws": "wss://advanced-trade-ws.coinbase.com", "rest_time": "https://api.coinbase.com/api/v3/brokerage/time"},
}
KRAKEN_DEPTHS = (10, 25, 100, 500, 1000)       # Kraken v2 book: allowed subscription depths (official doc)

DIRECT = "DIRECT"                               # same quote currency on every venue
CONVERSION_REQUIRED = "CONVERSION_REQUIRED"     # quotes differ (e.g. USD vs USDT) — needs a documented FX rate
NOT_COMPARABLE = "NOT_COMPARABLE"               # fewer than two venues carry the instrument


@dataclass(frozen=True)
class VenueSymbol:
    venue: str
    symbol: str          # the venue's own identifier, exactly as sent on the wire
    base: str
    quote: str


@dataclass(frozen=True)
class Instrument:
    key: str                                   # research key, e.g. "BTC-USDT"
    venues: Tuple[VenueSymbol, ...]

    def comparability(self) -> Dict:
        quotes = sorted({v.quote for v in self.venues})
        bases = sorted({v.base for v in self.venues})
        if len(self.venues) < 2 or len(bases) != 1:
            cls = NOT_COMPARABLE
        elif len(quotes) == 1:
            cls = DIRECT
        else:
            cls = CONVERSION_REQUIRED
        return {"instrument": self.key, "class": cls, "quotes": quotes, "venues": [v.venue for v in self.venues]}


def _usdt(base: str) -> Instrument:
    """The same base against USDT on all three venues (each venue's own symbol format)."""
    return Instrument(f"{base}-USDT", (
        VenueSymbol("binance", f"{base}USDT", base, "USDT"),
        VenueSymbol("kraken", f"{base}/USDT", base, "USDT"),
        VenueSymbol("coinbase", f"{base}-USDT", base, "USDT"),
    ))


# Conservative default: four liquid bases listed against USDT on all three venues (listing is re-verified live at
# startup — a venue that rejects the subscription is recorded as NOT_LISTED, never assumed).
DEFAULT_INSTRUMENTS: Tuple[Instrument, ...] = tuple(_usdt(b) for b in ("BTC", "ETH", "SOL", "XRP"))


@dataclass(frozen=True)
class RecorderConfig:
    instruments: Tuple[Instrument, ...] = DEFAULT_INSTRUMENTS
    venues: Tuple[str, ...] = VENUES
    sample_interval_s: float = 1.0             # how often a sample row is written (NOT the feed update rate)
    depth: int = 10                            # levels per side written in each sample
    stale_after_s: float = 5.0                 # a book whose last update is older than this is flagged STALE
    future_ts_tolerance_s: float = 2.0         # exchange timestamp later than local receipt by more → CLOCK_ANOMALY
    max_duration_s: Optional[float] = None     # None = manual session (stop with Ctrl+C)
    rotate_mb: float = 64.0                    # rotate the observation file at this size
    reconnect_initial_s: float = 2.0
    reconnect_max_s: float = 60.0
    connect_timeout_s: float = 15.0
    clock_check_every_s: float = 600.0
    out_dir: str = "research_data/cross_exchange_arb"

    def __post_init__(self):
        if not (0.1 <= self.sample_interval_s <= 3600):
            raise ValueError("sample_interval_s must be between 0.1 and 3600 seconds")
        if not (1 <= self.depth <= 100):
            raise ValueError("depth must be between 1 and 100 levels")
        if any(v not in VENUES for v in self.venues) or not self.venues:
            raise ValueError(f"venues must be a non-empty subset of {VENUES}")
        if self.stale_after_s <= 0 or self.future_ts_tolerance_s < 0:
            raise ValueError("stale_after_s must be > 0 and future_ts_tolerance_s >= 0")
        if self.max_duration_s is not None and self.max_duration_s <= 0:
            raise ValueError("max_duration_s must be positive or None")
        if self.rotate_mb <= 0:
            raise ValueError("rotate_mb must be positive")

    def venue_symbols(self, venue: str) -> List[Tuple[Instrument, VenueSymbol]]:
        return [(i, vs) for i in self.instruments for vs in i.venues if vs.venue == venue and venue in self.venues]

    def kraken_subscription_depth(self) -> int:
        """Smallest allowed Kraken depth ≥ max(requested, 10) — the checksum always covers the top 10."""
        need = max(self.depth, 10)
        return next(d for d in KRAKEN_DEPTHS if d >= need)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["instruments"] = [{"key": i.key, "venues": [asdict(v) for v in i.venues]} for i in self.instruments]
        d["venues"] = list(self.venues)
        return d


def instruments_from_bases(bases: List[str]) -> Tuple[Instrument, ...]:
    out = []
    for b in bases:
        b = b.strip().upper()
        if not b.isalnum() or len(b) > 12:
            raise ValueError(f"invalid base asset {b!r}")
        out.append(_usdt(b))
    return tuple(out)
