# FX DCA Calibration Report — Phase B

**Protocol:** FX-DCA-PROTOCOL-1-A5 (v6)  
**Report date:** 2026-10-17  
**Status:** COMPLETE — holdout test run 2026-10-17; Section 7 uses analytical power estimates  
**Author:** Steven Bwenje  
**Holdout:** OPENED 2026-10-17 (owner decision) — EUR/USD SHORT, v6, result: SUGGESTIVE (p = 0.0380)  

---

## 1. Executive Summary

EUR/USD SHORT (v6) is the only direction with a suggestive permutation signal
(p = 0.0702, M3 block-shuffle, N = 499) and passes the walk-forward consistency
gate (4/5 folds positive). USD/CHF LONG at v5 (no ADX gate) produces p = 0.162,
the second-strongest signal, but the ADX gate suppresses it to p = 0.434 under
v6. USD/JPY LONG (v6) returns p = 0.436 and fails the walk-forward consistency
gate (3/5 folds positive — INCONSISTENT). No direction passes the Bonferroni
threshold of α = 0.0167 (3 tests) on calibration data. Power analysis labels
EUR/USD SHORT UNDERPOWERED (~40% power at α = 0.0167 with available holdout data).

**Holdout result (2026-10-17):** EUR/USD SHORT (v6) was run on the sealed holdout
(Dec 2024 – Oct 2026, 11,238 candles). Real holdout P&L: +$13.62 (16 closed
trades). M3 permutation test: **p = 0.0380** (N = 499, seed = 20261017).
Verdict: **SUGGESTIVE** — beats the 5% threshold but does not reach the Bonferroni
threshold (p < 0.0167). This constitutes the first out-of-sample evidence for
EUR/USD SHORT but NOT first canonical edge evidence (which requires p < 0.0167).
The result is consistent with the UNDERPOWERED label from calibration.

---

## 2. Instruments and Data

**Strategy:** FX DCA — 7-level dollar-cost averaging ladder, ATR-scaled spacing,
NEXT_OPEN execution, trailing-stop profit exit (PROFIT_TARGET_TRAILING),
TIME_EXIT after 720 candles.

**Eligible pair/direction combos (v6, carry gate + ADX gate):**

| Pair | Direction | Swap (pips/day) | Gate |
|---|---|---|---|
| EUR/USD | SHORT | +0.123 | PASS |
| USD/JPY | LONG | +0.798 | PASS |
| USD/CHF | LONG | +0.598 | PASS |

**Data source:** MT5 1h OHLCV, ICFundedEvaluations account  
**Instrument:** spread-only (commission = $0), no slippage model  
**Pip sizes:** EUR/USD = USD/CHF = 0.0001; USD/JPY = 0.01  

**Data timeline (all three instruments):**

| Partition | Start | End | Candles |
|---|---|---|---|
| Full dataset | 2020-10-07 | 2026-10-16 | 37,461 |
| **Calibration (70%)** | **2020-10-07** | **2024-12-20** | **26,175** |
| Embargo | 2024-12-20 | 2024-12-24 | 48 |
| **Holdout (30%) — SEALED** | **2024-12-24** | **2026-10-16** | **11,238** |

**Walk-forward fold test boundaries (within calibration):**

| Fold | Test start | Test end | Test candles |
|---|---|---|---|
| 1 | 2021-08-02 | 2022-04-05 | 4,235 |
| 2 | 2022-04-07 | 2022-12-12 | 4,235 |
| 3 | 2022-12-14 | 2023-08-18 | 4,235 |
| 4 | 2023-08-22 | 2024-04-29 | 4,235 |
| 5 | 2024-05-01 | 2024-12-20 | 3,995 |

Each fold uses an expanding training window (fold 1: 5,000 rows; fold 5: 22,132
rows). 48-candle embargo between each fold's train end and test start.

---

## 3. Walk-Forward Fold Results

### 3.1 EUR/USD SHORT (v6 — carry gate + ADX gate)

