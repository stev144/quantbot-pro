# Trading Decision Journal: Architecture Proposal

**Quant Bot Pro / Steph Quant Technologies · 2026-10-07 · Status: DESIGN FOR REVIEW. No code written.**
Branch at time of audit: `stage2d-revb` @ `e69d166`. METHODOLOGY `STAGE2D-REVB-4`, ladder `QBP-LADDER-D7-1`,
registry `QBP-HYP-D3-1`, zones `QBP-ZONES-1`.

> **Five-Gate status of this whole proposal: G1–G5 NOT EVALUATED.** Nothing is implemented. Each component below
> states which gates apply and what evidence will be required. Nothing here changes live trading, DRY_RUN,
> any strategy, or any existing result.

---

## 0. Executive summary

1. **What exists today is trade-centric.** `TradeRecord` is the journal. The single-symbol live engine
   (`ExecutionEngine.execute_signal`) refuses trades through **six silent `return`s that are only logged**:
   - position already open;
   - OPEN DB row;
   - unreconciled positions;
   - balance ≤ 0;
   - drawdown kill-switch;
   - quantity ≤ 0.

   `bot_runner` also discards `NO_SIGNAL`, `HIGH_VOLATILITY`, price-deviation and no-closed-candle outcomes
   without recording them. The **backtester** records only executed trades. Every "no trade" is lost, and
   with it the denominator.
2. **Almost all the governance machinery the journal needs already exists**, and the design reuses it rather
   than duplicating it:

   | Requirement | Existing component |
   |---|---|
   | Evidence classification | `quant_core/verdict_ladder.py` (OBSERVATION → CANONICAL, D-7) |
   | Family registration and multiple testing | `quant_core/hypothesis_registry.py` (D-3) |
   | Provenance | `quant_core/provenance.py` (`code_state`, `config_hash`, `data_version`, `OutputProvenance`, sidecars), `research_lab/data_fingerprint.py` |
   | Causal label timing | `quant_core/timing.py` (D-1: signal at close *t*, fill at open *t+1*) |
   | Zones and sealed holdout | `quant_core/research_zones.py` (S / V / F, ledger) |
   | Calendars and universes | `quant_core/market_profile.py` |
   | Costs | `config/execution_costs.py` (venue table), `quant_core/costs.py` (fail-closed `COST_UNAVAILABLE`), `quant_core/funding.py` (borrow / `BORROW_UNAVAILABLE`) |
   | Dependence-aware inference | `quant_core/inference.py` (block permutation, HAC, D-10 block lengths) |
   | OOS folds with purge | `research/oos_validator.py` |
   | Closed-candle discipline | `bot_runner.drop_forming_candles` |
   | Append-only records | `research_lab.models` immutability pattern |
   | Existing rejection and incident journals | `SignalRejection`, `ExecutionIncident` |

3. **What is genuinely missing:**
   - a decision record;
   - a point-in-time feature snapshot;
   - a controlled reason taxonomy;
   - a label/counterfactual store kept physically separate from P&L;
   - post-trade metrics (MAE/MFE, execution quality);
   - human annotations;
   - a journal-run provenance header.
4. **Governance consequence: live journal data is sealed holdout.** Every live/dry-run decision is dated
   2026-09-25 or later. That falls in UNASSIGNED_EXPOSED (never evidence) or in the sealed holdout F
   (from 2026-10-04). Live-journal *labels* and *pattern research* are therefore sealed until the D-8
   ledgered evaluation (2027-04-04 at the earliest). Research volume must come from **backtest decision streams**
   in zones S and V. Live rows are operational records until F is opened (owner decision OD-J4).
5. **12 owner decisions** must be made before implementation (§20).

---

## 1. Current architecture audit (verified in source, 2026-10-07)

