# LCEX-1 — Five-Fill Ladder Cap: Preregistration & Owner Decision

**Identifier:** FX-DCA-LADDER-COUNT-EXPERIMENT-1 (LCEX-1)
**Status:** OWNER DECISION RECORDED — implementation authorised
**Decision date:** 2026-10-19
**Supersedes (for fill-count only):** FX-DCA-PROTOCOL-1-A6 (v7) Part 3 ladder specification

---

## Owner decision

The DCA ladder for all forex pairs is capped at **5 total fills** (1 entry + 4 DCA add-ons,
levels 1–5). Levels 6 and 7 are permanently removed from the forex configuration.

This decision replaces the prior 7-fill configuration (1 entry + 6 DCA add-ons) and applies
to all forex simulation, fold-running, and paper-trading code.

---

## Parameter change

| Parameter | Before (7-fill) | After (5-fill) |
|---|---|---|
| Total fills | 7 | 5 |
| DCA levels | 2–7 (6 levels) | 2–5 (4 levels) |
| base_pips | p40/p60/p75/p85/p92/p97 (6 values) | p40/p60/p75/p85 (4 values) |
| multipliers | (2, 4, 8, 16, 32, 64) | (2, 4, 8, 16) |
| MAX_DEPLOYED_MULTIPLIER | 127 (1+2+4+8+16+32+64) | 31 (1+2+4+8+16) |
| _MAX_LEVELS | 7 | 5 |

All other parameters unchanged: `MAX_CYCLE_CANDLES`, `profit_activation_pct`,
`trailing_pct`, ladder spacings for levels 2–5, exit rules.

---

## Scope

Applies to:
- `bot/research/fx_fold_runner.py` — `FxLadderSpec`, `_MULTIPLIERS`, `_MAX_LEVELS`,
  `_MAX_DEPLOYED_MULTIPLIER`, `build_fx_ladder_spec_from_analysis()`
- `bot/paper_trading/ranging_paper_engine.py` — `_MULTIPLIERS`

Does NOT change:
- QC-2 holdout (sealed until 2027-01-17)
- Full-ladder stop rule (pre-registered 2026-10-18; K values and test window unchanged —
  the "7-fill" label in that document now means the 5-fill cap is the full ladder)
- Ladder spacings for levels 2–5 (derived from calibration MAE quantiles p40–p85)
- Any non-forex code paths

---

## Governance

This document is committed BEFORE the code change, per the five-gate governance rule
(G5: no parameter change without pre-specified rationale committed first).
