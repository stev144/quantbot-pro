# Stage 2D Rev B — D-1 / D-2 / D-3 / D-4 / D-5 / G-1 + L / Seed Lock: Implementation Report

**Date:** 2026-10-02. **Methodology version:** `STAGE2D-REVB-3` (bumped from REVB-2, because L, Design A and the provenance rules changed the methodology).

**Final status:** see §20.

---

## 1. Baseline test status (before any change)

- Focused subset on the unchanged tree: **123 tests, OK** (lifecycle, seed, both parity suites, Kalman/protocol, pairs accounting, pairs config, executability).
- Last full focused regression on the same tree: **544 OK, 1 skipped** (pre-Step-12 audit).

## 2. Files inspected

- **Journal and live path:** `bot/journal/models.py` (`TradeRecord`); `bot/pairs/trade_logger.py`; `bot/pairs/execution.py`; `bot/pairs/signal_engine.py`; `bot/core/pairs_bot_runner.py`; `bot/pairs/config.py`; `bot/pairs/health.py`.
- **Existing provenance:** `bot/quant_core/provenance.py`; `bot/research_lab/data_fingerprint.py` (`DatasetIdentity`); `bot/research_lab/models.py` (`ResearchExperiment.data_fingerprint`).
- **Research engines:** `bot/research/kalman_filter_engine.py`; `entry_exit_engine.py`; `cointegration_engine.py`; `permutation_test_engine.py`; `walk_forward_engine.py`.
- **Parity harnesses:** `parity_forensics.py`, `parity_replay.py`.

**Mechanisms found:**
- `TradeRecord` is the only live journal table. No event or audit table exists.
- `DatasetIdentity` fingerprints identity **metadata** (source, symbol, dates, row count), not content.
- `quant_core.provenance` has `config_hash` and `code_state`, but no artifact sidecars.

These shaped the choices: extend the journal app and `quant_core.provenance` rather than add new systems.

## 3. Files changed in this task

| File | Change |
|---|---|
| `bot/quant_core/provenance.py` | **+** `data_version()` (D-5 content fingerprint), `DataVersion`, `file_sha256`, `OutputProvenance`, sidecar read/write, `legacy_provenance` / `write_legacy_provenance`, `validate_canonical_provenance`, `make_run_id`, status constants (G-1, D-4) |
| `bot/quant_core/protocol.py` | `METHODOLOGY_VERSION = STAGE2D-REVB-3`; `init_window_candles = 1000`; `seed_design = "A_PRE_SELECTION"` (validated) |
| `bot/quant_core/seed.py` | seed record adds `seed_design` and `data_version`; `validate_seed` enforces Design A, L and a content data version; new `seed_id()` and `validate_design_a()` |
| `bot/research/kalman_filter_engine.py` | `run_canonical` computes the data version; new `write_canonical_dataset()` (canonical CSV + provenance sidecar in `<out>/canonical/`) |
| `bot/research/entry_exit_engine.py` | `CanonicalProvenanceError`; `rule_set="CANONICAL"` validates input provenance before reading; new `CANONICAL_UNCERTIFIED` mode; summary lineage fields; output sidecars; D-1 trade fields |
| `bot/journal/models.py` + `bot/migrations/0012_…` | `TradeRecord.reconciliation_affected` and `reconciliation_gaps`; new `SignalRejection` table (D-1, D-2) |
| `bot/pairs/trade_logger.py` | `log_leg_exit(…, reconciliation_affected, reconciliation_gaps)`; new `log_signal_rejection()` |
| `bot/pairs/execution.py` | `close_pair_trade(…, reconciliation_affected, reconciliation_gaps)`, passed through to both legs |
| `bot/pairs/signal_engine.py` | the exit stamps the reconciliation flag and gaps; a C1 rejection carries its `EntrySignMismatch`; new `abort_entry()` |
| `bot/pairs/canonical_eligibility.py` | **new** — D-3 fail-closed canonical eligibility |
| `bot/core/pairs_bot_runner.py` | journals rejections; the log line prints the reason; D-3 gate on new entries only; reconciliation provenance on exit; `session.seed`; aborts the engine-side entry when an entry is not executed |
| `bot/research/cointegration_engine.py`, `permutation_test_engine.py`, `walk_forward_engine.py` | one `write_legacy_provenance(...)` call after each eligibility- or evidence-relevant output (G-1) |
| `bot/research/parity_forensics.py`, `parity_replay.py` | the research side uses `CANONICAL_UNCERTIFIED` (an explicit shared θ₀ is not a canonical dataset); D-1/C3 flags in trade pairs; `clean_trade_pairs()` |
| Tests | new `bot/tests/test_stage2d_provenance.py`; updated `test_canonical_seed.py`, `test_canonical_lifecycle.py`, `test_quant_core_kalman.py` |
| Docs | `STAGE2D_CANONICAL_PROTOCOL.md` (version, L, Design A, status rows); this report |

