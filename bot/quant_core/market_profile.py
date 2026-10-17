# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/quant_core/market_profile.py
#
# claude code changed: new file — 2026-10-04, asset-agnostic research infrastructure.
#
# The canonical research path (REVB-4) was written for a 24/7 crypto venue: a continuous hourly grid, a gap
# of > g_max grid candles re-seeds the Kalman state, the short leg is financed by recorded perpetual funding,
# costs are a venue fee + slippage, and the universe is D-6's point-in-time Binance listing. This module makes
# those MARKET rules an explicit, versioned component (a MarketProfile) instead of an implicit assumption, so
#
#   * CRYPTO keeps exactly its REVB-4 behaviour — CRYPTO_PROFILE only *describes* the rules the code already
#     applies (continuous calendar, CANONICAL.gap_max_candles, perp funding, venue costs, D-6 universe); no
#     crypto code path changes because of it (crypto outputs fingerprinted identical before/after);
#   * FOREX gets the mechanism the decision lock already fixed as a PRINCIPLE (Decision 5, STAGE2D_DECISION_LOCK):
#       A. a SCHEDULED market closure is not missing data — its hours are EXCLUDED from the grid (no prediction
#          step, no Q accumulation, no warmup count, no z slot; the crypto > g_max re-seed rule never fires on it);
#       B. unexpected missing data during market hours follows an FX gap policy whose g_max is an OWNER value;
#   * everything Decision 5 left open — the calendar SOURCE, the FX g_max, the seed convention, whether closure
#     hours count in H / label horizons, the swap (financing) source, the spread model, the FX universe — is a
#     `None` field on FOREX_PROFILE. A profile with any `None` field cannot be used canonically:
#     require_canonical() raises OwnerDecisionMissing with status FX_CALENDAR_UNAVAILABLE (decision lock:
#     "FX results from this engine must be reported as FX_CALENDAR_UNAVAILABLE, not as negative evidence").
#
# Nothing here guesses a calendar: SessionCalendar takes explicit closure intervals from a NAMED source; there
# is no built-in FX calendar and no weekend heuristic is ever used AS a calendar. The one heuristic below
# (assert_continuous_market) can only REFUSE — it stops session-traded data from running under the 24/7 crypto
# rules when it arrives under an unregistered name; it never classifies hours as closed.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from typing import Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

MARKET_PROFILE_VERSION = "QBP-MARKET-1"
HOUR = pd.Timedelta(hours=1)
FX_CALENDAR_UNAVAILABLE = "FX_CALENDAR_UNAVAILABLE"      # decision-lock Decision 5 status label
CRYPTO_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")


class OwnerDecisionMissing(RuntimeError):
    """A canonical consumer needs a market rule the owner has not decided (fail closed, never guessed)."""


class AssetClassMismatch(ValueError):
    """Data or symbols of one asset class reached another asset class's rules, or a pair mixes classes."""


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _utc_index(idx) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


# ── calendars ────────────────────────────────────────────────────────────────────────────────────────────────

class ContinuousCalendar:
    """24/7 venue: every hour is a trading hour (crypto). trading_grid() is the complete hourly grid — the exact
    grid KalmanFilterEngine._align_prices has always built."""
    kind = "CONTINUOUS_24_7"
    is_continuous = True
    source = "24/7 venue — no scheduled closures"

    def is_trading(self, index) -> np.ndarray:
        return np.ones(len(index), dtype=bool)

    def trading_grid(self, start, end) -> pd.DatetimeIndex:
        return pd.date_range(_utc(start), _utc(end), freq="h")

    def first_trading_at_or_after(self, t) -> pd.Timestamp:
        return _utc(t).ceil("h")

    def identity(self) -> dict:
        return {"kind": self.kind, "source": self.source}