| Area | What the code does today | Journal-relevant gap |
|---|---|---|
| Live decision loop | `bot/core/bot_runner.py`: closed candles (`drop_forming_candles`) → cross-venue price check → `RegimeDetector.detect` → `StrategyRouter.route` → `execute_signal` → `manage_positions`. `DRY_RUN = True`. | Non-trade outcomes only go to the log file. There is no decision ID, and the regime and indicator values at decision time are not persisted (except `rsi_at_entry` on executed trades). |
| Execution guards | `ExecutionEngine.execute_signal`: 6 early `return`s, see §0.1. | Rejections are invisible to research. Guards short-circuit, so later gates are never evaluated (order-dependent attribution). |
| Signal quality filter | `engines/trade_filter.trade_quality_filter` (via `signal_engine.py`): free-text reasons (`rr_ratio_too_low: 1.20`, `risk_too_small`, …). | Free-text reasons, not a controlled vocabulary. |
| Executed trades | `journal.models.TradeRecord`: entry/exit, fees, slippage, `risk_amount`, `r_multiple`, `holding_candles`, narratives, `venue`, pairs and DCA linkage, `fill_model`, `borrow_cost_status`, `execution_mode` (LIVE / DRY_RUN / UNKNOWN), `methodology_version`, `protocol_hash`, `data_version`. | No decision link. Planned, observed and post-trade values share one row. No MAE/MFE (not computed anywhere for single-symbol trades; the pairs engine tracks only a z-score "MFE"). |
| Pairs rejections | `journal.models.SignalRejection` (ENTRY_SIGN_MISMATCH only, with z, held spread, α/β, prices, methodology, protocol hash, data version). Written by `pairs/trade_logger.log_signal_rejection`. | A good prototype of the decision record, but pairs-only, one reason, no feature snapshot. **Keep and generalise; do not replace.** |
| Execution failures | `journal.models.ExecutionIncident` (pairs leg failures, manual-review flag, resolution, `execution_mode`). | Pairs-only. Maps cleanly to `EXECUTION_FAILED`. |
| Backtester | `backtesting/backtester.py` runs the same regime → router → strategy pipeline and returns a trade list. | Non-trades are discarded. This is the main future source of research-grade decision volume. |
| Research engines | `entry_exit_engine` (pairs lifecycle), `dca_engine` (cycles, censoring), cointegration, cross-section, contagion. | Each has its own trade/cycle dict. No shared decision schema. |
| Evidence | `verdict_ladder.ladder_level(LadderEvidence)`; `research_lab.verdict`; `validated_feature_registry`. | Reuse as-is. |
| Registry | `HypothesisRegistry`: ≤ 5 primaries per program-quarter, exploration BH q = 0.10 on S, confirmation Holm on V, append-only. | Reuse; journal research becomes a *program*. Endpoints are fixed to `NET_MEAN_RETURN_PER_TRADE` / `IC`; see OD-J11. |
| Provenance | `code_state()` (commit, dirty, dirty_digest), `config_hash`, `data_version` (QBP-DATAV1), `OutputProvenance` sidecars, `input_files_fingerprint`. | Reuse. A journal run needs one header row so that per-decision rows stay small. |
| Timing | `timing.RESEARCH_LABEL_CONTRACT` (d = 1, open); `forward_label()`. | Reuse for every journal label. |
| Zones | `ZONES` (S, embargo, V, unassigned, sealed F), `seal_before_write`, `open_holdout_evaluation` ledger. | Labels must respect the seal (§9.4). |
| Market profile | `MarketProfile` (calendar, g_max, seed rule, horizon basis, financing, cost model, universe, `profile_hash`); FX horizon basis still undecided. | Labels on FX are blocked until OD-FX-4 (horizon basis). |
| Storage | Postgres via `DATABASE_URL` on Railway; SQLite locally. **No pyarrow, no scikit-learn** in the venv; statsmodels present. | Bulk decision streams cannot go in Postgres; see OD-J5 (storage) and OD-J10 (ML libraries). |
| Dead code | `bot/core/trade.py` duplicate `Trade`. | The journal must import `Trade` only from `bot.journal.models`. |

---

## 2. Existing components to reuse (no second system of any kind)

| Need | Reuse | Rule |
|---|---|---|
| Provenance | `provenance.code_state`, `config_hash`, `data_version`, `make_run_id`, `OutputProvenance`, `write_provenance` | No new provenance fields outside a `JournalRun` header that *stores* these values. |
| Evidence state | `verdict_ladder.LEVELS` / `ladder_level` | No new taxonomy. Journal findings get a ladder level like any other result. |
| Multiple testing / families | `HypothesisRegistry` | Program `"decision_journal"`, one family per quarter. |
| Dependence-aware tests | `inference.block_permutation_p`, `confirmatory_ic_test`, `d10_block_length` | No new bootstrap implementation. A block *bootstrap* for CIs, if needed, goes into `inference.py`. |
| OOS | `oos_validator.build_folds` (purge, embargo), `evaluate_feature_oos` | No second fold builder. |
| Label timing | `timing.forward_label`, `RESEARCH_LABEL_CONTRACT` | d = 1 for every label; d = 2 is a sensitivity check only. |
| Zones / seal | `ZONES`, `assert_no_sealed_data`, ledger | Labels never read F before the ledgered evaluation. |
| Calendars / gaps | `MarketProfile.calendar`, `gap_max_candles` | Label windows crossing an over-limit gap are CENSORED / UNTESTABLE. |
| Costs | `get_venue_execution_costs`, `costs.CostModel`, `funding.funding_cost` | Unknown cost → `COST_UNAVAILABLE`, never 0. |
| P&L / R | `TradeRecord` fields; `simulation.py` | Real P&L stays only in `TradeRecord`. |
| Regime | `RegimeResult` (every field always populated) | Snapshot stores it verbatim, versioned. |
| Immutability | `ResearchRecordIsImmutableError` pattern | Journal rows are insert-only; corrections supersede. |
| Health | `deep_health_check` | Add a journal layer to it later; no separate checker. |

---

## 3. Missing components

1. `JournalRun`: the provenance header per bot session or backtest run.
2. `DecisionEvent`: one row per meaningful decision point, executed or not.
3. `ReasonTaxonomy`: a versioned, controlled vocabulary (code module, not DB-editable).
4. `MarketSnapshot`: point-in-time features with per-feature availability proof.
5. `DecisionLabel`: forward-return and counterfactual labels, physically separate from P&L.
6. `PostTradeMetrics`: MAE/MFE, execution quality and holding time for real trades (derived, versioned).
7. `HumanAnnotation`: notes, plans, reviews, tags, emotions, attachments; never a model feature.
8. `JournalGap`: a coverage record for any period when the recorder failed.
9. `DecisionRecorder`: the single writer used by the backtester and (later, owner-approved) the live loop.
10. `Labeler`: an offline job that fills `DecisionLabel` under the timing, seal, calendar and cost rules.
11. Journal analytics / ML discovery: research code that only *reads*, writes outputs with sidecars, and
    registers hypotheses.

---

## 4. Proposed journal domain model

```
JournalRun 1──* DecisionEvent 1──1 MarketSnapshot
                     │
                     ├──0..1 TradeRecord   (existing; new nullable decision_id; EXECUTED decisions only)
                     │           └──0..1 PostTradeMetrics   (derived, versioned)
                     ├──* DecisionLabel    (FORWARD_RETURN | COUNTERFACTUAL_RULE; status-bearing; versioned)
                     ├──0..1 SignalRejection   (existing, pairs; linked, not replaced)
                     ├──0..1 ExecutionIncident (existing; linked)
                     └──* HumanAnnotation  (also attachable to TradeRecord)
JournalRun 1──* JournalGap
```

