# Five-Gate Remediation Report (2026-10-03)

**Program:** `FIVE_GATE_REMEDIATION_PROGRAM_2026-10-03.md` (inspection, audit-vs-code comparison, dependency order).

**Scope of this report:** remediation batch 1 (earlier today) plus Stage A (this pass). Stage B is blocked on owner decisions.

**Not done here:**
- no market research was rerun;
- no expensive calibration;
- no holdout touched;
- nothing traded;
- no historical artifact deleted;
- no commit.

**Status vocabulary (as instructed):**
- **FIXED** = root cause repaired, regression test exists, five gates reassessed, consumers reviewed, provenance and classification updated.
- Otherwise: IMPLEMENTED BUT NOT YET VERIFIED, PARTIALLY REMEDIATED, BLOCKED, or OPEN.

---

## A. Remediation report (F-01 … F-21)

| ID | Status | Root cause | Files / functions changed | Tests | Gate impact | Historical impact | Remaining uncertainty |
|---|---|---|---|---|---|---|---|
| **F-01** PCA look-ahead | **PARTIALLY REMEDIATED** (G4 causal fit fixed; G3 / F-04 open) | `run_pca` fitted mean, std, correlation and loadings on the full history and residuals were computed with that fit | `pca_engine.py`: new `compute_residuals_causal` (expanding window, refit every 168, first fit after 1,000, fit strictly before the refit row; projection L·Lᵀ so sign/rotation-invariant); `PCAConfig.min_fit_candles`, `refit_every`; `run_pca_engine` uses the causal residuals (the full-history fit is kept only for the descriptive scree/loadings files) | `test_pca_causal` (5): future-perturbation invariance; **defect injection** (the legacy fit fails the same check); hand-computed first segment (atol 1e-15); known-answer factor removal (residual–factor \|corr\| < 0.10, observed 0.030–0.047; residual–noise corr > 0.85, observed 0.888–0.898); refusals | G1 PASS, G2 PASS, G4 PASS for causality. **G4 still FAIL for labels** (same-close `rolling(h).sum().shift(-h)`: F-04). G3 FAIL (B = 100, family). G5: fit window/refit cadence are reused conventions, sensitivity untested | PCA residual IC stays **INVALIDATED**; no rerun | Refit cadence and window sensitivity; k varies between refits (logged) |
| **F-02** IID inference | **PARTIALLY REMEDIATED** | Analytic Spearman/ANOVA p assume independent observations; labels overlap | **Batch 1:** `quant_core/inference.py` (HAC, circular shift, block permutation), `research/inference_calibration.py` (size/power). **Stage A:** `IID_PVALUE_BASIS` label added to every decision-feeding IID p (`regime_conditional_ic` cells + interaction F-test, contagion, derivatives, feature decay, feature stability, Kalman signal table); `regime_conditional_status` explanation states the IID basis and that the status is provisional | Calibration artifact (registered UNVERIFIED); regime/contagion/derivatives/feature suites (§E) | G3 still **FAIL** for every engine: labelled, not repaired. Wiring a valid method needs D-2 / D-10 | All IID-based significance claims LEGACY / UNVERIFIED (unchanged) | **New finding:** the regime interaction test is an OLS ANOVA F-test (IID, homoskedastic), also invalid under overlap; the audit missed it. **Audit correction:** the `oos_validator` per-fold p is serialized but feeds no decision |
| **F-03** legacy seed | **PARTIALLY REMEDIATED** | Legacy `run()` seeds θ₀ with OLS on the first 10,000 candles | Batch 1: legacy LEGACY sidecar with `seed_lookahead`; entry/exit `kalman_seed_lineage`; retest on `run_canonical` | `test_kalman_seed_lineage` (8) | Canonical path G1, G2, G4 PASS. Legacy consumers (pipeline runner, walk-forward folds, pairs permutation, research-lab pairs tool) still use the legacy seed; they now self-identify | Legacy pairs evidence INVALIDATED | Migration of the remaining consumers needs zone S (D-8) to place seeds |
| **F-04** zero latency | **BLOCKED** (D-1) | Labels from close t with the signal at close t | Batch 1: `quant_core/timing.py` contract (no default d) | `test_timing_contract` (4, incl. bid-ask bounce demo) | G4 FAIL in cross-sectional, contagion, feature, derivatives, PCA labels | Unchanged | Owner chooses d |
| **F-05** pairs null | **BLOCKED** (D-4 / Step 17) | z-timing shuffle preserves the cross-asset relationship | — | — | G3 FAIL | Pairs permutation verdicts INVALIDATED | — |
| **F-06** walk-forward boundary | **FIXED** (scope note) | `test_start = train_end` with inclusive slicing | Batch 1: `walk_forward_engine._generate_folds` exclusive boundary, anchored `test_end` | `test_walk_forward_engine` (18) | G1, G2 PASS. No prediction-horizon label in this engine (lifecycle P&L), so a horizon purge is N/A; the folds still use the legacy seed (F-03) | Legacy walk-forward stays LEGACY | Purge/embargo design applies once folds carry labels |
| **F-07** resolution / families | **PARTIALLY REMEDIATED** | B = 100 (20 in research-lab tools) against families | Stage A: `permutation_stats.compute_permutation_verdict` reports `{metric}_n_permutations`, `_p_floor`, `_p_at_floor` | permutation suites (§E) | G3 still FAIL vs families. **Correction to my program note:** with B = 20 an exact permutation test is still valid at α = 0.05 for one pre-declared hypothesis; the defect is resolution vs family correction and power | Unchanged | B per family needs D-3 |
| **F-08** DCA power | **BLOCKED** (D-5) | Statistic does not respond to the planted effect | — | `dca_calibration` reproduces it | G3 FAIL | No DCA significance claim exists | — |
| **F-09** partial candles | **FIXED** | Files written mid-candle; no completed-candle rule | Batch 1: `data_fetcher._build_ohlcv_dataframe`, `research/data_integrity.py`, data v2 + manifest + archive | `test_data_integrity` (4) | G1, G2, G4, G5 PASS | Pre-v2 results not byte-reproducible on v2 (already LEGACY) | Stale-cache fallback (pre-existing) |
| **F-10** survivorship | **BLOCKED** (D-6) | Current-listing universes | — | — | G4 / G5 FAIL | Unchanged | No authoritative listing data |
| **F-11** verdict overstatement | **PARTIALLY REMEDIATED** | Field equated the raw p with an FDR p | Batch 1: field None + `family_correction`; templates | `test_research_lab_statistical_integrity` | Field honest; vocabulary BLOCKED (D-7) | Earlier reports UNVERIFIED | — |
| **F-12** borrow fallback | **FIXED** (fail-closed for canonical) | Borrow charged 0 when not supplied | Batch 1: `borrow_status`. Stage A: `EntryExitEngine._output_provenance_status` (single source for both the summary row and the sidecar) returns **UNCERTIFIED** when borrow is not modelled; the sidecar keeps the full lineage plus `cost_complete=False` and `downgrade_reason`; the summary row gains `cost_complete`; new `_canonical_lineage()` | `CostCompletenessGateTest` (2, discriminating: no borrow → UNCERTIFIED; borrow given → CANONICAL); `test_canonical_dataset_without_modelled_borrow_is_not_canonical` (row and sidecar agree) | G4 / G5 PASS: incomplete economics can no longer validate as CANONICAL | Legacy pairs P&L = ASSUMED_ZERO_BORROW | No real borrow-rate source exists, so canonical pairs output is currently impossible by design |
| **F-13** DCA R | **FIXED** | Return on deployed capital stored as R | Batch 1: `r_multiple=None`, `return_on_max_deployed`; R ratios R-only | phase3 + DCA suites | PASS | DCA Sharpe/Sortino from the old field INVALIDATED | — |
| **F-14** scorer | **PARTIALLY REMEDIATED** | None coerced to 0; thresholds for an old basis | Batch 1: undefined ratios unscored | `UndefinedRatioTests` (3) | Thresholds BLOCKED (D-11) | Rankings LEGACY | — |
| **F-15** Decision Lock drift | **FIXED** | Lock text predates REVB-3 | `STAGE2D_DECISION_LOCK.md`: C6 row and §C6 marked superseded (original text kept); append-only Supersession record S-1 | Document | G5 PASS | — | Zones still BLOCKED (stated in S-1) |
| **F-16** repeated scans | **BLOCKED** (D-3 / D-8) | Separate BH per scan | — | — | G3 / G5 FAIL | Scan survivors LEGACY | — |
| **F-17** health baseline | **FIXED** | Runner passed the legacy OLS `hedge_ratio` as `seed_beta` | `pairs_bot_runner._health_baseline_beta` (never raises: a non-numeric or non-finite seed β falls back to the labelled legacy value); both health calls; heartbeat log shows the baseline source | `HealthBaselineSourceTest` (4, incl. a case where the baseline flips the verdict, and the no-raise case) | G4 / G5 PASS | — | `monitor_engine` (monitor-only pairs, no entries) still seeds θ₀ from the legacy hedge ratio; recorded, out of the trading path |
| **F-18** writer | **FIXED** | Unnamed index written as `index` | Committed in `39b76a2`; Stage A adds a regression test | `test_legacy_writer_declares_its_lookahead_seed` (column + times). **Mutation-checked:** reverting the writer makes it fail (`'index' != 'timestamp'`) | G1 PASS | — | Commit decision is yours |
| **F-19** "untouched holdout" | **FIXED** | **Audit inaccurate:** no field has this name (the field is `oos_adf_pvalue`); the false wording was in 3 comments and 1 test docstring | `cointegration_engine.py` comments; `test_cointegration_engine.py` docstring | Grep clean | G5 PASS | — | — |
| **F-20** forming candle | **FIXED** | No closed-candle rule at fetch | Batch 1 (with F-09) | `test_data_integrity` | PASS | — | — |
| **F-21** duplicate ATR | **FIXED** (documented) | `atr_indicator` = second `_calculate_atr()` call | `feature_calculator.py` comment at the source; already detected by the correlation matrix | `test_feature_correlation_matrix` (existing) | G1 PASS | — | Families must count it once (D-3) |

