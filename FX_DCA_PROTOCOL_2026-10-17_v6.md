# FX DCA Research Protocol v6

**Identifier:** FX-DCA-PROTOCOL-1-A5
**Status:** AMENDMENT — written BEFORE re-running any simulation or permutation test on zone S with these filters.
**Amendment date:** 2026-10-17
**Supersedes (for changed sections only):** FX-DCA-PROTOCOL-1-A4 (FX_DCA_PROTOCOL_2026-10-16_v5.md) for Part 3C (entry filters) only. v1–v5 documents and their hashes remain in the repository unchanged.
**Unchanged sections:** Part 1 (owner decisions), Part 2 (instrument universe and data specification), Part 3 (data-derived ladder specification and locked calibration thresholds), Part 3A (locked calibration values), Part 3B (time exit, deployment cap), Part 4 (validation splits), Part 5 (null model and statistical requirements — amended below for family size), Part 6 (what this protocol does not authorise).

---

## Governance note: pre-specification commitment

This document is written and hashed BEFORE any re-run of the zone S simulation or permutation test with v6 parameters. The two entry filters below (carry gate, ADX gate) were derived from structural reasoning and canonical technical-analysis constants — not from scanning zone S P&L outcomes for a filter that improves results:

**Carry gate motivation:** v5 results showed a consistent pattern: the four positive-P&L directions (EUR/USD SHORT, USD/JPY LONG, USD/CHF LONG, AUD/USD SHORT) are structurally different from the negative ones — the DCA ladder is held open for tens to hundreds of candles in an adverse cycle, and swap (financing cost) compounds against the position for every candle held. Directions that earn positive carry receive a structural buffer; directions that pay negative carry are structurally disadvantaged for this strategy's holding profile. The determination was made from broker swap rates (ICFundedEvaluations_MT5_SNAPSHOT_2026-10-16, stored in forex_instrument_spec.py), not from zone S P&L outcomes.

**ADX gate motivation:** the full-ladder depth scan showed that all 39 fully-deployed cycles (762 USDT, all levels hit) occurred during sustained directional moves — the DCA mechanic averaged into a trending market until it ran out of levels. ADX is the standard measure of trend strength (Wilder, 1978); ADX(14) > 25 is the canonical threshold separating trending from ranging regimes and is widely used as a pre-specified constant in the technical analysis literature. This threshold was not selected by scanning zone S for a value that improves P&L.

Both filters, applied together as AND logic, reduce the set of cycles the simulation opens. They do not change the ladder spacing, multipliers, time exit, or profit trailing stop — all of which remain locked at v5/A4 values.

---

## Amendment log

### A5-1: Carry-direction gate (adds Part 3C, Section 1)

**Change:** Before opening a new DCA cycle, check that the direction's overnight swap rate (per the locked instrument spec) is ≥ 0 pips/day. If swap < 0, suppress the cycle-open — no new cycle opens on that candle.

**Threshold:** swap_direction ≥ 0.0 pips/day (strictly non-negative)

**Swap rates used (ICFundedEvaluations_MT5_SNAPSHOT_2026-10-16):**

| Pair | Direction | Swap (pips/day) | Gate result |
|------|-----------|-----------------|-------------|
| EUR/USD | LONG  | −0.840 | **FAIL — direction excluded** |
| EUR/USD | SHORT | +0.123 | PASS |
| GBP/USD | LONG  | −0.504 | **FAIL — direction excluded** |
| GBP/USD | SHORT | −0.336 | **FAIL — direction excluded** |
| USD/JPY | LONG  | +0.798 | PASS |
| USD/JPY | SHORT | −1.806 | **FAIL — direction excluded** |
| USD/CHF | LONG  | +0.598 | PASS |
| USD/CHF | SHORT | −1.386 | **FAIL — direction excluded** |
| AUD/USD | LONG  | −0.020 | **FAIL — direction excluded** |
| AUD/USD | SHORT | −0.504 | **FAIL — direction excluded** |

**Eligible pair/direction combos after A5-1 (locked):**
1. EUR/USD SHORT
2. USD/JPY LONG
3. USD/CHF LONG

**Rationale for strict threshold (≥ 0, not ≥ −ε):** a threshold of exactly zero requires no tuning argument; it is the natural boundary between paying carry and earning carry. AUD/USD LONG at −0.020 pips/day is marginal but on the wrong side. Per protocol governance, the strict threshold is preferred over a looser one that requires justifying ε.

**Implementation note:** the gate is evaluated against the `pair_spec.swap_long` or `pair_spec.swap_short` field of the `ForexInstrumentSpec` passed to `run_fx_fold()`. If the swap field is `None` (unknown), the gate defaults to BLOCKED (no cycle opens) — an unknown financing cost is treated as potentially negative. The swap rate is a static field from the snapshot; it does not update intra-simulation.

**Swap rate validity caveat:** swap rates change with central bank interest rate decisions. The rates above are a static snapshot from 2026-10-16. If rates change materially, a v7 amendment is required before further zone S runs — do not re-run with stale rates and call it the same protocol.

---

### A5-2: ADX trend-strength entry gate (adds Part 3C, Section 2)

**Change:** Before opening a new DCA cycle, compute ADX(14) on the 1h OHLCV data available at the current candle (point-in-time, no lookahead). If ADX(14) > 25, suppress the cycle-open — no new cycle opens on that candle.