**Type separation (hard rule, G1/G5).** Real outcomes live only in `TradeRecord` and `PostTradeMetrics`.
Hypothetical outcomes live only in `DecisionLabel`. There is no foreign key from `DecisionLabel` to
`TradeRecord`. Real-P&L aggregators (`TradeAnalytics`, dashboard, `live_api`) may not import the label
module; this is enforced by an import-boundary test. Every `DecisionLabel` row carries the constant
`outcome_kind = "COUNTERFACTUAL_NOT_EXECUTED"` or `"FORWARD_RETURN_NOT_EXECUTED"`.

### 4.1 Decision status (the 14 required states; one documented addition proposed)

| `decision_status` | Meaning | Origin class (for §8) |
|---|---|---|
| `EXECUTED` | An order was filled and a `TradeRecord` exists | — |
| `SIGNAL_GENERATED` | Transient: a signal exists and gates are not yet evaluated. **Never a final state**; a run that ends with rows in this state is a recorder defect | — |
| `NO_SIGNAL` | The strategy evaluated and produced no entry | STRATEGY |
| `REJECTED` | A strategy-level rule rejected a generated signal (e.g. the quality filter, ENTRY_SIGN_MISMATCH) | STRATEGY |
| `REGIME_REJECTED` | The router does not trade this regime (`HIGH_VOLATILITY`) | STRATEGY |
| `COST_REJECTED` | Estimated cost too high, or cost unknown (fail-closed) | STRATEGY |
| `LIQUIDITY_REJECTED` | Spread or depth outside limits | STRATEGY |
| `TIMING_REJECTED` | Outside an allowed window, or the signal is stale | STRATEGY |
| `RISK_REJECTED` | Kill-switch, size below minimum, risk cap | RISK |
| `PORTFOLIO_REJECTED` | Position or exposure capacity reached | PORTFOLIO_CAPACITY |
| `BORROW_REJECTED` | Borrow or funding unavailable or unacceptable | STRATEGY (cost-side) |
| `DATA_REJECTED` | Data quality: no closed candle, gap, price deviation, balance unavailable | DATA |
| `DUPLICATE_REJECTED` | Same symbol/direction already open, or the signal already acted on | PORTFOLIO_CAPACITY |
| `EXECUTION_FAILED` | Order attempted, not (fully) filled; links to `ExecutionIncident` | EXECUTION |
| *proposed* `SYSTEM_REJECTED` | Operational safety gate: **positions not reconciled** at startup | SYSTEM |

**Why `SYSTEM_REJECTED` is proposed (OD-J1).** The reconciliation gate is neither a market-data defect nor a
risk-policy decision. Filing it under `RISK_REJECTED` would contaminate the question "which rejection
reasons save money?" with periods when the bot simply could not know its own state. If the owner declines,
the fallback is `DATA_REJECTED` / `POSITION_STATE_UNKNOWN`, recorded as a documented compromise.

`manual` decisions: this bot has no manual veto path today. A `MANUAL` origin is reserved in the taxonomy
but **not built** (§19).

### 4.2 Reason taxonomy (`bot/journal/taxonomy.py`, versioned `QBP-JRNL-REASON-1`)

Two levels: each **reason code** is a controlled enum mapped to **exactly one** allowed `decision_status` and
one `stage`. An optional machine `reason_detail` holds the numbers, and an optional human explanation lives
only in `HumanAnnotation`.

| Stage | Reason codes (initial set, each traced to existing code) | Status |
|---|---|---|
| DATA | `NO_CLOSED_CANDLE` (bot_runner), `CROSS_VENUE_PRICE_DEVIATION` (price_validator), `BALANCE_UNAVAILABLE` (balance ≤ 0), `DATA_GAP_EXCEEDS_GMAX`, `DATA_QUALITY_FAILURE` | DATA_REJECTED |
| SYSTEM | `POSITIONS_NOT_RECONCILED` | SYSTEM_REJECTED (OD-J1) |
| REGIME | `WRONG_REGIME` (HIGH_VOLATILITY routing) | REGIME_REJECTED |
| STRATEGY | `NO_ENTRY_CONDITION`, `ADX_TOO_LOW`, `STRATEGY_RULE_FAILURE`, `ENTRY_SIGN_MISMATCH` (lifecycle.py) | NO_SIGNAL / REJECTED |
| FILTER | `RR_RATIO_TOO_LOW`, `RISK_TOO_SMALL`, `MISSING_STOP`, `MISSING_TARGET`, `INVALID_SIGNAL` (trade_filter) | REJECTED |
| EDGE | `EXPECTED_EDGE_TOO_LOW` (*reserved*: no strategy has a canonical edge estimate today) | REJECTED |
| COST | `COST_TOO_HIGH`, `COST_UNAVAILABLE` | COST_REJECTED |
| LIQUIDITY | `SPREAD_TOO_WIDE`, `LIQUIDITY_TOO_LOW` | LIQUIDITY_REJECTED |
| FINANCING | `BORROW_UNAVAILABLE`, `FUNDING_UNACCEPTABLE` | BORROW_REJECTED |
| TIMING | `TIMING_WINDOW_EXPIRED` | TIMING_REJECTED |
| RISK | `DRAWDOWN_KILL_SWITCH`, `SIZE_BELOW_MINIMUM` | RISK_REJECTED |
| PORTFOLIO | `MAX_EXPOSURE_REACHED`, `POSITION_ALREADY_OPEN` | PORTFOLIO_REJECTED |
| DUPLICATE | `DUPLICATE_SIGNAL`, `OPEN_TRADE_ROW_EXISTS` | DUPLICATE_REJECTED |
| EXECUTION | `ORDER_STILL_OPEN`, `ORDER_UNCONFIRMED`, `RATE_LIMITED`, `LEG_FAILURE` | EXECUTION_FAILED |

