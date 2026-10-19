# Trading Decision Journal: Phase 1 Implementation Report

**Quant Bot Pro · 2026-10-07 · Scope: backtester-only recorder (owner authorization + sign-off of 2026-10-07)**
Design: `TRADING_DECISION_JOURNAL_DESIGN_REVB_2026-10-07.md` (current; Amendment 1 appended). Owner decisions,
verbatim: `TRADING_DECISION_JOURNAL_PHASE1_OWNER_SIGNOFF_2026-10-07.md` (with the execution-mode amendment).
The journal is research infrastructure. **Nothing here is evidence of a trading edge.**

## 1. Implementation status
Phase 1 is implemented for the existing single-symbol backtester, opt-in and off by default.

The stop points hit before coding were all resolved by owner decisions, none by assumption:
- OD-J13 unsigned (resolved by D1);
- what counts as a "candidate" (D2);
- gate-vector semantics (D3);
- 11 ambiguous raw reasons (D4);
- the missing final-candle outcome (D5);
- TradeRecord not applying to backtests (D6);
- environment / execution-mode overlap (D7, plus the RESEARCH amendment);
- coverage storage (D8).

## 2. Files changed
| File | Change |
|---|---|
| `bot/journal/taxonomy.py` | **new**: environments, terminal statuses, lifecycle stages, transition table, reason codes ↔ (status, stage), exact raw-reason mapping (D4), versioned gate order (D3), coverage categories (D8) |
| `bot/journal/decision_recorder.py` | **new**: `BacktestDecisionRecorder` (observer) |
| `bot/journal/decision_export.py` | **new**: CSV.gz + provenance-sidecar export and reader (export only, OD-J5) |
| `bot/journal/models.py` (CRLF preserved) | **added**: `JournalRecordIsImmutableError`, insert-only base/queryset, `JournalRun`, `DecisionEvent`, `MarketSnapshot`, `JournalGap`, and `TradeRecord.decision_id` (nullable). No existing field changed |
| `bot/migrations/0016_decision_journal_phase1.py` | **new**: additive migration |
| `bot/backtesting/backtester.py` | `journal_recorder=None` parameter (also on `backtest()`), `_journal()` hook caller, observation hooks, three `_last_fill_refusal` tags, `BACKTEST_LOOP_START = 100` (replaces two literal `100`s; same value). No logic changed |
| `bot/tests/test_decision_journal_phase1.py` | **new**: 35 tests |
| `config/settings.py` | **unrelated, user-requested:** hotspot IP `172.20.10.6` added to `ALLOWED_HOSTS` |
| Docs | this report; the sign-off record; Revision B Amendment 1 |

**Not touched:** strategies, router, live loop, `bot_runner`, execution engine, pairs, DCA, FX, holdout rules, P&L
code, and Revision A.

## 3. Migration `0016`
- **Additive only:** `AddField traderecord.decision_id (null=True)`, plus `CreateModel` ×4 and `AddConstraint` ×7.
- A test asserts this: only AddField / CreateModel / AddConstraint, and the single AddField is nullable.
- `makemigrations --check` reports no drift.
- **Not applied to your local database.** Run `python manage.py migrate` when ready; until then, pages that read
  `TradeRecord` will fail on the missing column.
- No legacy backfill, and no historical decision is fabricated.

## 4. Lifecycle implemented
- **Stages:** EVALUATED → SIGNAL_GENERATED → GATES_EVALUATED → EXECUTION_ATTEMPTED.
- **Allowed terminal outcomes per last stage reached:**

| Last stage | Allowed terminal outcomes |
|---|---|
| EVALUATED | NO_SIGNAL, REGIME_REJECTED, DATA_REJECTED, SYSTEM_REJECTED |
| SIGNAL_GENERATED | CENSORED_END_OF_DATA |
| GATES_EVALUATED | REJECTED, SYSTEM_REJECTED, other rejections |
| EXECUTION_ATTEMPTED | EXECUTED, EXECUTION_FAILED |

- **Enforcement:** `taxonomy.validate_terminal` at creation and again before persisting. DB checks enforce:
  - a terminal status;
  - information_time > candle open;
  - a trade link if and only if EXECUTED;
  - one decision per (run, symbol, candle);
  - one decision per backtest trade.
- **Evaluation points (OD-J13, backtester half, D1):** only the candles the backtester actually evaluates, i.e.
  flat, no entry pending, not the exit candle, index ≥ 100.
- **Never decisions (run-level coverage only, D8):** warm-up, in-position, exit-candle and pre-loop candles.
  - Coverage holds counts per category, run-length intervals that reproduce the per-candle category sequence
    exactly, missing grid ranges, and rows dropped by the backtester's data preparation.