### Deliberately unchanged

- `bot/pairs/pnl.py` (0-line diff);
- `bot/research/dca_engine.py` (0-line diff);
- sizing and capital;
- all historical result files (none deleted, overwritten or relabelled);
- `research_lab` models and `DatasetIdentity` (reused only conceptually);
- the FX, zones and holdout code paths (none exist yet).

---

## 4. D-1 — reconciliation-affected exit provenance

**Definition (single, from the existing C5 state):** an exit taken while `PairSignalEngine.requires_reconciliation` is True. No second definition of reconciliation was created.

**Where it lives:**

| Place | How |
|---|---|
| Live trade object | `trade.reconciliation_affected` and `trade.reconciliation_gaps` (ISO, `;`-joined), stamped in the exit branch |
| Runner | passes both into `close_pair_trade` |
| Trade logger | writes both onto **each leg's** `TradeRecord` row |
| Database | migration `0012` adds the fields, so the flag survives restart |
| Research `TradeRecord` | carries the same two fields (always False in research, which has no live gaps) and serialises them |
| Parity harness | live exits carry the flag; `trade_pairs` has `live_reconciliation_affected`; `ForensicReport.clean_trade_pairs()` drops reconciliation-affected and data-incomplete rows, so they never count as clean parity evidence |

**Unchanged behaviour:** existing positions may still exit during reconciliation; new entries stay blocked.

## 5. D-2 — durable sign-mismatch rejection

- **Storage:** a new `SignalRejection` table in the **journal app**, next to `TradeRecord`. There is no separate logging system.
- **Fields:** pair, venue, decision timestamp, intended direction, entry z, held spread, α_e, β_e, the decision-candle closes, reason, methodology version, protocol hash, and the seed's content data version.
- **Idempotency:** a unique constraint on (pair, venue, decision candle, reason), so re-logging after a restart is a no-op.
- **Flow:**
  1. The engine returns the `EntrySignMismatch` on the `NONE` decision.
  2. The runner calls `trade_logger.log_signal_rejection()`.
  3. A journal failure is logged CRITICAL. It is never fatal, because the decision is already a no-trade.
- **Log line:** `action=NONE | reason=ENTRY_SIGN_MISMATCH intended=LONG_SPREAD`. It was `action=NONE | LONG_SPREAD`. `ENTER` prints `direction=…`; every other action prints `reason=…`.

## 6. D-3 — canonical live eligibility

`bot/pairs/canonical_eligibility.check_entry_eligibility(pair, seed, protocol)` returns OK only when all of these hold:

1. A verdict artifact exists at `research_data/canonical_eligibility/<PAIR>.json`, and its provenance sidecar validates as CANONICAL. That means:
   - kind `ENTRY_ELIGIBILITY`;
   - the current methodology and protocol hash;
   - a content data version **equal to the pair's validated seed record's**;
   - a run id;
   - unchanged artifact bytes.
2. The body names this pair; its run id matches the provenance; its `seed_id` matches the seed lineage.
3. It cites at least one evidence item, and **every** item has kind, CANONICAL status, the same methodology, protocol and data version, and a run id. Otherwise the result is "mixed-version" or "unversioned".
4. The verdict is `ELIGIBLE`.

**Blocking rules:**
- Anything else blocks the **new entry**. The gate runs only in the runner's `ENTER` branch; exits never consult it.
- Legacy `cointegration_pairs.csv` / `permutation_verdict.csv` are **not read** by the gate, so they cannot enable an entry. The legacy pre-filter in `bot/pairs/config.py` still decides which pairs run a signal engine at all: legacy evidence can only narrow, never widen.
- **No canonical verdict producer exists yet** (canonical selection is Steps 13–17), so every pair is currently blocked for new entries. This is fail-closed by design.

