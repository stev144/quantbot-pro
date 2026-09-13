# ============================================================
# bot/research/capabilities.py
# claude code changed: new file — Forex Integration Stage 1 (Architectural
# Parity). A small, reusable capability-declaration primitive: any
# research engine can ask "does this data actually satisfy requirement X"
# and get back an explicit, honest answer — OK / NOT_APPLICABLE /
# INSUFFICIENT_DATA — rather than silently computing a feature from data
# that doesn't really support it. Motivating example (the concrete case
# this is first applied to, in feature_calculator.py): Forex OHLCV always
# reports volume=0 (Yahoo Finance provides no real traded-volume figure
# for OTC FX), so a volume-derived feature silently becomes all-NaN for
# every Forex row today, with no indication anywhere that this is a data
# limitation rather than "the feature just doesn't fire."
#
# Deliberately minimal: one enum, one result shape, one check function.
# Retrofitting every research engine to declare its full data-requirement
# set is real, separate work (Stage 3's engine-by-engine capability
# audit) — this primitive exists and is proven correct on one real case
# here; broader adoption is intentionally out of this file's scope.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd


class DataRequirement(Enum):
    """What kind of market data a research engine/feature actually needs.
    Named after the concrete data types this platform's real providers
    do or don't supply — not a speculative superset."""
    REAL_VOLUME = "real_volume"      # genuine traded volume (e.g. a centralized exchange) — NOT Forex's always-zero tick placeholder
    TICK_VOLUME = "tick_volume"      # a tick/update count, which Forex CAN provide via a broker feed (not via the current Yahoo Finance path)
    BID_ASK = "bid_ask"              # separate bid/ask columns, not just a single "close"
    ORDER_BOOK = "order_book"        # centralized limit order book depth
    DERIVATIVES = "derivatives"      # funding rate / open interest — perpetual-futures-only concepts


@dataclass(frozen=True)
class CapabilityResult:
    """claude code changed: new. satisfied=True means the check function
    computed a real answer from real data. satisfied=False always carries
    a `status` explaining WHY, distinguishing an asset-class/market for
    which the requirement is genuinely meaningless (NOT_APPLICABLE) from
    one where it could apply in principle but this data doesn't have it
    (INSUFFICIENT_DATA) — the same OK/NOT_APPLICABLE/INSUFFICIENT_DATA
    vocabulary the mission's own capability-mechanism request uses."""
    satisfied: bool
    status: str   # "OK" | "NOT_APPLICABLE" | "INSUFFICIENT_DATA"
    reason: str


def check_data_requirement(
    df: pd.DataFrame,
    requirement: DataRequirement,
    asset_class: "str | None" = None,
) -> CapabilityResult:
    """
    Real, evidence-based check — never assumes based on asset_class alone
    when the actual data can be inspected instead. asset_class is used
    only to produce an honest, specific reason string; the pass/fail
    decision itself is always made from the DataFrame's real contents.
    """
    if requirement == DataRequirement.REAL_VOLUME:
        if "volume" not in df.columns:
            return CapabilityResult(False, "INSUFFICIENT_DATA", "no 'volume' column present in this dataset")
        # claude code changed: real check, not an asset_class guess — a
        # column that is always exactly 0 (or entirely NaN) carries no
        # real traded-volume information, regardless of which provider
        # or asset class produced it.
        volume = df["volume"]
        if volume.isna().all():
            return CapabilityResult(False, "INSUFFICIENT_DATA", "'volume' column is entirely NaN")
        if (volume.fillna(0) == 0).all():
            reason = "'volume' is always 0 in this dataset"
            if asset_class:
                reason += f" (expected for {asset_class}: no centralized traded-volume figure is available from this provider)"
            return CapabilityResult(False, "NOT_APPLICABLE", reason)
        return CapabilityResult(True, "OK", "real, non-zero traded volume present")

    if requirement == DataRequirement.TICK_VOLUME:
        if "volume" not in df.columns or df["volume"].isna().all():
            return CapabilityResult(False, "INSUFFICIENT_DATA", "no tick/volume-count column present")
        return CapabilityResult(True, "OK", "a volume/tick-count column is present")

    if requirement == DataRequirement.BID_ASK:
        has_bid_ask = "bid" in df.columns and "ask" in df.columns
        if has_bid_ask:
            return CapabilityResult(True, "OK", "bid/ask columns present")
        return CapabilityResult(False, "NOT_APPLICABLE", "this dataset has only a single close price, no separate bid/ask (requires a broker/tick feed — see Stage 2)")

    if requirement == DataRequirement.ORDER_BOOK:
        return CapabilityResult(False, "NOT_APPLICABLE", "no historical order-book depth is available for this dataset")

    if requirement == DataRequirement.DERIVATIVES:
        return CapabilityResult(False, "NOT_APPLICABLE", "derivatives data (funding rate / open interest) is a perpetual-futures-only concept; not applicable to this instrument")

    raise ValueError(f"Unknown DataRequirement: {requirement!r}")