A new code requires a taxonomy-version bump. Mixing versions in one dataset is refused.

### 4.3 Gate vector (order-independent attribution)

Today's guards short-circuit, so a decision that fails gate 2 never shows whether it would also have failed
gate 5. Attributing "money saved" to the first failing gate is then an artefact of code order. Proposal:
every `DecisionEvent` stores a **gate vector**, `{gate: PASS | FAIL | NOT_EVALUATED}` for every gate whose
evaluation is side-effect-free.
- `decision_status` remains the *first* failure in a fixed, documented order.
- Analyses use the vector.
- In the live engine this requires extracting a pure evaluator; that is an owner decision (OD-J7) and is
  **not** part of Phase 1.

---

## 5. Database / schema design

Postgres (Railway) holds **live and dry-run** journal rows. **Backtest and research** decision streams are
files plus provenance sidecars (OD-J5), because a 150-symbol × 40k-candle backtest yields about 6M decisions.

| Table | Key fields | Constraints |
|---|---|---|
| `JournalRun` | `run_id` (= `make_run_id`), `source` {LIVE, DRY_RUN, BACKTEST, RESEARCH}, `started_at`, `ended_at`, `code_commit`, `code_dirty`, `dirty_digest`, `methodology_version`, `protocol_hash`, `config_hash` (over a **redacted** config), `zones_hash`, `market_profile_hash`, `universe_id`, `strategy`, `strategy_version`, `venue`, `account_label`, `data_version` (backtests), `feature_set_version`, `taxonomy_version`, `status` {OPEN, CLOSED, INCOMPLETE} | Insert, then close once; immutable after CLOSED |
| `DecisionEvent` | `decision_id` (UUID), `run`, `candle_open_time`, `information_time` (= candle close, UTC), `symbol`, `asset_class`, `venue`, `strategy`, `strategy_version`, `candidate_direction` {LONG, SHORT, null}, `decision_status`, `reason_code`, `reason_stage`, `reason_detail` (JSON), `rejection_origin`, `gate_vector` (JSON), planned `entry` / `sl` / `tp` / `planned_risk` / `expected_r` / `estimated_cost` (cost-model output + status), `expected_edge` (null + reason unless a validated model supplies it; **none does today**), `risk_state` / `portfolio_state` / `liquidity_state` / `financing_state` / `data_quality_state` (JSON), `execution_eligible`, `execution_mode`, `research_zone` (computed from `information_time`), `supersedes` (FK, nullable), `recorded_at` | Unique (`run`, `symbol`, `strategy`, `information_time`); insert-only; `SIGNAL_GENERATED` forbidden at run close |
| `MarketSnapshot` | `decision` (1:1), `feature_set_version`, `as_of` (= `information_time`), `values` (JSON), `available_at` (JSON per feature), `input_window_sha256` (closed candles used), `missing` (list) | Every `available_at` ≤ `as_of` (DB check plus writer assertion) |
| `TradeRecord` (existing) | **+ `decision_id`** (nullable UUID, indexed) | Additive migration only; no existing field changes |
| `PostTradeMetrics` | `trade` (1:1 per `metrics_version`), `mae_price`, `mfe_price`, `mae_r`, `mfe_r`, `time_to_mae`, `time_to_mfe`, `holding_hours`, `implementation_shortfall` (fill vs decision close), `fill_vs_rule_reference` (fill vs open *t+1*), `metrics_version`, `status` | Derived; recomputable; never edits `TradeRecord` |
| `DecisionLabel` | `decision`, `outcome_kind` (constant per kind), `label_type` {FORWARD_RETURN, COUNTERFACTUAL_RULE}, `label_version`, `rule_id` (hash of rule parameters), `horizon_candles`, `label_window_start` (≥ `information_time` + 1 bar open), `status` {OK, CENSORED, UNTESTABLE, SEALED, COST_UNAVAILABLE, BORROW_UNAVAILABLE}, hypothetical fill ts/price, exit ts/price/reason {STOP, TARGET, TIME, AMBIGUOUS_BAR_STOP_FIRST}, `gross_r`, `net_r`, cost breakdown, `mae_r`, `mfe_r`, `t_to_stop`, `t_to_target`, `data_version`, `computed_at` | Check: `label_window_start > information_time`; no FK to `TradeRecord` |
| `HumanAnnotation` | `target` (decision or trade), `author`, `created_at`, `kind` {NOTE, PRE_TRADE_PLAN, POST_TRADE_REVIEW, CHECKLIST, SETUP_NAME, TAG, EMOTION, CONFIDENCE, MISTAKE, LESSON, ATTACHMENT}, `value` (JSON), `text`, `attachment_ref`, `written_after_outcome` (computed), `supersedes` | Insert-only; edits are new versions |
| `JournalGap` | `run`, `from_ts`, `to_ts`, `reason` | Research excludes gap windows |

Existing `SignalRejection` and `ExecutionIncident` stay, with a nullable `decision_id` added. A one-time
backfill (§17) creates LEGACY `DecisionEvent`s that point at them; old rows are never edited.

---

## 6. Decision lifecycle

