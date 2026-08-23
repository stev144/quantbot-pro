# ============================================================
# bot/research_lab/data_availability.py
# Research Lab — data availability checker (section 7).
#
# claude code changed: new file. "Before research begins, determine
# whether the required data actually exists... Do NOT silently substitute
# another dataset." This module never trusts the interpreter's own claims
# about what data a hypothesis needs — it independently checks disk state
# for the asset's OHLCV and each named feature against a closed, honest
# allowlist. Anything not on the allowlist is INSUFFICIENT_DATA, not a
# best-effort guess.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from bot.instruments import UnknownInstrumentError, get_instrument, resolve_ohlcv_path  # claude code changed: Multi-Asset Foundation Refactor STEP 2 — was symbol_to_filename() called directly here, a second independent copy of the same provider-path convention tools/_data.py also had (see bot/instruments.py's module docstring)
from bot.research_lab.spec import ResearchSpec, DERIVABLE_FROM_OHLCV  # claude code changed: DERIVABLE_FROM_OHLCV moved to spec.py — single source, see that module's docstring for why (it used to drift independently from tools/statistical_tools.py's own copy)

# claude code changed: new — real, honest examples of data sources this
# platform does NOT ingest anywhere (confirmed via the Research Agent
# architecture audit this session: data_fetcher.py is Binance spot
# klines/ticker only). Listed explicitly so a rejection can name what's
# missing, rather than the checker only ever saying "unknown."
KNOWN_UNAVAILABLE_SOURCES = {
    "funding_rate", "open_interest", "orderbook_depth_history",
    "options_flow", "liquidation_data", "social_sentiment", "on_chain_data",
}


# claude code changed: new — Multi-Asset Foundation Refactor STEP 6. The
# explicit five-state taxonomy the refactor brief's section 7 asked for.
# AVAILABLE/UNAVAILABLE existed implicitly as the `available` bool before;
# these three states didn't exist as anything a caller could distinguish:
#   REQUIRES_DATASET     — the instrument/data IS the right kind, just not
#                           fetched yet (run fetch_all_symbols.py and it's
#                           solved — a temporary, actionable gap)
#   REQUIRES_ASSET_CLASS — the instrument belongs to an asset class this
#                           platform has never ingested any data for at all
#                           (US_EQUITY/FOREX today — an architectural gap,
#                           not a "just fetch it" gap)
#   REQUIRES_PROVIDER    — the feature is a genuinely different kind of
#                           data (funding_rate, open_interest) that no
#                           OHLCV re-fetch would ever produce — needs a new
#                           provider integration, not more of the same one
# `available` (bool) is kept as-is alongside `status` — every existing
# reader of `.available`/`all_available` keeps working unchanged; `status`
# is additive richness, not a replacement.
AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
REQUIRES_PROVIDER = "REQUIRES_PROVIDER"
REQUIRES_ASSET_CLASS = "REQUIRES_ASSET_CLASS"
REQUIRES_DATASET = "REQUIRES_DATASET"


@dataclass
class DataRequirementCheck:
    name: str
    available: bool
    reason: str
    status: str = AVAILABLE  # claude code changed: new field, see taxonomy above — defaults to AVAILABLE only as a dataclass default; every real construction site below sets it explicitly


@dataclass
class DataAvailabilityReport:
    all_available: bool
    checks: List[DataRequirementCheck] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "all_available": self.all_available,
            "checks": [{"name": c.name, "available": c.available, "reason": c.reason, "status": c.status} for c in self.checks],
        }


def _ohlcv_check(asset: str) -> DataRequirementCheck:
    """
    claude code changed: Multi-Asset Foundation Refactor STEP 2/6. Was
    `_ohlcv_path()` returning a bare Path this function's caller then
    called `.exists()` on directly. Now routes through
    bot.instruments.resolve_ohlcv_path() (the single provider-path
    boundary, see that module) and turns its two distinct failure modes
    into the right status rather than letting either propagate as a raw
    exception — this function must always return a check, never raise.
    """
    try:
        path = resolve_ohlcv_path(asset)
    except UnknownInstrumentError as exc:
        # claude code changed: distinguish "wrong asset class, no data
        # source exists at all for it" from "not a real instrument" —
        # resolve_ohlcv_path's message already says which; in practice
        # this branch is unreachable for a spec that already passed
        # validate_spec() (asset is checked against the supported
        # universe first), but this function must degrade honestly rather
        # than crash if that invariant is ever violated by a future caller.
        instrument = get_instrument(asset)
        status = REQUIRES_ASSET_CLASS if instrument is not None else UNAVAILABLE
        return DataRequirementCheck(name=f"ohlcv:{asset}", available=False, reason=str(exc), status=status)

    if path.exists():
        return DataRequirementCheck(name=f"ohlcv:{asset}", available=True, reason=f"found {path}", status=AVAILABLE)
    return DataRequirementCheck(
        name=f"ohlcv:{asset}", available=False,
        reason=f"no OHLCV file at {path} — run fetch_all_symbols.py", status=REQUIRES_DATASET,
    )


def check_data_availability(spec: ResearchSpec) -> DataAvailabilityReport:
    """
    Independently verifies every data source a validated ResearchSpec
    needs actually exists on disk. Must only be called on a spec that
    already passed spec.validate_spec() — an ambiguous/invalid asset or
    feature list isn't this function's job to interpret.
    """
    checks: List[DataRequirementCheck] = []

    ohlcv_check = _ohlcv_check(spec.asset)
    checks.append(ohlcv_check)
    ohlcv_available = ohlcv_check.available

    # claude code changed: new — Advanced Quant Research Capability
    # Architecture. A pairs hypothesis needs TWO OHLCV files, not one —
    # cointegration testing is meaningless with only asset's own history.
    if spec.hypothesis_type == "pairs" and spec.asset_b:
        checks.append(_ohlcv_check(spec.asset_b))

    # claude code changed: new — for a conditional hypothesis, the feature
    # under test lives inside spec.conditions, not spec.features. Checking
    # both (deduplicated) means data availability is verified for whichever
    # shape the confirmed spec actually uses, rather than assuming
    # hypothesis_type=="feature" everywhere.
    condition_feature_names = [c.get("feature") for c in spec.conditions if c.get("feature")]
    feature_names_to_check = list(dict.fromkeys(list(spec.features) + condition_feature_names))  # claude code changed: dedupe, preserve order

    for feature_name in feature_names_to_check:
        if feature_name in KNOWN_UNAVAILABLE_SOURCES:
            checks.append(DataRequirementCheck(
                name=feature_name, available=False,
                reason=f"'{feature_name}' is not ingested anywhere on this platform — no substitute dataset will be used",
                status=REQUIRES_PROVIDER,
            ))
        elif feature_name in DERIVABLE_FROM_OHLCV:
            checks.append(DataRequirementCheck(
                name=feature_name, available=ohlcv_available,
                reason="derivable from OHLCV via feature_calculator.py" if ohlcv_available
                else "derivable from OHLCV, but the underlying OHLCV file is missing",
                status=AVAILABLE if ohlcv_available else REQUIRES_DATASET,
            ))
        else:
            checks.append(DataRequirementCheck(
                name=feature_name, available=False,
                reason=f"'{feature_name}' is not a recognized feature this platform can calculate",
                status=UNAVAILABLE,
            ))

    return DataAvailabilityReport(
        all_available=all(c.available for c in checks),
        checks=checks,
    )