**Found and fixed while wiring D-3:** a refused `ENTER` previously left the engine holding a phantom trade (pre-existing for health refusals). Under D-3 that would have happened on every signal. `abort_entry()` now clears it, and treats the aborted fill candle like an exit fill, so no same-candle re-decision is possible.

This is provenance control only. It proves nothing about any edge.

## 7. D-4 — canonical / legacy data separation

**Mechanism:** a provenance **sidecar** (`<artifact>.provenance.json`), written by `KalmanFilterEngine.write_canonical_dataset()`. It records:
- kind `KALMAN_STATE`;
- status CANONICAL;
- the methodology version and protocol hash;
- the content data version;
- a deterministic run id;
- the producer;
- the artifact's SHA-256;
- the code commit and dirty flag;
- `extra.seed` (the full seed record), `extra.seed_id` and `extra.data` (the data-version details).

**Gate:** `EntryExitEngine(rule_set="CANONICAL").run()` calls `_validate_canonical_input()` **before reading any data or writing any output**, and raises `CanonicalProvenanceError` unless all of these hold:
- the status is CANONICAL, the kind is right, and the methodology and protocol hash match the engine's protocol;
- a content data version and a run id are present;
- the artifact bytes match the recorded digest;
- the embedded seed passes `validate_seed`: causal, Design A, L, same methodology and hash, a data version present;
- `seed_id` matches the seed;
- the seed's data version equals the dataset's;
- the pair matches;
- after loading, the dataset's first row is the seed candle.

**Labels:** canonical labels (`provenance_status = CANONICAL`, `methodology_version = STAGE2D-REVB-3`, `protocol_hash`, `data_version`, `seed_id`, `seed_design`, `input_run_id`) are written **only** after this validation. Output sidecars (summary, trade log) are CANONICAL only then; otherwise they are UNCERTIFIED or LEGACY.

**`CANONICAL_UNCERTIFIED`** (new, explicit) applies the canonical lifecycle to unprovenanced input, such as hand-built test CSVs or the parity harness's shared θ₀. Its summary says `UNCERTIFIED` with an empty protocol hash. It can never be canonical evidence.

**The audit's adversarial case is now rejected** (`test_the_audit_adversarial_case_is_rejected`):
- the legacy look-ahead-seeded file raises "not CANONICAL";
- the output directory stays empty (no summary emitted);
- copying a genuine canonical sidecar next to it raises "artifact bytes".

## 8. D-5 — canonical data-version fingerprint

`provenance.data_version(frames, timeframe, columns, start, end)`, scheme `QBP-DATAV1`:

**Canonicalisation:**
- Index by candle open time; tz-naive is read as UTC.
- **Row order is normalised** (sorted by timestamp): ordering is not data.
- An identical duplicate timestamp is collapsed; a conflicting duplicate raises.
- Hash the complete grid of `timeframe` over [start, end]. A grid candle with no row hashes as the token `MISSING`.
- One line per (symbol, candle): `symbol|ISO-ts|v1|v2|…`, values as exact `repr()` (round-trip) text, symbols sorted.
- The header `QBP-DATAV1|<timeframe>|<columns>` means close-only and OHLCV fingerprints never collide.

**Persisted:** `data_version`, scheme, timeframe, columns, symbols, start, end, `n_grid_candles`, `n_rows_observed`, `n_missing`.

**Consumed columns:** the Kalman pipeline consumes **only closes**, so `run_canonical` fingerprints exactly that: each leg's close on the aligned grid range. A pipeline that consumes OHLCV must pass OHLCV, and the tests confirm both forms work.

**Where it is recorded:** the seed record, the canonical dataset sidecar, canonical eligibility, rejection rows, and canonical summaries.

## 9. G-1 — research-output provenance

- **Legacy engines** (cointegration scan `cointegration_pairs.csv`, permutation `permutation_verdict.csv`, walk-forward fold reports and leaderboard) now write a **LEGACY** sidecar:
  - methodology `LEGACY-PRE-STAGE2D`, protocol hash empty, data version `NOT_RECORDED`;
  - run id, producer, artifact digest, code commit.
  - It is best-effort: a provenance failure never breaks the legacy run.
- **Files with no sidecar** (every historical file) read as `LEGACY / UNKNOWN`. Nothing is converted and nothing is overwritten.
- **Entry/exit outputs** carry CANONICAL, UNCERTIFIED or LEGACY sidecars (§7).
- **The single validator** `validate_canonical_provenance` rejects every non-canonical status.