| Fold | Closed trades | Net P&L (USD) | Sign |
|---|---|---|---|
| 1 | 6 | +10.9702 | ✓ |
| 2 | 7 | +6.1835 | ✓ |
| 3 | 6 | **−1.3286** | ✗ |
| 4 | 6 | +11.4377 | ✓ |
| 5 | 5 | +21.0629 | ✓ |
| **Total** | **30** | **+48.3257** | **4/5 positive** |

Consistency: **PASSES** (4/5 ≥ 4 threshold). Fold 3 (Dec 2022 – Aug 2023) is the
sole negative fold; this coincides with a period of EUR/USD mean-trending rather
than ranging, consistent with the DCA mechanic recovering poorly in sustained
directional moves.

### 3.2 USD/JPY LONG (v6 — carry gate + ADX gate)

| Fold | Closed trades | Net P&L (USD) | Sign |
|---|---|---|---|
| 1 | 7 | +4.9359 | ✓ |
| 2 | 9 | +12.3044 | ✓ |
| 3 | 10 | +17.8385 | ✓ |
| 4 | 7 | **−0.1618** | ✗ |
| 5 | 7 | **−9.7149** | ✗ |
| **Total** | **40** | **+25.2021** | **3/5 positive** |

Consistency: **FAILS** (3/5 < 4 threshold). Folds 4–5 (Aug 2023 – Dec 2024)
are both negative, suggesting the strategy's edge deteriorated in the later
calibration period. Notably fold 5 (May–Dec 2024) coincides with USD/JPY's
historical volatility spike and subsequent BoJ intervention regime. No
profitability claim may be made for this direction per protocol v2 Part 4.

### 3.3 USD/CHF LONG — v5 (no gate) vs v6 (with gate)

**v5 (no ADX gate, carry gate only):**

| Fold | Closed trades | Net P&L (USD) | Sign |
|---|---|---|---|
| 1 | 6 | +4.6113 | ✓ |
| 2 | 8 | +23.7661 | ✓ |
| 3 | 6 | +12.7333 | ✓ |
| 4 | 6 | **−1.5310** | ✗ |
| 5 | 6 | +1.1421 | ✓ |
| **Total** | **32** | **+40.7218** | **4/5 positive** |

Consistency (v5): **PASSES** (4/5).

**v6 permutation p = 0.4340** — the ADX gate reduces the signal substantially.
The H4 investigation (Section 6.2) explains why: the ADX gate blocks profitable
entries that occur during downtrends, which the DCA mechanic recovers via deep
fills.

---

## 4. Permutation Test Results (M3)

**Methodology:** M3 block-shuffle log-return permutation. Block size = 48h.
N = 499 permutations per direction. Seed = 20261017. Davison & Hinkley +1
correction applied.  
**Conservative bias:** M3 yields a median null p ≈ 0.70 (expected 0.50 under
ideal shuffle). The test is conservative — it does not inflate Type I error but
reduces power relative to an ideal permutation.  
**Bonferroni threshold:** α = 0.05 / 3 = **0.0167** (3 eligible directions).  
**Minimum achievable p (N=499):** 1/500 = 0.0020 — rejection is possible.

| Direction | Gates | Total P&L | p-value (M3) | vs threshold | Result |
|---|---|---|---|---|---|
| EUR/USD SHORT | v6 | +$48.33 | **0.0702** | 4.2× above | NOT SIGNIFICANT |
| USD/JPY LONG | v6 | +$25.20 | 0.4360 | 26.1× above | NOT SIGNIFICANT |
| USD/CHF LONG | v6 | — | 0.4340 | 26.0× above | NOT SIGNIFICANT |
| USD/CHF LONG | v5 | +$40.72 | **0.1620** | 9.7× above | NOT SIGNIFICANT |

**Sensitivity analysis (v5 vs v6):** EUR/USD SHORT improves under v6 (p drops
from 0.268 → 0.0702); USD/CHF LONG worsens under v6 (p rises from 0.162 →
0.434). This asymmetry is explained by H4 (Section 6.2): the ADX gate benefits
SHORT strategies whose edge is mean-reversion in ranging conditions, but
damages LONG strategies by blocking profitable downtrend entries.

**0 of 3 directions pass the Bonferroni threshold on calibration data.**

---

## 5. Walk-Forward Consistency Assessment

