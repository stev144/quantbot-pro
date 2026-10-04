# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/quant_core/verdict_ladder.py
#
# claude code changed: new file — 2026-10-03, owner decision D-7 (DECISION_SHEET_D1-D11_2026-10-03.md, signed).
# Replaces SUPPORTED with a ladder; each level requires EVERY level below it; a missing input caps the verdict at
# the level below it and is never assumed.
#
#   OBSERVATION  an effect in the hypothesised direction was observed
#   STATISTICAL  + dependence-aware test (D-2) AND confirmation inside a registered D-3 family (Holm, zone V)
#   ECONOMIC     + positive net of real fees, slippage and financing, with complete costs (borrow row)
#   TRADEABLE    + causal execution d ≥ 1 (D-1), passes zone V, capacity checked at trade size
#   CANONICAL    + full provenance AND a pass on the sealed forward holdout (D-8 ledger)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import List, Optional, Tuple

LADDER_VERSION = "QBP-LADDER-D7-1"
LEVELS = ("OBSERVATION", "STATISTICAL", "ECONOMIC", "TRADEABLE", "CANONICAL")
LEGACY_POSITIVE_VERDICTS = ("SUPPORTED", "PARTIALLY_SUPPORTED")    # stored pre-D-7 rows: UNVERIFIED (F-11)


@dataclass(frozen=True)
class LadderEvidence:
    """Every field is True (requirement met), False (failed) or None (input missing → caps, never assumed)."""
    effect_observed: Optional[bool] = None            # OBSERVATION
    dependence_aware_test: Optional[bool] = None      # STATISTICAL (D-2)
    family_confirmed: Optional[bool] = None           # STATISTICAL (D-3 registry: Holm reject in zone V)
    net_positive_after_costs: Optional[bool] = None   # ECONOMIC
    cost_complete: Optional[bool] = None              # ECONOMIC (fees + slippage + recorded financing)
    causal_execution_d1: Optional[bool] = None        # TRADEABLE (D-1)
    passes_validation_zone: Optional[bool] = None     # TRADEABLE (zone V)
    capacity_checked: Optional[bool] = None           # TRADEABLE
    provenance_complete: Optional[bool] = None        # CANONICAL
    forward_holdout_passed: Optional[bool] = None     # CANONICAL (D-8 ledgered evaluation)


REQUIREMENTS = {
    "OBSERVATION": ("effect_observed",),
    "STATISTICAL": ("dependence_aware_test", "family_confirmed"),
    "ECONOMIC": ("net_positive_after_costs", "cost_complete"),
    "TRADEABLE": ("causal_execution_d1", "passes_validation_zone", "capacity_checked"),
    "CANONICAL": ("provenance_complete", "forward_holdout_passed"),
}


def ladder_level(evidence: LadderEvidence) -> Tuple[Optional[str], List[str]]:
    """Highest level whose requirements — and all lower levels' — are True. Returns (level or None, reasons the
    ladder stopped). None means not even OBSERVATION (no effect in the hypothesised direction)."""
    level, reasons = None, []
    for lvl in LEVELS:
        missing = [r for r in REQUIREMENTS[lvl] if getattr(evidence, r) is None]
        failed = [r for r in REQUIREMENTS[lvl] if getattr(evidence, r) is False]
        if missing or failed:
            if failed:
                reasons.append(f"{lvl} not reached — failed: {', '.join(failed)}")
            if missing:
                reasons.append(f"{lvl} not reached — missing input (capped, never assumed): {', '.join(missing)}")
            break
        level = lvl
    return level, reasons


assert set(f.name for f in fields(LadderEvidence)) == {r for v in REQUIREMENTS.values() for r in v}
