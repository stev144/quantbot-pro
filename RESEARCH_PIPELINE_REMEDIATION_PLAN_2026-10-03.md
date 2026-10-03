# Research Pipeline Remediation Plan and Batch-1 Report (2026-10-03)

**Reset point:** CRRP-2026-10-02 · **Source:** `FULL_RESEARCH_PIPELINE_FIVE_GATE_AUDIT_2026-10-03.md` (every finding re-verified against code before repair).

**Mode:** controlled remediation, batch 1.
- No research rerun, no strategy optimisation, no holdout touched, nothing traded, no historical artifact deleted.
- Every diagnostic was synthetic or read-only, with the network refused.

---

## 0. Provenance freeze (Phase 0)

| Item | Value |
|---|---|
| Baseline HEAD for this batch | `39b76a2bca92f090d2b2c3c52de85713ba53926b` (working tree clean at start) |
| Audit provenance boundary | The audit ran on **`ef5636b` plus 3 uncommitted changes**, all since committed in `39b76a2`: (1) the one-line Kalman writer fix: the legacy `run()` CSV timestamp column was named `index`; the audit's Kalman probes used `quant_core` and never touched this writer; (2) the retest module `pairs_crrp_retest.py`; (3) one registry entry. **Verified:** the registry diff is exactly one added entry, no removals or reorderings, all other keys identical. Its 407-line git diff is JSON re-serialisation formatting. |
| Audit diagnostics | Inline scripts (not committed) plus `dca_calibration.py` (committed in `ef5636b`). No audit artifact was mixed into production paths. |
| Batch-1 changes | Listed in §4, each with a five-gate dossier in `FIVE_GATE_CORRECTNESS_MATRIX_2026-10-03.md` |

## 1. Confirmed findings and root causes

| ID | Root cause (verified) | Batch-1 outcome |
|---|---|---|
| F-09 | Files were written mid-candle, and the fetch path kept the forming candle (`_build_ohlcv_dataframe` had no completed-candle rule) | **Repaired:** fetch-time rule; v2 dataset (145 files corrected, v1 archived byte-identical); v2 manifest |
| F-03 | The legacy `KalmanFilterEngine.run()` seeds θ₀ with an OLS over the first 10,000 candles, applied from t = 0; its output carried no seed lineage; `CANONICAL_UNCERTIFIED` accepted it silently | **Repaired (lineage + retest path):** the legacy writer emits a LEGACY sidecar declaring `seed_method=LEGACY_OLS_FIRST_N, seed_lookahead=true`; every entry/exit summary records `kalman_seed_lineage`; the retest module moved to `run_canonical` (causal, L = 1000) + `rule_set="CANONICAL"`. The legacy method is kept (not deleted) for history. |
| F-04 | Labels were built close-to-close from the signal bar (d = 0) | **Contract built, not wired:** `quant_core/timing.py` (no default d; d = 0 refused unless explicitly diagnostic). **BLOCKED:** canonical d is an owner decision |
| F-02 | IID Spearman p-values on overlapping labels with persistent features | **Calibrated candidates built, not wired:** `quant_core/inference.py` + `research/inference_calibration.py`. **BLOCKED:** method choice is an owner decision (§3) |
| F-05 / F-07 | Legacy pairs null (z-timing); B = 100 everywhere vs family sizes | **Specified (§3), not implemented:** Step 17 needs an approved spec; B per family needs the family definitions |
| F-08 | The DCA statistic does not respond to the planted effect (mean p 0.52–0.55 under H₁ vs 0.48–0.54 under H₀) | **Diagnosed; BLOCKED** on a redesign decision |
| F-10 | Universes are current listings applied back to 2021; no listing/delisting history on disk | **BLOCKED:** no authoritative point-in-time data (not fabricated) |
| F-11 | `fdr_adjusted_p_value = block_p` asserted a family correction that does not exist; the UI showed None as 0 | **Repaired:** None + `family_correction = NOT_APPLIED_NO_REGISTERED_FAMILY`; the raw p kept; UI says "not applied". **Verdict vocabulary: BLOCKED** (owner) |
| F-16 | Separate BH per scan; no cross-scan family registry | **Specified, not implemented** (owner family definition) |
| M-03 | `test_start = train_end` with inclusive `.loc` slicing put the boundary candle in train and test (and in the fold OLS seed) | **Repaired:** exclusive boundary; test window anchored on `train_end` |
| F-14 | Undefined Sharpe/Sortino (None) coerced to 0 and scored "Poor" | **Repaired:** labelled undefined, no points |
| M-06 | A DCA "r_multiple" was return on deployed capital, consumed as R | **Repaired:** `r_multiple=None`, `return_on_max_deployed`; R ratios exclude trades without R (None if none) |
| F-12 | Pairs borrow charged 0 when no rate was given (flagged, but not in the borrow vocabulary) | **Repaired:** `borrow_status` = BORROW_KNOWN / ASSUMED_ZERO_BORROW in every summary |
| F-01 | Full-history PCA fit | **Not in this batch's order; pending.** Needs a declared rolling/expanding window spec |