```
closed candle t available  ──► DecisionRecorder.open(decision_id, information_time = close(t), snapshot)
   strategy evaluates      ──► NO_SIGNAL / REJECTED / REGIME_REJECTED ....... final (insert row)
   signal generated        ──► SIGNAL_GENERATED (in-memory only; never persisted as final)
   gates evaluated         ──► first FAIL → <X>_REJECTED + gate vector ...... final
   all gates PASS          ──► order attempted
        filled             ──► EXECUTED  + TradeRecord(decision_id)
        not filled         ──► EXECUTION_FAILED + ExecutionIncident(decision_id)
```

- **One decision per (run, symbol, strategy, candle).** A signal that persists over consecutive candles while
  a position is open becomes `DUPLICATE_REJECTED` / `POSITION_ALREADY_OPEN`. It is kept, so analyses can
  de-duplicate explicitly (§10.3).
- **Failure behaviour (OD-J7).** Recording must never change the trading decision. If a write fails, the
  decision proceeds exactly as today, a `JournalGap` is opened, the run is marked `INCOMPLETE`, and an alert is
  logged. Research excludes gap windows. Recommended, strict alternative for DRY_RUN only: halt *new entries*
  while the journal is down. This is the owner's call.

## 7. Trade lifecycle integration

- `TradeLogger.log_entry` gains an optional `decision_id`; the default `None` keeps every current caller valid.
- **Planned vs observed vs post-trade** stay separate:
  - *planned* values are on `DecisionEvent` (`entry`, `sl`, `tp`, `planned_risk`, `expected_r`, `estimated_cost`);
  - *observed* values are on `TradeRecord` (fills, fees, slippage, exit, net P&L, `r_multiple`), unchanged;
  - *post-trade* values are on `PostTradeMetrics` (MAE/MFE, shortfall), versioned and recomputable.
- **Actual risk** = |fill − stop| × quantity, stored in `PostTradeMetrics`. **Planned risk** stays on the decision.
- Pairs (`pair_trade_id`, legs) and DCA (`cycle_id`, `dca_level`) keep their grouping keys. One decision maps to
  one pair trade or one cycle *entry*. DCA add-on levels are lifecycle events of the same decision, not new
  decisions. **No filters are added to DCA**; DCA is journaled descriptively only.

## 8. Rejected-opportunity lifecycle and missed-trade analysis

1. The rejection is recorded at decision time with its snapshot (immutable).
2. After the label window closes *and* the window is outside the sealed zone, the Labeler writes
   `DecisionLabel` rows.
3. The analysis splits by **origin**: STRATEGY, PORTFOLIO_CAPACITY, EXECUTION, SYSTEM, DATA (and MANUAL,
   reserved).
4. **Opportunity cost for PORTFOLIO_CAPACITY** is a *derived report*, not a stored verdict:
   - the rejected decision's counterfactual net R over [hypothetical fill, hypothetical exit];
   - the **blocking position's real mark-to-market return over the same interval** (from `TradeRecord` plus
     candles);
   - the difference, with its status.
5. **No "mistake" field exists.** A profitable counterfactual is an outcome, not an error. "Mistake" exists only
   as a human annotation kind, and is flagged `written_after_outcome` when written after the label window
   closed (hindsight).

---

## 9. Counterfactual-label methodology

### 9.1 The hypothetical execution rule (`rule_id`, frozen per `label_version`) — OD-J2
- **Signal** at the close of candle *t* (`information_time`). **Fill at the open of *t+1*** (D-1,
  `RESEARCH_LABEL_CONTRACT`). d = 2 is a sensitivity variant with a different `rule_id`, never used for selection.
- **Stop and target** = the decision's own planned `sl` / `tp`. For decisions without them (`NO_SIGNAL`), only
  direction-free forward-return labels are computed.
- **Path:** hourly OHLC from the fill bar onward.
  - If one bar touches both stop and target, the order is unknowable, so the rule is **stop first**
    (conservative), flagged `AMBIGUOUS_BAR_STOP_FIRST`, with a target-first sensitivity variant.
  - A gap through the stop exits at that bar's open (not the stop price).
- **Time exit** at the close of *t + 1 + H* (H fixed per rule; proposed H = 24 candles for 1 h strategies).
- **Costs:**
  - fees and slippage from `get_venue_execution_costs(venue)`;
  - shorts are financed via `funding.funding_cost`;
  - an unknown component gives `COST_UNAVAILABLE` / `BORROW_UNAVAILABLE`, with gross still reported and net
    left null.
- **Censoring:** a window that runs past available data, crosses a calendar gap above `gap_max_candles`, or
  enters the sealed zone is `CENSORED` / `UNTESTABLE` / `SEALED`. It is never truncated into a short "outcome".

### 9.2 Labels per decision
- Forward returns at 1, 4 and 24 candles (`timing.forward_label`, open *t+1* → open *t+1+h*).
- Rule outcome: hit stop, hit target, time exit, R (gross and net), MAE, MFE, time to stop, time to target.

### 9.3 Same rule for executed decisions (methodologically essential)
The rule label is also computed for **EXECUTED** decisions. Comparisons are always **counterfactual vs
counterfactual** (rejected-under-rule vs executed-under-rule). They are never counterfactual vs real P&L, because
mixing two measurement methods biases every "saved/destroyed money" estimate. Real fill vs rule is reported
separately as execution quality (`fill_vs_rule_reference`).

### 9.4 Seal and zones
`DecisionLabel` writing calls `ZONES.assert_no_sealed_data` on the label window.
- Decisions whose label window touches F are written with status `SEALED` and **no values**, until
  `open_holdout_evaluation` is ledgered (OD-J4).
