# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/forex_instrument_spec.py
# Workstream A — DCA forex instrument specification layer
#
# claude code changed: new file — 2026-10-16. DCA-research-specific instrument metadata that the existing
# bot.instruments.Instrument intentionally omits (Instrument is the identity/routing layer; this is the
# trading-contract layer). Every field that is truly broker-specific defaults to None; is_cost_complete()
# returns False when any material cost input is absent, blocking any simulation from claiming cost_complete=True
# with placeholder values.
#
# Design decisions (each justified against the codebase):
#   - Separate class, not fields bolted onto Instrument: Instrument is frozen=True and imported by
#     ~20 modules; adding optional fields for DCA research would pollute its interface. The brief says
#     "reuse existing modules" — bot.instruments.Instrument is reused HERE as the identity anchor.
#   - Placeholder constants are EXPLICITLY labelled; no hardcoded real-looking numbers passed off as
#     broker-verified. swap_long=None, swap_short=None everywhere — matches Instrument.swap_long/short
#     comment ("require a real broker, Stage 2").
#   - is_cost_complete() follows the existing dca_engine.py convention where FINANCING_UNAVAILABLE ->
#     cost_complete=False in summarize_dca_cycles(). Same rule, one place.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from bot.instruments import ASSET_CLASS_FOREX, Instrument, get_instrument  # claude code changed: reuse, not re-derive


# ── Specification version ──────────────────────────────────────────────────────────────────────────────────
FOREX_INSTRUMENT_SPEC_VERSION = "FX-DCA-SPEC-1"  # claude code changed: bump when any field semantics change — provenance records this


# ── Core DCA instrument spec ───────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ForexInstrumentSpec:
    """
    DCA research trading contract for one forex instrument.

    Fields deliberately set to None when unavailable from a real broker.
    Never invent a plausible-sounding default — the is_cost_complete()
    method enforces that simulations carry the right completeness label.

    account_currency: the simulation account's settlement currency (e.g. "USD").
    All P&L results are expressed in this currency.
    """
    # ── Identity (link back to the instrument registry) ──────────────────
    canonical_symbol: str           # e.g. "EUR/USD"
    asset_class: str = ASSET_CLASS_FOREX  # always FOREX for this class  # claude code changed: type guard

    # ── Pricing ──────────────────────────────────────────────────────────
    pip_size: float = None          # e.g. 0.0001 for EUR/USD, 0.01 for USD/JPY — reused from Instrument registry
    price_precision: int = None     # decimal places in broker quotes, e.g. 5 for EUR/USD

    # ── Position sizing ───────────────────────────────────────────────────
    contract_size: float = None     # base-currency units per 1.0 standard lot, e.g. 100_000
    quantity_unit: str = "lots"     # "lots" | "units" — lots is the standard retail convention
    min_volume: float = None        # minimum tradable position in lots, e.g. 0.01
    volume_increment: float = None  # smallest lot increment, e.g. 0.01
    quantity_precision: int = None  # decimal places in quantity, e.g. 2

    # ── Account currency ─────────────────────────────────────────────────
    account_currency: str = "USD"   # settlement currency for P&L; default USD matches the Binance-world baseline

    # ── Execution costs ───────────────────────────────────────────────────
    spread_typical_pips: Optional[float] = None      # typical bid/ask spread in pips; broker-specific, None = unknown
    commission_per_lot: Optional[float] = None       # broker commission per standard lot in account currency; None = no-commission account
    slippage_typical_pips: Optional[float] = None    # estimated execution slippage in pips; None = unknown

    # ── Financing (swap/rollover) ─────────────────────────────────────────
    swap_long: Optional[float] = None   # overnight rollover for long position in pips/day; None = unavailable (broker-specific)
    swap_short: Optional[float] = None  # overnight rollover for short position in pips/day; None = unavailable (broker-specific)
    swap_triple_day: Optional[int] = None  # weekday index where triple swap is charged (0=Mon…6=Sun); None = unknown

    # ── Risk / margin ─────────────────────────────────────────────────────
    leverage_max: Optional[float] = None   # maximum permitted leverage, e.g. 30 for EU retail; None = unknown
    margin_rate: Optional[float] = None    # required margin fraction (1/leverage), e.g. 1/30; None = unknown

    # ── Provenance ───────────────────────────────────────────────────────
    source: str = "PLACEHOLDER"           # where this spec was obtained, e.g. "ICFundedEvaluations_MT5_2026-10"
    specification_version: str = FOREX_INSTRUMENT_SPEC_VERSION  # version tag for audit trail
    validated: bool = False               # True only when confirmed against a real live broker connection

    def __post_init__(self):  # claude code changed: validate at construction — fail loud, not silently wrong
        if self.asset_class != ASSET_CLASS_FOREX:
            raise ValueError(f"ForexInstrumentSpec requires asset_class=FOREX, got {self.asset_class!r}")
        if not self.canonical_symbol:
            raise ValueError("canonical_symbol must be non-empty")
        if self.pip_size is not None and self.pip_size <= 0:
            raise ValueError(f"pip_size must be > 0, got {self.pip_size}")
        if self.contract_size is not None and self.contract_size <= 0:
            raise ValueError(f"contract_size must be > 0, got {self.contract_size}")

    def pip_value_in_quote(self, lots: float = 1.0) -> Optional[float]:
        """
        Value of 1 pip move for `lots` standard lots, in the quote currency.
        EUR/USD: 1 lot x 100_000 units x 0.0001 pip = $10 USD per pip.
        Returns None when contract_size or pip_size is unavailable.
        """  # claude code changed: independent formula — pip_value = pip_size * contract_size * lots
        if self.pip_size is None or self.contract_size is None:
            return None
        return self.pip_size * self.contract_size * lots

    def is_cost_complete(self) -> bool:
        """
        Returns False when any material cost input is unavailable.
        Simulations must carry cost_complete=False in their provenance
        when this returns False — never claim full cost accuracy with
        placeholder specs.

        Completeness requires: spread, commission (or explicitly confirmed
        zero-commission account: set commission_per_lot=0.0, not None),
        swap_long and swap_short. Slippage may remain unknown (it's a
        secondary, smaller cost) without failing completeness.
        """  # claude code changed: same logic as dca_engine.py summarize_dca_cycles cost_complete gate
        return (
            self.spread_typical_pips is not None
            and self.commission_per_lot is not None
            and self.swap_long is not None
            and self.swap_short is not None
        )

    def get_instrument(self) -> Optional[Instrument]:
        """Return the corresponding Instrument registry entry, or None if not registered."""  # claude code changed: links back to single-source identity
        return get_instrument(self.canonical_symbol)


