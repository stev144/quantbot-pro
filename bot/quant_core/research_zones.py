# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/quant_core/research_zones.py
#
# claude code changed: new file — 2026-10-03, owner decision D-8 (DECISION_SHEET_D1-D11_2026-10-03.md,
# signed 2026-10-03). ONE source of truth for the research zones and the sealed forward holdout.
#
#   INIT        the L = 1000 grid candles immediately before the seed (= the S start)   [Design A, REVB-3]
#   SELECTION   2021-11-07 00:00 → 2024-03-31 23:00 UTC (candle open times, inclusive)
#   EMBARGO     504 candles (21 days) after S — no window may span S and V
#   VALIDATION  2024-04-22 00:00 → 2026-09-24 23:00 UTC
#   UNASSIGNED  2026-09-25 00:00 → 2026-10-03 23:00 UTC — exposed (live dry-run pilot), never evidence
#   HOLDOUT F   2026-10-04 00:00 UTC onward — SEALED; evaluated ONCE at F + 182 days (2027-04-04 00:00
#               UTC), extended in 90-day steps while fewer than 30 trades have occurred
#
# All history before F is PSEUDO_HOLDOUT — NOT CLAIM-ELIGIBLE (every candle has been exposed). Only the
# forward holdout is claim-eligible. Any change to a boundary is a new ZONES_VERSION (new hash).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from bot.quant_core.provenance import config_hash

ZONES_VERSION = "QBP-ZONES-1"
HOUR = pd.Timedelta(hours=1)

PRE_INIT_UNUSED = "PRE_INIT_UNUSED"
INIT = "INIT"
SELECTION = "SELECTION"
EMBARGO = "EMBARGO"
VALIDATION = "VALIDATION"
UNASSIGNED_EXPOSED = "UNASSIGNED_EXPOSED"
FORWARD_HOLDOUT = "FORWARD_HOLDOUT_SEALED"
PSEUDO_HOLDOUT_LABEL = "PSEUDO_HOLDOUT — NOT CLAIM-ELIGIBLE"

DEFAULT_LEDGER = Path(__file__).resolve().parents[1] / "research" / "governance" / "forward_holdout_ledger.json"


class HoldoutSealedError(RuntimeError):
    """Research tried to read or write sealed forward-holdout candles."""


def to_utc(values) -> pd.DatetimeIndex:
    """Timestamps → tz-aware UTC. Numeric values are epoch times: ms if > 1e11, else seconds (a plain
    pd.to_datetime would read epoch-ms as nanoseconds, i.e. 1970, and silently seal nothing)."""
    v = pd.Series(values) if not isinstance(values, (pd.Series, pd.Index)) else values
    if pd.api.types.is_numeric_dtype(v):
        arr = pd.Series(v).astype("float64")
        unit = "ms" if arr.abs().max() > 1e11 else "s"
        return pd.DatetimeIndex(pd.to_datetime(arr, unit=unit, utc=True))
    return pd.DatetimeIndex(pd.to_datetime(v, utc=True))


def _ts(s) -> pd.Timestamp:
    t = pd.Timestamp(s)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


