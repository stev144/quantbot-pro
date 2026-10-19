# FX DCA Research Protocol v2

**Identifier:** FX-DCA-PROTOCOL-1-A1
**Status:** AMENDMENT to FX-DCA-PROTOCOL-1 — written and hashed BEFORE any FX DCA results are examined.
**Amendment date:** 2026-10-16
**Supersedes (for changed sections only):** FX-DCA-PROTOCOL-1 (FX_DCA_PROTOCOL_2026-10-16.md); that document and its hash remain in the repository unchanged.
**Unchanged sections:** Part 1 (owner decisions), Part 3 (ladder), Part 5 (null model and statistical requirements), Part 6 (what this protocol does not authorise) — reproduced verbatim from v1.

---

## Amendment log

### A1-1: Minimum candle count revised (Part 2)

**Change:** `Minimum acceptable` reduced from 40,000 to 37,000 trading candles per pair.

**Reason:** Stage 2 data collection (2026-10-16) yielded 37,461 1h candles per pair across all five target instruments. This was verified to be the broker's **full available history** (~6 calendar years, confirmed by: 37,461 / (5 trading days × 24 h × 52 weeks) ≈ 6.02 years). It is not a depth-setting failure: MT5 "Max bars in history" was verified, and the terminal returned consecutive candles with no truncation artefact. Requesting additional history returned no further data. The original minimum of 40,000 assumed ~6.5–7 years of broker history; ICFundedEvaluations-MT5-Demo holds approximately 6 years. The shortfall is 539 candles (1.3%) — within normal variation for demo-account history depth. Statistical power (Part 5) is assessed against the actual 26,175 calibration candles rather than the minimum.

**Effect:** All five target pairs (EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD) remain eligible for this experiment. No pair is excluded.

### A1-2: Walk-forward fold structure added (Part 4)

**Change:** Part 4 is extended with a mandatory 5-fold expanding-window stability analysis within the calibration period.

**Reason:** The original 70/30 split tests overall calibration-vs-holdout performance but does not test whether that performance is consistent across sub-periods. A result driven entirely by one 6-month window in an otherwise flat strategy is not robust. The walk-forward folds directly address G1 (quantitative discipline: robustness), G3 (statistics: no single-period luck), and G5 (research integrity: pre-specified before any fold result is seen). The folds run only within calibration — the holdout remains sealed throughout.

**Important:** The strategy parameters are fixed across all folds (no optimisation, no refitting). Walk-forward here means stability assessment, not parameter tuning.

---

## Part 1 — Owner decisions recorded here (FOREX_PROFILE fields)
*(Unchanged from v1)*

### OD-FX-HB-1: horizon_basis = TRADING_CANDLE_COUNT

Ladder distances and all time-based metrics (holding period, time underwater, max_cycle_candles) are counted in **trading candles only** — hourly candles during scheduled trading hours, with weekend closures (OD-FX-1) and FX_HOLIDAY_CLOSURES excluded.

**Rationale:** Directly analogous to the crypto baseline (`GRID_CANDLES_24_7`): one candle = one trading hour in both cases. A 100-candle underwater event means the same thing for both asset classes. Weekend wall-clock time is not trading time; counting it inflates measured holding periods and distorts cross-asset comparisons. Any deviation from this rule is a separately labelled variant.

### OD-FX-FS-1: financing_source = MT5_BROKER_STATIC

Swap/rollover rates come from the MT5 broker (ICFundedEvaluations) currently published specification, applied as a **static historical approximation**.

**Constraints:**
- All results using this approximation carry the label `STATIC_SWAP_APPROXIMATION`.
- `cost_complete=True` is only achievable once actual swap rates are filled into `ForexInstrumentSpec.swap_long` and `ForexInstrumentSpec.swap_short` from a verified Stage 2 broker connection.
- Until actual rates are present, `financing_status = UNAVAILABLE` applies and `cost_complete=False`.
- Historical swap rates may differ from current published rates; this approximation is explicitly stated, not hidden.
- A variant using a different financing source is a separate, labelled strategy variant — never silently substituted.

### OD-FX-CM-1: cost_model = MT5_BROKER_PUBLISHED

Transaction costs (spread and commission) come from the MT5 broker (ICFundedEvaluations) published instrument specifications.

**Constraints:**
- `cost_complete=True` is only achievable once `ForexInstrumentSpec.spread_typical_pips` and `ForexInstrumentSpec.commission_per_lot` are filled from the Stage 2 broker connection.
- Until actual values are present in the instrument spec, `cost_complete=False` and all results carry `COST_INCOMPLETE`.
- No simulation may claim accurate cost treatment when `is_cost_complete()` returns False.
- Spread is applied as a round-trip cost (half on entry, half on exit) unless the broker spec provides separate bid/ask; if MT5 tick data provides bid/ask, use them directly.
- CONSERVATIVE_FIXED_MARKUP (2× typical spread) is a separately labelled variant, not the baseline.

