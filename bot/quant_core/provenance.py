# ============================================================
# bot/quant_core/provenance.py
# claude code changed: new file — Stage 2D Phase A (governance/versioning).
#
# Primitives that make every research result traceable to: code version,
# specification version, configuration, data, universe and evaluation zone.
# Dataset fingerprints REUSE bot.research_lab.data_fingerprint.DatasetIdentity
# (no second fingerprint scheme). The artifact SCHEMA that stores these
# fields is an open owner decision (Rev B OPEN-37); this module only provides
# deterministic identifiers.
# ============================================================

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from bot.research_lab.data_fingerprint import DatasetIdentity  # reuse, not duplicate

SPEC_VERSION = "STAGE2C-1"   # scratchpad/STAGE2C_CORRECTED_SPECIFICATION.md

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _canonical(obj: Any) -> Any:
    """Normalise a config object so equal configurations hash equally."""
    if isinstance(obj, Mapping):
        return {str(k): _canonical(obj[k]) for k in sorted(obj, key=str)}
    if isinstance(obj, (list, tuple)):
        return [_canonical(v) for v in obj]
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"non-finite value in configuration: {obj!r}")
        return repr(obj)   # exact float text, no rounding
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    raise TypeError(f"unsupported configuration value type: {type(obj).__name__}")