@dataclass(frozen=True)
class ResearchZones:
    init_window_candles: int = 1000                         # = CanonicalProtocol L (REVB-3); checked in tests
    selection_start: str = "2021-11-07T00:00:00+00:00"
    selection_end: str = "2024-03-31T23:00:00+00:00"        # last candle OPEN time in S
    embargo_candles: int = 504
    validation_start: str = "2024-04-22T00:00:00+00:00"
    validation_end: str = "2026-09-24T23:00:00+00:00"
    holdout_start: str = "2026-10-04T00:00:00+00:00"        # F
    first_evaluation: str = "2027-04-04T00:00:00+00:00"     # F + 182 days
    extension_days: int = 90
    min_holdout_trades: int = 30
    signed_by: str = "Steven Bwenje"
    signed_on: str = "2026-10-03"
    decision_ref: str = "DECISION_SHEET_D1-D11_2026-10-03.md#D-8"

    def __post_init__(self):
        s0, s1 = _ts(self.selection_start), _ts(self.selection_end)
        v0, v1 = _ts(self.validation_start), _ts(self.validation_end)
        f, e = _ts(self.holdout_start), _ts(self.first_evaluation)
        if not (s0 < s1 < v0 <= v1 < f < e):
            raise ValueError("zones must be ordered: S_start < S_end < V_start <= V_end < F < first evaluation")
        if v0 != s1 + HOUR + self.embargo_candles * HOUR:
            raise ValueError(f"V must start exactly {self.embargo_candles} candles after the last S candle "
                             f"(expected {s1 + HOUR + self.embargo_candles * HOUR}, got {v0})")
        if e != f + pd.Timedelta(days=182):
            raise ValueError("first evaluation must be F + 182 days")
        for t in (s0, s1, v0, v1, f, e):
            if t != t.floor("h"):
                raise ValueError(f"zone boundary {t} is not on the hourly grid")

    # ── times ────────────────────────────────────────────────────────────────
    @property
    def S0(self): return _ts(self.selection_start)
    @property
    def S1(self): return _ts(self.selection_end)
    @property
    def V0(self): return _ts(self.validation_start)
    @property
    def V1(self): return _ts(self.validation_end)
    @property
    def F(self): return _ts(self.holdout_start)
    @property
    def E(self): return _ts(self.first_evaluation)

    def init_window(self) -> Tuple[pd.Timestamp, pd.Timestamp]:
        """Inclusive open times of the L candles before the seed (= S start)."""
        return self.S0 - self.init_window_candles * HOUR, self.S0 - HOUR

    # ── classification ───────────────────────────────────────────────────────
    def classify(self, ts) -> str:
        t = _ts(ts)
        i0, _ = self.init_window()
        if t >= self.F:
            return FORWARD_HOLDOUT
        if t > self.V1:
            return UNASSIGNED_EXPOSED
        if t >= self.V0:
            return VALIDATION
        if t > self.S1:
            return EMBARGO
        if t >= self.S0:
            return SELECTION
        if t >= i0:
            return INIT
        return PRE_INIT_UNUSED

    def classify_index(self, index) -> pd.Series:
        idx = pd.DatetimeIndex(index)
        idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        return pd.Series([self.classify(t) for t in idx], index=idx, name="zone")

    @staticmethod
    def claim_eligible(zone: str) -> bool:
        return zone == FORWARD_HOLDOUT

    # ── the seal ─────────────────────────────────────────────────────────────
    def assert_no_sealed_data(self, index, purpose: str) -> None:
        """Raise HoldoutSealedError if any timestamp is in the sealed holdout. Every research path that
        produces evidence calls this; the only legitimate reader of holdout candles is a ledgered
        evaluation (open_holdout_evaluation)."""
        if len(index) == 0:
            return
        idx = to_utc(pd.Index(index))
        n = int((idx >= self.F).sum())
        if n:
            raise HoldoutSealedError(f"{purpose}: {n} candle(s) at or after the sealed forward holdout start "
                                     f"{self.F.isoformat()} — research may not read them (D-8)")

    def split_sealed(self, df: pd.DataFrame, ts_col: Optional[str] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """(research_rows, sealed_rows): rows with open time ≥ F go to the sealed store, never to the
        research data directory."""
        ts = to_utc(df[ts_col]) if ts_col else to_utc(pd.Index(df.index))
        sealed = np.asarray(ts >= self.F, dtype=bool)
        return df.loc[~sealed], df.loc[sealed]

    def as_dict(self) -> dict:
        d = asdict(self)
        d["zones_version"] = ZONES_VERSION
        return d

    def zones_hash(self) -> str:
        return config_hash(self.as_dict())


ZONES = ResearchZones()


# ── sealed store for candles ≥ F (written by fetchers, never read by research) ─────────────────────────────
SEALED_DIR_NAME = "_sealed_holdout_F2026-10-04"


def write_sealed_rows(sealed: pd.DataFrame, filename: str, data_root, ts_col: str = "timestamp") -> Optional[Path]:
    """Append-merge sealed rows into <data_root>/_sealed_holdout_F2026-10-04/<filename>. Returns the path,
    or None when there is nothing to seal. Existing sealed rows are kept (dedup on timestamp, keep first:
    a sealed candle, once stored, is never overwritten)."""
    if sealed is None or len(sealed) == 0:
        return None
    out = Path(data_root) / SEALED_DIR_NAME
    out.mkdir(parents=True, exist_ok=True)
    path = out / filename
    new = sealed.copy()
    new[ts_col] = to_utc(new[ts_col]).to_numpy()
    if path.exists():
        prev = pd.read_csv(path)
        prev[ts_col] = to_utc(prev[ts_col]).to_numpy()   # CSV round-trip gives strings: normalise before dedup
        merged = pd.concat([prev, new], ignore_index=True)
        merged = merged.drop_duplicates(subset=[ts_col], keep="first")
    else:
        merged = new
    merged = merged.sort_values(ts_col)
    merged.to_csv(path, index=False)
    return path


# ── evaluate-ONCE ledger ─────────────────────────────────────────────────────────────────────────────────
def seal_before_write(df: pd.DataFrame, filepath, ts_col: str = "timestamp", zones: "ResearchZones" = None) -> pd.DataFrame:
    """For raw-data writers (fetchers): returns only the rows research may store at `filepath`; rows at or
    after F are append-merged into the sealed store next to it (<dir>/_sealed_holdout_F2026-10-04/<name>)."""
    zones = zones or ZONES
    if df is None or len(df) == 0 or ts_col not in df.columns:
        return df
    research, sealed = zones.split_sealed(df, ts_col=ts_col)
    if len(sealed):
        p = write_sealed_rows(sealed, Path(filepath).name, Path(filepath).parent, ts_col=ts_col)
        import logging
        logging.getLogger(__name__).warning(f"D-8 seal: {len(sealed)} row(s) at/after {zones.F.isoformat()} "
                                            f"withheld from {filepath} → {p}")
    return research


def open_holdout_evaluation(run_id: str, *, now: Optional[datetime] = None, n_trades: Optional[int] = None,
                            zones: ResearchZones = ZONES, ledger_path: Path = DEFAULT_LEDGER) -> dict:
    """The single authorised gate to the sealed holdout. Refuses before the scheduled evaluation date,
    refuses a second evaluation, and records the attempt (append-only) BEFORE any holdout data is read.
    If n_trades < min_holdout_trades the evaluation is deferred by extension_days and recorded as such."""
    now = now or datetime.now(timezone.utc)
    now_ts = pd.Timestamp(now).tz_convert("UTC") if pd.Timestamp(now).tzinfo else pd.Timestamp(now).tz_localize("UTC")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else \
        {"zones_version": ZONES_VERSION, "zones_hash": zones.zones_hash(), "events": []}
    if ledger.get("zones_hash") != zones.zones_hash():
        raise HoldoutSealedError("ledger belongs to a different zones definition — refusing")
    due = zones.E
    for ev in ledger["events"]:
        if ev["type"] == "EVALUATED":
            raise HoldoutSealedError(f"the forward holdout was already evaluated (run {ev['run_id']}); "
                                     "it can be evaluated only once")
        if ev["type"] == "DEFERRED":
            due = max(due, _ts(ev["next_due"]))
    if now_ts < due:
        raise HoldoutSealedError(f"holdout evaluation not due until {due.isoformat()} (now {now_ts.isoformat()})")
    if n_trades is not None and n_trades < zones.min_holdout_trades:
        ev = {"type": "DEFERRED", "run_id": run_id, "at": now_ts.isoformat(), "n_trades": int(n_trades),
              "next_due": (due + pd.Timedelta(days=zones.extension_days)).isoformat()}
    else:
        ev = {"type": "EVALUATED", "run_id": run_id, "at": now_ts.isoformat(),
              "n_trades": None if n_trades is None else int(n_trades)}
    ledger["events"].append(ev)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    return ev