---

## Part 2 — Instrument universe and data specification
*(Amended: minimum candle count revised — see A1-1)*

**Target pairs:** EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD

**Data source:** MT5 via ICFundedEvaluations broker, 1-hour OHLC (H1 timeframe)

**Target candle count:** ~50,000 trading candles per pair (approximately 5–6 years)

**Minimum acceptable:** ~~40,000~~ **37,000** trading candles per pair (revised A1-1, 2026-10-16); instruments with fewer are excluded from this experiment, not labelled failures

**Actual collected (2026-10-16):** 37,461 candles per pair; verified as full broker history depth

**Calendar:** OD-FX-1 (decided 2026-10-06): Friday 17:00 → Sunday 17:00 America/New_York, plus FX_HOLIDAY_CLOSURES

**Symbol mapping:** MT5 broker symbols must be verified against `ForexInstrumentSpec.canonical_symbol` before any run

**Mandatory data validation before any run:**
1. No duplicate timestamps
2. No OHLC violations (high < low, close outside [low, high], open outside [low, high])
3. No zero or negative prices
4. Gap audit: record all unexpected gaps (> 1 trading candle during trading hours); gaps > gap_max_candles=5 trigger cycle suspension (no invented prices)
5. Dataset fingerprint (SHA-256 of the raw candle data) recorded in every run's provenance

---

## Part 3 — DCA ladder specification
*(Unchanged from v1)*

**Ladder baseline:** OBSERVED_BASELINE — identical to the crypto DCA baseline, verified against real TradeFlow screenshots (WhatsApp Image 2026-09-26 21.28.26/27):

| Level | Adverse-move % (from previous fill) | Multiplier |
|---|---|---|
| 1 (entry) | — | ×1 |
| 2 | 5% | ×2 |
| 3 | 7% | ×4 |
| 4 | 10% | ×8 |
| 5 | 15% | ×16 |
| 6 | 15% | ×32 |
| 7 | 10% | ×64 |

Drop % is measured from the **previous fill price** (sequential), not from the original entry.

**Allocation unit:** expressed as a fraction of the simulated account equity per pair (not a fixed USD/USDT amount); the base unit is 0.01 lots × the instrument's minimum allocation per run

**Directions:** both LONG and SHORT must be run; they are not assumed equivalent and results are not aggregated across directions

**Fill model:** NEXT_OPEN (canonical default per DCA_RESEARCH_PROTOCOL_2026-10-02.md §2)

**profit_activation_pct:** 2.5% from weighted-average price (same as crypto baseline)

**trailing_pct:** 0.5% trailing stop from peak (same as crypto baseline)

**max_cycle_candles:** None (no time stop — consistent with the observed baseline "no stop-loss")

---

## Part 4 — Validation splits (pre-specified and sealed)
*(Amended: walk-forward fold structure added — see A1-2)*

### Primary split

**Split definition (37,461 candles per pair):**
- **Calibration period (in-sample):** first 70% of available trading candles, minus the embargo strip = **26,175 candles**
- **Embargo (calibration-holdout boundary):** 48 trading candles at the boundary; these appear in neither split
- **Holdout period (out-of-sample):** last 30% of available trading candles = **11,238 candles**
- **Arithmetic check:** 26,175 + 48 + 11,238 = 37,461 ✓
- **Holdout sealed:** no simulation results from the holdout period may be examined, reported, or used to modify any parameter until the calibration analysis (including all walk-forward folds) is complete and its report is recorded

### Walk-forward stability folds (within calibration only)

Five expanding-window folds are run within the 26,175 calibration candles. The strategy parameters are **fixed and identical across all folds** — there is no refitting, no optimisation. The folds exist solely to test whether observed calibration-period returns are consistent across sub-periods.

**Fold generation rules (deterministic, applied to calibration candles [0, 26175)):**

| Parameter | Value |
|---|---|
| Number of folds | 5 |
| Window type | Expanding (train grows; test is a fixed-size slice) |
| Minimum initial training window | 5,000 candles |
| Test slice size | ⌊(26,175 − 5,000) / 5⌋ = 4,235 candles (folds 1–4); remainder assigned to fold 5 |
| Embargo within each fold | 48 candles between that fold's train end and test start |
| Index basis | 0-based position in the sorted calibration array (not calendar dates) |

**Derived fold boundaries (0-indexed, inclusive start / exclusive end):**

