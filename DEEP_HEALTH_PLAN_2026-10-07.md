# Deep Health: Implementation and Audit Plan (2026-10-07)

**Request:** upgrade System Health into a genuine five-gate Deep Health system, reusing the existing
`deep_health_check` infrastructure. No change to strategy, execution, P&L formulas, research methodology, live
wiring or holdout rules.

## 1. What exists today (inspected)

| Piece | What it does | Verdict |
|---|---|---|
| `/system/health/` → `bot/views/system_health.py` → `terminal_data.get_system_health()` | DB `SELECT 1`, 6 import checks, research-file freshness, API-key presence | Shallow: "import OK" proves nothing about correctness. **Kept verbatim as L0.** |
| `bot/management/commands/deep_health_check.py` (3,194 lines) | L1–L5 structural/functional/behavioural/integration/drift walk of every module; `HealthFinding` + `compute_overall_severity` (max, never averaged); 12 standalone category checks | Good sink (findings, worst-of severity), but a module-walking audit (slow, generic), not gate-organised known answers. **Reused as the CLI entry and finding sink; its modes are unchanged.** |
| `bot/tests/test_deep_health_check.py` (34 tests) | Tests for the above | Kept; must still pass. |
| Canonical component tests (below) | Known answers per component | Most are `SimpleTestCase` (no DB), so **L2 runs them in-process, unchanged**, as the deepest evidence. |

**Owner-decision status relevant to health (from the governance log and code):**
- D-2 block permutation is calibrated (size 3.3–6.3 %, 9 scenarios).
- **OPEN-36 is unresolved:** the calibration gate margins δ and α are owner-declared
  (`bot/quant_core/calibration.py:size_gate`). So no runtime calibration gate may report PASS; it reports
  BLOCKED(OPEN-36), with the measured rate and Wilson bound shown as descriptive evidence.
- **D-4** Step-17 null: BLOCKED, owner compute decision pending.
- **D-5** DCA statistic: BLOCKED, owner definitions pending.
- **D-6** point-in-time universe: BLOCKED.
- Canonical walk-forward fold boundaries: BLOCKED.
- **FX** (OD-FX-1 to 9): BLOCKED.
- Engle-Granger size: descriptive only (worst Wilson upper bound 13.8 % > 5 %), so PARTIAL.

These are **reported**, not decided. No new owner decision is needed to build the system: every one of them
surfaces as BLOCKED with its precise reason.

## 2. Architecture (no parallel framework)

- **`bot/deep_health/`** holds the gate-organised known-answer checks:
  - status vocabulary;
  - `@health_check` registry;
  - runner;
  - one module per level.
- **`deep_health_check`** gains `--level L0|L1|L2`. It runs that suite, maps every non-PASS result into the
  existing `HealthFinding` (FAIL → RED, BLOCKED/UNKNOWN → ORANGE, PARTIAL → YELLOW) and writes a JSON report.
  The existing `--mode fast|standard|deep` path is untouched.
- **L0** = `terminal_data.get_system_health()` unchanged (each shallow check becomes a G1 row), plus a real
  PostgreSQL probe: connect, write → read → forced rollback on a TEMP table, migrations fully applied, journal
  and trade tables present.
- **Statuses:** PASS / PARTIAL / FAIL / BLOCKED / UNKNOWN / N/A.
  - Aggregation is worst-of, never averaged: FAIL > BLOCKED > UNKNOWN > PARTIAL > PASS; N/A ignored.
  - The overall status can never be green while any FAIL/BLOCKED/UNKNOWN/PARTIAL exists, and the page lists
    every non-PASS row.
- **A check that raises** is FAIL (G1, "check crashed"), never skipped.
- **A check whose prerequisite is missing** (module absent, decision pending, L2 not run on this commit) is
  BLOCKED or UNKNOWN with the reason.
- **Page:** L0 + L1 run live on each request, with no files written; target well under 2 s.
  - L2 is CLI-only (`python manage.py deep_health_check --level L2`). It writes
    `logs/deep_health/L2_<run_id>.json`, which the page shows.
  - The page marks it UNKNOWN when it is missing or was produced on a different code commit.
- **Safety:**
  - Every level runs inside a no-network guard (socket connect refused).
  - Trading/execution objects are never constructed against an exchange.
  - The DB probe touches only a TEMP table inside a rolled-back transaction.
  - The holdout ledger is exercised only with a temp ledger path (never the real one).
  - L2's reused suites are `SimpleTestCase` (Django forbids DB queries in them).

## 3. Component plan: existing tests reused and new deterministic checks

L = level. Gates checked, per component. "Reused" = the canonical suite that already proves the component in
CI; L2 re-runs the DB-free ones in-process. "New" = the runtime known-answer / invariant / mutation check added
here (small hand-derived fixtures, independent of the code under test).