## 10. L = 1000 lock

`CanonicalProtocol.init_window_candles = 1000`.

**Documented in code and in the protocol document:**
- It is a **frozen baseline convention** supported by the stability evidence (`STAGE2D_PRE_STEP12_AUDIT.md` §3).
- It is not an optimised trading parameter and not claimed to be mathematically unique.
- It was not chosen from P&L, IC, Sharpe or win rate.
- L = 504 is a development sensitivity check only.
- Changing L changes the protocol hash and requires a methodology-version change.

No sweep was run.

**Tests:**
- the protocol default is 1000;
- a real `run_canonical` under `CANONICAL` produces a 1,000-candle window with the seed at candle 1000;
- a seed validated under L = 1000 fails under L = 504;
- unset L still fails closed.

## 11. Design A seed lock

`CanonicalProtocol.seed_design = "A_PRE_SELECTION"` is the only accepted value (the constructor raises otherwise). Every seed records it, and `validate_seed` enforces it.

`validate_design_a(seed, close_a, close_b, selection_start)` requires:
- a valid record;
- an init window ending before the selection zone (default: the seed candle, where Design A puts it);
- θ₀ **reproduced from the init-window closes alone**.

**Leakage tests:**
- **Positive control:** θ₀ fitted on selection-zone candles, or on the whole span, placed at the start → rejected ("data-version mismatch").
- **Negative control:** the causal seed → accepted.
- An init window that doesn't end before `selection_start` → rejected.

**Documented:** init observations are initialisation data, not evidence. The Kalman state keeps a fading, measured, nonzero dependence on them, and **independence is not claimed.**

## 12. Tests added (`bot/tests/test_stage2d_provenance.py`, 30 tests)

| Area | Tests |
|---|---|
| D-5 | identical → equal; price, volume, timestamp, row removed, symbol, timeframe and column changes → differ; row order normalised (documented); missing counted; conflicting duplicate raises |
| D-4 | canonical dataset accepted with full lineage and a canonical summary sidecar; **the audit adversarial case** rejected with nothing emitted, and a forged sidecar rejected; 10 provenance mutations each rejected (missing, legacy status, methodology, protocol hash, no data version, wrong data fingerprint, wrong seed design, wrong seed lineage, non-causal seed, wrong L); modified dataset bytes; engine/dataset protocol mismatch; `UNCERTIFIED` never canonical |
| L / Design A | L = 1000 and Design A locked with the real baseline run; positive and negative leakage controls; init window after the selection start rejected |
| D-3 | canonical verdict eligible; missing verdict, legacy copy and no seed blocked; missing methodology, mixed-version, legacy item and INELIGIBLE blocked; wrong protocol and wrong data version blocked; tampered verdict bytes blocked |
| G-1 | legacy sidecar written, loaded and preserved, and rejected as canonical; no or corrupt sidecar → LEGACY; the permutation engine writes a LEGACY sidecar |
| D-1 (database) | affected and clean exits persisted and re-read from the database only ("restart"); clean exits unmarked |
| D-2 (database) | rejection persisted, idempotent across a logger "restart", never a `TradeRecord` |
| Runner (offline stubs) | mismatch journaled, logged as `reason=ENTRY_SIGN_MISMATCH intended=LONG_SPREAD`, never executed; ineligible entry refused with no phantom trade; eligible entry then an exit **during reconciliation** passes `reconciliation_affected=True` and the gap; clean exit unmarked |
| Parity consumer | `clean_trade_pairs()` excludes reconciliation-affected and data-incomplete rows |

## 13. Focused test results

- `test_stage2d_provenance`: **30 tests, OK.**
- Lifecycle, seed, both parity suites and Kalman/protocol after the harness updates: **82 tests, OK.**

## 14. Regression test results

Full focused regression: **617 tests, OK, 1 skipped, 0 expected failures** (1,196 s).

It covers the 29 Stage 2D / pairs / research modules of the previous 544-test run, plus `test_stage2d_provenance`, `test_trade_persistence`, `test_new_views` and `test_production_gating`. The new migration was applied to the test database.

The single skip is pre-existing: `test_oos_validator` skips when an uncommitted ad-hoc cache is absent.

Not run, deliberately: `bot/core/dry_run_test.py` and the adapter tests that call live public exchange endpoints (the no-exchange constraint).

## 15. Failures

**None** in the final runs.