def config_hash(config: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON form of a configuration mapping."""
    text = json.dumps(_canonical(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CodeState:
    commit: Optional[str]       # None when git is unavailable — never a guessed value
    dirty: Optional[bool]       # True when the working tree has uncommitted changes
    dirty_digest: Optional[str] # sha256 of `git status --porcelain` + `git diff HEAD`
    error: str = ""

    @property
    def reproducible(self) -> bool:
        return self.commit is not None and self.dirty is False


def code_state(repo_root: Path = _REPO_ROOT) -> CodeState:
    """Current commit and whether the tree is dirty. A dirty tree is reported
    honestly (dirty=True plus a digest of the uncommitted changes)."""
    def _git(*args: str) -> bytes:
        # raw bytes: diffs may contain text in any encoding, and the digest must be exact
        return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, check=True).stdout

    try:
        commit = _git("rev-parse", "HEAD").decode("ascii").strip()
        porcelain = _git("status", "--porcelain")
        diff = _git("diff", "HEAD")
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError) as e:
        return CodeState(None, None, None, error=f"git unavailable: {e}")
    dirty = bool(porcelain.strip())
    digest = hashlib.sha256(porcelain + diff).hexdigest() if dirty else None
    return CodeState(commit, dirty, digest)


def universe_id(symbols: Iterable[str], selection_timestamp_utc: str) -> str:
    """Identifier of a universe: its sorted membership plus the point-in-time
    selection timestamp (Stage 2C §15). Duplicate symbols are an error."""
    syms = list(symbols)
    if len(set(syms)) != len(syms):
        raise ValueError("universe contains duplicate symbols")
    payload = {"symbols": sorted(syms), "selection_timestamp_utc": selection_timestamp_utc}
    return config_hash(payload)


def trial_id(*, config: Mapping[str, Any], data: Iterable[DatasetIdentity], universe: str,
             zone: Mapping[str, Any], code: CodeState, spec_version: str = SPEC_VERSION) -> str:
    """One research trial = one fully specified configuration evaluated on one
    data segment (Stage 2C §6). Any change to any component changes the id."""
    payload = {
        "spec_version": spec_version,
        "config_hash": config_hash(config),
        "data_fingerprints": sorted(d.fingerprint() for d in data),
        "universe_id": universe,
        "zone": dict(zone),
        "code_commit": code.commit,
        "code_dirty_digest": code.dirty_digest,
    }
    return config_hash(payload)


def provenance_record(*, config: Mapping[str, Any], data: Iterable[DatasetIdentity], universe: str,
                      zone: Mapping[str, Any], code: Optional[CodeState] = None) -> dict:
    """Flat dict of every traceability field, ready to attach to an artifact."""
    data = list(data)
    code = code or code_state()
    return {
        "spec_version": SPEC_VERSION,
        "config_hash": config_hash(config),
        "data_fingerprints": ",".join(sorted(d.fingerprint() for d in data)),
        "universe_id": universe,
        "zone": json.dumps(dict(zone), sort_keys=True),
        "code_commit": code.commit,
        "code_dirty": code.dirty,
        "code_dirty_digest": code.dirty_digest,
        "trial_id": trial_id(config=config, data=data, universe=universe, zone=zone, code=code),
    }



# ════════════════════════════════════════════════════════════════════════════
# claude code changed: Stage 2D Rev B D-5 / G-1 / D-4 — content data version,
# output provenance sidecars and the canonical/legacy distinction.
#
# DatasetIdentity (above, reused) fingerprints IDENTITY metadata (source,
# symbol, dates, row count). It cannot detect a changed price. The data
# version below is a CONTENT fingerprint of the exact rows consumed.
# ════════════════════════════════════════════════════════════════════════════

CANONICAL = "CANONICAL"
LEGACY = "LEGACY"
UNCERTIFIED = "UNCERTIFIED"            # canonical rules applied to input without canonical provenance — never evidence
DATA_VERSION_SCHEME = "QBP-DATAV1"
SIDECAR_SUFFIX = ".provenance.json"


@dataclass(frozen=True)
class DataVersion:
    data_version: str        # sha256 hex of the canonical text (see data_version())
    scheme: str
    timeframe: str
    columns: tuple           # exactly the consumed fields, in the hashed order
    symbols: tuple
    start: str               # ISO UTC open time of the first grid candle hashed
    end: str                 # ISO UTC open time of the last grid candle hashed
    n_grid_candles: int      # per symbol (complete grid start..end)
    n_rows_observed: int     # summed over symbols
    n_missing: int           # summed over symbols (grid candles with no row)

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["columns"], d["symbols"] = list(self.columns), list(self.symbols)
        return d


def _num(v) -> str:
    """Exact float text (repr round-trips every IEEE double); NaN -> 'NaN'."""
    f = float(v)
    return "NaN" if math.isnan(f) else repr(f)


def data_version(frames, *, timeframe: str, columns, start=None, end=None) -> DataVersion:
    """Deterministic content fingerprint of the rows an experiment consumes.

    Canonicalisation (scheme QBP-DATAV1):
      * every frame is indexed by candle OPEN time; tz-naive is read as UTC;
      * ROW ORDER IS NORMALISED (sorted by timestamp) — order is not data;
        an identical duplicate timestamp is collapsed, a conflicting one raises;
      * the hashed range is [start, end] (default: the union of the frames'
        first/last timestamps) on the COMPLETE grid of `timeframe`; a grid
        candle with no row is the literal token MISSING (so removing a row
        changes the hash);
      * one line per (symbol, candle): "<symbol>|<iso ts>|v1|v2|..." with each
        value as exact repr() text, columns in the given order; symbols sorted;
      * header "QBP-DATAV1|<timeframe>|<columns>" so close-only and OHLCV
        fingerprints of the same rows can never collide.
    Pass exactly the columns the experiment consumes (the Kalman pipeline
    consumes only close; an OHLCV consumer passes OHLCV)."""
    import pandas as pd
    cols = tuple(columns)
    if not cols or not frames:
        raise ValueError("data_version needs at least one symbol and one column")
    step = pd.Timedelta(timeframe)
    norm = {}
    for sym, df in frames.items():
        d = df.loc[:, list(cols)].copy()
        idx = pd.DatetimeIndex(d.index)
        d.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        d = d.sort_index()
        dup = d.index.duplicated(keep=False)
        if dup.any():
            if (d[dup].groupby(level=0).nunique(dropna=False) > 1).any().any():
                raise ValueError(f"{sym}: conflicting duplicate rows")
            d = d[~d.index.duplicated(keep="first")]
        norm[str(sym)] = d
    lo = pd.Timestamp(start) if start is not None else min(d.index[0] for d in norm.values())
    hi = pd.Timestamp(end) if end is not None else max(d.index[-1] for d in norm.values())
    lo = lo.tz_localize("UTC") if lo.tzinfo is None else lo.tz_convert("UTC")
    hi = hi.tz_localize("UTC") if hi.tzinfo is None else hi.tz_convert("UTC")
    grid = pd.date_range(lo, hi, freq=step)
    h = hashlib.sha256(f"{DATA_VERSION_SCHEME}|{timeframe}|{','.join(cols)}\n".encode())
    n_obs = n_miss = 0
    for sym in sorted(norm):
        g = norm[sym].reindex(grid)
        present = g.notna().any(axis=1).to_numpy()
        for ts, row, has in zip(grid, g.itertuples(index=False, name=None), present):
            if has:
                n_obs += 1
                h.update(f"{sym}|{ts.isoformat()}|{'|'.join(_num(v) for v in row)}\n".encode())
            else:
                n_miss += 1
                h.update(f"{sym}|{ts.isoformat()}|MISSING\n".encode())
    return DataVersion(h.hexdigest(), DATA_VERSION_SCHEME, timeframe, cols, tuple(sorted(norm)),
                       grid[0].isoformat(), grid[-1].isoformat(), len(grid), n_obs, n_miss)


def file_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class OutputProvenance:
    """Provenance of one research artifact. `status` is CANONICAL only when
    produced under the current Stage 2D methodology/protocol/data rules;
    LEGACY = older or unknown methodology; UNCERTIFIED = canonical rules on
    input without canonical provenance. Only CANONICAL can be evidence."""
    kind: str                 # KALMAN_STATE, ENTRY_ELIGIBILITY, COINTEGRATION_SCAN, PERMUTATION_VERDICT, WALK_FORWARD_SUMMARY, ...
    status: str               # CANONICAL | LEGACY | UNCERTIFIED
    methodology_version: str
    protocol_hash: str        # "" when not produced under a canonical protocol
    data_version: str         # content fingerprint, or "NOT_RECORDED"
    run_id: str
    producer: str             # module that wrote the artifact
    artifact_sha256: str      # sha256 of the artifact bytes this record describes
    code_commit: Optional[str] = None
    code_dirty: Optional[bool] = None
    extra: Optional[dict] = None   # kind-specific lineage (e.g. the seed record)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def sidecar_path(artifact) -> Path:
    return Path(str(artifact) + SIDECAR_SUFFIX)


def make_run_id(**fields) -> str:
    """Deterministic identifier of a run from its defining fields."""
    return config_hash({k: ("" if v is None else v) for k, v in fields.items()})


def write_provenance(artifact, prov: OutputProvenance) -> Path:
    p = sidecar_path(artifact)
    p.write_text(json.dumps(prov.as_dict(), indent=2, sort_keys=True, default=str), encoding="utf-8")
    return p


def legacy_provenance(artifact, *, kind: str, producer: str, data_version: str = "NOT_RECORDED") -> OutputProvenance:
    """Label an artifact written by a pre-Stage-2D (legacy) engine. It stays
    readable as legacy and can never satisfy a canonical requirement."""
    code = code_state()
    digest = file_sha256(artifact)
    return OutputProvenance(kind, LEGACY, "LEGACY-PRE-STAGE2D", "", data_version,
                            make_run_id(kind=kind, producer=producer, artifact=digest, commit=code.commit,
                                        dirty=code.dirty_digest),
                            producer, digest, code.commit, code.dirty)


def write_legacy_provenance(artifact, *, kind: str, producer: str) -> Optional[Path]:
    """Best-effort LEGACY sidecar for a legacy engine's output: a provenance
    failure must never break the legacy research run it describes."""
    try:
        return write_provenance(artifact, legacy_provenance(artifact, kind=kind, producer=producer))
    except OSError:
        return None


# claude code changed: 2026-10-05 — provenance for the NON-CANONICAL research engines (cross-section, feature
# validation, contagion, PCA). Their outputs had no sidecar at all, so when a test overwrote 213 cross-section
# files their origin could not be established. Each sidecar now records WHAT produced the file: the exact inputs
# (per-file sha256, or a QBP-DATAV1 content version for in-memory data), the run configuration, the code state
# and a deterministic run id. Status is always LEGACY — these engines do not follow the canonical protocol.
RESEARCH_ENGINE_METHODOLOGY = "NON-CANONICAL-RESEARCH-ENGINE"
INPUT_FILES_SCHEME = "QBP-INPUTFILES-SHA256-V1"


def input_files_fingerprint(paths) -> dict:
    """Exact identity of the input files a run read: sha256 of each file's bytes, keyed by its path as given;
    `version` is the sha256 of the sorted "path:sha" lines (any changed, added or removed file changes it)."""
    files = {str(Path(p).as_posix()): file_sha256(p) for p in paths}
    version = hashlib.sha256("\n".join(f"{k}:{v}" for k, v in sorted(files.items())).encode("utf-8")).hexdigest()
    return {"scheme": INPUT_FILES_SCHEME, "version": version, "files": files}


def input_frames_fingerprint(frames, *, columns, timeframe: str = "1h") -> dict:
    """Identity of in-memory input data (e.g. loaded through a provider): the QBP-DATAV1 content version of
    exactly `columns` of every frame (reuses data_version())."""
    cols = [c for c in columns if all(c in df.columns for df in frames.values())]
    dv = data_version(frames, timeframe=timeframe, columns=cols)
    return {"scheme": DATA_VERSION_SCHEME, "version": dv.data_version, "symbols": sorted(frames), "columns": cols}


def write_research_output_provenance(artifact, *, kind: str, producer: str, inputs: Optional[dict],
                                     config: Optional[Mapping[str, Any]] = None,
                                     code: Optional[CodeState] = None) -> Optional[Path]:
    """LEGACY sidecar for a non-canonical research engine's output. Compute `inputs` and `code` ONCE per run and
    pass them for every artifact. Best-effort like write_legacy_provenance (a provenance failure must not break
    the research run), but a failure is logged, never silent."""
    import logging
    try:
        code = code or code_state()
        cfg = dict(config or {})
        chash = config_hash(cfg)
        data_ver = (inputs or {}).get("version") or "NOT_RECORDED"
        digest = file_sha256(artifact)
        run_id = make_run_id(kind=kind, producer=producer, data=data_ver, config=chash, commit=code.commit,
                             dirty=code.dirty_digest)
        prov = OutputProvenance(kind, LEGACY, RESEARCH_ENGINE_METHODOLOGY, "", data_ver, run_id, producer, digest,
                                code.commit, code.dirty,
                                extra={"inputs": inputs, "config": cfg, "config_hash": chash,
                                       "evidence_status": "DESCRIPTIVE — non-canonical engine, never evidence"})
        return write_provenance(artifact, prov)
    except (OSError, ValueError, TypeError) as e:
        logging.getLogger(__name__).warning(f"provenance sidecar NOT written for {artifact}: {e}")
        return None


def read_provenance(artifact) -> OutputProvenance:
    """The artifact's provenance. No sidecar, or an unreadable one, is LEGACY
    of unknown methodology — never canonical by default."""
    p = sidecar_path(artifact)
    unknown = OutputProvenance("UNKNOWN", LEGACY, "UNKNOWN", "", "NOT_RECORDED", "", "", "")
    if not p.exists():
        return unknown
    try:
        return OutputProvenance(**json.loads(p.read_text(encoding="utf-8")))
    except (ValueError, TypeError):
        return unknown


def is_sha256(s) -> bool:
    return isinstance(s, str) and len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def validate_canonical_provenance(prov: OutputProvenance, *, kind: str, methodology_version: str,
                                  protocol_hash: str, artifact=None, data_version: Optional[str] = None):
    """Result OK(prov) only for CANONICAL provenance of the expected kind,
    methodology version and protocol hash, with a real content data version
    and run id, whose artifact bytes (if given) still match the recorded
    digest and whose data version (if given) matches."""
    from bot.quant_core.status import Status, bad, ok
    if prov.status != CANONICAL:
        return bad(Status.SKIP, f"{kind}: provenance status {prov.status!r} is not CANONICAL")
    if prov.kind != kind:
        return bad(Status.SKIP, f"provenance kind {prov.kind!r} != {kind!r}")
    if prov.methodology_version != methodology_version:
        return bad(Status.SKIP, f"{kind}: methodology {prov.methodology_version!r} != {methodology_version!r}")
    if prov.protocol_hash != protocol_hash:
        return bad(Status.SKIP, f"{kind}: protocol hash {prov.protocol_hash!r} != {protocol_hash!r}")
    if not is_sha256(prov.data_version):
        return bad(Status.SKIP, f"{kind}: no content data version recorded")
    if data_version is not None and prov.data_version != data_version:
        return bad(Status.SKIP, f"{kind}: data version {prov.data_version[:12]} != expected {str(data_version)[:12]}")
    if not prov.run_id:
        return bad(Status.SKIP, f"{kind}: no run id")
    if artifact is not None and file_sha256(artifact) != prov.artifact_sha256:
        return bad(Status.SKIP, f"{kind}: artifact bytes do not match the provenance digest (modified or substituted file)")
    return ok(prov)