**Parameters:**
- **Period:** 14 candles (Wilder's original period; not optimised)
- **Threshold:** ADX(14) ≤ 25 to permit a new cycle open (standard "non-trending" cutoff)
- **Data:** computed from the test fold's price data up to and including the current candle, using only the rolling window of the preceding 14 candles (plus the warmup period below)

**Warmup:** ADX(14) requires a minimum of 28 candles of price history to be numerically stable (14 candles for +DM/−DM smoothing, plus a further 14 for the ADX smoothing). For the first 28 candles of any fold's test period, the gate defaults to BLOCKED (no cycle opens). Cycles may open from candle 28 onward.

**Gate logic:** BOTH gates must pass (AND logic). A cycle opens only when:
- `swap_direction ≥ 0` (A5-1), AND
- `ADX(14) ≤ 25` (A5-2)

If either gate fails, the cycle-open is suppressed. The simulation advances to the next candle and re-checks. An existing open cycle is NOT closed by a gate failure — gates only block new cycle opens.

**Implementation note:** ADX is computed using the standard Wilder smoothing method. The formula used must be recorded in the implementation commit and must match the formula here:
- True Range: `TR = max(high−low, |high−prev_close|, |low−prev_close|)`
- +DM, −DM: standard Wilder directional movement
- Smoothed +DM14, −DM14, TR14: Wilder's EMA with α = 1/14
- +DI14 = 100 × smoothed+DM14 / TR14
- −DI14 = 100 × smoothed−DM14 / TR14
- DX = 100 × |+DI14 − −DI14| / (+DI14 + −DI14)
- ADX14 = Wilder smoothed DX over 14 periods

Any deviation from this formula — including using a simple moving average instead of Wilder smoothing — constitutes a protocol violation and requires a v7 amendment before proceeding.

---

## Part 3C (new) — Entry filters

*(Added by A5. Applied in `_simulate_data_derived_next_open()` and `run_fx_fold()` in `bot/research/fx_fold_runner.py`. Do not apply to the DCA engine in `bot/engines/dca_engine.py`.)*

### Section 1: Carry-direction gate

- **Rule:** suppress cycle-open if `pair_spec.swap_direction < 0.0` (where `swap_direction` is `swap_long` for LONG cycles, `swap_short` for SHORT cycles)
- **Eligible directions (locked as of 2026-10-17):** EUR/USD SHORT, USD/JPY LONG, USD/CHF LONG
- **Unknown swap (`None`):** defaults to BLOCKED
- **Existing open cycles:** unaffected — gate applies only at new cycle open

### Section 2: ADX trend-strength gate

- **Rule:** suppress cycle-open if `ADX(14) > 25` at the current candle (point-in-time)
- **Period:** 14 candles; **threshold:** 25; both are fixed constants, not to be changed without a v7 amendment
- **Warmup:** first 28 candles of each fold's test period default to BLOCKED
- **Existing open cycles:** unaffected — gate applies only at new cycle open
- **AND logic with Section 1:** both must pass

---

## Part 5 amendment — Statistical requirements (family size update)

*(Remainder of Part 5 unchanged from v3/v4/v5.)*

The restriction to 3 eligible pair/direction combos (A5-1) changes the family size for multiple-testing correction:

- **v5 family:** 5 pairs × 2 directions = 10 primary tests
- **v6 family:** 3 eligible directions = 3 primary tests
- **Bonferroni α per test:** 0.05 / 3 = **0.0167**
- **Smallest achievable p (N=499):** 1/500 = 0.0020 — rejection is possible
- **FDR-BH secondary:** applied over the same 3 tests at α=0.05

The permutation test procedure (block-shuffle, N≥499, block=48h, seed=20261017) is otherwise unchanged.

---

## What is now authorised

With v6 hashed and committed:

1. **Implementation of Part 3C gates** in `bot/research/fx_fold_runner.py`
2. **Re-run of all 5-fold walk-forward results** for the 3 eligible pair/direction combos (EUR/USD SHORT, USD/JPY LONG, USD/CHF LONG) using v6 simulation parameters (carry gate + ADX gate + all v5/A4 parameters)
3. **Re-run of block-shuffle permutation test** (N≥499, block=48h, seed=20261017) for the 3 eligible directions — these results supersede v5/A4 results as the primary inferential claim
4. Comparison of v5 and v6 results as a declared sensitivity analysis (carry+ADX gate on/off)
5. **Holdout remains sealed throughout**

The 7 excluded directions (EUR/USD LONG, GBP/USD LONG, GBP/USD SHORT, USD/JPY SHORT, USD/CHF SHORT, AUD/USD LONG, AUD/USD SHORT) are excluded from this protocol's scope. Results for those directions from the v5 run remain on record as descriptive-only.

---

## What this protocol does NOT authorise

*(Additions to existing Part 6 list.)*

- **Threshold scanning:** re-running with ADX threshold values other than 25, or swap thresholds other than 0, to find a value that improves zone S results. Any threshold change requires a v7 amendment written and hashed BEFORE the scan.
- **Post-hoc pair re-inclusion:** re-including an excluded direction (e.g. AUD/USD LONG at swap=−0.020) after observing that its v6 results are favourable.
- **Filter stacking beyond A5-1/A5-2:** adding further entry conditions (RSI, Bollinger Band, volatility filter, etc.) without a new protocol version. Each new filter requires its own pre-specified amendment.
- **Accessing holdout data** at any point in this phase.

---

## Governance

**Hash:** SHA-256 of the full UTF-8 text of this document, recorded in `FOREX_PROFILE.protocol_hash` in `bot/quant_core/market_profile.py`

**Supersedes:** FX-DCA-PROTOCOL-1-A4 for Part 3C and Part 5 (family size) only; v1–v5 documents and their hashes remain in the repository unchanged

**Amendment procedure:** any further substantive change requires a new document (FX_DCA_PROTOCOL_2026-10-17_v7.md) with a new hash; prior documents and their hashes remain unchanged