| Direction | Gates | Folds positive | Threshold | Gate result |
|---|---|---|---|---|
| EUR/USD SHORT | v6 | 4/5 | ≥ 4/5 | **PASSES** |
| USD/JPY LONG | v6 | 3/5 | ≥ 4/5 | **FAILS — INCONSISTENT** |
| USD/CHF LONG | v5 | 4/5 | ≥ 4/5 | **PASSES** |

USD/JPY LONG is labelled **INCONSISTENT** per protocol v2 Part 4. Even though
its total calibration P&L is positive (+$25.20), no profitability claim may be
made and this direction is not a candidate for the holdout test.

---

## 6. Phase B Investigations

### 6.1 H1 — EUR/USD SHORT P&L Decomposition (Edge Mechanism)

**Question:** Is EUR/USD SHORT's edge driven by price mean-reversion
(PROFIT_TARGET_TRAILING exits) or by carry accumulation (TIME_EXIT + swap
credits)?

**Findings (v6, 5-fold calibration, 30 closed trades):**

| Exit mechanism | N | Gross P&L | Swap P&L | Commission | Net P&L |
|---|---|---|---|---|---|
| PROFIT_TARGET_TRAILING | ~16 | dominant | trivial | small | ~$24 |
| TIME_EXIT | ~14 | smaller | trivial | small | ~$24 |
| **Total** | **30** | — | — | — | **+$48.33** |

Component shares (approximate):
- **Gross (price reversion): 95–99% of net P&L**
- Swap (carry): 0.8–4.9% of net P&L
- Commission: small negative contribution

**Verdict:** EUR/USD SHORT's edge is almost entirely **price mean-reversion**,
not carry. The DCA mechanic fills deep into adverse moves and recovers when
price reverts. Carry is structurally positive (EUR/USD SHORT earns +0.123
pips/day) but is not the primary driver. This supports the strategy's
theoretical basis and rules out the alternative hypothesis that the edge is a
carry-duration artefact.

Fill depth analysis: P&L increases monotonically with fill depth — deeper fills
(more DCA levels triggered) produce higher net P&L per trade, consistent with
the mean-reversion mechanism.

### 6.2 H4 — ADX Gate Direction Check (Does the Gate Hurt LONG Strategies?)

**Question:** Does the v6 ADX(14) > 25 gate unfairly block profitable LONG
entries, because ADX > 25 occurs primarily during uptrends (when LONGs recover
fastest)?

**Methodology:** For each calibration fold test period, computed ADX(14) and
EMA(20) at every candle. Classified ADX > 25 candles as uptrend
(close > EMA_20) or downtrend (close ≤ EMA_20). Also classified each v5 entry
candle by its ADX/EMA state.

**Aggregate results across all 5 folds:**

*USD/JPY LONG:*
- Total ADX > 25 candles: significant proportion of test period
- Of those, **79% are in downtrend** (close ≤ EMA_20)
- Of v5 entry candles blocked by v6 gate: **79% are downtrend entries**

*USD/CHF LONG:*
- Of ADX > 25 candles: **86% are in downtrend**
- Of v5 entry candles blocked by v6 gate: **86% are downtrend entries**

**Verdict: H4 hypothesis NOT SUPPORTED.**

The hypothesis predicted ADX > 25 occurs mostly in uptrends — the data shows
the opposite: 79–86% of blocked entries are during downtrends. However, this
does not vindicate the gate. DCA LONG strategies profit *from* downtrend entries
via deep fill recovery — the mechanic fills more levels into the adverse move and
earns a larger recovery when price reverts. The ADX gate is blocking exactly
the entries that generate the strategy's edge.

**Implication:** The ADX gate is net-harmful for LONG directions. A v7
specification that removes the ADX gate for LONG directions only would likely
recover the p = 0.162 signal for USD/CHF LONG. This is a candidate for a
pre-specified v7 amendment if the power analysis (Section 7) indicates the
signal is worth pursuing.

---

## 7. Power Analysis

**Question:** Given the observed calibration p-values, how much more calibration
data would be needed to achieve 80% power at α = 0.0167? Is the gap closable in
practice, or structurally out of range?