- **Initial warm-up (D4 #9/#12), implementation definition:** a warm-up-class result counts as WARMUP only until
  that strategy (moving average or mean reversion) first returns a non-warm-up result in the run. After that,
  the same raw reason is DATA_REJECTED / DATA_QUALITY_FAILURE.
- **Candidate (D2 option A):** only a BUY/SELL.
  - Router direction conflicts and the RANGING-branch direction toggles are post-signal (REJECTED or
    SYSTEM_REJECTED, with a direction).
  - Fill-time refusals are post-signal.
  - A signal with no next open is CENSORED_END_OF_DATA (D5), never EXECUTION_FAILED.
- **Trade link (D6):** `backtest_trade_id = "<run_id>:T000001"`, sequential. Fill facts stay in the backtest
  result; the recorder verifies the k-th EXECUTED decision against the k-th trade's `entry_index`.
- **Episodes (OD-J14 open):** not implemented. Only atomic decisions are stored.

## 5. Reason taxonomy (`QBP-JRNL-REASON-1`)
- Implements the owner's D4 table exactly. Prefixed raw reasons keep their parameter in structured detail
  (confidence, verdict, structure label + required trend).
- `longs_disabled` / `shorts_disabled` are pre-signal on trend paths and post-signal on the ranging path, as in the
  router's code.
- Any other string, or a string on a path that cannot emit it, raises `UnmappedReason`. The result is a
  MAPPING_FAILURE gap with the raw reason preserved, the run marked INCOMPLETE, and no decision row. This covers
  the router's `unhandled_regime_{regime}` fallback.
- **Gate order `QBP-JRNL-GATES-BT-1`:**
  - router → moving average (9 checks) / mean reversion (9 checks) → post-signal router checks → well-formed
    signal → 3 fill checks;
  - PASS only where control flow demonstrably passed, FAIL at the refusing check, NOT_EVALUATED otherwise;
  - the confidence check is NOT_EVALUATED because the backtester disables it;
  - post-signal checks are NOT_EVALUATED when no candidate exists;
  - the order is pinned to the source by a test.
- **Environment (OD-J15):** BACKTEST. **Execution mode:** RESEARCH (owner amendment).

## 6. Provenance
`JournalRun` reuses `bot.quant_core.provenance`:
- commit, dirty flag and digest;
- `config_hash` over the backtester, router, sizer and strategy scalars;
- QBP-DATAV1 `data_version` of the consumed OHLCV;
- methodology `NON-CANONICAL-RESEARCH-ENGINE`, with protocol hash "" (not a canonical protocol);
- zones version and hash, market-profile hash;
- taxonomy, schema, gate-order, feature-set and coverage versions;
- evidence status "DESCRIPTIVE — non-canonical engine, never evidence".

Decisions copy the run identity. Each snapshot carries the SHA-256 of the exact candle window the strategy saw.
The export sidecar is written by the existing `write_research_output_provenance`.

**Limitation:** the strategies have no version constants, so `strategy_version` = `UNVERSIONED@<commit>[+dirty]`.

## 7. Backtester integration
- **Hooks:**
  - `on_run_start` / `on_empty_run`;
  - `on_fill` (after the next-open fill attempt);
  - `on_not_evaluated` (exit candle; in position);
  - `on_evaluation` (at each of the backtester's four existing branches, with the disposition that branch
    decided: INVALID_OUTPUT, NO_SIGNAL, INVALID_SIGNAL_VALUE, CANDIDATE);
  - `on_censored_end_of_data`;
  - `close`.
- `Backtester._journal()` swallows and logs recorder errors and hands them to the recorder. A failure makes the run
  INCOMPLETE with a RECORDER_FAILURE gap; the backtest result is unchanged.
- The recorder reads the backtester's state but never writes it. It buffers in memory and bulk-inserts at close
  inside one transaction.

## 8. Recorder OFF vs ON equivalence
On real data (`data/BTC_USDT_1h.csv`, last 2,000 candles), the full result dict (JSON, sorted keys) and the
backtester state are **identical** with the recorder on and off. Backtester state compared: rejection counters,
signals seen, balance, unfilled signals, equity curve, mark-to-market points, max drawdown, fees.

Cases checked:
- allow_unvalidated = True, with real fills;
- allow_unvalidated = False (governance block);
- a forced zero-size fill refusal;
- an injected unmapped reason;
- a recorder that raises mid-run.

**Negative control:** a recorder that moves a candidate's stop is detected (the results differ).

## 9. Tests
- `bot.tests.test_decision_journal_phase1`: **35 tests, all pass** (PostgreSQL test database, offline under the
  netguard, no network attempts).
- **One real defect was found by the tests and fixed:** an insert-only row constructed with an existing primary key
  would have been silently UPDATED (Django tries an UPDATE first). Insert-only models now force INSERT, so a
  duplicate raises IntegrityError.
- **Coverage of your test list:**
  - items 1–14: all covered;
  - original items 1–23: all covered, including export round trip (23) and run INCOMPLETE behaviour (22).
- **Independent oracles:**
  - the backtester's own counters equal the decision + warm-up denominator, per raw reason;
  - your D4 table is re-typed in the test file;
  - truncation invariance: over 1,000 decisions are identical when the future 400 candles are removed.
- **Related regression:** 25 modules touching the backtester, journal, trade logger and execution engine.
  - **349 tests: 343 pass, 6 fail. All 6 are pre-existing**, with the same assertion and value as in the original
    baseline and in every full run before this change.
  - 4 need the live network, which the netguard refuses (logged: 4 attempts to `api.binance.com` /
    `api.kraken.com`): three `test_bot_runner_venue_sessions` tests (`'network_error' != 'ok'`) and
    `test_execution_engine_adapter_wiring.test_exit_updates_trade_record`.
  - `test_research_lab_tools…test_real_1h_crypto_result_is_byte_identical` returns 0.336 against a stale golden
    value 0.3465; unchanged since the 10-04 runs.
  - `test_forex_terminal_data…test_universe_is_currency_balanced_not_usd_dominated` is the known baseline failure.
  - None is caused by the journal changes.

## 10. Mutation / negative tests: 15 of 15 caught (files restored byte-identical)
| # | Defect planted | Caught by |
|---|---|---|
| J1 | Future leakage: the snapshot reads candle i+1 | causal snapshot test |
| J2 / J3 | Wrong decision timestamp (+2 h; = open) | causal test / DB check → INCOMPLETE |
| J4 | Duplicate (force_insert removed) | duplicate test |
| J5 / J6 / J7 | Incorrect mapping; governance filed as NO_SIGNAL; unknown silently NO_SIGNAL | D4 table / unknown-reason tests |
| J8 / J9 | Altered fill timing (recorder link; engine entry index) | link test / recorder refuses → INCOMPLETE |
| J10 | Recorder-induced strategy mutation | ON/OFF equivalence |
| J11 | Warm-up boundary moved to 99 | boundary test |
| J12 | An in-position candle recorded as a decision | non-evaluation test |
| J13 | Gate vector claims PASS after the failure | gate-vector test |
| J14 | Censored filed as EXECUTION_FAILED | censoring test (transition table refuses it) |
| J15 | Recorder failure swallowed (run CLOSED) | recorder-failure test |

## 11. Five-gate audit (Phase 1 scope only)
| Gate | Status | Evidence / limits |
|---|---|---|
| G1 Software | **PASS** | 35/35; 15/15 mutations; transition table and DB constraints; insert-only, with a real defect found and fixed; migration additive and in sync. Limit: tested on PostgreSQL only (SQLite is not the configured engine) |
| G2 Maths | **PARTIAL** | No P&L math changed (proven by equivalence). Coverage arithmetic and the denominator are checked against independent counters. `planned_risk_per_unit` / `planned_reward_risk` have no dedicated known-answer test |
| G3 Statistics | **N/A** | Phase 1 makes no statistical claim and produces no inference |
| G4 Quant discipline | **PASS** | Information time = candle close; next-open fill link verified; D5 censoring; truncation invariance (no look-ahead); costs only referenced from the single source; financing and liquidity recorded honestly as "not modelled" |
| G5 Integrity | **PARTIAL** | Full run provenance (commit, dirty digest, config, data version recomputed and equal, zones, profile, versions); window hashes; export sidecar round trip. Limits: strategies are unversioned (`UNVERSIONED@commit`); `run_id` includes the start time, so decision ids are deterministic within a run but not across re-runs |

No critical FAIL.

## 12. Remaining risks
- The recorder reads backtester attribute names. A rename makes the run INCOMPLETE (loud), not wrong.
- **Volume:** about one decision per evaluated candle (≈ 40k per 5-year 1 h symbol run) plus snapshots. Fine for
  occasional research runs; not intended for dashboard backtests (the recorder is off by default).
- The per-strategy warm-up rule is my implementation of "genuine initial warm-up". It is documented and tested,
  but it is a definition.
- The source-order audit is regex-based and will flag, not silently pass, unusual new syntax.
- The local database is not migrated.

## 13–15. Open items
- **OD-J13:** the backtester half is approved and implemented. The live-loop half is OPEN. Pairs and DCA are
  deferred.
- **OD-J14:** OPEN. No episode logic exists; decisions are atomic.
- **V-1:** OPEN. Not resolved. No zone V or holdout claim has been made.

## 16. Explicitly NOT implemented
- live / DRY_RUN / PAPER wiring;
- order execution changes;
- FX, DCA and pairs journals;
- `DecisionLabel` / counterfactuals (Phase 2);
- `PostTradeMetrics`;
- human annotations;
- episodes;
- ML / scikit-learn / NLP / screenshots / order-book models;
- expected-edge gating;
- any strategy, parameter or filter change;
- confirmation runs;
- holdout evaluation;
- dashboard changes;
- research sweeps;
- legacy backfill;
- REPLAY environment.

**Commit status:** nothing is committed yet.