**Defects in my own Stage A changes, caught by the guarded regression and fixed:**
1. **F-12 first version:** the sidecar said UNCERTIFIED while the summary row still said CANONICAL (two sources of truth). Fixed by moving the rule into `_output_provenance_status`.
2. **F-17 first version:** `float(seed.beta0)` raised when the seed object carried no numeric β (a test mock), inside the entry path. That left the engine holding a trade the executor never opened. Fixed by making the helper total (no exception path).

**Test changed to match the corrected spec (justified, not weakened):** `test_stage2d_provenance.test_canonical_dataset_is_accepted_and_labelled_with_lineage` now supplies a modelled borrow rate.
- **Why:** since F-12, a CANONICAL label requires complete costs.
- **What is unchanged:** all of its assertions.
- **What was added:** a new sibling test pins the no-borrow outcome (UNCERTIFIED, lineage kept, row and sidecar agree).

**New issues found during remediation (added, not hidden):**
- **N-1:** the regime interaction test is an IID OLS F-test (see F-02).
- **N-2:** `monitor_engine` uses the legacy hedge ratio for monitor-only θ₀ (see F-17).
- **N-3:** this session ran some focused test modules without the offline network guard: the first permutation run (stopped after about 10 minutes and rerun guarded). These suites use only public read endpoints by project convention and place no orders. Every result reported in §E comes from guarded runs.

