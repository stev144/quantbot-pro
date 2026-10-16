# FX DCA Research Protocol v4

**Identifier:** FX-DCA-PROTOCOL-1-A3
**Status:** AMENDMENT — written and hashed AFTER movement analysis results were examined (this is the intended and pre-registered step: v3/A2 pre-registered the estimator design; this v4/A3 locks the computed threshold values from the calibration data).
**Amendment date:** 2026-10-16
**Supersedes (for changed sections only):** FX-DCA-PROTOCOL-1-A2 (FX_DCA_PROTOCOL_2026-10-16_v3.md) for Part 3A (locked calibration thresholds) only. v1, v2, and v3 documents and their hashes remain in the repository unchanged.
**Unchanged sections:** Part 1 (owner decisions), Part 2 (instrument universe and data specification), Part 3 spacing estimator design (unchanged — this amendment adds the locked values, not a design change), Part 4 (validation splits), Part 5 (null model and statistical requirements), Part 6 (what this protocol does not authorise).

---

## Amendment log

### A3-1: Locked calibration threshold values (adds Part 3A)

**Change:** The actual computed ATR_ref and MAE quantile values are recorded here as fixed constants for the calibration run. These values were computed by `bot/research/fx_movement_analysis.py` using:
- Calibration data only (no holdout data accessed at any point)
- The estimator design pre-specified in v3/A2 (horizon H=24, quantiles p40/p60/p75/p85/p92/p97, purge window 168 rows, ATR window 24 candles)
- The fold splitter per v2/A1 (70/30 primary split, 48-candle embargo)

Full per-horizon results are saved in `data/forex/fx_movement_results.json` (committed). The values below are the H=24 rows used for ladder level assignment and are read-only from this point forward.

**Governance note:** The estimator design is locked in v3. No design parameter (horizon, quantile mapping, scaling formula, purge window) may change without a new amendment (v5). These locked values may not be revised based on simulation outcomes. If the first controlled calibration run reveals a systematic problem (e.g. all folds hit the minimum spacing floor), that constitutes evidence for a v5 amendment; it does not permit retroactive adjustment of these values.

---

## Part 3A (new) — Locked calibration threshold values

*(Added by A3. These values are computed from calibration data, stored here as protocol record. All values in pip units. pip_size: EUR/USD=GBP/USD=USD/CHF=AUD/USD=0.0001, USD/JPY=0.01.)*

### ATR_ref (price units and pip units)

| Pair | ATR_ref (price) | ATR_ref (pips) | Calibration candles |
|---|---|---|---|
| EUR/USD | 0.001280 | 12.80 | 26,175 |
| GBP/USD | 0.001752 | 17.52 | 26,174 |
| USD/JPY | 0.181292 | 18.13 | 26,175 |
| USD/CHF | 0.001167 | 11.67 | 26,175 |
| AUD/USD | 0.001245 | 12.45 | 26,175 |

ATR_ref computed per v3 Step 1: 24-candle rolling ATR median over training rows, excluding the last 168 rows (purge). These are full-calibration values (all 26,175 rows as single training set). Per-fold ATR_ref values will be computed at fold boundaries using training rows only; they will not be stored here.

### Locked level thresholds — LONG direction (H=24, pips)

| Pair | Level 2 (p40) | Level 3 (p60) | Level 4 (p75) | Level 5 (p85) | Level 6 (p92) | Level 7 (p97) | n |
|---|---|---|---|---|---|---|---|
| EUR/USD | 22.8 | 37.9 | 54.4 | 70.9 | 90.1 | 117.9 | 26,007 |
| GBP/USD | 31.0 | 51.4 | 71.5 | 92.3 | 118.7 | 163.5 | 26,006 |
| USD/JPY | 25.9 | 46.3 | 73.2 | 104.3 | 149.7 | 228.4 | 26,007 |
| USD/CHF | 20.5 | 33.0 | 46.7 | 61.0 | 77.8 | 104.5 | 26,007 |
| AUD/USD | 21.6 | 35.6 | 49.8 | 65.0 | 84.2 | 108.0 | 26,007 |

### Locked level thresholds — SHORT direction (H=24, pips)

| Pair | Level 2 (p40) | Level 3 (p60) | Level 4 (p75) | Level 5 (p85) | Level 6 (p92) | Level 7 (p97) | n |
|---|---|---|---|---|---|---|---|
| EUR/USD | 22.2 | 36.2 | 51.9 | 67.3 | 85.8 | 119.0 | 26,007 |
| GBP/USD | 30.4 | 49.6 | 69.1 | 88.9 | 112.8 | 158.9 | 26,006 |
| USD/JPY | 29.9 | 50.7 | 74.0 | 102.3 | 135.9 | 194.3 | 26,007 |
| USD/CHF | 20.0 | 32.9 | 46.1 | 58.8 | 74.3 | 98.6 | 26,007 |
| AUD/USD | 21.6 | 34.7 | 47.4 | 61.2 | 76.4 | 101.0 | 26,007 |

### USD/JPY LONG/SHORT asymmetry note

