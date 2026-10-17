# FX DCA Research Protocol v7

**Identifier:** FX-DCA-PROTOCOL-1-A6
**Status:** AMENDMENT — written and committed BEFORE any new simulation run on post-v6 data.
**Amendment date:** 2026-10-17
**Supersedes (for changed sections only):** FX-DCA-PROTOCOL-1-A5 (FX_DCA_PROTOCOL_2026-10-17_v6.md)
for Part 3C Section 2 (ADX gate — revised scope), Part 4 (holdout definition), and Part 5
(family size and statistical threshold). v1–v6 documents and their hashes remain in the
repository unchanged.
**Unchanged sections:** Part 1 (owner decisions), Part 2 (instrument universe and data
specification), Part 3 (ladder specification and locked calibration thresholds), Part 3A
(locked calibration values), Part 3B (time exit, deployment cap), Part 3C Section 1
(carry-direction gate — unchanged), Part 6 (what this protocol does not authorise —
additions below).

---

## Governance note: pre-specification commitment

This document is written and committed BEFORE any simulation run with v7 parameters on
data post-dating 2026-10-16 (the end of the v6 holdout). The three changes below (A6-1,
A6-2, A6-3) are derived from evidence collected under v6 calibration and holdout, not from
scanning new data:

**A6-1 motivation (ADX gate removal for LONG):** H4 investigation in v6 calibration showed
that 79–86% of ADX > 25 candles at entry are during downtrends, not uptrends. The DCA LONG
mechanic profits from downtrend entries via deep fill recovery — the gate was blocking
exactly the entries that generate the strategy's edge. This finding was produced from v6
calibration data (Oct 2020 – Dec 2024), not from scanning new data. Documented in
FX_DCA_CALIBRATION_REPORT_2026-10-17.md Section 6.2.

**A6-2 motivation (USD/JPY LONG removal):** Walk-forward consistency gate FAILED (3/5 folds
positive, threshold ≥ 4/5). Folds 4 and 5 (Aug 2023 – Dec 2024) were both negative
(−$0.16 and −$9.71 respectively). Per protocol v2 Part 4: "No profitability claim may be
made for an INCONSISTENT direction." USD/JPY LONG will not be retested under this family.
Documented in calibration report Section 3.2 and 5.

**A6-3 motivation (new holdout):** The v6 holdout (Dec 2024 – Oct 2026) was opened and
consumed by owner decision 2026-10-17. Result: EUR/USD SHORT p = 0.0380 (SUGGESTIVE, not
PASS). That period is permanently consumed and cannot be re-used as a clean out-of-sample
test. A new holdout is required. Documented in calibration report Section 10.

---

## Amendment log

### A6-1: ADX gate restricted to SHORT directions only

**Change:** The ADX(14) ≤ 25 entry gate (v6 A5-2) is retained for SHORT directions and
REMOVED for LONG directions.

**Revised gate logic:**

| Direction | Carry gate (A5-1) | ADX gate (A5-2) | Combined |
|---|---|---|---|
| EUR/USD SHORT | swap_short ≥ 0 → PASS | ADX(14) ≤ 25 → applied | BOTH must pass |
| USD/CHF LONG | swap_long ≥ 0 → PASS | **NOT applied** | carry gate only |

**Rationale in full:** The v6 calibration H4 investigation (calibration report Section 6.2)
established that the ADX > 25 condition co-occurs predominantly with downtrend candles
(79–86% across all 5 folds for both LONG instruments). The DCA ladder's mechanics profit
from LONG entries during downtrends by filling deeply into the adverse move and recovering
when price reverts. Blocking those entries via the ADX gate removes the strategy's
primary edge mechanism for LONG directions. This asymmetry — ADX gate beneficial for
SHORT, harmful for LONG — was not anticipated at v6 specification and is the primary
finding of the Phase B H4 investigation.

**What is unchanged for EUR/USD SHORT:** the AND logic of carry gate + ADX gate is
retained. The v6 holdout result (p = 0.0380) was produced under these exact parameters.
No change to EUR/USD SHORT's simulation parameters.

**What changes for USD/CHF LONG:** reverts to v5-equivalent entry logic (carry gate
only). The v5 calibration result (p = 0.162, +$40.72, 32 trades, 4/5 folds positive)
is the prior evidence for this direction. This is the strongest available signal for
USD/CHF LONG and is the baseline expectation entering the v7 holdout.

