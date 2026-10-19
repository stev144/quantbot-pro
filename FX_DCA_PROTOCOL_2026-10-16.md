# FX DCA Research Protocol v1

**Identifier:** FX-DCA-PROTOCOL-1
**Status:** PRE-REGISTERED SPECIFICATION — written and hashed BEFORE any FX DCA results are examined.
**Owner decision date:** 2026-10-16
**Scope:** `bot/research/dca_engine.py` + forex adapter modules (`forex_instrument_spec.py`, `forex_accounting.py`, `forex_data_validation.py`, `forex_universe.py`)
**Relationship to existing protocol:** FX extension of `DCA_RESEARCH_PROTOCOL_2026-10-02.md`. All timing, accounting and fill-model rules from §1–§3 of that document apply unchanged. This document records only the FX-specific additions and the four owner decisions required by `FOREX_PROFILE.require_canonical()`.

---

## Part 1 — Owner decisions recorded here (FOREX_PROFILE fields)

These four values unblock `FOREX_PROFILE.require_canonical()`. They are binding once this document is hashed and the hash is approved.

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

**Target pairs:** EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD

**Data source:** MT5 via ICFundedEvaluations broker, 1-hour OHLC (H1 timeframe)

**Target candle count:** ~50,000 trading candles per pair (approximately 5–6 years)

**Minimum acceptable:** 40,000 trading candles per pair; instruments with fewer are excluded from this experiment, not labelled failures

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

**Split definition:**
- **Calibration period (in-sample):** first 70% of available trading candles per pair
- **Holdout period (out-of-sample):** last 30% of available trading candles per pair
- **Embargo:** 48 trading candles (48 trading hours) removed from the boundary; no candle within the embargo zone appears in either split
- **Holdout sealed:** no simulation results from the holdout period may be examined, reported, or used to modify any parameter until the calibration analysis is complete and its report is recorded

**Calibration period purposes (exhaustive — no others permitted):**
1. Verify accounting invariants on real FX data (not a research result)
2. Confirm instrument-aware P&L computation (pip-based sizing, account-currency conversion)
3. Run the null calibration (block-shuffle, see Part 5) to establish the p-value floor
4. Compute descriptive statistics for the closed-cycle and open-cycle populations (not a claim of edge)

No parameter optimisation of any kind is permitted on the calibration period. Any parameter change after seeing calibration-period results requires a new protocol version with a new hash.

---

## Part 5 — Null model and statistical requirements

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
1. Both calibration and holdout analyses complete
2. All accounting invariants pass
3. `cost_complete` status explicitly recorded (results with `cost_complete=False` labelled COST_INCOMPLETE)
4. Closed-cycle and open-cycle P&L reported separately; never summed silently
5. Maximum unrealised loss, ladder exhaustion count, longest underwater period, and capital utilisation reported
6. No result is labelled "profitable" unless:
   (a) `cost_complete=True`
   (b) holdout p-value < 0.05 after Bonferroni correction
   (c) open cycles marked to liquidation value still show positive net P&L after costs
   (d) the result is not driven by a single pair (removing the best pair leaves sign intact)

---

## Part 6 — What this protocol does NOT authorise

- Live or paper trading
- Parameter optimisation (ladder percentages, profit_activation_pct, trailing_pct, block size after seeing results)
- Addition of a stop-loss, regime filter, or any other strategy modification without a new protocol version
- Comparison of this FX result to the crypto baseline unless both have `cost_complete=True`
- Unsealing holdout data before calibration is complete and its report is recorded
- Using any result from an invalidated or superseded run to inform subsequent decisions without explicit labelling

---

## Governance

**Hash:** SHA-256 of the full UTF-8 text of this document, computed before any simulation run; recorded in `FOREX_PROFILE.protocol_hash` in `bot/quant_core/market_profile.py`

**Supersedes:** nothing (first FX DCA protocol)

**Amendment procedure:** any substantive change requires a new document (FX_DCA_PROTOCOL_2026-10-16_v2.md or later date) with a new hash; the old hash and document remain in the repository unchanged
