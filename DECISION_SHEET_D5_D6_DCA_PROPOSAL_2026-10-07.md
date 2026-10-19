# Decision Sheet (PROPOSAL, NOT SIGNED): a real answer for the DCA engine

**Status:** PROPOSED by Claude Code on 2026-10-07 for owner review. **Nothing here is in force.** Each item takes
effect only when the owner signs it, edits it, or rejects it. Recommendations are the strictest defensible option.

**Owner request:** "the real answer of the DCA engine on the 50 most liquid coins on Binance".

## 0. Facts that constrain any answer (verified today)

1. **Zone V is not independent for DCA.**
   - `bot/research/dca_engine.py` and its baseline ladder (6 USDT; ×2 … ×64; drops 5/7/10/15/15/10 %) were first
     committed 2026-09-27. That is after almost all of zone V (2024-04-22 → 2026-09-24) was observable.
   - By the strict V-1 reasoning (governance 2026-10-07), a zone-V result for this design is **development evidence,
     never confirmatory**. Only the sealed holdout F is clean.
   - F is evaluated **once, for everything**, from 2027-04-04 (D-8 ledger). **A claimable DCA answer cannot exist
     before 2027-04-04.**
2. **Survivorship.** The 155 local coin files were fetched recently and contain no coins that collapsed or were
   delisted in 2022–24 (FTT, SRM, ANC, MIR, OMG, WAVES … are missing). "Top 50 by today's volume" selects survivors,
   which biases DCA upward, since DCA loses its whole ladder on a coin that never recovers. **D-6 (signed 2026-10-03)
   already requires delisted symbols, from data.binance.vision.**
3. **D-3 limits a family to ≤ 5 pre-registered hypotheses per program per quarter.** 50 per-coin confirmatory tests are
   not allowed. A single pooled hypothesis is allowed.
4. **Compute.**
   - One D-5 statistic = 1.04 s (21k candles), so one test with B = 999 bootstrap paths ≈ 17 min.
   - OPEN-36 size calibration = 3,202 replicate tests per scenario.
     - Single-coin procedure: ≈ 37 CPU-days per scenario.
     - Pooled 50-coin procedure: ≈ 50 × that ≈ 5 CPU-years. **Not feasible in any reasonable budget.**
5. **D-5 (signed) already fixes:**
   - statistic = MTM net return per unit of maximum deployed capital (open positions liquidated at data end);
   - null = stationary block bootstrap;
   - effects = planted mean reversion of 5 % / 10 % amplitude, half-lives 24 / 72 / 168 h;
   - **rule:** run on market data only if calibration shows ≥ 80 % power at the smallest effect, otherwise DCA stays
     descriptive **permanently**.
   - `run_d5_calibration` is deliberately unimplemented until items 1–2 below are fixed.

## 1. D-5 open item 1: the matched-exposure benchmark

**Proposed (strictest):** the benchmark holds a **constant** USDT exposure in the same coin, equal to the DCA run's
**time-weighted average deployed capital**. It is bought at the first open, sold at the last close, and pays the same
fee and slippage rates.
- **Test statistic:** (DCA MTM − benchmark MTM) / DCA maximum deployed capital.
- **Why:** this isolates the only thing DCA claims to add, the *timing* of exposure. A full-capital buy-and-hold would
  compare different amounts of risk. A path-matched benchmark would be DCA itself.

☐ accept ☐ other: ________

## 2. D-5 open item 2: stationary-bootstrap mean block length

**Proposed:** a **24 h** mean block (the D-10 floor), with 6 h and 72 h as reported sensitivities, never used for
selection.
- **Why:** the null must destroy the planted mean reversion. Blocks longer than the smallest planted half-life
  (24 h) would keep part of it inside each block. Blocks shorter than about a day would break intraday
  volatility clustering.
- Calibration then decides honestly. If power at the smallest effect is < 80 %, DCA stays descriptive
  permanently (signed D-5 rule).

☐ accept ☐ other: ________

## 3. D-6: point-in-time "top 50 most liquid" universe

**Proposed (D-6 as signed, plus a top-N rule):**
- **Membership at the test start t:** the USDT spot pair is listed ≥ 90 days before t and still trading at t.
- **Ranking:** by trailing 30-day quote volume computed at t; take the top 50.
- **Exclusions:** stablecoins, leveraged tokens and fiat pairs.
- **Delisted coins are included:** history fetched from data.binance.vision. An open cycle at delisting is
  liquidated at the last traded price and flagged.
- **The universe is frozen at t,** so no later information enters.
- **Dependency:** a one-time archive download for delisted coins (network access to data.binance.vision).

☐ accept (top 50 at t) ☐ use a volume floor instead: ______ ☐ other

## 4. What is tested, and where (D-3)

**Proposed (strictest, feasible):**
- **(a) Development evidence, zone S then zone V,** on the full point-in-time top-50 universe: per-coin D-5
  statistics and the pooled mean, **descriptive only**. Calibration-free, cheap (hours).
- **(b) Confirmatory family for zone F** (single evaluation ≥ 2027-04-04): **at most 5 registered per-coin
  hypotheses**, the five most liquid coins of the point-in-time universe at F's start.
  - Each test: D-5 statistic vs the item-1 benchmark, one-sided, stationary bootstrap (item 2), B = 999.
  - Correction: Holm across the five, at FWER 0.05.
  - The owner writes the economic rationale (not derived from results).
- **(c) Calibration before (b) is usable:** single-coin D-5 procedure, OPEN-36 size gate (3,202 replicates per
  scenario) plus the D-5 power gate (≥ 80 % at amplitude 5 %, all three half-lives).
  - Cost ≈ 37 CPU-days per scenario → **cloud recommended** (pinned `OPENBLAS_CORETYPE`, checksum-verified against
    this PC).
- **Not recommended:** a pooled 50-coin confirmatory test (calibration ≈ 5 CPU-years).

☐ accept (a)+(b)+(c) ☐ descriptive (a) only ☐ other: ________

## 5. Compute route for (c)

**Proposed:** rented cloud machine(s); the owner provides the account and budget. Local alternative ≈ 4–10 weeks of
this PC at full load.

☐ cloud ☐ local ☐ defer calibration

---

**Signed:** ______________________ **Date:** __________

---

## OWNER SIGN-OFF (2026-10-07, in the Claude Code session): "accept all"

1. Benchmark: constant exposure = the DCA run's time-weighted average deployed capital, same coin and costs;
   statistic = (DCA MTM − benchmark MTM) / DCA max deployed capital. ACCEPTED.
2. Stationary-bootstrap mean block 24 h (6 h, 72 h sensitivities only). ACCEPTED.
3. Universe: top 50 USDT spot pairs by trailing 30-day quote volume at the test start, listed ≥ 90 days, still
   trading; stablecoins, leveraged tokens and fiat excluded; delisted coins included (data.binance.vision); frozen at t.
   ACCEPTED.
4. (a) zone S + V development evidence on the top-50 universe (descriptive); (b) ≤ 5 registered per-coin hypotheses
   for zone F, Holm FWER 0.05, owner-written rationale; (c) calibration first (OPEN-36 size + D-5 power). ACCEPTED.
5. Compute: cloud. ACCEPTED.

Signed: Steven Bwenje (owner) — 2026-10-07