| Fold | Train | Embargo | Test |
|---|---|---|---|
| 1 | [0, 5000) | [5000, 5048) | [5048, 9283) |
| 2 | [0, 9283) | [9283, 9331) | [9331, 13566) |
| 3 | [0, 13566) | [13566, 13614) | [13614, 17849) |
| 4 | [0, 17849) | [17849, 17897) | [17897, 22132) |
| 5 | [0, 22132) | [22132, 22180) | [22180, 26175) |

**Fold 5 test size:** 26,175 − 22,180 = 3,995 candles (slightly smaller than folds 1–4 due to rounding)

**Consistency criterion:** the sign and order-of-magnitude of the return must be consistent across all 5 folds for a strategy to be considered robust (not merely profitable on one lucky sub-period). A strategy that passes on 2 of 5 folds is labelled INCONSISTENT and no profitability claim is made even if the overall calibration return is positive.

**Permutation test per fold:** each fold's test result is independently permutation-tested (same BLOCK_SHUFFLE, 499 permutations minimum) and a per-fold p-value reported. Fold p-values are combined using Stouffer's method (equal weights) for a combined calibration p-value, which supplements (does not replace) the overall calibration permutation test.

### Calibration period purposes (exhaustive — no others permitted)

1. Verify accounting invariants on real FX data (not a research result)
2. Confirm instrument-aware P&L computation (pip-based sizing, account-currency conversion)
3. Run the null calibration (block-shuffle, see Part 5) to establish the p-value floor
4. Compute descriptive statistics for the closed-cycle and open-cycle populations (not a claim of edge)
5. Run walk-forward stability folds (5 folds, see above) and report per-fold returns and p-values

No parameter optimisation of any kind is permitted on the calibration period. Any parameter change after seeing calibration-period results requires a new protocol version with a new hash.

---

## Part 5 — Null model and statistical requirements
*(Unchanged from v1)*

**Null model:** BLOCK_SHUFFLE (same as the crypto DCA baseline)
- Block-shuffle close-to-close log-returns for each pair independently
- Block size: fixed before any run, chosen as: max(24, modal holding candles from calibration-period descriptive run); recorded in provenance; not changed after seeing results
- 499 permutations minimum per pair

**Multiple testing correction:**
- Primary: Bonferroni across the 5 pairs (α = 0.05 / 5 = 0.01 per pair)
- Secondary report: FDR-BH (Benjamini-Hochberg) across the 5 pairs
- Both must be reported; only the Bonferroni-corrected result governs any claim of significance

**Minimum statistical power:** 0.80 at the calibration-period observed effect size; if the available data does not support this power level, the result is labelled UNDERPOWERED and no profitability claim is made

**What constitutes a reportable result (all conditions must hold):**
1. Both calibration (including all 5 walk-forward folds) and holdout analyses complete
2. All accounting invariants pass
3. `cost_complete` status explicitly recorded (results with `cost_complete=False` labelled COST_INCOMPLETE)
4. Closed-cycle and open-cycle P&L reported separately; never summed silently
5. Maximum unrealised loss, ladder exhaustion count, longest underwater period, and capital utilisation reported
6. No result is labelled "profitable" unless:
   (a) `cost_complete=True`
   (b) holdout p-value < 0.05 after Bonferroni correction
   (c) open cycles marked to liquidation value still show positive net P&L after costs
   (d) the result is not driven by a single pair (removing the best pair leaves sign intact)
   (e) walk-forward consistency criterion passes (≥ 4 of 5 folds consistent in sign — see Part 4)

---

## Part 6 — What this protocol does NOT authorise
*(Unchanged from v1)*

- Live or paper trading
- Parameter optimisation (ladder percentages, profit_activation_pct, trailing_pct, block size after seeing results)
- Addition of a stop-loss, regime filter, or any other strategy modification without a new protocol version
- Comparison of this FX result to the crypto baseline unless both have `cost_complete=True`
- Unsealing holdout data before calibration (including all walk-forward folds) is complete and its report is recorded
- Using any result from an invalidated or superseded run to inform subsequent decisions without explicit labelling
- Retroactively adjusting the walk-forward fold boundaries or consistency criterion after seeing any fold result

---

## Governance

**Hash:** SHA-256 of the full UTF-8 text of this document, computed before any simulation run; recorded in `FOREX_PROFILE.protocol_hash` in `bot/quant_core/market_profile.py`

**Supersedes:** FX-DCA-PROTOCOL-1 (FX_DCA_PROTOCOL_2026-10-16.md) for Parts 2 and 4 only; v1 document and its hash remain in the repository unchanged

**Amendment procedure:** any further substantive change requires a new document (FX_DCA_PROTOCOL_2026-10-16_v3.md) with a new hash; prior documents and their hashes remain in the repository unchanged