- UNASSIGNED_EXPOSED decisions (2026-09-25 → 10-03) get labels marked `PSEUDO_HOLDOUT — NOT CLAIM-ELIGIBLE`.

### 9.5 Markets
Labels use the instrument's `MarketProfile` calendar. **FX labels are blocked** until OD-FX-4 (horizon basis:
trading candles vs wall-clock hours) is decided. Crypto is continuous, so wall-clock and trading candles coincide.

---

## 10. ML-ready feature/label architecture

### 10.1 Features at T
`MarketSnapshot.values`, computed **only from closed candles ≤ t**, with the input candle window hashed. Fields
are admitted to `feature_set_version` only with a proof of availability:

| Group | Fields | Admissible now? |
|---|---|---|
| Price/vol | returns 1/4/24, realised vol, ATR, ADX, RSI, EMA spread, BB width | Yes: computed from closed candles |
| Regime | full `RegimeResult` | Yes |
| Structure | `structure_analyser` outputs | Only after a truncation-invariance test (§16) |
| Calendar | hour, weekday, session (from `MarketProfile.calendar`) | Yes, deterministic |
| Spread / book / liquidity | live order-book snapshot | **Live only.** No historical book in backtests, so it is train/serve-skewed. Recorded, excluded from model features until historical data exists |
| Funding / borrow | last settled funding ≤ t | Yes, with `available_at` = settlement time |
| Cross-asset | BTC return/regime at t | Yes, if from the same closed-candle grid |
| Portfolio | exposure, open positions, balance | Yes (state at t) |
| Human tags / notes | — | **No.** Never features without an explicit, versioned NLP pipeline |

### 10.2 Labels from T+1
Only `DecisionLabel` rows with `label_window_start > information_time`. A builder joins features and labels
and asserts the temporal ordering row by row. Exports are by **allowlist**; `HumanAnnotation` is never in an
allowlist.

### 10.3 Dependence
- Consecutive hourly decisions and overlapping horizons are dependent.
- Inference uses `inference.py` block methods (`d10_block_length(h)`).
- CV uses `oos_validator.build_folds` with purge ≥ horizon plus embargo.
- Duplicate decisions are collapsed to the first decision of each episode for "per-opportunity" estimands. Both
  views are reported, labelled.

---

## 11. ML discovery layer (design only; nothing built in Phases 1–4)

The progression is fixed. Each step must pass before the next is considered:
1. **Descriptive conditional statistics** (setup × hour, × regime, × weekday, × volatility bucket; rejection
   reason × counterfactual net R; delay × outcome; exit rule × MFE/MAE). Each table reports n, mean, and a
   block-bootstrap CI. **Cells below a minimum n (OD-J12) are suppressed**, not shown.
2. **Block-permutation inference** on a small number of pre-declared contrasts.
3. **Logistic regression** (statsmodels, available).
4. **Decision trees**, then **RF/GBM** (needs scikit-learn, OD-J10).

Then:
- **Output = hypotheses, never rules.** Every candidate condition becomes a written hypothesis with an economic
  rationale. At most 5 per family are registered in `HypothesisRegistry` (program `decision_journal`).
- Exploration uses **zone S only** (BH q = 0.10, permanently EXPLORATORY). Confirmation uses **zone V only**
  (Holm, B = 999).
- The ladder level comes from `verdict_ladder`.
- **No import path from the ML/analytics layer into `bot/strategies`, `bot/engines` or `bot/core`.** This is
  enforced by an import-boundary test, so no model can promote itself.
- **Multiple-testing accounting.** A grid of k cells is k tests. The family size is the number of cells
  actually examined, recorded in the output; it is never the 5 that get registered.

---

## 12. Five-gate enforcement points

| Gate | Enforcement point | Mechanism |
|---|---|---|
| G1 Software | Recorder, Labeler, schema | DB constraints (insert-only, unique decision per candle, `available_at` ≤ `as_of`, `label_window_start` > `information_time`); `SIGNAL_GENERATED` never final; deterministic `decision_id` for backtests (hash of run, symbol, strategy, t); mutation tests on every guard; netguard offline tests |
| G2 Maths | `PostTradeMetrics`, Labeler | Known-answer tests on hand-built candle paths: R, gross/net, MAE/MFE, stop-first ambiguity, gap-through-stop, funding sign, censoring |
| G3 Statistics | Analytics / ML | Block methods only (no IID p-values; `IID_PVALUE_BASIS` is descriptive-only); registry families; BH on S / Holm on V; calibration of any new test via `calibration.py`; purged OOS |
| G4 Quant | Rule and labels | d = 1 fill at the open; venue costs; funding/borrow fail-closed; calendar gaps; censoring; capacity (size vs volume) before TRADEABLE |
| G5 Integrity | `JournalRun`, sidecars, seal | Commit plus dirty digest; config / zones / profile / feature / label / taxonomy versions; `SEALED` labels; LEGACY marking; no dataset mixes versions; truncation-invariance test (no look-ahead) |

**No journal statistic becomes "edge".** Its ceiling is set by `ladder_level`:
- descriptive journal tables are at most OBSERVATION;
- STATISTICAL needs a registered family confirmed in V;
- ECONOMIC needs complete costs;
- TRADEABLE needs causal execution plus a V pass plus capacity;
- CANONICAL needs full provenance plus a ledgered F pass.

A single descriptive pattern can never be CANONICAL; this is structural in the ladder already.

## 13. Provenance design
`JournalRun` stores, once per run:
- `run_id`, strategy and version;
- `METHODOLOGY_VERSION`, `protocol_hash`, `config_hash` (redacted), `zones_hash`, `market_profile_hash`,
  `universe_id`;