# ── Placeholder specs for the five target pairs ───────────────────────────────────────────────────────────
# claude code changed: EXPLICITLY labelled PLACEHOLDER — not broker-verified values.
# pip_size/contract_size reuse bot.instruments._build_forex_registry()'s own values (the single source).
# Swap, commission, spread: None (broker-specific, require a real MT5 connection — Stage 2).
# validated=False everywhere: no live broker confirmation has occurred.

PLACEHOLDER_EURUSD = ForexInstrumentSpec(
    canonical_symbol="EUR/USD",
    pip_size=0.0001,        # standard 5-decimal-place EUR/USD pip
    price_precision=5,
    contract_size=100_000.0,
    min_volume=0.01,
    volume_increment=0.01,
    quantity_precision=2,
    account_currency="USD",
    spread_typical_pips=None,   # requires real broker data
    commission_per_lot=None,    # requires real broker data
    swap_long=None,             # requires real broker data
    swap_short=None,            # requires real broker data
    leverage_max=30.0,          # EU retail regulatory maximum (ESMA); actual account leverage may differ
    margin_rate=1.0 / 30.0,     # 1/leverage_max
    source="PLACEHOLDER_CONVENTION",
    validated=False,
)  # claude code changed: new — Workstream A placeholder, not broker-verified

PLACEHOLDER_GBPUSD = ForexInstrumentSpec(
    canonical_symbol="GBP/USD",
    pip_size=0.0001,
    price_precision=5,
    contract_size=100_000.0,
    min_volume=0.01,
    volume_increment=0.01,
    quantity_precision=2,
    account_currency="USD",
    spread_typical_pips=None,
    commission_per_lot=None,
    swap_long=None,
    swap_short=None,
    leverage_max=30.0,
    margin_rate=1.0 / 30.0,
    source="PLACEHOLDER_CONVENTION",
    validated=False,
)  # claude code changed: new — Workstream A placeholder

PLACEHOLDER_USDJPY = ForexInstrumentSpec(
    canonical_symbol="USD/JPY",
    pip_size=0.01,          # JPY-quoted pairs use 0.01 pip (2 decimal places in quote)
    price_precision=3,
    contract_size=100_000.0,
    min_volume=0.01,
    volume_increment=0.01,
    quantity_precision=2,
    account_currency="USD",
    spread_typical_pips=None,
    commission_per_lot=None,
    swap_long=None,
    swap_short=None,
    leverage_max=30.0,
    margin_rate=1.0 / 30.0,
    source="PLACEHOLDER_CONVENTION",
    validated=False,
)  # claude code changed: new — Workstream A placeholder; JPY pip=0.01 matches instruments.py