During development, two test-fixture or harness adjustments were needed, both for intended reasons:
- The hand-built-CSV lifecycle tests and both parity harnesses moved to `rule_set="CANONICAL_UNCERTIFIED"`. D-4 now correctly refuses their unprovenanced input under `CANONICAL`, and their assertions are unchanged.
- The protocol and seed tests were updated for L = 1000 and REVB-3. Unset L still fails closed, and L = 504 now gives a different hash.

One self-introduced syntax slip, a G-1 import placed above `from __future__` in `cointegration_engine.py`, was caught before any test ran and corrected; the diff is now 2 lines.

## 16. Remaining risks

1. **Migration `0012` is not applied to the operational database.** Tests apply it to the test database only. **`python manage.py migrate` must run before the pairs bot is restarted**, or journal writes of the new fields will fail. I didn't run it: it would alter the live database while the bot may be running old code.
2. **No canonical eligibility producer exists.** Every pair is blocked for new live entries until canonical selection exists (Steps 13–17). This is intended fail-closed behaviour.
3. **Unchanged code paths:** walk-forward, permutation and the research lab still run the LEGACY rule set and emit LEGACY-labelled outputs. Migrating them is Steps 13–17.
4. **Live data version:** live doesn't recompute the research content fingerprint (exchange data may differ in range). It verifies data identity through θ₀ reproduction from exchange closes at startup, which is a partial check.
5. **Trust boundary:** sidecars are integrity checks, not signatures. Someone with write access could forge both artifact and sidecar consistently. The guarantee is against accidental mislabelling and stale or legacy reuse, not against deliberate forgery.
6. The `UNCERTIFIED` mode exists by design. Any consumer that wants evidence must require `provenance_status == CANONICAL`, as eligibility does.

## 17. Exact methodology / provenance fields

- **`OutputProvenance` (sidecar):** `kind`, `status` (CANONICAL | LEGACY | UNCERTIFIED), `methodology_version`, `protocol_hash`, `data_version`, `run_id`, `producer`, `artifact_sha256`, `code_commit`, `code_dirty`, `extra`.
- **`KalmanSeed`:** `pair_name`, `seed_timestamp`, `beta0`, `alpha0`, `methodology_version`, `protocol_hash`, `init_window_start`, `init_window_end`, `init_method`, `n_init_observed`, `data_first_timestamp`, `data_last_timestamp`, `n_grid_candles`, `n_observed_candles`, `seed_design`, `data_version`.
- **`DataVersion`:** `data_version`, `scheme`, `timeframe`, `columns`, `symbols`, `start`, `end`, `n_grid_candles`, `n_rows_observed`, `n_missing`.
- **Canonical summary:** `provenance_status`, `methodology_version`, `protocol_hash`, `data_version`, `seed_id`, `seed_design`, `input_run_id`, `rule_set`.
- **`TradeRecord` (database):** `reconciliation_affected`, `reconciliation_gaps`.
- **`SignalRejection` (database):** `pair_name`, `venue`, `decision_timestamp`, `intended_direction`, `zscore`, `held_spread`, `alpha_e`, `beta_e`, `price_a`, `price_b`, `reason`, `methodology_version`, `protocol_hash`, `data_version`, `created_at`.

## 18. Activity confirmation

- No exchange call: every test uses offline stubs or synthetic data.
- No live trading, no live restart.
- No 150-symbol scan; no research zone, holdout or FX work.
- No parameter optimisation; no P&L, sizing or capital change.
- Nothing committed. The operational database was not migrated.

## 19. DCA confirmation

`bot/research/dca_engine.py` was **not modified** (`git diff` shows 0 lines). `bot/pairs/pnl.py` was also not modified.

## 20. Final status

# READY_FOR_STEP_12

The pre-Step-12 audit's prerequisites (§16) were D-1, D-2 and D-4 fixed and tested, including a governance test that a legacy-lineage Kalman input is refused under `rule_set="CANONICAL"`. All three are done and green. D-3, D-5, G-1, the L = 1000 lock and the Design A lock are also done.

**Operational precondition (not a Step 12 code prerequisite):** apply migration `0012` (`python manage.py migrate`) before the pairs bot is next restarted.

**Still required before Steps 13–20** (unchanged by this task):
- the research zones (S, V, WF, F, L_H);
- a canonical selection/eligibility producer (until it exists, every pair stays blocked for new live entries);
- the FX calendar and FX g_max;
- the reconciliation/backfill stage.