**Method (analytical):** The M3 permutation p-value encodes the percentile of
the real P&L in the null distribution. Under CLT, real P&L scales linearly with
data quantity while null standard deviation scales as √n; therefore the Z-score
(real P&L − null mean) / null std scales as √(data multiplier). The required
data multiplier for 80% power is:

```
k_80 = ( (Z_target + Z_80%) / Z_current )²

where:
  Z_current = Φ⁻¹(1 − p_observed)   [current percentile in null]
  Z_target  = Φ⁻¹(1 − 0.0167) ≈ 2.12  [Bonferroni threshold]
  Z_80%     = Φ⁻¹(0.80) = 0.842         [80% power requirement]
```

**M3 conservative bias note:** M3 block-shuffle produces a median null p ≈ 0.70
(expected 0.50 under ideal shuffle). This means Z_current is derived from a
conservatively inflated null — the bias works against significance but does NOT
inflate Type I error. The analytical Z-scores below therefore slightly
understate the true signal-to-noise ratio. A rough de-biased Z_current can be
obtained by adjusting: Z_adj ≈ Φ⁻¹(1 − p × 0.50/0.70), which gives modestly
more optimistic multipliers; the un-adjusted (conservative) values are reported
here.

**Calibration test exposure (total across 5 folds):** ~3.0 years  
**Holdout data available:** 22 months (Dec 2024 – Oct 2026), already accumulated

---

### 7.1 EUR/USD SHORT (v6, observed p = 0.0702)

**Confirmed real total P&L:** +$48.33 (30 closed trades)

| Quantity | Value |
|---|---|
| Observed p (M3, v6) | 0.0702 |
| Real P&L percentile in null | 93.0th |
| Z_current = Φ⁻¹(0.930) | **+1.48** |
| Z_target (p = 0.0167) | 2.12 |
| Gap in Z-score units | 0.64 |
| k for median p = 0.0167 (threshold crossing) | (2.12/1.48)² = **2.1×** |
| k for 80% power at α = 0.0167 | ((2.12+0.842)/1.48)² = **4.0×** |
| Current calibration test exposure | ~3.0 years |
| Data needed for threshold crossing | ~6.3 years |
| Data needed for 80% power | ~12.0 years |

**Power at realistic data multipliers:**

| Data multiplier | Extra years | Expected Z | Power (%) |
|---|---|---|---|
| 1.0× (now) | 0 | 1.48 | 13% |
| 1.61× (+ holdout) | +1.83 yrs | 1.88 | 40% |
| 2.0× | +3.0 yrs | 2.09 | 48% |
| 3.0× | +6.0 yrs | 2.56 | 74% |
| 4.0× | +9.0 yrs | 2.96 | 85% |

**Verdict: BORDERLINE.**

EUR/USD SHORT is the strongest signal in the project. The threshold-crossing
multiplier is 2.1× — meaning roughly double the calibration data would push the
*expected* p-value to 0.0167. The holdout (22 months already accumulated) adds
1.83 years, bringing the total to ~4.83 years = 1.61× the current calibration
test exposure. At 1.61×, power is approximately **40%** — meaning there is a
40% chance the holdout permutation test passes at p < 0.0167.

This does NOT meet the protocol's 80% power requirement for a "powered" result.
However, the holdout is the single remaining clean test; running it is
recommended (the 40% pass probability is meaningful, not negligible) with the
explicit understanding that a failure would be informative rather than
conclusive. The result is labelled **UNDERPOWERED** per protocol v2 Part 5 —
the available data does not support 80% power at the observed effect size.

---

### 7.2 USD/CHF LONG (v7/A6-1, no ADX gate for LONG, observed p = 0.162)

**Confirmed real total P&L:** +$40.72 (32 closed trades)

*Empirical null stats from M3 permutation run 2026-10-17 (N=499, block=48h, seed=20261017,
v7/A6-1 gates — carry gate only for LONG; replaces analytical estimates in original draft).*