PLACEHOLDER_USDCHF = ForexInstrumentSpec(
    canonical_symbol="USD/CHF",
    pip_size=0.0001,
    price_precision=5,
    contract_size=100_000.0,
    min_volume=0.01,
    volume_increment=0.01,
    quantity_precision=2,
    account_currency="USD",
    spread_typical_pips=None,
    commission_per_lot=None,
    swap_long=None,
    swap_short=None,
    leverage_max=30.0,
    margin_rate=1.0 / 30.0,
    source="PLACEHOLDER_CONVENTION",
    validated=False,
)  # claude code changed: new — Workstream A placeholder

PLACEHOLDER_AUDUSD = ForexInstrumentSpec(
    canonical_symbol="AUD/USD",
    pip_size=0.0001,
    price_precision=5,
    contract_size=100_000.0,
    min_volume=0.01,
    volume_increment=0.01,
    quantity_precision=2,
    account_currency="USD",
    spread_typical_pips=None,
    commission_per_lot=None,
    swap_long=None,
    swap_short=None,
    leverage_max=30.0,
    margin_rate=1.0 / 30.0,
    source="PLACEHOLDER_CONVENTION",
    validated=False,
)  # claude code changed: new — Workstream A placeholder


PLACEHOLDER_SPECS = {  # claude code changed: new — lookup dict for tests and future DCA runs
    "EUR/USD": PLACEHOLDER_EURUSD,
    "GBP/USD": PLACEHOLDER_GBPUSD,
    "USD/JPY": PLACEHOLDER_USDJPY,
    "USD/CHF": PLACEHOLDER_USDCHF,
    "AUD/USD": PLACEHOLDER_AUDUSD,
}


def get_forex_instrument_spec(canonical_symbol: str) -> Optional[ForexInstrumentSpec]:
    """Return the placeholder spec for a target pair, or None if not defined."""  # claude code changed: new
    return PLACEHOLDER_SPECS.get(canonical_symbol)


# ── MT5-verified specs (Stage 2 collection 2026-10-16) ────────────────────────────────────────────────────
# claude code changed: new — 2026-10-16; cost_complete gate resolved; values from fx_instrument_specs_collected.json
#
# COST MODEL (OD-FX-CM-1, OD-FX-FS-1):
#   commission_per_lot=0.0 — ICFundedEvaluations spread-only model; MT5 symbol_info().commission returned
#     None (no commission field configured), confirming zero-commission. Set to 0.0 (not None) so
#     is_cost_complete() can return True. Authorized by operator 2026-10-16.
#
#   spread_typical_pips — live MT5 snapshot 2026-10-16T11:17 UTC. EUR/USD and AUD/USD returned 0.0
#     (ECN tight spread during low-activity period). GBP/USD, USD/JPY, USD/CHF returned 0.1 pips.
#     WARNING: point-in-time snapshot only; real spreads widen during high-volatility events.
#     Label: STATIC_SNAPSHOT — consistent with OD-FX-CM-1 static approximation policy.
#
#   swap_long / swap_short — from MT5 symbol_info().swap_long/swap_short (pips/day).
#     Label: STATIC_SWAP_APPROXIMATION (OD-FX-FS-1) — rates change with interest rate decisions.
#
#   validated=True — values came from a real MT5 broker connection (read-only demo). "validated"
#     means source is a live broker connection, NOT that the spread is a long-run typical value.

MT5_EURUSD = ForexInstrumentSpec(  # claude code changed: new — Stage 2 verified; cost_complete=True
    canonical_symbol="EUR/USD",
    pip_size=0.0001,
    price_precision=5,
    contract_size=100_000.0,
    min_volume=0.01,
    volume_increment=0.01,
    quantity_precision=2,
    account_currency="USD",
    spread_typical_pips=0.0,    # snapshot 2026-10-16T11:17 UTC; may widen during volatility
    commission_per_lot=0.0,     # spread-only account confirmed; 0.0 not None
    swap_long=-0.84,            # pips/day; STATIC_SWAP_APPROXIMATION per OD-FX-FS-1
    swap_short=0.123,           # pips/day; STATIC_SWAP_APPROXIMATION
    swap_triple_day=3,
    leverage_max=None,          # not exposed by MT5 demo account
    margin_rate=None,
    source="ICFundedEvaluations_MT5_SNAPSHOT_2026-10-16",
    validated=True,
)