- `data_version` (QBP-DATAV1, backtests), code commit, dirty flag and digest;
- feature, label and taxonomy versions.

Every exported dataset or analysis output gets an `OutputProvenance` sidecar via `write_provenance`; new
`kind`s are `DECISION_STREAM`, `DECISION_LABELS`, `JOURNAL_ANALYSIS`. `status` is CANONICAL only if the run was
clean (`code_dirty = False`) under the current methodology. Otherwise it is UNCERTIFIED or LEGACY, exactly as
today. Live rows carry `input_window_sha256` per decision, because a live data version cannot be computed after
the fact.

## 14. Evidence classification
Reuse `verdict_ladder` unchanged. A journal finding's `LadderEvidence` is filled from:
- the registry (family confirmation);
- the cost-complete label status;
- the timing contract;
- zone V results;
- a capacity check;
- the provenance sidecar;
- the D-8 ledger.

A missing input caps the level, as the ladder already does. **No new classification system.**

## 15. Data retention, privacy and security
- **Retention:** nothing is deleted. Rows are insert-only, and corrections use `supersedes`.
  - Backtest streams are compressed files with sidecars under `research_data/journal_runs/<run_id>/` (the
    existing `research_data/*` gitignore applies).
  - Live `NO_SIGNAL` rows are kept in full: about 8.8k per symbol-venue-year, which is small.
  - Backtest `NO_SIGNAL` rows are kept in full too. Sampling would destroy the denominator (OD-J6).
- **Attachments** (screenshots, later voice) go to object storage, because the Railway filesystem is ephemeral
  (OD-J8). The DB holds a reference and a SHA-256 only.
- **Secrets:** snapshot and config hashing run on a redacted config. API keys, account numbers and credentials
  are never stored; the account is a label.
- **Human notes** may hold personal or psychological content. They are staff-only, never exported to
  ML datasets (allowlist), and never sent to external services.

## 16. Testing strategy (all offline under the netguard; no mocking of exchanges, consistent with project convention)
- **Truncation invariance (no look-ahead, G5):** for random t, the snapshot computed from `df[:t]` equals the one
  computed from the full `df`. Applied to every admitted feature.
- **Label causality:** `label_window_start` > `information_time` for every row, plus a mutation test that shifts
  the fill to the close of t, which must fail.
- **Known answers (G2):** about 15 hand-built paths covering stop, target, ambiguous bar, gap-through, time exit,
  censoring, short-side funding, `COST_UNAVAILABLE`.
- **Separation:** an import-boundary test (analytics/ML must not import strategies or engines; real-P&L
  aggregators must not import labels), plus a check that dashboard totals equal `TradeRecord` sums with labels
  present.
- **Seal:** writing labels inside F without a ledger entry raises; `SEALED` rows carry no values.
- **Lifecycle:** every recorder path ends in a final status; a run with an unclosed `SIGNAL_GENERATED` fails.
- **Equivalence (before any live wiring):** with the recorder on vs off, the backtester produces byte-identical
  trades and results. The existing golden fingerprints must not change.
- **Taxonomy:** every reason code maps to one status; unknown codes are refused; version mixing is refused.

## 17. Migration strategy
1. Migration `0016`: new tables plus nullable `decision_id` on `TradeRecord`, `SignalRejection` and
   `ExecutionIncident`. Additive only; no column changes or drops.
2. Backfill command (idempotent, dry-run first): create LEGACY `DecisionEvent`s.
   - `TradeRecord` rows become EXECUTED; `SignalRejection` rows become REJECTED / ENTRY_SIGN_MISMATCH;
     `ExecutionIncident` rows become EXECUTION_FAILED.
   - Snapshot `missing = ["*"]`, provenance `NOT_RECORDED`.
   - **Old rows are never modified** except to set their own nullable link.
3. Backtester recorder is behind an explicit argument (default off). Default backtests stay byte-identical.
4. Live wiring is a separate, owner-approved phase. It stays DRY_RUN and is proven equivalent by tests first.

## 18. Risks and failure modes
| Risk | Mitigation |
|---|---|
| Look-ahead through forming candles or full-sample normalisation | `drop_forming_candles`; truncation-invariance test; `available_at` checks |
| Order-dependent rejection attribution | Gate vector (OD-J7) |
| Counterfactual optimism (perfect fills at the open, no queue, intrabar order) | d = 1 open fill, venue slippage, stop-first ambiguity, sensitivity variants |
| Comparing counterfactuals with real P&L | Same-rule comparison only (§9.3) |
| Sealed-F breach via dashboards that show live counterfactuals | `SEALED` status and no values until the ledger is opened |
| Multiple-testing explosion (setup × hour × weekday × regime) | Family = cells examined; BH on S, Holm on V; minimum cell n |
| Hindsight bias in human tags | `written_after_outcome` flag; tags excluded from features |
| Silent recorder failure leaving holes in the denominator | `JournalGap` and `INCOMPLETE` runs; research excludes gaps |
| Train/serve skew (live-only order-book features) | Recorded, but excluded from models |
| Storage growth / Postgres bloat | Bulk streams are file-based; DB holds live/dry-run only |
| Clock errors (cf. the FX server-clock defect) | UTC `information_time` from candle open time + Δ; FX via the converted data |
| Tiny live sample (one symbol, few signals per month) | Live journal is operational; research volume comes from backtests in S/V |