class SessionCalendar:
    """Scheduled closures as explicit half-open UTC intervals [start, end) on hour boundaries. MECHANISM ONLY —
    the interval list must be compiled from an owner-named source (Decision 5: a vetted library, or an owner rule
    naming venue session times, DST handling and a holiday list). There is deliberately no default instance."""
    kind = "SESSION"
    is_continuous = False

    def __init__(self, closures: Iterable[Tuple[object, object]], *, source: str):
        if not (isinstance(source, str) and source.strip()):
            raise ValueError("a SessionCalendar needs a named source (decision lock Decision 5) — none given")
        iv = sorted((_utc(a), _utc(b)) for a, b in closures)
        for a, b in iv:
            if not a < b:
                raise ValueError(f"closure interval [{a}, {b}) is empty or reversed")
            if a != a.floor("h") or b != b.floor("h"):
                raise ValueError(f"closure interval [{a}, {b}) is not on hour boundaries")
        for (a0, b0), (a1, _) in zip(iv, iv[1:]):
            if a1 < b0:
                raise ValueError(f"closure intervals overlap: [{a0}, {b0}) and one starting {a1}")
        self.source = source
        self.closures = tuple(iv)
        self._starts = pd.DatetimeIndex([a for a, _ in iv]) if iv else pd.DatetimeIndex([], tz="UTC")
        self._ends = pd.DatetimeIndex([b for _, b in iv]) if iv else pd.DatetimeIndex([], tz="UTC")

    def is_trading(self, index) -> np.ndarray:
        idx = _utc_index(index)
        if len(self.closures) == 0 or len(idx) == 0:
            return np.ones(len(idx), dtype=bool)
        k = self._starts.searchsorted(idx, side="right") - 1          # last closure starting at or before t
        inside = (k >= 0) & (idx < self._ends[np.clip(k, 0, None)])
        return ~np.asarray(inside)

    def trading_grid(self, start, end) -> pd.DatetimeIndex:
        full = pd.date_range(_utc(start), _utc(end), freq="h")
        return full[self.is_trading(full)]

    def first_trading_at_or_after(self, t, horizon: pd.Timedelta = pd.Timedelta(days=31)) -> pd.Timestamp:
        g = self.trading_grid(_utc(t).ceil("h"), _utc(t).ceil("h") + horizon)
        if len(g) == 0:
            raise ValueError(f"no trading hour within {horizon} at or after {t}")
        return g[0]

    def identity(self) -> dict:
        digest = hashlib.sha256(json.dumps([[a.isoformat(), b.isoformat()] for a, b in self.closures]).encode()).hexdigest()
        return {"kind": self.kind, "source": self.source, "n_closures": len(self.closures), "closures_sha256": digest}


def weekly_closure_intervals(start, end, *, close: Tuple[int, int], reopen: Tuple[int, int]) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    """Closure intervals for a fixed UTC weekly rule (close=(weekday, hour), reopen=(weekday, hour); Monday = 0).
    A construction helper only — NO default times: which rule (and its DST/holiday handling) applies to which
    venue is the owner's Decision-5 calendar source."""
    start, end = _utc(start).floor("D"), _utc(end).ceil("D")
    out = []
    day = start - pd.Timedelta(days=7)
    while day <= end:
        if day.weekday() == close[0]:
            a = day + pd.Timedelta(hours=close[1])
            gap_days = (reopen[0] - close[0]) % 7 or 7
            b = day + pd.Timedelta(days=gap_days, hours=reopen[1])
            if b > a and b > start and a < end:
                out.append((a, b))
        day += pd.Timedelta(days=1)
    return out


def assert_no_closure_rows(index, calendar, *, purpose: str) -> None:
    """A row observed INSIDE a scheduled closure means the calendar or the data is wrong — fail closed."""
    if calendar is None or calendar.is_continuous:
        return
    idx = _utc_index(index)
    bad = idx[~calendar.is_trading(idx)]
    if len(bad):
        raise ValueError(f"{purpose}: {len(bad)} observation(s) inside scheduled closures of '{calendar.source}' "
                         f"(first {bad[0]}) — calendar or data is wrong; refusing rather than guessing")