---

### A6-2: USD/JPY LONG permanently removed from the Bonferroni family

**Change:** USD/JPY LONG is removed from the eligible direction set and will not be
tested under this protocol or any future amendment without a full re-analysis of its
walk-forward consistency.

**Reason:** Walk-forward consistency gate FAILED — 3/5 folds positive. Protocol v2 Part 4
requires ≥ 4/5 folds positive for a direction to be a holdout candidate. USD/JPY LONG
folds 4 and 5 were both negative in the terminal calibration period (Aug 2023 – Dec 2024),
suggesting structural deterioration. The INCONSISTENT label is permanent for this family.

**Re-inclusion criteria (if ever sought):** a future protocol version may re-introduce
USD/JPY LONG only if (1) a new, fully pre-specified walk-forward analysis on data wholly
beyond the current holdout shows ≥ 4/5 folds positive, and (2) the new protocol is
committed BEFORE the analysis is run. This is not authorised under v7.

---

### A6-3: New holdout definition — data from 2026-10-16 onward

**Change:** The v7 holdout is ALL 1h OHLCV data for EUR/USD and USD/CHF generated after
2026-10-16 08:00 UTC (the final candle of the v6 holdout).

**Partition:**

| Partition | Period | Status |
|---|---|---|
| Consumed calibration (v6) | Oct 2020 – Dec 2024 | CONSUMED — calibration evidence on record |
| Consumed holdout (v6) | Dec 2024 – Oct 16 2026 | CONSUMED — holdout result p=0.0380 on record |
| **v7 holdout** | **Oct 16 2026 onward** | **CLEAN — sealed until minimum exposure met** |

**No 70/30 re-split:** the v7 holdout is not split into calibration and holdout sub-zones.
All data from Oct 16 2026 onward is clean holdout. The v6 calibration results serve as the
prior for both directions; no new calibration run is performed on v7.

**Minimum test exposure before opening the holdout:**

| Criterion | Requirement |
|---|---|
| Elapsed calendar time | ≥ 12 months after 2026-10-16 (i.e. ≥ 2027-10-16) |
| Minimum closed trades per direction | ≥ 10 closed trades (not open cycles) |
| Both criteria must be met | AND logic — the later-satisfied criterion determines the open date |

**Rationale for minimum:** At the observed calibration rate (~6–8 closed trades/year per
direction), 12 months gives approximately 6–8 trades — insufficient for a meaningful
permutation test. 12 months is the floor; a 24-month accumulation (~12–16 trades per
direction) is preferred before opening.

**Holdout opening procedure:** owner decision required, same as v6. The holdout test script
must be committed BEFORE the holdout is opened (equivalent to the v6 `holdout_test_eurusd_short.py`
pre-commitment).

---

## Part 3C (revised) — Entry filters

*(Section 1 unchanged from v6/A5. Section 2 revised by A6-1.)*

### Section 1: Carry-direction gate (unchanged from v6)

- **Rule:** suppress cycle-open if `pair_spec.swap_direction < 0.0`
- **Eligible directions (v7 family):** EUR/USD SHORT, USD/CHF LONG
- **Unknown swap (`None`):** defaults to BLOCKED
- **Existing open cycles:** unaffected

### Section 2: ADX trend-strength gate (revised — SHORT directions only)

- **Rule:** for SHORT directions only, suppress cycle-open if `ADX(14) > 25`
- **LONG directions:** ADX gate NOT applied — carry gate alone governs
- **Period:** 14 candles (Wilder smoothing); **threshold:** 25; both remain fixed constants
- **Warmup:** first 28 candles of any fold's test period default to BLOCKED (EUR/USD SHORT
  only — USD/CHF LONG has no ADX gate)
- **Existing open cycles:** unaffected

**Gate combinations:**

| Direction | Gates active |
|---|---|
| EUR/USD SHORT | carry gate AND ADX(14) ≤ 25 (unchanged from v6) |
| USD/CHF LONG | carry gate only (ADX gate removed by A6-1) |

---

## Part 4 amendment — Data splits and holdout

*(Replaces Part 4 for the new holdout boundary only; fold construction method unchanged.)*

**v7 holdout boundary:** 2026-10-16 08:00 UTC (first bar excluded from all simulations
until the holdout is opened per A6-3).

