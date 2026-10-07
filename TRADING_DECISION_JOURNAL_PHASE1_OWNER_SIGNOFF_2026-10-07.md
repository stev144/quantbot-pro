# Trading Decision Journal: Phase 1 Owner Sign-off (verbatim record)

Recorded 2026-10-07 by Claude Code before any Phase 1 implementation code was written, as the owner required
("Proceed with Phase 1 only after recording these decisions exactly").

- The text inside the fence below is the owner's sign-off, unedited.
- Design: `TRADING_DECISION_JOURNAL_DESIGN_REVB_2026-10-07.md` (current); Revision A is superseded and unchanged.
- Pre-coding findings that led to D1–D8: the stop report of 2026-10-07 in the session transcript, summarised in
  `TRADING_DECISION_JOURNAL_PHASE1_IMPLEMENTATION_REPORT_2026-10-07.md` §1.

```text
OWNER SIGN-OFF — TRADING DECISION JOURNAL PHASE 1
Date: 2026-10-07
Design: TRADING_DECISION_JOURNAL_DESIGN_REVB_2026-10-07

Proceed with Phase 1 only after recording the following owner decisions.

OD-J13 / D1
----------
APPROVED FOR PHASE 1 — BACKTESTER HALF ONLY.

Approve the backtester evaluation-point definition exactly as verified from the code:

- evaluation occurs only while flat;
- existing warm-up period is not an evaluation point;
- backtester does not evaluate on the candle on which a trade closes;
- evaluation begins at the existing candle-100 boundary;
- only the actual evaluation points produced by the current backtester are journalled;
- do not align the backtester to the live loop;
- do not modify strategy timing or behaviour.

The live-loop half of OD-J13 remains OPEN.

Pairs and DCA evaluation-point definitions remain DEFERRED and OUT OF SCOPE for Phase 1.

D2 — WHAT IS A CANDIDATE?
--------------------------
APPROVED: OPTION A.

A candidate exists only when the strategy/router produces an actual BUY or SELL signal.

Therefore:

- strategy conditions that stop before BUY/SELL are NO_SIGNAL;
- preserve their specific machine-readable no-signal reason;
- router direction conflicts occurring after a BUY/SELL signal are REJECTED;
- post-signal fill-time refusals are REJECTED;
- do not infer a hidden "candidate" from internal strategy predicates such as EMA trend, RSI extreme, etc.

Do not modify strategy internals merely to manufacture additional candidate stages.

D3 — GATE VECTOR
-----------------
APPROVED with the following precise interpretation:

Control-flow-proven PASS is acceptable.

For a gate/check that the actual executed control flow demonstrably passed:

    PASS

For the check that actually failed:

    FAIL

For checks that occur later in the strategy/control-flow path and therefore were never evaluated:

    NOT_EVALUATED

The gate vector must represent observed control flow, not hypothetical evaluation.

Do not claim that PASS means "the entire strategy condition set passed."

The check order/list must be versioned and documented per strategy so that the meaning of the gate vector is reproducible.

The gate vector must remain analytically separate from the final reason/status.

Do not use the first failing gate to assign causal credit to a filter.

D4 — REASON/STATUS TAXONOMY
----------------------------
APPROVED with the following mappings.

Use the exact reason codes below or the closest existing controlled code already present in the approved taxonomy. Do not invent alternate meanings.

1. Router: high_volatility_blocked

    status = REGIME_REJECTED
    stage  = REGIME
    reason = WRONG_REGIME

2. Router: confidence_too_low_{x}

    status = REGIME_REJECTED
    stage  = REGIME
    reason = REGIME_CONFIDENCE_TOO_LOW

Store the confidence value x in structured detail, not inside the primary reason code.

3. Router: adx_below_minimum_for_trend

    status = REGIME_REJECTED
    stage  = REGIME
    reason = ADX_TOO_LOW

This is a router/regime admissibility decision, not a strategy malfunction.

4. Router: strategy_not_validated_{verdict}

    status = SYSTEM_REJECTED
    stage  = GOVERNANCE
    reason = STRATEGY_NOT_VALIDATED

Preserve the actual verdict in structured detail.

This is a governance/safety block, not evidence that the strategy had no signal.

5. Router: longs_disabled / shorts_disabled

    status = SYSTEM_REJECTED
    stage  = CONFIGURATION
    reason = LONGS_DISABLED / SHORTS_DISABLED

These are configuration/policy blocks, not strategy failures.

6. Router:
   sell_rejected_in_uptrend
   buy_rejected_in_downtrend

    status = REJECTED
    stage  = REGIME
    reason = DIRECTION_CONFLICTS_REGIME

These occur after a directional signal exists.

7. Router: mean_reversion_returned_none

    status = SYSTEM_REJECTED
    stage  = STRATEGY_RUNTIME
    reason = STRATEGY_OUTPUT_INVALID

Do not classify this as ordinary NO_SIGNAL unless code inspection proves that None is an intentional normal no-signal result.

8. Moving average: missing_data

    status = DATA_REJECTED
    stage  = DATA
    reason = DATA_QUALITY_FAILURE

9. Moving average:
   not_enough_data
   indicator_warmup

During the genuine initial warm-up period:

    status = WARMUP / NOT_EVALUATED
    reason = WARMUP

It must NOT count as an evaluation point.

If the same condition occurs unexpectedly after the warm-up boundary, treat it as:

    status = DATA_REJECTED
    stage  = DATA
    reason = DATA_QUALITY_FAILURE

Do not silently label a mid-series data problem as ordinary warm-up.

10. Moving average: structure_analysis_failed

    status = SYSTEM_REJECTED
    stage  = STRATEGY_RUNTIME
    reason = STRUCTURE_ANALYSIS_FAILURE

Do not classify an internal failure as NO_SIGNAL.

11. Moving average:
    flat_market
    no_ema_trend
    structure_not_uptrend_{label}
    structure_not_downtrend_{label}
    no_pullback
    no_momentum

    status = NO_SIGNAL
    stage  = STRATEGY
    reason = corresponding controlled reason code

For example:

    FLAT_MARKET
    NO_TREND
    STRUCTURE_NOT_CONFIRMED
    NO_PULLBACK
    NO_MOMENTUM

Store the label as structured detail where applicable.

12. Mean reversion:
    not_enough_data
    rsi_not_ready
    atr_unavailable
    bb_unavailable

Use the same warm-up/data distinction:

Initial warm-up:

    status = WARMUP / NOT_EVALUATED
    reason = WARMUP

Unexpected post-warm-up unavailability:

    status = DATA_REJECTED
    stage  = DATA
    reason = DATA_QUALITY_FAILURE

13. Mean reversion:
    non_ranging_market
    range_too_wide
    no_extreme
    buy_no_confirmation
    sell_no_confirmation

    status = NO_SIGNAL
    stage  = STRATEGY
    reason = corresponding controlled code

14. Backtester: invalid_signal

Where the router output is not a valid BUY/SELL signal:

    status = SYSTEM_REJECTED
    stage  = STRATEGY_RUNTIME
    reason = INVALID_SIGNAL

Do not treat malformed strategy output as NO_SIGNAL.

15. Backtester: unknown / missing reason

Do NOT silently classify an unknown reason as NO_SIGNAL, REJECTED, or another trading outcome.

Preserve the raw reason.

Record an explicit journal mapping failure and mark the JournalRun INCOMPLETE.

The journal must not change the backtest behaviour.

An unmapped runtime reason is a taxonomy/integration defect that must be fixed before the run can be considered complete research evidence.

16. Fill-time: quantity <= 0

    status = REJECTED
    stage  = RISK
    reason = SIZE_BELOW_MINIMUM

17. Fill-time: zero stop distance

    status = SYSTEM_REJECTED
    stage  = STRATEGY_RUNTIME
    reason = INVALID_SIGNAL

A zero stop distance is not a valid risk decision.

18. Fill-time: unexpected signal value

    status = SYSTEM_REJECTED
    stage  = STRATEGY_RUNTIME
    reason = INVALID_SIGNAL

Do not create a fake trade or NO_SIGNAL record.

IMPORTANT D4 RULE:
The controlled reason code must describe what the engine actually did.
Do not force every failure into the strategy/no-signal category.

D5 — SIGNAL ON FINAL CANDLE
----------------------------
APPROVED:

    CENSORED_END_OF_DATA

Use this when a valid signal exists but there is no subsequent open at which the existing next-open execution rule can fill it.

Do NOT call it:

    EXECUTION_FAILED

because no execution attempt occurred.

It must be excluded from fill-dependent outcome statistics.

D6 — BACKTEST TRADE LINKAGE
----------------------------
APPROVED.

For Phase 1:

- executed DecisionEvent links to the in-memory backtest trade using run_id + stable trade sequence identifier;
- do not copy fill facts unnecessarily into DecisionEvent;
- existing backtest trade remains the source of truth for observed simulated fill facts;
- add nullable TradeRecord.decision_id now as an additive schema change for future live integration;
- do not force backtest trades into TradeRecord merely to satisfy the journal design;
- do not modify canonical P&L calculations.

Prefer a stable deterministic backtest trade identifier derived from the run and trade sequence rather than relying only on an unstable Python object identity.

D7 — ENVIRONMENT / EXECUTION MODE
----------------------------------
APPROVED with one implementation safeguard.

Use:

environment:
    BACKTEST
    RESEARCH
    PAPER
    DRY_RUN
    LIVE

For Phase 1:

    environment = BACKTEST

and:

    execution_mode = SIMULATED

Keep execution_mode semantically distinct:

    LIVE
    DRY_RUN
    SIMULATED
    UNKNOWN

Do NOT create two independent canonical fields that encode the same concept.

However, if an existing run_type field already exists elsewhere in the repository, do not destructively remove or rename it merely for Phase 1.

Instead:

- make environment the canonical journal field;
- preserve existing run_type for backward compatibility;
- mark it deprecated/redundant where appropriate;
- avoid destructive migration.

No legacy backfill is part of Phase 1.

D8 — COVERAGE / NOT-EVALUATED
------------------------------
APPROVED as a compact run-level coverage mechanism, NOT one database row per candle for Phase 1.

The decision journal must retain all genuine evaluation points, including NO_SIGNAL.

But do not create tens of thousands of additional DecisionEvent-like rows solely to represent non-evaluation.

Store run-level coverage counts and compact coverage intervals/categories sufficient to prove:

- evaluation count
- warm-up count
- in-position non-evaluation
- close-candle non-evaluation
- pending-entry non-evaluation
- pre-loop/non-start period
- any actual skipped data/candle ranges

Use the existing source candle grid plus trade lifecycle to make exact coverage reproducible.

For Phase 1, do NOT create a full per-candle coverage table unless implementation proves it is necessary to establish reproducibility.

Do not count any non-evaluated category as NO_SIGNAL.

CORE SEMANTIC RULES
-------------------
Maintain these distinctions permanently:

NO_SIGNAL
= strategy actually evaluated and found no candidate.

REJECTED
= candidate existed but a downstream strategy/router/risk/filter decision rejected it.

SYSTEM_REJECTED
= system, governance, configuration, or runtime safety condition rejected/blocked it.

NOT_EVALUATED
= the engine did not perform a strategy evaluation.

WARMUP
= evaluation was not yet possible because the required initial history did not exist.

CENSORED_END_OF_DATA
= valid opportunity exists, but required future execution/outcome window does not exist.

EXECUTION_FAILED
= eligible order reached execution and failed to produce the required fill.

EXECUTED
= simulated backtest execution produced a fill.

Do not merge these categories.

PHASE 1 SCOPE
-------------
Proceed only with the backtester recorder.

Do not touch:

- live loop
- DRY_RUN wiring
- PAPER trading
- pairs
- DCA
- FX
- ML
- strategy rules
- strategy parameters
- holdout methodology
- confirmation runs
- episode derivation
- new filters
- expected-edge gates

OD-J14 remains OPEN.

Store atomic decision events only.
Do not collapse decisions into episodes.

V-1 remains OPEN and is not resolved by this implementation.

TEST REQUIREMENT
----------------
Before declaring Phase 1 complete, prove:

1. Recorder OFF and ON produce equivalent backtest trading behaviour.
2. Existing evaluation-point behaviour is unchanged.
3. Existing warm-up boundaries are unchanged.
4. Existing no-signal semantics are unchanged.
5. Unknown/unmapped reasons cannot silently become valid research outcomes.
6. Gate vectors reflect actual observed control flow.
7. Future information cannot enter the decision snapshot.
8. Decision events remain immutable.
9. Duplicate events are rejected.
10. Provenance is complete.
11. Final-candle signals are censored rather than execution failures.
12. No-evaluation candles never become NO_SIGNAL.
13. Existing P&L mathematics remains unchanged.
14. Migration is additive/non-destructive.

Use negative/mutation tests for leakage, timing changes, incorrect reason mapping, duplicate records, and recorder-induced strategy changes.

After implementation, report G1–G5 separately as:

PASS / PARTIAL / FAIL / UNKNOWN / N/A

Do not claim that the journal implementation proves a trading edge.

Proceed with Phase 1 only after recording these decisions exactly.
```