def max_missing_trading_run(observed_index, calendar, start, end) -> int:
    """Longest run of consecutive TRADING-grid candles in [start, end] with no observation. Scheduled closure
    hours are not on the trading grid, so they never count (Decision 5 A); only missing trading hours do (B)."""
    grid = calendar.trading_grid(start, end)
    if len(grid) == 0:
        return 0
    missing = ~grid.isin(_utc_index(observed_index))
    best = run = 0
    for m in missing:
        run = run + 1 if m else 0
        best = max(best, run)
    return int(best)


def assert_continuous_market(index, *, purpose: str, min_saturdays: int = 4) -> None:
    """Refuse session-traded data under the 24/7 rules. If at least `min_saturdays` whole UTC Saturdays lie inside
    the observed span and more than half of them have NO observation, the data has a weekly closure pattern no
    24/7 venue produces. REFUSAL ONLY: this never marks any hour as closed (it is not a calendar)."""
    idx = _utc_index(index)
    if len(idx) < 2:
        return
    first_sat = (idx.min() + pd.Timedelta(days=1)).normalize()
    sats = pd.date_range(first_sat, idx.max().normalize() - pd.Timedelta(days=1), freq="W-SAT")
    if len(sats) < min_saturdays:
        return
    observed_days = set(idx.normalize())
    empty = float(np.mean([s not in observed_days for s in sats]))
    if empty > 0.5:
        raise AssetClassMismatch(f"{purpose}: {empty:.0%} of {len(sats)} UTC Saturdays in the data have no candle — "
                                 f"a session-traded (e.g. FX) series cannot run under the 24/7 crypto market rules")


# ── profiles ─────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MarketProfile:
    """The market rules one asset class's canonical research needs. A None field = an undecided owner rule."""
    asset_class: str
    calendar: Optional[object]                  # ContinuousCalendar | SessionCalendar (from a named source)
    gap_max_candles: Optional[int]              # unexpected missing TRADING candles before the state is invalidated
    seed_rule: Optional[str]                    # how the canonical seed candle is placed relative to the S start
    horizon_basis: Optional[str]                # what H, label horizons, warmup, z window, L, embargo count
    financing_source: Optional[str]             # recorded short-leg financing source
    cost_model: Optional[str]                   # transaction-cost model
    universe_rule: Optional[str]                # point-in-time universe rule
    protocol_hash: Optional[str]                # the CanonicalProtocol instance this profile is bound to

    DECISION_FIELDS = ("calendar", "gap_max_candles", "seed_rule", "horizon_basis", "financing_source",
                       "cost_model", "universe_rule", "protocol_hash")
    # claude code changed: 2026-10-06 — the rules a zone-S cointegration SELECTION screen needs (no costs, financing,
    # holding horizon or protocol values are used by the Engle–Granger screen itself)
    SELECTION_FIELDS = ("calendar", "gap_max_candles", "seed_rule", "universe_rule")

    def missing_decisions(self) -> List[str]:
        return [f for f in self.DECISION_FIELDS if getattr(self, f) is None]

    @property
    def canonical_capable(self) -> bool:
        return not self.missing_decisions()

    def require_canonical(self, purpose: str) -> "MarketProfile":
        missing = self.missing_decisions()
        if missing:
            status = FX_CALENDAR_UNAVAILABLE if self.asset_class == "FOREX" else "MARKET_PROFILE_INCOMPLETE"
            raise OwnerDecisionMissing(f"{status}: {purpose} — {self.asset_class} market profile has undecided owner "
                                       f"rules {missing}; not negative evidence (decision lock Decision 5)")
        return self

    def require_selection(self, purpose: str) -> "MarketProfile":   # claude code changed: 2026-10-06 — new
        """Gate for the exploratory zone-S selection screen only: calendar, gap rule, seed rule and universe must be
        decided. Everything downstream (Kalman state, lifecycle, costs, evidence) still needs require_canonical()."""
        missing = [f for f in self.SELECTION_FIELDS if getattr(self, f) is None]
        if missing:
            status = FX_CALENDAR_UNAVAILABLE if self.asset_class == "FOREX" else "MARKET_PROFILE_INCOMPLETE"
            raise OwnerDecisionMissing(f"{status}: {purpose} — {self.asset_class} selection rules undecided {missing}")
        return self

    def identity(self) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d["calendar"] = self.calendar.identity() if self.calendar is not None else None
        d["version"] = MARKET_PROFILE_VERSION
        return d

    def profile_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.identity(), sort_keys=True, default=str).encode()).hexdigest()