## B. Five-gate correctness matrix (affected components)

| Component | G1 | G2 | G3 | G4 | G5 | Overall |
|---|---|---|---|---|---|---|
| PCA residual engine | PASS | PASS | FAIL (B = 100; IID families) | FAIL (labels F-04); causal fit PASS | PARTIAL | Not validated |
| Data layer v2 | PASS | PASS | N/A | PASS | PASS | Validated for data |
| Kalman canonical | PASS | PASS | N/A | PASS | PASS (lineage) | Validated component |
| Kalman legacy | PASS | PASS | N/A | FAIL | PASS (self-declares LEGACY) | Legacy only |
| Entry/exit (canonical) | PASS | PASS | N/A | PASS only with modelled borrow | PASS | Canonical-capable only with known borrow |
| Pairs health | PASS | PASS | N/A | PASS | PASS | Validated |
| Walk-forward engine | PASS | PASS | FAIL (OOS p inherits F-02/F-07) | FAIL (legacy seed) | PARTIAL | Legacy only |
| Regime research | PASS | PASS | FAIL (IID IC + IID F-test; labelled) | PASS (detector) | PARTIAL | Not validated |
| Contagion / derivatives / feature decay / stability | PASS | PASS | FAIL (labelled) | FAIL (F-04) | PARTIAL | Not validated |
| Permutation stats | PASS | PASS | PARTIAL (exact; resolution now reported) | N/A | PASS | Valid per test; families BLOCKED |
| Research-lab verdicts | PASS | PASS | PARTIAL (field fixed) | FAIL | BLOCKED (D-7) | Not validated |
| Strategy scorer | PASS | PASS | N/A | BLOCKED (D-11) | PASS | Not validated |
| DCA | PASS | PASS | FAIL | PASS | PARTIAL | Descriptive only |