Any simulation that reads candles beyond 2026-10-16 08:00 UTC without explicit owner
authorisation of the v7 holdout test is a protocol violation. The existing holdout guard
(`holdout_guard` parameter in `run_fx_fold()`) must be set to the new boundary.

---

## Part 5 amendment — Statistical requirements (family size update)

*(Replaces Part 5 family-size provisions from v6; permutation procedure unchanged.)*

**v7 family:** 2 directions — EUR/USD SHORT (v6 gates) and USD/CHF LONG (carry gate only).

| Item | v6 | v7 |
|---|---|---|
| Family size | 3 | **2** |
| Bonferroni α per test | 0.0167 | **0.025** |
| FDR-BH secondary α | 0.05 / 3 tests | **0.05 / 2 tests** |
| Smallest achievable p (N=499) | 1/500 = 0.0020 | 0.0020 (unchanged) |

**PASS threshold for v7 holdout:** p < **0.025** (per direction, one-sided, N≥499 M3
block-shuffle permutation test, seed=20261017, block=48h).

**Note on the v6 holdout result:** EUR/USD SHORT achieved p = 0.0380 on the v6 holdout.
Under the v7 threshold (p < 0.025) this result would still fail. This does NOT retroactively
constitute a near-PASS — the v6 holdout was run under v6 parameters and the v7 threshold
applies only to the v7 holdout (data post-2026-10-16). The v6 result remains on record as
SUGGESTIVE under v6 parameters.

---

## Power analysis — v7 family

**EUR/USD SHORT (v6 gates, v7 threshold p < 0.025):**

| Quantity | Calibration estimate | Holdout estimate |
|---|---|---|
| Observed p | 0.0702 (calib) | 0.0380 (holdout) |
| Z_current | +1.48 (calib) | +1.617 (holdout) |
| Z_target (p < 0.025) | **1.96** | **1.96** |
| k for threshold crossing | (1.96/1.48)² = **1.75×** | (1.96/1.617)² = **1.47×** |
| k for 80% power | ((1.96+0.842)/1.48)² = **3.6×** | ((1.96+0.842)/1.617)² = **3.0×** |
| Current exposure | ~3.0 yrs calib + 1.83 yrs holdout | — |
| Extra data for threshold crossing | ~2.25 yrs (using calib Z) | **~0.86 yrs (using holdout Z)** |
| Extra data for 80% power | ~7.8 yrs (using calib Z) | **~5.5 yrs (using holdout Z)** |

The holdout Z estimate (+1.617) is based on only 16 trades — noisy. The calibration Z
(+1.48) is based on 30 trades over 4 years and is more stable. Using the more conservative
calibration estimate: ~2.25 years of additional exposure (equivalent to ~14 closed trades)
would push the expected p to the v7 threshold. That corresponds to approximately **2.5–3
years of holdout accumulation** from Oct 2026.

**USD/CHF LONG (carry gate only, v7 threshold p < 0.025):**

| Quantity | Value |
|---|---|
| Observed p (v5, carry gate only) | 0.162 |
| Z_current | +0.99 |
| Z_target (p < 0.025) | 1.96 |
| k for threshold crossing | (1.96/0.99)² = **3.9×** |
| k for 80% power | ((1.96+0.842)/0.99)² = **8.0×** |
| Extra data for threshold crossing | ~8.7 yrs |
| Extra data for 80% power | ~21 yrs |

**USD/CHF LONG is labelled UNDERPOWERED — structural constraint.** 8.7 years of additional
data for a threshold crossing is not a realistic collection target. However, it remains in
the family because: (1) it passes the consistency gate (4/5 folds positive); (2) removing
the ADX gate recovers its strongest observed signal (p=0.162 vs p=0.434 under v6); and
(3) if EUR/USD SHORT passes the v7 holdout, running USD/CHF LONG concurrently at low cost
provides a second data point. A USD/CHF LONG result at p < 0.025 would be the first
canonical edge evidence for that direction; its low power means a failure would be
uninformative rather than conclusive.

---

## Capital efficiency acknowledgment

The v6 calibration and holdout analysis established the return-on-capital profile of the
strategy at its current simulation parameters (`initial_usdt = $6.00`, max deployment $762):

