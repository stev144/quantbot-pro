# Five-Gate Remediation Program: Inspection and Plan (2026-10-03)

**Baseline:** HEAD `39b76a2` plus the uncommitted remediation batch 1 (16 modified files, 11 new files; listed in `RESEARCH_PIPELINE_REMEDIATION_PLAN_2026-10-03.md` §4).

**Authority:** `FULL_RESEARCH_PIPELINE_FIVE_GATE_AUDIT_2026-10-03.md`. Every finding below was re-checked against the current code before it was planned; no audit PASS is inherited.

## 1. Audit versus current code

| ID | Audit claim | Current tree (verified by reading the code) | Discrepancy |
|---|---|---|---|
| F-01 | PCA fitted on full history | Still true: `run_pca_engine` → `run_pca(returns)` uses whole-sample mean, std, correlation and loadings | — |
| F-02 | IID p feeds verdicts | IID `spearmanr` p at 12 sites. **These feed a decision or verdict:** `oos_validator` l.530; `regime_conditional_ic` (→ BH → `regime_conditional_status` → research-lab REGIME_DEPENDENT); `feature_decay_analyzer._grade`; `kalman_filter_engine` l.1820 (selects the "best" signal by p < 0.05); contagion `hypothesis_7`; `derivatives_engine` (`significant` flag). Descriptive only: `entry_exit` entry-IC; `feature_validator` per-year / per-regime IC (no p used) | The audit listed 3 sites; there are 6 decision-feeding ones |
| F-03 | Legacy seed contaminates pairs research | Batch 1: the legacy writer now emits a LEGACY sidecar; entry/exit summaries carry the lineage; the retest moved to `run_canonical`. **Still on the legacy path:** `cointegration_pipeline_runner`, `walk_forward_engine` folds, `permutation_test_engine`, the research-lab pairs tool | Partially remediated |
| F-04 | Same-close labels | Still true in cross-sectional (`shift(-1)` of the close return), contagion, feature calculator, derivatives, **and PCA labels** (`rolling(h).sum().shift(-h)`) | The audit did not list PCA or derivatives |
| F-05 | Pairs null shuffles z timing | Unchanged | — |
| F-06 | `test_start = train_end` | Batch 1: exclusive boundary; test window anchored on `train_end`. The walk-forward engine has no prediction-horizon label (lifecycle P&L), so a horizon purge does not apply; `oos_validator` already purges by horizon | Remediated (§3 dossier) |
| F-07 | B = 100 vs families | Unchanged. **New:** the research-lab tools default `n_permutations=20` (`research_tools.py:294`, `regime_tools.py:174`), so p_min = 1/21 = 0.0476 < 0.05 and a single unbeatable permutation result is reported as "significant" | Worse than audited |
| F-08 | DCA power ≈ 0 | Unchanged (batch-1 diagnosis: the statistic does not respond to the planted effect) | — |
| F-09 | Partial last candles | Batch 1: data v2, manifest, fetch rule | Remediated |
| F-10 | Survivorship | Unchanged; no listing data on disk | BLOCKED |
| F-11 | `fdr_adjusted_p_value = block_p`; SUPPORTED overstated | Field fixed in batch 1; vocabulary unchanged | Partially remediated |
| F-12 | Borrow = 0 when unspecified | Summaries carry `cost_status = COST_UNAVAILABLE:borrow` and `borrow_status = ASSUMED_ZERO_BORROW`. **Gap:** no gate stops an incomplete-cost run from being labelled CANONICAL | Partially remediated |
| F-13 | DCA r_multiple | Fixed in batch 1 | Remediated |
| F-14 | Scorer thresholds / None → 0 | None → undefined fixed; thresholds unchanged (owner) | Partially remediated |
| F-15 | Decision Lock says L BLOCKED | Still says "Mechanism LOCKED; L BLOCKED" (lines 14, 65); code and REVB-3 lock L = 1000 | — |
| F-16 | Repeated scans | Unchanged | — |
| F-17 | Health drift vs legacy seed | Confirmed: `pairs_bot_runner` passes `cfg.hedge_ratio` (the legacy OLS from `cointegration_pairs.csv`) as `seed_beta`, even when a validated canonical seed is loaded in `session.seed` | — |
| F-18 | Writer `index` column | Committed in `39b76a2`; **no regression test covers the column name** | Untested |
| F-19 | "Untouched holdout" fields | **Audit inaccurate:** there are no such field names. The output field is `oos_adf_pvalue`; the false wording is in 3 comments (`cointegration_engine.py` l.334, 755, 1058) | Wording only |
| F-20 | Forming candle in `data_fetcher` | Fixed in batch 1 | Remediated |
| F-21 | `atr_indicator` ≡ `atr` | Already detected and documented by the feature-correlation matrix (`feature_intelligence_data.py:554`; test `test_feature_correlation_matrix`). Still computed and still counted in validator families | Partially documented |

## 2. Dependency order

```
Stage A (no owner input; causal / provenance hygiene)
  F-01 causal PCA ── F-17 health baseline ── F-18 writer test ── F-19 wording ── F-15 supersession
  F-12 cost-completeness gate ── F-07 resolution guard (floor-level "significance" refused) ── F-02 IID demotion labels
Stage B (owner decisions D-1…D-9, D-10/D-11 below)
  D-1 d ─► F-04 labels (cross-sectional, contagion, feature, derivatives, PCA) ─► D-2 inference ─► F-02 wiring
  D-3 families + B ─► F-07 / F-16 ─► D-4 Step-17 null ─► F-05 ─► D-8 zones ─► canonical pairs
  D-5 DCA design ─► F-08 calibration
  D-6 universe data ─► F-10
  D-7 vocabulary ─► F-11
Stage C (only after B): calibrated, provenance-complete market research (not in this program's scope until approved)
```

## 3. Owner decisions required

Decisions D-1…D-9 are unchanged from `RESEARCH_PIPELINE_REMEDIATION_PLAN_2026-10-03.md` §5. Two are added:
- **D-10:** HAC lag rule and block-length rule, once D-2 is chosen.
- **D-11:** scorer threshold re-specification (F-14).

A decision is taken here only where existing project documentation already fixes it (§4).

## 4. Decisions taken from existing documentation (not invented)

| Decision | Source | Value |
|---|---|---|
| Kalman init window L | REVB-3: `protocol.py`, `STAGE2D_CANONICAL_PROTOCOL.md` §C6, D1–D5 report §10 | L = 1000 (the Decision Lock text is superseded) |
| PCA causal fit (F-01; not on the owner list) | Reuses two frozen project conventions; no new tuned values: L = 1000 (minimum history before a first fit) and the 168-candle cadence (weekly refit, the project-wide z minimum) | Expanding-window fit on returns strictly before each refit point; refit every 168 candles; first fit after 1,000 candles. A baseline convention, not optimised; sensitivity to be studied before any canonical use |