## C. Research evidence classification

**Unchanged** (no history upgraded; see `RESEARCH_EVIDENCE_IMPACT_REGISTER_2026-10-03.md`):
- PCA residual IC: INVALIDATED.
- Kalman pairs edge claims: INVALIDATED.
- Forex top_k = 1: INVALIDATED / UNVERIFIED.
- Contagion, crypto cross-sectional, feature, regime: LEGACY / DESCRIPTIVE ("edge not established").
- Research-lab experiments: UNVERIFIED.
- Inference calibration: UNVERIFIED (synthetic).

**Edge established: none.**

## D. Provenance / reproducibility

| Item | Value |
|---|---|
| Code commit | `39b76a2` + uncommitted working tree (43 paths; **dirty**) |
| Data version | v2 (`bot/research/governance/data_manifest_CRRP-2026-10-02_v2.json`, 213 files fingerprinted; v1 archived at `data/_archive/v1_CRRP-2026-10-02/`) |
| Methodology version | `STAGE2D-REVB-3` |
| Protocol hash | `7dbaa8fe8bab068708565852950589fd8d546c53d4ee7d68b75182f8fe455a6c` |
| Run IDs | Exist for canonical Kalman / entry-exit outputs (`make_run_id`); absent from the feature / cross-sectional / contagion / PCA / DCA engines (open) |
| Universe version | None (F-10 BLOCKED) |
| Holdout status | All history ≤ 2026-09-24 PSEUDO_HOLDOUT; no forward holdout defined (D-8) |

## E. Test report

All runs below were offline under the network guard. Each harness used its own test database.

| Run | Modules | Ran | PASS | FAIL / ERROR | SKIPPED | Classification of failures |
|---|---|---|---|---|---|---|
| `perm` (permutation consumers) | `test_forex_crypto_cross_asset_proof`, `test_oos_validator`, `test_research_lab_capability_registry`, `test_stage2d_engine`, `test_permutation_test_engine`, `test_regime_conditional_permutation` | 112 | 111 | 0 | 1 | — (skip: Phase 2E cache file absent, pre-existing condition) |
| `aff` (27 affected modules, first pass) | regime, contagion, derivatives, feature decay/stability, Kalman, PCA, pairs, canonical, Stage 2D, entry/exit, cointegration, parity | 440 | 421 | 19 | 0 | **15 PRE-EXISTING FAILURES** (`test_derivatives_data` ×13, `test_derivatives_engine` ×2: network refused by guard; all 15 are in the clean-HEAD baseline failure list). **3 failures + 1 error were NEW**, all in `test_stage2d_provenance`, caused by my Stage A defects (see §A); fixed |
| `aff2` (rerun after the fixes) | `test_stage2d_provenance`, `test_pairs_health`, `test_kalman_seed_lineage`, `test_canonical_lifecycle`, `test_canonical_seed`, `test_entry_exit_engine`, `test_parity_replay`, `test_parity_forensics`, `test_stage2d_engine`, `test_pairs_live_safety` | 208 | 208 | 0 | 0 | — |

