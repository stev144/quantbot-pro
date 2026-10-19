"""
bot/research/fx_holdout_guard.py
Code-level holdout protection for the FX DCA research pipeline.

Implements F2 from the FX DCA audit (2026-10-16): the FX holdout
(starting 2024-12-24) was not code-protected by the existing ZONES
system (which seals the crypto holdout from 2026-10-04 onward).

Design:
  - FxDataRole enum — labels each FxSplits partition so callers always
    know what they hold.
  - FxHoldoutGuard — derived from the ACTUAL per-pair FxSplits, NOT
    manually hardcoded.  Uses the EARLIEST holdout start across all
    pairs for maximum protection.
  - assert_no_fx_holdout(df, purpose) — raises FxHoldoutSealedError
    if any row falls in the sealed holdout window.

Limitations (honest per audit F2):
  The underlying CSV files in data/forex/ remain readable by any
  process.  This guard protects the SIMULATION and ANALYSIS call paths
  (assert_no_fx_holdout is called at the top of simulate_dca_path_fx).
  A process that bypasses the simulation entry point and reads the CSV
  directly is NOT protected by this guard.  The sealed window is
  2024-12-24 00:00 UTC onward (earliest holdout across all pairs).

V6 holdout (FX-DCA-PROTOCOL-1-A1/A5): data-derived; EUR/USD SHORT holdout
test consumed 2026-10-17, p=0.0380 SUGGESTIVE.

V7 holdout (FX-DCA-PROTOCOL-1-A6, amendment A6-3): all data after
2026-10-16 08:00 UTC. Hard-coded as V7_HOLDOUT_START. Cannot be opened
before 2027-10-16 AND >= 10 closed trades per direction. Use
make_v7_fx_holdout_guard() to obtain a guard for this window.

Protocol: FX-DCA-PROTOCOL-1-A6 (supersedes A1).
"""
# claude code changed: new file — 2026-10-16, F2 holdout protection; FxDataRole + FxHoldoutGuard
# claude code changed: v7/A6-3 — added V7_HOLDOUT_START constant + make_v7_fx_holdout_guard()

from __future__ import annotations

import enum
from typing import Dict, Optional

import pandas as pd


# ── Role labels ───────────────────────────────────────────────────────────────

class FxDataRole(enum.Enum):  # claude code changed: new — explicit role for every FxSplits partition
    """Labels a DataFrame's position within the FX DCA protocol splits."""
    CALIBRATION = "CALIBRATION"          # permitted for simulation and analysis
    CALIBRATION_EMBARGO = "CALIBRATION_EMBARGO"  # never used in simulation
    HOLDOUT = "HOLDOUT"                  # SEALED until holdout-release authorised


# ── Exception ─────────────────────────────────────────────────────────────────

class FxHoldoutSealedError(RuntimeError):  # claude code changed: new — mirrors HoldoutSealedError for FX
    """Raised when a simulation or analysis path attempts to use sealed FX holdout data."""


# ── Guard ─────────────────────────────────────────────────────────────────────

class FxHoldoutGuard:  # claude code changed: new — F2 FX holdout protection
    """
    Protects the sealed FX holdout window.

    Instantiate via make_fx_holdout_guard() (derived from actual FxSplits)
    or FxHoldoutGuard(holdout_start) for testing with an explicit boundary.

    The holdout_start is the EARLIEST holdout start across all five
    protocol pairs — using the strictest (earliest) boundary ensures every
    pair is protected even when per-pair row counts differ by 1–2 candles.
    """

    def __init__(self, holdout_start: pd.Timestamp) -> None:  # claude code changed: new
        if holdout_start.tzinfo is None:
            holdout_start = holdout_start.tz_localize("UTC")
        self._holdout_start: pd.Timestamp = holdout_start.tz_convert("UTC")

    @property
    def holdout_start(self) -> pd.Timestamp:  # claude code changed: new — read-only accessor
        return self._holdout_start

    def assert_no_fx_holdout(self, df: pd.DataFrame, purpose: str) -> None:  # claude code changed: new
        """
        Raise FxHoldoutSealedError if any row's timestamp is at or after
        the sealed holdout start.

        Parameters
        ----------
        df : pd.DataFrame
            Must contain a 'timestamp' column of UTC-aware (or UTC-naive)
            timestamps.
        purpose : str
            Human-readable description used in the error message so the
            caller can be identified in logs.

        Notes
        -----
        This check protects the simulation and analysis entry points only.
        Direct CSV access is not prevented by this guard.  See module
        docstring for the full scope statement.
        """
        if df is None or len(df) == 0:
            return
        if "timestamp" not in df.columns:
            raise ValueError(f"{purpose}: DataFrame has no 'timestamp' column — cannot verify holdout safety")
        ts = pd.to_datetime(df["timestamp"], utc=True)
        n_sealed = int((ts >= self._holdout_start).sum())
        if n_sealed:
            raise FxHoldoutSealedError(
                f"{purpose}: {n_sealed} row(s) at or after the sealed FX holdout start "
                f"{self._holdout_start.isoformat()} — research may not use them "
                f"(FX-DCA-PROTOCOL-1-A1 Part 4, F2 holdout guard)"
            )

    def is_calibration_safe(self, df: pd.DataFrame) -> bool:  # claude code changed: new — non-raising check
        """Return True iff no row falls in the sealed holdout window."""
        if df is None or len(df) == 0 or "timestamp" not in df.columns:
            return True
        ts = pd.to_datetime(df["timestamp"], utc=True)
        return bool((ts < self._holdout_start).all())