USD/JPY shows meaningful directional asymmetry at high percentiles (LONG p97=228.4 vs SHORT p97=194.3 pips). This is consistent with the strong USD/JPY uptrend in the 2021–2024 calibration period (JPY weakness producing larger LONG adverse excursions). The data-derived ladder correctly assigns wider LONG thresholds at levels 6–7 compared to SHORT. This is a descriptor of the calibration period — it does not imply that USD/JPY LONG trades require wider spacing in future folds; per-fold ATR_ref scaling in Step 3 of v3 handles ongoing volatility variation.

### How these values are used in simulation

The base spacings above are percentage-form values derived from pip/price (the MAE was computed as `pips × pip_size / entry_close`, not as a fixed pip count). During simulation, the level threshold in pip units is converted back to a price fraction at the entry candle's close price before ATR scaling is applied:

```
base_spacing_L = threshold_pips × pip_size / entry_price
triggered_spacing = base_spacing_L × clamp(current_ATR / ATR_ref, 0.5, 2.0)
triggered_spacing = max(triggered_spacing, 5 × pip_size / entry_price)
triggered_spacing = min(triggered_spacing, 0.25)
```

Where `ATR_ref` and `threshold_pips` are the per-fold, per-direction values (for fold 1, the full-calibration values above are used for the training set — per-fold values are computed anew from that fold's training rows at fold boundary time; the locked values in this table are for the full-calibration training set and serve as a record, not as fixed inputs to all folds).

### Per-fold computation requirement

The locked values above are for the full 26,175-row calibration set. During the walk-forward run:
- Fold 1 training set = 5,000 rows → its ATR_ref and MAE quantiles are recomputed from those rows
- Fold 2 training set = 9,283 rows → recomputed
- Folds 3–5 similarly

The full-calibration values here establish the expected magnitude and are retained for the provenance record. Per-fold values are computed at run time by `fx_movement_analysis.analyse_pair()` on the fold's training slice. No separate amendment is required for per-fold values; they are the natural application of the v3/A2 estimator.

### Data provenance

| Pair | CSV | Provenance JSON |
|---|---|---|
| EUR/USD | `data/forex/EURUSD_1h.csv` | `data/forex/EURUSD_1h.provenance.json` |
| GBP/USD | `data/forex/GBPUSD_1h.csv` | `data/forex/GBPUSD_1h.provenance.json` |
| USD/JPY | `data/forex/USDJPY_1h.csv` | `data/forex/USDJPY_1h.provenance.json` |
| USD/CHF | `data/forex/USDCHF_1h.csv` | `data/forex/USDCHF_1h.provenance.json` |
| AUD/USD | `data/forex/AUDUSD_1h.csv` | `data/forex/AUDUSD_1h.provenance.json` |

Full movement results (all horizons, both directions, pip and percentage): `data/forex/fx_movement_results.json`

---

## Part 1 — Owner decisions (unchanged from v3)

*(No change — see FX_DCA_PROTOCOL_2026-10-16_v3.md Part 1 for full text.)*

---

## Part 2 — Instrument universe and data specification (unchanged from v3)

*(No change — see FX_DCA_PROTOCOL_2026-10-16_v3.md Part 2 for full text.)*

---

## Part 3 — Data-derived ladder specification (unchanged from v3, design only)

*(The estimator design is unchanged — see FX_DCA_PROTOCOL_2026-10-16_v3.md Part 3 for the full estimator description. Part 3A above adds the computed values.)*

---

## Part 4 — Validation splits (unchanged from v3)

*(No change — see FX_DCA_PROTOCOL_2026-10-16_v3.md Part 4 for full text.)*

---

## Part 5 — Null model and statistical requirements (unchanged from v3)

*(No change — see FX_DCA_PROTOCOL_2026-10-16_v3.md Part 5 for full text.)*

---

## Part 6 — What this protocol does not authorise (unchanged from v3)

*(No change — see FX_DCA_PROTOCOL_2026-10-16_v3.md Part 6 for full text.)*

---

## What is now authorised (first controlled calibration run)

With v4 hashed and committed:

1. **Fold 1 calibration run** — 5,000-row training set, 48-candle embargo, 4,235-row test set (EUR/USD dates: Oct 2020 → Apr 2022)
2. The run uses `simulate_dca_path_fx()` in `bot/engines/dca_engine.py` with `FX_BASELINE_CONFIG` and MT5 specs from `MT5_SPECS`
3. Per-fold ATR_ref and MAE quantiles are computed from the 5,000-row training slice using `fx_movement_analysis.analyse_pair()`
4. Holdout data remains sealed throughout
5. Results from fold 1 may be examined; parameter changes based on fold 1 results require a new amendment

---

## Governance

**Hash:** SHA-256 of the full UTF-8 text of this document, computed before any simulation run; recorded in `FOREX_PROFILE.protocol_hash` in `bot/quant_core/market_profile.py`

**Supersedes:** FX-DCA-PROTOCOL-1-A2 (FX_DCA_PROTOCOL_2026-10-16_v3.md) for Part 3A (locked values) only; v1, v2, and v3 documents and their hashes remain in the repository unchanged

**Amendment procedure:** any further substantive change requires a new document (FX_DCA_PROTOCOL_2026-10-16_v5.md) with a new hash; prior documents and their hashes remain in the repository unchanged
