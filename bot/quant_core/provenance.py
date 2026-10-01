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