# ── Factory ───────────────────────────────────────────────────────────────────

def make_fx_holdout_guard(splits_by_pair: "Dict[str, FxSplits]") -> FxHoldoutGuard:  # claude code changed: new
    """
    Derive FxHoldoutGuard from the actual FxSplits for all protocol pairs.

    Uses the EARLIEST holdout start across all pairs so that every pair
    is protected even when per-pair row counts differ by 1–2 candles.

    Parameters
    ----------
    splits_by_pair : dict[str, FxSplits]
        Mapping from canonical pair name (e.g. "EUR/USD") to the FxSplits
        produced by make_fx_splits() for that pair.

    Returns
    -------
    FxHoldoutGuard
        Configured with the strictest (earliest) holdout start across all
        provided splits.

    Raises
    ------
    ValueError
        If splits_by_pair is empty or any FxSplits has an empty holdout.
    """
    if not splits_by_pair:
        raise ValueError("splits_by_pair must contain at least one pair")

    earliest: Optional[pd.Timestamp] = None
    for pair, splits in splits_by_pair.items():
        if len(splits.holdout) == 0:
            raise ValueError(f"FxSplits for {pair!r} has an empty holdout — cannot derive holdout start")
        ts_col = splits.holdout["timestamp"]
        first_holdout = pd.to_datetime(ts_col.iloc[0], utc=True)
        if earliest is None or first_holdout < earliest:
            earliest = first_holdout

    return FxHoldoutGuard(holdout_start=earliest)  # type: ignore[arg-type]


def load_fx_holdout_guard_from_csvs(
    csv_paths: "Dict[str, str]",
) -> FxHoldoutGuard:  # claude code changed: new — convenience loader from the collected CSVs
    """
    Load all five FX CSVs, run make_fx_splits on each, and derive the guard.

    This is the canonical way to obtain the guard at analysis time without
    manually reading or hardcoding any timestamp.

    Parameters
    ----------
    csv_paths : dict[str, str]
        Mapping from canonical pair name to CSV file path.
        e.g. {"EUR/USD": "data/forex/EURUSD_1h.csv", ...}
    """
    # Import here to avoid circular import at module load time
    from bot.research.fx_fold_splitter import make_fx_splits  # claude code changed: deferred import; avoids circular
    splits_by_pair = {}
    for pair, path in csv_paths.items():
        df = pd.read_csv(path, parse_dates=["timestamp"])
        splits_by_pair[pair] = make_fx_splits(df)
    return make_fx_holdout_guard(splits_by_pair)


# ── Module-level convenience constant ────────────────────────────────────────
# Populated lazily — call get_default_fx_holdout_guard() rather than importing
# this directly, so that tests can pass explicit guards without touching CSVs.
_DEFAULT_GUARD: Optional[FxHoldoutGuard] = None

_DEFAULT_CSV_PATHS = {  # claude code changed: protocol pair → collected CSV path
    "EUR/USD": "data/forex/EURUSD_1h.csv",
    "GBP/USD": "data/forex/GBPUSD_1h.csv",
    "USD/JPY": "data/forex/USDJPY_1h.csv",
    "USD/CHF": "data/forex/USDCHF_1h.csv",
    "AUD/USD": "data/forex/AUDUSD_1h.csv",
}


def get_default_fx_holdout_guard(csv_paths: Optional[Dict[str, str]] = None) -> FxHoldoutGuard:  # claude code changed: new
    """
    Return the shared FxHoldoutGuard, loading it from CSVs on first call.

    Uses a module-level cache so CSVs are only loaded once per process.
    Pass csv_paths to override the default locations (useful in tests).
    """
    global _DEFAULT_GUARD
    if csv_paths is not None:
        return load_fx_holdout_guard_from_csvs(csv_paths)
    if _DEFAULT_GUARD is None:
        _DEFAULT_GUARD = load_fx_holdout_guard_from_csvs(_DEFAULT_CSV_PATHS)
    return _DEFAULT_GUARD


# ── V7 holdout boundary (FX-DCA-PROTOCOL-1-A6, amendment A6-3) ───────────────

V7_HOLDOUT_START = pd.Timestamp("2026-10-16 08:00", tz="UTC")  # claude code changed: v7/A6-3 — hard-coded v7 holdout boundary; do NOT move earlier


def make_v7_fx_holdout_guard() -> FxHoldoutGuard:  # claude code changed: v7/A6-3 — factory for the v7 holdout guard
    """
    Return an FxHoldoutGuard sealed at the v7 holdout boundary.

    The v7 holdout is all data from 2026-10-16 08:00 UTC onward
    (FX-DCA-PROTOCOL-1-A6, amendment A6-3). It cannot be opened before
    2027-10-16 AND >= 10 closed trades per direction have accumulated.

    Use this guard in any script that processes live or freshly fetched
    FX data — it prevents accidentally reading v7 holdout rows in a
    research context.
    """
    return FxHoldoutGuard(holdout_start=V7_HOLDOUT_START)