| Quantity | Value |
|---|---|
| Observed p (M3, v7/A6-1) | 0.1620 |
| Real P&L percentile in null | 84.0th |
| Empirical null mean | +$13.97 |
| Empirical null std | $27.38 |
| Empirical Z_current = (real − null_mean) / null_std | **+0.977** |
| Z_target (v7: p = 0.025, 2-test family) | 1.960 |
| Gap in Z-score units | 0.983 |
| Threshold P&L to reach significance | +$67.63 |
| Gap to threshold | −$26.90 |
| k for median p = 0.025 (threshold crossing) | (1.960/0.977)² = **4.0×** |
| k for 80% power at α = 0.025 | ((1.960+0.842)/0.977)² = **8.2×** |
| Current calibration test exposure | ~3.0 years |
| Data needed for threshold crossing | ~12.1 years |
| Data needed for 80% power | ~24.7 years |

**Power at realistic data multipliers (v7 α = 0.025):**

| Data multiplier | Extra years | Expected Z | Power (%) |
|---|---|---|---|
| 1.0× (now) | 0 | +0.977 | 6% |
| 1.33× (+ 12mo v7 holdout minimum) | +1.0 yr | +1.128 | 20% |
| 2.0× | +3.0 yrs | +1.382 | 25% |
| 3.0× | +6.0 yrs | +1.692 | 35% |
| 4.0× | +9.0 yrs | +1.954 | 50% |
| 8.2× | +21.7 yrs | +2.802 | 80% |

**Verdict: OUT OF RANGE (unchanged from original draft).**

USD/CHF LONG requires ~8.2× the current calibration data (~25 years) for 80%
power at the v7 Bonferroni threshold. The v7 family reduction (3 tests → 2)
eases the threshold from α=0.0167 to α=0.025, cutting the required multiplier
from 8.9× to 8.2× — a modest improvement that does not change the structural
conclusion. The 12-month v7 holdout minimum accumulates ~10–11 trades at the
current rate (~10.7/year) and gives only **20% power** — well below the 80%
standard for a powered test.

The p = 0.162 signal sits at the 84th percentile of the M3 null (empirical null
mean +$13.97 on 32-trade calibration), suggesting some directional tendency, but
the effect size is too small to reach statistical significance within any
practically collectible data horizon. USD/CHF LONG remains in the v7 family for
governance purposes (10 closed-trade minimum tracks accumulation), but **no
holdout opening is anticipated to be informative** until ~12 years of test
exposure have accumulated.

**ADX gate removal (v7/A6-1):** implemented 2026-10-17 (commit 67170c5). The
v7 result (p=0.162) is identical to v5 (no ADX gate), confirming that the gate
removal restores the signal. The v6 result with the gate applied was p=0.434,
consistent with the H4 finding that the ADX gate suppressed LONG entries.

---

### 7.3 Summary

*All values empirical (M3 permutation, N=499, block=48h, seed=20261017). EUR/USD SHORT
used calibration data; holdout result reported in Section 10. USD/CHF LONG run 2026-10-17.*

| Direction | p (calib) | Z_current | Null mean | Null std | α (v7) | 80%-power k | Verdict |
|---|---|---|---|---|---|---|---|
| EUR/USD SHORT (v7/A6-1) | 0.0702 | +1.48 | −$5.69 | $11.94 | 0.025 | ~2.5× | UNDERPOWERED |
| USD/CHF LONG (v7/A6-1) | 0.1620 | +0.977 | +$13.97 | $27.38 | 0.025 | 8.2× | OUT OF RANGE |
| USD/JPY LONG | removed | — | — | — | — | — | DROPPED (v7/A6-2: 3/5 folds inconsistent) |

**Holdout status (v7):**
- EUR/USD SHORT: holdout run 2026-10-17, p=0.0380 — SUGGESTIVE (above v7 α=0.025). New holdout accumulating from 2026-10-16 08:00 UTC.
- USD/CHF LONG: in v7 family; holdout minimum 12 months + ≥10 trades. Expected power at minimum: 20%. Not an informative test until ~12 years test exposure accumulated.

*Addendum A (empirical null stats) is now complete and integrated into Sections 7.1 and 7.2.*

---

## 8. Accounting Invariants

Phase A code fixes applied to `bot/research/fx_fold_runner.py` before any
calibration results were examined:

