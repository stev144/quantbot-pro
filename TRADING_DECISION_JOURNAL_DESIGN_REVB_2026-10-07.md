# Trading Decision Journal: Methodology Revision B

**Quant Bot Pro / Steph Quant Technologies · 2026-10-07 · Supersedes Revision A**
(`TRADING_DECISION_JOURNAL_DESIGN_2026-10-07.md`, kept unchanged for the record).
Status: design only. No implementation code. Live trading, DRY_RUN, strategies and existing results are untouched.
All five gates: NOT EVALUATED.

## Verdict

**NOT IMPLEMENTATION-READY.**

- 19 owner decisions (§14: OD-J1 to OD-J18, with OD-J7 split into a and b) and 1 verification item (V-1)
  are open.
- I have not chosen any of them by assumption. Where the code or the brief leaves a choice open, §14 lists the
  options and stops there.
- **Phase 1 becomes implementation-ready** when these 6 are signed: OD-J1, OD-J5, OD-J6, OD-J7a, OD-J13, OD-J15.
  - Phase 1 = recorder in the single-symbol **backtester only**, opt-in, byte-identical when off.
- Every later phase has its own blocking decisions (§14).

---

## 1. What Revision B changes (the 11 mandatory clarifications)

| # | Clarification | Revision B response | § |
|---|---|---|---|
| 1 | Separate lifecycle stages from terminal outcomes | Stages (`EVALUATED`, `SIGNAL_GENERATED`, `GATES_EVALUATED`, `EXECUTION_ATTEMPTED`) live in an append-only stage log. The terminal outcome is a separate, write-once record. `SIGNAL_GENERATED` is no longer an outcome | 5 |
| 2 | Define a meaningful evaluation point for NO_SIGNAL | An evaluation point is defined per engine from the code as it is. Bot clock ticks, warm-up candles, closures, in-position candles without a signal, missed candles and re-evaluations are **not** evaluation points | 4 |
| 3 | Opportunity Episode identifier | Analytic, derived and versioned. It never changes decision rows; the rule has an open owner choice (OD-J14) | 7 |
| 4 | Gate vector independent of terminal status; no causal value from the first failure | The vector is stored per gate, independently. The terminal outcome is the gate that *actually* stopped the engine, a record-keeping fact only. Causal statements only from pre-registered ablation runs | 6 |
| 5 | Intrabar ambiguity explicit; stop-first is not truth | Every rule label carries `path_resolution`, `ambiguity` and `outcome_assumption`. Ambiguous labels store both variants, and results are reported as bounds | 9 |
| 6 | MAE/MFE named by resolution | `*_bar_1h` names and a `excursion_resolution` field. Described as bar-resolution excursion, with the exit-bar overstatement flagged | 10 |
| 7 | Exploration/confirmation boundary | Data that showed a pattern can never confirm it, whenever the registration happened. Zone V label reads are guarded and logged. V-1 is an open verification | 11 |
| 8 | Legacy backfill limits | Legacy decisions get no snapshot. Any later reconstruction is `RECONSTRUCTED_POST_HOC`, never point-in-time, and excluded by default. Legacy populations are flagged incomplete | 8 |
| 9 | Environment, execution mode, research zone kept separate | Four independent fields (environment, run_type, execution_mode, research_zone) with only logically necessary constraints | 3 |
| 10 | n ≥ 30 is display only | Renamed `display_min_episodes`. It never certifies validity; validity comes from the inference method | 12 |
| 11 | Sole sources of truth unchanged | Restated as a binding list | 2 |

## 2. Sole sources of truth (binding)

The journal **reads from and writes alongside** these; it never re-implements them:

| Domain | Sole source |
|---|---|
| Five gates | `FIVE_GATE_CORRECTNESS_MATRIX_2026-10-03.md` and its gate definitions |
| Evidence classification | `bot/quant_core/verdict_ladder.py` (D-7) |
| Hypotheses, families, multiple testing | `bot/quant_core/hypothesis_registry.py` (D-3) |
| Provenance | `bot/quant_core/provenance.py`, `bot/research_lab/data_fingerprint.py` |
| OOS / folds | `bot/research/oos_validator.py` |
| Research zones, seal, ledger | `bot/quant_core/research_zones.py` (D-8) |
| Label timing | `bot/quant_core/timing.py` (D-1) |
| Market calendars, universes | `bot/quant_core/market_profile.py` |
| Costs, funding, borrow | `bot/config/execution_costs.py`, `bot/quant_core/costs.py`, `bot/quant_core/funding.py` |
| Real P&L | `bot/journal/models.TradeRecord` (and `bot/pairs/pnl.py` for pairs) |
| Inference | `bot/quant_core/inference.py` |

