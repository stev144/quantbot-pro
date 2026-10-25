# Cross-Exchange Research — Phase C: Preregistered Test XARB-C1 (design, 2026-10-25)

**Status:** designed, implemented, calibrated. **Not drafted, not signed, not evaluated.**
Research-only: no orders, no signals, no execution. Phase D (execution) is not implemented.

## 1. Why a Phase C at all
Phase B was descriptive. At base-tier taker fees, 0 of 24 directions was ever net-positive during 9.7 hours.
- A description is not evidence, and it can't answer the one question that still matters: **at your real fee tier, do net-positive gaps appear often enough, and last long enough, to justify studying execution?**
- Phase C asks that question once, on fresh data, under rules fixed in advance.

## 2. The registered question (XARB-C1)
**H1.** At the registered taker fees, the share of time during which a 1,000-USDT trade is net-positive meets this test:
- on **any** coin, in **any** direction;
- buying by walking the recorded asks and selling into the recorded bids;
- the net-positive gap must last **at least 2 consecutive one-second samples**;
- the share must exceed **θ = 0.1 %** of the time (about 3.6 s per hour).

| Item | Fixed value | Why |
|---|---|---|
| Unit | one UTC hour; it counts only if ≥ 90 % of its instants have a measurable direction | gaps and data quality vary by hour |
| Outcome Y_h | qualifying instants ÷ eligible instants in hour h | — |
| Episode length | L_min = 2 samples | a one-sample blip can't survive about 75–210 ms feed delay plus order latency |
| Threshold θ | 0.001 | economic floor fixed before any data; below it nothing is worth an execution study |
| Sample | the **first 336 eligible hours** (14 days) recorded **after signing**; then stop | no optional stopping |
| Test | batch means: 7 non-overlapping 48-hour batches, one-sided Student t (6 df), α = 0.025 | robust to hour-to-hour dependence and time-of-day patterns |
| Decision | **PASS** only if the lower confidence bound of mean Y > θ; **INCONCLUSIVE** if observed hourly lag-1 autocorrelation > 0.9; otherwise **FAIL** | the test is calibrated only up to ρ = 0.9 |
| Data guards | sessions starting before signing, or with a different recorder version/schema/interval/depth/venues, are refused | nothing Phase B saw can be reused |
| Once | the result file is written once and never overwritten; a second evaluation is refused | — |
| Tamper check | SHA-256 digest of the registration; signing or evaluating an edited file is refused | — |

**What a PASS means:** only that an *execution-realism* study is justified (latency, inventory, rebalancing between venues). It is not evidence of profit and grants no permission to trade.

**What a FAIL means:** no such evidence at those fees.

## 3. Calibration (G3 evidence, before any data)
The first design failed calibration and was replaced:
- A **percentile moving-block bootstrap** (6-hour blocks, α = 0.05) passed **6.8–14 %** of the time at the null boundary. That is too often.
- **24-hour batches** at α = 0.05 reached 5.8–14.8 %.

The registered design is 48-hour batches, α = 0.025, 336 hours. Simulated AR(1) hourly series with true mean exactly θ:

| Hourly dependence ρ | 0 | 0.5 | 0.8 | 0.9 | 0.95 |
|---|---|---|---|---|---|
| False-pass rate (3,000 sims) | 2.7 % | 2.8 % | 3.2 % | 4.1 % | 5.9 % → guarded by INCONCLUSIVE above 0.9 |

**Power** (ρ = 0.8, standard deviation = θ): 1.00 if the true share is 2θ, 0.75 if it is 1.5θ.

**Known answers:**
- all-zero hours → FAIL;
- constant 3θ → PASS;
- exactly θ → FAIL;
- a high-dependence ramp → INCONCLUSIVE.

**Phase B session as a known-answer run.** It is pre-registration data, so it is **not evidence** and is never evaluated. At base-tier fees it gave 9 eligible hours, all with Y = 0.

Rerun the calibration with `python manage.py research_cross_exchange_phase_c --calibrate`.

## 4. What the owner must provide, and the steps
1. **Your real taker fees** per venue, or confirmation that base tier is what you pay.
   - At base tier (Binance 0.10 %, Kraken 0.80 %, Coinbase 0.60 %), Phase B already implies **FAIL**, so the test would be uninformative.
   - Then run `python manage.py research_cross_exchange_phase_c --draft --fee binance=… --fee kraken=… --fee coinbase=…`.
2. **Review and sign:** `python manage.py research_cross_exchange_phase_c --sign "<your name>"`. Only data recorded **after** this moment counts.
3. **Start a NEW recorder session** after signing, with the same settings (`--interval 1 --depth 10`, all three venues). The session running now started before signing and will be refused.
   - Keep the PC awake. 336 eligible hours means at least 14 days of recording, about 18 GB of disk.
4. **Check progress** with `--status`. When 336 eligible hours exist, run `--evaluate` once.

## 5. Five-gate assessment
| Gate | Status | Evidence |
|---|---|---|
| **G1 Software** | **PASS** | 7 new tests: known answers, batch-means arithmetic, lifecycle guards, one-shot, tamper, l_min counting. Safety tests extended to the new command; 50/50 OK. |
| **G2 Mathematics** | **PASS** | Batch-means t known answer; net gap reuses Phase B's known-answer walk/fee arithmetic |
| **G3 Statistics** | **PASS (design)** | Calibrated false-pass ≤ 4.1 % for ρ ≤ 0.9, with an INCONCLUSIVE guard beyond; power reported; no optional stopping; single test (no multiplicity) |
| **G4 Quant / trading** | **PARTIAL** | Executable sides, depth walk, per-leg fees, persistence requirement. Not modelled: latency fills, inventory/rebalancing, network fees. A PASS only opens that study. |
| **G5 Research integrity** | **BLOCKED (owner)** | Needs your fees and your signature. Fresh post-registration data only; Phase B data excluded by construction. |

## 6. Not implemented
- Phase D: execution or paper execution.
- Any cross-venue order path. This would also conflict with the owner rule that each venue fetches its own data and signal.