MT5_GBPUSD = ForexInstrumentSpec(  # claude code changed: new — Stage 2 verified; cost_complete=True
    canonical_symbol="GBP/USD",
    pip_size=0.0001,
    price_precision=5,
    contract_size=100_000.0,
    min_volume=0.01,
    volume_increment=0.01,
    quantity_precision=2,
    account_currency="USD",
    spread_typical_pips=0.1,    # snapshot 2026-10-16T11:17 UTC
    commission_per_lot=0.0,     # spread-only account confirmed; 0.0 not None
    swap_long=-0.504,           # pips/day; STATIC_SWAP_APPROXIMATION
    swap_short=-0.336,          # pips/day; STATIC_SWAP_APPROXIMATION
    swap_triple_day=3,
    leverage_max=None,
    margin_rate=None,
    source="ICFundedEvaluations_MT5_SNAPSHOT_2026-10-16",
    validated=True,
)

MT5_USDJPY = ForexInstrumentSpec(  # claude code changed: new — Stage 2 verified; cost_complete=True
    canonical_symbol="USD/JPY",
    pip_size=0.01,
    price_precision=3,
    contract_size=100_000.0,
    min_volume=0.01,
    volume_increment=0.01,
    quantity_precision=2,
    account_currency="USD",
    spread_typical_pips=0.1,    # snapshot 2026-10-16T11:17 UTC
    commission_per_lot=0.0,     # spread-only account confirmed; 0.0 not None
    swap_long=0.798,            # pips/day; STATIC_SWAP_APPROXIMATION (positive = earns carry)
    swap_short=-1.806,          # pips/day; STATIC_SWAP_APPROXIMATION
    swap_triple_day=3,
    leverage_max=None,
    margin_rate=None,
    source="ICFundedEvaluations_MT5_SNAPSHOT_2026-10-16",
    validated=True,
)

MT5_USDCHF = ForexInstrumentSpec(  # claude code changed: new — Stage 2 verified; cost_complete=True
    canonical_symbol="USD/CHF",
    pip_size=0.0001,
    price_precision=5,
    contract_size=100_000.0,
    min_volume=0.01,
    volume_increment=0.01,
    quantity_precision=2,
    account_currency="USD",
    spread_typical_pips=0.1,    # snapshot 2026-10-16T11:17 UTC
    commission_per_lot=0.0,     # spread-only account confirmed; 0.0 not None
    swap_long=0.598,            # pips/day; STATIC_SWAP_APPROXIMATION (positive = earns carry)
    swap_short=-1.386,          # pips/day; STATIC_SWAP_APPROXIMATION
    swap_triple_day=3,
    leverage_max=None,
    margin_rate=None,
    source="ICFundedEvaluations_MT5_SNAPSHOT_2026-10-16",
    validated=True,
)

MT5_AUDUSD = ForexInstrumentSpec(  # claude code changed: new — Stage 2 verified; cost_complete=True
    canonical_symbol="AUD/USD",
    pip_size=0.0001,
    price_precision=5,
    contract_size=100_000.0,
    min_volume=0.01,
    volume_increment=0.01,
    quantity_precision=2,
    account_currency="USD",
    spread_typical_pips=0.0,    # snapshot 2026-10-16T11:17 UTC; may widen during volatility
    commission_per_lot=0.0,     # spread-only account confirmed; 0.0 not None
    swap_long=-0.02,            # pips/day; STATIC_SWAP_APPROXIMATION
    swap_short=-0.504,          # pips/day; STATIC_SWAP_APPROXIMATION
    swap_triple_day=3,
    leverage_max=None,
    margin_rate=None,
    source="ICFundedEvaluations_MT5_SNAPSHOT_2026-10-16",
    validated=True,
)

MT5_SPECS: dict = {  # claude code changed: new — verified specs lookup; use over PLACEHOLDER_SPECS for real simulations
    "EUR/USD": MT5_EURUSD,
    "GBP/USD": MT5_GBPUSD,
    "USD/JPY": MT5_USDJPY,
    "USD/CHF": MT5_USDCHF,
    "AUD/USD": MT5_AUDUSD,
}


def get_mt5_forex_instrument_spec(canonical_symbol: str) -> Optional[ForexInstrumentSpec]:
    """Return the MT5-verified spec (cost_complete=True) for a target pair, or None if not collected."""  # claude code changed: new — Stage 2 gate resolver
    return MT5_SPECS.get(canonical_symbol)