## 2. Dependencies (why this order)

```
F-09 data ──► everything (every engine reads data/)
F-03 seed ──► pairs research, permutation, walk-forward, research-lab pairs, retest
F-04 timing ──► feature / cross-sectional / contagion labels ──► F-02 inference (labels must be causal before testing them)
F-02 + F-07 ──► every significance claim (feature, contagion, regime, OOS)
F-05 (Step 17) ──► any pairs evidence
F-08 ──► any DCA significance claim
F-10 ──► cross-sectional / scans
F-11 / F-16 ──► what the research lab may claim
```

## 3. Statistical evidence for the open method decisions

**F-02: candidate dependence-aware tests** (n = 2,000; nominal α = 5 %; size SE ≈ 1.3 %; R = 300 under H₀, 200 under H₁; B = 99). Output in `research_data/_governance/inference_calibration_2026-10-03.json`.

| Scenario (h = overlap, φ = feature persistence) | IID Spearman | HAC L = 2h | HAC auto | Circular shift | Block perm (b = 4h) |
|---|---|---|---|---|---|
| Size h = 1, φ 0 / 0.9 / 0.99 | 3.7 / 5.3 / 5.0 | 4.0 / 5.3 / 5.0 | 4.0 / 6.0 / 4.7 | 3.3 / 3.7 / 6.0 | 2.7 / 3.7 / 4.3 |
| Size h = 4, φ 0 / 0.9 / 0.99 | 6.3 / **26.7** / **32.7** | 6.0 / 8.7 / 9.7 | 5.7 / 7.0 / 9.0 | 5.0 / 7.0 / 8.3 | 4.3 / 6.0 / 5.7 |
| Size h = 24, φ 0 / 0.9 / 0.99 | 6.0 / **60.0** / **60.0** | 6.3 / 8.3 / 8.0 | 7.0 / 8.0 / 8.3 | 3.7 / 5.3 / 5.3 | 5.3 / 4.0 / 2.7 |
| Power h = 4, φ 0.9, IC 0.05 | (invalid) | 52.5 | 49.0 | 44.0 | 42.5 |
| Power h = 24, φ 0.99, IC 0.05 | (invalid) | 62.5 | 62.5 | 45.5 | 47.0 |
| Power h = 24, φ 0.9, IC 0.03 | (invalid) | 11.0 | 11.0 | 8.0 | 8.5 |

What the calibration shows:
- **IID is invalid** whenever labels overlap and the feature is persistent; F-02 is reproduced at 60 % for h = 24.
- **HAC is anti-conservative** (7–10 %) with persistent regressors in this sample size, a known small-sample Newey–West distortion.
- **Block permutation and circular shift are close to nominal**, at about 8–15 points less power than HAC.
- **All methods have low power for IC ≈ 0.03.**
- **Computational cost:** HAC ≈ 1 ms per test; permutation methods ≈ B × 1 ms.

**F-07, permutation count:** the smallest attainable p is 1/(B + 1); passing Bonferroni or BH-rank-k for m tests needs B ≥ m/(α·k) − 1. Examples: m = 20 → B ≥ 399; m = 240 → B ≥ 4,799; m = 2,156 (contagion) → B ≥ 43,119 per test, or a two-stage design (analytic screen → permutation confirmation within the screened family).

**F-05, Step 17 null (specification proposal, not implemented):** a pairs edge claim requires a null that destroys the **cross-asset relationship** while preserving each leg's own dynamics.
- **Candidates:** an independent block bootstrap of each leg's returns, or a circular shift of leg B (with returns rolled, as in the retest amendment).
- **Pipeline:** re-run the full pipeline (canonical Kalman, lifecycle, costs) per replicate.
- **Calibration required:** size ≈ α on unrelated random-walk pairs; power on a synthetic OU-cointegrated pair.

**F-08, DCA:** the statistic doesn't respond to the planted effect (mean p unchanged under H₁). A redesign must:
- define the effect at the ladder's scale (moves of the trigger sizes, 5–15 %);
- choose the statistic (e.g. a single MTM P&L or MTM-drawdown-adjusted return, not an intersection with a second statistic);
- use a sample long enough for enough completed cycles;
- be re-calibrated before any run.

## 4. Batch-1 changes (files)

