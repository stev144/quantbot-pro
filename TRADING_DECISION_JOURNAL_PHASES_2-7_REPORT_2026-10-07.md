# Trading Decision Journal: Phases 2–7 Implementation Report

**Quant Bot Pro · 2026-10-07**
- **Scope.** The owner asked to "build all the 7 journal phases in order" under the five gates. Phase 1 is reported
  separately (`TRADING_DECISION_JOURNAL_PHASE1_IMPLEMENTATION_REPORT_2026-10-07.md`).
- **Decisions.** Every owner decision used here is recorded **before coding** in
  `TRADING_DECISION_JOURNAL_PHASE1_OWNER_SIGNOFF_2026-10-07.md` (Phases 2–7 section: OD-J2/J3/J4/J8/J9/J11/J12/J16/
  J17/J18 delegated to the strictest Revision B recommendations; OD-J14 = (iii); Phase 6 DRY_RUN; Phase 7
  statsmodels only), with the owner's written confirmation of the same.
- **Status of the journal.** It is research infrastructure. **Nothing here is evidence of a trading edge.**

## Phase-by-phase summary

| Phase | What was built | Commit | Tests | Mutations caught |
|---|---|---|---|---|
| 2 Outcomes | `DecisionLabel` (forward 1/4/24 h via the D-1 timing contract; counterfactual rule: next-open fill, planned SL/TP, gap-through at the open, same-bar stop-first baseline + target-first bound, H = 24, venue costs, recorded funding for shorts), `PostTradeMetrics` (bar-resolution MAE/MFE ± exit bar, shortfall), provenance-checked labeler, sealing, zone-S-only display | `4f3c8cf` | 32 | 10/10 (P2-8 exposed a test hole, fixed) |
| 3 Legacy backfill | LEGACY decisions from TradeRecord / ExecutionIncident (pairs legs grouped by `pair_trade_id`), separate taxonomy, no snapshot, NOT_RECORDED provenance, incomplete-population flag; unknown environments skipped, never invented | `5db832a` | 8 | 2/2 |
| 4 Analytics | Opportunity episodes (OD-J14 iii, both counts), the only label reader (zone S; V only for a registered open family, logged; V refused for the backtester strategies per V-1; F/unassigned/legacy never), outcome tables by status/reason/direction/hour/weekday/regime with episode units, OD-J17 interval, display suppression < 30; `time_block_bootstrap_mean_ci` in `inference.py`; registry `QBP-HYP-D3-2` | `da7f40c` | 20 | 6/6 (P4-3 needed a constructed multi-decision episode) |
| 5 Human journaling | `HumanAnnotation` (10 kinds, insert-only revisions, hindsight flag), staff-only read/write, CSRF, no attachments (OD-J8), never in exports/analytics/features | `3989c0a` | 7 | 3/3 |
| 6 Live wiring (DRY_RUN) | `LiveDecisionRecorder` observing `bot_runner` + `ExecutionEngine` through try/except hooks that pass copies; write-ahead `DecisionAttempt`; missed-candle gaps; same-candle re-evaluation not a new point; orphan detection; refuses non-DRY_RUN; off unless `JOURNAL_LIVE_RECORDER=1`; labeler refuses live runs (OD-J4) | (this commit) | 13 | 7/7 (P6-7 added after the regression finding below) |
| 7 Discovery | statsmodels logistic on zone-S episodes, purged walk-forward folds (`oos_validator.build_folds`), out-of-fold log-loss vs base rate + AUC, sign stability ≥ 80 % → CANDIDATES for owner review only; JSON + provenance sidecar, never overwritten; import boundary | (this commit) | 6 | 4/4 (P7-4 exposed a weak refusal test, fixed) |

**V-1 was resolved (strict) during Phase 4.** The backtester strategies were first committed on 2026-08-16, after
most of zone V, so zone V is **not** independent of them. Their journal findings can never be confirmed in V; only
the sealed F is clean. The read guard enforces this.

## Phase 6 equivalence: the owner's blocker test
- **What was driven.** The REAL `bot_runner.process_venue_candle` and the REAL `ExecutionEngine` (DRY_RUN), over 400
  replayed hours of real local BTC candles (`bot/tests/journal_replay.py`, an offline deterministic replay source),
  with the recorder OFF and then ON.
- **Result: identical.** Every per-step status / signal / regime, every trade (prices, sizes, fees, P&L, exit
  reasons), the final positions, the slippage statistics and the drawdown guard.
- **Negative controls.**
  - A recorder that touches the engine changes the bot, and the comparison detects it.
  - A recorder that scribbles on the signal and frame it receives changes nothing, because it only ever gets copies
    (mutation P6-1, which removes the copies, is caught).
  - A crashing recorder changes nothing; its run closes INCOMPLETE.