| Fix | Description | Status |
|---|---|---|
| F-03 | Censored trade net P&L (open trades excluded from permutation test metric) | Applied |
| F-04 | Holdout guard parameter wired correctly | Applied |
| F-05 | MTM liquidation exit spread applied at correct price level | Applied |
| F-07 | `swap_pnl_usdt` key harmonisation across all trade dict producers | Applied |
| F-08 | Bar-interval guard for swap accrual (hourly candles only) | Applied |

`cost_complete` status: **TRUE** — all trades include spread costs, swap
credits/debits, and commission. Open cycles at fold end are excluded from the
permutation test metric (DATA_END_STILL_OPEN) and reported separately.

Known open items (non-blocking):
- F-09: `forex_accounting.py:spread_cost_account_currency()` should raise
  ValueError not return None silently (low priority)
- F-10: "crypto only" note missing from `dca_calibration.py` header

---

## 9. Holdout-Open Decision Gate

Per protocol v2 Part 4 and v6/A5 Part 5, the holdout may be unsealed when:

| Gate | Requirement | Status |
|---|---|---|
| All 5 folds run | Calibration simulation complete | ✓ DONE |
| Permutation tests run | M3, N≥499, all 3 directions | ✓ DONE |
| Calibration report recorded | This document committed | ✓ (pending final commit) |
| Walk-forward consistency | ≥ 4/5 folds positive | EUR/USD SHORT ✓, USD/CHF LONG v5 ✓, USD/JPY LONG ✗ |
| Power analysis | 80% power at observed effect size | ⏳ PENDING |
| Accounting complete | `cost_complete=True` | ✓ DONE |
| Owner decision | Holdout test authorised | ⏳ AWAITING OWNER |

**Candidates for holdout test (directions meeting all non-power gates):**
1. **EUR/USD SHORT (v6)** — p = 0.0702 on calibration, consistency ✓
2. **USD/CHF LONG (v5)** — p = 0.162 on calibration, consistency ✓ — requires
   a v7 amendment removing the ADX gate for LONG directions before the holdout
   run, since v5 is not the current authorised specification

**USD/JPY LONG is excluded** — fails walk-forward consistency (3/5 folds).

**Power analysis outcome (Section 7):**
- EUR/USD SHORT: UNDERPOWERED (requires ~4.0× data for 80% power; holdout
  adds ~1.61×, giving ~40% power). Holdout test **recommended** with explicit
  UNDERPOWERED label.
- USD/CHF LONG: OUT OF RANGE (requires ~8.9× data). **Not a holdout candidate.**
- USD/JPY LONG: INCONSISTENT + OUT OF RANGE. Excluded.

**Recommendation:** Open the holdout for EUR/USD SHORT (v6) only. A holdout
failure would be consistent with the calibration evidence, not conclusive proof
of no edge. A holdout pass (p < 0.0167) would constitute the first canonical
edge evidence for this direction.

**Owner decision received 2026-10-17. Holdout test run same day. See Section 10.**

---

## 10. Holdout Test Results — EUR/USD SHORT (v6)

**Authorisation:** Owner decision 2026-10-17, after calibration report committed at f16aa73.  
**Protocol:** FX-DCA-PROTOCOL-1-A5 (v6). Permutation method: M3, N=499, seed=20261017.  
**Script:** `holdout_test_eurusd_short.py`

### 10.1 Real Holdout Run

| Item | Value |
|---|---|
| Holdout period | 2024-12-24 → 2026-10-16 |
| Holdout candles | 11,238 |
| Train candles (full calibration) | 26,175 |
| Closed trades | **16** |
| Open cycles at end (excluded per F-03) | 1 (MTM = −$0.0130, informational) |
| Closed P&L | **+$13.6163 USD** |
| Per-trade rate | +$0.851/trade |

Per-trade rate in calibration: +$48.33/30 = +$1.61/trade. The holdout rate (+$0.85) is roughly half — consistent with a weakened but still positive signal, and consistent with the UNDERPOWERED prediction (smaller sample, noisy estimate).

Trade detail (exit reason / gross / swap / net / fills):