| File | Change |
|---|---|
| `bot/data_fetcher.py` | Completed-candle rule at fetch (`_build_ohlcv_dataframe`, `as_of_ms`) |
| `bot/research/data_integrity.py` (new) | Partial-candle audit; versioned correction with archive and v2 manifest |
| `data/*_1h.csv` (145 files, gitignored) | Last partial/unverifiable row dropped; v1 originals in `data/_archive/v1_CRRP-2026-10-02/` |
| `bot/research/governance/data_manifest_CRRP-2026-10-02_v2.json` (new) | v2 lineage |
| `bot/research/kalman_filter_engine.py` | LEGACY sidecar with seed lineage on the legacy writer |
| `bot/research/entry_exit_engine.py` | `kalman_seed_lineage`, `borrow_status` in summaries |
| `bot/research/pairs_crrp_retest.py` | Amendment 2: canonical causal seed path (not run) |
| `bot/quant_core/timing.py` (new) | Timing contract (F-04) |
| `bot/quant_core/inference.py`, `bot/research/inference_calibration.py` (new) | F-02 candidates and calibration |
| `bot/research/walk_forward_engine.py` | M-03 exclusive boundary |
| `bot/research_lab/tools/conditional_tools.py`, `templates/research_lab/{results,report}.html` | F-11 field correction |
| `bot/engines/strategy_scorer.py` | F-14 undefined ratios |
| `bot/research/dca_engine.py`, `bot/backtesting/backtester.py` | M-06 |
| Registry + `research_data/model_governance_log.md` | Dataset v2 entry (append-only, CRLF, history byte-identical); calibration artifact registered |
| Tests (new / updated) | `test_data_integrity` (4), `test_kalman_seed_lineage` (6), `test_timing_contract` (4), `test_phase3_engineering` (+3), `test_walk_forward_engine` (updated boundary assertion), `test_research_lab_statistical_integrity` (updated F-11 assertion) |

## 5. Owner decisions required (STOP points)

| Decision | Options | Consequences | Evidence needed to choose |
|---|---|---|---|
| **D-1 Canonical execution delay d** (F-04) for the feature / cross-sectional / contagion labels | d = 1 bar at the next open (proposed minimum); d = 1 at the next close; larger d from measured latency | Larger d is more conservative and removes bounce artifacts (bounce demo: IC 0.157 → 0.014); with d < true latency, results stay inflated | Live/dry-run latency measurements; the venue's order-book timing |
| **D-2 Inference method** (F-02) | Block permutation (b = k·h); circular shift; HAC (with a size correction) | Permutation methods: valid size, lower power, B cost. HAC: more power, 7–10 % size here | §3 table; possibly a larger-n calibration at your data length |
| **D-3 Test families and B** (F-07, F-16) | Per-program families with B from the formula; two-stage screen + confirmation | Defines what "significant" can mean; compute cost | Count of planned tests per program |
| **D-4 Step 17 null** (F-05) | Independent-leg block bootstrap vs circular shift | Both destroy the relationship; they differ in preserved structure | Calibration on random-walk and OU synthetic pairs (to run after approval) |
| **D-5 DCA significance design** (F-08) | Effect definition, statistic, sample length | Without a redesign the test cannot detect anything | A new calibration after the redesign |
| **D-6 Point-in-time universe data** (F-10) | Source authoritative listing/delisting history, or keep cross-sectional/scans BLOCKED | Without it, survivorship bias cannot be removed | A data-source decision |
| **D-7 Research-lab verdict vocabulary** (F-11) | Proposed tiers: OBSERVATION → STATISTICAL RESULT (family-corrected, dependence-aware) → ECONOMIC RESULT (net of costs) → TRADEABLE RESULT (causal execution, capacity, OOS) → CANONICAL EVIDENCE (provenance plus forward holdout) | Current "SUPPORTED" implies more than tested | Approval of the tiers |
| **D-8 Zones S/V/WF/F and forward-holdout start** | (unchanged) | Unblocks canonical pairs selection and claim-eligible OOS | — |
| **D-9 PCA causal fit window** (F-01) | Rolling W candles vs expanding; refit frequency | Removes the look-ahead; W affects factor stability | A sensitivity study after the choice |

## 6. Blocked items

F-04 wiring (D-1) · F-02 wiring (D-2) · F-07/F-16 (D-3) · F-05 (D-4) · F-08 (D-5) · F-10 (D-6) · F-11 tiers (D-7) · canonical pairs evidence (D-8) · F-01 (D-9).

## 7. Regression

This was a full offline run of `bot/tests` (122 modules, 4 shards, network refused by a guard), compared failure by failure against the clean-HEAD baseline (`565e55d`, same harness).

| Run | Tests run | Failures + errors |
|---|---|---|
| Baseline (HEAD 565e55d) | 1,538 | 85 |
| Batch 1 (working tree) | 1,679 (+141 tests added since the baseline) | 85 |

- **New failures: 0. Fixed failures: 0.** The 85 failing tests are exactly the same set in both runs.
- **What they are:**
  - tests that need live exchange or network access (Binance/Kraken adapters, order books, derivatives / agg-trade fetches, venue readiness / comparison, Yahoo provider);
  - data-freshness checks;
  - pre-existing data-dependent assertions (`test_real_1h_crypto_result_is_byte_identical_to_pre_fix_baseline`, `test_exactly_target_size_unique_symbols`, `test_universe_is_currency_balanced_not_usd_dominated`, MT5 marker).
- **New batch tests:** all pass (data integrity 4, Kalman seed lineage 6, timing contract 4, undefined ratios 3, plus the updated walk-forward and research-lab assertions).
- **Data v2:** it did not change the outcome of any data-dependent test.

## 8. Readiness

**Not research-ready.** Batch 1 makes the data and the pairs seed lineage trustworthy and adds the tools to decide the statistical design. No engine becomes canonical-capable until decisions D-1…D-9 are made and implemented. **No strategy is promoted; no edge is established.**