| # | Component (area) | L | Reused canonical tests | New deterministic checks (gate) |
|---|---|---|---|---|
| 1 | Shallow infrastructure checks (Infra) | 0 | `get_system_health()` itself | every existing row preserved as a G1 row (ok → PASS, warn → PARTIAL, err → FAIL) |
| 2 | PostgreSQL (Infra) | 0 | n/a | connect + version (G1); TEMP-table write/read/rollback with the row gone after rollback (G1); unapplied migrations = 0 (G1/G5); journal + trade tables present (G1) |
| 3 | Position sizer (Quant) | 1 | `test_fixed_fraction_execution`, `test_risk_controls_inheritance`, deep_health L3 `_test_q_*` | 10,000 @ 1 %, entry 100 / stop 98 → 50.0, risk = 100 (G2); short side symmetric (G2); floors, never rounds up: 33.3333 (G2/G4); 10 % request capped at MAX_RISK_PCT (G4); entry == stop / balance ≤ 0 → 0.0 = skip (G1, fail-closed) |
| 4 | Canonical pair P&L `bot.pairs.pnl` (Quant) | 1 | `test_pairs_pnl_component` (23), `test_pairs_pnl_identity` (7) | hand-derived LONG_SPREAD example (qty, mark P&L, slippage, fees, borrow, net) (G2); SHORT_SPREAD sign mirror (G2); identity net = mark − slip − fees − borrow (G2); half + half = whole (G2); borrow only on the short leg, first hour charged (G4) |
| 5 | Execution timing contract `quant_core.timing` (Quant) | 1 | `test_timing_contract` (4) | open path [100, 101, 103, 106, 110], d = 1, h = 1 → label₀ = 103/101 − 1 (G4); d = 0 refused unless diagnostic (G4); no open column → refused, no close fallback (G1/G4); last d + h labels NaN = censored (G4) |
| 6 | Execution costs `engines.simulation` + venue table (Quant) | 1 | `test_execution_costs`, `test_cost_model`, `test_order_manager_cost_overrides` | slippage signs for LONG/SHORT entry/exit at 0.0005 (G2/G4); fees 100 → 110 × 2 @ 0.1 % = 0.42 (G2); gross P&L LONG +20 / SHORT −20 (G2); Kraken fee > Binance; FEE_RATE = Binance row (G4); **silent fallbacks reported**: unknown direction → price unchanged (warning only); bad P&L input → 0.0 (G1 PARTIAL, reported not changed) |
| 7 | Funding / financing `quant_core.funding` (Quant) | 1 | `test_perp_funding` (10) | 2 units short @ 100, settlements +0.0003 / +0.0001 → cost −0.08 (shorts receive positive funding) (G2/G4); sign flip → +0.04 (G4); uncovered window → cost None, never 0 (G1/G4); gap > 8 h → None (G4); settlement exactly at t0 excluded, at t1 included (G4) |
| 8 | Regime detector (Quant) | 1 / 2 | `test_regime_precomputer` (1), deep_health L4 pipeline | L1: strong synthetic up-trend → TRENDING_UP; flat low-vol → not trending; every RegimeResult field populated (G1/G2). L2: causal mutation, precompute on the full frame vs frame with rows ≥ k replaced by noise → results < k identical at 3 cut points (G5) |
| 9 | Canonical Kalman `quant_core.kalman` (Quant) | 1 / 2 | `test_quant_core_kalman` (17), `test_kalman_seed_lineage` (8) | L1: known β = 1.5 recovered from a wrong seed (β₀ = 1.0) within 4 posterior sd + 0.02 (G2/G3); NaN observation = predict-only (G1); gap > gap_max → invalid, re-seed to θ₀ with segment + 1 (G5); seed candle = ZONES.S0 (G5). L2: future mutation, outputs ≤ t unchanged when observations > t change (G5) |
| 10 | DCA engine `research.dca_engine` (Quant) | 1 | `test_dca_engine` (27), `test_dca_phase2` (41) | NEXT_OPEN known path: open at the next open after the first close (G4); ladder fill at the next open after a close-confirmed trigger (G4); weighted average = Σ usdt / Σ qty and capital = Σ usdt (G2); open cycle at data end → censored, exit DATA_END_STILL_OPEN, no fabricated fill (G4); sealed-candle input refused (G5). D-5 statistic → BLOCKED |
| 11 | OOS validator `research.oos_validator` (Research) | 1 | `test_oos_validator` (59, DB TestCase, CI only) | build_folds on 2,000 h, horizon 24, embargo 12: chronological, disjoint, test gap ≥ purge + embargo, n_purged = 24, no train label overlaps test (G5); a purge-0 mutation is detected as leakage (G5); assert_temporal_disjoint raises on overlap (G1). Canonical walk-forward boundaries → BLOCKED |
| 12 | Research zones / sealing (Research) | 1 | `test_research_zones` (14) | boundary classification S1 / S1 + 1 h / V0 / F − 1 h / F (G5); assert_no_sealed_data raises on a single F candle (G5); split_sealed (G5); open_holdout_evaluation with a temp ledger → "not due", ledger not created (G5); zones hash pinned to the signed D-8 definition (G5) |
| 13 | Provenance / fingerprints (Research) | 1 | `test_stage2d_provenance` (36), journal provenance tests | config_hash key-order invariant, value-sensitive (G5); data_version changes on a 1e-12 edit (round-trip) (G5); provenance sidecar write → read round trip in a temp dir (G5); validate_canonical_provenance rejects a wrong methodology (G5); code_state reports commit / dirty (G5) |
| 14 | Decision journal (Research) | 1 | `test_decision_journal_phase1`–`phase7` (131) | simulate_rule_path known answers: target, stop-first both-in-bar, gap-through at open, time exit, censored (G2/G4); bars after the exit do not change the result (G5); validate_terminal refuses an inconsistent status/stage (G1); live gate vector: price-sanity gate first (G1); outcome display zones = ("SELECTION",) only (G5) |
| 15 | Statistical inference (Research, G3) | 1 / 2 | `test_d2_inference` (10), `test_quant_core_calibration` (14) | L1: method selection, confirmatory test = block permutation, block = max(4h, 24), B = 999 (G3); x = y → p at its floor 1/(B + 1) (G3). L2: small null-size run, measured rate + Wilson bound reported; **formal gate BLOCKED (OPEN-36)**. EG cointegration → PARTIAL (worst Wilson UB 13.8 %). D-4 null → BLOCKED. Journal block bootstrap: L2 coverage sanity (descriptive) |
| 16 | Reused canonical suites (L2, all areas) | 2 | the `SimpleTestCase` modules above, run in-process under the network guard | each suite PASS / FAIL with its counts; `TestCase` (DB) suites listed as CI-only, not run |