- **No behavioural difference was found, so there was no blocker.**

## Five-gate audit (Phases 2–7)
| Gate | Status | Evidence | Limits |
|---|---|---|---|
| G1 Software | **PASS** | 86 new tests across phases 2–7; 32/32 mutations caught (3 found real test holes, all fixed: P2-8, P4-3, P7-4); the full regression caught one real Phase 6 defect, fixed and pinned (below); insert-only everywhere; additive / non-destructive migrations 0017–0020 (two constraints relaxed only for clearly-marked legacy or live rows); migrations in sync | Tested on PostgreSQL only |
| G2 Maths | **PASS** | Known-answer tests: rule paths (target, stop, both-in-bar, fill-bar, gap-through stop/target, time exit, censoring, untestable), gross/net R with fees + slippage + short financing, MAE/MFE ± exit bar (including negative MFE from slippage, seen on real trades), forward-return window/censor/gap indices, post-trade shortfall | Bar resolution only (stated on every label) |
| G3 Statistics | **PASS** | Episode units (both OD-J14 counts); dependence-aware time-block bootstrap (D-10 block length in hours, fails closed < 10 blocks, coverage sanity 85–99 % on independent data); display suppression is display-only; discovery uses purged + embargoed walk-forward folds and must beat the base rate out of fold; no p-values or claims | Intervals are descriptive; no confirmatory test was run (none is allowed for the backtester strategies in V, per V-1) |
| G4 Quant | **PASS** | D-1 timing (fill at the open after the decision); venue costs from the single source; short financing from recorded funding, never a silent 0 (funding files end 2026-09-19, later windows → COST_UNAVAILABLE); gaps → UNTESTABLE; F → SEALED; live write-ahead; DRY_RUN only | Live/dry-run outcomes stay sealed until the D-8 evaluation |
| G5 Integrity | **PASS** | Labeler refuses when price data no longer reproduces the run's data_version; zone read guard + registry logging; V-1 enforced; legacy limits; human notes separated; discovery outputs carry provenance and are candidates only; every owner decision recorded before coding | `strategy_version` is UNVERSIONED@commit (the strategies have no version constants) |

## Not done / remaining open
- No confirmatory journal test was run. None may be run in V for the backtester strategies (V-1), and F is sealed
  until the D-8 evaluation (2027-04-04 at the earliest).
- Pairs and DCA evaluation points are still not defined, so the journal does not record those engines.
- No scikit-learn, trees or NLP (owner Phase 7 decision); no screenshot storage (OD-J8).
- The live recorder is off unless `JOURNAL_LIVE_RECORDER=1`, and attaches only to DRY_RUN engines.

## Regression finding (Phase 6), fixed before commit
- **What the regression found.** The full regression found a real defect that the equivalence test could not see:
  `test_phase3_engineering.FormingCandleTests.test_runner_decides_on_closed_candles_only` returned `error` instead of
  `ok`. Three runner hooks read engine state (`has_position`, `open_positions`, `_last_refusal`) in the trading path
  **even with no recorder attached**. The real engine has those attributes; the test's minimal engine does not.
- **That breaks the owner's rule.** With the recorder off, the bot must do exactly what it did before. So it was
  treated as a blocker and fixed.
- **The fix.**
  - Hook arguments are now built lazily, inside `_journal`'s try block, and only when a recorder is attached.
  - The engine's `before_order` hook is skipped entirely when no hook is set.
- **How it is pinned.**
  - The new test `RecorderOffPathTests` drives the BUY path with an engine that has only the pre-Phase-6 attributes.
  - Mutation P6-7 (arguments evaluated eagerly again) is caught.
  - The equivalence suite still passes.

## Regression at completion (offline, network-guarded, after the last edit)
The run was done in batches because an earlier single run was stopped by low system memory.

| Batch | Tests | Result |
|---|---|---|
| Journal Phases 1–7 + UI | 130 | OK |
| Execution / engine / runner / risk / trade persistence / backtester venue / Phase 3 engineering | 95 | 4 failures, all pre-existing |
| Analytics / pairs / forex / provenance / regime / OOS evaluator | 227 | 2 failures; 1 pre-existing (forex currency balance); the other was the Phase 6 defect above, now passing |
| Research lab / D-2 inference / D-3 registry / Market Intelligence fail-fast / navbar | 85 | 1 failure, pre-existing (block-size baseline 0.336) |

- **The six remaining failures existed before this work.** The 3 bot_runner venue-session tests need a real exchange,
  which the offline guard refuses. The other three are `test_exit_updates_trade_record`, the forex currency-balance
  check and the research-lab byte-identical baseline.
- **Proof.** The two live-path modules were rerun with the five modified files reverted to HEAD (and migration 0020
  moved aside): the same 4 failures, identical.
- `makemigrations --check`: no changes.