**New tests in this program (all PASS):**

| Kind | Tests |
|---|---|
| Causality (future perturbation) | `test_pca_causal` future-perturbation; `test_kalman_seed_lineage` canonical future shock |
| Adversarial / defect injection | `test_pca_causal` legacy fit fails the same check; F-18 writer revert caught (`'index' != 'timestamp'`) |
| Known-answer | PCA first segment (atol 1e-15); PCA factor removal; Kalman β recovery; timing-contract label |
| Provenance / fail-closed | `CostCompletenessGateTest` (2); `test_canonical_dataset_without_modelled_borrow_is_not_canonical`; `HealthBaselineSourceTest` (4) |
| Data integrity | `test_data_integrity` (4) |
| Statistical size / power | `inference_calibration` (synthetic, registered UNVERIFIED): size of IID, HAC, circular shift and block permutation at h ∈ {1, 4, 24}, φ ∈ {0, 0.9, 0.99}; power at 3 planted effects. Not a unit test (runtime) |

**NOT RUN in this pass:**
- the full 122-module regression after Stage A (the last full run, 1,679 tests, was on batch 1 and showed 0 new failures);
- any market research;
- any calibration of the Step-17 null, DCA redesign or cross-sectional null (blocked).

**Network-guard note:** one unguarded focused run happened earlier in this pass (N-3). It was stopped and rerun under the guard; no result here comes from it.

## F. Owner decision register

| # | Decision | Blocks |
|---|---|---|
| D-1 | Execution delay d | F-04 (all labels incl. PCA) |
| D-2 | Dependence inference method | F-02 wiring |
| D-10 | HAC lag rule / block-length rule | F-02 wiring |
| D-3 | Research families, primary endpoints, B or two-stage design | F-07, F-16, F-21 counting |
| D-4 | Step 17 canonical pairs null | F-05 |
| D-5 | DCA statistical redesign | F-08 |
| D-6 | Point-in-time universe source | F-10 |
| D-7 | Research-lab SUPPORTED requirements | F-11 |
| D-8 | Zones S / V / WF / F and forward-holdout start | canonical pairs, F-03 migration, F-16 |
| D-9 | PCA fit window: **taken from existing conventions** (expanding, 1000 / 168); confirm or replace | F-01 sensitivity |
| D-11 | Scorer thresholds | F-14 |
| — | Borrow-rate data source | canonical pairs output (F-12 now fails closed) |
| — | Commit of batch 1 + Stage A | — |

Options, consequences and evidence for D-1…D-9 are in `RESEARCH_PIPELINE_REMEDIATION_PLAN_2026-10-03.md` §5.

## G. Canonical readiness

| Research family | Readiness | Reason |
|---|---|---|
| Pairs (Stage 2D canonical) | **BLOCKED** | D-8 zones, D-4 null, borrow source (F-12 fail-closed) |
| Pairs legacy | **LEGACY-ONLY** | F-03 seed in legacy consumers, F-05 |
| Cointegration selection | **BLOCKED** | D-8, D-3 (F-16) |
| PCA residual | **BLOCKED** | Causal fit done; F-04 labels (D-1), F-02/F-07 (D-2, D-3) |
| Feature research | **BLOCKED** | D-1, D-2, D-3 |
| Cross-sectional | **BLOCKED** | D-1, D-2, D-3, D-6 |
| Contagion | **BLOCKED** | D-1, D-2, D-3 |
| Regime research | **BLOCKED** | D-2 (IID IC + IID interaction test) |
| DCA | **BLOCKED** | D-5 |
| Walk-forward | **LEGACY-ONLY** | Legacy seed (needs D-8 for canonical seeds) |
| Research lab | **BLOCKED** | D-7, D-3 |
| Data layer | **CANONICAL-CAPABLE** | v2 + manifest + fetch rule |
| Derivatives / trade-flow / order-book | **UNKNOWN** | Not audited |