def _crypto_profile() -> MarketProfile:
    from bot.quant_core.protocol import CANONICAL
    return MarketProfile(
        asset_class="CRYPTO",
        calendar=ContinuousCalendar(),
        gap_max_candles=CANONICAL.gap_max_candles,                     # owner D8 (REVB-4), unchanged
        seed_rule="FIRST_TRADING_CANDLE_AT_OR_AFTER_S",                # on a 24/7 grid this IS the S start (C6/D-8)
        horizon_basis="GRID_CANDLES_24_7 (one candle = one clock hour)",
        financing_source="RECORDED_PERP_FUNDING QBP-FUND-1 (borrow row, signed 2026-10-03)",
        cost_model="CRYPTO_VENUE_FEE_PLUS_SLIPPAGE (bot.config.execution_costs)",
        universe_rule="D-6 QBP-PIT-D6-1 (point-in-time Binance listing + owner volume floor)",
        protocol_hash=CANONICAL.config_hash(),
    )


CRYPTO_PROFILE = _crypto_profile()

FX_CALENDAR_SOURCE = ("OD-FX-1 (owner, 2026-10-06): closed Friday 17:00 -> Sunday 17:00 America/New_York "
                      "(= 22:00 UTC in winter, 21:00 UTC during US daylight saving)")   # claude code changed: 2026-10-06 — holidays described separately


# claude code changed: 2026-10-06 — owner-delegated holiday list ("do what's recommended"). Every broker-wide gap in
# the 2021-05 .. 2026-10 MT5 data (missing for >= 54 of 56 currency pairs at once) that falls on Christmas or New
# Year, taken hour by hour from the data. The one non-holiday broker-wide gap (Thu 2021-12-02 09:00-14:00 UTC, an
# outage) is deliberately NOT a closure: it stays missing data. Coverage ends 2026-10-02; later holidays are not
# listed, so they would show as missing hours until added.
FX_HOLIDAY_CLOSURES = tuple((pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC")) for a, b in (
    ("2021-12-24 19:00", "2021-12-24 22:00"),   # Fri 24 Dec 14:00 NY early close
    ("2021-12-26 22:00", "2021-12-26 23:00"),   # Sun 26 Dec late reopen
    ("2021-12-31 19:00", "2021-12-31 22:00"),   # Fri 31 Dec 14:00 NY early close
    ("2022-01-02 22:00", "2022-01-02 23:00"),   # Sun 02 Jan late reopen
    ("2022-12-25 22:00", "2022-12-26 22:00"),   # Christmas observed Mon 26 Dec
    ("2023-12-24 22:00", "2023-12-25 22:00"),   # Christmas Mon 25 Dec
    ("2023-12-31 22:00", "2024-01-01 22:00"),   # New Year Mon 1 Jan
    ("2024-12-24 22:00", "2024-12-25 22:00"),   # Christmas Wed 25 Dec
    ("2024-12-31 22:00", "2025-01-01 22:00"),   # New Year Wed 1 Jan
    ("2025-12-24 23:00", "2025-12-25 22:00"),   # Christmas Thu 25 Dec
    ("2025-12-31 23:00", "2026-01-01 22:00"),   # New Year Thu 1 Jan
))
FX_HOLIDAY_SOURCE = ("owner-delegated 2026-10-06: Christmas/New Year broker-wide closures measured in the "
                     "ICFundedEvaluations MT5 data 2021-05..2026-10 (the 2021-12-02 outage excluded)")