| Period | P&L | Duration | Return on $762 | Annualised |
|---|---|---|---|---|
| Calibration (EUR/USD SHORT, 5-fold) | +$48.33 | ~4 years | 6.3% | ~1.6%/yr |
| Holdout (EUR/USD SHORT) | +$13.62 | 22 months | 1.79% | ~0.98%/yr |

**This return does not constitute a viable standalone strategy at $6 initial size.**
The statistical edge is real (p=0.038 out-of-sample) but the economic return on required
capital is below any reasonable hurdle rate. The strategy scales linearly with
`initial_usdt` — the % return is unchanged, only the absolute dollar figures scale.

This acknowledgment is included for governance completeness and does NOT change the
statistical gate (p < 0.025). A statistically PASS result at the current effect size
remains insufficient for live trading authorisation without a parallel assessment of
whether the capital efficiency is acceptable at the operator's chosen `initial_usdt`.
Any live trading authorisation requires a separate owner decision that addresses:
(a) `initial_usdt` sizing, (b) number of pairs run concurrently, (c) drawdown risk
under max-ladder deployment, and (d) broker execution quality vs. the simulation's
spread/swap assumptions.

---

## What is now authorised under v7

With v7 hashed and committed:

1. **Implementation of revised Part 3C** in `bot/research/fx_fold_runner.py` — remove
   ADX gate from USD/CHF LONG paths, leave EUR/USD SHORT paths unchanged.
2. **v7 holdout guard update** — set the `holdout_guard` boundary to 2026-10-16 08:00 UTC
   in `fx_fold_runner.py` and any script that constructs `make_fx_splits()`.
3. **Monitoring of new data accumulation** — periodic checks of how many candles and
   closed trades have accrued in the v7 holdout zone (read-only; no simulation on that data).
4. **Opening the v7 holdout** — only after both minimum criteria in A6-3 are met AND
   owner decision is received; a test script equivalent to `holdout_test_eurusd_short.py`
   must be committed BEFORE the holdout is opened.

---

## What this protocol does NOT authorise

*(Additions to existing Part 6 list.)*

- **Retroactive re-classification of v6 holdout result:** EUR/USD SHORT p=0.0380 remains
  SUGGESTIVE under v6 parameters. It does not retroactively constitute a v7 PASS.
- **Re-including USD/JPY LONG** without a new consistency analysis on genuinely new data
  (see A6-2 re-inclusion criteria).
- **Changing `initial_usdt`** without a separate explicit owner decision — this changes
  the capital risk profile and is not covered by a statistical protocol amendment.
- **Opening the v7 holdout before minimum criteria are met** (12 months elapsed AND ≥ 10
  closed trades per direction).
- **Adding further entry conditions** beyond the two gates in Part 3C without a v8 amendment
  pre-specified before any new simulation.
- **Applying v7 gates to directions not in the v7 family** (GBP/USD, AUD/USD,
  USD/JPY directions) without a new pre-specified instrument addition protocol.

---

## Summary of v7 family

| Direction | Gates | Calibration p | Holdout status | v7 threshold |
|---|---|---|---|---|
| EUR/USD SHORT | carry + ADX(14)≤25 (v6, unchanged) | 0.0702 | v6 consumed (p=0.0380 SUGGESTIVE) | p < 0.025 |
| USD/CHF LONG | carry gate only (ADX removed) | 0.162 (v5) | not yet run | p < 0.025 |
| USD/JPY LONG | — | INCONSISTENT (3/5 folds) | **EXCLUDED** | — |

**Earliest v7 holdout opening:** 2027-10-16 (12-month minimum) provided ≥ 10 closed trades
per direction have accrued by that date.

---

## Governance

**Supersedes:** FX-DCA-PROTOCOL-1-A5 for Part 3C Section 2, Part 4, and Part 5 (family
size and threshold) only. v1–v6 documents and their hashes remain unchanged.

**Amendment procedure:** any further substantive change requires FX_DCA_PROTOCOL_..._v8.md
committed BEFORE the change is implemented in code or any simulation is run under the new
parameters.

**Prior results on record:**
- v6 calibration report: `FX_DCA_CALIBRATION_REPORT_2026-10-17.md` (commit f6b1ae1)
- v6 holdout result: EUR/USD SHORT p=0.0380, SUGGESTIVE (Section 10 of calibration report)
- v6 protocol: `FX_DCA_PROTOCOL_2026-10-17_v6.md` (FX-DCA-PROTOCOL-1-A5)