Any extension (for example a registry event type, a new provenance `kind`, or a new `execution_mode` value)
is made **inside** that module, with its own version bump, never in a parallel file.

## 3. Identity dimensions (kept separate)

| Field | Meaning | Values | Set by |
|---|---|---|---|
| `environment` | Where the process ran | **Owner vocabulary, OD-J15** (I will not invent one) | Run start |
| `run_type` | What kind of process made the decision | `LIVE_LOOP`, `BACKTEST`, `RESEARCH_ENGINE`, `LEGACY_BACKFILL` | Run start |
| `execution_mode` | Whether orders were real | Existing vocabulary `LIVE`, `DRY_RUN`, `UNKNOWN` (`execution_mode_for`), plus `SIMULATED` for backtests (OD-J15; a vocabulary extension made in `journal.models`) | Run start; never inferred from data |
| `research_zone` | Which zone the decision's information time falls in | `ZONES` labels (S, embargo, V, unassigned, sealed F, …) with `zones_hash` | Computed per decision from `information_time` |
| `venue`, `account_label` | Where an order would go | Existing venue ids; account is a label, never a number | Run start |

**Constraints (logical necessity only):**
- `LIVE_LOOP` ⇒ `execution_mode` ∈ {LIVE, DRY_RUN};
- `BACKTEST` / `RESEARCH_ENGINE` ⇒ `SIMULATED`;
- `LEGACY_BACKFILL` ⇒ copied from the source row (UNKNOWN allowed).

Nothing else is inferred. "Live implies zone F" is never assumed; the zone is always computed. Decision rows
carry copies of the run-level fields, validated equal to the run, so that every query can filter on each one
independently.

## 4. Meaningful evaluation points

**Definition.** An evaluation point is one occasion on which an engine **could have opened a new position and
actually ran its entry logic**. Each one is keyed by (engine, strategy, strategy_version, symbol, venue,
execution_mode, closed-candle open time).

### 4.1 Per engine, as the code behaves today (verified 2026-10-07)

| Engine | Is an evaluation point | Is NOT an evaluation point |
|---|---|---|
| Backtester (`backtester.run`) | A candle `i ≥ 100` where the engine is flat, no entry is pending, and `i` is not the candle on which a trade closed (the loop `continue`s there) | Candles while a trade is open or an entry is pending; the exit candle; `i < 100` |
| Live single-symbol loop (`bot_runner`) | A **newly closed** candle (closed-candle rule, `drop_forming_candles`) on which the router runs **while the engine is flat** in that symbol | Polling iterations without a new closed candle; a repeat evaluation of an already-evaluated candle (e.g. after a restart, which is recorded as `reevaluation_of`, not a new point); candles never evaluated because the loop drifted or was down |
| Pairs live / research, DCA | **Not defined in Revision B.** The pairs and DCA entry logic must be read before a definition is written. Out of Phase 1 scope (OD-J13) | — |

### 4.2 Excluded by rule, for every engine
- **Warm-up:** the strategy returns `not_enough_data` / `indicator_warmup` (backtester counters), or there is
  less history than the strategy's `min_candles`. These are counted in run coverage, never as NO_SIGNAL.
- **Market closures:** candles inside the instrument's `MarketProfile.calendar` closures.
- **In-position candles without a signal:** these are not opportunities.

### 4.3 Recorded, but outside opportunity denominators
- **Live in-position signals.** The live loop runs the router while a position is open, and `execute_signal`
  refuses at `has_position` / the OPEN-row check. Those refusals are real engine decisions. They are recorded
  with `entry_eligible = False` and terminal DUPLICATE_REJECTED or PORTFOLIO_REJECTED (§5.3), and are excluded
  from every opportunity-rate denominator.
- **Engine difference:** the backtester never evaluates in position, so these rows exist only for live. This
  difference is recorded, not harmonised: harmonising would be a strategy change.

### 4.4 Missed and failed candles
- **Missed candle** (loop drift, downtime): no evaluation happened. A `JournalGap(kind = EVALUATION_NOT_PERFORMED)`
  is recorded. It is not NO_SIGNAL.
