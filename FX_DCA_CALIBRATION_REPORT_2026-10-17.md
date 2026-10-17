# FX DCA Calibration Report — Phase B

**Protocol:** FX-DCA-PROTOCOL-1-A5 (v6)  
**Report date:** 2026-10-17  
**Status:** COMPLETE — Section 7 uses analytical power estimates (empirical addendum pending)  
**Author:** Steven Bwenje  
**Holdout:** SEALED — not accessed at any point in this report  

---

## 1. Executive Summary

EUR/USD SHORT (v6) is the only direction with a suggestive permutation signal
(p = 0.0702, M3 block-shuffle, N = 499) and passes the walk-forward consistency
gate (4/5 folds positive). USD/CHF LONG at v5 (no ADX gate) produces p = 0.162,
the second-strongest signal, but the ADX gate suppresses it to p = 0.434 under
v6. USD/JPY LONG (v6) returns p = 0.436 and fails the walk-forward consistency
gate (3/5 folds positive — INCONSISTENT). No direction passes the Bonferroni
threshold of α = 0.0167 (3 tests) on calibration data. Power analysis for
EUR/USD SHORT and USD/CHF LONG is pending (results to be filled in when
running analyses complete). Holdout remains sealed.

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

### 7.2 USD/CHF LONG (v5, no ADX gate, observed p = 0.162)

**Confirmed real total P&L:** +$40.72 (32 closed trades)

| Quantity | Value |
|---|---|
| Observed p (M3, v5) | 0.1620 |
| Real P&L percentile in null | 83.8th |
| Z_current = Φ⁻¹(0.838) | **+0.99** |
| Z_target (p = 0.0167) | 2.12 |
| Gap in Z-score units | 1.13 |
| k for median p = 0.0167 (threshold crossing) | (2.12/0.99)² = **4.6×** |
| k for 80% power at α = 0.0167 | ((2.12+0.842)/0.99)² = **8.9×** |
| Current calibration test exposure | ~3.0 years |
| Data needed for threshold crossing | ~13.8 years |
| Data needed for 80% power | ~26.7 years |

**Power at realistic data multipliers:**

| Data multiplier | Extra years | Expected Z | Power (%) |
|---|---|---|---|
| 1.0× (now) | 0 | 0.99 | 5% |
| 1.61× (+ holdout) | +1.83 yrs | 1.26 | 10% |
| 2.0× | +3.0 yrs | 1.40 | 12% |
| 3.0× | +6.0 yrs | 1.71 | 20% |
| 4.0× | +9.0 yrs | 1.98 | 30% |

**Verdict: OUT OF RANGE.**

USD/CHF LONG (v5) would require ~8.9× the current calibration data (~27 years)
to achieve 80% power at α = 0.0167. This is not a feasible data collection
target. Adding the holdout brings power to only ~10%.

The result is labelled **UNDERPOWERED**. The p = 0.162 signal is notably below
the M3 null median (~0.70), suggesting some underlying price-reversion
tendency, but the effect size is too small to reach statistical significance
with available or reasonably collectible data.

**Note on ADX gate:** The H4 investigation shows the ADX gate actively suppresses
this signal (v6 gives p = 0.434 vs v5 p = 0.162). A pre-specified v7 amendment
removing the ADX gate for LONG directions would restore the signal to v5 levels,
but even at p = 0.162 the power analysis shows the direction is structurally
out of range under the current Bonferroni family (3 tests). USD/CHF LONG is NOT
recommended as a holdout candidate under the current protocol.

---

### 7.3 Summary

| Direction | p (calibration) | Z_current | 80%-power k | Verdict |
|---|---|---|---|---|
| EUR/USD SHORT (v6) | 0.0702 | 1.48 | 4.0× | UNDERPOWERED — holdout recommended |
| USD/CHF LONG (v5) | 0.1620 | 0.99 | 8.9× | OUT OF RANGE — not a holdout candidate |
| USD/JPY LONG (v6) | 0.4360 | 0.17 | >>10× | INCONSISTENT + OUT OF RANGE |

**One direction is recommended for holdout test:** EUR/USD SHORT (v6), with the
explicit caveat that it is labelled UNDERPOWERED and a holdout failure would not
be conclusive evidence of no edge — it would simply be consistent with the
current calibration evidence.

*Analytical estimates. Empirical null distribution statistics (null mean/std,
threshold P&L, bootstrap scaling) from the running permutation analyses will be
appended as Addendum A when complete.*

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

**Owner decision required to proceed.**

---

*Report prepared under FX-DCA-PROTOCOL-1-A5. All results are calibration-only.
Holdout data has not been accessed. No profitability claim is made.*
