# ============================================================
# bot/research_lab/data_fingerprint.py
# claude code changed: new file — Hardening Mission Section 5 (data
# versioning / reproducibility). The prior platform audit found no
# research result anywhere in this project records what data actually
# produced it — two researchers re-running the same script six months
# apart get silently different underlying candles for any "recent" window,
# with no way to detect that they diverged. This is the single, shared
# utility every research script (phase2d/e/f-style scripts, and any future
# ResearchExperiment) should call, rather than each one growing its own
# ad hoc versioning scheme.
#
# Deliberately NOT a hash of the full raw dataset (impractical for the
# ~165M-row trade-flow acquisitions already run in this project) — a
# fingerprint of the parameters that DETERMINE the dataset (source,
# symbol, venue, timeframe, exact date range, schema version, and the
# actual row count once acquired) is sufficient to detect "this is not the
# same data" without hashing gigabytes on every run. Two acquisitions with
# an identical fingerprint used the same source/window/schema AND produced
# the same row count — the practical, cheap definition of "same dataset"
# this project's own acquisition scripts already implicitly rely on.
# ============================================================

from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass(frozen=True)
class DatasetIdentity:
    source: str            # e.g. "binance_futures_bookDepth_archive", "binance_spot_klines"
    symbol: str             # e.g. "BTC/USDT"
    venue: str              # e.g. "binance"
    timeframe: str          # e.g. "1h"
    start_date: str         # "YYYY-MM-DD"
    end_date: str           # "YYYY-MM-DD"
    schema_version: str = "1"
    row_count: Optional[int] = None   # filled in AFTER acquisition — None means "not yet acquired"

    def fingerprint(self) -> str:
        """
        Deterministic sha256 hex digest over every identity field. Two
        DatasetIdentity instances with the same fields (including row_count)
        always produce the same fingerprint — the same acquisition run
        re-executed produces the same identity string; a materially
        different acquisition (different window, different row count
        because the source changed) produces a different one.
        """
        payload = "|".join(str(v) for v in asdict(self).values())
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fingerprint_dataset(
    source: str, symbol: str, venue: str, timeframe: str,
    start_date: str, end_date: str, row_count: Optional[int] = None,
    schema_version: str = "1",
) -> str:
    """Convenience wrapper — see DatasetIdentity.fingerprint()."""
    return DatasetIdentity(
        source=source, symbol=symbol, venue=venue, timeframe=timeframe,
        start_date=start_date, end_date=end_date,
        schema_version=schema_version, row_count=row_count,
    ).fingerprint()
