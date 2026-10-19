# FX DCA Research Protocol v5

**Identifier:** FX-DCA-PROTOCOL-1-A4
**Status:** AMENDMENT — written AFTER permutation test fold results were examined (p=1.000 uniformly across all 10 tests; see governance note below). Parameters in this amendment were NOT selected to optimise those results.
**Amendment date:** 2026-10-17
**Supersedes (for changed sections only):** FX-DCA-PROTOCOL-1-A3 (FX_DCA_PROTOCOL_2026-10-16_v4.md) for Part 3B (risk controls) only. v1, v2, v3, and v4 documents and their hashes remain in the repository unchanged.
**Unchanged sections:** Part 1 (owner decisions), Part 2 (instrument universe and data specification), Part 3 (data-derived ladder specification and locked calibration thresholds), Part 3A (locked calibration values), Part 4 (validation splits), Part 5 (null model and statistical requirements), Part 6 (what this protocol does not authorise).

---

## Governance note: results examined before this amendment

The following results were examined before this document was written:

- Full 5-fold walk-forward fold results for all 5 protocol pairs, both directions (LONG/SHORT), using the v4/A3 simulation (no time exit, no explicit deployment cap beyond the 7-level ladder structure).
- Block-shuffle permutation test results: p=1.000 for all 10 (pair, direction) combinations, N=499 permutations, block=48h, seed=20261017. Real temporal sequence performs at or below the floor of the null distribution on all pairs.

This amendment adds risk controls (time exit, explicit deployment cap) that are required by the research mission specification (Sections 6.2 and 6.3). These parameters were NOT chosen to improve the above results:

- `MAX_CYCLE_CANDLES = 720` was derived from the H=24 MAE horizon (30 × 24 = 720 candles), not from inspecting per-fold P&L across candidate values. With p=1.000 uniformly there is no P&L optimisation to perform.
- The deployment cap is derived from the existing ladder structure (7 fills × pre-specified multipliers), not from P&L outcomes.

A re-run of the fold simulation and permutation test with v5 parameters is authorised by this amendment. Those results will supersede the v4 results above for any further inferential claim.

---

## Amendment log

### A4-1: Time-based exit specification (adds Part 3B, Section 1)

**Change:** Pre-specifies a maximum cycle duration after which an open cycle is force-exited at the next candle's open price.

**MAX_CYCLE_CANDLES = 720**

Rationale: H=24 is the MAE measurement horizon used for ladder level assignment (v3/A2 Part 3, Step 2). 720 = 30 × 24 — if a DCA cycle has not recovered in 30× the measurement horizon (approximately 30 trading days on 1h candles), it is exited regardless of profit state. This is a pre-specified round number. It was not selected by scanning over candidate values or by observing that a specific value improved calibration P&L.

Execution rule:
- At each candle's close, after the profit-target trailing-stop check, if `candles_held >= MAX_CYCLE_CANDLES` and no exit is already scheduled, set `pending_exit = TIME_EXIT`.
- The exit executes at the NEXT candle's open (NEXT_OPEN model), consistent with all other exits in this simulation.
- Spread, commission, and accumulated swap P&L are included in the exit calculation, exactly as for PROFIT_TARGET_TRAILING exits.
- If the profit trailing-stop triggers on the same candle as the time exit condition, the profit exit takes precedence (it fires first in the decision order).
- A cycle that reaches the end of the test period with a pending TIME_EXIT that cannot be executed (no next candle) is recorded as DATA_END_STILL_OPEN, not TIME_EXIT.

Implementation: `max_cycle_candles` parameter added to `_simulate_data_derived_next_open()` and `run_fx_fold()` in `bot/research/fx_fold_runner.py`. Default value = 720. Set to `None` to disable (reproduces v4/A3 behaviour exactly for comparison runs).

### A4-2: Explicit deployment cap documentation (adds Part 3B, Section 2)

**Change:** Documents the existing implicit deployment cap explicitly. No simulation change; this is a governance record.

The existing ladder structure already enforces a hard maximum deployment per cycle:

```
Level 1 (entry):   initial_usdt × 1   =  6 USDT
Level 2 (DCA 1):   initial_usdt × 2   = 12 USDT
Level 3 (DCA 2):   initial_usdt × 4   = 24 USDT
Level 4 (DCA 3):   initial_usdt × 8   = 48 USDT
Level 5 (DCA 4):   initial_usdt × 16  = 96 USDT
Level 6 (DCA 5):   initial_usdt × 32  = 192 USDT
Level 7 (DCA 6):   initial_usdt × 64  = 384 USDT
```