## 4. New tests (`bot/tests/test_deep_health_gates.py`)

- **Correct known answers PASS:** every L1 component reaches its documented status (PASS, or the specific
  PARTIAL/BLOCKED with its reason).
- **Deliberately corrupted outputs FAIL:** the sizer returns ×2; pair net P&L off by 1; fees doubled.
- **Sign errors FAIL:** the slippage direction is flipped; the funding sign is flipped.
- **Timing errors FAIL:** `forward_label` filling at d = 0; a DCA fill at the same close.
- **Look-ahead mutations FAIL:** a regime precompute reading future rows; Kalman reading a future observation.
- **Missing prerequisites produce BLOCKED/UNKNOWN:** a missing module → BLOCKED; no L2 report → UNKNOWN; an L2
  report from another commit → UNKNOWN; OPEN-36 → BLOCKED.
- **No state change:** TradeRecord / journal / position counts and the holdout ledger are byte-identical
  before and after; no socket connects.
- **Determinism:** two runs give identical results (excluding the run ID and timestamp).
- **Shallow checks preserved:** every `get_system_health()` row appears in L0.
- **Aggregation:** one FAIL among PASSes → overall FAIL; N/A ignored; BLOCKED beats PARTIAL.
- **Page:** renders the overall status and the three areas; a failing component and its expected-vs-observed
  values are visible; L2 UNKNOWN is shown when there is no report.

Each new test is mutation-checked against its target.

## 5. Out of scope (unchanged)

- Strategy, execution, P&L formulas, methodology, live wiring and holdout rules.
- The silent fallbacks in `engines.simulation` are **reported** as G1 PARTIAL, not changed. Changing them would
  alter execution behaviour, which needs an explicit request.

## 6. Implementation result (2026-10-07)

- **Built as planned:**
  - `bot/deep_health/`: `core.py`, `l0_infrastructure.py`, `l1_quant.py`, `l1_research.py`, `l2_integrity.py`,
    `dashboard.py`.
  - `deep_health_check --level L0|L1|L2|all`.
  - The `/system/health/` dashboard.
  - Six-state template filters.
- **Speed:**
  - Page (L0 + L1): about 0.5 s warm, about 6–8 s on the first request after a server start (module imports).
  - L2: 46 s.
- **L1 on the current code:** 10 PASS, 1 PARTIAL (execution-cost fallbacks), 7 BLOCKED (owner decisions /
  OPEN-36), 0 FAIL. L0: PostgreSQL PASS; legacy rows PARTIAL only for the unset `BINANCE_API_KEY`.
- **L2:** regime and Kalman causality PASS; null size 9/200 = 4.5 % and bootstrap coverage 179/200 = 89.5 % (both
  BLOCKED by OPEN-36); the 16 reused canonical suites all PASS in-process.
- **Tests:** `bot/tests/test_deep_health_gates.py`, 35 tests, all pass.
  - Mutations of the health core: 7/7 caught (worst-of, crash handling, uncovered gate, network guard, stale L2,
    tolerance, calibration gate).
  - Regression (deep_health_check, views, data-layer contracts, navbar, forex terminal): 131 tests; the only
    failure is the known pre-existing forex currency-balance test.
- **Errors found during the build:**
  - Two wrong hand-derived expected values (borrow: 0.208104, not 0.0020810), caught by the check itself.
  - A wall-clock-dependent message (fixed: holdout probe uses E − 1 h).
  - The report-dir default bound at import time (fixed).
  - Suite stdout and disabled logging distorting L2 (fixed: captured stdout, logging re-enabled per suite).