def fx_new_york_close_calendar(start="2015-01-01", end="2031-12-31", holidays: bool = True) -> SessionCalendar:   # claude code changed: 2026-10-06 — new (owner OD-FX-1)
    """The owner's FX calendar: every week closed from Friday 17:00 to Sunday 17:00 New York time, converted to UTC
    week by week (so it follows US daylight saving), plus the measured Christmas/New Year closures
    (FX_HOLIDAY_CLOSURES; holidays=False gives the weekly rule alone)."""
    fridays = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="W-FRI")
    iv = []
    for f in fridays:
        a = pd.Timestamp(f.date()).replace(hour=17).tz_localize("America/New_York").tz_convert("UTC")
        b = (pd.Timestamp(f.date()) + pd.Timedelta(days=2)).replace(hour=17).tz_localize("America/New_York").tz_convert("UTC")
        iv.append((a, b))
    source = FX_CALENDAR_SOURCE + "; no holidays"
    if holidays:
        iv = iv + list(FX_HOLIDAY_CLOSURES)
        source = FX_CALENDAR_SOURCE + "; holidays: " + FX_HOLIDAY_SOURCE
    return SessionCalendar(iv, source=source)


# claude code changed: 2026-10-06 — owner-delegated OD-FX-7: the 56 MT5 currency pairs minus hard pegs (HKD, DKK:
# cointegrated by construction, not by market behaviour); metals (XAU, XAG) excluded (different session calendar —
# daily break — and contract). 51 pairs.
FX_UNIVERSE = frozenset(s.replace("_", "/") for s in (
    "AUD_CAD AUD_CHF AUD_JPY AUD_NZD AUD_SGD AUD_USD CAD_CHF CAD_JPY CHF_JPY CHF_SGD EUR_AUD EUR_CAD EUR_CHF "
    "EUR_GBP EUR_JPY EUR_NOK EUR_NZD EUR_PLN EUR_SEK EUR_SGD EUR_USD EUR_ZAR GBP_AUD GBP_CAD GBP_CHF GBP_JPY "
    "GBP_NOK GBP_NZD GBP_SEK GBP_SGD GBP_USD NOK_JPY NOK_SEK NZD_CAD NZD_CHF NZD_JPY NZD_USD SEK_JPY SGD_JPY "
    "USD_CAD USD_CHF USD_CNH USD_CZK USD_JPY USD_MXN USD_NOK USD_PLN USD_SEK USD_SGD USD_THB USD_ZAR").split())
FX_UNIVERSE_RULE = ("FX-FLOATING-51 v1 (owner-delegated 2026-10-06): MT5 currency pairs excluding hard pegs "
                    "(HKD, DKK) and metals (XAU, XAG)")


def in_universe(profile: "MarketProfile", symbol: str) -> bool:   # claude code changed: 2026-10-06 — new
    """FX: membership of FX_UNIVERSE. Crypto keeps its D-6 rule elsewhere (not applied by the selection screen)."""
    if profile.asset_class != "FOREX":
        return True
    canon = symbol if "/" in symbol else symbol.replace("_", "/", 1)
    return canon in FX_UNIVERSE