- **Due candle whose data failed** (no closed candle, price deviation, balance unavailable): an evaluation point
  existed and was refused, so the outcome is DATA_REJECTED.

The live loop sleeps 3,600 s *after* each pass, so its period exceeds one hour and it will eventually skip a
candle. The journal must make that visible (gap rows), not hide it.

## 5. Lifecycle stages vs terminal outcomes

### 5.1 Records
- `DecisionEvent`: the evaluation point. Inserted once, at `EVALUATED`.
- `DecisionStageEvent`: append-only, one row per stage reached, with a timestamp.
- `DecisionOutcome`: **write-once**, exactly one per decision, holding the terminal outcome.

### 5.2 Stages (not outcomes)
`EVALUATED` → `SIGNAL_GENERATED` → `GATES_EVALUATED` → `EXECUTION_ATTEMPTED`.

### 5.3 Terminal outcomes and allowed transitions

| From the last stage reached | Allowed terminal outcomes |
|---|---|
| `EVALUATED` (no signal produced) | NO_SIGNAL, REGIME_REJECTED (router does not trade the regime, so no strategy ran), DATA_REJECTED, SYSTEM_REJECTED* |
| `GATES_EVALUATED` (a gate refused) | REJECTED (strategy-level rule, e.g. quality filter, ENTRY_SIGN_MISMATCH), COST_REJECTED, LIQUIDITY_REJECTED, TIMING_REJECTED, RISK_REJECTED, PORTFOLIO_REJECTED, BORROW_REJECTED, DUPLICATE_REJECTED, DATA_REJECTED, SYSTEM_REJECTED* |
| `EXECUTION_ATTEMPTED` | EXECUTED (plus a `TradeRecord`), EXECUTION_FAILED (plus an `ExecutionIncident`) |

\* SYSTEM_REJECTED only if OD-J1 approves it.

