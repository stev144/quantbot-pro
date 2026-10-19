# ============================================================
# bot/pairs/canonical_eligibility.py
# claude code changed: new file — Stage 2D Rev B D-3: fail-closed CANONICAL
# live entry eligibility.
#
# A pair may open a NEW live position only if a canonical eligibility verdict
# exists for it, produced under the current methodology version and protocol
# hash, on the same content data version as the pair's validated seed record,
# with a run id, and with every piece of evidence it cites carrying the same
# canonical lineage. Anything legacy, stale, missing, unversioned, mixed or
# ambiguous → NEW ENTRY BLOCKED.
#
# Legacy outputs (cointegration_pairs.csv passes_filters, permutation_verdict.csv
# edge_appears_real) are NOT read here and can never ENABLE an entry. They may
# still RESTRICT one (bot/pairs/config.py's legacy pre-filter decides which pairs
# run a signal engine at all): legacy evidence can only narrow, never widen.
#
# Nothing in the repository produces canonical verdicts yet (canonical
# selection/permutation are Steps 13–17), so today every pair is blocked for
# new entries. Existing positions are unaffected: exits never consult this.
#
# This is provenance and eligibility control only — it says nothing about
# whether any pair has an edge.
# ============================================================

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Optional

from bot.quant_core import provenance as prov
from bot.quant_core.protocol import CANONICAL as CANONICAL_PROTOCOL, METHODOLOGY_VERSION, CanonicalProtocol
from bot.quant_core.seed import KalmanSeed, seed_id
from bot.quant_core.status import Result, Status, bad, ok

KIND = "ENTRY_ELIGIBILITY"
ELIGIBLE = "ELIGIBLE"
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ELIGIBILITY_DIR = os.path.join(_BASE_DIR, "research_data", "canonical_eligibility")
EVIDENCE_FIELDS = ("kind", "status", "methodology_version", "protocol_hash", "data_version", "run_id")


def eligibility_path(pair_name: str, registry_dir=ELIGIBILITY_DIR) -> Path:
    return Path(registry_dir) / f"{pair_name.replace('/', '_')}.json"


def write_eligibility(pair_name: str, *, seed: KalmanSeed, verdict: str, evidence: Iterable[dict],
                      protocol: CanonicalProtocol = CANONICAL_PROTOCOL, registry_dir=ELIGIBILITY_DIR,
                      producer: str) -> Path:
    """Write a canonical verdict artifact + its provenance sidecar. For the
    future canonical selection stage (and tests) — no current pipeline calls it."""
    evidence = [dict(e) for e in evidence]
    run_id = prov.make_run_id(kind=KIND, pair=pair_name, seed=seed_id(seed), data=seed.data_version,
                              protocol=protocol.config_hash(), verdict=verdict,
                              evidence=",".join(sorted(e.get("run_id", "") for e in evidence)))
    path = eligibility_path(pair_name, registry_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pair_name": pair_name, "verdict": verdict, "run_id": run_id,
                                "seed_id": seed_id(seed), "evidence": evidence}, indent=2, sort_keys=True),
                    encoding="utf-8")
    code = prov.code_state()
    prov.write_provenance(path, prov.OutputProvenance(
        kind=KIND, status=prov.CANONICAL, methodology_version=METHODOLOGY_VERSION,
        protocol_hash=protocol.config_hash(), data_version=seed.data_version, run_id=run_id,
        producer=producer, artifact_sha256=prov.file_sha256(path), code_commit=code.commit, code_dirty=code.dirty))
    return path


def check_entry_eligibility(pair_name: str, *, seed: Optional[KalmanSeed],
                            protocol: CanonicalProtocol = CANONICAL_PROTOCOL, registry_dir=ELIGIBILITY_DIR) -> Result:
    """OK only for a canonical ELIGIBLE verdict compatible with the current
    methodology, protocol, the pair's seed data version and seed lineage, whose
    evidence items all share that lineage. Every other case → SKIP (blocked)."""
    if seed is None:
        return bad(Status.SKIP, f"{pair_name}: no validated canonical seed — eligibility cannot be established")
    path = eligibility_path(pair_name, registry_dir)
    if not path.exists():
        return bad(Status.SKIP, f"{pair_name}: no canonical eligibility verdict (legacy cointegration/permutation "
                                f"outputs cannot enable a canonical entry)")
    p = prov.read_provenance(path)
    r = prov.validate_canonical_provenance(p, kind=KIND, methodology_version=METHODOLOGY_VERSION,
                                           protocol_hash=protocol.config_hash(), artifact=path,
                                           data_version=seed.data_version)
    if not r.ok:
        return r
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        return bad(Status.SKIP, f"{pair_name}: unreadable eligibility verdict ({e})")
    if body.get("pair_name") != pair_name:
        return bad(Status.SKIP, f"{pair_name}: verdict is for {body.get('pair_name')!r}")
    if body.get("run_id") != p.run_id:
        return bad(Status.SKIP, f"{pair_name}: verdict run id does not match its provenance")
    if body.get("seed_id") != seed_id(seed):
        return bad(Status.SKIP, f"{pair_name}: verdict was produced from a different seed lineage")
    evidence = body.get("evidence") or []
    if not evidence:
        return bad(Status.SKIP, f"{pair_name}: verdict cites no evidence")
    for e in evidence:
        missing = [f for f in EVIDENCE_FIELDS if not e.get(f)]
        if missing:
            return bad(Status.SKIP, f"{pair_name}: evidence {e.get('kind')!r} lacks {missing} (unversioned)")
        if (e["status"], e["methodology_version"], e["protocol_hash"], e["data_version"]) != \
                (prov.CANONICAL, METHODOLOGY_VERSION, protocol.config_hash(), seed.data_version):
            return bad(Status.SKIP, f"{pair_name}: evidence {e['kind']!r} has a different status/methodology/"
                                    f"protocol/data version (mixed-version evidence)")
    if body.get("verdict") != ELIGIBLE:
        return bad(Status.SKIP, f"{pair_name}: canonical verdict is {body.get('verdict')!r}")
    return ok(body)
