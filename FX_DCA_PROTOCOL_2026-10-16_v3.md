# FX DCA Research Protocol v3

**Identifier:** FX-DCA-PROTOCOL-1-A2
**Status:** AMENDMENT — written and hashed BEFORE any excursion or threshold results are examined.
**Amendment date:** 2026-10-16
**Supersedes (for changed sections only):** FX-DCA-PROTOCOL-1-A1 (FX_DCA_PROTOCOL_2026-10-16_v2.md) for Part 3 (ladder specification) only. v1 and v2 documents and their hashes remain in the repository unchanged.
**Unchanged sections:** Part 1 (owner decisions), Part 2 (instrument universe and data specification), Part 4 (validation splits), Part 5 (null model and statistical requirements), Part 6 (what this protocol does not authorise).

---

## Amendment log

### A2-1: Ladder spacing is now pair-specific and data-derived (replaces v2 Part 3)

**Change:** The fixed OBSERVED_BASELINE ladder spacings (5%/7%/10%/15%/15%/10% from v1/v2) are replaced by pair-specific, direction-specific spacing derived from the Maximum Adverse Excursion (MAE) distribution of each fold's training data. The position-size multipliers (×1/2/4/8/16/32/64 at levels 1–7) are unchanged from v1/v2.

**Reason:** The crypto baseline percentage spacings are not validated for FX instruments. FX pairs have materially different volatility profiles when expressed in pip units. EUR/USD at 1.08 with a 0.1% adverse move = 1.08 pips — a trivially small spacing compared to USD/JPY at 150.00 where 0.1% = 15 pips. Applying a single set of raw percentage spacings across all five pairs ignores instrument-specific pip-value differences and pair-specific mean-reversion characteristics, producing spacings that are either too tight (causing premature ladder exhaustion) or too wide (causing near-zero fill frequency) depending on the pair.

**Governance note:** This amendment is written and hashed *before* any pair-specific excursion statistics are examined. The estimator design (quantile mappings, horizon, scaling bounds) is fixed in this document. If examining the calibration data reveals that this estimator design is materially miscalibrated, a new amendment (v4) with a new hash is required before any design adjustment is applied.

---

## Part 3 (revised) — Data-derived ladder specification

*(Replaces Part 3 of FX-DCA-PROTOCOL-1-A1. All other parts are unchanged.)*

### Multipliers

Unchanged from v1/v2: ×1, ×2, ×4, ×8, ×16, ×32, ×64 at levels 1, 2, 3, 4, 5, 6, 7 respectively.

### Spacing estimator

For each pair and direction (LONG and SHORT are estimated and applied independently):

**Step 1 — Volatility reference (ATR_ref)**

Compute the training-period median of the 24-candle rolling Average True Range (ATR), where:
- ATR per candle = max(high − low, |high − prev_close|, |low − prev_close|)
- Rolling ATR = mean of the previous 24 ATR values (causal, no lookahead)
- `ATR_ref` = median of the rolling ATR series over all training rows **excluding the last 168 rows** (the purge window — see Purge rule below)
- `ATR_ref` is one scalar value per pair/direction/fold; direction does not affect the ATR calculation itself (ATR is symmetric) but is computed and stored per direction for bookkeeping clarity

**Step 2 — Base spacing per level (MAE quantile mapping)**

For each ladder level L ∈ {2, 3, 4, 5, 6, 7}, the base spacing is the corresponding quantile of the Maximum Adverse Excursion (MAE) distribution over a 24-candle forward horizon, computed on the training rows only:

| Level | Quantile of MAE distribution |
|---|---|
| 2 | p40 |
| 3 | p60 |
| 4 | p75 |
| 5 | p85 |
| 6 | p92 |
| 7 | p97 |

**MAE definition:**

For a LONG entry at candle *i*'s close price `C_i`:
```
MAE_long(i, H) = max(0, C_i − min(low[i+1 .. i+H])) / C_i
```

For a SHORT entry at candle *i*'s close price `C_i`:
```
MAE_short(i, H) = max(0, max(high[i+1 .. i+H]) − C_i) / C_i
```

Where H = 24 trading candles (the MAE estimation horizon).

MAE values are computed for all candles in the training set subject to the purge rule. The base spacing for level L is the empirical quantile of the resulting MAE distribution for the relevant direction.

**Step 3 — Volatility scaling at trigger time**

At the moment an entry or ladder addition is being evaluated during simulation, compute the current causal ATR (24-candle trailing, using only rows already observed up to and including the current candle). Scale the base spacing:

```
triggered_spacing = base_spacing_L × clamp(current_ATR / ATR_ref, 0.5, 2.0)
```

The `clamp` function restricts the volatility ratio to [0.5, 2.0], preventing degenerate spacing in extreme low- or high-volatility regimes.

**Step 4 — Minimum spacing constraint**

```
triggered_spacing = max(triggered_spacing, 5 × pip_size / entry_price)
```

Never less than 5 pips from the previous fill price, regardless of volatility. This prevents ladder additions that are within the typical bid/ask spread.

**Step 5 — Maximum spacing constraint**

```
triggered_spacing = min(triggered_spacing, 0.25)
```

Never more than 25% adverse move from the previous fill. This prevents a ladder from waiting indefinitely for a fill that is economically unreachable under normal conditions.

**Step 6 — Freeze rule**