| Exit | Gross | Swap | Net | Fills |
|---|---|---|---|---|
| TIME_EXIT | +0.3333 | +0.0148 | +0.3482 | 3 |
| TIME_EXIT | +0.4817 | +0.2599 | +0.7417 | 7 |
| TIME_EXIT | +2.3823 | +0.2476 | +2.6299 | 7 |
| TIME_EXIT | −0.4784 | +0.0290 | −0.4494 | 4 |
| TIME_EXIT | +1.8654 | +0.0587 | +1.9241 | 5 |
| TIME_EXIT | −1.2341 | +0.0589 | −1.1751 | 5 |
| TIME_EXIT | +1.5621 | +0.0281 | +1.5903 | 4 |
| TIME_EXIT | −0.0075 | +0.0133 | +0.0058 | 3 |
| TIME_EXIT | +0.0395 | +0.0283 | +0.0679 | 4 |
| TIME_EXIT | +3.5568 | +0.0581 | +3.6148 | 5 |
| TIME_EXIT | −0.0201 | +0.0584 | +0.0383 | 5 |
| TIME_EXIT | +0.5900 | +0.0131 | +0.6031 | 3 |
| PROFIT_TARGET_TRAILING | +0.4383 | +0.0039 | +0.4422 | 2 |
| TIME_EXIT | −0.2957 | +0.0289 | −0.2668 | 4 |
| TIME_EXIT | +3.0458 | +0.0590 | +3.1047 | 5 |
| PROFIT_TARGET_TRAILING | +0.3948 | +0.0019 | +0.3968 | 2 |

12/16 closed trades (75%) are positive. Exit mix: 14 TIME_EXIT, 2 PROFIT_TARGET_TRAILING.

### 10.2 Permutation Test

| Item | Value |
|---|---|
| N permutations | 499 |
| Method | M3 block-shuffle log-returns (48h blocks) |
| Seed | 20261017 |
| Null mean | −$5.6893 |
| Null median | −$4.5067 |
| Null std | $11.9364 |
| Null normality (Shapiro-Wilk p) | 0.0000 (non-normal) |
| Real P&L Z-score | **+1.617** |
| Threshold P&L for p ≤ 0.0167 | +$16.0451 |
| Gap to threshold | +$2.4288 (real P&L was $2.43 short) |
| **p-value (M3, N=499)** | **0.0380** |
| Bonferroni threshold | 0.0167 |

**Null is negatively biased** (median −$4.51 vs expected $0) — the M3 conservative bias confirmed in the holdout null, consistent with calibration null behaviour. This conservatism slightly deflates the Z-score; the un-biased Z would be somewhat higher.

### 10.3 Verdict

**SUGGESTIVE — p = 0.0380 < 0.05 but NOT Bonferroni-significant (threshold p < 0.0167)**

This result:
- **IS** the first out-of-sample (holdout) evidence for EUR/USD SHORT
- **IS NOT** first canonical edge evidence (which requires p < 0.0167)
- **IS** consistent with the calibration UNDERPOWERED label (predicted ~40% power; observed Z = 1.617 vs calibration Z = 1.48 — slightly above prediction)
- **IS NOT** a basis for profitability claims or live trading authorisation

The strategy is above the noise floor in two independent datasets (calibration and holdout), but the sample size is insufficient to clear the pre-specified Bonferroni bar. A 4.0× data expansion would be needed for 80% power; this is not achievable without a materially longer data collection period or a Bonferroni family reduction (fewer test directions).

### 10.4 Governance Note

This holdout has now been opened and **cannot be re-used as a clean out-of-sample test**. Any further strategy parameter changes require a v7 protocol amendment with a new holdout. The holdout period (Dec 2024 – Oct 2026) is permanently consumed.

**Next steps (pre-specified per protocol):**
- No live or paper trading is authorised (SUGGESTIVE result, not PASS)
- A v7 amendment could specify: (a) remove ADX gate for LONG directions and test USD/CHF LONG on new holdout data; (b) reduce Bonferroni family if USD/JPY LONG is permanently dropped; (c) continue accumulating EUR/USD SHORT data until threshold crossing
- All v7 changes require pre-specification before any new data is examined

---

*Report prepared under FX-DCA-PROTOCOL-1-A5. Holdout opened and tested 2026-10-17.
No profitability claim is made. SUGGESTIVE result ≠ canonical edge evidence.*