- Any other transition is refused.
- A decision without an outcome at run close is a recorder defect (backtest), or an **orphan** (live, §5.4).
- **Duplicate vs portfolio rule (deterministic, from the code's condition):**
  - a signal in the **same direction** as the open position → DUPLICATE_REJECTED / POSITION_ALREADY_OPEN;
  - the **opposite direction** → PORTFOLIO_REJECTED / POSITION_ALREADY_OPEN.

### 5.4 Write-ahead (live only, Phase 6)
1. `EXECUTION_ATTEMPTED` is committed **before** the order is sent.
2. On restart, an attempted decision without an outcome is an orphan.
3. Exchange reconciliation (the existing `reconcile_with_exchange` gate) resolves it to EXECUTED or
   EXECUTION_FAILED, with `terminal_source = RECONCILIATION`.
4. `terminal_source` ∈ {ENGINE, RECONCILIATION, LEGACY_BACKFILL}.

## 6. Gate vector and attribution

- **`GateEvaluation`** holds one row per (decision, gate): `gate_id`, `gate_version`, `order_index` (the
  engine's actual order), `result` ∈ {PASS, FAIL, NOT_EVALUATED, NOT_APPLICABLE}, the measured value, the
  threshold, and `evaluated_in_shadow`.
- **The terminal outcome is independent of the vector.** It is the gate that actually stopped the engine, an
  observed fact of execution order. It is stored for record-keeping only.
- **Shadow evaluation** of the gates after the refusing one is permitted only for gates proven side-effect-free.
  This needs pure evaluators extracted from `execute_signal`: Phase 6, with equivalence tests (OD-J7b). Until
  then those gates are `NOT_EVALUATED`.

**Attribution rules (binding):**
1. **A1.** The first failing gate is never used to attribute value, cost or causality to a gate.
2. **A2.** Outcomes conditioned on the gate vector are **associations**, confounded by the other gates, the
   regime and selection into evaluation. They are reported as such.
3. **A3.** A causal statement within the model ("this gate changed simulated outcomes by X") comes **only** from
   a pre-registered **ablation backtest**: the identical pipeline with one gate disabled, same data, costs and
   zones. It is registered in `HypothesisRegistry` (≤ 5 per family, OD-J18). It is never a broad sweep.
4. **A4.** Decisions where a gate is `NOT_EVALUATED` are excluded from analyses of that gate. The excluded count
   is always reported, and the analysis is marked incomplete.

## 7. Opportunity episodes (analytic only)

- An episode groups consecutive decisions that represent **one persisting opportunity**, so that a signal
  repeating over several candles is not counted as several independent opportunities.
- It is a **derived, versioned analysis artifact** (`EpisodeAssignment`: decision → `episode_id`,
  `episode_rule_version`). It **never** changes decision rows.
- **Proposed rule `QBP-JRNL-EPISODE-1`.** Within (strategy, strategy_version, symbol, venue, execution_mode, run
  lineage), an episode is the maximal run of consecutive candles with a same-direction signal (live in-position
  observations included). It is broken by:
  - (a) an evaluation point with no same-direction signal;
  - (b) a direction change;
  - (c) any `JournalGap` or missed candle;
  - (d) **the case below, which I cannot decide.**
- **(d) is undecidable from data, so it is OD-J14.** After a trade exits while the signal persists, is the next
  signal the same episode?
  - **Live** observes in-position signals, so continuity is observable there.
  - **The backtester does not evaluate in position**, so continuity across a trade is *unobservable*. Choosing
    "same episode" or "new episode" there is an assumption either way.
  - Options for the owner:
    - (i) always break at exit;
    - (ii) never break, treating an unobserved interval as continuing;
    - (iii) break, and report both counts.
  - The stricter choice for counting independent opportunities is (ii), because it yields fewer episodes. But it
    asserts continuity that was not observed.
- **NO_SIGNAL** points are not episodes.
- **Statistical unit:** per-opportunity analyses use one row per episode (its first decision, or an episode
  aggregate). Per-decision analyses use block methods (§12).

## 8. Snapshots and legacy limits

| `snapshot_kind` | Meaning | Usable as point-in-time features? |
|---|---|---|
| `POINT_IN_TIME_LIVE` | Captured by the live process at the decision, with `input_window_sha256` of the candles it saw | Yes |
| `SIMULATED_CAUSAL` | Computed inside a backtest run from candles ≤ t, by the code that made the simulated decision. Causal, but simulated; its data vintage is the run's `data_version`, which may differ from what live saw | Yes, labelled simulated; never mixed with LIVE in one dataset without the field |
| `RECONSTRUCTED_POST_HOC` | Computed later for a decision that was made without it | **No.** Never presented as the original snapshot; excluded from feature datasets by default; carries the reconstruction code version and date |

**Legacy backfill (binding limits):**
- Legacy `DecisionEvent`s are created from `TradeRecord` (EXECUTED), `SignalRejection` (REJECTED /
  ENTRY_SIGN_MISMATCH) and `ExecutionIncident` (EXECUTION_FAILED), with `terminal_source = LEGACY_BACKFILL`,
  provenance `NOT_RECORDED` and **no snapshot**.
- Reconstructing features for them is allowed only as `RECONSTRUCTED_POST_HOC`.
- The legacy population is **incomplete by construction**: no NO_SIGNAL, no other rejections, no denominators.
  It is flagged `LEGACY_INCOMPLETE_POPULATION` and cannot support rates, rejection-value, episode or
  opportunity-cost analyses.
- It never enters a registered family.

## 9. Counterfactual labels: intrabar ambiguity

The rule from Revision A (OD-J2: fill at the open of t+1, planned SL/TP, time exit H) is kept, with these
fields on every rule label:

- `path_resolution`: the data resolution the path was evaluated on (`BAR_1H` today).
- `ambiguity`:
  - `NONE`;
  - `STOP_AND_TARGET_SAME_BAR` (both inside one bar's range, the order unknowable at this resolution);
  - `FILL_BAR_BOTH_TOUCHED` (the same, on the fill bar).

  A gap through a level at a bar's **open** is resolved, not ambiguous: the open price is known.
- `outcome_assumption` ∈ {`RESOLVED`, `STOP_FIRST_ASSUMED`, `TARGET_FIRST_ASSUMED`}.

For an ambiguous label **both variants are stored**.
- **Stop-first is the conservative baseline** and is named as an assumption, never as the observed outcome.
- **Target-first is the optimistic bound.**
- Results are reported as **[stop-first, target-first] intervals**, together with the share of ambiguous labels.
- A finding must survive stop-first **and** be reported with its bound.
- "Ambiguous excluded" may be shown only as a labelled sensitivity check. Excluding them is itself a selection.
- Time to stop or target is in **bars**, not hours.
- Exact ordering needs finer data (1-minute or ticks). That is not available in the research data today, so it
  is not claimed. Adding it is a separate decision (OD-J17).

## 10. MAE/MFE resolution

- Fields: `mae_bar_1h_r`, `mfe_bar_1h_r` (and price versions), `excursion_resolution = BAR_1H`. They are always
  described as **bar-resolution excursion**, never as exact intrabar excursion.
- **Window:** the fill bar through the exit bar.
  - The fill bar's high/low after a fill at its open is valid.
  - The **exit bar's** extreme may occur *after* the exit (e.g. a stop hit mid-bar), so bar-resolution
    excursion can **overstate** it. Each value carries `exit_bar_included` and is also stored excluding the
    exit bar (OD-J9).
- **Real trades:** the same definitions in `PostTradeMetrics`. Live fills happen just after a candle closes, so
  the fill price is recorded and the fill bar is used from its open. The same resolution caveat applies.

## 11. Exploration / confirmation boundary

1. **B1. Data that revealed a pattern can never confirm it.** Registering the hypothesis *after* seeing it does
   not convert that data into confirmatory evidence. Under D-3, exploration uses zone S and confirmation uses
   zone V, after registration.
2. **B2. Zone V labels are read-guarded.** The journal label reader refuses V rows unless it is called inside a
   confirmatory run bound to a registered, open family. Each such read is appended to the registry's
   append-only event log, as a registry extension with a version bump (OD-J16).
   - Dashboards and ad-hoc analytics may show zone S labels and real P&L. They never show V labels.
3. **B3. Contamination is recorded, not ignored.** If V labels for a question are viewed outside B2, that
   question's V is consumed and can no longer confirm it. It would need the one-shot sealed F, or new data.
4. **B4. The family is fixed at registration.** Patterns noticed *during* a confirmatory run are exploratory and
   cannot be confirmed on the same V.
5. **B5. V is shared across research programs.** The registry controls error per family; cross-program reuse of
   V is disclosed in every journal finding.
6. **V-1 (verification, not a decision; unresolved).** Were the parameters of the moving-average and
   mean-reversion strategies, and the router thresholds, set while data in zone V (2024-04-22 onward) was
   visible?
   - If so, V is not independent of those strategies' design, and journal findings about them cannot be
     confirmatory in V.
   - I have **not** verified this. It must be established from git history and the design records before any
     Phase 4 confirmatory run. It does not block Phases 1–3.

## 12. Statistics: the display threshold is not validity

- `display_min_episodes` (OD-J12; Revision A proposed 30) only controls whether a descriptive cell is
  **displayed**. Suppressed cells are counted and their number is shown.
- **n ≥ 30 never implies statistical validity, normality or significance.** Under serial dependence and
  overlapping horizons, even large n can be far from independent.
- Validity comes only from:
  - dependence-aware inference (`inference.py` block methods, D-10 block lengths);
  - family control (registry);
  - adequate power;
  - and, for any new test, calibration (`calibration.py`).

## 13. Updated domain model and schema

```
JournalRun (environment, run_type, execution_mode, venue, account_label, provenance header)
 ├─* DecisionEvent            evaluation point; inserted once at EVALUATED
 │    ├─* DecisionStageEvent  append-only stages with timestamps
 │    ├─* GateEvaluation      per-gate result, value, threshold, order, shadow flag
 │    ├─1 DecisionOutcome     write-once terminal outcome + reason + planned values + states + terminal_source
 │    ├─0..1 MarketSnapshot   POINT_IN_TIME_LIVE | SIMULATED_CAUSAL   (never RECONSTRUCTED here)
 │    ├─0..1 ReconstructedSnapshot   RECONSTRUCTED_POST_HOC only, separate table
 │    ├─* DecisionLabel       FORWARD_RETURN | COUNTERFACTUAL_RULE; resolution, ambiguity, assumption, variant
 │    ├─0..1 TradeRecord      existing; + decision_id   (EXECUTED only)
 │    │     └─0..1 PostTradeMetrics   bar-resolution MAE/MFE, shortfall
 │    ├─0..1 SignalRejection / ExecutionIncident   existing; + decision_id
 │    └─* HumanAnnotation     separate; never a feature
 ├─* JournalGap               RECORDER_FAILURE | EVALUATION_NOT_PERFORMED | DATA_OUTAGE
 └─(derived) EpisodeAssignment   decision → episode_id, episode_rule_version   (analysis artifact)
```

| Record | Key fields | Constraints |
|---|---|---|
| JournalRun | run_id, environment, run_type, execution_mode, venue, account_label, code commit / dirty / digest, methodology, protocol hash, config hash (redacted), zones hash, profile hash, universe id, data version, feature / label / taxonomy / episode-rule versions, status | Immutable once CLOSED |
| DecisionEvent | decision_id, run, engine, strategy, strategy_version, symbol, venue, candle_open_time, information_time, entry_eligible, reevaluation_of, research_zone, zones_hash, copies of the run identity fields | Unique on the evaluation-point key per run; insert-only; identity copies equal the run |
| DecisionStageEvent | decision, stage, at | Append-only; stage order enforced |
| GateEvaluation | decision, gate_id, gate_version, order_index, result, value, threshold, evaluated_in_shadow | Insert-only |
| DecisionOutcome | decision (unique), terminal_outcome, reason_code, reason_stage, terminal_source, planned entry/SL/TP/risk/expected R/estimated cost + status, risk/portfolio/liquidity/financing/data-quality states, at | Write-once; transition table §5.3 enforced |
| MarketSnapshot | decision, snapshot_kind ∈ {POINT_IN_TIME_LIVE, SIMULATED_CAUSAL}, feature_set_version, values, available_at, input_window_sha256, missing | Every available_at ≤ information_time |
| ReconstructedSnapshot | decision, reconstruction code version, computed_at, values | Excluded from feature datasets unless explicitly requested and labelled |
| DecisionLabel | decision, outcome_kind (`*_NOT_EXECUTED`), label_type, label_version, rule_id, variant, horizon, window start, status, path_resolution, ambiguity, outcome_assumption, hypothetical fill / exit, gross / net R, costs, `mae_bar_1h_r`, `mfe_bar_1h_r`, bars to stop / target | Window start > information_time; no link to TradeRecord |
| PostTradeMetrics | trade, `mae_bar_1h_*`, `mfe_bar_1h_*`, exit_bar_included, excursion_resolution, shortfall, metrics_version, status | Derived; never edits TradeRecord |
| HumanAnnotation, JournalGap | As Revision A, plus the gap `kind` | Insert-only |
| EpisodeAssignment | decision, episode_id, episode_rule_version | Derived file / table; never joined back into decision rows |

The reason taxonomy (`QBP-JRNL-REASON-1`) is as in Revision A, plus one addition: **the backtester's existing
no-signal reason vocabulary** (`no_trend`, `no_pullback`, `rsi_out_of_range`, `no_momentum`, `no_setup`,
`trend_conflict`, `structure_not_uptrend`, `structure_not_downtrend`, `invalid_range`, `flat_market`, …) is mapped
code by code into NO_SIGNAL reasons. Warm-up reasons are excluded, per §4.

## 14. Owner decisions (signature required; none chosen by assumption)

| ID | Decision | Recommendation | Blocks phase |
|---|---|---|---|
| OD-J1 | Add SYSTEM_REJECTED (reconciliation gate)? | Yes | 1 |
| OD-J2 | Counterfactual rule (open t+1 fill, planned SL/TP, gap-through at the open, H = 24, venue costs, short funding) | As stated | 2 |
| OD-J3 | Label horizons | Fixed {1, 4, 24} + rule outcome | 2 |
| OD-J4 | Labels for live/dry-run decisions in F | SEALED until the D-8 ledger evaluation | 2 |
| OD-J5 | Bulk storage | Compressed CSV + sidecar (no new dependency) | 1 |
| OD-J6 | Retention of evaluation points | Keep every evaluation point (NO_SIGNAL included), live and backtest | 1 |
| OD-J7a | Recorder failure in the **backtester** | Abort the run as INCOMPLETE (no trading consequence; strictest) | 1 |
| OD-J7b | Recorder failure **live**, and shadow gate evaluators | Decision unchanged + JournalGap; evaluators only with equivalence tests | 6 |
| OD-J8 | Attachment storage | Object storage + hash; none until chosen | 5 |
| OD-J9 | MAE/MFE window | Store both with and without the exit bar | 2 |
| OD-J10 | scikit-learn | Defer | 7 |
| OD-J11 | Registry endpoint for journal questions | Add by registry version bump | 4 |
| OD-J12 | `display_min_episodes` value | 30, display only | 4 |
| OD-J13 | Evaluation-point definitions (§4) for the backtester and live loop; Phase 1 scope limited to single-symbol; pairs/DCA definitions deferred until their code is read | Approve as written | 1 |
| OD-J14 | Episode break at trade exit (§7: options i / ii / iii) | No recommendation forced: (iii) reports both, which avoids asserting unobserved continuity | 4 |
| OD-J15 | `environment` vocabulary; add `SIMULATED` to `execution_mode` | The owner names the environments; add SIMULATED | 1 |
| OD-J16 | Zone V read guard and logging as a registry extension (B2) | Approve | 4 |
| OD-J17 | Ambiguity reporting (stop-first baseline, target-first bound, interval + ambiguous share); finer path data later? | Approve the reporting; finer data deferred | 2 |
| OD-J18 | Gate ablation studies only as registered hypotheses (A3) | Approve | 4 |
| V-1 | Verify strategy-parameter provenance relative to zone V | Must be done before any confirmatory run | 4 |

(Revision A's precedence question is withdrawn. The terminal outcome is the gate that actually stopped the
engine, an observed fact, not a choice.)

## 15. Carried forward from Revision A, with Revision B amendments

- **Retention, privacy, security (Revision A §15):** unchanged.
- **Testing (Revision A §16), with these additions:**
  - transition-table enforcement (every illegal transition refused);
  - write-once outcome;
  - orphan detection;
  - evaluation-point keys (no tick duplicates, re-evaluation linked);
  - warm-up and closure exclusion;
  - ambiguity variants (both stored on hand-built same-bar paths);
  - exit-bar MAE/MFE known answers;
  - V read-guard refusal;
  - `RECONSTRUCTED_POST_HOC` excluded from default datasets;
  - identity-field consistency (decision equals run).
- **Migration (Revision A §17):** amended. The backfill writes `DecisionEvent` + `DecisionOutcome
  (terminal_source = LEGACY_BACKFILL)` with no snapshot.
- **Risks (Revision A §18), with these additions:**
  - live loop drift skipping candles (made visible by gap rows);
  - backtest/live evaluation-point asymmetry (recorded, not harmonised);
  - V contamination through dashboards (B2/B3).
- **What not to build yet (Revision A §19):** unchanged.
- **Phases (Revision A §21):** unchanged in order. Each phase starts only when its blocking decisions in §14 are
  signed.

*End of Revision B. No code written. All gate statuses NOT EVALUATED.*


---

## Amendment 1 (2026-10-07, Phase 1 implementation): corrections found in the code
- **§13 (reason taxonomy):** "the backtester's existing no-signal reason vocabulary (`no_trend`, `rsi_out_of_range`,
  `no_setup`, `trend_conflict`, …)" was WRONG.
  - Those are stale keys pre-seeded in `Backtester.rejections`, and no current strategy emits them.
  - The reasons actually emitted, and their mapping, are in `bot/journal/taxonomy.py` (owner D4).
  - No code was created for a reason nothing emits.
- **§3 (identity):** the existing `execution_mode` vocabulary is LIVE / DRY_RUN / RESEARCH / UNKNOWN, not
  LIVE / DRY_RUN / UNKNOWN. Per the owner, Phase 1 uses RESEARCH and SIMULATED is not added.
- **§4 (evaluation points):** the backtester has no separate gate layer. Every refusal happens inside the router or
  a strategy, or is one of three silent fill-time refusals. That is why D2 (option A) and D3 (control-flow-proven
  gate vector) were needed.
- **§5:** a terminal outcome was missing: CENSORED_END_OF_DATA (D5), allowed after SIGNAL_GENERATED.
- **§13 (schema), Phase 1 deviations, decided by the implementation and recorded here:**
  - stages and the gate vector are stored on the DecisionEvent row as JSON, not in separate tables, because a
    backtest decision's lifecycle is complete when it is written;
  - a separate write-once outcome table is deferred to live wiring, where outcomes arrive asynchronously;
  - coverage is run-level (D8).
- **Router fallback reason `unhandled_regime_{regime}`:** it has no mapping by design, so it becomes a
  MAPPING_FAILURE and the run is INCOMPLETE.