## 19. What should NOT be built yet
- Any ML model, scikit-learn dependency, RF/GBM, or automated rule promotion.
- NLP on notes, voice notes, auto-screenshots.
- A manual veto/override UI (no manual path exists; the MANUAL origin is reserved only).
- Order-book features in models (no historical data).
- `EXPECTED_EDGE` gating: no strategy has a canonical edge estimate.
- Live-loop wiring before the backtester recorder is proven equivalent.
- FX labels (blocked by OD-FX-4).
- Broad journal pattern sweeps before this methodology and the family registration are approved.
- Any DCA filter.
- A journal dashboard showing counterfactual P&L for F-period decisions.

## 20. Owner decisions required before implementation
| ID | Decision | Recommendation (strictest defensible) |
|---|---|---|
| OD-J1 | Add `SYSTEM_REJECTED` for the reconciliation gate? | Yes (documented in §4.1) |
| OD-J2 | Counterfactual rule: d = 1 open fill, planned SL/TP, stop-first on ambiguous bars, gap-through at the open, H = 24, venue costs, funding for shorts | As stated; d = 2 and target-first as sensitivity variants only |
| OD-J3 | Label horizons | Fixed {1, 4, 24} candles plus the rule outcome, pre-registered, versioned |
| OD-J4 | Live/dry-run labels in F | `SEALED` until the D-8 ledgered evaluation; operational real-P&L views unaffected |
| OD-J5 | Bulk storage format | CSV.gz plus sidecar now (no new dependency); pyarrow only if adopted deliberately |
| OD-J6 | `NO_SIGNAL` retention | Keep all, live and backtest (the denominator) |
| OD-J7 | Recorder failure policy, and extracting a pure gate evaluator for the gate vector | Decision unchanged + `JournalGap`; gate-vector refactor only in the live-wiring phase, with equivalence tests |
| OD-J8 | Attachment storage | Object storage with hash references; none until chosen |
| OD-J9 | MAE/MFE basis | Hourly high/low from the fill bar onward; intrabar path not claimed |
| OD-J10 | scikit-learn for trees/ensembles | Defer to Phase 7 |
| OD-J11 | Registry endpoint for journal questions (e.g. mean counterfactual net R of reason-r rejections) | Add an endpoint by a registry-version bump, not ad hoc |
| OD-J12 | Minimum cell size for descriptive tables | n ≥ 30 decision *episodes*; smaller cells suppressed |
| (link) | OD-FX-4 (FX horizon basis) | Unblocks FX labels |

## 21. Recommended implementation phases
| Phase | Scope | Exit criterion |
|---|---|---|
| 0 | Owner signs OD-J1…J12; taxonomy v1 frozen | Signed decision sheet |
| 1 | Schema, migration 0016, taxonomy module, `JournalRun` / `DecisionEvent` / `MarketSnapshot`, `DecisionRecorder`; **backtester only**, opt-in | G1/G5 tests; backtests byte-identical with the recorder off; truncation invariance |
| 2 | Labeler (forward returns and the counterfactual rule) on S/V backtest streams; `PostTradeMetrics` | G2 known-answer suite; seal tests; mutation tests |
| 3 | LEGACY backfill of existing journal rows | Idempotent; old rows unchanged |
| 4 | Descriptive journal analytics (zone S, EXPLORATORY) with block CIs; output sidecars | G3 review; family accounting |
| 5 | Human annotations (notes, plans, reviews, tags, emotions; attachments after OD-J8) | Separation tests |
| 6 | Live/dry-run wiring (DRY_RUN stays), gate-vector evaluator extraction | Equivalence tests; owner approval |
| 7 | Interpretable models (logit → trees; scikit-learn after OD-J10) generating registered hypotheses | Registry/ladder only; no rule promotion |

## 22. Component specification summary
| Component | Purpose | Inputs | Outputs | Source of truth | Depends on | Failure behaviour | Tests | Gates |
|---|---|---|---|---|---|---|---|---|
| Taxonomy | Controlled statuses and reasons | — | Enums, mapping | `bot/journal/taxonomy.py` | — | Unknown code raises | Mapping completeness, version mixing | G1, G5 |
| JournalRun | Provenance header | `code_state`, hashes, versions | Row | DB / sidecar | provenance, zones, market_profile | Missing value is recorded as such, never guessed | Hash determinism | G5 |
| DecisionRecorder | The single writer | Pipeline state at t | DecisionEvent + Snapshot | DB (live) / file (backtest) | Taxonomy, snapshot builder | Decision unchanged; `JournalGap` | Lifecycle finality, equivalence | G1, G5 |
| Snapshot builder | Point-in-time features | Closed candles ≤ t | `values`, `available_at`, window hash | MarketSnapshot | RegimeDetector, features | Missing feature listed, not imputed | Truncation invariance | G1, G4, G5 |
| Labeler | Forward/counterfactual labels | Decision, candles > t, costs, calendar | DecisionLabel | DB / file | timing, zones, costs, funding, market_profile | CENSORED / UNTESTABLE / SEALED / COST_UNAVAILABLE | Known-answer, causality, seal | G2, G4, G5 |
| PostTradeMetrics | MAE/MFE, shortfall | TradeRecord + candles | Row | DB | — | status ≠ OK, never 0 | Known-answer | G2 |
| HumanAnnotation | Trader journaling | User input | Row / attachment ref | DB / object store | Auth | Refuse without author | Separation, hindsight flag | G1, G5 |
| Journal analytics | Descriptive and inferential research | Decisions + labels (S/V) | Tables with sidecars | Files | inference, registry, oos_validator | Suppress small cells | Family accounting | G3, G5 |
| ML discovery | Hypothesis generation | Feature/label matrix | Registered hypotheses | Registry | Above + scikit-learn (later) | Never writes strategy config | Import boundary | G3, G5 |

*End of proposal. Nothing in this document has been implemented. All gate statuses are NOT EVALUATED.*