# Decision 5: the calendar is decided (OD-FX-1, 2026-10-06); every other rule below is still an owner decision (see
# the asset-agnostic section of PROTOCOL_IMPLEMENTATION_LOG_2026-10-03.md). None of them is guessed here.
FOREX_PROFILE = MarketProfile(asset_class="FOREX", calendar=fx_new_york_close_calendar(),   # claude code changed: 2026-10-06 — was None
                              gap_max_candles=5,                            # claude code changed: OD-FX-2 (owner-delegated): smallest limit that admits the single broker-wide outage (5 h, 2021-12-02); data-informed, recorded
                              seed_rule="FIRST_TRADING_CANDLE_AT_OR_AFTER_S",   # claude code changed: OD-FX-3 (owner-delegated): strict, = the crypto rule
                              horizon_basis="TRADING_CANDLE_COUNT (OD-FX-HB-1, 2026-10-16): count only trading candles; weekends/holidays (OD-FX-1) excluded; analogous to GRID_CANDLES_24_7 — one candle = one trading hour",  # claude code changed: 2026-10-16 OD-FX-HB-1 — was None; owner decision
                              financing_source="MT5_BROKER_STATIC (OD-FX-FS-1, 2026-10-16): ICFundedEvaluations published swap rates as static historical approximation; label STATIC_SWAP_APPROXIMATION; cost_complete requires ForexInstrumentSpec.swap_long/short from Stage 2",  # claude code changed: 2026-10-16 OD-FX-FS-1 — was None; owner decision
                              cost_model="MT5_BROKER_PUBLISHED (OD-FX-CM-1, 2026-10-16): ICFundedEvaluations published spread + commission; cost_complete requires ForexInstrumentSpec.spread_typical_pips + commission_per_lot from Stage 2",  # claude code changed: 2026-10-16 OD-FX-CM-1 — was None; owner decision
                              universe_rule=FX_UNIVERSE_RULE,               # claude code changed: OD-FX-7 (owner-delegated)
                              protocol_hash="2e022f3596cbe3cc401cbab6593168b4f9c6807a48f6a7fd8bccb875807e213a")  # claude code changed: 2026-10-17 FX-DCA-PROTOCOL-1-A4 — SHA-256 of FX_DCA_PROTOCOL_2026-10-16_v5.md; adds MAX_CYCLE_CANDLES=720 time-exit (Part 3B §1) and explicit deployment-cap documentation (§2); v4 hash 8ab4a52… preserved in v5 doc

_PROFILES = {"CRYPTO": CRYPTO_PROFILE, "FOREX": FOREX_PROFILE}


def profile_for_asset_class(asset_class: str) -> MarketProfile:
    if asset_class not in _PROFILES:
        raise OwnerDecisionMissing(f"no market profile exists for asset class {asset_class!r}")
    return _PROFILES[asset_class]


def _fx_codes() -> set:
    from bot.instruments import ASSET_CLASS_FOREX, list_instruments
    codes = set()
    for i in list_instruments(ASSET_CLASS_FOREX):
        codes.update(c for c in (i.base_currency, i.quote_currency) if c)
    return codes


def asset_class_for_symbol(symbol: str) -> str:
    """Instrument registry first (bot.instruments — the single source of asset_class). An unregistered name with
    a crypto stablecoin quote is CRYPTO; one whose two legs are both registry FX currency codes is FOREX; any
    other unregistered name (synthetic test pairs such as 'X'/'Y') keeps the legacy CRYPTO default, guarded by
    assert_continuous_market on the data itself."""
    from bot.instruments import get_instrument
    if not symbol:
        return "CRYPTO"          # legacy engines constructed without symbols (pre-asset-class code paths)
    canon = symbol if "/" in symbol else symbol.replace("_", "/", 1)
    inst = get_instrument(canon)
    if inst is not None:
        return inst.asset_class
    base, _, quote = canon.partition("/")
    if quote.upper() in CRYPTO_QUOTES:
        return "CRYPTO"
    if base and quote and {base.upper(), quote.upper()} <= _fx_codes():
        return "FOREX"
    return "CRYPTO"


def profile_for_pair(symbol_a: str, symbol_b: str) -> MarketProfile:
    ca, cb = asset_class_for_symbol(symbol_a), asset_class_for_symbol(symbol_b)
    if ca != cb:
        raise AssetClassMismatch(f"pair {symbol_a}/{symbol_b} mixes asset classes ({ca}, {cb}) — no protocol defines "
                                 f"a cross-asset pair")
    return profile_for_asset_class(ca)