All base spacing values (Steps 1–2) are computed from the fold's TRAINING rows only and are frozen before the fold's test period begins. They do not change during the fold's test phase. The `current_ATR` used in Step 3 is always causal: it is computed using only rows that have been observed up to and including the current simulation candle.

### Pre-specified horizons for descriptive reporting

The following horizons are fixed before any results are examined:
**6, 12, 24, 48, 96, 168 trading candles**

### Pre-specified statistics for descriptive reporting

For each pair, direction (LONG/SHORT), and horizon, the following statistics are reported in both percentage and pip units:
**mean, median, p40, p60, p75, p85, p92, p97, p99, sample count (n)**

### Purge rule for forward contamination

MAE labels at candle *i* use data from candles *i+1* through *i+H*. To prevent any label's forward window from crossing a fold boundary or the calibration-embargo boundary, exclude the last `max_horizon = 168` rows of each training set from MAE estimation. The MAE estimation horizon of 24 candles is safely within the 168-row purge and the 48-candle embargo.

### Embargo adequacy

The protocol v2 specifies a 48-candle embargo between each fold's training and test sets. The maximum MAE estimation horizon used here is 24 candles, which is fully within the 48-candle embargo. No embargo extension is required for this estimator.

### Cost completeness gate

All descriptive excursion statistics and spacing calculations may be computed with `cost_complete=False`. No profitability claim may be made until `cost_complete=True`. These descriptive statistics characterise price movement; they make no claim about the profitability of the DCA strategy.

### Fill model

Unchanged from v1/v2: NEXT_OPEN (entries and exits filled at the opening price of the candle following the trigger).

### Directions

Both LONG and SHORT must be estimated and reported independently. Results must not be aggregated across directions.

---

## Part 1 — Owner decisions (unchanged from v2)

*(Reproduced for completeness — no changes.)*

### OD-FX-HB-1: horizon_basis = TRADING_CANDLE_COUNT

Ladder distances and all time-based metrics are counted in trading candles only — hourly candles during scheduled trading hours, with weekend closures (OD-FX-1) and FX_HOLIDAY_CLOSURES excluded. Analogous to `GRID_CANDLES_24_7`: one candle = one trading hour.

### OD-FX-FS-1: financing_source = MT5_BROKER_STATIC

Swap/rollover rates from ICFundedEvaluations published specification, applied as a static historical approximation. Label: `STATIC_SWAP_APPROXIMATION`. `cost_complete=True` requires `ForexInstrumentSpec.swap_long` and `swap_short` filled from Stage 2.

### OD-FX-CM-1: cost_model = MT5_BROKER_PUBLISHED

Spread and commission from ICFundedEvaluations published instrument specifications. `cost_complete=True` requires `ForexInstrumentSpec.spread_typical_pips` and `commission_per_lot` filled from Stage 2.

---

## Part 2 — Instrument universe and data specification (unchanged from v2)

**Target pairs:** EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD

**Data source:** MT5 via ICFundedEvaluations broker, 1-hour OHLC (H1 timeframe)

**Minimum acceptable:** 37,000 trading candles per pair (amended A1-1, 2026-10-16)

**Actual collected (2026-10-16):** ~37,461 candles per pair; verified as full broker history depth

**Calendar:** OD-FX-1 (decided 2026-10-06): Friday 17:00 → Sunday 17:00 America/New_York, plus FX_HOLIDAY_CLOSURES

---

## Part 4 — Validation splits (unchanged from v2)

**Primary split:** Calibration 70% (26,175 candles) / embargo 48 candles / holdout 30% (11,238–11,239 candles, SEALED).

**Walk-forward folds:** 5 expanding folds within calibration (see v2 Part 4 for exact boundaries).

**Holdout sealed:** no simulation results from the holdout may be examined until calibration analysis (including all walk-forward folds) is complete and its report is recorded.

---

## Part 5 — Null model and statistical requirements (unchanged from v2)

**Null model:** BLOCK_SHUFFLE, 499 permutations minimum per pair.

**Multiple testing:** Bonferroni primary (α = 0.01 per pair), FDR-BH secondary.

**Stouffer combination of fold p-values:** Indicative only — fold p-values are drawn from the same autocorrelated time series and are not statistically independent. The Stouffer-combined p-value must not be used as a headline significance result. Fold-level p-values are reported individually; the primary inferential result is the overall calibration permutation test, not the Stouffer combination.

---

## Part 6 — What this protocol does not authorise (unchanged from v2)

- Live or paper trading
- Parameter optimisation after examining any fold result
- Selecting ladder quantiles or MAE horizons after examining results (these are fixed in this document)
- Comparison of FX results to the crypto baseline unless both have `cost_complete=True`
- Unsealing holdout data before calibration analysis is complete
- Using the movement analysis descriptive results to claim that a profitable strategy has been identified

---

## Governance

**Hash:** SHA-256 of the full UTF-8 text of this document, computed before any simulation run; recorded in `FOREX_PROFILE.protocol_hash` in `bot/quant_core/market_profile.py`

**Supersedes:** FX-DCA-PROTOCOL-1-A1 (FX_DCA_PROTOCOL_2026-10-16_v2.md) for Part 3 only; v1 and v2 documents and their hashes remain in the repository unchanged

**Amendment procedure:** any further substantive change requires a new document (FX_DCA_PROTOCOL_2026-10-16_v4.md) with a new hash; prior documents and their hashes remain in the repository unchanged