## Earlier approvals in the same session (recorded for completeness)
- **Approved:** OD-J1, OD-J5, OD-J6, OD-J7a and OD-J15 (Phase 1 authorization message, 2026-10-07).
- **OD-J5 clarification:** the structured journal/database is the canonical source of truth; compressed CSV +
  sidecar is a research/export format only.
- **OD-J15:** environment vocabulary BACKTEST, RESEARCH, PAPER, DRY_RUN, LIVE. REPLAY is not created.
- **Still open:** OD-J14 and V-1.

## Amendment (owner decision, 2026-10-07, during implementation)
- **Conflict found:** the existing `execution_mode` vocabulary (`bot/journal/models.py`, Post-audit Phase 1) is
  LIVE / DRY_RUN / RESEARCH / UNKNOWN, and RESEARCH already means "offline backtest/research output". D7's SIMULATED
  would have encoded that same concept a second time.
- **Owner answer:** "Reuse RESEARCH (Recommended)".
- **Effect:** Phase 1 records environment = BACKTEST and execution_mode = RESEARCH. No SIMULATED value was added.
- **D7 safeguard:** no `run_type` field exists anywhere in the repository code, so nothing needed deprecating.


## Phases 2-7 owner decisions (2026-10-07, AskUserQuestion answers, recorded before implementation)
The owner asked to "build all the 7 journal phases in order" under the five gates, and answered:
- **Open decisions → "Delegate: use recommendations".** Each is set to the strictest recommendation already written in
  Revision B and is recorded as **owner-delegated**:

| ID | Delegated value |
|---|---|
| OD-J2 | Counterfactual rule: signal at the close of t; fill at the OPEN of t+1; the decision's planned SL/TP; a gap through a level at a bar's open exits at that open; same-bar stop+target counts as stop first (baseline, labelled an assumption), with target-first as the bound; time exit after H = 24 bars; venue fees + slippage from `execution_costs`; shorts financed through recorded funding, and missing funding gives COST_UNAVAILABLE (never 0) |
| OD-J3 | Label horizons: forward returns at 1, 4 and 24 candles plus the rule outcome, fixed and versioned |
| OD-J4 | Labels whose window touches the sealed holdout F are SEALED, with no values |
| OD-J8 | No attachment/screenshot storage (none until storage is chosen); notes, tags and structured fields only |
| OD-J9 | Bar-resolution MAE/MFE stored both with and without the exit bar |
| OD-J11 | A journal endpoint is added to the registry by a version bump |
| OD-J12 | `display_min_episodes` = 30, a display threshold only |
| OD-J16 | Zone V label reads are guarded and logged through a registry extension (version bump) |
| OD-J17 | Ambiguity reporting: stop-first baseline, target-first bound, interval plus ambiguous share |
| OD-J18 | Gate ablations only as registered hypotheses |

- **OD-J14 → "(iii) Break, report both counts".** Episodes break at a trade exit; every analysis also reports the
  never-break count.
- **Phase 6 → "Build it, DRY_RUN, equivalence-tested".** The live-loop half of OD-J13 is approved by this answer, with
  the Revision B §4 live definition. Live trading is never enabled.
- **Phase 7 → "statsmodels logistic only"** (OD-J10 = defer scikit-learn).
