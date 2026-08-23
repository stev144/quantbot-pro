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
from pathlib import Path
from typing import List

from bot.fetch_all_symbols import symbol_to_filename  # claude code changed: reuse, section 25 — same symbol->filename convention as the real fetch pipeline
from bot.research_lab.spec import ResearchSpec, DERIVABLE_FROM_OHLCV  # claude code changed: DERIVABLE_FROM_OHLCV moved to spec.py — single source, see that module's docstring for why (it used to drift independently from tools/statistical_tools.py's own copy)

DATA_DIR = "data"

# claude code changed: new — real, honest examples of data sources this
# platform does NOT ingest anywhere (confirmed via the Research Agent
# architecture audit this session: data_fetcher.py is Binance spot
# klines/ticker only). Listed explicitly so a rejection can name what's
# missing, rather than the checker only ever saying "unknown."
KNOWN_UNAVAILABLE_SOURCES = {
    "funding_rate", "open_interest", "orderbook_depth_history",
    "options_flow", "liquidation_data", "social_sentiment", "on_chain_data",
}


@dataclass
class DataRequirementCheck:
    name: str
    available: bool
    reason: str


@dataclass
class DataAvailabilityReport:
    all_available: bool
    checks: List[DataRequirementCheck] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "all_available": self.all_available,
            "checks": [{"name": c.name, "available": c.available, "reason": c.reason} for c in self.checks],
        }


def _ohlcv_path(asset: str) -> Path:
    return Path(DATA_DIR) / symbol_to_filename(asset)  # claude code changed: symbol_to_filename() already returns the full "BTC_USDT_1h.csv" name — no extra suffix needed


def check_data_availability(spec: ResearchSpec) -> DataAvailabilityReport:
    """
    Independently verifies every data source a validated ResearchSpec
    needs actually exists on disk. Must only be called on a spec that
    already passed spec.validate_spec() — an ambiguous/invalid asset or
    feature list isn't this function's job to interpret.
    """
    checks: List[DataRequirementCheck] = []

    ohlcv_path = _ohlcv_path(spec.asset)
    ohlcv_available = ohlcv_path.exists()
    checks.append(DataRequirementCheck(
        name=f"ohlcv:{spec.asset}",
        available=ohlcv_available,
        reason=f"found {ohlcv_path}" if ohlcv_available else f"no OHLCV file at {ohlcv_path} — run fetch_all_symbols.py",
    ))

    # claude code changed: new — Advanced Quant Research Capability
    # Architecture. A pairs hypothesis needs TWO OHLCV files, not one —
    # cointegration testing is meaningless with only asset's own history.
    if spec.hypothesis_type == "pairs" and spec.asset_b:
        ohlcv_b_path = _ohlcv_path(spec.asset_b)
        ohlcv_b_available = ohlcv_b_path.exists()
        checks.append(DataRequirementCheck(
            name=f"ohlcv:{spec.asset_b}",
            available=ohlcv_b_available,
            reason=f"found {ohlcv_b_path}" if ohlcv_b_available else f"no OHLCV file at {ohlcv_b_path} — run fetch_all_symbols.py",
        ))

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
            ))
        elif feature_name in DERIVABLE_FROM_OHLCV:
            checks.append(DataRequirementCheck(
                name=feature_name, available=ohlcv_available,
                reason="derivable from OHLCV via feature_calculator.py" if ohlcv_available
                else "derivable from OHLCV, but the underlying OHLCV file is missing",
            ))
        else:
            checks.append(DataRequirementCheck(
                name=feature_name, available=False,
                reason=f"'{feature_name}' is not a recognized feature this platform can calculate",
            ))

    return DataAvailabilityReport(
        all_available=all(c.available for c in checks),
        checks=checks,
    )