Maximum total deployment per cycle = `initial_usdt × (1 + 2 + 4 + 8 + 16 + 32 + 64)` = `initial_usdt × 127` = **762 USDT** (with `initial_usdt = 6`).

This cap is enforced by `lvl_idx < len(spec.base_pips)` in the simulation loop (`spec.base_pips` has 6 elements — levels 2–7 only; the entry is level 1). No new level can be added once all 6 DCA slots are filled.

Constants recorded:
- `MAX_LEVELS = 7` (1 entry + 6 DCA add-ons)
- `MAX_DEPLOYED_MULTIPLIER = 127` (sum of all level multipliers including level 1)
- With `initial_usdt = 6`: `MAX_DEPLOYED_USDT = 762`

**Important limitations:**
- This cap limits notional capital committed per cycle. It does NOT cap loss: a fully deployed cycle (762 USDT notional) at a 100-pip adverse move on 1 standard lot has a mark-to-market loss that can exceed the notional (due to leverage). Actual monetary risk depends on leverage, lot sizing, and gap risk.
- Portfolio-level cap across simultaneous cycles is NOT YET IMPLEMENTED. This gate remains BLOCKED pending Phase 5 (portfolio wrapper).
- The cap does not prevent a new cycle from opening immediately after the old one closes — the simulation currently opens a new cycle on the next available candle after any exit.

---

## Part 3B (new) — Risk controls

*(Added by A4. These controls apply to the fold simulation in `bot/research/fx_fold_runner.py`. They do not affect the DCA engine in `bot/engines/dca_engine.py`.)*

### Section 1: Time-based exit

- **MAX_CYCLE_CANDLES = 720** (pre-specified; not to be changed without a v6 amendment)
- Applied in `_simulate_data_derived_next_open()` and propagated through `run_fx_fold()`
- Exit reason label: `TIME_EXIT`
- The `exit_reason` field in every trade record distinguishes `PROFIT_TARGET_TRAILING`, `TIME_EXIT`, and `DATA_END_STILL_OPEN`
- `TIME_EXIT` trades are closed trades, included in realised P&L (they are not censored)
- `DATA_END_STILL_OPEN` trades remain censored (excluded from primary test statistic, per v3/A2 Part 5)

### Section 2: Per-cycle deployment cap

- **MAX_LEVELS = 7**, **MAX_DEPLOYED_MULTIPLIER = 127**, **MAX_DEPLOYED_USDT = 762** (with initial_usdt=6)
- Already enforced by the existing ladder structure; now explicitly documented
- Portfolio-level cap: BLOCKED — not yet implemented; see Phase 5 of research mission specification

---

## Part 4 — Validation splits (unchanged from v3/v4)

*(No change.)*

---

## Part 5 — Null model and statistical requirements (unchanged from v3/v4)

*(No change. Block-shuffle permutation test at N=499, block=48h remains the primary null model. A re-run with v5 simulation parameters (max_cycle_candles=720) is now authorised.)*

---

## Part 6 — What this protocol does not authorise (unchanged from v3/v4)

*(No change.)*

---

## What is now authorised

With v5 hashed and committed:

1. **Re-run of all 5-fold walk-forward results** using v5 simulation parameters (`max_cycle_candles=720`)
2. **Re-run of block-shuffle permutation test** (N≥499, block=48h, same seed=20261017) using v5 simulation — these results supersede the v4/A3 permutation test results for any inferential claim
3. Comparison of v4/A3 results (no time exit) against v5/A4 results (max_cycle_candles=720) as a declared sensitivity analysis — the two runs differ only in this one parameter
4. **Holdout remains sealed throughout**

---

## Governance

**Hash:** SHA-256 of the full UTF-8 text of this document, recorded in `FOREX_PROFILE.protocol_hash` in `bot/quant_core/market_profile.py`

**Supersedes:** FX-DCA-PROTOCOL-1-A3 for Part 3B only; v1, v2, v3, and v4 documents and their hashes remain in the repository unchanged

**Amendment procedure:** any further substantive change requires a new document (FX_DCA_PROTOCOL_2026-10-16_v6.md) with a new hash; prior documents and their hashes remain unchanged
